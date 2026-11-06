"""Counts and the per-minute summary line (criterion 18). Counts and IDs only, never
payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone

import routine
from engine import MESSAGE_TYPES

SGT = timezone(timedelta(hours=8))

SENT = "sent"
DROPPED = "dropped"
FAILED = "failed"


@dataclass
class Counts:
    sent: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(MESSAGE_TYPES, 0)
    )
    dropped: int = 0
    failed: int = 0

    def add(self, outcome: str, message: str) -> None:
        if outcome == SENT:
            self.sent[message] = self.sent.get(message, 0) + 1
        elif outcome == DROPPED:
            self.dropped += 1
        else:
            self.failed += 1

    def text(self) -> str:
        sent = " ".join(f"{m} {self.sent.get(m, 0)}" for m in MESSAGE_TYPES)
        return f"sent {sent} | dropped {self.dropped} | failed {self.failed}"


def sgt_hhmm(epoch_ms: int) -> str:
    return (
        datetime.fromtimestamp(epoch_ms / 1000, UTC).astimezone(SGT).strftime("%H:%M")
    )


def format_elapsed(elapsed_ms: int) -> str:
    seconds = elapsed_ms // 1000
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class Reporter:
    """Counts outcomes per minute and in total, and formats the summary lines."""

    def __init__(self) -> None:
        self.minute = Counts()
        self.total = Counts()
        self.next_summary_ms = routine.MINUTE_MS

    def record(self, outcome: str, message: str) -> None:
        self.minute.add(outcome, message)
        self.total.add(outcome, message)

    def summary_line(
        self, epoch_ms: int, routine_ms: int, connected: int, devices: int
    ) -> str:
        """One minute's line, e.g. `12:03 SGT routine 21:58 | connected 40/40 |
        sent vitals 20 status 8 bed 0 motion 3 | dropped 0 | failed 0`; then resets
        the minute's counts."""
        line = (
            f"{sgt_hhmm(epoch_ms)} SGT routine {routine.format_hhmm(routine_ms)}"
            f" | connected {connected}/{devices} | {self.minute.text()}"
        )
        self.minute = Counts()
        self.next_summary_ms += routine.MINUTE_MS
        return line

    def total_line(self, elapsed_ms: int, connected: int, devices: int) -> str:
        return (
            f"total {format_elapsed(elapsed_ms)} | connected {connected}/{devices}"
            f" | {self.total.text()}"
        )
