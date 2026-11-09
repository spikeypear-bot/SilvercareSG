"""Unusual residents for the backfill (docs/features/backfill-variation.md).

A Profile gives one resident wider noise, a linear decline, bad days, and nightly
bedtime and wake-time shifts. The engine uses it only when the backfill passes one in,
so the live simulator and --dry-run never change.

Times are "routine ms": the engine's start routine time of day plus elapsed ms, so
day k is [k * DAY_MS, (k + 1) * DAY_MS) of Singapore days on the routine clock.

Pure: no clock, no network, no files.
"""

from __future__ import annotations

import bisect
from functools import lru_cache

import routine
from routine import ASLEEP, BEDROOM, CHARGING, DAY_MS, HOUR_MS, MINUTE_MS, OUT

UNUSUAL_RESIDENTS = ("res-0003", "res-0008", "res-0014", "res-0019")

# (HR, SpO2, skin temperature in tenths), uniform +-, instead of 4 / 1 / 4.
NOISE = (12, 3, 8)
# Reached linearly by the end of the range.
DECLINE_HR = 8
DECLINE_SPO2 = -2
BAD_DAY_RATE = 0.1
BAD_DAY_HR = 15
BAD_DAY_SPO2 = -3
SLEEP_SHIFT_MIN = 60  # bedtime and wake time each move by -60..+60 whole minutes


class Profile:
    def __init__(
        self,
        first_ms: int,
        stop_ms: int,
        bad_days: frozenset[int],
        bedtime_shift_ms: dict[int, int],
        wake_shift_ms: dict[int, int],
    ) -> None:
        """first_ms/stop_ms: the range's start and stop (the decline goes from 0 to
        full between them). Days missing from the shift maps aren't shifted."""
        self.first_ms = first_ms
        self.stop_ms = stop_ms
        self.bad_days = bad_days
        self.bedtime_shift_ms = bedtime_shift_ms
        self.wake_shift_ms = wake_shift_ms
        self._day = lru_cache(maxsize=8)(self._day_segments)

    def vitals_shift(self, routine_abs_ms: int) -> tuple[int, int]:
        """(HR, SpO2) added to the baseline at this time: decline plus bad day."""
        span = self.stop_ms - self.first_ms
        f = min(1.0, max(0.0, (routine_abs_ms - self.first_ms) / span))
        hr, spo2 = round(DECLINE_HR * f), round(DECLINE_SPO2 * f)
        if routine_abs_ms // DAY_MS in self.bad_days:
            hr, spo2 = hr + BAD_DAY_HR, spo2 + BAD_DAY_SPO2
        return hr, spo2

    def _day_segments(self, day: int, offset_ms: int) -> tuple[list[int], list[str]]:
        """Segment starts (ms in the day) and phases. With no shifts this is the
        routine table with the offset applied (routine.phase_at)."""
        w = self.wake_shift_ms.get(day, 0)
        b = self.bedtime_shift_ms.get(day, 0)
        o = offset_ms
        segments = [
            (0, ASLEEP),
            (7 * HOUR_MS + o + w, BEDROOM),
            (8 * HOUR_MS + o + w, OUT),
            (19 * HOUR_MS + o, CHARGING),
            (20 * HOUR_MS + o, OUT),  # may be 0 long when bedtime is 1 h early
            (21 * HOUR_MS + o + b, BEDROOM),
            (22 * HOUR_MS + o + b, ASLEEP),
        ]
        return [s for s, _ in segments], [p for _, p in segments]

    def phase(self, routine_abs_ms: int, offset_ms: int) -> str:
        starts, phases = self._day(routine_abs_ms // DAY_MS, offset_ms)
        return phases[bisect.bisect_right(starts, routine_abs_ms % DAY_MS) - 1]

    def next_change_ms(self, routine_abs_ms: int, offset_ms: int) -> int:
        """The next routine ms (> routine_abs_ms) where a device state can change:
        a segment start (not midnight) or a bedroom end plus the motion hold."""
        day = routine_abs_ms // DAY_MS
        points = []
        for k in (day, day + 1):
            starts, phases = self._day(k, offset_ms)
            base = k * DAY_MS
            points += [base + s for s in starts[1:]]
            points += [
                base + starts[i + 1] + routine.MOTION_HOLD_MS
                for i, p in enumerate(phases)
                if p == BEDROOM
            ]
        return min(p for p in points if p > routine_abs_ms)


def sleep_shift_ms(rng) -> int:
    return rng.randint(-SLEEP_SHIFT_MIN, SLEEP_SHIFT_MIN) * MINUTE_MS
