"""Typed scenario commands (docs/features/simulator-alert-scenarios.md, criterion 3).

A command source has `poll(now_ms) -> list[str]`: the lines typed since the last
call. StdinCommands reads lines on a daemon thread into a queue; the thread never
touches the engine. Only the run loop calls poll and starts scenarios.
"""

from __future__ import annotations

import queue
import threading
from typing import Protocol, TextIO


class CommandSource(Protocol):
    def poll(self, now_ms: int) -> list[str]: ...


class StdinCommands:
    """Reads lines from a terminal on a background thread."""

    def __init__(self, stream: TextIO) -> None:
        self._lines: queue.Queue[str] = queue.Queue()
        self._stream = stream
        self._thread = threading.Thread(
            target=self._read, name="simulator-commands", daemon=True
        )
        self._thread.start()

    def _read(self) -> None:
        try:
            for line in self._stream:
                self._lines.put(line)
        except (OSError, ValueError):
            return  # stdin closed: no more commands

    def poll(self, now_ms: int) -> list[str]:
        out: list[str] = []
        while True:
            try:
                out.append(self._lines.get_nowait())
            except queue.Empty:
                return out
