"""The device model and event schedule (criteria 7 to 15).

Pure and deterministic: no clock, no network, no files. Time is "elapsed" integer
milliseconds since the start of the run. The engine knows the real UTC time at
elapsed 0 (for `ts`) and the routine time of day at elapsed 0 (for the routine), so
`ts` is always real time and never the routine clock.
"""

from __future__ import annotations

import heapq
import random
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import payloads
import routine
from residents import Resident

VITALS_PERIOD_MS = routine.MINUTE_MS
STATUS_PERIOD_MS = 5 * routine.MINUTE_MS
# Start-up (dry-run) and connections (live): at most 10 devices per second.
STARTUP_SPACING_MS = 100

# Battery model (criterion 11), percent per hour.
BATTERY_DRAIN_PER_H = 1.5
BATTERY_CHARGE_PER_H = 40.0
BATTERY_START_RANGE = (60.0, 95.0)

MESSAGE_TYPES = ("vitals", "status", "bed", "motion")


@dataclass(frozen=True)
class Message:
    elapsed_ms: int
    device_id: str
    message: str
    payload: dict

    @property
    def topic(self) -> str:
        return payloads.topic(self.device_id, self.message)


@dataclass
class Battery:
    pct: float
    charging: bool
    last_ms: int = 0

    def advance(self, now_ms: int) -> None:
        hours = max(0, now_ms - self.last_ms) / routine.HOUR_MS
        rate = BATTERY_CHARGE_PER_H if self.charging else -BATTERY_DRAIN_PER_H
        self.pct = max(0.0, min(100.0, self.pct + rate * hours))
        self.last_ms = max(self.last_ms, now_ms)

    def published(self) -> int:
        return max(0, min(100, round(self.pct)))


@dataclass
class _ResidentState:
    resident: Resident
    offset_ms: int
    worn: bool
    charging: bool
    occupied: bool
    motion: bool
    # The resident's devices that are part of this run (None if not selected).
    wearable_id: str | None
    bedroom_id: str | None
    battery: Battery | None
    wearable_rng: random.Random | None


@dataclass(order=True)
class _Event:
    elapsed_ms: int
    seq: int
    kind: str = field(compare=False)
    key: str = field(compare=False)


def device_rng(seed: int, device_id: str) -> random.Random:
    """A separate stream per device, so the same seed and --start-at give the same
    values whatever the other devices do."""
    return random.Random(f"{seed}:{device_id}")


def offsets_rng(seed: int) -> random.Random:
    return random.Random(f"{seed}:offsets")


