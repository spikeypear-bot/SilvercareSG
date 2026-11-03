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

Escalation checks (docs/features/escalation.md, criterion 1) are a second message kind
on the same queue, sent by alert-processor itself with a 60 s delay:

    {"kind": "escalation_check", "schema_version": 1, "alert_id": "<uuid>",
     "resident_id": "res-0003", "event_ts": "2026-10-18T03:14:07.360Z"}

Status updates (docs/features/alert-cancel.md, criterion 6) are a third kind, sent
without delay by `critical-alert` (the wearable "I'm OK" cancel) and later by Track B's
acknowledge API:

    {"kind": "status_update", "schema_version": 1, "resident_id": "res-0001",
     "new_status": "cancelled", "changed_at": "2026-11-03T02:00:05.000Z",
     "by_role": "resident", "by": null, "by_name": null,
     "target": {"device_id": "wearable-0001"}}

`target` is {"device_id"} (every open alert from that device) or
{"alert_id", "event_ts"} (one alert). A `resident` update must be `cancelled` with a
device target, `by` null and `by_name` null; a `staff` or `caregiver` update must be
`acknowledged` with an alert target, a Cognito `sub` (UUID) as `by`, and a `by_name`
of 1-100 characters. `by_name` is a full name: it is used only to build the masked name
in the follow-up email and never stored or logged.

A body without `kind` is an alert message. `parse_message` returns an `Alert`, an
`EscalationCheck`, a `StatusUpdate`, or a `Rejection`; an unknown `kind` is rejected
like any invalid body.
"""

from __future__ import annotations

import json
import re
import unicodedata
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

KIND_ESCALATION_CHECK = "escalation_check"
CHECK_FIELDS = ("kind", "schema_version", "alert_id", "resident_id", "event_ts")

# Status updates (docs/features/alert-cancel.md, criterion 6).
KIND_STATUS_UPDATE = "status_update"
KINDS = frozenset({KIND_ESCALATION_CHECK, KIND_STATUS_UPDATE})
STATUS_UPDATE_FIELDS = (
    "kind",
    "schema_version",
    "resident_id",
    "new_status",
    "changed_at",
    "by_role",
    "by",
    "by_name",
    "target",
)
STATUS_CANCELLED = "cancelled"
STATUS_ACKNOWLEDGED = "acknowledged"
NEW_STATUSES = frozenset({STATUS_CANCELLED, STATUS_ACKNOWLEDGED})
ROLE_RESIDENT = "resident"
ROLE_STAFF = "staff"
ROLE_CAREGIVER = "caregiver"
BY_ROLES = frozenset({ROLE_RESIDENT, ROLE_STAFF, ROLE_CAREGIVER})
DEVICE_TARGET_FIELDS = frozenset({"device_id"})
ALERT_TARGET_FIELDS = frozenset({"alert_id", "event_ts"})
BY_NAME_MAX_LEN = 100
# A Cognito user's sub: a UUID, lowercase.
SUB_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


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


@dataclass(frozen=True)
class EscalationCheck:
    """Enough to build the alert's key: RESIDENT#<resident_id>,
    ALERT#<event_ts>#<alert_id>."""

    alert_id: str
    resident_id: str
    event_ts: str


@dataclass(frozen=True)
class StatusUpdate:
    """A validated status_update. Exactly one target: device_id (a cancel), or
    alert_id + event_ts (an acknowledge). by and by_name are None for the resident."""

    resident_id: str
    new_status: str
    changed_at: str
    changed_dt: datetime
    by_role: str
    by: str | None
    by_name: str | None
    device_id: str | None = None
    alert_id: str | None = None
    event_ts: str | None = None


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


