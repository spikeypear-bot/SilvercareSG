"""Validate an alert message from the alerts queue (docs/features/alerts-core.md,
criteria 1-3 and 15; docs/features/vitals-alerts.md, criteria 1-4).

The message is built by lambdas/ingest/src/alerts.py; this is alert-processor's own
copy of the schema (it is built from a different zip). Pure functions, no AWS calls.

`parse_alert` returns either an `Alert` or a `Rejection` with a short reason code that
names the field, never its value. Floats are parsed as `Decimal`, so `0.91` reaches
DynamoDB as exactly `0.91`. Unknown fields are rejected.

Alert types and severities:

    fall                                  critical
    hr_high, hr_low, spo2_low             warning (cloud check) or critical (device)
    skin_temp_high, skin_temp_low         warning (the device sends no skin temperature)

Vitals `details` are {metric, value, limit, direction} plus `previous_value` for a
warning or `held_s` for a critical alert. `metric` and `direction` must match the
alert type; `value`, `limit`, and `previous_value` must be in the metric's valid range
from docs/metrics.md (integers for hr_bpm and spo2_pct), except that a critical
`hr_low` may have `value` 0 (no pulse); `held_s` is an integer 10-600.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

SCHEMA_VERSION = 1
UNASSIGNED = "unassigned"

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-[0-9]{4}\Z")
RESIDENT_ID_RE = re.compile(r"^(res-[0-9]{4}|unassigned)\Z")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

FIELDS = (
    "schema_version",
    "alert_id",
    "alert_type",
    "severity",
    "resident_id",
    "device_id",
    "event_ts",
    "received_at",
    "source_message_id",
    "details",
)


@dataclass(frozen=True)
class NumberField:
    integer: bool
    minimum: int | Decimal
    maximum: int | Decimal


SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"
SEVERITIES = frozenset({SEVERITY_WARNING, SEVERITY_CRITICAL})

# Fall details (docs/metrics.md ranges for the fall fields).
FALL_DETAILS: dict[str, NumberField] = {
    "confidence": NumberField(integer=False, minimum=0, maximum=1),
    "peak_g": NumberField(integer=False, minimum=0, maximum=16),
    "still_s": NumberField(integer=True, minimum=0, maximum=600),
}

# Valid range per vitals metric (docs/metrics.md, wearable vitals).
METRIC_RANGES: dict[str, NumberField] = {
    "hr_bpm": NumberField(integer=True, minimum=20, maximum=250),
    "spo2_pct": NumberField(integer=True, minimum=50, maximum=100),
    "skin_temp_c": NumberField(
        integer=False, minimum=Decimal("20.0"), maximum=Decimal("45.0")
    ),
}
HELD_S = NumberField(integer=True, minimum=10, maximum=600)
NO_PULSE_VALUE = 0

# Vitals alert_type -> (metric, direction).
VITALS_TYPES: dict[str, tuple[str, str]] = {
    "hr_high": ("hr_bpm", "above"),
    "hr_low": ("hr_bpm", "below"),
    "spo2_low": ("spo2_pct", "below"),
    "skin_temp_high": ("skin_temp_c", "above"),
    "skin_temp_low": ("skin_temp_c", "below"),
}

# Severities each alert_type may have.
TYPE_SEVERITIES: dict[str, frozenset[str]] = {
    "fall": frozenset({SEVERITY_CRITICAL}),
    "hr_high": SEVERITIES,
    "hr_low": SEVERITIES,
    "spo2_low": SEVERITIES,
    "skin_temp_high": frozenset({SEVERITY_WARNING}),
    "skin_temp_low": frozenset({SEVERITY_WARNING}),
}
ALERT_TYPES = frozenset(TYPE_SEVERITIES)


@dataclass(frozen=True)
class Rejection:
    reason: str


@dataclass(frozen=True)
class Alert:
    alert_id: str
    alert_type: str
    severity: str
    resident_id: str
    device_id: str
    event_ts: str
    event_dt: datetime
    received_at: str
    received_dt: datetime
    source_message_id: str
    details: dict[str, Any]  # int or Decimal values


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-standard JSON constant {value}")


def parse_ts(value: Any) -> datetime | None:
    """UTC datetime of an ISO 8601 ms Z string, or None if invalid."""
    if type(value) is not str or not TS_RE.match(value):
        return None
    try:
        return datetime.strptime(value, TS_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _is_number(value: Any, integer: bool) -> bool:
    if type(value) is int:
        return True
    return not integer and isinstance(value, Decimal) and value.is_finite()


def _check_number(name: str, value: Any, spec: NumberField) -> str | None:
    if not _is_number(value, spec.integer):
        return f"wrong_type:details.{name}"
    if not spec.minimum <= value <= spec.maximum:
        return f"out_of_range:details.{name}"
    return None


def _check_fall_details(details: dict) -> str | None:
    if set(details) - set(FALL_DETAILS):
        return "unknown_field:details"
    for name, spec in FALL_DETAILS.items():
        if name not in details:
            return f"missing:details.{name}"
        reason = _check_number(name, details[name], spec)
        if reason:
            return reason
    return None


def _check_vitals_details(alert_type: str, severity: str, details: dict) -> str | None:
    """Types and ranges only (docs/features/vitals-alerts.md, decisions table and
    criterion 4): no check that `value` is past `limit` or that `limit` matches
    `direction`. `metric` and `direction` must match the alert_type, because the type
    is derived from them."""
    extra = "previous_value" if severity == SEVERITY_WARNING else "held_s"
    names = ("metric", "value", "limit", "direction", extra)
    if set(details) - set(names):
        return "unknown_field:details"
    for name in names:
        if name not in details:
            return f"missing:details.{name}"

    metric, direction = VITALS_TYPES[alert_type]
    for name, expected in (("metric", metric), ("direction", direction)):
        if type(details[name]) is not str:
            return f"wrong_type:details.{name}"
        if details[name] != expected:
            return f"invalid:details.{name}"

    value_range = METRIC_RANGES[metric]
    value = details["value"]
    no_pulse = (
        alert_type == "hr_low"
        and severity == SEVERITY_CRITICAL
        and type(value) is int
        and value == NO_PULSE_VALUE
    )
    if not no_pulse:
        reason = _check_number("value", value, value_range)
        if reason:
            return reason
    extra_spec = value_range if extra == "previous_value" else HELD_S
    for name, spec in (("limit", value_range), (extra, extra_spec)):
        reason = _check_number(name, details[name], spec)
        if reason:
            return reason
    return None


def _check_details(alert_type: str, severity: str, details: Any) -> str | None:
    if not isinstance(details, dict):
        return "wrong_type:details"
    if alert_type == "fall":
        return _check_fall_details(details)
    return _check_vitals_details(alert_type, severity, details)


def _check_str(body: dict, name: str, pattern: re.Pattern[str]) -> str | None:
    value = body[name]
    if type(value) is not str:
        return f"wrong_type:{name}"
    if not pattern.match(value):
        return f"invalid:{name}"
    return None


def validate(body: Any) -> Alert | Rejection:
    """Check a parsed body against the alert message schema."""
    if not isinstance(body, dict):
        return Rejection("invalid:body")
    for name in FIELDS:
        if name not in body:
            return Rejection(f"missing:{name}")
    if set(body) - set(FIELDS):
        return Rejection("unknown_field")

    version = body["schema_version"]
    if type(version) is not int:
        return Rejection("wrong_type:schema_version")
    if version != SCHEMA_VERSION:
        return Rejection("invalid:schema_version")

    alert_type = body["alert_type"]
    if type(alert_type) is not str:
        return Rejection("wrong_type:alert_type")
    if alert_type not in ALERT_TYPES:
        return Rejection("invalid:alert_type")

    severity = body["severity"]
    if type(severity) is not str:
        return Rejection("wrong_type:severity")
    if severity not in TYPE_SEVERITIES[alert_type]:
        return Rejection("invalid:severity")

    for name, pattern in (
        ("alert_id", UUID_RE),
        ("source_message_id", UUID_RE),
        ("resident_id", RESIDENT_ID_RE),
        ("device_id", DEVICE_ID_RE),
    ):
        reason = _check_str(body, name, pattern)
        if reason:
            return Rejection(reason)

    dates: dict[str, datetime] = {}
    for name in ("event_ts", "received_at"):
        if type(body[name]) is not str:
            return Rejection(f"wrong_type:{name}")
        parsed = parse_ts(body[name])
        if parsed is None:
            return Rejection(f"invalid:{name}")
        dates[name] = parsed

    reason = _check_details(alert_type, severity, body["details"])
    if reason:
        return Rejection(reason)

    return Alert(
        alert_id=body["alert_id"],
        alert_type=alert_type,
        severity=severity,
        resident_id=body["resident_id"],
        device_id=body["device_id"],
        event_ts=body["event_ts"],
        event_dt=dates["event_ts"],
        received_at=body["received_at"],
        received_dt=dates["received_at"],
        source_message_id=body["source_message_id"],
        details=dict(body["details"]),
    )


def parse_body(text: Any) -> Any:
    """JSON-decode an SQS body with floats as Decimal. Returns None if it isn't JSON."""
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text, parse_float=Decimal, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return None


def parse_alert(text: Any) -> Alert | Rejection:
    """Parse and validate one SQS record body."""
    body = parse_body(text)
    if body is None:
        return Rejection("invalid:json")
    return validate(body)


def log_ids(text: Any) -> dict[str, str | None]:
    """IDs for the log line of a rejected body: each only if it matches its format.

    Anything else is logged as "invalid" (or omitted when absent), so free text from a
    malformed body never reaches the logs.
    """
    body = parse_body(text)
    body = body if isinstance(body, dict) else {}

    def pick(name: str, ok: Any) -> str | None:
        if name not in body:
            return None
        value = body[name]
        return value if type(value) is str and ok(value) else "invalid"

    return {
        "alert_id": pick("alert_id", lambda v: bool(UUID_RE.match(v))),
        "alert_type": pick("alert_type", lambda v: v in ALERT_TYPES),
        "severity": pick("severity", lambda v: v in SEVERITIES),
        "device_id": pick("device_id", lambda v: bool(DEVICE_ID_RE.match(v))),
        "resident_id": pick("resident_id", lambda v: bool(RESIDENT_ID_RE.match(v))),
    }
