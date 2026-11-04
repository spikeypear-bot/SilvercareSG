"""Tests for `iot-smoke-test.py --alerts` (docs/features/vitals-alerts.md, criterion 27).

No network: sockets are blocked (conftest), the MQTT connection is a fake, and the
pause before the last message is patched out. The payloads are also checked with the
ingest Lambda's own validator (lambdas/ingest/src/validation.py, pure, loaded by path)
so the smoke test and the Lambda can't drift apart.
"""

from __future__ import annotations

import importlib.util
import itertools
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
FIXED_NOW = datetime(2026, 10, 18, 3, 15, 0, 120_000, tzinfo=UTC)
UUID_ANY_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)

# Default cloud-check thresholds (vitals-alerts decisions table).
HR_MAX = 120
SPO2_MIN = 92
SKIN_MIN, SKIN_MAX = 30.0, 38.0
HR_MIN = 50


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


def counter_ids():
    n = iter(range(1, 100))
    return lambda: f"00000000-0000-4000-8000-{next(n):012d}"


def as_rule_event(device_id, message, payload, received_at_ms=1792293300120):
    return {
        **json.loads(json.dumps(payload)),
        "device_id": device_id,
        "message": message,
        "received_at": received_at_ms,
    }


def alert_messages(smoke):
    return smoke.build_alert_messages(FIXED_NOW, counter_ids())


# ---- build_alert_messages ----


def test_alert_messages_are_five_in_the_spec_order(smoke):
    messages = alert_messages(smoke)

    assert [m for _, m, _ in messages] == [
        "vitals",
        "vitals",
        "vitals_alert",
        "vitals_alert",
        "vitals",
    ]


def test_vitals_heart_rates_are_180_then_185_then_182(smoke):
    messages = alert_messages(smoke)

    hrs = [p["hr_bpm"] for _, m, p in messages if m == "vitals"]
    assert hrs == [180, 185, 182]


def test_vitals_heart_rates_are_above_a_personal_limit_of_130(smoke):
    hrs = [p["hr_bpm"] for _, m, p in alert_messages(smoke) if m == "vitals"]

    assert min(hrs) > 130


def test_device_alerts_are_140_above_130_then_no_pulse_below_45(smoke):
    messages = alert_messages(smoke)

    device_alerts = [
        {k: p[k] for k in ("metric", "value", "limit", "direction", "held_s")}
        for _, m, p in messages
        if m == "vitals_alert"
    ]
    assert device_alerts == [
        {
            "metric": "hr_bpm",
            "value": 140,
            "limit": 130,
            "direction": "above",
            "held_s": 10,
        },
        {
            "metric": "hr_bpm",
            "value": 0,
            "limit": 45,
            "direction": "below",
            "held_s": 10,
        },
    ]


def test_vitals_alert_payload_has_exactly_envelope_and_contract_fields(smoke):
    (_, _, payload) = alert_messages(smoke)[2]

    assert set(payload) == {
        "schema_version",
        "message_id",
        "ts",
        "metric",
        "value",
        "limit",
        "direction",
        "held_s",
    }


def test_other_vitals_fields_are_normal_for_the_default_thresholds(smoke):
    for _, message, payload in alert_messages(smoke):
        if message != "vitals":
            continue
        assert SPO2_MIN <= payload["spo2_pct"]
        assert SKIN_MIN <= payload["skin_temp_c"] <= SKIN_MAX


def test_every_vitals_heart_rate_is_past_the_default_max(smoke):
    hrs = [p["hr_bpm"] for _, m, p in alert_messages(smoke) if m == "vitals"]

    assert all(hr > HR_MAX for hr in hrs)


