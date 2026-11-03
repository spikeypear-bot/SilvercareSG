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


# ---- status updates (docs/features/alert-cancel.md, criterion 6) ----

# A synthetic Cognito sub (lowercase UUID) and a synthetic full name for acknowledges.
STAFF_SUB = "a1b2c3d4-0000-4000-8000-00000000abcd"
CAREGIVER_SUB = "a1b2c3d4-0000-4000-8000-00000000cafe"
STAFF_NAME = "Test Nurse Alpha"
CAREGIVER_NAME = "Test Caregiver Beta"
# The cancel press reached AWS IoT 52.64 s after the default alert's event_ts.
CHANGED_AT = "2026-10-18T03:15:00.000Z"


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
    for name, value in overrides.items():
        if value is DELETE:
            body.pop(name, None)
        else:
            body[name] = value
    return body


def ack_message(by_role: str = "staff", **overrides: Any) -> dict[str, Any]:
    """A valid acknowledge (alert target) of the default alert by staff or a
    caregiver."""
    staff = by_role == "staff"
    fields: dict[str, Any] = {
        "new_status": "acknowledged",
        "by_role": by_role,
        "by": STAFF_SUB if staff else CAREGIVER_SUB,
        "by_name": STAFF_NAME if staff else CAREGIVER_NAME,
        "target": {"alert_id": ALERT_ID, "event_ts": EVENT_TS},
    }
    fields.update(overrides)
    return cancel_message(**fields)


def cancel_body(**overrides: Any) -> str:
    return json.dumps(cancel_message(**overrides), separators=(",", ":"))


def ack_body(by_role: str = "staff", **overrides: Any) -> str:
    return json.dumps(ack_message(by_role, **overrides), separators=(",", ":"))
