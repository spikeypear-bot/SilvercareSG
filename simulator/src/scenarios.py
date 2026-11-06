"""Alert scenarios (docs/features/simulator-alert-scenarios.md).

Parsing of `--scenario` flags and typed commands, the checks on the target wearable,
and the scenario values (vitals overrides, the fall's acceleration window). Pure: no
clock, no network, no files. The engine owns every scenario's state and schedule.

Every scenario draws its values from its own random.Random, never from the device's
normal stream, so a run without scenarios is unchanged and the normal fields of a
reading stay what they would have been.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass

import payloads
import routine
from residents import DEVICE_ID_RE

FALL = "fall"
HR_SPIKE = "hr-spike"
NO_PULSE = "no-pulse"
SPO2_DROP = "spo2-drop"
IM_OK = "im-ok"
NAMES = (FALL, HR_SPIKE, NO_PULSE, SPO2_DROP, IM_OK)
VITALS_SCENARIOS = (HR_SPIKE, NO_PULSE, SPO2_DROP)

# Default flag offset: every device has connected by then (criterion 2).
DEFAULT_OFFSET_MS = 30 * routine.SECOND_MS

VITALS_DURATION_MS = 3 * routine.MINUTE_MS
FALL_MAX_DOWN_MS = 10 * routine.MINUTE_MS
FALL_MESSAGE_DELAY_MS = 20 * routine.SECOND_MS
FALL_STILL_S = 20
ALERT_DELAY_MS = 10 * routine.SECOND_MS
ALERT_HELD_S = 10
ALERT_REPEAT_MS = 5 * routine.MINUTE_MS
# The wearable accepts "I'm OK" presses at least 5 s apart, and a cancel is sent at
# least 5 s after the fall message it cancels.
CANCEL_SPACING_MS = 5 * routine.SECOND_MS

# Values past every seeded personal limit and the device limits (spec, Alerts).
HR_SPIKE_RANGE = (150, 155)
SPO2_DROP_RANGE = (82, 84)
FALL_CONFIDENCE_RANGE = (0.85, 0.95)
FALL_PEAK_G_RANGE = (3.0, 4.5)
FALL_ACTIVITY_RANGE = (0, 3)
# The fall's acceleration window: 1 s before to 1 s after the impact at 25 Hz.
FALL_SAMPLES_BEFORE = 25  # the impact is the next sample (offset 0 ms)
FALL_SAMPLES_AFTER = 24

# (metric, limit, direction) of the scenario's vitals_alert.
ALERT_RULE = {
    HR_SPIKE: ("hr_bpm", 130, "above"),
    SPO2_DROP: ("spo2_pct", 88, "below"),
    NO_PULSE: ("hr_bpm", 45, "below"),
}

SKIP_NOT_WORN = "not worn"
SKIP_BUSY = "busy"
SKIP_TOO_SOON = "too soon"
CUT_OFF = "cut off: run ended"

FLAG_RE = re.compile(r"^(?P<name>[^:@\s]+):(?P<device>[^:@\s]+)(?:@(?P<offset>\S+))?$")
WEARABLE_RE = re.compile(r"^wearable-[0-9]{4}$")

HELP_TEXT = (
    "scenarios: " + ", ".join(NAMES) + " | type <name> <wearable_id>,"
    " e.g. fall wearable-0005"
)


@dataclass(frozen=True)
class Request:
    """A scenario to start: name, target wearable, and (flags) the elapsed offset."""

    name: str
    device_id: str
    offset_ms: int = DEFAULT_OFFSET_MS


def _name_problem(name: str) -> str | None:
    if name not in NAMES:
        return "unknown scenario name (use " + ", ".join(NAMES) + ")"
    return None


def parse_flag(
    text: str, parse_offset: Callable[[str], int], duration_ms: int | None
) -> Request:
    """`<name>:<wearable_id>[@<offset>]` -> Request. Raises ValueError with the
    problem (never the value itself)."""
    match = FLAG_RE.fullmatch(text)
    if not match or not DEVICE_ID_RE.fullmatch(match["device"]):
        raise ValueError(
            "must be <name>:<wearable_id>[@<offset>], e.g. fall:wearable-0005@2m"
        )
    problem = _name_problem(match["name"])
    if problem:
        raise ValueError(problem)
    offset_ms = DEFAULT_OFFSET_MS
    if match["offset"] is not None:
        try:
            offset_ms = parse_offset(match["offset"])
        except ValueError:
            raise ValueError(
                "offset must be a positive number with s, m, or h, e.g. 90s"
            ) from None
    if duration_ms is not None and offset_ms >= duration_ms:
        raise ValueError("offset is at or past --duration")
    return Request(match["name"], match["device"], offset_ms)


def target_problem(
    device_id: str, known: Collection[str], in_run: Collection[str]
) -> str | None:
    """Why device_id can't be a scenario target, or None if it can."""
    if not WEARABLE_RE.fullmatch(device_id):
        return (
            f"{device_id} is not a wearable"
            if DEVICE_ID_RE.fullmatch(device_id)
            else "not a wearable ID"
        )
    if device_id not in known:
        return f"{device_id} is not in the devices file"
    if device_id not in in_run:
        return f"{device_id} is not in this run (--devices)"
    return None


