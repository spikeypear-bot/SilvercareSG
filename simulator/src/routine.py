"""The shared daily routine (docs/features/simulator-core.md, criteria 7 and 8).

All times are integer milliseconds so phase boundaries are exact. "Routine time" is a
time of day in Singapore time (UTC+8) on the routine clock: `--start-at` (or the real
Singapore time) plus the real time elapsed. Each resident follows the routine shifted
by their own offset.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable

SECOND_MS = 1000
MINUTE_MS = 60 * SECOND_MS
HOUR_MS = 60 * MINUTE_MS
DAY_MS = 24 * HOUR_MS

ASLEEP = "asleep"
BEDROOM = "bedroom"
OUT = "out"
CHARGING = "charging"

# (start, end, phase) in routine time of day; together they cover the whole day.
ROUTINE: tuple[tuple[int, int, str], ...] = (
    (0, 7 * HOUR_MS, ASLEEP),
    (7 * HOUR_MS, 8 * HOUR_MS, BEDROOM),
    (8 * HOUR_MS, 19 * HOUR_MS, OUT),
    (19 * HOUR_MS, 20 * HOUR_MS, CHARGING),
    (20 * HOUR_MS, 21 * HOUR_MS, OUT),
    (21 * HOUR_MS, 22 * HOUR_MS, BEDROOM),
    (22 * HOUR_MS, DAY_MS, ASLEEP),
)
# Times of day where the phase changes (22:00-07:00 is one asleep phase).
BOUNDARIES_MS: tuple[int, ...] = (
    7 * HOUR_MS,
    8 * HOUR_MS,
    19 * HOUR_MS,
    20 * HOUR_MS,
    21 * HOUR_MS,
    22 * HOUR_MS,
)
# The PIR clears motion 2 minutes after the last movement.
MOTION_HOLD_MS = 2 * MINUTE_MS
# Each resident's offset is drawn uniformly in this range (whole seconds).
MAX_OFFSET_S = 30 * 60

HHMM_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")


def parse_hhmm(text: str) -> int:
    """'HH:MM' (00:00-23:59) -> milliseconds since midnight. Raises ValueError."""
    match = HHMM_RE.fullmatch(text)
    if not match:
        raise ValueError("time must be HH:MM, 00:00 to 23:59")
    return int(match.group(1)) * HOUR_MS + int(match.group(2)) * MINUTE_MS


def format_hhmm(time_of_day_ms: int) -> str:
    minutes = (time_of_day_ms % DAY_MS) // MINUTE_MS
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def base_phase(time_of_day_ms: int) -> str:
    """The shared routine's phase at a time of day (no offset)."""
    t = time_of_day_ms % DAY_MS
    for start, end, phase in ROUTINE:
        if start <= t < end:
            return phase
    raise AssertionError("routine table does not cover the day")  # pragma: no cover


def phase_at(routine_ms: int, offset_ms: int) -> str:
    """A resident's phase at a routine time of day, with their offset applied to every
    boundary (a +10 min offset means they go to sleep at 22:10)."""
    return base_phase(routine_ms - offset_ms)


def motion_on(routine_ms: int, offset_ms: int) -> bool:
    """The PIR state: on during a bedroom phase and for the hold time after it ends."""
    return (
        phase_at(routine_ms, offset_ms) == BEDROOM
        or phase_at(routine_ms - MOTION_HOLD_MS, offset_ms) == BEDROOM
    )


def wearable_state(phase: str) -> tuple[bool, bool]:
    """(worn, charging) for a phase."""
    charging = phase == CHARGING
    return (not charging, charging)


def bed_occupied(phase: str) -> bool:
    return phase == ASLEEP


def bedroom_ends_ms() -> list[int]:
    """Times of day where a bedroom phase ends."""
    return [end for _, end, phase in ROUTINE if phase == BEDROOM]


def change_points_ms(offset_ms: int) -> list[int]:
    """Every time of day where one of the resident's device states can change:
    the phase boundaries and the motion-clear times, shifted by the offset."""
    points = list(BOUNDARIES_MS) + [end + MOTION_HOLD_MS for end in bedroom_ends_ms()]
    return sorted({(p + offset_ms) % DAY_MS for p in points})


def next_change_delay_ms(routine_ms: int, offset_ms: int) -> int:
    """Milliseconds from a routine time to the resident's next change point (> 0)."""
    now = routine_ms % DAY_MS
    delays = [(p - now) % DAY_MS or DAY_MS for p in change_points_ms(offset_ms)]
    return min(delays)


def draw_offsets(resident_ids: Iterable[str], rng: random.Random) -> dict[str, int]:
    """One offset per resident, uniform in -30..+30 minutes (whole seconds), in ms.

    A draw is redrawn if any of its change points falls in the same second as another
    resident's, so two residents never change state at the same moment (which keeps
    steady-state publishes at most 4 per second).
    """
    taken: set[int] = set()
    offsets: dict[str, int] = {}
    for resident_id in sorted(resident_ids):
        while True:
            offset_ms = rng.randint(-MAX_OFFSET_S, MAX_OFFSET_S) * SECOND_MS
            points = set(change_points_ms(offset_ms))
            if not points & taken:
                break
        taken |= points
        offsets[resident_id] = offset_ms
    return offsets
