"""Tests for scripts/iot-smoke-test.py (docs/features/iot-core.md, criteria 22-25).

No network: the autouse no_network fixture blocks sockets, the check flow uses a fake
Connection, and the Connection class is driven by a fake paho client.
"""

from __future__ import annotations

import io
import json
import re
import ssl
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from helpers import PRIVATE_KEY_TEXT, REPO_ROOT

WEARABLE = "wearable-0001"
BEDROOM = "bedroom-0001"
ENDPOINT = "synthetic-ats.iot.ap-southeast-1.amazonaws.com"
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
ENVELOPE_KEYS = {"schema_version", "message_id", "ts"}
STATUS_KEYS = {
    "wearable": ENVELOPE_KEYS | {"battery_pct", "worn", "charging", "fw_version"},
    "bedroom": ENVELOPE_KEYS | {"battery_pct", "fw_version"},
}
FIXED_NOW = datetime(2026, 10, 18, 3, 15, 0, 120_000, tzinfo=UTC)
FIXED_ID = "00000000-0000-4000-8000-000000000001"


# ---- device type and topics ----


@pytest.mark.parametrize(
    ("device_id", "expected"),
    [(WEARABLE, "wearable"), ("wearable-0020", "wearable"), (BEDROOM, "bedroom")],
)
def test_device_type_from_id(smoke, device_id, expected):
    assert smoke.device_type(device_id) == expected


@pytest.mark.parametrize(
    "bad_id", ["wearable-001", "watch-0001", "Wearable-0001", "wearable-0001\n", ""]
)
def test_device_type_rejects_malformed_id(smoke, bad_id):
    with pytest.raises(ValueError):
        smoke.device_type(bad_id)


@pytest.mark.parametrize("message", ["status", "vitals", "fall", "motion", "bed"])
def test_build_topic(smoke, message):
    assert smoke.build_topic(WEARABLE, message) == f"silvercare/v1/{WEARABLE}/{message}"


@pytest.mark.parametrize("bad_id", ["wearable-1", "+", "#", "wearable-0001/status"])
def test_build_topic_rejects_bad_device_id(smoke, bad_id):
    with pytest.raises(ValueError):
        smoke.build_topic(bad_id, "status")


@pytest.mark.parametrize("bad_message", ["", "Status", "status/x", "+", "#", "a b"])
def test_build_topic_rejects_bad_message(smoke, bad_message):
    with pytest.raises(ValueError):
        smoke.build_topic(WEARABLE, bad_message)


# ---- other device / disallowed message ----


@pytest.mark.parametrize(
    ("device_id", "expected"),
    [
        (WEARABLE, "wearable-0002"),
        ("wearable-0002", WEARABLE),
        ("wearable-0020", WEARABLE),
        (BEDROOM, "bedroom-0002"),
        ("bedroom-0007", BEDROOM),
    ],
)
def test_other_device_id_is_a_different_device_of_the_same_type(
    smoke, device_id, expected
):
    other = smoke.other_device_id(device_id)

    assert other == expected
    assert other != device_id
    assert smoke.device_type(other) == smoke.device_type(device_id)


def test_other_device_id_is_in_the_fleet(smoke, device_keys):
    devices = device_keys.load_devices(REPO_ROOT / "infra" / "devices" / "devices.json")
    ids = {d["id"] for d in devices}
    for device_id in ids:
        assert smoke.other_device_id(device_id) in ids


@pytest.mark.parametrize(
    ("dtype", "expected"), [("wearable", "motion"), ("bedroom", "vitals")]
)
def test_disallowed_message_per_type(smoke, dtype, expected):
    assert smoke.DISALLOWED_MESSAGE[dtype] == expected
    assert expected not in smoke.ALLOWED_MESSAGES[dtype]


def test_allowed_messages_match_policy(smoke):
    assert smoke.ALLOWED_MESSAGES == {
        "wearable": ("vitals", "fall", "status"),
        "bedroom": ("motion", "bed", "status"),
    }


