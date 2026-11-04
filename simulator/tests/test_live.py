"""Live-run logic with a fake MQTT client and a fake clock (criteria 15 to 19).

Nothing here opens a network connection or reads a key: the key, certificate, and CA
files are empty placeholders in tmp_path, only checked for existence, and the client
factory is fake (except the last test, which builds a real paho client from a
throwaway certificate generated in tmp_path and never connects it).
"""

from __future__ import annotations

import io
import itertools
import json
import re
import signal
import threading
from collections import Counter

import clock as clock_mod
import contract
import fakes
import main
import pytest
from conftest import START_EPOCH_MS, run_dry, ts_to_epoch_ms
from fakes import OK, RAISE, REFUSE, SILENT, World

ENDPOINT = "example-ats.iot.ap-southeast-1.amazonaws.com"
ALL = [f"wearable-{n:04d}" for n in range(1, 21)] + [
    f"bedroom-{n:04d}" for n in range(1, 21)
]
SUMMARY_RE = re.compile(
    r"^\d\d:\d\d SGT routine \d\d:\d\d \| connected (\d+)/(\d+) \| sent vitals (\d+)"
    r" status (\d+) bed (\d+) motion (\d+) \| dropped (\d+) \| failed (\d+)$"
)
PROBLEM_RE = re.compile(
    r"^(wearable|bedroom)-\d{4}: (connect refused \([A-Za-z0-9 ]+\)"
    r"|disconnected \([A-Za-z0-9 ]+\)|connect failed \([A-Za-z]+\)"
    r"|connection error \([A-Za-z]+\)|connection lost \(rc -?\d+\)"
    r"|connect timed out)$"
)


@pytest.fixture
def keys(tmp_path):
    folder = tmp_path / "device-keys"
    folder.mkdir()
    for device_id in ALL:
        (folder / f"{device_id}.crt").write_text("placeholder", encoding="utf-8")
        (folder / f"{device_id}.key").write_text("placeholder", encoding="utf-8")
    ca = tmp_path / "AmazonRootCA1.pem"
    ca.write_text("placeholder", encoding="utf-8")
    return folder, ca


class Live:
    def __init__(self, code, stdout, stderr, world):
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.world = world

    @property
    def out_lines(self):
        return self.stdout.splitlines()

    def summaries(self):
        return [
            SUMMARY_RE.fullmatch(line) for line in self.out_lines if " SGT " in line
        ]

    def total(self):
        return self.out_lines[-1]

    def problems(self):
        return [line for line in self.out_lines if PROBLEM_RE.fullmatch(line)]


def run_live(keys, world, argv, *, stop=None, start_at="21:58", seed="3"):
    folder, ca = keys
    out, err = io.StringIO(), io.StringIO()
    code = main.main(
        [
            "--endpoint",
            ENDPOINT,
            "--keys-dir",
            str(folder),
            "--ca-file",
            str(ca),
            "--random-seed",
            seed,
            "--start-at",
            start_at,
            *argv,
        ],
        stdout=out,
        stderr=err,
        clock=clock_mod.RealClock(world.time.monotonic, world.time.sleep),
        epoch_ms=lambda: START_EPOCH_MS,
        client_factory=world.factory,
        stop=stop,
    )
    return Live(code, out.getvalue(), err.getvalue(), world)


def assert_clean_output(result, tmp_path):
    text = result.stdout + result.stderr
    for leak in (
        "{",
        "hr_bpm",
        "message_id",
        "placeholder",
        str(tmp_path),
        "device-keys",
        result.world.secret_text,
    ):
        assert leak not in text, leak
    for line in result.out_lines:
        assert (
            line.startswith(("simulator live:", "total "))
            or SUMMARY_RE.fullmatch(line)
            or PROBLEM_RE.fullmatch(line)
        ), line
    for line in result.stderr.splitlines():
        assert line.startswith("error: "), line


def intervals(times):
    return [b - a for a, b in itertools.pairwise(times)]


# ---- connecting ----


