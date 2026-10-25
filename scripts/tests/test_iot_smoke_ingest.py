"""Tests for `iot-smoke-test.py --ingest` (docs/features/ingest-live.md, criteria 26, 29).

No network: sockets are blocked (conftest), and the MQTT connection is a fake. The
payloads are also checked with the ingest Lambda's own validator
(lambdas/ingest/src/validation.py, pure, loaded by path) so the smoke test and the
Lambda can't drift apart.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from conftest import load_script
from helpers import PRIVATE_KEY_TEXT, REPO_ROOT

WEARABLE = "wearable-0001"
BEDROOM = "bedroom-0001"
ENDPOINT = "synthetic-ats.iot.ap-southeast-1.amazonaws.com"
FIXED_NOW = datetime(2026, 10, 18, 3, 15, 0, 120_000, tzinfo=UTC)
UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
ALLOWED = {
    "wearable": ("vitals", "fall", "status"),
    "bedroom": ("motion", "bed", "status"),
}


@pytest.fixture(scope="module")
def validation():
    """The ingest Lambda's validation module (pure, no AWS)."""
    name = "ingest_lambda_validation"
    if name in sys.modules:
        return sys.modules[name]
    path = REPO_ROOT / "lambdas" / "ingest" / "src" / "validation.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def check_mod():
    return load_script("ingest-check.py", "ingest_check")


def counter_ids():
    n = iter(range(1, 100))
    return lambda: f"00000000-0000-4000-8000-{next(n):012d}"


def as_rule_event(device_id, message, payload, received_at_ms=1792293300120):
    """What the IoT rule would pass to the Lambda for this publish."""
    return {
        **json.loads(json.dumps(payload)),
        "device_id": device_id,
        "message": message,
        "received_at": received_at_ms,
    }


# ---- build_ingest_messages ----


@pytest.mark.parametrize("dtype", ["wearable", "bedroom"])
def test_ingest_messages_order_is_valid_each_type_then_duplicate_then_invalid(
    smoke, dtype
):
    messages = smoke.build_ingest_messages(dtype, FIXED_NOW)

    kinds = [kind for kind, _, _ in messages]
    types = [message for _, message, _ in messages]
    assert kinds == ["valid", "valid", "valid", "duplicate", "invalid"]
    assert types[:3] == list(ALLOWED[dtype])
    assert types[3] == types[0]


@pytest.mark.parametrize("dtype", ["wearable", "bedroom"])
def test_duplicate_is_the_first_valid_message_again(smoke, dtype):
    messages = smoke.build_ingest_messages(dtype, FIXED_NOW)

    assert messages[3][2] == messages[0][2]
    assert messages[3][2]["message_id"] == messages[0][2]["message_id"]


@pytest.mark.parametrize("dtype", ["wearable", "bedroom"])
def test_valid_and_invalid_message_ids_are_unique_uuid4(smoke, dtype):
    messages = smoke.build_ingest_messages(dtype)

    ids = [p["message_id"] for k, _, p in messages if k != "duplicate"]
    assert len(set(ids)) == 4
    assert all(UUID4_RE.match(i) for i in ids)


def test_wearable_invalid_message_is_vitals_with_hr_999(smoke):
    kind, message, payload = smoke.build_ingest_messages("wearable", FIXED_NOW)[-1]

    assert (kind, message, payload["hr_bpm"]) == ("invalid", "vitals", 999)


def test_bedroom_invalid_message_is_bed_with_occupied_yes(smoke):
    kind, message, payload = smoke.build_ingest_messages("bedroom", FIXED_NOW)[-1]

    assert (kind, message, payload["occupied"]) == ("invalid", "bed", "yes")


def test_ingest_messages_reject_unknown_device_type(smoke):
    with pytest.raises(ValueError):
        smoke.build_ingest_messages("thermostat")


@pytest.mark.parametrize("dtype", ["wearable", "bedroom"])
def test_payloads_never_contain_rule_added_fields(smoke, dtype):
    for _, _, payload in smoke.build_ingest_messages(dtype):
        assert not {"device_id", "message", "received_at"} & set(payload)


@pytest.mark.parametrize("device_id", [WEARABLE, BEDROOM])
def test_valid_payloads_pass_the_ingest_lambda_validation(smoke, validation, device_id):
    dtype = device_id.split("-")[0]
    handled = frozenset({"vitals", "fall", "status", "motion", "bed"})

    for kind, message, payload in smoke.build_ingest_messages(dtype, FIXED_NOW):
        if kind == "invalid":
            continue
        result = validation.validate_event(
            as_rule_event(device_id, message, payload), handled
        )
        assert isinstance(result, validation.ValidMessage), (message, result)