@pytest.mark.parametrize("device_id", [WEARABLE, BEDROOM])
def test_other_client_id_differs_from_thing_name(smoke, device_id):
    client_id = smoke.other_client_id(device_id)

    assert client_id != device_id
    assert client_id


# ---- payloads (docs/metrics.md) ----


@pytest.mark.parametrize("dtype", ["wearable", "bedroom"])
def test_status_payload_has_exactly_envelope_and_status_fields(smoke, dtype):
    payload = smoke.build_status_payload(dtype)

    assert set(payload) == STATUS_KEYS[dtype]


@pytest.mark.parametrize("dtype", ["wearable", "bedroom"])
def test_status_payload_envelope_types(smoke, dtype):
    payload = smoke.build_status_payload(dtype)

    assert type(payload["schema_version"]) is int
    assert payload["schema_version"] == 1
    assert uuid.UUID(payload["message_id"]).version == 4
    assert TS_RE.fullmatch(payload["ts"])


def test_wearable_status_field_types_and_ranges(smoke):
    payload = smoke.build_status_payload("wearable")

    assert type(payload["battery_pct"]) is int
    assert 0 <= payload["battery_pct"] <= 100
    assert type(payload["worn"]) is bool
    assert type(payload["charging"]) is bool
    assert isinstance(payload["fw_version"], str)
    assert 0 < len(payload["fw_version"]) <= 32


def test_bedroom_status_field_types_and_ranges(smoke):
    payload = smoke.build_status_payload("bedroom")

    battery = payload["battery_pct"]
    assert battery is None or (type(battery) is int and 0 <= battery <= 100)
    assert isinstance(payload["fw_version"], str)
    assert 0 < len(payload["fw_version"]) <= 32


def test_status_payload_uses_given_time_and_message_id(smoke):
    payload = smoke.build_status_payload("wearable", FIXED_NOW, FIXED_ID)

    assert payload["ts"] == "2026-10-18T03:15:00.120Z"
    assert payload["message_id"] == FIXED_ID


def test_each_payload_gets_a_new_message_id(smoke):
    first = smoke.build_status_payload("bedroom")["message_id"]
    second = smoke.build_status_payload("bedroom")["message_id"]

    assert first != second


def test_status_payload_rejects_unknown_type(smoke):
    with pytest.raises(ValueError):
        smoke.build_status_payload("watch")


def test_status_payload_holds_no_resident_data(smoke):
    for dtype in ("wearable", "bedroom"):
        payload = smoke.build_status_payload(dtype)
        assert not {"resident_id", "name", "nric", "address"} & set(payload)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 10, 18, 3, 15, tzinfo=UTC), "2026-10-18T03:15:00.000Z"),
        (
            datetime(2026, 10, 18, 3, 12, 45, 120_999, tzinfo=UTC),
            "2026-10-18T03:12:45.120Z",
        ),
        (
            datetime(2026, 10, 18, 11, 15, tzinfo=timezone(timedelta(hours=8))),
            "2026-10-18T03:15:00.000Z",
        ),
    ],
    ids=["whole-second", "truncates-to-ms", "converts-sgt-to-utc"],
)
def test_format_ts(smoke, now, expected):
    assert smoke.format_ts(now) == expected


def test_envelope_has_only_envelope_fields(smoke):
    assert set(smoke.build_envelope()) == ENVELOPE_KEYS


@pytest.mark.parametrize("dtype", ["wearable", "bedroom"])
def test_encoded_payload_is_compact_json_under_8_kb(smoke, dtype):
    payload = smoke.build_status_payload(dtype, FIXED_NOW, FIXED_ID)

    data = smoke.encode_payload(payload)

    assert json.loads(data) == payload
    assert b" " not in data
    assert len(data) < 8 * 1024


# ---- report and exit code ----


