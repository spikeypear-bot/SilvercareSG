"""Flags, the dry-run's isolation from network and keys, and simulator imports
(criteria 1, 3, 20)."""

from __future__ import annotations

import ast
import builtins
import io
import socket
import ssl
import sys
import threading
from pathlib import Path

import clock as clock_mod
import main
import pytest
from conftest import MINUTE_MS, SRC_DIR, START_EPOCH_MS, message_of, run_dry

ENDPOINT = "example-ats.iot.ap-southeast-1.amazonaws.com"


@pytest.fixture
def no_files_or_connections(monkeypatch):
    """Fail the test if main reads the seed/devices files, checks key files, or
    builds an MQTT client."""

    def fail(*args, **kwargs):
        raise AssertionError("must not get this far")

    monkeypatch.setattr(main, "load_json", fail)
    monkeypatch.setattr(main, "missing_key_files", fail)
    monkeypatch.setattr(main, "paho_client_factory", fail)
    return fail


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--instant", "--duration", "1h"],
        ["--endpoint", ENDPOINT, "--instant", "--duration", "1h"],
        ["--dry-run", "--instant"],
        ["--dry-run", "--endpoint", ENDPOINT],
        ["--dry-run", "--keys-dir", "somewhere"],
        ["--dry-run", "--ca-file", "somewhere.pem"],
        ["--dry-run", "--duration", "0m"],
        ["--dry-run", "--duration", "30"],
        ["--dry-run", "--duration", "1d"],
        ["--dry-run", "--duration", "-5m"],
        ["--dry-run", "--duration", "1.5h"],
        ["--dry-run", "--duration", "30 m"],
        ["--dry-run", "--start-at", "24:00"],
        ["--dry-run", "--start-at", "7:00"],
        ["--dry-run", "--start-at", "12:60"],
        ["--dry-run", "--start-at", "noon"],
        ["--dry-run", "--devices", "wearable-1"],
        ["--dry-run", "--devices", ""],
        ["--dry-run", "--devices", "wearable-0001,wearable-0001"],
        ["--dry-run", "--devices", "res-0001"],
        ["--dry-run", "--devices", "wearable-0001,"],
        ["--dry-run", "--random-seed", "abc"],
        ["--dry-run", "--bogus"],
        ["--endpoint", "not a host"],
        ["--endpoint", ""],
        ["--endpoint", "https://example.iot.amazonaws.com"],
        ["--endpoint", "localhost"],
    ],
)
def test_bad_or_conflicting_flags_exit_2_before_reading_or_connecting(
    argv, no_files_or_connections, capsys
):
    with pytest.raises(SystemExit) as exc:
        main.main(argv, stdout=io.StringIO(), stderr=io.StringIO())
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "argv",
    [
        ["--dry-run", "--instant", "--duration", "1m", "--devices", "wearable-0099"],
        ["--endpoint", ENDPOINT, "--keys-dir", "KEYS", "--devices", "bedroom-0021"],
    ],
)
def test_device_not_in_the_devices_file_exits_2_before_connecting(
    argv, monkeypatch, tmp_path
):
    argv = [str(tmp_path / "keys") if a == "KEYS" else a for a in argv]
    monkeypatch.setattr(main, "paho_client_factory", lambda *a: pytest.fail("built"))
    monkeypatch.setattr(main, "missing_key_files", lambda *a: pytest.fail("checked"))
    out, err = io.StringIO(), io.StringIO()
    assert main.main(argv, stdout=out, stderr=err) == 2
    assert out.getvalue() == ""
    assert "wearable-0099" in err.getvalue() or "bedroom-0021" in err.getvalue()


def test_missing_seed_file_exits_2_without_its_path(tmp_path):
    missing = tmp_path / "nowhere" / "seed.json"
    result = run_dry(["--dry-run", "--seed-file", str(missing)])
    assert result.code == 2
    assert str(tmp_path) not in result.stderr
    assert result.stdout == ""


def test_unreadable_devices_file_exits_2(tmp_path):
    bad = tmp_path / "devices.json"
    bad.write_text("{not json", encoding="utf-8")
    result = run_dry(["--dry-run", "--devices-file", str(bad)])
    assert result.code == 2
    assert "{not json" not in result.stderr


def test_parse_duration_units():
    assert main.parse_duration("90s") == 90_000
    assert main.parse_duration("30m") == 30 * MINUTE_MS
    assert main.parse_duration("24h") == 24 * 60 * MINUTE_MS


# ---- dry-run opens nothing ----


