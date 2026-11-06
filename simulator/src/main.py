"""SilverCare device simulator (docs/features/simulator-core.md).

Plays the devices in infra/devices/devices.json, each resident from
scripts/seed-data.json following their own baseline vitals and daily routine, and
publishes normal data exactly as docs/metrics.md defines it.

  python simulator/src/main.py --dry-run [--instant --duration 24h] [--random-seed 1]
  python simulator/src/main.py --endpoint <iot_endpoint> --keys-dir <dir>   (live)

A live run needs --endpoint, an AWS IoT endpoint in ap-southeast-1 (ending
.iot.ap-southeast-1.amazonaws.com, so data stays in Singapore), and --keys-dir: there
is no default keys folder, so every live run names it (`make simulate` passes it).
--ca-file defaults to the public devices/AmazonRootCA1.pem.

A live run connects to AWS IoT Core with each device's own certificate and prints only
counts, device IDs, and reason codes. --dry-run opens no connection and reads no key
files: it prints one {"topic", "payload"} JSON line per message to stdout and the
summaries to stderr. Bad or conflicting flags, or invalid seed/devices files, exit 2
before anything connects.

Standard library and paho-mqtt only (paho is imported only in a live run). Importing
this module opens no connection and reads no file.
"""

from __future__ import annotations

import argparse
import random
import re
import signal
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import routine
from clock import InstantClock, RealClock, now_epoch_ms
from engine import Engine
from live import ClientFactory, LinkManager, missing_key_files, paho_client_factory
from report import SGT, Reporter
from residents import DEVICE_ID_RE, SetupError, build_residents, load_json
from runner import DRY_RUN_STEP_S, LIVE_STEP_S, DryRunSink, LiveSink, run

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED_FILE = REPO_ROOT / "scripts" / "seed-data.json"
DEFAULT_DEVICES_FILE = REPO_ROOT / "infra" / "devices" / "devices.json"
DEFAULT_CA_FILE = REPO_ROOT / "devices" / "AmazonRootCA1.pem"

ENDPOINT_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
# Data stays in Singapore: only AWS IoT endpoints in ap-southeast-1 (case-insensitive).
ENDPOINT_SUFFIX = ".iot.ap-southeast-1.amazonaws.com"
DURATION_RE = re.compile(r"^([0-9]{1,6})([smh])$")
DURATION_UNIT_MS = {"s": 1000, "m": routine.MINUTE_MS, "h": routine.HOUR_MS}

EXIT_OK = 0
EXIT_NO_CONNECTION = 1
EXIT_USAGE = 2


def parse_duration(text: str) -> int:
    """'90s', '30m', '2h', '24h' -> milliseconds (> 0). Raises ValueError."""
    match = DURATION_RE.fullmatch(text)
    if not match or int(match.group(1)) == 0:
        raise ValueError("duration must be a positive number with s, m, or h, e.g. 30m")
    return int(match.group(1)) * DURATION_UNIT_MS[match.group(2)]