def test_report_all_pass_returns_zero(smoke):
    out = io.StringIO()

    code = smoke.report([("check a", True), ("check b", True)], out)

    assert code == 0
    assert out.getvalue().splitlines() == [
        "PASS  check a",
        "PASS  check b",
        "2/2 checks passed",
    ]


def test_report_any_fail_returns_non_zero(smoke):
    out = io.StringIO()

    code = smoke.report([("check a", True), ("check b", False)], out)

    assert code != 0
    assert out.getvalue().splitlines() == [
        "PASS  check a",
        "FAIL  check b",
        "1/2 checks passed",
    ]


def test_report_all_fail_returns_non_zero(smoke):
    assert smoke.report([("a", False), ("b", False)], io.StringIO()) != 0


def test_report_no_results_returns_non_zero(smoke):
    assert smoke.report([], io.StringIO()) != 0


def test_report_accepts_a_generator(smoke):
    out = io.StringIO()

    code = smoke.report((r for r in [("a", True)]), out)

    assert code == 0
    assert "1/1 checks passed" in out.getvalue()


def test_report_defaults_to_stdout(smoke, capsys):
    smoke.report([("a", True)])

    assert "PASS  a" in capsys.readouterr().out


# ---- check flow (fake Connection) ----


class FakeConnection:
    """Stands in for smoke.Connection. Behaviour is scripted per client ID."""

    instances: ClassVar[list[FakeConnection]] = []
    script: ClassVar[dict] = {}

    def __init__(self, endpoint, client_id, tls):
        self.endpoint = endpoint
        self.client_id = client_id
        self.tls = tls
        self.published: list[tuple[str, bytes]] = []
        self.closed_calls = 0
        FakeConnection.instances.append(self)

    def connect(self):
        return self.script["connect"].get(self.client_id, "connected")

    def publish(self, topic, payload):
        self.published.append((topic, payload))
        return self.script["publish"](topic)

    def close(self):
        self.closed_calls += 1


def broker_like(device_id: str):
    """Script that behaves like IoT Core with the feature's policies."""
    dtype = device_id.split("-")[0]
    allowed = {
        f"silvercare/v1/{device_id}/{m}"
        for m in {
            "wearable": ("vitals", "fall", "status"),
            "bedroom": ("motion", "bed", "status"),
        }[dtype]
    }

    def publish(topic):
        return "puback" if topic in allowed else "closed"

    return {"connect": {f"{device_id}-not-me": "refused"}, "publish": publish}


@pytest.fixture
def fake_conn(smoke, monkeypatch):
    FakeConnection.instances = []
    monkeypatch.setattr(smoke, "Connection", FakeConnection)
    return FakeConnection


TLS = {"ca": "ca.pem", "cert": "dev.crt", "key": "dev.key"}


@pytest.mark.parametrize("device_id", [WEARABLE, BEDROOM])
def test_all_checks_pass_against_a_correct_broker(smoke, fake_conn, device_id):
    fake_conn.script = broker_like(device_id)

    results = smoke.run_checks(device_id, ENDPOINT, TLS)

    assert [name for name, _ in results] == [
        smoke.CHECK_CONNECT,
        smoke.CHECK_PUBLISH,
        smoke.CHECK_OTHER_DEVICE,
        smoke.CHECK_DISALLOWED,
        smoke.CHECK_OTHER_CLIENT_ID,
    ]
    assert all(passed for _, passed in results)


def test_each_check_uses_a_fresh_connection_that_is_closed(smoke, fake_conn):
    fake_conn.script = broker_like(WEARABLE)

    smoke.run_checks(WEARABLE, ENDPOINT, TLS)

    assert len(fake_conn.instances) == 5
    assert all(c.closed_calls == 1 for c in fake_conn.instances)
    assert all(c.endpoint == ENDPOINT and c.tls == TLS for c in fake_conn.instances)