def test_one_client_per_device_with_its_id_on_port_8883(keys, tmp_path):
    world = World(fakes.FakeTime())
    result = run_live(keys, world, ["--duration", "10s"])
    assert result.code == 0
    assert world.created == ALL
    assert [d for _, d, *_ in world.connects] == ALL
    assert {(host, port) for _, _, host, port, _ in world.connects} == {
        (ENDPOINT, 8883)
    }
    assert all(keepalive > 0 for *_, keepalive in world.connects)
    assert_clean_output(result, tmp_path)


def test_connections_start_at_most_10_per_second(keys):
    world = World(fakes.FakeTime())
    run_live(keys, world, ["--duration", "10s"])
    times = sorted(t for t, *_ in world.connects)
    for t in times:
        assert sum(1 for u in times if t <= u < t + 1000) <= 10


def test_reconnects_also_count_towards_10_per_second(keys):
    # Every connection is closed right away, so all 40 keep reconnecting.
    world = World(fakes.FakeTime(), drop=lambda d, t: True)
    run_live(keys, world, ["--duration", "2m"])
    times = sorted(t for t, *_ in world.connects)
    assert len(times) > 80
    for t in times:
        assert sum(1 for u in times if t <= u < t + 1000) <= 10


def test_refused_connection_backs_off_from_1_s_doubling_to_60_s(keys):
    world = World(
        fakes.FakeTime(),
        policy=lambda d, n, t: REFUSE if d == "wearable-0001" else OK,
    )
    result = run_live(
        keys, world, ["--duration", "6m", "--devices", "wearable-0001,wearable-0002"]
    )
    assert result.code == 0
    gaps = intervals(world.connect_times("wearable-0001"))
    assert len(gaps) >= 9
    for i, gap in enumerate(gaps):
        nominal = min(1000 * 2**i, 60_000)
        assert nominal <= gap <= nominal + 200, (i, gap)


def test_broker_closing_at_once_still_backs_off_to_60_s(keys):
    world = World(
        fakes.FakeTime(),
        drop=lambda d, t: d == "wearable-0001",
    )
    run_live(
        keys, world, ["--duration", "6m", "--devices", "wearable-0001,wearable-0002"]
    )
    gaps = intervals(world.connect_times("wearable-0001"))
    assert all(gap >= 1000 for gap in gaps)
    assert max(gaps) >= 60_000
    assert all(gap <= 60_000 + 200 for gap in gaps)
    assert gaps == sorted(gaps)


def test_backoff_resets_after_a_stable_connection(keys):
    dropped = set()

    def drop(device_id, t):
        if device_id == "wearable-0001" and t >= 90_000 and "a" not in dropped:
            dropped.add("a")
            return True
        if device_id == "wearable-0001" and t >= 200_000 and "b" not in dropped:
            dropped.add("b")
            return True
        return False

    world = World(fakes.FakeTime(), drop=drop)
    run_live(
        keys, world, ["--duration", "5m", "--devices", "wearable-0001,wearable-0002"]
    )
    connects = world.connect_times("wearable-0001")
    drops = [t for t, d in world.drops if d == "wearable-0001"]
    assert len(connects) == 3 and len(drops) == 2
    for drop_t, reconnect_t in zip(drops, connects[1:]):
        assert 1000 <= reconnect_t - drop_t <= 1200


def test_connect_exception_retries_with_backoff_and_prints_only_the_type(
    keys, tmp_path
):
    world = World(
        fakes.FakeTime(),
        policy=lambda d, n, t: RAISE if d == "bedroom-0002" and n <= 3 else OK,
    )
    result = run_live(
        keys, world, ["--duration", "1m", "--devices", "wearable-0001,bedroom-0002"]
    )
    assert result.code == 0
    gaps = intervals(world.connect_times("bedroom-0002"))
    assert len(gaps) == 3
    assert all(gap >= 1000 for gap in gaps)
    assert "bedroom-0002: connect failed (OSError)" in result.out_lines
    assert "/secrets" not in result.stdout
    assert_clean_output(result, tmp_path)