def validate_check(body: dict) -> EscalationCheck | Rejection:
    """Check a parsed body that has a `kind` against the escalation check schema."""
    kind = body.get("kind")
    if type(kind) is not str:
        return Rejection("wrong_type:kind")
    if kind != KIND_ESCALATION_CHECK:
        return Rejection("invalid:kind")
    for name in CHECK_FIELDS:
        if name not in body:
            return Rejection(f"missing:{name}")
    if set(body) - set(CHECK_FIELDS):
        return Rejection("unknown_field")

    version = body["schema_version"]
    if type(version) is not int:
        return Rejection("wrong_type:schema_version")
    if version != SCHEMA_VERSION:
        return Rejection("invalid:schema_version")

    for name, pattern in (
        ("alert_id", UUID_RE),
        ("resident_id", RESIDENT_ID_RE),
    ):
        reason = _check_str(body, name, pattern)
        if reason:
            return Rejection(reason)

    if type(body["event_ts"]) is not str:
        return Rejection("wrong_type:event_ts")
    if parse_ts(body["event_ts"]) is None:
        return Rejection("invalid:event_ts")

    return EscalationCheck(
        alert_id=body["alert_id"],
        resident_id=body["resident_id"],
        event_ts=body["event_ts"],
    )


def _valid_by_name(value: Any) -> bool:
    """1-100 characters, at least one that isn't a space, and no control or other
    non-printing characters (they could break the email's lines)."""
    if type(value) is not str or not 1 <= len(value) <= BY_NAME_MAX_LEN:
        return False
    if not value.strip():
        return False
    return not any(unicodedata.category(ch).startswith("C") for ch in value)


def _check_target(target: Any) -> tuple[dict[str, str] | None, str]:
    """(target fields, "") for a valid device or alert target, else (None, reason)."""
    if not isinstance(target, dict):
        return None, "wrong_type:target"
    names = set(target)
    if names == DEVICE_TARGET_FIELDS:
        device_id = target["device_id"]
        if type(device_id) is not str or not DEVICE_ID_RE.match(device_id):
            return None, "invalid:target.device_id"
        return {"device_id": device_id}, ""
    if names == ALERT_TARGET_FIELDS:
        alert_id = target["alert_id"]
        if type(alert_id) is not str or not UUID_RE.match(alert_id):
            return None, "invalid:target.alert_id"
        if parse_ts(target["event_ts"]) is None:
            return None, "invalid:target.event_ts"
        return {"alert_id": alert_id, "event_ts": target["event_ts"]}, ""
    return None, "invalid:target"


def _check_role_rules(
    by_role: str, new_status: str, target: dict[str, str], by: Any, by_name: Any
) -> str | None:
    """Criterion 6: what each by_role may send."""
    if by_role == ROLE_RESIDENT:
        if new_status != STATUS_CANCELLED:
            return "invalid:new_status"
        if "device_id" not in target:
            return "invalid:target"
        if by is not None:
            return "invalid:by"
        if by_name is not None:
            return "invalid:by_name"
        return None
    if new_status != STATUS_ACKNOWLEDGED:
        return "invalid:new_status"
    if "alert_id" not in target:
        return "invalid:target"
    if type(by) is not str or not SUB_RE.match(by):
        return "invalid:by"
    if not _valid_by_name(by_name):
        return "invalid:by_name"
    return None