def test_checks_use_own_client_id_then_another(smoke, fake_conn):
    fake_conn.script = broker_like(WEARABLE)

    smoke.run_checks(WEARABLE, ENDPOINT, TLS)

    ids = [c.client_id for c in fake_conn.instances]
    assert ids[:4] == [WEARABLE] * 4
    assert ids[4] != WEARABLE


@pytest.mark.parametrize(
    ("device_id", "other", "disallowed"),
    [(WEARABLE, "wearable-0002", "motion"), (BEDROOM, "bedroom-0002", "vitals")],
)
def test_checks_publish_to_expected_topics(
    smoke, fake_conn, device_id, other, disallowed
):
    fake_conn.script = broker_like(device_id)

    smoke.run_checks(device_id, ENDPOINT, TLS)

    topics = [[t for t, _ in c.published] for c in fake_conn.instances]
    assert topics == [
        [],
        [f"silvercare/v1/{device_id}/status"],
        [f"silvercare/v1/{other}/status"],
        [f"silvercare/v1/{device_id}/{disallowed}"],
        [],
    ]


@pytest.mark.parametrize("device_id", [WEARABLE, BEDROOM])
def test_own_topic_check_publishes_a_valid_status_payload(smoke, fake_conn, device_id):
    fake_conn.script = broker_like(device_id)

    smoke.run_checks(device_id, ENDPOINT, TLS)

    _, data = fake_conn.instances[1].published[0]
    payload = json.loads(data)
    assert set(payload) == STATUS_KEYS[device_id.split("-")[0]]
    assert payload["schema_version"] == 1


def _results(smoke, device_id=WEARABLE):
    return dict(smoke.run_checks(device_id, ENDPOINT, TLS))


def test_connect_check_fails_when_connection_refused(smoke, fake_conn):
    script = broker_like(WEARABLE)
    script["connect"][WEARABLE] = "refused"
    fake_conn.script = script

    results = _results(smoke)

    assert results[smoke.CHECK_CONNECT] is False
    # Every check that needs a connection fails too; nothing is published.
    assert results[smoke.CHECK_PUBLISH] is False
    assert results[smoke.CHECK_OTHER_DEVICE] is False
    assert results[smoke.CHECK_DISALLOWED] is False
    assert all(not c.published for c in fake_conn.instances)


@pytest.mark.parametrize("outcome", ["closed", "timeout"])
def test_publish_check_fails_without_puback(smoke, fake_conn, outcome):
    script = broker_like(WEARABLE)
    script["publish"] = lambda topic: outcome
    fake_conn.script = script

    assert _results(smoke)[smoke.CHECK_PUBLISH] is False


@pytest.mark.parametrize("outcome", ["puback", "timeout"])
def test_other_device_check_fails_unless_connection_closed(smoke, fake_conn, outcome):
    script = broker_like(WEARABLE)
    script["publish"] = lambda topic: (
        outcome if "wearable-0002" in topic else broker_like(WEARABLE)["publish"](topic)
    )
    fake_conn.script = script

    results = _results(smoke)

    assert results[smoke.CHECK_OTHER_DEVICE] is False
    assert results[smoke.CHECK_DISALLOWED] is True


@pytest.mark.parametrize("outcome", ["puback", "timeout"])
def test_disallowed_check_fails_unless_connection_closed(smoke, fake_conn, outcome):
    script = broker_like(WEARABLE)
    script["publish"] = lambda topic: (
        outcome
        if topic.endswith("/motion")
        else broker_like(WEARABLE)["publish"](topic)
    )
    fake_conn.script = script

    results = _results(smoke)

    assert results[smoke.CHECK_DISALLOWED] is False
    assert results[smoke.CHECK_OTHER_DEVICE] is True


