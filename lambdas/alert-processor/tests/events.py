"""Builders for alert messages and SQS events. All IDs and values are synthetic.

The alert message is the one lambdas/ingest/src/alerts.py builds
(docs/features/alerts-core.md, criterion 1). IDs are fixed so log checks never meet a
random UUID.
"""

from __future__ import annotations

import json
from typing import Any

ALERT_ID = "6f1c2e0a-5b3d-5e8f-9a47-000000000001"
ALERT_ID_2 = "6f1c2e0a-5b3d-5e8f-9a47-000000000002"
ALERT_ID_3 = "6f1c2e0a-5b3d-5e8f-9a47-000000000003"
SOURCE_MESSAGE_ID = "c2a1d4f0-0000-4000-8000-000000000001"
RESIDENT = "res-0003"
DEVICE = "wearable-0007"
EVENT_TS = "2026-10-18T03:14:07.360Z"
RECEIVED_AT = "2026-10-18T03:14:07.410Z"

DELETE = object()


def alert_message(**overrides: Any) -> dict[str, Any]:
    """A valid fall alert message. Pass a field as DELETE to remove it."""
    body: dict[str, Any] = {
        "schema_version": 1,
        "alert_id": ALERT_ID,
        "alert_type": "fall",
        "severity": "critical",
        "resident_id": RESIDENT,
        "device_id": DEVICE,
        "event_ts": EVENT_TS,
        "received_at": RECEIVED_AT,
        "source_message_id": SOURCE_MESSAGE_ID,
        "details": {"confidence": 0.91, "peak_g": 3.8, "still_s": 20},
    }
    for name, value in overrides.items():
        if value is DELETE:
            body.pop(name, None)
        else:
            body[name] = value
    return body


# Vitals alert details per (alert_type, severity) (docs/features/vitals-alerts.md,
# criterion 3). Synthetic readings past the default or device limits.
VITALS_DETAILS: dict[tuple[str, str], dict[str, Any]] = {
    ("hr_high", "warning"): {
        "metric": "hr_bpm",
        "value": 128,
        "limit": 120,
        "direction": "above",
        "previous_value": 125,
    },
    ("hr_low", "warning"): {
        "metric": "hr_bpm",
        "value": 44,
        "limit": 50,
        "direction": "below",
        "previous_value": 46,
    },
    ("spo2_low", "warning"): {
        "metric": "spo2_pct",
        "value": 89,
        "limit": 92,
        "direction": "below",
        "previous_value": 90,
    },
    ("skin_temp_high", "warning"): {
        "metric": "skin_temp_c",
        "value": 38.4,
        "limit": 38.0,
        "direction": "above",
        "previous_value": 38.2,
    },
    ("skin_temp_low", "warning"): {
        "metric": "skin_temp_c",
        "value": 29.5,
        "limit": 30.0,
        "direction": "below",
        "previous_value": 29.8,
    },
    ("hr_high", "critical"): {
        "metric": "hr_bpm",
        "value": 140,
        "limit": 130,
        "direction": "above",
        "held_s": 10,
    },
    ("hr_low", "critical"): {
        "metric": "hr_bpm",
        "value": 40,
        "limit": 45,
        "direction": "below",
        "held_s": 10,
    },
    ("spo2_low", "critical"): {
        "metric": "spo2_pct",
        "value": 85,
        "limit": 88,
        "direction": "below",
        "held_s": 12,
    },
}

NO_PULSE_DETAILS = {
    "metric": "hr_bpm",
    "value": 0,
    "limit": 45,
    "direction": "below",
    "held_s": 10,
}


def vitals_message(
    alert_type: str = "hr_high", severity: str = "warning", **overrides: Any
) -> dict[str, Any]:
    """A valid vitals alert message (warning from telemetry, critical from the device).

    `details` may be overridden; other overrides replace top-level fields.
    """
    fields: dict[str, Any] = {
        "alert_type": alert_type,
        "severity": severity,
        "details": dict(VITALS_DETAILS[(alert_type, severity)]),
    }
    fields.update(overrides)
    return alert_message(**fields)


