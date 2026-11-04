"""Tests for `iot-smoke-test.py --low-battery` (docs/features/watchdog.md, criterion
18a).

No network: sockets are blocked (conftest) and the MQTT connection is the fake from
test_iot_smoke_cancel.py. Payloads are checked with the ingest Lambda's own validator
(lambdas/ingest/src/validation.py) and against the watchdog's own battery rule
(lambdas/watchdog/src/checks.py), both pure and loaded by path.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from datetime import UTC, datetime

import pytest
from helpers import PRIVATE_KEY_TEXT, REPO_ROOT
from test_iot_smoke_cancel import (
    BEDROOM,
    UUID_ANY_RE,
    WEARABLE,
    FakeConnection,
    args,
    as_rule_event,
    counter_ids,
)

FIXED_NOW = datetime(2026, 11, 4, 2, 0, 0, 120_000, tzinfo=UTC)
STATUS = frozenset({"status"})


def _load(name: str, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validation():
    path = REPO_ROOT / "lambdas" / "ingest" / "src" / "validation.py"
    return _load("ingest_lambda_validation", path)


@pytest.fixture
def fake_conn(smoke, monkeypatch):
    """The cancel tests' fake MQTT connection (models the IoT policy)."""
    FakeConnection.instances = []
    FakeConnection.fail_topic = None
    FakeConnection.refuse_other_client = True
    FakeConnection.log = []
    monkeypatch.setattr(smoke, "Connection", FakeConnection)
    monkeypatch.setattr(
        smoke.time, "sleep", lambda s: FakeConnection.log.append(("sleep", s))
    )
    return FakeConnection


@pytest.fixture
def device_files(tmp_path):
    """Placeholder CA, certificates, and keys under tmp_path (no real PEM)."""
    keys = tmp_path / "keys"
    keys.mkdir()
    ca = tmp_path / "ca.pem"
    ca.write_text("synthetic ca placeholder\n")
    for device_id in (WEARABLE, BEDROOM):
        (keys / f"{device_id}.crt").write_text("synthetic cert placeholder\n")
        (keys / f"{device_id}.key").write_text("synthetic key placeholder\n")
    return {"ca": ca, "keys": keys}


@pytest.fixture(scope="module")
def watchdog_checks():
    path = REPO_ROOT / "lambdas" / "watchdog" / "src" / "checks.py"
    return _load("watchdog_checks_for_scripts", path)


def payload(smoke, dtype="wearable"):
    return smoke.build_low_battery_payload(
        dtype, FIXED_NOW, "00000000-0000-4000-8000-000000000001"
    )


# ---- build_low_battery_payload ----


def test_wearable_payload_is_battery_10_worn_not_charging(smoke):
    assert payload(smoke) == {
        "schema_version": 1,
        "message_id": "00000000-0000-4000-8000-000000000001",
        "ts": "2026-11-04T02:00:00.120Z",
        "battery_pct": 10,
        "worn": True,
        "charging": False,
        "fw_version": smoke.FW_VERSION,
    }


def test_bedroom_payload_is_battery_10_without_wearable_fields(smoke):
    assert payload(smoke, "bedroom") == {
        "schema_version": 1,
        "message_id": "00000000-0000-4000-8000-000000000001",
        "ts": "2026-11-04T02:00:00.120Z",
        "battery_pct": 10,
        "fw_version": smoke.FW_VERSION,
    }


@pytest.mark.parametrize(
    ("device_id", "dtype"), [(WEARABLE, "wearable"), (BEDROOM, "bedroom")]
)
def test_low_battery_payload_passes_ingest_validation(
    smoke, validation, device_id, dtype
):
    result = validation.validate_event(
        as_rule_event(device_id, "status", payload(smoke, dtype)), STATUS
    )

    assert isinstance(result, validation.ValidMessage), result


@pytest.mark.parametrize("dtype", ["wearable", "bedroom"])
def test_low_battery_payload_is_low_for_the_watchdog(smoke, watchdog_checks, dtype):
    entry = payload(smoke, dtype)
    st = watchdog_checks.Status(
        entry, entry["ts"], watchdog_checks.parse_ts(entry["ts"])
    )

    assert watchdog_checks.low_battery_pct(st) == 10
    assert smoke.LOW_BATTERY_PCT < watchdog_checks.BATTERY_LOW_BELOW


def test_low_battery_wearable_is_not_also_off_wrist(smoke, watchdog_checks):
    entry = payload(smoke)
    st = watchdog_checks.Status(
        entry, entry["ts"], watchdog_checks.parse_ts(entry["ts"])
    )
    dev = watchdog_checks.Device(WEARABLE, "wearable", "res-0001")

    assert watchdog_checks.is_off_wrist(dev, st) is False


def test_plain_status_payload_has_a_normal_battery(smoke, watchdog_checks):
    """The plain smoke test's status resolves the alert (criterion 18a)."""
    entry = smoke.build_status_payload("wearable", FIXED_NOW)
    st = watchdog_checks.Status(
        entry, entry["ts"], watchdog_checks.parse_ts(entry["ts"])
    )

    assert watchdog_checks.low_battery_pct(st) is None


def test_low_battery_payload_gets_a_fresh_message_id_each_time(smoke):
    first = smoke.build_low_battery_payload("wearable", FIXED_NOW)
    second = smoke.build_low_battery_payload("wearable", FIXED_NOW)

    assert first["message_id"] != second["message_id"]


# ---- build_low_battery_messages ----


def test_low_battery_messages_are_one_status(smoke):
    messages = smoke.build_low_battery_messages("bedroom", FIXED_NOW, counter_ids())

    assert [(m, p["battery_pct"]) for _, m, p in messages] == [("status", 10)]