@pytest.mark.parametrize(
    ("outcome", "passed"),
    [
        ("refused", True),
        ("closed", True),
        ("connected", False),
        ("timeout", False),
        ("error", False),
    ],
)
def test_other_client_id_check(smoke, fake_conn, outcome, passed):
    script = broker_like(WEARABLE)
    script["connect"] = {smoke.other_client_id(WEARABLE): outcome}
    fake_conn.script = script

    assert _results(smoke)[smoke.CHECK_OTHER_CLIENT_ID] is passed


# ---- main ----


@pytest.fixture
def device_files(tmp_path: Path) -> dict[str, Path]:
    """Placeholder CA, cert, and key files in tmp_path (main only checks they exist)."""
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    ca = tmp_path / "ca.pem"
    ca.write_text("synthetic ca placeholder\n")
    (keys_dir / f"{WEARABLE}.crt").write_text("synthetic cert placeholder\n")
    (keys_dir / f"{WEARABLE}.key").write_text("synthetic key placeholder\n")
    return {"ca": ca, "keys": keys_dir}


def main_args(device_files, device_id=WEARABLE, endpoint=ENDPOINT, *extra):
    return [
        "--device",
        device_id,
        "--endpoint",
        endpoint,
        "--ca-file",
        str(device_files["ca"]),
        "--keys-dir",
        str(device_files["keys"]),
        *extra,
    ]


def test_main_exits_zero_when_all_checks_pass(smoke, fake_conn, device_files, capsys):
    fake_conn.script = broker_like(WEARABLE)

    code = smoke.main(main_args(device_files))

    out = capsys.readouterr().out
    assert code == 0
    assert out.count("PASS") == 5
    assert "FAIL" not in out


def test_main_exits_non_zero_when_a_check_fails(smoke, fake_conn, device_files, capsys):
    script = broker_like(WEARABLE)
    script["connect"] = {smoke.other_client_id(WEARABLE): "connected"}
    fake_conn.script = script

    code = smoke.main(main_args(device_files))

    out = capsys.readouterr().out
    assert code != 0
    assert f"FAIL  {smoke.CHECK_OTHER_CLIENT_ID}" in out


def test_main_passes_device_files_to_connections(smoke, fake_conn, device_files):
    fake_conn.script = broker_like(WEARABLE)

    smoke.main(main_args(device_files))

    assert fake_conn.instances[0].tls == {
        "ca": str(device_files["ca"]),
        "cert": str(device_files["keys"] / f"{WEARABLE}.crt"),
        "key": str(device_files["keys"] / f"{WEARABLE}.key"),
    }


def test_main_output_has_only_check_names_and_results(
    smoke, fake_conn, device_files, capsys
):
    fake_conn.script = broker_like(WEARABLE)

    smoke.main(main_args(device_files))

    out, err = capsys.readouterr()
    names = {
        smoke.CHECK_CONNECT,
        smoke.CHECK_PUBLISH,
        smoke.CHECK_OTHER_DEVICE,
        smoke.CHECK_DISALLOWED,
        smoke.CHECK_OTHER_CLIENT_ID,
    }
    lines = out.splitlines()
    assert {line.split("  ", 1)[1] for line in lines[:-1]} == names
    assert re.fullmatch(r"\d+/\d+ checks passed", lines[-1])
    assert err == ""


def test_main_output_never_includes_payloads_keys_or_certs(
    smoke, fake_conn, device_files, capsys
):
    fake_conn.script = broker_like(WEARABLE)

    smoke.main(main_args(device_files))

    out, err = capsys.readouterr()
    published = [p for c in fake_conn.instances for _, p in c.published]
    assert published
    text = out + err
    for data in published:
        payload = json.loads(data)
        assert payload["message_id"] not in text
        assert payload["ts"] not in text
    for field in ("schema_version", "message_id", "battery_pct", "fw_version", "{"):
        assert field not in text
    assert "placeholder" not in text
    assert PRIVATE_KEY_TEXT not in text
    assert "CERTIFICATE" not in text