@pytest.mark.parametrize(
    ("device_id", "reason"),
    [(WEARABLE, "out_of_range:hr_bpm"), (BEDROOM, "wrong_type:occupied")],
)
def test_invalid_payload_is_rejected_by_the_ingest_lambda(
    smoke, validation, device_id, reason
):
    dtype = device_id.split("-")[0]
    handled = frozenset({"vitals", "fall", "status", "motion", "bed"})
    _, message, payload = smoke.build_ingest_messages(dtype, FIXED_NOW)[-1]

    result = validation.validate_event(
        as_rule_event(device_id, message, payload), handled
    )

    assert result == validation.Rejection(reason)


@pytest.mark.parametrize(
    ("dtype", "message"),
    [(d, m) for d, ms in ALLOWED.items() for m in ms],
)
def test_each_message_payload_is_compact_json_under_8_kb(smoke, dtype, message):
    payload = smoke.build_message_payload(dtype, message, FIXED_NOW)

    assert len(smoke.encode_payload(payload)) < 8 * 1024


@pytest.mark.parametrize(
    ("dtype", "message"), [("wearable", "motion"), ("bedroom", "vitals")]
)
def test_message_payload_rejects_type_device_may_not_send(smoke, dtype, message):
    with pytest.raises(ValueError):
        smoke.build_message_payload(dtype, message)


def test_payloads_use_given_time(smoke):
    for _, _, payload in smoke.build_ingest_messages("wearable", FIXED_NOW):
        assert payload["ts"] == "2026-10-18T03:15:00.120Z"


def test_fall_payload_has_full_accel_window(smoke):
    payload = smoke.build_message_payload("wearable", "fall", FIXED_NOW)

    window = payload["accel_window"]
    assert window["hz"] == 25
    assert window["start_offset_ms"] == -1000
    assert len(window["samples"]) == 50


# ---- command and report ----


def test_ingest_check_command(smoke):
    cmd = smoke.ingest_check_command(WEARABLE, ["a", "b"], ["c"])

    assert cmd == (
        "python scripts/ingest-check.py --device wearable-0001"
        " --message-ids a,b --invalid-ids c"
    )


def test_report_ingest_prints_ids_and_command_but_no_values(smoke):
    messages = smoke.build_ingest_messages("wearable", FIXED_NOW, counter_ids())
    out = io.StringIO()

    smoke.report_ingest(WEARABLE, messages, out)

    text = out.getvalue()
    for _, _, payload in messages:
        assert payload["message_id"] in text
    assert "python scripts/ingest-check.py --device wearable-0001" in text
    for value in ("999", "33.4", "0.91", "1.0.0", "hr_bpm", "accel", "2026-10-18"):
        assert value not in text


def test_printed_command_round_trips_through_ingest_check(
    smoke, check_mod, monkeypatch
):
    messages = smoke.build_ingest_messages("bedroom", FIXED_NOW, counter_ids())
    out = io.StringIO()
    smoke.report_ingest(BEDROOM, messages, out)
    argv = out.getvalue().strip().splitlines()[-1].split()[2:]  # drop python + path
    valid = [p["message_id"] for k, _, p in messages if k == "valid"]
    invalid = [p["message_id"] for k, _, p in messages if k == "invalid"]
    seen = {}

    class FakeClient:
        """Every valid ID once, in the device's first item; the others are empty."""

        def get_item(self, **kwargs):
            if kwargs["Key"]["sk"]["S"] != "MSG#motion":
                return {}
            entries = [{"M": {"message_id": {"S": i}}} for i in valid]
            return {"Item": {"entries": {"L": entries}}}

    real_check = check_mod.check

    def fast_check(client, table, device_id, valid_ids, invalid_ids):
        seen.update(device=device_id, valid=valid_ids, invalid=invalid_ids)
        return real_check(
            client, table, device_id, valid_ids, invalid_ids, sleep=lambda s: None
        )

    monkeypatch.setattr(check_mod, "check", fast_check)

    code = check_mod.main(argv, client=FakeClient())

    assert code == 0
    assert seen == {"device": BEDROOM, "valid": valid, "invalid": invalid}


# ---- main --ingest with a fake connection ----