def test_low_battery_payload_never_has_rule_added_fields(smoke):
    ((_, _, body),) = smoke.build_low_battery_messages("wearable", FIXED_NOW)

    assert not {"device_id", "message", "received_at"} & set(body)


# ---- report_low_battery ----


def report(smoke, device_id=WEARABLE) -> tuple[str, list]:
    messages = smoke.build_low_battery_messages(
        device_id.split("-")[0], FIXED_NOW, counter_ids()
    )
    out = io.StringIO()
    smoke.report_low_battery(device_id, messages, out)
    return out.getvalue(), messages


def test_report_prints_the_message_id(smoke):
    text, messages = report(smoke)

    assert messages[0][2]["message_id"] in text


def test_report_expects_the_email_to_ops_and_caregivers_but_not_nurses(smoke):
    text, _ = report(smoke)

    assert f"'SilverCare device problem: Low battery - {WEARABLE} (<resident>)'" in text
    assert "to the ops subscribers and to the resident's caregivers" in text
    assert "no email to the nurses" in text
    assert "within 5 minutes (the next watchdog run)" in text


def test_report_says_the_plain_smoke_test_resolves_it_quietly(smoke):
    text, _ = report(smoke)

    assert "run the plain smoke test" in text
    assert "resolved quietly (no email" in text
    assert "resolved_by_role 'system'" in text


def test_report_says_the_device_is_reported_offline_after_15_minutes(smoke):
    text, _ = report(smoke)

    assert "About 15 minutes after the device's last status" in text
    assert "'SilverCare device problem: Device offline'" in text


def test_report_warns_to_use_a_device_the_simulator_is_not_sending_for(smoke):
    text, _ = report(smoke)

    assert "Use an assigned device the simulator isn't sending for" in text


def test_report_names_the_expected_item_attributes(smoke):
    text, _ = report(smoke)

    assert "notified_at and ops_notified_at" in text
    assert "battery_low" in text


def test_report_prints_no_payload_values(smoke):
    text, _ = report(smoke, BEDROOM)

    stripped = UUID_ANY_RE.sub("<id>", text)
    for value in ("battery_pct", "fw_version", "{", "2026-11-04", "charging"):
        assert value not in stripped, value


# ---- main --low-battery with a fake connection ----


@pytest.mark.parametrize("device_id", [WEARABLE, BEDROOM])
def test_main_low_battery_publishes_one_status_after_the_checks(
    smoke, fake_conn, device_files, capsys, device_id
):
    code = smoke.main(args(device_files, device_id, "--low-battery"))

    out = capsys.readouterr().out
    assert code == 0
    assert f"PASS  {smoke.CHECK_LOW_BATTERY}" in out
    conn = fake_conn.instances[-1]
    assert conn.client_id == device_id
    ((topic, raw),) = conn.published
    assert topic == f"silvercare/v1/{device_id}/status"
    sent = json.loads(raw)
    assert sent["battery_pct"] == 10
    assert sent["message_id"] in out
    assert "Low-battery message sent" in out


def test_main_low_battery_on_the_wire_passes_ingest_validation(
    smoke, fake_conn, device_files, validation
):
    smoke.main(args(device_files, WEARABLE, "--low-battery"))

    ((_, raw),) = fake_conn.instances[-1].published
    result = validation.validate_event(
        as_rule_event(WEARABLE, "status", json.loads(raw)), STATUS
    )
    assert isinstance(result, validation.ValidMessage), result


def test_main_low_battery_skipped_when_an_earlier_check_fails(
    smoke, fake_conn, device_files, capsys
):
    fake_conn.refuse_other_client = False

    code = smoke.main(args(device_files, WEARABLE, "--low-battery"))

    captured = capsys.readouterr()
    assert code != 0
    assert f"FAIL  {smoke.CHECK_LOW_BATTERY}" in captured.out
    assert "Skipping the low-battery message" in captured.err
    assert len(fake_conn.instances) == 5
    assert "Low-battery message sent" not in captured.out


def test_main_low_battery_without_puback_fails_and_prints_no_expectations(
    smoke, fake_conn, device_files, capsys
):
    fake_conn.fail_topic = f"silvercare/v1/{BEDROOM}/status"

    code = smoke.main(args(device_files, BEDROOM, "--low-battery"))

    out = capsys.readouterr().out
    assert code != 0
    assert f"FAIL  {smoke.CHECK_LOW_BATTERY}" in out
    assert "Low-battery message sent" not in out


def test_main_low_battery_with_ingest_keeps_the_ingest_command_last(
    smoke, fake_conn, device_files, capsys
):
    code = smoke.main(args(device_files, WEARABLE, "--ingest", "--low-battery"))

    out = capsys.readouterr().out
    assert code == 0
    assert f"PASS  {smoke.CHECK_LOW_BATTERY}" in out
    assert (
        out.rstrip("\n")
        .splitlines()[-1]
        .lstrip()
        .startswith("python scripts/ingest-check.py")
    )


def test_main_without_the_flag_sends_no_low_battery_status(
    smoke, fake_conn, device_files, capsys
):
    smoke.main(args(device_files, WEARABLE))

    out = capsys.readouterr().out
    assert smoke.CHECK_LOW_BATTERY not in out
    assert len(fake_conn.instances) == 5


def test_main_low_battery_output_has_no_payload_values_or_keys(
    smoke, fake_conn, device_files, capsys
):
    smoke.main(args(device_files, WEARABLE, "--low-battery"))

    captured = capsys.readouterr()
    text = UUID_ANY_RE.sub("<id>", captured.out + captured.err)
    for value in ("battery_pct", "fw_version", "{", PRIVATE_KEY_TEXT):
        assert value not in text, value
