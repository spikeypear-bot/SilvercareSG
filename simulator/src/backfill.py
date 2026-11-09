"""Simulator backfill: past days of history as local raw-history files
(docs/features/simulator-backfill.md).

Runs the simulator engine over a past date range on an instant clock and writes every
message as the IoT rules and the history Lambda would have stored it:

    <out>/telemetry/message=<m>/date=<yyyy-mm-dd>/<yyyymmdd>T235959Z-<uuid>.jsonl.gz

one gzipped JSON Lines file per message type and UTC date of `received_at`, lines in
`received_at` order. Falls (some cancelled with "I'm OK") are started with the
engine's own `fall` and `im-ok` scenarios. Four fixed residents are unusual
(docs/features/backfill-variation.md): wider noise, a decline, bad days, irregular
sleep, readings that may cross their thresholds and alert, and `--vitals-episodes`
heart-rate spikes and SpO2 drops.

  python simulator/src/backfill.py --from 2026-03-01 --to 2026-10-31 --out backfill-data
      [--falls 12] [--vitals-episodes 20] [--random-seed 1] [--seed-file ...] [--devices-file ...]

It never connects to anything (no MQTT, no AWS) and writes only under --out. Bad flags,
invalid seed/devices files, or falls that can't be placed exit 2 with one `error: ...`
line before anything is written. The same arguments and input files always give
byte-identical files with the same names. Prints only to stderr: the run, the falls
(wearable ID and time), and counts; never payload values.

Standard library only. Importing this module reads no file and writes nothing.
"""

from __future__ import annotations

import sys

# Before any import that needs 3.11 (datetime.UTC), so older Pythons get a clear message.
# Everything in this file must still parse on 3.9 so this check can run.
if sys.version_info < (3, 11):  # noqa: UP036
    print(
        "backfill.py needs Python 3.11 or newer (you have"
        f" {sys.version_info[0]}.{sys.version_info[1]}); see README",
        file=sys.stderr,
    )
    sys.exit(2)

import argparse
import gzip
import heapq
import json
import math
import os
import random
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import NoReturn, TextIO

import routine
import scenarios
import variation
from engine import Engine, Message
from report import SGT
from residents import SetupError, build_residents, load_json

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED_FILE = REPO_ROOT / "scripts" / "seed-data.json"
DEFAULT_DEVICES_FILE = REPO_ROOT / "infra" / "devices" / "devices.json"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

DAY_MS = routine.DAY_MS
SECOND_MS = routine.SECOND_MS
LEAD_IN_DAYS = 2
MAX_RANGE_DAYS = 400
DEFAULT_FALLS = 12
MAX_FALLS = 100
DEFAULT_RANDOM_SEED = 1
# About 1 a month per unusual resident at --from, rising to about 10 by --to (8 months).
DEFAULT_VITALS_EPISODES = 176
MAX_VITALS_EPISODES = 400
# Episodes are this many times as frequent at --to as at --from (linear in between).
EPISODE_RAMP = 10
# A vitals episode starts at least this long after, and ends at least this long
# before, any fall or other episode on the same wearable.
EPISODE_GAP_MS = 10 * routine.MINUTE_MS
# Delay from ts to received_at (the IoT rule's timestamp()), in ms, inclusive.
RECEIVED_DELAY_MS = (100, 900)
# A cancel ("I'm OK") starts this long after its fall's start, in ms, inclusive.
CANCEL_AFTER_MS = (30 * SECOND_MS, 90 * SECOND_MS)
# Fall phases: up and wearing the watch (not asleep, not charging).
FALL_PHASES = frozenset({routine.BEDROOM, routine.OUT})
MAX_TIME_DRAWS = 10_000

# The engine is driven this far at a time.
STEP_MS = routine.HOUR_MS
# received_at is the send time plus 100-900 ms, and the engine sends in time order, so a
# later message can only overtake a line by less than 1 s. A line whose received_at is
# this far before the engine's time is safe to write (a wide margin).
REORDER_MARGIN_MS = routine.MINUTE_MS

# Message types in the summary's order; any other type would be listed after them.
SUMMARY_TYPES = ("vitals", "status", "motion", "bed", "fall", "alert_cancel")
FILE_STAMP = "T235959Z"
LINES_PER_WRITE = 1024

DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
INT_RE = re.compile(r"^[+-]?[0-9]{1,30}$")

_encode = json.JSONEncoder(separators=(",", ":")).encode


