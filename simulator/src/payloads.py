"""Message payloads exactly as docs/metrics.md defines them (criteria 9 to 14).

Payloads never hold device_id, message, or received_at: the IoT rules add those.
"""

from __future__ import annotations

import json
import math
import random
from datetime import UTC, datetime

from residents import Baseline, Limits
from routine import ASLEEP, BEDROOM, OUT

SCHEMA_VERSION = 1
FW_VERSION = "1.0.0"
MAX_PAYLOAD_BYTES = 8 * 1024

# Per-phase vitals model (criterion 10).
HR_PHASE_DELTA = {ASLEEP: -8, BEDROOM: 0, OUT: 10}
HR_NOISE = 4
SPO2_NOISE = 1
TEMP_NOISE_TENTHS = 4  # +-0.4 degC
TEMP_ASLEEP_TENTHS = 3  # +0.3 degC asleep
ACTIVITY_RANGE = {ASLEEP: (0, 5), BEDROOM: (5, 30), OUT: (20, 70)}
ACCEL_MAGNITUDE = (0.91, 1.09)  # inside 0.9-1.1 g even after rounding to 3 decimals
NULL_RATE = 0.02
SPO2_NULL_RATE_OUT = 0.05


def topic(device_id: str, message: str) -> str:
    return f"silvercare/v1/{device_id}/{message}"


def format_ts(epoch_ms: int) -> str:
    """ISO 8601 UTC with milliseconds and Z, e.g. 2026-10-18T03:15:00.000Z."""
    moment = datetime.fromtimestamp(epoch_ms // 1000, UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{epoch_ms % 1000:03d}Z"


def envelope(epoch_ms: int, message_id: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "message_id": message_id,
        "ts": format_ts(epoch_ms),
    }


def encode(payload: dict) -> bytes:
    """Compact JSON bytes for MQTT. Raises ValueError over the contract's 8 KB."""
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(data) >= MAX_PAYLOAD_BYTES:
        raise ValueError("payload over 8 KB")
    return data


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _accel(rng: random.Random) -> dict:
    """One sample: gravity in a random wrist orientation (mostly face up), with a
    magnitude in 0.9-1.1 g."""
    x, y, z = rng.gauss(0, 0.3), rng.gauss(0, 0.3), 1.0
    norm = math.sqrt(x * x + y * y + z * z)
    magnitude = rng.uniform(*ACCEL_MAGNITUDE)
    return {
        "x": round(x / norm * magnitude, 3),
        "y": round(y / norm * magnitude, 3),
        "z": round(z / norm * magnitude, 3),
    }


def vitals_values(
    rng: random.Random, baseline: Baseline, limits: Limits, phase: str
) -> dict:
    """The vitals fields for one reading while worn (phase asleep, bedroom, or out).

    Every value stays inside the contract's valid range and the resident's
    thresholds narrowed by the margins (residents.Limits)."""
    hr = baseline.hr_bpm + HR_PHASE_DELTA[phase] + rng.randint(-HR_NOISE, HR_NOISE)
    spo2 = baseline.spo2_pct + rng.randint(-SPO2_NOISE, SPO2_NOISE)
    temp = baseline.skin_temp_tenths + rng.randint(
        -TEMP_NOISE_TENTHS, TEMP_NOISE_TENTHS
    )
    if phase == ASLEEP:
        temp += TEMP_ASLEEP_TENTHS
    low, high = ACTIVITY_RANGE[phase]
    activity = rng.randint(low, high)
    accel = _accel(rng)
    hr_null = rng.random() < NULL_RATE
    temp_null = rng.random() < NULL_RATE
    spo2_null = rng.random() < (SPO2_NULL_RATE_OUT if phase == OUT else NULL_RATE)
    temp_tenths = _clamp(temp, limits.temp_min_tenths, limits.temp_max_tenths)
    return {
        "hr_bpm": None if hr_null else _clamp(hr, limits.hr_min, limits.hr_max),
        "spo2_pct": None
        if spo2_null
        else _clamp(spo2, limits.spo2_min, limits.spo2_max),
        "skin_temp_c": None if temp_null else temp_tenths / 10,
        "activity_pct": activity,
        "accel_g": accel,
    }


def vitals(epoch_ms: int, message_id: str, values: dict) -> dict:
    return {**envelope(epoch_ms, message_id), **values}


def wearable_status(
    epoch_ms: int, message_id: str, battery_pct: int, worn: bool, charging: bool
) -> dict:
    return {
        **envelope(epoch_ms, message_id),
        "battery_pct": battery_pct,
        "worn": worn,
        "charging": charging,
        "fw_version": FW_VERSION,
    }


def bedroom_status(epoch_ms: int, message_id: str) -> dict:
    # Mains powered: battery_pct is null.
    return {
        **envelope(epoch_ms, message_id),
        "battery_pct": None,
        "fw_version": FW_VERSION,
    }


def bed(epoch_ms: int, message_id: str, occupied: bool) -> dict:
    return {**envelope(epoch_ms, message_id), "occupied": occupied}


def motion(epoch_ms: int, message_id: str, detected: bool) -> dict:
    return {**envelope(epoch_ms, message_id), "motion": detected}