class Engine:
    """Generates every message of the selected devices in time order."""

    def __init__(
        self,
        residents: Iterable[Resident],
        device_order: list[str],
        selected: Iterable[str],
        *,
        seed: int,
        start_epoch_ms: int,
        start_routine_ms: int,
        schedule_startup: bool = True,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        residents = list(residents)
        selected_set = set(selected)
        self.devices = [d for d in device_order if d in selected_set]
        self.start_epoch_ms = start_epoch_ms
        self.start_routine_ms = start_routine_ms % routine.DAY_MS
        self._new_id = new_id or (lambda: str(uuid.uuid4()))
        self._heap: list[_Event] = []
        self._seq = 0
        self.started: set[str] = set()

        # Offsets for every resident (not only selected ones), so the same seed gives
        # the same offsets whichever devices run.
        offsets = routine.draw_offsets(
            [r.resident_id for r in residents], offsets_rng(seed)
        )
        self._owner: dict[str, _ResidentState] = {}
        self._states: dict[str, _ResidentState] = {}
        for resident in residents:
            wearable = (
                resident.wearable_id if resident.wearable_id in selected_set else None
            )
            bedroom = (
                resident.bedroom_id if resident.bedroom_id in selected_set else None
            )
            if wearable is None and bedroom is None:
                continue
            offset = offsets[resident.resident_id]
            phase = routine.phase_at(self.start_routine_ms, offset)
            worn, charging = routine.wearable_state(phase)
            wearable_rng = device_rng(seed, wearable) if wearable else None
            battery = None
            if wearable_rng is not None:
                battery = Battery(wearable_rng.uniform(*BATTERY_START_RANGE), charging)
            state = _ResidentState(
                resident=resident,
                offset_ms=offset,
                worn=worn,
                charging=charging,
                occupied=routine.bed_occupied(phase),
                motion=routine.motion_on(self.start_routine_ms, offset),
                wearable_id=wearable,
                bedroom_id=bedroom,
                battery=battery,
                wearable_rng=wearable_rng,
            )
            self._states[resident.resident_id] = state
            for device_id in (wearable, bedroom):
                if device_id:
                    self._owner[device_id] = state

        self._schedule_all(schedule_startup)

    # ---- schedule ----

    def _push(self, elapsed_ms: int, kind: str, key: str) -> None:
        self._seq += 1
        heapq.heappush(self._heap, _Event(elapsed_ms, self._seq, kind, key))

    @staticmethod
    def _first_slot(period_ms: int, slot_ms: int, after_ms: int) -> int:
        """The first time k * period + slot that is strictly after after_ms."""
        k = max(0, (after_ms - slot_ms) // period_ms + 1)
        return k * period_ms + slot_ms

    def _schedule_all(self, schedule_startup: bool) -> None:
        wearables = [d for d in self.devices if d.startswith("wearable-")]
        vitals_spacing = VITALS_PERIOD_MS // len(wearables) if wearables else 0
        status_spacing = STATUS_PERIOD_MS // len(self.devices) if self.devices else 0
        for index, device_id in enumerate(self.devices):
            startup_ms = index * STARTUP_SPACING_MS
            if schedule_startup:
                self._push(startup_ms, "startup", device_id)
            self._push(
                self._first_slot(STATUS_PERIOD_MS, index * status_spacing, startup_ms),
                "status",
                device_id,
            )
        for index, device_id in enumerate(wearables):
            startup_ms = self.devices.index(device_id) * STARTUP_SPACING_MS
            self._push(
                self._first_slot(VITALS_PERIOD_MS, index * vitals_spacing, startup_ms),
                "vitals",
                device_id,
            )
        for resident_id, state in self._states.items():
            delay = routine.next_change_delay_ms(self.start_routine_ms, state.offset_ms)
            self._push(delay, "routine", resident_id)

    def next_time_ms(self) -> int | None:
        return self._heap[0].elapsed_ms if self._heap else None

    def pop_due(self, until_ms: int) -> list[Message]:
        """Every message due at or before until_ms, in time order."""
        out: list[Message] = []
        while self._heap and self._heap[0].elapsed_ms <= until_ms:
            event = heapq.heappop(self._heap)
            out.extend(self._handle(event))
        return out

    # ---- time ----

    def routine_ms(self, elapsed_ms: int) -> int:
        """The shared routine clock (time of day, no resident offset)."""
        return (self.start_routine_ms + elapsed_ms) % routine.DAY_MS

    def epoch_ms(self, elapsed_ms: int) -> int:
        """The real UTC time of an elapsed time (used for ts)."""
        return self.start_epoch_ms + elapsed_ms

    # ---- messages ----

    def _msg(
        self, now_ms: int, device_id: str, message: str, build: Callable
    ) -> Message:
        payload = build(self.epoch_ms(now_ms), self._new_id())
        return Message(now_ms, device_id, message, payload)

    def _wearable_status(self, now_ms: int, device_id: str) -> Message:
        state = self._owner[device_id]
        assert state.battery is not None
        state.battery.advance(now_ms)
        level = state.battery.published()
        return self._msg(
            now_ms,
            device_id,
            "status",
            lambda ts, mid: payloads.wearable_status(
                ts, mid, level, state.worn, state.charging
            ),
        )

    def _status(self, now_ms: int, device_id: str) -> Message:
        if device_id.startswith("wearable-"):
            return self._wearable_status(now_ms, device_id)
        return self._msg(now_ms, device_id, "status", payloads.bedroom_status)

    def startup_messages(self, device_id: str, now_ms: int) -> list[Message]:
        """status, and for a bedroom sensor its current bed and motion state."""
        self.started.add(device_id)
        out = [self._status(now_ms, device_id)]
        if device_id.startswith("bedroom-"):
            state = self._owner[device_id]
            out.append(
                self._msg(
                    now_ms,
                    device_id,
                    "bed",
                    lambda ts, mid: payloads.bed(ts, mid, state.occupied),
                )
            )
            out.append(
                self._msg(
                    now_ms,
                    device_id,
                    "motion",
                    lambda ts, mid: payloads.motion(ts, mid, state.motion),
                )
            )
        return out

    def _vitals(self, now_ms: int, device_id: str) -> list[Message]:
        state = self._owner[device_id]
        phase = routine.phase_at(self.routine_ms(now_ms), state.offset_ms)
        if phase == routine.CHARGING:
            return []  # off the wrist: no vitals
        assert state.wearable_rng is not None
        values = payloads.vitals_values(
            state.wearable_rng, state.resident.baseline, state.resident.limits, phase
        )
        return [
            self._msg(
                now_ms,
                device_id,
                "vitals",
                lambda ts, mid: payloads.vitals(ts, mid, values),
            )
        ]

    def _routine_change(self, now_ms: int, state: _ResidentState) -> list[Message]:
        """Publish whatever changed at one of the resident's change points."""
        out: list[Message] = []
        routine_now = self.routine_ms(now_ms)
        phase = routine.phase_at(routine_now, state.offset_ms)
        wearable, bedroom = state.wearable_id, state.bedroom_id

        if state.battery is not None:
            state.battery.advance(now_ms)  # at the old charging rate
        worn, charging = routine.wearable_state(phase)
        if (worn, charging) != (state.worn, state.charging):
            state.worn, state.charging = worn, charging
            if state.battery is not None:
                state.battery.charging = charging
            if wearable:
                out.append(self._wearable_status(now_ms, wearable))

        occupied = routine.bed_occupied(phase)
        if occupied != state.occupied:
            state.occupied = occupied
            if bedroom:
                out.append(
                    self._msg(
                        now_ms,
                        bedroom,
                        "bed",
                        lambda ts, mid: payloads.bed(ts, mid, occupied),
                    )
                )
        detected = routine.motion_on(routine_now, state.offset_ms)
        if detected != state.motion:
            state.motion = detected
            if bedroom:
                out.append(
                    self._msg(
                        now_ms,
                        bedroom,
                        "motion",
                        lambda ts, mid: payloads.motion(ts, mid, detected),
                    )
                )
        return out

    def _handle(self, event: _Event) -> list[Message]:
        now = event.elapsed_ms
        if event.kind == "startup":
            return self.startup_messages(event.key, now)
        if event.kind == "status":
            self._push(now + STATUS_PERIOD_MS, "status", event.key)
            return [self._status(now, event.key)]
        if event.kind == "vitals":
            self._push(now + VITALS_PERIOD_MS, "vitals", event.key)
            return self._vitals(now, event.key)
        if event.kind == "routine":
            state = self._states[event.key]
            delay = routine.next_change_delay_ms(self.routine_ms(now), state.offset_ms)
            self._push(now + delay, "routine", event.key)
            return self._routine_change(now, state)
        raise AssertionError(f"unknown event kind {event.kind}")  # pragma: no cover