def test_no_connack_times_out_and_retries(keys, tmp_path):
    world = World(
        fakes.FakeTime(),
        policy=lambda d, n, t: SILENT if d == "wearable-0001" and n == 1 else OK,
    )
    result = run_live(
        keys, world, ["--duration", "1m", "--devices", "wearable-0001,wearable-0002"]
    )
    first, second = world.connect_times("wearable-0001")[:2]
    assert 10_000 <= second - first <= 11_500
    assert "wearable-0001: connect timed out" in result.out_lines
    assert_clean_output(result, tmp_path)


def test_connection_problems_print_device_id_and_reason_only(keys, tmp_path):
    world = World(
        fakes.FakeTime(),
        policy=lambda d, n, t: REFUSE if d == "wearable-0003" and n == 1 else OK,
    )
    result = run_live(keys, world, ["--duration", "30s"])
    assert "wearable-0003: connect refused (Not authorized)" in result.out_lines
    assert_clean_output(result, tmp_path)


def test_loop_exception_prints_only_the_type_and_reconnects(
    keys, tmp_path, monkeypatch
):
    world = World(fakes.FakeTime())
    original = fakes.FakeClient.loop
    raised = []

    def loop(self, timeout=1.0):
        if (
            self.device_id == "wearable-0002"
            and self.connected
            and not raised
            and self.world.time.elapsed_ms() >= 20_000
        ):
            raised.append(1)
            self.connected = False
            raise OSError(f"{tmp_path}/wearable-0002.key Synthetic secret text")
        return original(self, timeout)

    monkeypatch.setattr(fakes.FakeClient, "loop", loop)
    result = run_live(
        keys, world, ["--duration", "1m", "--devices", "wearable-0001,wearable-0002"]
    )
    assert "wearable-0002: connection error (OSError)" in result.out_lines
    assert len(world.connect_times("wearable-0002")) == 2
    assert_clean_output(result, tmp_path)


# ---- publishing ----


def test_publishes_use_qos_1_retain_false_and_follow_the_contract(keys):
    world = World(fakes.FakeTime())
    result = run_live(keys, world, ["--duration", "3m"])
    assert result.code == 0
    assert len(world.published) > 100
    for p in world.published:
        assert p["qos"] == 1
        assert p["retain"] is False
        assert isinstance(p["payload"], (bytes, bytearray))
        assert len(p["payload"]) < 8 * 1024
        payload = json.loads(p["payload"])
        assert contract.problems(p["topic"], payload) == []
        assert p["topic"].split("/")[2] == p["device_id"]
    ids = [json.loads(p["payload"])["message_id"] for p in world.published]
    assert len(ids) == len(set(ids))


def test_ts_is_the_real_time_of_the_reading(keys):
    world = World(fakes.FakeTime())
    run_live(keys, world, ["--duration", "2m"])
    for p in world.published:
        ts = ts_to_epoch_ms(json.loads(p["payload"])["ts"])
        # Delivered on the poll after it was due (50 ms steps).
        assert 0 <= p["at"] - (ts - START_EPOCH_MS) <= 100


def test_startup_messages_go_out_on_first_connect(keys):
    world = World(fakes.FakeTime())
    run_live(keys, world, ["--duration", "1m"])
    connacks = {d: t for t, d, ok in world.connacks if ok}
    for device_id in ALL:
        mine = world.publishes(device_id)
        kinds = [p["topic"].split("/")[3] for p in mine]
        if device_id.startswith("bedroom-"):
            assert kinds[:3] == ["status", "bed", "motion"]
            assert {p["at"] for p in mine[:3]} == {connacks[device_id]}
        else:
            assert kinds[0] == "status"
            assert mine[0]["at"] == connacks[device_id]