def vitals_body(
    alert_type: str = "hr_high", severity: str = "warning", **overrides: Any
) -> str:
    return json.dumps(
        vitals_message(alert_type, severity, **overrides), separators=(",", ":")
    )


def alert_body(**overrides: Any) -> str:
    """The SQS body text, as critical-alert sends it (compact JSON)."""
    return json.dumps(alert_message(**overrides), separators=(",", ":"))


def record(body: Any, message_id: str = "sqs-msg-0001") -> dict[str, Any]:
    """One SQS record as Lambda's event source mapping delivers it."""
    return {
        "messageId": message_id,
        "receiptHandle": "synthetic-receipt",
        "body": body,
        "attributes": {"ApproximateReceiveCount": "1"},
        "messageAttributes": {
            "alert_type": {"stringValue": "fall", "dataType": "String"}
        },
        "eventSource": "aws:sqs",
        "eventSourceARN": "arn:aws:sqs:ap-southeast-1:123456789012:silvercare-alerts",
        "awsRegion": "ap-southeast-1",
    }


def sqs_event(*records: dict[str, Any]) -> dict[str, Any]:
    return {"Records": list(records)}


def check_message(**overrides: Any) -> dict[str, Any]:
    """A valid escalation check (docs/features/escalation.md, criterion 1) for the
    default alert. Pass a field as DELETE to remove it."""
    body: dict[str, Any] = {
        "kind": "escalation_check",
        "schema_version": 1,
        "alert_id": ALERT_ID,
        "resident_id": RESIDENT,
        "event_ts": EVENT_TS,
    }
    for name, value in overrides.items():
        if value is DELETE:
            body.pop(name, None)
        else:
            body[name] = value
    return body


def check_body(**overrides: Any) -> str:
    """The check's SQS body text, compact JSON as alert-processor sends it."""
    return json.dumps(check_message(**overrides), separators=(",", ":"))


# ---- status updates (docs/features/alert-cancel.md, criterion 6; changed by
# docs/features/alert-routing.md, criterion 9) ----

# Synthetic Cognito subs (lowercase UUIDs) and a synthetic full name for cancels.
NURSE_SUB = "a1b2c3d4-0000-4000-8000-00000000abcd"
CAREGIVER_SUB = "a1b2c3d4-0000-4000-8000-00000000cafe"
CAREGIVER_NAME = "Test Caregiver Beta"
CAREGIVER_MASKED = "Te** Ca******* Be**"
NOTE = "Synthetic note: ambulance on the way, 8 minutes."
# The cancel press reached AWS IoT 52.64 s after the default alert's event_ts.
CHANGED_AT = "2026-10-18T03:15:00.000Z"


