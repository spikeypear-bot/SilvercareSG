"""Simulator backfill (docs/features/simulator-backfill.md, criterion 14).

Every run uses a fixed fake "today", short ranges, and writes only under tmp_path.
No test opens a network connection or reads device keys: the shared runs block
sockets and any open of a key file or the repo's private key folder.
"""

from __future__ import annotations

import builtins
import gzip
import importlib.util
import io
import json
import re
import socket
import ssl
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import backfill
import contract
import main
import pytest
import routine_ref
from conftest import DEVICES_FILE, REPO_ROOT, SEED_FILE, SRC_DIR, ts_to_epoch_ms
from engine import Engine
from report import SGT
from residents import build_residents

TODAY = date(2026, 11, 8)
FROM = "2026-03-10"
TO = "2026-03-13"
FALLS = 6  # round(6 / 4) = 2 cancels
SEED = 1
OTHER_SEED = 2
SMALL_DEVICES = [
    f"{kind}-000{i}" for kind in ("wearable", "bedroom") for i in (1, 2, 3, 4)
]

SECOND_MS = 1000
MINUTE_MS = 60 * SECOND_MS
DAY_MS = 24 * 60 * MINUTE_MS
FALL_MESSAGE_DELAY_MS = 20 * SECOND_MS
FALL_MAX_DOWN_MS = 10 * MINUTE_MS
MESSAGES = ("vitals", "status", "motion", "bed", "fall", "alert_cancel")

UUID4 = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
KEY_RE = re.compile(
    r"^telemetry/message=(?P<message>vitals|status|motion|bed|fall|alert_cancel)"
    r"/date=(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})"
    rf"/(?P<compact>[0-9]{{8}})T235959Z-{UUID4}\.jsonl\.gz$"
)
HEADER_RE = re.compile(
    r"^backfill: (?P<first>\d{4}-\d{2}-\d{2}) to (?P<last>\d{4}-\d{2}-\d{2})"
    r" \(lead-in from (?P<lead>\d{4}-\d{2}-\d{2})\), (?P<devices>\d+) devices,"
    r" random seed (?P<seed>-?\d+)$"
)
FALL_LINE_RE = re.compile(
    r"^fall (?P<wearable>wearable-\d{4}) (?P<when>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) SGT"
    r"(?P<cancelled> \(cancelled\))?$"
)
LINES_RE = re.compile(
    r"^lines: vitals (\d+), status (\d+), motion (\d+), bed (\d+), fall (\d+),"
    r" alert_cancel (\d+) \(total (\d+)\)$"
)
FILES_RE = re.compile(
    r"^files: (?P<files>\d+), UTC dates (?P<first>\d{4}-\d{2}-\d{2})"
    r" to (?P<last>\d{4}-\d{2}-\d{2})$"
)


def _load_ingest_validation():
    """ingest's validation.py under its own module name (it imports only the
    standard library), so it can't clash with a simulator module."""
    path = REPO_ROOT / "lambdas" / "ingest" / "src" / "validation.py"
    spec = importlib.util.spec_from_file_location("ingest_validation", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ingest_validation"] = module
    spec.loader.exec_module(module)
    return module


ingest_validation = _load_ingest_validation()
ALL_MESSAGES = frozenset(
    m for allowed in ingest_validation.ALLOWED_MESSAGES.values() for m in allowed
)


def sgt_midnight_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=SGT).timestamp()) * 1000


def sgt(epoch_ms: int) -> datetime:
    return datetime.fromtimestamp(epoch_ms / 1000, UTC).astimezone(SGT)


# ---- running the tool ----


@dataclass
class Run:
    code: int
    stdout: str
    stderr: str
    out: Path


def run(argv: list[str], out: Path, today: date = TODAY) -> Run:
    err = io.StringIO()
    real_stdout = sys.stdout
    captured = io.StringIO()
    sys.stdout = captured
    try:
        code = backfill.main([*argv, "--out", str(out)], stderr=err, today=today)
    finally:
        sys.stdout = real_stdout
    return Run(code, captured.getvalue(), err.getvalue(), out)