def validate_status_update(body: dict) -> StatusUpdate | Rejection:
    """Check a parsed body with kind status_update against its schema (criterion 6).
    Reason codes name the field, never its value (by_name is a full name)."""
    for name in STATUS_UPDATE_FIELDS:
        if name not in body:
            return Rejection(f"missing:{name}")
    if set(body) - set(STATUS_UPDATE_FIELDS):
        return Rejection("unknown_field")

    version = body["schema_version"]
    if type(version) is not int:
        return Rejection("wrong_type:schema_version")
    if version != SCHEMA_VERSION:
        return Rejection("invalid:schema_version")

    reason = _check_str(body, "resident_id", RESIDENT_ID_RE)
    if reason:
        return Rejection(reason)

    for name, allowed in (("new_status", NEW_STATUSES), ("by_role", BY_ROLES)):
        if type(body[name]) is not str:
            return Rejection(f"wrong_type:{name}")
        if body[name] not in allowed:
            return Rejection(f"invalid:{name}")

    if type(body["changed_at"]) is not str:
        return Rejection("wrong_type:changed_at")
    changed_dt = parse_ts(body["changed_at"])
    if changed_dt is None:
        return Rejection("invalid:changed_at")

    target, reason = _check_target(body["target"])
    if target is None:
        return Rejection(reason)

    role_reason = _check_role_rules(
        body["by_role"], body["new_status"], target, body["by"], body["by_name"]
    )
    if role_reason:
        return Rejection(role_reason)

    return StatusUpdate(
        resident_id=body["resident_id"],
        new_status=body["new_status"],
        changed_at=body["changed_at"],
        changed_dt=changed_dt,
        by_role=body["by_role"],
        by=body["by"],
        by_name=body["by_name"],
        device_id=target.get("device_id"),
        alert_id=target.get("alert_id"),
        event_ts=target.get("event_ts"),
    )


def parse_alert(text: Any) -> Alert | Rejection:
    """Parse and validate one SQS record body as an alert message."""
    body = parse_body(text)
    if body is None:
        return Rejection("invalid:json")
    return validate(body)


def parse_message(text: Any) -> Alert | EscalationCheck | StatusUpdate | Rejection:
    """Parse and validate one SQS record body of any kind: a body with `kind`
    status_update is a status update, one with any other `kind` an escalation check
    (or rejected), anything else an alert message."""
    body = parse_body(text)
    if body is None:
        return Rejection("invalid:json")
    if isinstance(body, dict) and "kind" in body:
        if body["kind"] == KIND_STATUS_UPDATE:
            return validate_status_update(body)
        return validate_check(body)
    return validate(body)


def check_body(alert_id: str, resident_id: str, event_ts: str) -> str:
    """The escalation check message for one alert, as an SQS message body."""
    return json.dumps(
        {
            "kind": KIND_ESCALATION_CHECK,
            "schema_version": SCHEMA_VERSION,
            "alert_id": alert_id,
            "resident_id": resident_id,
            "event_ts": event_ts,
        },
        separators=(",", ":"),
    )


def log_ids(text: Any) -> dict[str, str | None]:
    """IDs for the log line of a rejected body: each only if it matches its format.

    Anything else is logged as "invalid" (or omitted when absent), so free text from a
    malformed body never reaches the logs. Works for every message kind; for a status
    update, device_id and alert_id come from `target`. Never `by` or `by_name`.
    """
    body = parse_body(text)
    body = body if isinstance(body, dict) else {}

    def pick(name: str, ok: Any) -> str | None:
        if name not in body:
            return None
        value = body[name]
        return value if type(value) is str and ok(value) else "invalid"

    ids = {
        "alert_id": pick("alert_id", lambda v: bool(UUID_RE.match(v))),
        "alert_type": pick("alert_type", lambda v: v in ALERT_TYPES),
        "severity": pick("severity", lambda v: v in SEVERITIES),
        "device_id": pick("device_id", lambda v: bool(DEVICE_ID_RE.match(v))),
        "resident_id": pick("resident_id", lambda v: bool(RESIDENT_ID_RE.match(v))),
    }
    # Only bodies that name a kind get the key (escalation checks, status updates, or
    # unknown kinds).
    if "kind" in body:
        ids["kind"] = pick("kind", lambda v: v in KINDS)
    target = body.get("target")
    if body.get("kind") == KIND_STATUS_UPDATE and isinstance(target, dict):
        for name, pattern in (("device_id", DEVICE_ID_RE), ("alert_id", UUID_RE)):
            if name in target:
                value = target[name]
                ok = type(value) is str and pattern.match(value)
                ids[name] = value if ok else "invalid"
    return ids