@pytest.fixture
def guard_network_and_keys(monkeypatch):
    """Any socket, TLS key load, or open of a key/cert/CA or secrets/ file fails."""

    def no_socket(*args, **kwargs):
        raise AssertionError("dry-run opened a socket")

    for name in ("socket", "create_connection", "getaddrinfo", "socketpair"):
        monkeypatch.setattr(socket, name, no_socket)
    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", no_socket)
    monkeypatch.setattr(ssl.SSLContext, "load_verify_locations", no_socket)

    real_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        text = str(file)
        if text.endswith((".key", ".crt", ".pem")) or "secrets" in Path(text).parts:
            raise AssertionError("dry-run opened a key file")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(
        main, "paho_client_factory", lambda *a: pytest.fail("built a client")
    )
    sys.modules.pop("paho.mqtt.client", None)


def test_guard_catches_key_file_reads(guard_network_and_keys, tmp_path):
    key = tmp_path / "wearable-0001.key"
    with pytest.raises(AssertionError):
        key.read_text()
    with pytest.raises(AssertionError):
        socket.create_connection(("127.0.0.1", 1))


def test_dry_run_instant_opens_no_socket_and_reads_no_keys(guard_network_and_keys):
    result = run_dry(
        ["--dry-run", "--instant", "--duration", "2h", "--random-seed", "1"]
    )
    assert result.code == 0
    assert len(result.lines) > 1000
    assert "paho.mqtt.client" not in sys.modules


def test_dry_run_real_time_opens_no_socket_and_reads_no_keys(guard_network_and_keys):
    fake = FakeTime()
    result = run_dry(
        ["--dry-run", "--duration", "2m", "--random-seed", "1"],
        clock=clock_mod.RealClock(fake.monotonic, fake.sleep),
    )
    assert result.code == 0
    assert result.lines


# ---- real-time dry-run with a fake clock ----


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []
        self.hooks: list[tuple[float, object]] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds > 0
        self.sleeps.append(seconds)
        self.now += seconds
        for at, hook in list(self.hooks):
            if self.now - 1000.0 >= at:
                self.hooks.remove((at, hook))
                hook()


def test_real_time_dry_run_waits_for_each_message_and_stops_at_duration():
    fake = FakeTime()
    result = run_dry(
        ["--dry-run", "--duration", "3m", "--random-seed", "5", "--start-at", "10:00"],
        clock=clock_mod.RealClock(fake.monotonic, fake.sleep),
    )
    assert result.code == 0
    assert 179.9 <= fake.now - 1000.0 <= 180.5
    assert max(fake.sleeps) <= 0.25 + 1e-9
    elapsed = [result.elapsed_ms(line) for line in result.lines]
    assert elapsed == sorted(elapsed) and max(elapsed) < 3 * MINUTE_MS
    summaries = result.stderr.splitlines()[1:-1]
    assert len(summaries) == 3
    assert result.stderr.splitlines()[-1].startswith("total 0:03:00 | connected 40/40")


def test_real_time_dry_run_matches_instant_output():
    argv = ["--duration", "5m", "--random-seed", "5", "--start-at", "10:00"]
    fake = FakeTime()
    ids = iter(range(10**6))
    ids2 = iter(range(10**6))
    real = run_dry(
        ["--dry-run", *argv],
        clock=clock_mod.RealClock(fake.monotonic, fake.sleep),
        new_id=lambda: f"00000000-0000-4000-8000-{next(ids):012d}",
    )
    instant = run_dry(
        ["--dry-run", "--instant", *argv],
        new_id=lambda: f"00000000-0000-4000-8000-{next(ids2):012d}",
    )
    assert real.stdout == instant.stdout


def test_stop_event_ends_a_dry_run_cleanly_with_exit_0():
    fake = FakeTime()
    stop = threading.Event()
    fake.hooks.append((90.0, stop.set))
    result = run_dry(
        ["--dry-run", "--random-seed", "5"],
        clock=clock_mod.RealClock(fake.monotonic, fake.sleep),
        stop=stop,
    )
    assert result.code == 0
    assert 90.0 <= fake.now - 1000.0 <= 90.5
    assert result.stderr.splitlines()[-1].startswith("total 0:01:30 ")
    assert max(result.elapsed_ms(line) for line in result.lines) <= 90_500