def test_startup_messages_are_not_repeated_after_a_reconnect(keys):
    world = World(
        fakes.FakeTime(),
        drop=lambda d, t: d == "bedroom-0001" and 30_000 <= t < 30_100,
    )
    run_live(
        keys,
        world,
        ["--duration", "3m", "--devices", "wearable-0001,bedroom-0001"],
        start_at="12:00",
    )
    assert len(world.connect_times("bedroom-0001")) == 2
    kinds = Counter(p["topic"].split("/")[3] for p in world.publishes("bedroom-0001"))
    assert kinds["bed"] == 1 and kinds["motion"] == 1


def test_messages_due_while_disconnected_are_dropped_and_counted_not_queued(keys):
    state = {"dropped_at": None}

    def drop(device_id, t):
        if device_id == "wearable-0002" and t >= 65_000 and state["dropped_at"] is None:
            state["dropped_at"] = t
            return True
        return False

    def policy(device_id, attempt, t):
        if device_id == "wearable-0002" and attempt > 1 and t < 200_000:
            return REFUSE
        return OK

    world = World(fakes.FakeTime(), policy=policy, drop=drop)
    argv = ["--duration", "6m", "--devices", "wearable-0001,wearable-0002"]
    result = run_live(keys, world, argv, start_at="12:00")
    assert result.code == 0
    down = state["dropped_at"]
    up = max(t for t, d, ok in world.connacks if d == "wearable-0002" and ok)
    assert up > 200_000

    # What the device would have sent: the same run as a dry-run.
    dry = run_dry(
        ["--dry-run", "--instant", "--random-seed", "3", "--start-at", "12:00", *argv]
    )
    expected = [
        line
        for line in dry.lines
        if line["topic"].startswith("silvercare/v1/wearable-0002/")
        and down <= dry.elapsed_ms(line) < up
    ]
    assert len(expected) >= 4

    sent = [json.loads(p["payload"]) for p in world.publishes("wearable-0002")]
    sent_elapsed = [ts_to_epoch_ms(s["ts"]) - START_EPOCH_MS for s in sent]
    assert not [e for e in sent_elapsed if down <= e < up]
    # Nothing is flushed on reconnect: no burst right after it.
    assert (
        sum(1 for p in world.publishes("wearable-0002") if up <= p["at"] < up + 2000)
        <= 1
    )
    total = result.total()
    assert f"| dropped {len(expected)} |" in total
    dropped_per_minute = sum(int(m[7]) for m in result.summaries())
    assert dropped_per_minute == len(expected)


def test_values_match_the_dry_run_for_the_same_seed(keys):
    world = World(fakes.FakeTime())
    argv = ["--duration", "5m", "--devices", "wearable-0004,bedroom-0004"]
    run_live(keys, world, argv, start_at="07:00")
    dry = run_dry(
        ["--dry-run", "--instant", "--random-seed", "3", "--start-at", "07:00", *argv]
    )

    def strip(payload):
        return {k: v for k, v in payload.items() if k != "message_id"}

    live_vitals = [
        strip(json.loads(p["payload"]))
        for p in world.published
        if p["topic"].endswith("/vitals")
    ]
    dry_vitals = [
        strip(line["payload"])
        for line in dry.lines
        if line["topic"].endswith("/vitals")
    ]
    assert live_vitals == dry_vitals


def test_failed_publishes_are_counted(keys):
    world = World(
        fakes.FakeTime(), publish_rc=lambda d: 4 if d == "wearable-0001" else 0
    )
    result = run_live(
        keys, world, ["--duration", "3m", "--devices", "wearable-0001,wearable-0002"]
    )
    failed = len(world.publishes("wearable-0001"))
    assert failed >= 3
    assert f"| failed {failed}" in result.total()


# ---- output (criterion 18) ----