class UsageError(Exception):
    """A bad flag or input file: exit 2 with `error: <message>`."""


class BackfillFailed(Exception):
    """The run went wrong after it started writing: exit 1, output removed."""


class _Parser(argparse.ArgumentParser):
    """Every argparse problem becomes one `error: ...` line and exit 2."""

    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="simulator/src/backfill.py",
        description="Write past days of simulated history as local raw-history files.",
    )
    parser.add_argument(
        "--from", dest="from_date", required=True, help="first day, yyyy-mm-dd SGT"
    )
    parser.add_argument(
        "--to", dest="to_date", required=True, help="last day, yyyy-mm-dd SGT"
    )
    parser.add_argument(
        "--out", required=True, help="output folder (must not exist, or be empty)"
    )
    parser.add_argument(
        "--falls", default=str(DEFAULT_FALLS), help="number of falls, 0 to 100"
    )
    parser.add_argument(
        "--vitals-episodes",
        default=str(DEFAULT_VITALS_EPISODES),
        help="heart-rate spikes and SpO2 drops on the unusual residents, 0 to 400",
    )
    parser.add_argument(
        "--random-seed", default=str(DEFAULT_RANDOM_SEED), help="integer seed"
    )
    parser.add_argument("--seed-file", type=Path, default=DEFAULT_SEED_FILE)
    parser.add_argument("--devices-file", type=Path, default=DEFAULT_DEVICES_FILE)
    return parser


# ---- validation (nothing written yet) ----


def parse_date(text: str, flag: str) -> date:
    if not DATE_RE.fullmatch(text):
        raise UsageError(f"{flag} must be a date yyyy-mm-dd")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise UsageError(f"{flag} is not a real date") from None


def parse_int(text: str, flag: str) -> int:
    if not INT_RE.fullmatch(text.strip()):
        raise UsageError(f"{flag} must be an integer")
    return int(text)


def check_out(out: Path) -> None:
    if out.is_symlink():
        raise UsageError("--out is a symbolic link")
    if out.exists():
        if not out.is_dir() or any(out.iterdir()):
            raise UsageError("--out exists and isn't an empty directory")
    elif not out.parent.is_dir():
        # Never create folders outside --out.
        raise UsageError("--out's parent folder doesn't exist")


@dataclass(frozen=True)
class Window:
    first: date  # --from
    last: date  # --to
    lead_in: date  # --from minus 2 days: the engine's first day
    start_epoch_ms: int  # lead_in 00:00 SGT
    stop_epoch_ms: int  # --to + 1 day 00:00 SGT (exclusive)

    @property
    def stop_elapsed_ms(self) -> int:
        return self.stop_epoch_ms - self.start_epoch_ms

    def day_elapsed_ms(self, day: date) -> int:
        """Elapsed ms at 00:00 SGT of day."""
        return (day - self.lead_in).days * DAY_MS


def sgt_midnight_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=SGT).timestamp()) * 1000


def make_window(first: date, last: date, today: date) -> Window:
    if first > last:
        raise UsageError("--from is after --to")
    lead_in = first - timedelta(days=LEAD_IN_DAYS)
    if (last - lead_in).days + 1 > MAX_RANGE_DAYS:
        raise UsageError(
            f"the range with its {LEAD_IN_DAYS}-day lead-in is more than"
            f" {MAX_RANGE_DAYS} days"
        )
    if last >= today:
        raise UsageError("--to must be before today (Singapore time)")
    return Window(
        first=first,
        last=last,
        lead_in=lead_in,
        start_epoch_ms=sgt_midnight_ms(lead_in),
        stop_epoch_ms=sgt_midnight_ms(last + timedelta(days=1)),
    )


# ---- seeded randomness ----


def seeded_uuid4(rng: random.Random) -> Callable[[], str]:
    """Version-4 UUID strings from rng (valid for ingest's UUID_V4_RE)."""
    return lambda: str(uuid.UUID(int=rng.getrandbits(128), version=4))


def rng_for(seed: int, purpose: str) -> random.Random:
    return random.Random(f"{seed}:backfill:{purpose}")


# ---- falls ----


@dataclass(frozen=True)
class Fall:
    wearable_id: str
    at_ms: int  # elapsed ms of the fall's start
    cancel_at_ms: int | None  # elapsed ms of the "I'm OK", or None


PhaseOf = Callable[[str, int], str]  # (wearable, elapsed ms) -> routine phase


