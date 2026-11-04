"""Helpers for the alert scenario tests (docs/features/simulator-alert-scenarios.md).

play() drives the Engine directly, like the run loop does with an instant clock, and
records every message with its send time and every scenario line with the time it was
produced. Typed commands are handled after the events due at the same time, as in
runner.run.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import routine
from conftest import DEVICES_FILE, SEED_FILE, START_EPOCH_MS, load_doc
from engine import Engine
from residents import build_residents

SECOND_MS = 1000
MINUTE_MS = 60_000
SEED = 11
START_AT = "12:00"  # everyone is out and wearing their wearable (+-30 min offsets)


def id_counter():
    """Fixed, valid UUID v4 message IDs."""
    numbers = itertools.count(1)
    return lambda: f"00000000-0000-4000-8000-{next(numbers):012d}"


@dataclass
class Played:
    messages: list  # engine.Message, in send order
    notices: list[tuple[int, str]]  # (elapsed_ms, line)

    def of(self, device_id: str, message: str | None = None) -> list:
        return [
            m
            for m in self.messages
            if m.device_id == device_id and (message is None or m.message == message)
        ]

    def lines(self) -> list[str]:
        return [line for _, line in self.notices]


def make_engine(
    flags=(), *, seed=SEED, start_at=START_AT, devices=None, new_id=None
) -> Engine:
    residents, order = build_residents(load_doc(SEED_FILE), load_doc(DEVICES_FILE))
    engine = Engine(
        residents,
        order,
        devices or order,
        seed=seed,
        start_epoch_ms=START_EPOCH_MS,
        start_routine_ms=routine.parse_hhmm(start_at),
        schedule_startup=True,
        new_id=new_id or id_counter(),
    )
    for name, device_id, at_ms in flags:
        engine.schedule_scenario(name, device_id, at_ms)
    return engine


def play(engine: Engine, until_ms: int, typed=()) -> Played:
    """Every message due in [0, until_ms). typed: [(at_ms, name, device_id)]."""
    pending = sorted(typed)
    messages, notices = [], []
    while True:
        times = [t for t in (engine.next_time_ms(),) if t is not None]
        if pending:
            times.append(pending[0][0])
        times = [t for t in times if t < until_ms]
        if not times:
            break
        now = min(times)
        messages.extend(engine.pop_due(now))
        notices.extend((now, line) for line in engine.take_notices())
        while pending and pending[0][0] == now:
            _, name, device_id = pending.pop(0)
            messages.extend(engine.start_scenario(name, device_id, now))
            notices.extend((now, line) for line in engine.take_notices())
    return Played(messages, notices)


def run_engine(flags=(), until_ms=15 * MINUTE_MS, typed=(), **kwargs) -> Played:
    return play(make_engine(flags, **kwargs), until_ms, typed)


def strip_id(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k != "message_id"}


def keyed(played: Played, device_id: str | None = None) -> dict:
    """(elapsed, device, message, n) -> payload without message_id."""
    out = {}
    seen: dict = {}
    for m in played.messages:
        if device_id is not None and m.device_id != device_id:
            continue
        key = (m.elapsed_ms, m.device_id, m.message)
        n = seen.get(key, 0)
        seen[key] = n + 1
        out[(*key, n)] = strip_id(m.payload)
    return out


def magnitude(sample) -> float:
    return math.sqrt(sum(v * v for v in sample))


def accel_list(vitals_payload: dict) -> list[float]:
    accel = vitals_payload["accel_g"]
    return [accel["x"], accel["y"], accel["z"]]