def test_one_summary_line_per_minute_like_the_spec_example(keys, tmp_path):
    world = World(fakes.FakeTime())
    result = run_live(keys, world, ["--duration", "3m"])
    lines = result.out_lines
    assert lines[0] == "simulator live: 40 devices, random seed 3, routine 21:58"
    summaries = [line for line in lines if " SGT " in line]
    assert [s[:25] for s in summaries] == [
        "12:01 SGT routine 21:59 |",
        "12:02 SGT routine 22:00 |",
        "12:03 SGT routine 22:01 |",
    ]
    for line in summaries:
        match = SUMMARY_RE.fullmatch(line)
        assert match and match[1] == "40" and match[2] == "40"
        assert match[7] == "0" and match[8] == "0"
    sent = sum(sum(int(m[i]) for i in range(3, 7)) for m in result.summaries())
    assert sent == len(world.published)
    assert result.stderr == ""
    assert_clean_output(result, tmp_path)


def test_summary_shows_fewer_connected_while_a_device_is_down(keys):
    world = World(
        fakes.FakeTime(),
        policy=lambda d, n, t: REFUSE if d == "wearable-0002" and n > 1 else OK,
        drop=lambda d, t: d == "wearable-0002" and t >= 30_000,
    )
    result = run_live(
        keys, world, ["--duration", "2m", "--devices", "wearable-0001,wearable-0002"]
    )
    assert [m[1] for m in result.summaries()] == ["1", "1"]
    assert "connected 2/2" in result.total()  # ever connected


# ---- shutting down (criterion 19) ----


def assert_clean_shutdown(result, at_s):
    assert result.code == 0
    connected = {d for _, d, ok in result.world.connacks if ok}
    assert connected
    assert {d for _, d in result.world.disconnects} >= connected
    last_disconnect = max(t for t, _ in result.world.disconnects)
    assert all(p["at"] <= last_disconnect for p in result.world.published)
    assert last_disconnect <= at_s * 1000 + 200
    assert result.total().startswith("total ")


def test_duration_end_disconnects_every_device_and_exits_0(keys):
    world = World(fakes.FakeTime())
    result = run_live(keys, world, ["--duration", "90s"])
    assert_clean_shutdown(result, 90)
    assert result.total().startswith("total 0:01:30 | connected 40/40 | sent ")
    assert {d for _, d in world.disconnects} == set(ALL)


def test_ctrl_c_disconnects_every_device_and_exits_0(keys):
    time = fakes.FakeTime()
    time.hooks.append((30.0, lambda: signal.raise_signal(signal.SIGINT)))
    world = World(time)
    before = signal.getsignal(signal.SIGINT)
    result = run_live(keys, world, [])
    assert_clean_shutdown(result, 30)
    assert result.total().startswith("total 0:00:30 | connected 40/40")
    assert signal.getsignal(signal.SIGINT) is before


def test_sigterm_also_shuts_down_cleanly(keys):
    time = fakes.FakeTime()
    time.hooks.append((12.0, lambda: signal.raise_signal(signal.SIGTERM)))
    world = World(time)
    before = signal.getsignal(signal.SIGTERM)
    result = run_live(keys, world, [])
    assert_clean_shutdown(result, 12)
    assert signal.getsignal(signal.SIGTERM) is before


def test_stop_event_disconnects_every_device_and_exits_0(keys):
    time = fakes.FakeTime()
    stop = threading.Event()
    time.hooks.append((20.0, stop.set))
    result = run_live(keys, World(time), [], stop=stop)
    assert_clean_shutdown(result, 20)


def test_exit_1_when_no_device_can_connect(keys, tmp_path):
    world = World(fakes.FakeTime(), policy=lambda d, n, t: REFUSE)
    result = run_live(keys, world, [])
    assert result.code == 1
    assert "error: no device could connect" in result.stderr
    assert world.time.now - world.time.start < 15
    assert len(result.problems()) >= 40
    assert world.published == []
    assert_clean_output(result, tmp_path)


def test_exit_1_when_every_connect_raises(keys, tmp_path):
    world = World(fakes.FakeTime(), policy=lambda d, n, t: RAISE)
    result = run_live(keys, world, [])
    assert result.code == 1
    assert_clean_output(result, tmp_path)


def test_exit_1_when_duration_ends_before_any_device_connects(keys):
    world = World(fakes.FakeTime(), policy=lambda d, n, t: SILENT)
    result = run_live(keys, world, ["--duration", "2s"])
    assert result.code == 1


