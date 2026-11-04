"""Shared fixtures for the simulator tests (docs/features/simulator-core.md, 21).

The simulator's modules are flat files in simulator/src/, so that folder goes on
sys.path. No test opens a network connection, reads secrets/, or runs a live
simulation: live runs use the fake MQTT client in fakes.py and a fake clock.
"""

from __future__ import annotations

import copy
import io
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SRC_DIR = TESTS_DIR.parent / "src"
REPO_ROOT = TESTS_DIR.parents[1]
SEED_FILE = REPO_ROOT / "scripts" / "seed-data.json"
DEVICES_FILE = REPO_ROOT / "infra" / "devices" / "devices.json"

for path in (str(SRC_DIR), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

# 2026-11-03T04:00:00.250Z = 12:00:00.250 SGT. The 250 ms makes sure nothing assumes
# the start time is on a whole second.
START_EPOCH_MS = (
    int(datetime(2026, 11, 3, 4, 0, 0, tzinfo=UTC).timestamp() * 1000) + 250
)
HOUR_MS = 3_600_000
MINUTE_MS = 60_000
DAY_MS = 24 * HOUR_MS

# The 24 h run every day-level test shares: routine 03:00, real time 12:00 SGT, so the
# routine clock and ts are 9 hours apart.
DAY_SEED = 7
DAY_START_AT = "03:00"


def load_doc(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def seed_doc() -> dict:
    return load_doc(SEED_FILE)


@pytest.fixture(scope="session")
def devices_doc() -> dict:
    return load_doc(DEVICES_FILE)


@pytest.fixture
def seed_copy(seed_doc) -> dict:
    return copy.deepcopy(seed_doc)


@pytest.fixture
def devices_copy(devices_doc) -> dict:
    return copy.deepcopy(devices_doc)


@dataclass
class DryRun:
    code: int
    stdout: str
    stderr: str
    lines: list[dict]  # parsed stdout JSON lines

    def elapsed_ms(self, line: dict) -> int:
        return ts_to_epoch_ms(line["payload"]["ts"]) - START_EPOCH_MS


def ts_to_epoch_ms(ts: str) -> int:
    moment = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    return round(moment.timestamp() * 1000)


def run_dry(argv: list[str], **kwargs) -> DryRun:
    """main.main with captured output and a fixed real start time."""
    import main

    out, err = io.StringIO(), io.StringIO()
    kwargs.setdefault("epoch_ms", lambda: START_EPOCH_MS)
    code = main.main(argv, stdout=out, stderr=err, **kwargs)
    text = out.getvalue()
    lines = [json.loads(line) for line in text.splitlines() if line]
    return DryRun(code, text, err.getvalue(), lines)


@pytest.fixture(scope="session")
def day_run() -> DryRun:
    """A 24 h --instant dry-run for all 40 devices."""
    result = run_dry(
        [
            "--dry-run",
            "--instant",
            "--duration",
            "24h",
            "--random-seed",
            str(DAY_SEED),
            "--start-at",
            DAY_START_AT,
        ]
    )
    assert result.code == 0
    return result


@pytest.fixture(scope="session")
def by_device(day_run) -> dict[str, list[tuple[int, dict]]]:
    """device_id -> [(elapsed_ms, line)] in output order."""
    out: dict[str, list[tuple[int, dict]]] = {}
    for line in day_run.lines:
        device_id = line["topic"].split("/")[2]
        out.setdefault(device_id, []).append((day_run.elapsed_ms(line), line))
    return out


def message_of(line: dict) -> str:
    return line["topic"].split("/")[3]
