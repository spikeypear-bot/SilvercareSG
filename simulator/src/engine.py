"""The device model and event schedule (criteria 7 to 15).

Each wearable has its current device alert limits (docs/features/device-alert-limits.md):
the seed file's at the start, then whatever a live run's shadow applies (set_limits).
Normal readings stay inside them (criterion 21), and vitals_alert comes from the
current readings against them (criterion 22): a reading past a limit for 10 s sends
one, then one every 5 minutes while it lasts, one timer per metric.

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
from dataclasses import dataclass, field, replace

import payloads
import routine
import scenarios
from residents import HR_RANGE, SPO2_RANGE, DeviceLimits, Resident, reading_limits
from variation import NOISE, Profile

VITALS_PERIOD_MS = routine.MINUTE_MS
STATUS_PERIOD_MS = 5 * routine.MINUTE_MS
# Start-up (dry-run) and connections (live): at most 10 devices per second.
STARTUP_SPACING_MS = 100

# Battery model (criterion 11), percent per hour.
BATTERY_DRAIN_PER_H = 1.5
BATTERY_CHARGE_PER_H = 40.0
BATTERY_START_RANGE = (60.0, 95.0)

MESSAGE_TYPES = (
    "vitals",
    "status",
    "bed",
    "motion",
    "fall",
    "vitals_alert",
    "alert_cancel",
)
_SCENARIO_KINDS = frozenset({"scenario", "cancel", "end", "vitals_alert", "fall"})
# vitals_alert metrics, in the order they are sent when due at the same moment.
ALERT_METRICS = ("hr_bpm", "spo2_pct")
# At the same elapsed time: scenario ends first, then flag scenario starts, then
# everything else (heap order is (time, seq)).
_END_SEQ_START = -(2 * 10**9)
_FLAG_SEQ_START = -(10**9)


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
    # Backfill only (docs/features/backfill-variation.md); None everywhere else.
    profile: Profile | None = None


@dataclass(order=True)
class _Event:
    elapsed_ms: int
    seq: int
    kind: str = field(compare=False)
    key: str = field(compare=False)
    # Scenario events: (scenario id,) or (name,) for a flag start.
    data: tuple = field(default=(), compare=False)


@dataclass
class _Scenario:
    """One running scenario other than im-ok (at most one per wearable)."""

    sid: int
    name: str
    device_id: str
    t0: int
    end_ms: int | None  # None for a fall: it ends when the resident gets up
    rng: random.Random
    value: int | None = None  # the current hr-spike / spo2-drop reading
    lying: tuple[float, float, float] | None = None  # fall only


@dataclass
class _Episode:
    """A metric's reading past a limit: its alert timer (criterion 22)."""

    eid: int
    next_ms: int  # when its next vitals_alert is due