@pytest.mark.parametrize(
    "bad_id", ["wearable-001", "watch-0001", "../wearable-0001", ""]
)
def test_main_rejects_malformed_device_id(smoke, fake_conn, device_files, bad_id):
    code = smoke.main(main_args(device_files, device_id=bad_id))

    assert code != 0
    assert fake_conn.instances == []


@pytest.mark.parametrize(
    "bad_endpoint",
    ["https://synthetic.example.com", "localhost", "synthetic.example.com:8883", ""],
)
def test_main_rejects_endpoint_that_is_not_a_host_name(
    smoke, fake_conn, device_files, bad_endpoint, capsys
):
    code = smoke.main(main_args(device_files, endpoint=bad_endpoint))

    assert code != 0
    assert "--endpoint" in capsys.readouterr().err
    assert fake_conn.instances == []


@pytest.mark.parametrize("missing", ["ca", "crt", "key"])
def test_main_fails_before_connecting_if_a_file_is_missing(
    smoke, fake_conn, device_files, missing, capsys
):
    target = {
        "ca": device_files["ca"],
        "crt": device_files["keys"] / f"{WEARABLE}.crt",
        "key": device_files["keys"] / f"{WEARABLE}.key",
    }[missing]
    target.unlink()

    code = smoke.main(main_args(device_files))

    assert code != 0
    assert str(target) in capsys.readouterr().err
    assert fake_conn.instances == []


def test_main_job_flag_is_not_available_yet(smoke, fake_conn, device_files, capsys):
    code = smoke.main(main_args(device_files, WEARABLE, ENDPOINT, "--job"))

    assert code != 0
    assert "ota-updates" in capsys.readouterr().err
    assert fake_conn.instances == []


# ---- Connection with a fake paho client (criteria 22-23) ----


class FakeClient:
    """Stands in for paho.mqtt.client.Client. Never opens a socket."""

    instances: ClassVar[list[FakeClient]] = []
    # What the fake broker does: "connack" ok/refused/none, "puback" ok/close/none.
    behaviour: ClassVar[dict] = {}

    def __init__(self, callback_api_version, client_id, protocol, clean_session):
        self.callback_api_version = callback_api_version
        self.client_id = client_id
        self.protocol = protocol
        self.clean_session = clean_session
        self.tls_kwargs: dict = {}
        self.connect_args: tuple = ()
        self.published: list[dict] = []
        self.disconnected = False
        self.pending: list = []
        self.next_mid = 1
        FakeClient.instances.append(self)

    def tls_set(self, **kwargs):
        self.tls_kwargs = kwargs

    def connect(self, host, port, keepalive):
        self.connect_args = (host, port, keepalive)
        if self.behaviour.get("connect_raises"):
            raise OSError("synthetic tls failure")
        connack = self.behaviour.get("connack", "ok")
        if connack == "ok":
            self.pending.append(lambda: self._connack(False))
        elif connack == "refused":
            self.pending.append(lambda: self._connack(True))
        elif connack == "close":
            self.pending.append(self._close)

    def _connack(self, failure):
        self.on_connect(self, None, {}, SimpleNamespace(is_failure=failure), None)

    def _close(self):
        self.on_disconnect(self, None, {}, SimpleNamespace(is_failure=True), None)

    def publish(self, topic, payload, qos, retain):
        mid = self.next_mid
        self.next_mid += 1
        self.published.append(
            {"topic": topic, "payload": payload, "qos": qos, "retain": retain}
        )
        puback = self.behaviour.get("puback", "ok")
        if puback == "ok":
            self.pending.append(
                lambda: self.on_publish(self, None, mid, SimpleNamespace(), None)
            )
        elif puback == "close":
            self.pending.append(self._close)
        elif puback == "loop_error":
            self.pending.append("error")
        return SimpleNamespace(rc=0, mid=mid)

    def loop(self, timeout):
        if self.pending:
            event = self.pending.pop(0)
            if event == "error":
                return 7  # MQTT_ERR_CONN_LOST
            event()
        return 0

    def disconnect(self):
        self.disconnected = True