class FakeConnection:
    instances: ClassVar[list[FakeConnection]] = []
    publish_result: ClassVar = None
    refuse_other_client = True

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
        return FakeConnection.publish_result(self.client_id, topic)

    def close(self):
        pass


def broker(device_id, fail_topic=None):
    dtype = device_id.split("-")[0]
    allowed = {f"silvercare/v1/{device_id}/{m}" for m in ALLOWED[dtype]}

    def result(client_id, topic):
        if topic == fail_topic:
            return "timeout"
        return "puback" if topic in allowed else "closed"

    return result


@pytest.fixture
def fake_conn(smoke, monkeypatch):
    FakeConnection.instances = []
    FakeConnection.refuse_other_client = True
    monkeypatch.setattr(smoke, "Connection", FakeConnection)
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


@pytest.mark.parametrize("device_id", [WEARABLE, BEDROOM])
def test_main_ingest_publishes_messages_in_order_and_prints_command(
    smoke, fake_conn, device_files, capsys, device_id
):
    fake_conn.publish_result = broker(device_id)

    code = smoke.main(args(device_files, device_id, "--ingest"))

    out = capsys.readouterr().out
    assert code == 0
    assert f"PASS  {smoke.CHECK_INGEST}" in out
    assert "6/6 checks passed" in out
    ingest_conn = fake_conn.instances[-1]
    assert ingest_conn.client_id == device_id
    dtype = device_id.split("-")[0]
    expected_types = [
        *ALLOWED[dtype],
        ALLOWED[dtype][0],
        {"wearable": "vitals", "bedroom": "bed"}[dtype],
    ]
    assert [t for t, _ in ingest_conn.published] == [
        f"silvercare/v1/{device_id}/{m}" for m in expected_types
    ]
    sent_ids = [json.loads(p)["message_id"] for _, p in ingest_conn.published]
    assert sent_ids[3] == sent_ids[0]
    for message_id in sent_ids:
        assert message_id in out
    assert f"python scripts/ingest-check.py --device {device_id}" in out


def test_main_ingest_output_has_no_payload_values_or_keys(
    smoke, fake_conn, device_files, capsys
):
    fake_conn.publish_result = broker(WEARABLE)

    smoke.main(args(device_files, WEARABLE, "--ingest"))

    captured = capsys.readouterr()
    text = captured.out + captured.err
    for value in ("999", "33.4", "hr_bpm", "accel", "{", PRIVATE_KEY_TEXT, "synthetic"):
        assert value not in text


def test_main_ingest_skipped_when_an_earlier_check_fails(
    smoke, fake_conn, device_files, capsys
):
    fake_conn.publish_result = broker(WEARABLE)
    fake_conn.refuse_other_client = False

    code = smoke.main(args(device_files, WEARABLE, "--ingest"))

    captured = capsys.readouterr()
    assert code != 0
    assert f"FAIL  {smoke.CHECK_INGEST}" in captured.out
    assert "ingest-check.py" not in captured.out
    assert "Skipping" in captured.err
    assert len(fake_conn.instances) == 5  # no ingest connection


def test_main_ingest_fails_without_command_when_a_publish_gets_no_puback(
    smoke, fake_conn, device_files, capsys
):
    fake_conn.publish_result = broker(
        WEARABLE, fail_topic="silvercare/v1/wearable-0001/fall"
    )

    code = smoke.main(args(device_files, WEARABLE, "--ingest"))

    out = capsys.readouterr().out
    assert code != 0
    assert f"FAIL  {smoke.CHECK_INGEST}" in out
    assert "ingest-check.py" not in out


def test_main_without_ingest_flag_sends_no_ingest_messages(
    smoke, fake_conn, device_files, capsys
):
    fake_conn.publish_result = broker(WEARABLE)

    code = smoke.main(args(device_files, WEARABLE))

    out = capsys.readouterr().out
    assert code == 0
    assert smoke.CHECK_INGEST not in out
    assert len(fake_conn.instances) == 5


def test_run_ingest_returns_false_when_connect_fails(smoke, monkeypatch):
    class Refused(FakeConnection):
        def connect(self):
            return "refused"

    monkeypatch.setattr(smoke, "Connection", Refused)
    messages = smoke.build_ingest_messages("wearable", FIXED_NOW)

    assert smoke.run_ingest(WEARABLE, ENDPOINT, {}, messages) is False


def test_smoke_test_needs_no_aws_credentials(smoke):
    source = (REPO_ROOT / "scripts" / "iot-smoke-test.py").read_text()

    assert "boto3" not in source
    assert "botocore" not in source