def small_devices_file(folder: Path) -> Path:
    """The devices file with only residents 1-4 (8 devices)."""
    doc = json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
    doc["devices"] = [d for d in doc["devices"] if d["id"] in SMALL_DEVICES]
    assert len(doc["devices"]) == len(SMALL_DEVICES)
    path = folder / "devices-small.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def guard(mp: pytest.MonkeyPatch) -> None:
    """Fail on any socket, TLS key load, or open of a key/cert file or of the
    repo's private key folder."""

    def no_network(*args, **kwargs):
        raise AssertionError("backfill opened a socket or loaded a key")

    for name in ("socket", "create_connection", "getaddrinfo", "socketpair"):
        mp.setattr(socket, name, no_network)
    mp.setattr(ssl.SSLContext, "load_cert_chain", no_network)
    mp.setattr(ssl.SSLContext, "load_verify_locations", no_network)
    real_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        text = str(file)
        if text.endswith((".key", ".crt", ".pem")) or "secrets" in Path(text).parts:
            raise AssertionError("backfill opened a key file")
        return real_open(file, *args, **kwargs)

    mp.setattr(builtins, "open", guarded_open)
    mp.setattr(io, "open", guarded_open)


def base_argv(devices_file: Path, seed: int = SEED, falls: int = FALLS) -> list[str]:
    return [
        "--from",
        FROM,
        "--to",
        TO,
        "--falls",
        str(falls),
        "--random-seed",
        str(seed),
        "--devices-file",
        str(devices_file),
    ]


@dataclass
class Output:
    run: Run
    files: dict[str, bytes]  # key relative to out -> raw gzip bytes
    lines: dict[str, list[dict]]  # key -> parsed lines, in file order

    @property
    def all_lines(self) -> list[dict]:
        return [line for lines in self.lines.values() for line in lines]

    def by_message(self, message: str) -> list[dict]:
        return sorted(
            (line for line in self.all_lines if line["message"] == message),
            key=lambda line: line["received_at"],
        )


def read_output(result: Run) -> Output:
    files: dict[str, bytes] = {}
    lines: dict[str, list[dict]] = {}
    for path in sorted(p for p in result.out.rglob("*") if p.is_file()):
        key = path.relative_to(result.out).as_posix()
        raw = path.read_bytes()
        files[key] = raw
        text = gzip.decompress(raw).decode("utf-8")
        assert text.endswith("\n"), key
        lines[key] = [json.loads(line) for line in text.splitlines()]
    return Output(result, files, lines)