@pytest.fixture
def fake_client(smoke, monkeypatch):
    FakeClient.instances = []
    FakeClient.behaviour = {}
    monkeypatch.setattr(smoke.mqtt, "Client", FakeClient)
    monkeypatch.setattr(smoke, "TIMEOUT_S", 0.05)
    return FakeClient


def make_connection(smoke, client_id=WEARABLE):
    return smoke.Connection(ENDPOINT, client_id, TLS)


def test_connection_uses_mutual_tls_with_the_device_files(smoke, fake_client):
    make_connection(smoke)

    kwargs = fake_client.instances[0].tls_kwargs
    assert kwargs["ca_certs"] == TLS["ca"]
    assert kwargs["certfile"] == TLS["cert"]
    assert kwargs["keyfile"] == TLS["key"]
    assert kwargs["cert_reqs"] == ssl.CERT_REQUIRED


def test_connection_uses_given_client_id_and_clean_session(smoke, fake_client):
    make_connection(smoke, client_id="wearable-0001-not-me")

    client = fake_client.instances[0]
    assert client.client_id == "wearable-0001-not-me"
    assert client.clean_session is True


def test_connect_uses_port_8883(smoke, fake_client):
    assert make_connection(smoke).connect() == "connected"

    host, port, _ = fake_client.instances[0].connect_args
    assert (host, port) == (ENDPOINT, 8883)


@pytest.mark.parametrize(
    ("connack", "expected"),
    [
        ("ok", "connected"),
        ("refused", "refused"),
        ("close", "closed"),
        ("none", "timeout"),
    ],
)
def test_connect_outcomes(smoke, fake_client, connack, expected):
    fake_client.behaviour = {"connack": connack}

    assert make_connection(smoke).connect() == expected


def test_connect_tls_failure_returns_error(smoke, fake_client):
    fake_client.behaviour = {"connect_raises": True}

    assert make_connection(smoke).connect() == "error"


def test_publish_is_qos1_not_retained(smoke, fake_client):
    conn = make_connection(smoke)
    conn.connect()

    assert conn.publish("silvercare/v1/wearable-0001/status", b"{}") == "puback"

    sent = fake_client.instances[0].published[0]
    assert sent["qos"] == 1
    assert sent["retain"] is False


@pytest.mark.parametrize(
    ("puback", "expected"),
    [
        ("ok", "puback"),
        ("close", "closed"),
        ("loop_error", "closed"),
        ("none", "timeout"),
    ],
)
def test_publish_outcomes(smoke, fake_client, puback, expected):
    fake_client.behaviour = {"puback": puback}
    conn = make_connection(smoke)
    conn.connect()

    assert conn.publish("silvercare/v1/wearable-0001/status", b"{}") == expected


def test_close_disconnects_an_open_connection(smoke, fake_client):
    conn = make_connection(smoke)
    conn.connect()

    conn.close()

    assert fake_client.instances[0].disconnected is True


def test_close_skips_disconnect_after_broker_closed(smoke, fake_client):
    fake_client.behaviour = {"connack": "close"}
    conn = make_connection(smoke)
    conn.connect()

    conn.close()

    assert fake_client.instances[0].disconnected is False


def test_full_run_with_fake_client_never_opens_a_socket(
    smoke, fake_client, device_files, capsys
):
    # All publishes get PUBACK, so checks 3 and 4 fail, but no network is touched.
    code = smoke.main(main_args(device_files))

    out = capsys.readouterr().out
    assert code != 0
    assert len(fake_client.instances) == 5
    assert f"FAIL  {smoke.CHECK_OTHER_DEVICE}" in out
    assert f"FAIL  {smoke.CHECK_DISALLOWED}" in out