def _apply(body: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    for name, value in overrides.items():
        if value is DELETE:
            body.pop(name, None)
        else:
            body[name] = value
    return body


def cancel_message(**overrides: Any) -> dict[str, Any]:
    """A valid wearable cancel (by_role resident, device target) for the default
    resident and device. Pass a field as DELETE to remove it."""
    body: dict[str, Any] = {
        "kind": "status_update",
        "schema_version": 1,
        "resident_id": RESIDENT,
        "new_status": "cancelled",
        "changed_at": CHANGED_AT,
        "by_role": "resident",
        "by": None,
        "by_name": None,
        "target": {"device_id": DEVICE},
    }
    return _apply(body, overrides)


def caregiver_cancel_message(**overrides: Any) -> dict[str, Any]:
    """A valid caregiver cancel (alert target, sub, name claim, no note key) of the
    default alert. Pass a field as DELETE to remove it."""
    body = cancel_message(
        by_role="caregiver",
        by=CAREGIVER_SUB,
        by_name=CAREGIVER_NAME,
        target={"alert_id": ALERT_ID, "event_ts": EVENT_TS},
    )
    return _apply(body, overrides)


def nurse_ack_message(note: Any = DELETE, **overrides: Any) -> dict[str, Any]:
    """A valid nurse acknowledge (alert target, sub, by_name null) of the default
    alert. `note` is absent unless given (None sends "note": null)."""
    body = cancel_message(
        new_status="acknowledged",
        by_role="nurse",
        by=NURSE_SUB,
        by_name=None,
        target={"alert_id": ALERT_ID, "event_ts": EVENT_TS},
    )
    if note is not DELETE:
        body["note"] = note
    return _apply(body, overrides)


def _compact(body: dict[str, Any]) -> str:
    return json.dumps(body, separators=(",", ":"))


def cancel_body(**overrides: Any) -> str:
    return _compact(cancel_message(**overrides))


def caregiver_cancel_body(**overrides: Any) -> str:
    return _compact(caregiver_cancel_message(**overrides))


def nurse_ack_body(note: Any = DELETE, **overrides: Any) -> str:
    return _compact(nurse_ack_message(note, **overrides))


# ---- device problems (docs/features/watchdog.md, criteria 8, 8a, 10, 14a) ----

OPS_SUB = "a1b2c3d4-0000-4000-8000-0000000000ee"
# The watchdog's run that saw the problem, and its last-heartbeat / since times.
DP_EVENT_TS = "2026-11-04T01:40:00.000Z"
DP_RECEIVED_AT = "2026-11-04T02:00:00.000Z"
DP_CHANGED_AT = "2026-11-04T02:05:00.000Z"

DEVICE_PROBLEM_DETAILS: dict[str, dict[str, Any]] = {
    "device_offline": {"last_seen": DP_EVENT_TS},
    "battery_low": {"battery_pct": 15},
    "not_worn": {"since": DP_EVENT_TS},
}
DEVICE_PROBLEM_TYPES = tuple(DEVICE_PROBLEM_DETAILS)


def device_problem_message(
    alert_type: str = "battery_low", **overrides: Any
) -> dict[str, Any]:
    """A valid watchdog alert message (A1 format, severity warning). `details` may be
    overridden; pass a field as DELETE to remove it."""
    fields: dict[str, Any] = {
        "alert_type": alert_type,
        "severity": "warning",
        "event_ts": DP_EVENT_TS,
        "received_at": DP_RECEIVED_AT,
        "details": dict(DEVICE_PROBLEM_DETAILS[alert_type]),
    }
    fields.update(overrides)
    return alert_message(**fields)


def device_problem_body(alert_type: str = "battery_low", **overrides: Any) -> str:
    return _compact(device_problem_message(alert_type, **overrides))


def ops_ack_message(**overrides: Any) -> dict[str, Any]:
    """A valid ops acknowledge (alert target, sub, by_name null, no note key) of the
    default alert at DP_EVENT_TS."""
    body = cancel_message(
        new_status="acknowledged",
        changed_at=DP_CHANGED_AT,
        by_role="ops",
        by=OPS_SUB,
        by_name=None,
        target={"alert_id": ALERT_ID, "event_ts": DP_EVENT_TS},
    )
    return _apply(body, overrides)


def caregiver_ack_message(**overrides: Any) -> dict[str, Any]:
    """A valid caregiver acknowledge (alert target, sub, name claim, no note key)."""
    body = ops_ack_message(
        by_role="caregiver", by=CAREGIVER_SUB, by_name=CAREGIVER_NAME
    )
    return _apply(body, overrides)


def resolve_message(**overrides: Any) -> dict[str, Any]:
    """The watchdog's system resolve (alert target, by and by_name null, no note)."""
    body = ops_ack_message(new_status="resolved", by_role="system", by=None)
    return _apply(body, overrides)


def ops_ack_body(**overrides: Any) -> str:
    return _compact(ops_ack_message(**overrides))


def caregiver_ack_body(**overrides: Any) -> str:
    return _compact(caregiver_ack_message(**overrides))


def resolve_body(**overrides: Any) -> str:
    return _compact(resolve_message(**overrides))