def _duration(text: str) -> int:
    try:
        return parse_duration(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _start_at(text: str) -> int:
    try:
        return routine.parse_hhmm(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _device_list(text: str) -> list[str]:
    ids = [part.strip() for part in text.split(",")]
    if not ids or any(not DEVICE_ID_RE.fullmatch(i) for i in ids):
        raise argparse.ArgumentTypeError(
            "devices must be a comma-separated list of IDs like wearable-0001"
        )
    if len(set(ids)) != len(ids):
        raise argparse.ArgumentTypeError("devices lists an ID twice")
    return ids


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simulator/src/main.py",
        description="Simulate SilverCare devices publishing normal data to AWS IoT.",
    )
    parser.add_argument(
        "--endpoint",
        help="live only, required: AWS IoT endpoint host (the iot_endpoint output),"
        f" ending {ENDPOINT_SUFFIX}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print messages instead of publishing (no keys, no network)",
    )
    parser.add_argument(
        "--instant",
        action="store_true",
        help="dry-run only: generate the whole --duration at once",
    )
    parser.add_argument(
        "--duration",
        type=_duration,
        help="how long to run, e.g. 30m, 2h, 24h (default: until Ctrl+C)",
    )
    parser.add_argument(
        "--start-at",
        type=_start_at,
        help="routine time of day at start, HH:MM Singapore time (default: now)",
    )
    parser.add_argument(
        "--devices", type=_device_list, help="comma-separated device IDs (default: all)"
    )
    parser.add_argument(
        "--random-seed", type=int, help="seed for offsets and values (default: random)"
    )
    parser.add_argument(
        "--keys-dir",
        type=Path,
        help="live only, required: folder with <device_id>.crt and .key (no default)",
    )
    parser.add_argument(
        "--ca-file",
        type=Path,
        help="live only: Amazon root CA file (default: devices/AmazonRootCA1.pem)",
    )
    parser.add_argument("--seed-file", type=Path, default=DEFAULT_SEED_FILE)
    parser.add_argument("--devices-file", type=Path, default=DEFAULT_DEVICES_FILE)
    return parser


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse and check flags; bad or conflicting flags exit 2 (argparse)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.dry_run:
        for flag, value in (
            ("--endpoint", args.endpoint),
            ("--keys-dir", args.keys_dir),
            ("--ca-file", args.ca_file),
        ):
            if value is not None:
                parser.error(
                    f"{flag} is for live runs; it can't be used with --dry-run"
                )
    else:
        if args.endpoint is None:
            parser.error("--endpoint is required unless --dry-run")
        if not ENDPOINT_RE.fullmatch(
            args.endpoint
        ) or not args.endpoint.lower().endswith(ENDPOINT_SUFFIX):
            parser.error(
                "--endpoint must be an AWS IoT endpoint host name in ap-southeast-1,"
                f" ending {ENDPOINT_SUFFIX} (the iot_endpoint output)"
            )
        if args.keys_dir is None:
            parser.error("--keys-dir is required for a live run (there is no default)")
        if args.instant:
            parser.error("--instant needs --dry-run")
    if args.instant and args.duration is None:
        parser.error("--instant needs --duration")
    return args


def _install_signals(stop: threading.Event) -> Callable[[], None]:
    """Ctrl+C and SIGTERM set stop; returns a function restoring the old handlers."""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    previous = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.getsignal(sig)
        signal.signal(sig, lambda signum, frame: stop.set())

    def restore() -> None:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    return restore


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    clock=None,
    epoch_ms: Callable[[], int] = now_epoch_ms,
    client_factory: ClientFactory | None = None,
    stop: threading.Event | None = None,
    new_id: Callable[[], str] | None = None,
) -> int:
    """Run the simulator. The keyword arguments are for tests: a fake clock, a fixed
    start time, a fake MQTT client factory, a stop event, and message IDs."""
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    args = parse_args(argv)

    def err(line: str) -> None:
        print(line, file=stderr, flush=True)

    try:
        seed = load_json(args.seed_file, "seed file")
        devices_doc = load_json(args.devices_file, "devices file")
        residents, device_order = build_residents(seed, devices_doc)
    except SetupError as exc:
        err(f"error: {exc}")
        return EXIT_USAGE
    selected = set(args.devices or device_order)
    unknown = [d for d in args.devices or [] if d not in device_order]
    if unknown:
        err(f"error: not in the devices file: {', '.join(unknown)}")
        return EXIT_USAGE

    keys_dir = args.keys_dir  # required for live runs; unused in a dry-run
    ca_file = args.ca_file or DEFAULT_CA_FILE
    if not args.dry_run:
        # Existence only, before connecting. Never prints paths or contents of keys.
        missing, ca_missing = missing_key_files(
            [d for d in device_order if d in selected], keys_dir, ca_file
        )
        if ca_missing:
            err("error: CA file not found (--ca-file)")
        if missing:
            err(f"error: missing certificate or key for: {', '.join(missing)}")
        if missing or ca_missing:
            return EXIT_USAGE

    seed_value = args.random_seed
    if seed_value is None:
        seed_value = random.SystemRandom().randrange(1_000_000_000)
    start_epoch = epoch_ms()
    if args.start_at is not None:
        start_routine = args.start_at
    else:
        moment = datetime.fromtimestamp(start_epoch / 1000, UTC).astimezone(SGT)
        start_routine = (
            moment.hour * routine.HOUR_MS
            + moment.minute * routine.MINUTE_MS
            + moment.second * 1000
            + moment.microsecond // 1000
        )

    engine = Engine(
        residents,
        device_order,
        selected,
        seed=seed_value,
        start_epoch_ms=start_epoch,
        start_routine_ms=start_routine,
        schedule_startup=args.dry_run,
        new_id=new_id,
    )
    if clock is None:
        clock = InstantClock() if args.instant else RealClock()
    stop = stop or threading.Event()
    reporter = Reporter()
    mode = "dry-run" if args.dry_run else "live"
    summary_stream = stderr if args.dry_run else stdout

    def summary(line: str) -> None:
        print(line, file=summary_stream, flush=True)

    summary(
        f"simulator {mode}: {len(engine.devices)} devices, random seed {seed_value},"
        f" routine {routine.format_hhmm(start_routine)}"
    )

    restore = _install_signals(stop)
    links: LinkManager | None = None
    try:
        if args.dry_run:
            sink = DryRunSink(stdout, flush=not args.instant)
            outcome = run(
                engine,
                sink,
                clock,
                reporter,
                duration_ms=args.duration,
                stop=stop,
                summary_out=summary,
                step_s=DRY_RUN_STEP_S,
            )
        else:
            factory = client_factory or paho_client_factory(keys_dir, ca_file)
            try:
                links = LinkManager(engine.devices, args.endpoint, factory, summary)
            except (OSError, ValueError) as exc:
                # e.g. an unreadable certificate; the type only, never the message.
                err(f"error: TLS setup failed ({type(exc).__name__})")
                return EXIT_USAGE
            outcome = run(
                engine,
                LiveSink(links),
                clock,
                reporter,
                duration_ms=args.duration,
                stop=stop,
                summary_out=summary,
                links=links,
                step_s=LIVE_STEP_S,
            )
    except BrokenPipeError:
        # stdout closed early (e.g. piped into head): stop quietly.
        return EXIT_OK
    finally:
        restore()
        if links is not None:
            links.close()

    elapsed = clock.elapsed_ms()
    if args.duration is not None:
        elapsed = min(elapsed, args.duration)
    if links is not None:
        connected = links.ever_connected_count()
        summary(reporter.total_line(elapsed, connected, len(engine.devices)))
        if outcome == "no_connection" or connected == 0:
            err("error: no device could connect")
            return EXIT_NO_CONNECTION
        return EXIT_OK
    summary(reporter.total_line(elapsed, len(engine.started), len(engine.devices)))
    stdout.flush()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
