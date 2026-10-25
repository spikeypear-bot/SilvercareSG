"""Builders for fake IoT rule events (docs/metrics.md examples + the rule's fields).

Every event is a device payload plus what the topic rule SELECT adds:
`topic(3) AS device_id, topic(4) AS message, timestamp() AS received_at`.
All IDs and values are synthetic.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from typing import Any

WEARABLE = "wearable-0001"
BEDROOM = "bedroom-0001"

BASE_TS = "2026-10-18T03:12:00.000Z"

FALL_SAMPLES = [[0.01, -0.1, 0.99]] * 50

PAYLOADS: dict[str, dict[str, Any]] = {
    "vitals": {
        "hr_bpm": 72,
        "spo2_pct": 97,
        "skin_temp_c": 33.4,
        "activity_pct": 12,
        "accel_g": {"x": 0.02, "y": -0.11, "z": 0.99},
    },
    "fall": {
        "confidence": 0.91,
        "peak_g": 3.8,
        "still_s": 20,
        "accel_window": {
            "hz": 25,
            "start_offset_ms": -1000,
            "samples": FALL_SAMPLES,
        },
    },
    "wearable_status": {
        "battery_pct": 64,
        "worn": True,
        "charging": False,
        "fw_version": "1.0.0",
    },
    "motion": {"motion": True},
    "bed": {"occupied": False},
    "bedroom_status": {"battery_pct": 88, "fw_version": "1.0.0"},
}

# Stored allow-list per (device type, message), docs/data-model.md.
STORED_FIELDS = {
    ("wearable", "vitals"): {"hr_bpm", "spo2_pct", "skin_temp_c", "activity_pct"},
    ("wearable", "fall"): {"confidence", "peak_g", "still_s"},
    ("wearable", "status"): {"battery_pct", "worn", "charging", "fw_version"},
    ("bedroom", "motion"): {"motion"},
    ("bedroom", "bed"): {"occupied"},
    ("bedroom", "status"): {"battery_pct", "fw_version"},
}


def iso_to_epoch_ms(ts: str) -> int:
    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def new_message_id() -> str:
    return str(uuid.uuid4())


def rule_event(message: str, device_id: str | None = None, **overrides: Any) -> dict:
    """A valid rule event for `message`, with optional field overrides.

    Pass a field as `_DELETE` to remove it.
    """
    if device_id is None:
        device_id = BEDROOM if message in ("motion", "bed") else WEARABLE
    device_type = device_id.split("-", 1)[0]
    key = f"{device_type}_status" if message == "status" else message
    ts = overrides.get("ts", BASE_TS)
    event: dict[str, Any] = {
        "schema_version": 1,
        "message_id": new_message_id(),
        "ts": BASE_TS,
        **copy.deepcopy(PAYLOADS[key]),
        "device_id": device_id,
        "message": message,
        # The rule received it 410 ms after the device's ts (no clock skew).
        "received_at": iso_to_epoch_ms(ts if _valid_ts(ts) else BASE_TS) + 410,
    }
    for name, value in overrides.items():
        if value is _DELETE:
            event.pop(name, None)
        else:
            event[name] = value
    return event


def _valid_ts(ts: Any) -> bool:
    try:
        iso_to_epoch_ms(ts)
    except (TypeError, ValueError):
        return False
    return True


class _Delete:
    def __repr__(self) -> str:
        return "DELETE"


_DELETE = _Delete()
DELETE = _DELETE


def ts_at(minute: int, second: int = 0, millis: int = 0) -> str:
    """A ts on the base day at 03:<minute>:<second>.<millis>Z."""
    return f"2026-10-18T03:{minute:02d}:{second:02d}.{millis:03d}Z"
