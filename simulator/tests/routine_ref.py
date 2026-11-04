"""An independent copy of the routine table (criterion 7) for checking the simulator's
output, plus helpers to recover each resident's offset from that output."""

from __future__ import annotations

HOUR_MS = 3_600_000
MINUTE_MS = 60_000
DAY_MS = 24 * HOUR_MS

TABLE = (
    (0, 7, "asleep"),
    (7, 8, "bedroom"),
    (8, 19, "out"),
    (19, 20, "charging"),
    (20, 21, "out"),
    (21, 22, "bedroom"),
    (22, 24, "asleep"),
)


def phase(routine_ms: int, offset_ms: int) -> str:
    t = (routine_ms - offset_ms) % DAY_MS
    for start_h, end_h, name in TABLE:
        if start_h * HOUR_MS <= t < end_h * HOUR_MS:
            return name
    raise AssertionError("unreachable")


def hhmm_ms(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * HOUR_MS + int(minutes) * MINUTE_MS


def elapsed_of(time_of_day_ms: int, offset_ms: int, start_routine_ms: int) -> int:
    """Elapsed time (in [0, 1 day)) at which a resident reaches a routine time."""
    return (time_of_day_ms + offset_ms - start_routine_ms) % DAY_MS


def signed(ms: int) -> int:
    """A time difference normalised to [-12 h, 12 h)."""
    return (ms + DAY_MS // 2) % DAY_MS - DAY_MS // 2


# (routine time of day, message, value) of every state change a resident's devices
# report, from the routine table and the PIR hold time.
BED_CHANGES = ((22 * HOUR_MS, True), (7 * HOUR_MS, False))
MOTION_CHANGES = (
    (7 * HOUR_MS, True),
    (21 * HOUR_MS, True),
    (8 * HOUR_MS + 2 * MINUTE_MS, False),
    (22 * HOUR_MS + 2 * MINUTE_MS, False),
)
WEAR_CHANGES = ((19 * HOUR_MS, (False, True)), (20 * HOUR_MS, (True, False)))


def expected_changes(
    changes, offset_ms: int, start_routine_ms: int, duration_ms: int
) -> set:
    out = set()
    for tod, value in changes:
        e = elapsed_of(tod, offset_ms, start_routine_ms)
        while e < duration_ms:
            if e > 0:
                out.add((e, value))
            e += DAY_MS
    return out