@dataclass
class _WearableScenarios:
    started: int = 0  # scenarios started on this wearable (seeds their streams)
    active: _Scenario | None = None
    last_cancel_ms: int | None = None  # when the latest alert_cancel is (to be) sent


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
        profiles: dict[str, Profile] | None = None,
    ) -> None:
        residents = list(residents)
        selected_set = set(selected)
        self.devices = [d for d in device_order if d in selected_set]
        self.start_epoch_ms = start_epoch_ms
        self.start_routine_ms = start_routine_ms % routine.DAY_MS
        self._new_id = new_id or (lambda: str(uuid.uuid4()))
        self._heap: list[_Event] = []
        self._seq = 0
        self._seed = seed
        self._flag_seq = _FLAG_SEQ_START
        self._end_seq = _END_SEQ_START
        self._sid = 0
        self._scenarios: dict[str, _WearableScenarios] = {}
        self._notices: list[str] = []
        self.started: set[str] = set()
        # Each wearable's current device alert limits, and its open alert episodes.
        self._limits: dict[str, DeviceLimits] = {}
        self._episodes: dict[tuple[str, str], _Episode] = {}
        self._eid = 0
        # Profile wearables' latest non-null normal readings, held until the next one.
        self._held: dict[tuple[str, str], int] = {}
        profiles = profiles or {}

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
            profile = profiles.get(resident.resident_id)
            if profile is not None:
                phase = profile.phase(self.start_routine_ms, offset)
                motion = self._motion_with(profile.phase, self.start_routine_ms, offset)
            else:
                phase = routine.phase_at(self.start_routine_ms, offset)
                motion = routine.motion_on(self.start_routine_ms, offset)
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
                motion=motion,
                wearable_id=wearable,
                bedroom_id=bedroom,
                battery=battery,
                wearable_rng=wearable_rng,
                profile=profile,
            )
            self._states[resident.resident_id] = state
            if wearable:
                self._limits[wearable] = resident.device_limits
            for device_id in (wearable, bedroom):
                if device_id:
                    self._owner[device_id] = state

        self._schedule_all(schedule_startup)

    # ---- schedule ----

    def _push(self, elapsed_ms: int, kind: str, key: str, data: tuple = ()) -> None:
        self._seq += 1
        heapq.heappush(self._heap, _Event(elapsed_ms, self._seq, kind, key, data))

    def schedule_scenario(self, name: str, device_id: str, at_ms: int) -> None:
        """A --scenario flag: start the scenario at elapsed at_ms. Flags start in
        their command-line order, after scenario ends but before other events due
        at the same time."""
        self._flag_seq += 1
        heapq.heappush(
            self._heap,
            _Event(at_ms, self._flag_seq, "scenario", device_id, (name,)),
        )

    def _push_end(self, at_ms: int, device_id: str, sid: int) -> None:
        """A scenario's end. It runs before anything else due at the same time, so
        a scenario ending at t is over for every event at t (it covers [t0, t))."""
        self._end_seq += 1
        heapq.heappush(
            self._heap, _Event(at_ms, self._end_seq, "end", device_id, (sid,))
        )

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
            self._push(self._next_change_delay(state, 0), "routine", resident_id)

    def offset_ms(self, device_id: str) -> int:
        """The routine offset of the resident a device of this run belongs to (read
        only; the backfill places falls with it)."""
        return self._owner[device_id].offset_ms

    def phase(self, device_id: str, elapsed_ms: int) -> str:
        """The routine phase of the resident a device belongs to (read only; the
        backfill places falls and vitals episodes with it)."""
        return self._phase(self._owner[device_id], elapsed_ms)

    def _phase(self, state: _ResidentState, now_ms: int) -> str:
        if state.profile is not None:
            return state.profile.phase(self.start_routine_ms + now_ms, state.offset_ms)
        return routine.phase_at(self.routine_ms(now_ms), state.offset_ms)

    @staticmethod
    def _motion_with(phase_of: Callable[[int, int], str], t: int, offset: int) -> bool:
        """routine.motion_on for a phase function."""
        return routine.BEDROOM in (
            phase_of(t, offset),
            phase_of(t - routine.MOTION_HOLD_MS, offset),
        )

    def _motion(self, state: _ResidentState, now_ms: int) -> bool:
        if state.profile is not None:
            t = self.start_routine_ms + now_ms
            return self._motion_with(state.profile.phase, t, state.offset_ms)
        return routine.motion_on(self.routine_ms(now_ms), state.offset_ms)

    def _next_change_delay(self, state: _ResidentState, now_ms: int) -> int:
        if state.profile is not None:
            t = self.start_routine_ms + now_ms
            return state.profile.next_change_ms(t, state.offset_ms) - t
        return routine.next_change_delay_ms(self.routine_ms(now_ms), state.offset_ms)

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
        phase = self._phase(state, now_ms)
        if phase == routine.CHARGING:
            return []  # off the wrist: no vitals
        assert state.wearable_rng is not None
        limits = reading_limits(self._limits[device_id], state.resident.limits)
        # Always drawn from the normal stream, so the other fields stay normal.
        if state.profile is None:
            values = payloads.vitals_values(
                state.wearable_rng, state.resident.baseline, limits, phase
            )
        else:
            hr, spo2 = state.profile.vitals_shift(self.start_routine_ms + now_ms)
            base = state.resident.baseline
            base = replace(base, hr_bpm=base.hr_bpm + hr, spo2_pct=base.spo2_pct + spo2)
            # Not clamped to the thresholds: only to the contract's valid range.
            limits = replace(
                limits,
                hr_min=HR_RANGE[0],
                hr_max=HR_RANGE[1],
                spo2_min=SPO2_RANGE[0],
                spo2_max=SPO2_RANGE[1],
            )
            values = payloads.vitals_values(
                state.wearable_rng, base, limits, phase, noise=NOISE
            )
            for metric in ALERT_METRICS:
                if values[metric] is not None:  # a null reading changes nothing
                    self._held[(device_id, metric)] = values[metric]
        active = self._active(device_id, now_ms)
        if active is not None:
            if active.name in (scenarios.HR_SPIKE, scenarios.SPO2_DROP):
                active.value = scenarios.spike_value(active.name, active.rng)
                self._check_crossings(device_id, now_ms)
            values = scenarios.override_vitals(
                active.name, values, active.rng, active.value, active.lying
            )
        if state.profile is not None:
            self._check_crossings(device_id, now_ms)  # a held reading past a limit
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
        phase = self._phase(state, now_ms)
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
        detected = self._motion(state, now_ms)
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

    # ---- scenarios ----

    def take_notices(self) -> list[str]:
        """Scenario lines (started/ended/skipped) produced since the last call."""
        out, self._notices = self._notices, []
        return out

    def cut_off(self, until_ms: int) -> list[str]:
        """When the run stops after handling every event up to until_ms: a line for
        each scenario still going and each im-ok whose alert_cancel wasn't sent, so
        nobody thinks a fall was cancelled when it wasn't."""
        lines: list[str] = []
        for device_id in sorted(self._scenarios):
            sc = self._scenarios[device_id]
            if sc.last_cancel_ms is not None and sc.last_cancel_ms > until_ms:
                lines.append(
                    f"scenario {scenarios.IM_OK} {device_id} {scenarios.CUT_OFF}"
                )
            if sc.active is not None:
                lines.append(
                    f"scenario {sc.active.name} {device_id} {scenarios.CUT_OFF}"
                )
        return lines

    def _active(self, device_id: str, now_ms: int) -> _Scenario | None:
        """The scenario affecting the device's readings at now_ms, if any."""
        sc = self._scenarios.get(device_id)
        active = sc.active if sc else None
        if active is None:
            return None
        if active.end_ms is not None and now_ms >= active.end_ms:
            return None
        return active

    def _worn(self, device_id: str, now_ms: int) -> bool:
        return self._phase(self._owner[device_id], now_ms) != routine.CHARGING

    def start_scenario(self, name: str, device_id: str, now_ms: int) -> list[Message]:
        """Start a scenario on a wearable of this run at now_ms.

        Returns the messages due at once. Later messages and the end are scheduled.
        The lines (`scenario <name> <id> started`, `... skipped: <reason>`, `...
        ended`) come from take_notices(), in order."""
        assert name in scenarios.NAMES and device_id in self._owner
        assert device_id.startswith("wearable-")
        label = f"scenario {name} {device_id}"
        sc = self._scenarios.setdefault(device_id, _WearableScenarios())
        if name == scenarios.IM_OK:
            return self._start_im_ok(label, sc, device_id, now_ms)
        if not self._worn(device_id, now_ms):
            self._notices.append(f"{label} skipped: {scenarios.SKIP_NOT_WORN}")
            return []
        if sc.active is not None:
            self._notices.append(f"{label} skipped: {scenarios.SKIP_BUSY}")
            return []
        self._sid += 1
        rng = scenarios.scenario_rng(self._seed, device_id, sc.started)
        sc.started += 1
        if name == scenarios.FALL:
            active = _Scenario(
                self._sid,
                name,
                device_id,
                now_ms,
                None,
                rng,
                lying=scenarios.lying_orientation(rng),
            )
            self._push(
                now_ms + scenarios.FALL_MESSAGE_DELAY_MS,
                "fall",
                device_id,
                (active.sid,),
            )
            self._push_end(now_ms + scenarios.FALL_MAX_DOWN_MS, device_id, active.sid)
        else:
            end_ms = now_ms + scenarios.VITALS_DURATION_MS
            active = _Scenario(self._sid, name, device_id, now_ms, end_ms, rng)
            active.value = scenarios.spike_value(name, rng)
        sc.active = active
        # Its readings may now be past a limit: that starts the 10 s alert timer.
        self._check_crossings(device_id, now_ms)
        if active.end_ms is not None:
            self._push_end(active.end_ms, device_id, active.sid)
        self._notices.append(f"{label} started")
        return []

    def _start_im_ok(
        self, label: str, sc: _WearableScenarios, device_id: str, now_ms: int
    ) -> list[Message]:
        if (
            sc.last_cancel_ms is not None
            and now_ms < sc.last_cancel_ms + scenarios.CANCEL_SPACING_MS
        ):
            self._notices.append(f"{label} skipped: {scenarios.SKIP_TOO_SOON}")
            return []
        active = sc.active
        if active is not None and active.name == scenarios.FALL:
            fall_at = active.t0 + scenarios.FALL_MESSAGE_DELAY_MS
            if now_ms < fall_at + scenarios.CANCEL_SPACING_MS:
                # The fall message isn't out yet, or went out less than 5 s ago.
                cancel_at = fall_at + scenarios.CANCEL_SPACING_MS
                sc.last_cancel_ms = cancel_at
                self._push(cancel_at, "cancel", device_id, (active.sid,))
                self._notices.append(f"{label} started (after the fall message)")
                return []
        sc.last_cancel_ms = now_ms
        self._notices.append(f"{label} started")
        return self._send_cancel(device_id, now_ms)

    def _send_cancel(self, device_id: str, now_ms: int) -> list[Message]:
        """The alert_cancel; it ends an active fall (the resident gets up)."""
        out = [self._msg(now_ms, device_id, "alert_cancel", payloads.alert_cancel)]
        sc = self._scenarios[device_id]
        if sc.active is not None and sc.active.name == scenarios.FALL:
            self._end(sc, now_ms)
        self._notices.append(f"scenario {scenarios.IM_OK} {device_id} ended")
        return out

    def _end(self, sc: _WearableScenarios, now_ms: int) -> None:
        assert sc.active is not None
        device_id = sc.active.device_id
        self._notices.append(f"scenario {sc.active.name} {device_id} ended")
        sc.active = None
        self._check_crossings(device_id, now_ms)  # back inside: the episode ends

    def _current(self, event: _Event) -> tuple[_WearableScenarios, _Scenario] | None:
        """The wearable's scenario if it is still the one the event belongs to."""
        sc = self._scenarios.get(event.key)
        if sc is None or sc.active is None or sc.active.sid != event.data[0]:
            return None
        return sc, sc.active

    # ---- device alert limits and vitals_alert (criteria 20 to 22) ----

    def limits(self, device_id: str) -> DeviceLimits:
        """A wearable's current device alert limits."""
        return self._limits[device_id]

    def set_limits(self, device_id: str, limits: DeviceLimits, now_ms: int) -> bool:
        """Apply new device limits at now_ms (from the shadow). They apply at once to
        readings and open episodes. Returns whether they differ from the current."""
        changed = self._limits[device_id] != limits
        self._limits[device_id] = limits
        self._check_crossings(device_id, now_ms)
        return changed

    def _reading(self, device_id: str, metric: str, now_ms: int) -> int | None:
        """The wearable's current reading of metric if a scenario has moved it, else
        a profile wearable's held normal reading, else None (a normal reading, inside
        the limits by criterion 21). No pulse is 0."""
        active = self._active(device_id, now_ms)
        if active is not None:
            if active.name == scenarios.NO_PULSE:
                return 0 if metric == "hr_bpm" else None
            if active.name == scenarios.HR_SPIKE and metric == "hr_bpm":
                return active.value
            if active.name == scenarios.SPO2_DROP and metric == "spo2_pct":
                return active.value
        return self._held.get((device_id, metric))

    def _crossed(
        self, device_id: str, metric: str, now_ms: int
    ) -> tuple[int, int, str] | None:
        """(value, limit crossed, direction) if the reading is past a limit."""
        value = self._reading(device_id, metric, now_ms)
        if value is None:
            return None
        limits = self._limits[device_id]
        if metric == "hr_bpm":
            if value < limits.hr_min:
                return value, limits.hr_min, "below"
            if value > limits.hr_max:
                return value, limits.hr_max, "above"
            return None
        if value < limits.spo2_min:
            return value, limits.spo2_min, "below"
        return None

    def _check_crossings(self, device_id: str, now_ms: int) -> None:
        """Start a 10 s timer for each metric newly past a limit; end the episode of
        each metric back inside."""
        for metric in ALERT_METRICS:
            key = (device_id, metric)
            crossed = self._crossed(device_id, metric, now_ms) is not None
            if crossed and key not in self._episodes:
                self._eid += 1
                due = now_ms + scenarios.ALERT_DELAY_MS
                self._episodes[key] = _Episode(self._eid, due)
                self._push(due, "vitals_alert", device_id, (metric, self._eid))
            elif not crossed and key in self._episodes:
                del self._episodes[key]

    def _vitals_alerts(self, event: _Event) -> list[Message]:
        """Every vitals_alert of the wearable due now, hr_bpm first; each then
        repeats every 5 minutes while its episode lasts."""
        now, device_id = event.elapsed_ms, event.key
        metric, eid = event.data
        episode = self._episodes.get((device_id, metric))
        if episode is None or episode.eid != eid or episode.next_ms != now:
            return []  # the episode ended, or this was sent with a tied metric
        out: list[Message] = []
        worn = self._worn(device_id, now)
        for name in ALERT_METRICS:
            due = self._episodes.get((device_id, name))
            if due is None or due.next_ms != now:
                continue
            due.next_ms = now + scenarios.ALERT_REPEAT_MS
            self._push(due.next_ms, "vitals_alert", device_id, (name, due.eid))
            crossed = self._crossed(device_id, name, now)
            if not worn or crossed is None:
                continue  # sent only while worn
            value, limit, direction = crossed
            out.append(
                self._msg(
                    now,
                    device_id,
                    "vitals_alert",
                    lambda ts, mid, m=name, v=value, lim=limit, d=direction: (
                        payloads.vitals_alert(
                            ts, mid, m, v, lim, d, scenarios.ALERT_HELD_S
                        )
                    ),
                )
            )
        return out

    def _fall(self, now_ms: int, active: _Scenario) -> list[Message]:
        assert active.lying is not None
        values = scenarios.fall_values(active.rng, active.lying)
        payload = payloads.fall(
            self.epoch_ms(active.t0),  # ts is the impact
            self._new_id(),
            values.confidence,
            values.peak_g,
            scenarios.FALL_STILL_S,
            values.samples,
        )
        return [Message(now_ms, active.device_id, "fall", payload)]

    def _handle_scenario(self, event: _Event) -> list[Message]:
        now = event.elapsed_ms
        if event.kind == "scenario":
            return self.start_scenario(event.data[0], event.key, now)
        if event.kind == "cancel":
            return self._send_cancel(event.key, now)
        if event.kind == "vitals_alert":
            return self._vitals_alerts(event)
        current = self._current(event)
        if current is None:
            return []  # the scenario has ended since
        sc, active = current
        if event.kind == "end":
            self._end(sc, now)
            return []
        if event.kind == "fall":
            return self._fall(now, active)
        raise AssertionError(f"unknown event kind {event.kind}")  # pragma: no cover

    def _handle(self, event: _Event) -> list[Message]:
        now = event.elapsed_ms
        if event.kind in _SCENARIO_KINDS:
            return self._handle_scenario(event)
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
            self._push(now + self._next_change_delay(state, now), "routine", event.key)
            return self._routine_change(now, state)
        raise AssertionError(f"unknown event kind {event.kind}")  # pragma: no cover