def parse_command(line: str) -> Request | str | None:
    """A typed line -> Request, "help", or None for a blank line. Raises ValueError
    with the problem (never the line itself)."""
    parts = line.split()
    if not parts:
        return None
    if parts == ["help"]:
        return "help"
    if len(parts) != 2:
        raise ValueError("type <name> <wearable_id>, or help")
    name, device_id = parts
    problem = _name_problem(name)
    if problem:
        raise ValueError(problem)
    if not DEVICE_ID_RE.fullmatch(device_id):
        raise ValueError("not a wearable ID (e.g. wearable-0005)")
    return Request(name, device_id, 0)


# ---- values ----


def scenario_rng(seed: int, device_id: str, number: int) -> random.Random:
    """The scenario's own stream: the n-th scenario on that wearable in this run."""
    return random.Random(f"{seed}:{device_id}:scenario:{number}")


def _scaled(x: float, y: float, z: float, magnitude: float) -> list[float]:
    norm = math.sqrt(x * x + y * y + z * z) or 1.0
    return [round(v / norm * magnitude, 3) for v in (x, y, z)]


def upright_sample(rng: random.Random) -> list[float]:
    """About 1 g, mostly on z (the wrist face up): a normal reading's sample."""
    return list(payloads.accel_sample(rng).values())


def lying_sample(rng: random.Random, lying: tuple[float, float, float]) -> list[float]:
    """About 1 g in the lying orientation (mostly on x), with a little noise."""
    x, y, z = lying
    return _scaled(
        x,
        y + rng.gauss(0, 0.05),
        z + rng.gauss(0, 0.05),
        rng.uniform(*payloads.ACCEL_MAGNITUDE),
    )


def lying_orientation(rng: random.Random) -> tuple[float, float, float]:
    return (rng.choice((-1.0, 1.0)), rng.uniform(-0.25, 0.25), rng.uniform(-0.25, 0.25))


@dataclass
class FallValues:
    confidence: float
    peak_g: float
    samples: list[list[float]]


def fall_values(rng: random.Random, lying: tuple[float, float, float]) -> FallValues:
    """The fall message's values: 25 upright samples, the impact at index 25 with
    magnitude peak_g, then lying samples. Every axis is within -16..16."""
    confidence = round(rng.uniform(*FALL_CONFIDENCE_RANGE), 2)
    peak_g = round(rng.uniform(*FALL_PEAK_G_RANGE), 2)
    samples = [upright_sample(rng) for _ in range(FALL_SAMPLES_BEFORE)]
    samples.append(_scaled(rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1), peak_g))
    samples.extend(lying_sample(rng, lying) for _ in range(FALL_SAMPLES_AFTER))
    return FallValues(confidence, peak_g, samples)


def spike_value(name: str, rng: random.Random) -> int | None:
    """A new reading of the scenario's metric (None for no-pulse and fall)."""
    if name == HR_SPIKE:
        return rng.randint(*HR_SPIKE_RANGE)
    if name == SPO2_DROP:
        return rng.randint(*SPO2_DROP_RANGE)
    return None


def override_vitals(
    name: str,
    values: dict,
    rng: random.Random,
    value: int | None,
    lying: tuple[float, float, float] | None,
) -> dict:
    """The normal vitals fields with the scenario's metric replaced (no clamp)."""
    out = dict(values)
    if name == HR_SPIKE:
        out["hr_bpm"] = value
    elif name == SPO2_DROP:
        out["spo2_pct"] = value
    elif name == NO_PULSE:
        # vitals accepts 20-250 bpm only, so "no pulse" is null here.
        out["hr_bpm"] = None
        out["spo2_pct"] = None
        out["activity_pct"] = 0
    elif name == FALL:
        assert lying is not None
        out["activity_pct"] = rng.randint(*FALL_ACTIVITY_RANGE)
        x, y, z = lying_sample(rng, lying)
        out["accel_g"] = {"x": x, "y": y, "z": z}
    return out