def test_ctrl_c_ends_a_dry_run_cleanly_with_exit_0():
    import signal

    fake = FakeTime()
    fake.hooks.append((30.0, lambda: signal.raise_signal(signal.SIGINT)))
    before = signal.getsignal(signal.SIGINT)
    result = run_dry(
        ["--dry-run", "--random-seed", "5"],
        clock=clock_mod.RealClock(fake.monotonic, fake.sleep),
    )
    assert result.code == 0
    assert fake.now - 1000.0 <= 30.5
    assert result.stderr.splitlines()[-1].startswith("total 0:00:30 ")
    assert signal.getsignal(signal.SIGINT) is before


def test_dry_run_line_format_is_topic_then_payload():
    result = run_dry(["--dry-run", "--instant", "--duration", "1m"])
    first = result.stdout.splitlines()[0]
    assert first.startswith(
        '{"topic": "silvercare/v1/wearable-0001/status", "payload": {'
    )
    assert message_of(result.lines[0]) == "status"


def test_instant_ts_starts_at_the_real_current_time_by_default(monkeypatch):
    import time

    monkeypatch.setattr(time, "time_ns", lambda: START_EPOCH_MS * 1_000_000)
    import io as _io

    out, err = _io.StringIO(), _io.StringIO()
    assert (
        main.main(
            ["--dry-run", "--instant", "--duration", "1m"], stdout=out, stderr=err
        )
        == 0
    )
    assert '"ts": "2026-11-03T04:00:00.250Z"' in out.getvalue().splitlines()[0]


# ---- imports (criterion 1) ----


def test_simulator_imports_only_the_standard_library_and_paho():
    local = {p.stem for p in SRC_DIR.glob("*.py")}
    allowed = set(sys.stdlib_module_names) | local | {"paho"}
    for path in SRC_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] in allowed, (path.name, name)


def test_paho_is_only_imported_inside_the_client_factory():
    for path in SRC_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", None) or node.names[0].name
                assert not module.startswith("paho"), path.name


def test_no_source_file_mentions_secrets():
    for path in SRC_DIR.glob("*.py"):
        assert "secrets" not in path.read_text(encoding="utf-8").lower(), path.name


# ---- live-only flags: --keys-dir required, endpoint in ap-southeast-1 ----


def test_live_run_without_keys_dir_exits_2_before_anything(
    no_files_or_connections, capsys
):
    with pytest.raises(SystemExit) as exc:
        main.main(
            ["--endpoint", ENDPOINT],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            client_factory=no_files_or_connections,
        )
    assert exc.value.code == 2
    assert "--keys-dir" in capsys.readouterr().err


@pytest.mark.parametrize(
    "endpoint",
    [
        "x.iot.us-east-1.amazonaws.com",
        "x.iot.ap-southeast-1.amazonaws.com.evil.com",
        "example.com",
        "iot.ap-southeast-1.amazonaws.com.example",
        "x..iot.ap-southeast-1.amazonaws.com",
        "x_y.iot.ap-southeast-1.amazonaws.com",
        ".iot.ap-southeast-1.amazonaws.com",
        "x.iot.ap-southeast-1.amazonaws.com ",
    ],
)
def test_endpoint_outside_ap_southeast_1_exits_2_without_echoing_it(
    endpoint, no_files_or_connections, capsys, tmp_path
):
    with pytest.raises(SystemExit) as exc:
        main.main(
            ["--endpoint", endpoint, "--keys-dir", str(tmp_path)],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            client_factory=no_files_or_connections,
        )
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "--endpoint" in captured.err
    # The message may name the required suffix, but never the value given.
    text = (captured.err + captured.out).replace(
        "ending .iot.ap-southeast-1.amazonaws.com", "ending <suffix>"
    )
    assert endpoint.strip() not in text


def test_upper_case_endpoint_in_region_is_accepted(tmp_path):
    from fakes import FakeTime, World

    folder = tmp_path / "keys"
    folder.mkdir()
    for name in ("wearable-0001.crt", "wearable-0001.key"):
        (folder / name).write_text("placeholder", encoding="utf-8")
    ca = tmp_path / "ca.pem"
    ca.write_text("placeholder", encoding="utf-8")
    world = World(FakeTime())
    endpoint = "EXAMPLE-ATS.IOT.AP-SOUTHEAST-1.AMAZONAWS.COM"
    code = main.main(
        [
            "--endpoint",
            endpoint,
            "--keys-dir",
            str(folder),
            "--ca-file",
            str(ca),
            "--devices",
            "wearable-0001",
            "--duration",
            "5s",
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        clock=clock_mod.RealClock(world.time.monotonic, world.time.sleep),
        epoch_ms=lambda: START_EPOCH_MS,
        client_factory=world.factory,
    )
    assert code == 0
    assert world.connects[0][2] == endpoint