@pytest.fixture(scope="module")
def inputs(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("inputs")
    small_devices_file(folder)
    return folder


def _guarded_run(tmp_path_factory, inputs: Path, name: str, seed: int) -> Output:
    parent = tmp_path_factory.mktemp(name)
    with pytest.MonkeyPatch.context() as mp:
        guard(mp)
        result = run(base_argv(inputs / "devices-small.json", seed), parent / "out")
    assert result.code == 0, result.stderr
    return read_output(result)


@pytest.fixture(scope="module")
def first(tmp_path_factory, inputs) -> Output:
    return _guarded_run(tmp_path_factory, inputs, "first", SEED)


@pytest.fixture(scope="module")
def again(tmp_path_factory, inputs) -> Output:
    return _guarded_run(tmp_path_factory, inputs, "again", SEED)


@pytest.fixture(scope="module")
def other(tmp_path_factory, inputs) -> Output:
    return _guarded_run(tmp_path_factory, inputs, "other", OTHER_SEED)


@pytest.fixture(scope="module")
def offsets() -> dict[str, int]:
    """wearable -> routine offset, from an engine built as criterion 5 says."""
    seed_doc = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    devices_doc = json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
    residents, order = build_residents(seed_doc, devices_doc)
    engine = Engine(
        residents,
        order,
        SMALL_DEVICES,
        seed=SEED,
        start_epoch_ms=sgt_midnight_ms(date(2026, 3, 8)),
        start_routine_ms=0,
        schedule_startup=True,
    )
    return {d: engine.offset_ms(d) for d in SMALL_DEVICES if d.startswith("wearable")}


@dataclass
class StderrFall:
    wearable: str
    when: str  # yyyy-mm-dd HH:MM SGT
    cancelled: bool


def stderr_falls(result: Run) -> list[StderrFall]:
    out = []
    for line in result.stderr.splitlines():
        match = FALL_LINE_RE.fullmatch(line)
        if match:
            out.append(
                StderrFall(match["wearable"], match["when"], bool(match["cancelled"]))
            )
    return out


# ======== window and lead-in (criterion 4) ========


WINDOW_START_MS = sgt_midnight_ms(date(2026, 3, 8))  # FROM - 2 days, 00:00 SGT
WINDOW_STOP_MS = sgt_midnight_ms(date(2026, 3, 14))  # TO + 1 day, 00:00 SGT


def test_window_constants_are_singapore_midnights():
    assert datetime.fromtimestamp(WINDOW_START_MS / 1000, UTC) == datetime(
        2026, 3, 7, 16, tzinfo=UTC
    )
    assert datetime.fromtimestamp(WINDOW_STOP_MS / 1000, UTC) == datetime(
        2026, 3, 13, 16, tzinfo=UTC
    )


def test_first_messages_are_at_the_lead_in_start(first):
    ts = [ts_to_epoch_ms(line["ts"]) for line in first.all_lines]
    assert min(ts) >= WINDOW_START_MS
    # Start-up messages at 00:00 SGT of FROM - 2 days (100 ms apart).
    assert min(ts) < WINDOW_START_MS + SECOND_MS
    starts = [t for t in ts if t < WINDOW_START_MS + SECOND_MS]
    assert len(starts) >= len(SMALL_DEVICES)


def test_lead_in_days_have_messages_for_every_device(first):
    lead_in_end = sgt_midnight_ms(date(2026, 3, 10))
    devices = {
        line["device_id"]
        for line in first.all_lines
        if ts_to_epoch_ms(line["ts"]) < lead_in_end
    }
    assert devices == set(SMALL_DEVICES)


def test_no_line_is_received_at_or_after_the_stop(first):
    received = [line["received_at"] for line in first.all_lines]
    assert max(received) < WINDOW_STOP_MS


def test_messages_run_until_just_before_the_stop(first):
    vitals = [line["received_at"] for line in first.by_message("vitals")]
    # Each wearable sends vitals every minute: the last minute before the stop is full.
    last_minute = [r for r in vitals if r >= WINDOW_STOP_MS - MINUTE_MS]
    assert {
        line["device_id"]
        for line in first.by_message("vitals")
        if line["received_at"] >= WINDOW_STOP_MS - 2 * MINUTE_MS
    } == {d for d in SMALL_DEVICES if d.startswith("wearable")}
    assert last_minute


def test_header_names_range_lead_in_devices_and_seed(first):
    header = first.run.stderr.splitlines()[0]
    match = HEADER_RE.fullmatch(header)
    assert match, header
    assert match["first"] == FROM
    assert match["last"] == TO
    assert match["lead"] == "2026-03-08"
    assert match["devices"] == str(len(SMALL_DEVICES))
    assert match["seed"] == str(SEED)


# ======== lines (criterion 7) ========


def test_every_line_passes_ingest_validation(first):
    rejected = Counter()
    for line in first.all_lines:
        result = ingest_validation.validate_event(line, ALL_MESSAGES)
        if not isinstance(result, ingest_validation.ValidMessage):
            rejected[(line["message"], result.reason)] += 1
    assert not rejected


def test_every_payload_follows_the_metrics_contract(first):
    rule_fields = {"device_id", "message", "received_at"}
    bad = Counter()
    for line in first.all_lines:
        payload = {k: v for k, v in line.items() if k not in rule_fields}
        topic = f"silvercare/v1/{line['device_id']}/{line['message']}"
        for problem in contract.problems(topic, payload):
            bad[(line["message"], problem)] += 1
    assert not bad


def test_every_message_type_is_present(first):
    assert {line["message"] for line in first.all_lines} == set(MESSAGES)


def test_received_at_is_integer_ms_100_to_900_after_ts_except_fall(first):
    delays = []
    for line in first.all_lines:
        if line["message"] == "fall":
            continue
        assert type(line["received_at"]) is int
        delays.append(line["received_at"] - ts_to_epoch_ms(line["ts"]))
    assert min(delays) >= 100
    assert max(delays) <= 900
    # Drawn, not fixed.
    assert len(set(delays)) > 100


def test_fall_received_at_is_20_1_to_20_9_s_after_its_ts(first):
    falls = first.by_message("fall")
    assert len(falls) == FALLS
    for line in falls:
        assert type(line["received_at"]) is int
        delay = line["received_at"] - ts_to_epoch_ms(line["ts"])
        assert FALL_MESSAGE_DELAY_MS + 100 <= delay <= FALL_MESSAGE_DELAY_MS + 900


def test_fall_lines_are_in_received_at_order_in_their_files(first):
    for key, lines in first.lines.items():
        if "message=fall/" in key:
            received = [line["received_at"] for line in lines]
            assert received == sorted(received), key


def test_lines_are_compact_json_with_rule_fields(first):
    for key, raw in first.files.items():
        text = gzip.decompress(raw).decode("utf-8")
        for line in text.splitlines()[:5]:
            assert ", " not in line and '": ' not in line, key
            parsed = json.loads(line)
            assert {"device_id", "message", "received_at"} <= set(parsed)


def test_message_ids_are_unique(first):
    ids = [line["message_id"] for line in first.all_lines]
    assert len(ids) == len(set(ids))


# ======== files (criterion 8) ========


def test_every_file_key_has_the_exact_format(first):
    for key in first.files:
        match = KEY_RE.fullmatch(key)
        assert match, key
        assert match["compact"] == match["date"].replace("-", "")


def test_nothing_but_telemetry_files_is_written(first):
    entries = {
        p.relative_to(first.run.out).as_posix() for p in first.run.out.rglob("*")
    }
    files = set(first.files)
    folders = entries - files
    assert all(KEY_RE.fullmatch(f) for f in files)
    assert not any(e.endswith(".part") for e in entries)
    for folder in folders:
        assert folder == "telemetry" or folder.startswith("telemetry/message="), folder
    assert sorted(p.name for p in first.run.out.parent.iterdir()) == ["out"]


def test_lines_are_grouped_by_message_and_utc_date_of_received_at(first):
    for key, lines in first.lines.items():
        match = KEY_RE.fullmatch(key)
        assert lines, key
        for line in lines:
            assert line["message"] == match["message"]
            received = datetime.fromtimestamp(line["received_at"] / 1000, UTC)
            assert received.strftime("%Y-%m-%d") == match["date"], key


def test_one_file_per_message_type_and_utc_date(first):
    groups = Counter(
        (KEY_RE.fullmatch(k)["message"], KEY_RE.fullmatch(k)["date"])
        for k in first.files
    )
    assert all(count == 1 for count in groups.values())


def test_utc_partitions_span_the_lead_in_to_the_stop(first):
    vitals_dates = sorted(
        KEY_RE.fullmatch(k)["date"] for k in first.files if "message=vitals/" in k
    )
    # Lead-in 2026-03-08 00:00 SGT is 2026-03-07 16:00 UTC; the stop 2026-03-14 00:00
    # SGT is 2026-03-13 16:00 UTC.
    expected = [str(date(2026, 3, 7) + timedelta(days=i)) for i in range(7)]
    assert vitals_dates == expected


def test_lines_are_in_received_at_order_within_each_file(first):
    for key, lines in first.lines.items():
        received = [line["received_at"] for line in lines]
        assert received == sorted(received), key


def test_gzip_header_has_mtime_0_and_no_file_name(first):
    for key, raw in first.files.items():
        assert raw[:3] == b"\x1f\x8b\x08", key
        assert raw[4:8] == b"\x00\x00\x00\x00", key
        assert raw[3] & 0x08 == 0, key  # FNAME flag


def test_message_type_with_no_messages_has_no_folder(first):
    assert not (first.run.out / "telemetry" / "message=vitals_alert").exists()


# ======== same output for the same input (criterion 6) ========


def test_same_seed_gives_byte_identical_files_and_names(first, again):
    assert list(first.files) == list(again.files)
    for key in first.files:
        assert first.files[key] == again.files[key], key
    assert first.run.stderr == again.run.stderr


def test_another_seed_gives_different_output(first, other):
    assert set(first.files) != set(other.files)
    first_ids = {line["message_id"] for line in first.all_lines}
    other_ids = {line["message_id"] for line in other.all_lines}
    assert not first_ids & other_ids
    assert stderr_falls(first.run) != stderr_falls(other.run)


# ======== falls (criteria 9 to 11) ========


def test_exactly_n_fall_lines(first):
    assert len(first.by_message("fall")) == FALLS


def test_round_n_over_4_alert_cancel_lines(first):
    assert len(first.by_message("alert_cancel")) == round(FALLS / 4)


def test_no_vitals_alert_lines(first):
    assert not first.by_message("vitals_alert")
    assert not any("vitals_alert" in key for key in first.files)


def _fall_starts(output: Output) -> list[tuple[str, int]]:
    """(wearable, fall start epoch ms): a fall's ts is the impact, its start
    (docs/metrics.md; the message is sent 20 s later)."""
    return [
        (line["device_id"], ts_to_epoch_ms(line["ts"]))
        for line in output.by_message("fall")
    ]


def test_falls_are_only_on_wearables(first):
    assert all(d.startswith("wearable-") for d, _ in _fall_starts(first))


def test_falls_start_inside_the_range_never_in_the_lead_in(first):
    range_start = sgt_midnight_ms(date(2026, 3, 10))
    for _device, start in _fall_starts(first):
        assert range_start <= start < WINDOW_STOP_MS


def test_fall_times_in_stderr_match_the_fall_lines(first):
    from_stderr = sorted((f.wearable, f.when) for f in stderr_falls(first.run))
    from_lines = sorted(
        (device, f"{sgt(start):%Y-%m-%d %H:%M}")
        for device, start in _fall_starts(first)
    )
    assert from_stderr == from_lines


def test_falls_are_up_and_worn_for_the_whole_time_down(first, offsets):
    for device, start in _fall_starts(first):
        tod = (start - WINDOW_START_MS) % DAY_MS
        for t in range(tod, tod + FALL_MAX_DOWN_MS + 1, SECOND_MS):
            phase = routine_ref.phase(t, offsets[device])
            assert phase in {"bedroom", "out"}, (device, sgt(start), phase)


def test_at_most_one_fall_per_resident_per_singapore_day(first):
    days = Counter((device, sgt(start).date()) for device, start in _fall_starts(first))
    assert all(count == 1 for count in days.values())


def test_each_cancel_is_10_to_70_s_after_its_fall_message(first):
    falls = defaultdict(list)
    for line in first.by_message("fall"):
        falls[line["device_id"]].append(ts_to_epoch_ms(line["ts"]))
    for cancel in first.by_message("alert_cancel"):
        at = ts_to_epoch_ms(cancel["ts"])
        before = [t for t in falls[cancel["device_id"]] if t <= at]
        assert before, cancel["device_id"]
        # The fall message is sent 20 s after its ts (the impact).
        gap = at - (max(before) + FALL_MESSAGE_DELAY_MS)
        assert 10 * SECOND_MS <= gap <= 70 * SECOND_MS


def test_cancelled_falls_in_stderr_match_cancel_lines(first):
    cancelled = sorted(f.wearable for f in stderr_falls(first.run) if f.cancelled)
    lines = sorted(line["device_id"] for line in first.by_message("alert_cancel"))
    assert cancelled == lines
    assert len(cancelled) == round(FALLS / 4)


def test_falls_that_cant_be_placed_exit_2_before_writing(tmp_path, inputs):
    # 1 day x 4 wearables = 4 slots.
    argv = base_argv(inputs / "devices-small.json", falls=5)
    argv[argv.index(TO)] = FROM
    result = run(argv, tmp_path / "out")
    assert_usage_error(result)


# ======== output and summary (criterion 12) ========


def test_stdout_is_empty(first):
    assert first.run.stdout == ""


def test_stderr_has_only_header_falls_and_totals(first):
    lines = first.run.stderr.splitlines()
    assert HEADER_RE.fullmatch(lines[0])
    assert all(FALL_LINE_RE.fullmatch(line) for line in lines[1 : 1 + FALLS])
    assert len(lines) == 1 + FALLS + 2
    counts = LINES_RE.fullmatch(lines[-2])
    files = FILES_RE.fullmatch(lines[-1])
    assert counts, lines[-2]
    assert files, lines[-1]
    actual = Counter(line["message"] for line in first.all_lines)
    assert [int(c) for c in counts.groups()[:6]] == [actual[m] for m in MESSAGES]
    assert int(counts.groups()[6]) == len(first.all_lines)
    dates = sorted(KEY_RE.fullmatch(k)["date"] for k in first.files)
    assert int(files["files"]) == len(first.files)
    assert (files["first"], files["last"]) == (dates[0], dates[-1])


def test_stderr_has_no_payload_values(first):
    text = first.run.stderr
    for line in first.all_lines[:2000] + first.by_message("fall"):
        assert line["message_id"] not in text
        assert line["ts"] not in text
    assert "hr_bpm" not in text
    assert "res-" not in text


# ======== command line (criterion 1) ========


def test_backfill_never_imports_paho_or_the_live_runner():
    import ast

    tree = ast.parse((SRC_DIR / "backfill.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert not names & {"paho", "live", "main", "runner", "boto3", "botocore"}


# ======== exit 2 before writing anything (criterion 2) ========


def assert_usage_error(result: Run) -> None:
    assert result.code == 2, result.stderr
    assert result.stdout == ""
    lines = result.stderr.splitlines()
    assert len(lines) == 1, lines
    assert lines[0].startswith("error: ")
    assert not result.out.exists() or not any(result.out.iterdir())


def small(tmp_path: Path) -> list[str]:
    return ["--devices-file", str(small_devices_file(tmp_path))]


@pytest.mark.parametrize(
    ("first_day", "last_day"),
    [
        ("2026-02-30", "2026-03-02"),  # not a real date
        ("2026-03-01", "2026-02-29"),  # 2026 isn't a leap year
        ("2026-13-01", "2026-03-02"),
        ("2026-3-01", "2026-03-02"),  # not yyyy-mm-dd
        ("2026/03/01", "2026-03-02"),
        ("20260301", "2026-03-02"),
        ("2026-03-01 ", "2026-03-02"),
        ("", "2026-03-02"),
        ("yesterday", "2026-03-02"),
        ("2026-03-01", "2026-03-0x"),
        ("2026-03-01T00:00", "2026-03-02"),
    ],
)
def test_bad_date_exits_2(tmp_path, first_day, last_day):
    result = run(
        ["--from", first_day, "--to", last_day, *small(tmp_path)], tmp_path / "out"
    )
    assert_usage_error(result)


def test_from_after_to_exits_2(tmp_path):
    result = run(
        ["--from", "2026-03-05", "--to", "2026-03-04", *small(tmp_path)],
        tmp_path / "out",
    )
    assert_usage_error(result)


def test_from_equal_to_is_allowed():
    setup = backfill.validate(
        ["--from", "2026-03-05", "--to", "2026-03-05", "--out", "x"], TODAY
    )
    assert setup.window.first == setup.window.last


def test_range_of_401_days_with_lead_in_exits_2(tmp_path):
    # lead-in 2025-01-01 .. 2026-02-05 inclusive is 401 days.
    result = run(
        ["--from", "2025-01-03", "--to", "2026-02-05", *small(tmp_path)],
        tmp_path / "out",
    )
    assert_usage_error(result)


def test_range_of_400_days_with_lead_in_is_allowed(tmp_path):
    setup = backfill.validate(
        ["--from", "2025-01-03", "--to", "2026-02-04", "--out", str(tmp_path / "o")],
        TODAY,
    )
    assert (setup.window.last - setup.window.lead_in).days + 1 == 400
    assert not (tmp_path / "o").exists()


@pytest.mark.parametrize("last_day", ["2026-11-08", "2026-11-09", "2027-01-01"])
def test_to_today_or_later_exits_2(tmp_path, last_day):
    result = run(
        ["--from", "2026-11-01", "--to", last_day, *small(tmp_path)], tmp_path / "out"
    )
    assert_usage_error(result)


def test_to_yesterday_is_allowed(tmp_path):
    setup = backfill.validate(
        ["--from", "2026-11-06", "--to", "2026-11-07", "--out", str(tmp_path / "o")],
        TODAY,
    )
    assert setup.window.last == date(2026, 11, 7)


def test_today_is_the_singapore_date(monkeypatch):
    # 2026-11-07 16:30 UTC is 2026-11-08 00:30 in Singapore.
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 7, 16, 30, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(backfill, "datetime", FakeDatetime)
    assert backfill.today_sgt() == date(2026, 11, 8)


def test_to_singapore_today_exits_2_while_utc_is_still_yesterday(tmp_path, monkeypatch):
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 7, 16, 30, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(backfill, "datetime", FakeDatetime)
    err = io.StringIO()
    code = backfill.main(
        ["--from", "2026-11-06", "--to", "2026-11-08", "--out", str(tmp_path / "out")]
        + small(tmp_path),
        stderr=err,
    )
    assert code == 2
    assert err.getvalue().startswith("error: ")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "falls", ["-1", "101", "abc", "1.5", "", "1e2", "0x10", "12 3"]
)
def test_bad_falls_exits_2(tmp_path, falls):
    result = run(
        ["--from", FROM, "--to", TO, "--falls", falls, *small(tmp_path)],
        tmp_path / "out",
    )
    assert_usage_error(result)


@pytest.mark.parametrize("falls", ["0", "100"])
def test_falls_boundaries_are_allowed(tmp_path, falls):
    setup = backfill.validate(
        ["--from", FROM, "--to", TO, "--falls", falls, "--out", str(tmp_path / "o")],
        TODAY,
    )
    assert setup.falls_count == int(falls)


@pytest.mark.parametrize("seed", ["abc", "1.5", "", "one", "1e3"])
def test_bad_random_seed_exits_2(tmp_path, seed):
    result = run(
        ["--from", FROM, "--to", TO, "--random-seed", seed, *small(tmp_path)],
        tmp_path / "out",
    )
    assert_usage_error(result)


def test_out_that_is_a_file_exits_2(tmp_path):
    out = tmp_path / "out"
    out.write_text("synthetic", encoding="utf-8")
    result = run(["--from", FROM, "--to", TO, *small(tmp_path)], out)
    assert result.code == 2
    assert len(result.stderr.splitlines()) == 1
    assert result.stderr.startswith("error: ")
    assert out.read_text(encoding="utf-8") == "synthetic"


def test_out_that_is_a_non_empty_folder_exits_2_and_is_left_alone(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("synthetic", encoding="utf-8")
    result = run(["--from", FROM, "--to", TO, *small(tmp_path)], out)
    assert result.code == 2
    assert result.stderr.startswith("error: ")
    assert [p.name for p in out.iterdir()] == ["keep.txt"]


def test_out_that_is_a_symlink_to_an_empty_folder_exits_2(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    out = tmp_path / "out"
    out.symlink_to(target, target_is_directory=True)
    result = run(["--from", FROM, "--to", TO, *small(tmp_path)], out)
    assert_usage_error(result)
    assert out.is_symlink()
    assert list(target.iterdir()) == []


def test_out_that_is_a_dangling_symlink_exits_2(tmp_path):
    target = tmp_path / "not-there"
    out = tmp_path / "out"
    out.symlink_to(target, target_is_directory=True)
    result = run(["--from", FROM, "--to", TO, *small(tmp_path)], out)
    assert_usage_error(result)
    assert out.is_symlink()
    assert not target.exists()


def test_out_whose_parent_is_missing_exits_2_and_creates_nothing(tmp_path):
    out = tmp_path / "missing" / "out"
    result = run(["--from", FROM, "--to", TO, *small(tmp_path)], out)
    assert_usage_error(result)
    assert not (tmp_path / "missing").exists()


def test_out_that_is_an_empty_folder_is_used(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    devices = small(tmp_path)
    result = run(["--from", TO, "--to", TO, "--falls", "0", *devices], out)
    assert result.code == 0, result.stderr
    assert (out / "telemetry").is_dir()


def test_missing_required_flag_exits_2(tmp_path):
    err = io.StringIO()
    code = backfill.main(["--from", FROM, "--to", TO], stderr=err, today=TODAY)
    assert code == 2
    assert err.getvalue().startswith("error: ")
    assert len(err.getvalue().splitlines()) == 1


def test_unknown_flag_exits_2(tmp_path):
    result = run(
        ["--from", FROM, "--to", TO, "--endpoint", "example.invalid", *small(tmp_path)],
        tmp_path / "out",
    )
    assert_usage_error(result)


def write_inputs(tmp_path: Path, seed_doc, devices_doc) -> list[str]:
    seed_path = tmp_path / "seed.json"
    devices_path = tmp_path / "devices.json"
    seed_path.write_text(json.dumps(seed_doc), encoding="utf-8")
    devices_path.write_text(json.dumps(devices_doc), encoding="utf-8")
    return ["--seed-file", str(seed_path), "--devices-file", str(devices_path)]


def main_error(files: list[str]) -> str:
    err = io.StringIO()
    code = main.main(
        ["--dry-run", "--instant", "--duration", "1m", *files],
        stdout=io.StringIO(),
        stderr=err,
        client_factory=lambda device_id: pytest.fail("connected"),
    )
    assert code == 2
    return err.getvalue()


def _drop_baseline(seed, devices):
    del next(r for r in seed["residents"] if r["resident_id"] == "res-0003")["baseline"]


def _unassigned_device(seed, devices):
    seed["devices"] = [d for d in seed["devices"] if d["device_id"] != "bedroom-0013"]


def _wrong_type(seed, devices):
    entry = next(d for d in seed["devices"] if d["device_id"] == "wearable-0006")
    entry["device_type"] = "bedroom"


def _unknown_resident(seed, devices):
    entry = next(d for d in seed["devices"] if d["device_id"] == "wearable-0006")
    entry["resident_id"] = "res-0099"


def _bad_devices_list(seed, devices):
    devices["devices"] = "none"


@pytest.mark.parametrize(
    "break_input",
    [
        _drop_baseline,
        _unassigned_device,
        _wrong_type,
        _unknown_resident,
        _bad_devices_list,
    ],
)
def test_invalid_seed_or_devices_file_exits_2_with_mains_message(
    tmp_path, seed_copy, devices_copy, break_input
):
    break_input(seed_copy, devices_copy)
    files = write_inputs(tmp_path, seed_copy, devices_copy)
    result = run(["--from", FROM, "--to", TO, *files], tmp_path / "out")
    assert_usage_error(result)
    assert result.stderr == main_error(files)


@pytest.mark.parametrize("which", ["--seed-file", "--devices-file"])
def test_missing_input_file_exits_2_with_mains_message(tmp_path, which):
    files = [which, str(tmp_path / "nowhere.json")]
    result = run(["--from", FROM, "--to", TO, *files], tmp_path / "out")
    assert_usage_error(result)
    assert result.stderr == main_error(files)


@pytest.mark.parametrize("which", ["--seed-file", "--devices-file"])
def test_unreadable_json_input_exits_2_with_mains_message(tmp_path, which):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    files = [which, str(bad)]
    result = run(["--from", FROM, "--to", TO, *files], tmp_path / "out")
    assert_usage_error(result)
    assert result.stderr == main_error(files)


# ======== nothing kept when a run fails after it started writing ========


def _fail_after_writing(monkeypatch, exc: BaseException) -> None:
    """Raise exc on the engine's 30th step, after files were opened."""
    real = Engine.take_notices
    calls = {"n": 0}

    def take_notices(self):
        calls["n"] += 1
        if calls["n"] == 30:
            if isinstance(exc, str):
                return [exc]
            raise exc
        return real(self)

    monkeypatch.setattr(Engine, "take_notices", take_notices)


def test_skipped_fall_exits_1_and_removes_the_output(tmp_path, monkeypatch):
    _fail_after_writing(monkeypatch, "scenario fall 1 wearable-0001 skipped: test")
    result = run(
        ["--from", TO, "--to", TO, "--falls", "0", *small(tmp_path)], tmp_path / "out"
    )
    assert result.code == 1
    assert result.stderr.splitlines()[-1].startswith("error: ")
    assert "nothing kept" in result.stderr
    assert not result.out.exists()


def test_interrupt_exits_130_and_leaves_an_existing_out_empty(tmp_path, monkeypatch):
    _fail_after_writing(monkeypatch, KeyboardInterrupt())
    out = tmp_path / "out"
    out.mkdir()
    result = run(["--from", TO, "--to", TO, "--falls", "0", *small(tmp_path)], out)
    assert result.code == 130
    assert out.is_dir()
    assert list(out.iterdir()) == []


def test_unexpected_error_is_raised_and_removes_the_output(tmp_path, monkeypatch):
    _fail_after_writing(monkeypatch, RuntimeError("synthetic failure"))
    with pytest.raises(RuntimeError):
        run(
            ["--from", TO, "--to", TO, "--falls", "0", *small(tmp_path)],
            tmp_path / "out",
        )
    assert not (tmp_path / "out").exists()