def phases_ok(phase_of: PhaseOf, wearable: str, start_ms: int, end_ms: int, ok) -> bool:
    """Every phase from start_ms to end_ms (both included) is in ok. Phases change on
    whole seconds, so checking every second is exact."""
    return all(
        phase_of(wearable, t) in ok for t in range(start_ms, end_ms + 1, SECOND_MS)
    )


def place_falls(
    count: int,
    window: Window,
    wearables: list[str],
    phase_of: PhaseOf,
    rng: random.Random,
) -> list[Fall]:
    """count falls on random wearables, at most one per wearable (resident) per
    Singapore day in [--from, --to], each at a random time that is up and worn for the
    whole time down; round(count / 4) of them cancelled. In time order."""
    days = [
        window.first + timedelta(days=i)
        for i in range((window.last - window.first).days + 1)
    ]
    if count > len(days) * len(wearables):
        raise UsageError(
            f"can't place {count} falls: at most one per resident per day fits"
            f" {len(days) * len(wearables)} in this range"
        )
    free = {w: list(days) for w in wearables}
    placed: list[tuple[str, int]] = []
    for _ in range(count):
        wearable = rng.choice([w for w in wearables if free[w]])
        day = free[wearable].pop(rng.randrange(len(free[wearable])))
        for _attempt in range(MAX_TIME_DRAWS):
            at = window.day_elapsed_ms(day) + rng.randrange(DAY_MS // SECOND_MS) * 1000
            end = at + scenarios.FALL_MAX_DOWN_MS
            if phases_ok(phase_of, wearable, at, end, FALL_PHASES):
                break
        else:  # pragma: no cover - every day has hours up and worn
            raise UsageError(f"can't place a fall for {wearable}")
        placed.append((wearable, at))
    cancelled = set(rng.sample(range(count), round(count / 4)))
    falls = [
        Fall(
            wearable,
            at_ms,
            at_ms + rng.randint(*CANCEL_AFTER_MS) if index in cancelled else None,
        )
        for index, (wearable, at_ms) in enumerate(placed)
    ]
    return sorted(falls, key=lambda f: (f.at_ms, f.wearable_id))


# ---- unusual residents (docs/features/backfill-variation.md) ----


def build_profiles(window: Window, seed: int) -> dict[str, variation.Profile]:
    """A profile per unusual resident: a bad day with probability 1/10 and a bedtime
    and wake-time shift for every Singapore day in [--from, --to] (not the lead-in)."""
    rng = rng_for(seed, "variation")
    first = (window.first - window.lead_in).days
    last = (window.last - window.lead_in).days
    profiles = {}
    for resident_id in variation.UNUSUAL_RESIDENTS:
        bad, bedtime, wake = set(), {}, {}
        for day in range(first, last + 1):
            if rng.random() < variation.BAD_DAY_RATE:
                bad.add(day)
            bedtime[day] = variation.sleep_shift_ms(rng)
            wake[day] = variation.sleep_shift_ms(rng)
        profiles[resident_id] = variation.Profile(
            window.day_elapsed_ms(window.first),
            window.stop_elapsed_ms,
            frozenset(bad),
            bedtime,
            wake,
        )
    return profiles


@dataclass(frozen=True)
class Episode:
    name: str  # hr-spike or spo2-drop
    wearable_id: str
    at_ms: int  # elapsed ms of its start


def ramp_fraction(u: float) -> float:
    """Inverse CDF of a density rising linearly from 1 to EPISODE_RAMP over [0, 1]:
    u uniform in [0, 1) -> a fraction of the range in [0, 1)."""
    r = EPISODE_RAMP
    return (math.sqrt(1 + (r - 1) * (r + 1) * u) - 1) / (r - 1)


def place_episodes(
    count: int,
    window: Window,
    wearables: list[str],
    phase_of: PhaseOf,
    falls: list[Fall],
    rng: random.Random,
) -> list[Episode]:
    """round(count / 2) hr-spikes, then spo2-drops, each on a random unusual wearable
    at a random time (EPISODE_RAMP times as likely at --to as at --from, linear in
    between) worn for its whole 3 minutes, at least EPISODE_GAP_MS from any
    fall or other episode on that wearable. No unusual wearable in the run (a smaller
    devices file): none. In time order."""
    if not wearables:
        return []
    names = [scenarios.HR_SPIKE] * round(count / 2)
    names += [scenarios.SPO2_DROP] * (count - len(names))
    busy: dict[str, list[tuple[int, int]]] = {w: [] for w in wearables}
    for fall in falls:
        if fall.wearable_id in busy:
            busy[fall.wearable_id].append(
                (fall.at_ms, fall.at_ms + scenarios.FALL_MAX_DOWN_MS)
            )
    start = window.day_elapsed_ms(window.first)
    seconds = (window.stop_elapsed_ms - start - scenarios.VITALS_DURATION_MS) // 1000
    episodes = []
    for name in names:
        for _attempt in range(MAX_TIME_DRAWS):
            wearable = rng.choice(wearables)
            at = start + int(ramp_fraction(rng.random()) * seconds) * SECOND_MS
            end = at + scenarios.VITALS_DURATION_MS
            clash = any(
                at < e + EPISODE_GAP_MS and s < end + EPISODE_GAP_MS
                for s, e in busy[wearable]
            )
            if not clash and phases_ok(
                phase_of, wearable, at, end, {routine.ASLEEP, *FALL_PHASES}
            ):
                break
        else:
            raise UsageError(
                f"can't place {count} vitals episodes on the unusual residents in"
                " this range"
            )
        busy[wearable].append((at, end))
        episodes.append(Episode(name, wearable, at))
    return sorted(episodes, key=lambda e: (e.at_ms, e.wearable_id))


# ---- lines and files ----


def utc_date(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms // 1000, UTC).strftime("%Y-%m-%d")


def object_path(out: Path, message: str, day: str, unique: str) -> Path:
    return (
        out
        / "telemetry"
        / f"message={message}"
        / f"date={day}"
        / f"{day.replace('-', '')}{FILE_STAMP}-{unique}.jsonl.gz"
    )


@dataclass
class _OpenFile:
    day: str
    path: Path
    part: Path
    raw: object
    gz: gzip.GzipFile
    pending: list[bytes] = field(default_factory=list)

    def flush_pending(self) -> None:
        if self.pending:
            self.gz.write(b"".join(self.pending))
            self.pending.clear()


class Output:
    """Writes lines to one gzip JSON Lines file per (message type, UTC date); at most
    one open file per message type. A file is written as <name>.part and renamed when
    complete. Remembers every file and folder it created, to remove them on failure."""

    def __init__(self, out: Path, new_name: Callable[[], str]) -> None:
        self.out = out
        self._new_name = new_name
        self._open: dict[str, _OpenFile] = {}
        self.files: list[Path] = []
        self.created_dirs: list[Path] = []
        self.lines: dict[str, int] = {}
        self.dates: set[str] = set()
        self.alerts_by_device: dict[str, int] = {}

    def _mkdirs(self, folder: Path) -> None:
        missing = []
        while not folder.exists():
            missing.append(folder)
            folder = folder.parent
        for path in reversed(missing):
            path.mkdir()
            self.created_dirs.append(path)

    def write(self, message: str, day: str, line: str) -> None:
        current = self._open.get(message)
        if current is not None and current.day != day:
            self._close(message)
            current = None
        if current is None:
            current = self._start(message, day)
        current.pending.append(line.encode("utf-8") + b"\n")
        if len(current.pending) >= LINES_PER_WRITE:
            current.flush_pending()
        self.lines[message] = self.lines.get(message, 0) + 1

    def _start(self, message: str, day: str) -> _OpenFile:
        path = object_path(self.out, message, day, self._new_name())
        self._mkdirs(path.parent)
        part = path.with_name(path.name + ".part")
        raw = open(part, "xb")  # noqa: SIM115 - closed in _close / abort
        # filename="" and mtime=0: no name or time in the gzip header, so the same
        # input gives the same bytes.
        gz = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)
        opened = _OpenFile(day, path, part, raw, gz)
        self._open[message] = opened
        self.files.append(part)
        self.dates.add(day)
        return opened

    def _close(self, message: str) -> None:
        current = self._open.pop(message)
        current.flush_pending()
        current.gz.close()
        current.raw.close()
        os.replace(current.part, current.path)
        self.files[self.files.index(current.part)] = current.path

    def close(self) -> None:
        for message in list(self._open):
            self._close(message)

    def abort(self) -> None:
        """Remove every file and folder this run created (best effort)."""
        for current in self._open.values():
            for handle in (current.gz, current.raw):
                try:
                    handle.close()
                except Exception:  # noqa: BLE001,S110 - removing it anyway
                    pass
        self._open.clear()
        for path in self.files:
            path.unlink(missing_ok=True)
        for folder in reversed(self.created_dirs):
            try:
                folder.rmdir()
            except OSError:
                pass


class Lines:
    """Turns engine messages into stored lines and writes each message type's lines
    in received_at order (a small reorder buffer per type)."""

    def __init__(self, output: Output, delays: random.Random, window: Window) -> None:
        self._output = output
        self._delays = delays
        self._start = window.start_epoch_ms
        self._stop = window.stop_epoch_ms
        self._heaps: dict[str, list[tuple[int, int, str]]] = {}
        self._seq = 0
        self._dates: dict[int, str] = {}

    def add(self, message: Message) -> None:
        line = dict(message.payload)
        line["device_id"] = message.device_id
        line["message"] = message.message
        # When the engine sends it (a fall goes out 20 s after its ts, the impact),
        # plus the trip to IoT Core.
        sent_at = self._start + message.elapsed_ms
        received_at = sent_at + self._delays.randint(*RECEIVED_DELAY_MS)
        if received_at >= self._stop:
            return
        line["received_at"] = received_at
        if message.message == "vitals_alert":
            counts = self._output.alerts_by_device
            counts[message.device_id] = counts.get(message.device_id, 0) + 1
        self._seq += 1
        heap = self._heaps.setdefault(message.message, [])
        heapq.heappush(heap, (received_at, self._seq, _encode(line)))

    def flush(self, before_epoch_ms: int | None) -> None:
        """Write every buffered line with received_at < before_epoch_ms (None: all)."""
        for message in sorted(self._heaps):
            heap = self._heaps[message]
            while heap and (before_epoch_ms is None or heap[0][0] < before_epoch_ms):
                received_at, _, text = heapq.heappop(heap)
                self._output.write(message, self._date(received_at), text)

    def _date(self, epoch_ms: int) -> str:
        day = epoch_ms // DAY_MS
        text = self._dates.get(day)
        if text is None:
            text = utc_date(day * DAY_MS)
            self._dates[day] = text
        return text


# ---- run ----


def _check_notices(engine: Engine) -> None:
    for notice in engine.take_notices():
        if " skipped" in notice:
            # The notice names the scenario, the wearable, and the reason only.
            raise BackfillFailed(f"a scenario was skipped ({notice})")


def generate(
    engine: Engine, window: Window, output: Output, delays: random.Random
) -> None:
    """Drive the engine over the window and write its lines."""
    lines = Lines(output, delays, window)
    last_ms = window.stop_elapsed_ms - 1  # events due at the stop are dropped
    until = -1
    while until < last_ms:
        until = min(until + STEP_MS, last_ms)
        for message in engine.pop_due(until):
            lines.add(message)
        _check_notices(engine)
        lines.flush(engine.epoch_ms(until) - REORDER_MARGIN_MS)
    lines.flush(None)
    output.close()


def format_moment(at_ms: int, window: Window) -> str:
    moment = datetime.fromtimestamp((window.start_epoch_ms + at_ms) / 1000, UTC)
    return f"{moment.astimezone(SGT):%Y-%m-%d %H:%M} SGT"


def format_fall(fall: Fall, window: Window) -> str:
    cancelled = " (cancelled)" if fall.cancel_at_ms is not None else ""
    return f"fall {fall.wearable_id} {format_moment(fall.at_ms, window)}{cancelled}"


def summary_lines(output: Output) -> list[str]:
    types = list(SUMMARY_TYPES) + sorted(set(output.lines) - set(SUMMARY_TYPES))
    counts = ", ".join(f"{m} {output.lines.get(m, 0)}" for m in types)
    total = sum(output.lines.values())
    lines = [f"lines: {counts} (total {total})"]
    if output.dates:
        dates = sorted(output.dates)
        lines.append(f"files: {len(output.files)}, UTC dates {dates[0]} to {dates[-1]}")
    else:
        lines.append("files: 0")
    return lines


def today_sgt() -> date:
    return datetime.now(UTC).astimezone(SGT).date()


@dataclass(frozen=True)
class Setup:
    window: Window
    falls_count: int
    episodes_count: int
    seed: int
    out: Path
    residents: list
    device_order: list[str]


def validate(argv: list[str] | None, today: date) -> Setup:
    """Every check of criterion 2, in order. Raises UsageError; writes nothing."""
    args = build_parser().parse_args(argv)
    first = parse_date(args.from_date, "--from")
    last = parse_date(args.to_date, "--to")
    window = make_window(first, last, today)
    falls_count = parse_int(args.falls, "--falls")
    if not 0 <= falls_count <= MAX_FALLS:
        raise UsageError(f"--falls must be an integer from 0 to {MAX_FALLS}")
    episodes_count = parse_int(args.vitals_episodes, "--vitals-episodes")
    if not 0 <= episodes_count <= MAX_VITALS_EPISODES:
        raise UsageError(
            f"--vitals-episodes must be an integer from 0 to {MAX_VITALS_EPISODES}"
        )
    seed = parse_int(args.random_seed, "--random-seed")
    out = Path(args.out)
    check_out(out)
    try:
        seed_doc = load_json(args.seed_file, "seed file")
        devices_doc = load_json(args.devices_file, "devices file")
        residents, device_order = build_residents(seed_doc, devices_doc)
    except SetupError as exc:
        raise UsageError(str(exc)) from None
    return Setup(
        window, falls_count, episodes_count, seed, out, residents, device_order
    )


def main(
    argv: list[str] | None = None,
    *,
    stderr: TextIO | None = None,
    today: date | None = None,
) -> int:
    """Run the backfill. `today` (Singapore date) is for tests."""
    stderr = stderr or sys.stderr

    def err(line: str) -> None:
        print(line, file=stderr, flush=True)

    try:
        setup = validate(argv, today or today_sgt())
        window = setup.window
        profiles = build_profiles(window, setup.seed)
        engine = Engine(
            setup.residents,
            setup.device_order,
            setup.device_order,
            seed=setup.seed,
            start_epoch_ms=window.start_epoch_ms,
            start_routine_ms=0,  # midnight Singapore time
            schedule_startup=True,
            new_id=seeded_uuid4(rng_for(setup.seed, "message-ids")),
            profiles=profiles,
        )
        wearables = [d for d in engine.devices if d.startswith("wearable-")]
        unusual = {
            r.wearable_id: r.resident_id
            for r in setup.residents
            if r.resident_id in profiles and r.wearable_id in wearables
        }
        falls = place_falls(
            setup.falls_count,
            window,
            wearables,
            engine.phase,
            rng_for(setup.seed, "falls"),
        )
        episodes = place_episodes(
            setup.episodes_count,
            window,
            [w for w in wearables if w in unusual],
            engine.phase,
            falls,
            rng_for(setup.seed, "episodes"),
        )
    except UsageError as exc:
        err(f"error: {exc}")
        return EXIT_USAGE

    for fall in falls:
        engine.schedule_scenario(scenarios.FALL, fall.wearable_id, fall.at_ms)
        if fall.cancel_at_ms is not None:
            engine.schedule_scenario(
                scenarios.IM_OK, fall.wearable_id, fall.cancel_at_ms
            )
    for episode in episodes:
        engine.schedule_scenario(episode.name, episode.wearable_id, episode.at_ms)

    err(
        f"backfill: {window.first} to {window.last} (lead-in from {window.lead_in}),"
        f" {len(engine.devices)} devices, random seed {setup.seed}"
    )
    for fall in falls:
        err(format_fall(fall, window))
    for episode in episodes:
        err(
            f"{episode.name} {episode.wearable_id} {format_moment(episode.at_ms, window)}"
        )

    created_out = not setup.out.exists()
    setup.out.mkdir(exist_ok=True)
    output = Output(setup.out, seeded_uuid4(rng_for(setup.seed, "file-names")))
    try:
        generate(engine, window, output, rng_for(setup.seed, "received-at"))
        expected = {"fall": len(falls), "alert_cancel": round(len(falls) / 4)}
        for message, count in expected.items():
            if output.lines.get(message, 0) != count:
                raise BackfillFailed(f"wrote a different number of {message} lines")
        if set(output.alerts_by_device) - set(unusual):
            raise BackfillFailed("wrote vitals_alert lines for an ordinary resident")
    except BaseException as exc:
        output.abort()
        if created_out:
            try:
                setup.out.rmdir()
            except OSError:
                pass
        if isinstance(exc, BackfillFailed):
            err(f"error: {exc}; nothing kept")
            return EXIT_FAILED
        if isinstance(exc, KeyboardInterrupt):
            err("error: interrupted; nothing kept")
            return EXIT_INTERRUPTED
        raise

    for wearable, resident_id in sorted(unusual.items(), key=lambda x: x[1]):
        bad = len(profiles[resident_id].bad_days)
        alerts = output.alerts_by_device.get(wearable, 0)
        err(f"unusual {resident_id} {wearable}: {bad} bad days, {alerts} vitals_alert")
    for line in summary_lines(output):
        err(line)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