def test_message_ts_step_forward_within_the_pairing_limit(smoke):
    messages = alert_messages(smoke)

    ts = [
        datetime.strptime(p["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        for _, _, p in messages
    ]
    assert ts[0] == FIXED_NOW.replace(microsecond=120_000)
    steps = [b - a for a, b in itertools.pairwise(ts)]
    assert all(timedelta(0) < s <= timedelta(minutes=3) for s in steps)
    # The two vitals that pair (1st and 2nd) are at most 3 minutes apart.
    assert ts[1] - ts[0] <= timedelta(minutes=3)


def test_alert_message_ids_are_unique(smoke):
    ids = [p["message_id"] for _, _, p in smoke.build_alert_messages(FIXED_NOW)]

    assert len(set(ids)) == 5


def test_alert_payloads_never_contain_rule_added_fields(smoke):
    for _, _, payload in alert_messages(smoke):
        assert not {"device_id", "message", "received_at"} & set(payload)


def test_alert_payloads_pass_the_ingest_lambda_validation(smoke, validation):
    handled = frozenset({"vitals", "vitals_alert"})

    for _, message, payload in alert_messages(smoke):
        result = validation.validate_event(
            as_rule_event(WEARABLE, message, payload), handled
        )
        assert isinstance(result, validation.ValidMessage), (message, result)


def test_alert_payloads_are_rejected_for_a_bedroom(smoke, validation):
    handled = frozenset({"vitals", "vitals_alert"})

    for _, message, payload in alert_messages(smoke):
        result = validation.validate_event(
            as_rule_event(BEDROOM, message, payload), handled
        )
        assert isinstance(result, validation.Rejection), message


@pytest.mark.parametrize("message", ["vitals", "vitals_alert"])
def test_alert_topics_are_the_device_own_topics(smoke, message):
    assert smoke.build_topic(WEARABLE, message) == f"silvercare/v1/{WEARABLE}/{message}"


def test_each_alert_payload_is_compact_json_under_8_kb(smoke):
    for _, _, payload in alert_messages(smoke):
        data = smoke.encode_payload(payload)
        assert len(data) < 8192
        assert b", " not in data


# ---- report_alerts ----


def test_report_alerts_prints_ids_and_expectations_but_no_values(smoke):
    import io

    messages = alert_messages(smoke)
    out = io.StringIO()

    smoke.report_alerts(WEARABLE, messages, out)

    text = out.getvalue()
    for _, _, payload in messages:
        assert payload["message_id"] in text
    assert "suppressed" in text
    assert "SilverCare CRITICAL alert" in text
    stripped = UUID_ANY_RE.sub("<id>", text)
    for value in ("180", "185", "182", "140", "hr_bpm", "{", "33.4"):
        assert value not in stripped, value


# ---- main --alerts with a fake connection ----


class FakeConnection:
    instances: ClassVar[list[FakeConnection]] = []
    fail_topic: ClassVar[str | None] = None
    refuse_other_client: ClassVar[bool] = True

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
        if topic == FakeConnection.fail_topic:
            return "timeout"
        own = f"silvercare/v1/{self.client_id}/"
        allowed = ("vitals", "vitals_alert", "fall", "status", "motion", "bed")
        if not topic.startswith(own):
            return "closed"
        message = topic[len(own) :]
        dtype = self.client_id.split("-")[0]
        per_type = {
            "wearable": {"vitals", "vitals_alert", "fall", "status"},
            "bedroom": {"motion", "bed", "status"},
        }
        return (
            "puback" if message in allowed and message in per_type[dtype] else "closed"
        )

    def close(self):
        pass


@pytest.fixture
def fake_conn(smoke, monkeypatch):
    FakeConnection.instances = []
    FakeConnection.fail_topic = None
    FakeConnection.refuse_other_client = True
    monkeypatch.setattr(smoke, "Connection", FakeConnection)
    sleeps: list[float] = []
    monkeypatch.setattr(smoke.time, "sleep", sleeps.append)
    FakeConnection.sleeps = sleeps
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


def test_main_alerts_publishes_five_messages_in_order(
    smoke, fake_conn, device_files, capsys
):
    code = smoke.main(args(device_files, WEARABLE, "--alerts"))

    out = capsys.readouterr().out
    assert code == 0
    assert f"PASS  {smoke.CHECK_ALERTS}" in out
    alerts_conn = fake_conn.instances[-1]
    assert alerts_conn.client_id == WEARABLE
    assert [t for t, _ in alerts_conn.published] == [
        f"silvercare/v1/{WEARABLE}/{m}"
        for m in ("vitals", "vitals", "vitals_alert", "vitals_alert", "vitals")
    ]
    for _, payload in alerts_conn.published:
        assert json.loads(payload)["message_id"] in out


def test_main_alerts_pauses_once_before_the_repeat(smoke, fake_conn, device_files):
    smoke.main(args(device_files, WEARABLE, "--alerts"))

    assert fake_conn.sleeps.count(smoke.ALERTS_REPEAT_PAUSE_S) == 1


def test_main_alerts_pauses_once_between_the_vitals_pair(
    smoke, fake_conn, device_files
):
    smoke.main(args(device_files, WEARABLE, "--alerts"))

    assert fake_conn.sleeps.count(smoke.ALERTS_PAIR_PAUSE_S) == 1


def test_main_alerts_refuses_a_bedroom(smoke, fake_conn, device_files, capsys):
    code = smoke.main(args(device_files, BEDROOM, "--alerts"))

    captured = capsys.readouterr()
    assert code == 2
    assert "wearables only" in captured.err
    assert fake_conn.instances == []


def test_main_alerts_skipped_when_an_earlier_check_fails(
    smoke, fake_conn, device_files, capsys
):
    fake_conn.refuse_other_client = False

    code = smoke.main(args(device_files, WEARABLE, "--alerts"))

    captured = capsys.readouterr()
    assert code != 0
    assert f"FAIL  {smoke.CHECK_ALERTS}" in captured.out
    assert "Skipping the alert messages" in captured.err
    assert len(fake_conn.instances) == 5  # no alerts connection


def test_main_alerts_fails_without_expectations_when_a_publish_gets_no_puback(
    smoke, fake_conn, device_files, capsys
):
    fake_conn.fail_topic = f"silvercare/v1/{WEARABLE}/vitals_alert"

    code = smoke.main(args(device_files, WEARABLE, "--alerts"))

    out = capsys.readouterr().out
    assert code != 0
    assert f"FAIL  {smoke.CHECK_ALERTS}" in out
    assert "Alert messages sent" not in out


def test_main_alerts_and_ingest_together_keep_the_ingest_command_last(
    smoke, fake_conn, device_files, capsys
):
    code = smoke.main(args(device_files, WEARABLE, "--ingest", "--alerts"))

    out = capsys.readouterr().out
    assert code == 0
    assert f"PASS  {smoke.CHECK_INGEST}" in out
    assert f"PASS  {smoke.CHECK_ALERTS}" in out
    assert (
        out.rstrip("\n")
        .splitlines()[-1]
        .lstrip()
        .startswith("python scripts/ingest-check.py")
    )


def test_main_alerts_output_has_no_payload_values_or_keys(
    smoke, fake_conn, device_files, capsys
):
    smoke.main(args(device_files, WEARABLE, "--alerts"))

    captured = capsys.readouterr()
    text = UUID_ANY_RE.sub("<id>", captured.out + captured.err)
    for value in ("180", "185", "182", "140", "hr_bpm", "{", PRIVATE_KEY_TEXT):
        assert value not in text, value


def test_main_without_alerts_flag_sends_no_alert_messages(
    smoke, fake_conn, device_files, capsys
):
    code = smoke.main(args(device_files, WEARABLE))

    out = capsys.readouterr().out
    assert code == 0
    assert smoke.CHECK_ALERTS not in out
    assert len(fake_conn.instances) == 5
