"""Clocks for the run loop: real time, or instant (dry-run --instant and tests).

Both measure integer milliseconds elapsed since the run started.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class RealClock:
    def __init__(
        self,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._monotonic = monotonic
        self._sleep = sleep
        self._start = monotonic()

    def elapsed_ms(self) -> int:
        return int((self._monotonic() - self._start) * 1000)

    def wait_until(self, target_ms: int, max_step_s: float) -> None:
        """Sleep until target_ms, but at most max_step_s, so the loop can poll."""
        remaining_s = (target_ms - self.elapsed_ms()) / 1000
        if remaining_s > 0:
            self._sleep(min(remaining_s, max_step_s))


class InstantClock:
    """Jumps straight to the next due time instead of sleeping."""

    def __init__(self) -> None:
        self._now_ms = 0

    def elapsed_ms(self) -> int:
        return self._now_ms

    def wait_until(self, target_ms: int, max_step_s: float) -> None:
        self._now_ms = max(self._now_ms, target_ms)


def now_epoch_ms() -> int:
    """The real current UTC time in epoch milliseconds."""
    return time.time_ns() // 1_000_000
