"""The run loop shared by dry-run and live runs (criteria 15 and 17 to 20).

A live run also applies each wearable's shadow messages to the engine
(docs/features/device-alert-limits.md, criterion 20); a dry-run keeps the seed file's
limits."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import TextIO

import live
import payloads
import scenarios
from commands import CommandSource
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


def handle_command(
    engine: Engine,
    line: str,
    now_ms: int,
    known: set[str],
    deliver: Callable[[Message], None],
    err_out: Callable[[str], None],
) -> None:
    """One typed line: start a scenario at now_ms (its lines come from
    engine.take_notices()), print help, or print one error line (naming the problem
    only, never the typed text)."""
    try:
        request = scenarios.parse_command(line)
    except ValueError as exc:
        err_out(f"error: command: {exc}")
        return
    if request is None:
        return
    if request == "help":
        err_out(scenarios.HELP_TEXT)
        return
    assert isinstance(request, scenarios.Request)
    problem = scenarios.target_problem(request.device_id, known, engine.devices)
    if problem:
        err_out(f"error: command: {problem}")
        return
    for message in engine.start_scenario(request.name, request.device_id, now_ms):
        deliver(message)


def handle_shadow(
    engine: Engine,
    links: LinkManager,
    now_ms: int,
    log: Callable[[str], None],
) -> None:
    """Apply the shadow messages received since the last call (criterion 20). Lines
    name the device only, never limit values. Bedroom sensors ignore the content."""
    for device_id, suffix, payload in links.take_shadow_messages():
        if not device_id.startswith("wearable-"):
            continue
        action, limits = live.shadow_limits(suffix, payload, engine.limits(device_id))
        if action == live.REJECT:
            log(f"{device_id}: limits rejected")
            continue
        if action == live.IGNORE or limits is None:
            continue
        if action == live.APPLY and engine.set_limits(device_id, limits, now_ms):
            log(f"{device_id}: limits updated")
        result = links.publish(
            device_id,
            live.shadow_topic(device_id, live.SHADOW_UPDATE),
            live.reported_limits_payload(limits),
        )
        if result != "sent":
            log(f"{device_id}: limits report {result}")


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
    commands: CommandSource | None = None,
    known_devices: set[str] | None = None,
    err_out: Callable[[str], None] | None = None,
) -> str:
    """Run until stop, the end of duration, or (live) no device could connect.

    Returns "done" or "no_connection". Messages due at elapsed times in
    [0, duration) are generated; summaries go out every minute of elapsed time.
    Scenario lines go to summary_out. Typed commands (if a command source is given)
    start scenarios at the current elapsed time; their errors go to err_out.
    """
    known = known_devices if known_devices is not None else set(engine.devices)
    err = err_out or summary_out
    devices = len(engine.devices)

    def connected() -> int:
        return links.connected_count() if links else len(engine.started)

    def deliver(message: Message) -> None:
        reporter.record(sink.publish(message), message.message)

    due_until = -1  # every event up to here has been handled
    while not stop.is_set():
        now = clock.elapsed_ms()
        if duration_ms is not None:
            now = min(now, duration_ms)
        if links is not None:
            for device_id in links.poll(now):
                for message in engine.startup_messages(device_id, now):
                    deliver(message)
            handle_shadow(engine, links, now, summary_out)
            if links.none_could_connect():
                return "no_connection"
        due_until = now if duration_ms is None else min(now, duration_ms - 1)
        for message in engine.pop_due(due_until):
            deliver(message)
        for line in engine.take_notices():
            summary_out(line)
        if commands is not None and (duration_ms is None or now < duration_ms):
            for typed in commands.poll(now):
                handle_command(engine, typed, now, known, deliver, err)
                for line in engine.take_notices():
                    summary_out(line)
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
        if links is not None or commands is not None:
            # Poll the connections and typed commands at least every step, also
            # with a fake clock.
            targets.append(now + max(1, int(step_s * 1000)))
        clock.wait_until(min(targets), step_s)
    # --duration or Ctrl+C: say which scenarios didn't finish (e.g. an im-ok whose
    # alert_cancel was never sent).
    for line in engine.cut_off(due_until):
        summary_out(line)
    return "done"
