"""Validate an IoT rule event against docs/metrics.md (schema_version 1).

Pure functions, no AWS calls. `validate_event` returns either a `ValidMessage` or a
`Rejection` with a short reason code (e.g. `out_of_range:hr_bpm`). Reason codes name
the field, never its value.

Type rules (docs/features/ingest-live.md, criterion 12):
- integer fields accept Python ints only (not booleans, not floats such as 72.0)
- number fields accept ints and floats (finite), not booleans
- nullable fields also accept None (JSON null)
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1

DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-[0-9]{4}\Z")
UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

# Messages each device type may send (docs/metrics.md, Summary).
ALLOWED_MESSAGES: dict[str, frozenset[str]] = {
    "wearable": frozenset({"vitals", "fall", "status"}),
    "bedroom": frozenset({"motion", "bed", "status"}),
}

ACCEL_AXIS_LIMIT = 16
FALL_WINDOW_HZ = 25
FALL_WINDOW_START_OFFSET_MS = -1000
FALL_WINDOW_SAMPLES = 50
FW_VERSION_MAX_LEN = 32


@dataclass(frozen=True)
class Rejection:
    reason: str


@dataclass(frozen=True)
class ValidMessage:
    device_id: str
    device_type: str
    message: str
    message_id: str
    ts: str
    ts_dt: datetime
    received_at_ms: int
    fields: dict[str, Any]  # validated, stored-by-allow-list fields only


# ---- field type checks ----


def _is_int(value: Any) -> bool:
    return type(value) is int


def _is_number(value: Any) -> bool:
    if type(value) is int:
        return True
    return type(value) is float and math.isfinite(value)


def _is_bool(value: Any) -> bool:
    return type(value) is bool


@dataclass(frozen=True)
class Field:
    """One required payload field: its kind, whether null is allowed, and its range."""

    kind: str  # "int" | "number" | "bool" | "str"
    nullable: bool = False
    minimum: float | None = None
    maximum: float | None = None
    max_len: int | None = None


_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "int": _is_int,
    "number": _is_number,
    "bool": _is_bool,
    "str": lambda v: type(v) is str,
}


def _check_field(name: str, spec: Field, payload: dict) -> str | None:
    """Return a reason code if the field is missing, mistyped, or out of range."""
    if name not in payload:
        return f"missing:{name}"
    value = payload[name]
    if value is None:
        return None if spec.nullable else f"wrong_type:{name}"
    if not _TYPE_CHECKS[spec.kind](value):
        return f"wrong_type:{name}"
    if spec.minimum is not None and value < spec.minimum:
        return f"out_of_range:{name}"
    if spec.maximum is not None and value > spec.maximum:
        return f"out_of_range:{name}"
    if spec.max_len is not None and not (1 <= len(value) <= spec.max_len):
        return f"out_of_range:{name}"
    return None


# Required, stored fields per (device type, message), from docs/metrics.md. The stored
# allow-list in docs/data-model.md ("Stored fields per type") is exactly these keys.
_FW_VERSION = Field("str", max_len=FW_VERSION_MAX_LEN)

STORED_FIELDS: dict[tuple[str, str], dict[str, Field]] = {
    ("wearable", "vitals"): {
        "hr_bpm": Field("int", nullable=True, minimum=20, maximum=250),
        "spo2_pct": Field("int", nullable=True, minimum=50, maximum=100),
        "skin_temp_c": Field("number", nullable=True, minimum=20.0, maximum=45.0),
        "activity_pct": Field("int", minimum=0, maximum=100),
    },
    ("wearable", "fall"): {
        "confidence": Field("number", minimum=0.0, maximum=1.0),
        "peak_g": Field("number", minimum=0, maximum=16),
        "still_s": Field("int", minimum=0, maximum=600),
    },
    ("wearable", "status"): {
        "battery_pct": Field("int", minimum=0, maximum=100),
        "worn": Field("bool"),
        "charging": Field("bool"),
        "fw_version": _FW_VERSION,
    },
    ("bedroom", "motion"): {"motion": Field("bool")},
    ("bedroom", "bed"): {"occupied": Field("bool")},
    ("bedroom", "status"): {
        "battery_pct": Field("int", nullable=True, minimum=0, maximum=100),
        "fw_version": _FW_VERSION,
    },
}


# ---- validated but never stored ----


def _is_axis(value: Any) -> bool:
    return _is_number(value) and -ACCEL_AXIS_LIMIT <= value <= ACCEL_AXIS_LIMIT


def _check_accel_g(payload: dict) -> str | None:
    if "accel_g" not in payload:
        return "missing:accel_g"
    accel = payload["accel_g"]
    if not isinstance(accel, dict):
        return "invalid:accel_g"
    if not all(axis in accel and _is_axis(accel[axis]) for axis in ("x", "y", "z")):
        return "invalid:accel_g"
    return None


def _check_accel_window(payload: dict) -> str | None:
    if "accel_window" not in payload:
        return "missing:accel_window"
    window = payload["accel_window"]
    if not isinstance(window, dict):
        return "invalid:accel_window"
    hz = window.get("hz")
    offset = window.get("start_offset_ms")
    samples = window.get("samples")
    if not (_is_int(hz) and hz == FALL_WINDOW_HZ):
        return "invalid:accel_window"
    if not (_is_int(offset) and offset == FALL_WINDOW_START_OFFSET_MS):
        return "invalid:accel_window"
    if not (isinstance(samples, list) and len(samples) == FALL_WINDOW_SAMPLES):
        return "invalid:accel_window"
    for sample in samples:
        if not (
            isinstance(sample, list)
            and len(sample) == 3
            and all(_is_axis(v) for v in sample)
        ):
            return "invalid:accel_window"
    return None


EXTRA_CHECKS: dict[tuple[str, str], Callable[[dict], str | None]] = {
    ("wearable", "vitals"): _check_accel_g,
    ("wearable", "fall"): _check_accel_window,
}


# ---- envelope ----


def parse_ts(value: Any) -> datetime | None:
    """Return the UTC datetime for a contract `ts` string, or None if invalid."""
    if type(value) is not str or not TS_RE.match(value):
        return None
    try:
        return datetime.strptime(value, TS_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def validate_event(
    event: Any, handled_messages: frozenset[str]
) -> ValidMessage | Rejection:
    """Check one rule event. `handled_messages` are the types this function stores."""
    if not isinstance(event, dict):
        return Rejection("invalid:event")

    device_id = event.get("device_id")
    if type(device_id) is not str or not DEVICE_ID_RE.match(device_id):
        return Rejection("invalid:device_id")
    device_type = device_id.split("-", 1)[0]

    message = event.get("message")
    if type(message) is not str or message not in ALLOWED_MESSAGES[device_type]:
        return Rejection("invalid:message")
    if message not in handled_messages:
        return Rejection("wrong_function")

    if "schema_version" not in event:
        return Rejection("missing:schema_version")
    if not (
        _is_int(event["schema_version"]) and event["schema_version"] == SCHEMA_VERSION
    ):
        return Rejection("invalid:schema_version")

    if "message_id" not in event:
        return Rejection("missing:message_id")
    message_id = event["message_id"]
    if type(message_id) is not str or not UUID_V4_RE.match(message_id):
        return Rejection("invalid:message_id")

    if "ts" not in event:
        return Rejection("missing:ts")
    ts_dt = parse_ts(event["ts"])
    if ts_dt is None:
        return Rejection("invalid:ts")

    received_at = event.get("received_at")
    if not (_is_int(received_at) and received_at >= 0):
        return Rejection("invalid:received_at")

    key = (device_type, message)
    field_specs = STORED_FIELDS[key]
    for name, spec in field_specs.items():
        reason = _check_field(name, spec, event)
        if reason:
            return Rejection(reason)
    extra_check = EXTRA_CHECKS.get(key)
    if extra_check:
        reason = extra_check(event)
        if reason:
            return Rejection(reason)

    # Unknown fields, accel_g, and accel_window are dropped here (criteria 13, 18).
    return ValidMessage(
        device_id=device_id,
        device_type=device_type,
        message=message,
        message_id=message_id,
        ts=event["ts"],
        ts_dt=ts_dt,
        received_at_ms=received_at,
        fields={name: event[name] for name in field_specs},
    )
