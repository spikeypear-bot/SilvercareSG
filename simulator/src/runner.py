"""The run loop shared by dry-run and live runs (criteria 15 and 17 to 20)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import TextIO

import payloads
from engine import Engine, Message
from live import LinkManager
from report import SENT, Reporter

# How long the real-time loop sleeps at most between checks (live runs also poll
# their connections this often).
LIVE_STEP_S = 0.05
DRY_RUN_STEP_S = 0.25


class DryRunSink:
    """Prints one JSON line per message: {"topic": ..., "payload": {...}}."""

    def __init__(self, out: TextIO, flush: bool) -> None:
        self._out = out
        self._flush = flush

    def publish(self, message: Message) -> str:
        payloads.encode(message.payload)  # same size check as a live run
        line = json.dumps({"topic": message.topic, "payload": message.payload})
        self._out.write(line + "\n")
        if self._flush:
            self._out.flush()
        return SENT


class LiveSink:
    """Publishes over the device's own MQTT connection; never prints the payload."""

    def __init__(self, links: LinkManager) -> None:
        self._links = links

    def publish(self, message: Message) -> str:
        return self._links.publish(
            message.device_id, message.topic, payloads.encode(message.payload)
        )


def run(
    engine: Engine,
    sink: DryRunSink | LiveSink,
    clock,
    reporter: Reporter,
    *,
    duration_ms: int | None,
    stop: threading.Event,
    summary_out: Callable[[str], None],
    links: LinkManager | None = None,
    step_s: float = DRY_RUN_STEP_S,
) -> str:
    """Run until stop, the end of duration, or (live) no device could connect.

    Returns "done" or "no_connection". Messages due at elapsed times in
    [0, duration) are generated; summaries go out every minute of elapsed time.
    """
    devices = len(engine.devices)

    def connected() -> int:
        return links.connected_count() if links else len(engine.started)

    def deliver(message: Message) -> None:
        reporter.record(sink.publish(message), message.message)

    while not stop.is_set():
        now = clock.elapsed_ms()
        if duration_ms is not None:
            now = min(now, duration_ms)
        if links is not None:
            for device_id in links.poll(now):
                for message in engine.startup_messages(device_id, now):
                    deliver(message)
            if links.none_could_connect():
                return "no_connection"
        due_until = now if duration_ms is None else min(now, duration_ms - 1)
        for message in engine.pop_due(due_until):
            deliver(message)
        while now >= reporter.next_summary_ms:
            at = reporter.next_summary_ms
            summary_out(
                reporter.summary_line(
                    engine.epoch_ms(at), engine.routine_ms(at), connected(), devices
                )
            )
        if duration_ms is not None and now >= duration_ms:
            break
        targets = [reporter.next_summary_ms]
        next_event = engine.next_time_ms()
        if next_event is not None:
            targets.append(next_event)
        if duration_ms is not None:
            targets.append(duration_ms)
        if links is not None:
            # Poll the connections at least every step, also with a fake clock.
            targets.append(now + max(1, int(step_s * 1000)))
        clock.wait_until(min(targets), step_s)
    return "done"