def test_one_device_connecting_is_enough_for_exit_0(keys):
    world = World(
        fakes.FakeTime(),
        policy=lambda d, n, t: OK if d == "bedroom-0020" else REFUSE,
    )
    result = run_live(keys, world, ["--duration", "1m"])
    assert result.code == 0
    assert "connected 1/40" in result.total()


# ---- key files (criterion 16) ----


def test_missing_cert_or_key_exits_2_listing_device_ids_only(keys, tmp_path):
    folder, _ = keys
    (folder / "wearable-0005.crt").unlink()
    (folder / "bedroom-0017.key").unlink()
    world = World(fakes.FakeTime())
    result = run_live(keys, world, [])
    assert result.code == 2
    assert world.created == []
    assert re.findall(r"(?:wearable|bedroom)-\d{4}", result.stderr) == [
        "wearable-0005",
        "bedroom-0017",
    ]
    assert str(tmp_path) not in result.stderr
    assert ".key" not in result.stderr and ".crt" not in result.stderr
    assert result.stdout == ""


def test_missing_ca_file_exits_2_without_its_path(keys, tmp_path):
    _, ca = keys
    ca.unlink()
    world = World(fakes.FakeTime())
    result = run_live(keys, world, [])
    assert result.code == 2
    assert world.created == []
    assert str(tmp_path) not in result.stderr
    assert "AmazonRootCA1" not in result.stderr


def test_only_selected_devices_need_key_files(keys):
    folder, _ = keys
    (folder / "wearable-0005.key").unlink()
    world = World(fakes.FakeTime())
    result = run_live(keys, world, ["--duration", "5s", "--devices", "wearable-0001"])
    assert result.code == 0


def test_key_folder_that_does_not_exist_lists_every_device(tmp_path):
    world = World(fakes.FakeTime())
    ca = tmp_path / "ca.pem"
    ca.write_text("placeholder", encoding="utf-8")
    result = run_live((tmp_path / "missing", ca), world, [])
    assert result.code == 2
    assert set(re.findall(r"(?:wearable|bedroom)-\d{4}", result.stderr)) == set(ALL)


def test_tls_setup_failure_exits_2_with_the_error_type_only(keys, tmp_path):
    folder, ca = keys

    def factory(device_id):
        raise OSError(f"{folder}/{device_id}.key Synthetic secret text")

    out, err = io.StringIO(), io.StringIO()
    code = main.main(
        ["--endpoint", ENDPOINT, "--keys-dir", str(folder), "--ca-file", str(ca)],
        stdout=out,
        stderr=err,
        client_factory=factory,
        epoch_ms=lambda: START_EPOCH_MS,
    )
    assert code == 2
    assert "TLS setup failed (OSError)" in err.getvalue()
    assert str(tmp_path) not in out.getvalue() + err.getvalue()
    assert "Synthetic secret text" not in out.getvalue() + err.getvalue()


# ---- the real paho factory, never connected ----


def test_paho_factory_sets_client_id_and_verifies_the_ca(tmp_path, monkeypatch):
    import socket
    import ssl
    from datetime import UTC, datetime, timedelta

    pytest.importorskip("paho.mqtt.client")
    crypto = pytest.importorskip("cryptography")
    del crypto
    import live
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    def no_socket(*args, **kwargs):
        raise AssertionError("opened a socket")

    monkeypatch.setattr(socket, "create_connection", no_socket)

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-only")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    folder = tmp_path / "keys"
    folder.mkdir()
    pem = cert.public_bytes(serialization.Encoding.PEM)
    (folder / "wearable-0001.crt").write_bytes(pem)
    (folder / "wearable-0001.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    ca = tmp_path / "ca.pem"
    ca.write_bytes(pem)

    client = live.paho_client_factory(folder, ca)("wearable-0001")
    assert client._client_id == b"wearable-0001"
    context = client._ssl_context
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert client._clean_session is True
