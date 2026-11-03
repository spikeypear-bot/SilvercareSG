"""Tests for `iot-smoke-test.py --cancel` (docs/features/alert-cancel.md, criterion 17).

No network: sockets are blocked (conftest), the MQTT connection is a fake that
models the IoT policy (wearables may publish alert_cancel, bedrooms can't), and the
pause before the cancel is patched out. Payloads are checked with the ingest Lambda's
own validator (lambdas/ingest/src/validation.py, pure, loaded by path).
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from helpers import PRIVATE_KEY_TEXT, REPO_ROOT

WEARABLE = "wearable-0001"
BEDROOM = "bedroom-0001"
ENDPOINT = "synthetic-ats.iot.ap-southeast-1.amazonaws.com"
FIXED_NOW = datetime(2026, 11, 3, 2, 0, 0, 120_000, tzinfo=UTC)
UUID_ANY_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
CRITICAL = frozenset({"fall", "vitals_alert", "alert_cancel"})


@pytest.fixture(scope="module")
def validation():
    name = "ingest_lambda_validation"
    if name in sys.modules:
        return sys.modules[name]
    path = REPO_ROOT / "lambdas" / "ingest" / "src" / "validation.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def counter_ids():
    n = iter(range(1, 100))
    return lambda: f"00000000-0000-4000-8000-{next(n):012d}"


def as_rule_event(device_id, message, payload, received_at_ms=1793671200120):
    return {
        **json.loads(json.dumps(payload)),
        "device_id": device_id,
        "message": message,
        "received_at": received_at_ms,
    }


def cancel_messages(smoke):
    return smoke.build_cancel_messages(FIXED_NOW, counter_ids())


def parse_ts(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


# ---- build_cancel_payload ----


def test_cancel_payload_is_the_envelope_only(smoke):
    payload = smoke.build_cancel_payload(
        FIXED_NOW, "00000000-0000-4000-8000-000000000001"
    )

    assert payload == {
        "schema_version": 1,
        "message_id": "00000000-0000-4000-8000-000000000001",
        "ts": "2026-11-03T02:00:00.120Z",
    }


def test_cancel_payload_passes_ingest_validation_for_a_wearable(smoke, validation):
    payload = smoke.build_cancel_payload(FIXED_NOW)

    result = validation.validate_event(
        as_rule_event(WEARABLE, "alert_cancel", payload), CRITICAL
    )

    assert isinstance(result, validation.ValidMessage)


def test_cancel_payload_is_rejected_for_a_bedroom(smoke, validation):
    payload = smoke.build_cancel_payload(FIXED_NOW)

    result = validation.validate_event(
        as_rule_event(BEDROOM, "alert_cancel", payload), CRITICAL
    )

    assert result == validation.Rejection("invalid:message")


def test_cancel_payload_gets_a_fresh_message_id_each_time(smoke):
    first = smoke.build_cancel_payload(FIXED_NOW)
    second = smoke.build_cancel_payload(FIXED_NOW)

    assert first["message_id"] != second["message_id"]


# ---- build_cancel_messages ----


def test_cancel_messages_are_a_fall_then_an_alert_cancel(smoke):
    assert [m for _, m, _ in cancel_messages(smoke)] == ["fall", "alert_cancel"]


def test_cancel_ts_is_5_s_after_the_fall(smoke):
    (_, _, fall), (_, _, cancel) = cancel_messages(smoke)

    assert parse_ts(fall["ts"]) == FIXED_NOW
    assert parse_ts(cancel["ts"]) - parse_ts(fall["ts"]) == timedelta(
        seconds=smoke.CANCEL_PAUSE_S
    )


def test_cancel_messages_pass_ingest_validation(smoke, validation):
    for _, message, payload in cancel_messages(smoke):
        result = validation.validate_event(
            as_rule_event(WEARABLE, message, payload), CRITICAL
        )
        assert isinstance(result, validation.ValidMessage), (message, result)


def test_cancel_message_ids_are_unique(smoke):
    ids = [p["message_id"] for _, _, p in smoke.build_cancel_messages(FIXED_NOW)]

    assert len(set(ids)) == 2


def test_cancel_payloads_never_contain_rule_added_fields(smoke):
    for _, _, payload in cancel_messages(smoke):
        assert not {"device_id", "message", "received_at"} & set(payload)


def test_alert_cancel_topic_is_the_device_own_topic(smoke):
    assert (
        smoke.build_topic(WEARABLE, "alert_cancel")
        == f"silvercare/v1/{WEARABLE}/alert_cancel"
    )


# ---- report_cancel ----


def test_report_cancel_prints_ids_and_expectations_but_no_values(smoke):
    messages = cancel_messages(smoke)
    out = io.StringIO()

    smoke.report_cancel(WEARABLE, messages, out)

    text = out.getvalue()
    for _, _, payload in messages:
        assert payload["message_id"] in text
    assert "cancelled" in text
    assert "no 'SilverCare ESCALATED alert' email" in text
    stripped = UUID_ANY_RE.sub("<id>", text)
    for value in ("0.91", "3.8", "confidence", "peak_g", "{", "2026-11-03"):
        assert value not in stripped, value


# ---- main --cancel with a fake connection ----


class FakeConnection:
    instances: ClassVar[list[FakeConnection]] = []
    fail_topic: ClassVar[str | None] = None
    refuse_other_client: ClassVar[bool] = True
    log: ClassVar[list[tuple]] = []

    def __init__(self, endpoint, client_id, tls):
        self.client_id = client_id
        self.published: list[tuple[str, bytes]] = []
        FakeConnection.instances.append(self)

    def connect(self):
        if self.client_id.endswith("-not-me"):
            return "refused" if self.refuse_other_client else "connected"
        return "connected"

    def publish(self, topic, payload):
        self.published.append((topic, payload))
        FakeConnection.log.append(("publish", topic))
        if topic == FakeConnection.fail_topic:
            return "timeout"
        own = f"silvercare/v1/{self.client_id}/"
        if not topic.startswith(own):
            return "closed"
        per_type = {
            "wearable": {"vitals", "vitals_alert", "fall", "status", "alert_cancel"},
            "bedroom": {"motion", "bed", "status"},
        }
        dtype = self.client_id.split("-")[0]
        return "puback" if topic[len(own) :] in per_type[dtype] else "closed"

    def close(self):
        pass


@pytest.fixture
def fake_conn(smoke, monkeypatch):
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
    keys = tmp_path / "keys"
    keys.mkdir()
    ca = tmp_path / "ca.pem"
    ca.write_text("synthetic ca placeholder\n")
    for device_id in (WEARABLE, BEDROOM):
        (keys / f"{device_id}.crt").write_text("synthetic cert placeholder\n")
        (keys / f"{device_id}.key").write_text("synthetic key placeholder\n")
    return {"ca": ca, "keys": keys}


def args(device_files, device_id=WEARABLE, *extra):
    return [
        "--device",
        device_id,
        "--endpoint",
        ENDPOINT,
        "--ca-file",
        str(device_files["ca"]),
        "--keys-dir",
        str(device_files["keys"]),
        *extra,
    ]


def test_main_cancel_publishes_fall_pause_then_cancel(
    smoke, fake_conn, device_files, capsys
):
    code = smoke.main(args(device_files, WEARABLE, "--cancel"))

    out = capsys.readouterr().out
    assert code == 0
    assert f"PASS  {smoke.CHECK_CANCEL}" in out
    conn = fake_conn.instances[-1]
    assert conn.client_id == WEARABLE
    assert [t for t, _ in conn.published] == [
        f"silvercare/v1/{WEARABLE}/fall",
        f"silvercare/v1/{WEARABLE}/alert_cancel",
    ]
    tail = fake_conn.log[-3:]
    assert tail == [
        ("publish", f"silvercare/v1/{WEARABLE}/fall"),
        ("sleep", smoke.CANCEL_PAUSE_S),
        ("publish", f"silvercare/v1/{WEARABLE}/alert_cancel"),
    ]
    for _, payload in conn.published:
        assert json.loads(payload)["message_id"] in out


def test_main_cancel_payload_on_the_wire_is_envelope_only(
    smoke, fake_conn, device_files
):
    smoke.main(args(device_files, WEARABLE, "--cancel"))

    _, payload = fake_conn.instances[-1].published[-1]
    assert set(json.loads(payload)) == {"schema_version", "message_id", "ts"}


def test_main_cancel_refuses_a_bedroom_with_exit_2(
    smoke, fake_conn, device_files, capsys
):
    code = smoke.main(args(device_files, BEDROOM, "--cancel"))

    captured = capsys.readouterr()
    assert code == 2
    assert "--cancel is for wearables only" in captured.err
    assert fake_conn.instances == []


def test_main_cancel_skipped_when_an_earlier_check_fails(
    smoke, fake_conn, device_files, capsys
):
    fake_conn.refuse_other_client = False

    code = smoke.main(args(device_files, WEARABLE, "--cancel"))

    captured = capsys.readouterr()
    assert code != 0
    assert f"FAIL  {smoke.CHECK_CANCEL}" in captured.out
    assert "Skipping the cancel messages" in captured.err
    assert len(fake_conn.instances) == 5


def test_main_cancel_without_puback_fails_and_prints_no_expectations(
    smoke, fake_conn, device_files, capsys
):
    fake_conn.fail_topic = f"silvercare/v1/{WEARABLE}/alert_cancel"

    code = smoke.main(args(device_files, WEARABLE, "--cancel"))

    out = capsys.readouterr().out
    assert code != 0
    assert f"FAIL  {smoke.CHECK_CANCEL}" in out
    assert "Cancel messages sent" not in out


def test_main_cancel_with_ingest_keeps_the_ingest_command_last(
    smoke, fake_conn, device_files, capsys
):
    code = smoke.main(args(device_files, WEARABLE, "--ingest", "--cancel"))

    out = capsys.readouterr().out
    assert code == 0
    assert f"PASS  {smoke.CHECK_CANCEL}" in out
    assert (
        out.rstrip("\n")
        .splitlines()[-1]
        .lstrip()
        .startswith("python scripts/ingest-check.py")
    )


def test_main_cancel_output_has_no_payload_values_or_keys(
    smoke, fake_conn, device_files, capsys
):
    smoke.main(args(device_files, WEARABLE, "--cancel"))

    captured = capsys.readouterr()
    text = UUID_ANY_RE.sub("<id>", captured.out + captured.err)
    for value in ("0.91", "3.8", "confidence", "{", PRIVATE_KEY_TEXT):
        assert value not in text, value


def test_main_without_cancel_flag_sends_no_cancel(
    smoke, fake_conn, device_files, capsys
):
    smoke.main(args(device_files, WEARABLE))

    out = capsys.readouterr().out
    assert smoke.CHECK_CANCEL not in out
    assert all(
        not t.endswith("/alert_cancel")
        for c in fake_conn.instances
        for t, _ in c.published
    )
