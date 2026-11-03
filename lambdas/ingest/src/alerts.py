"""Alert messages for the alerts queue (docs/features/alerts-core.md, criteria 1-3;
docs/features/vitals-alerts.md, criteria 1-3).

Shared by every detector that raises alerts (`critical-alert` for falls and device
`vitals_alert`s, `telemetry` for the cloud vitals check; the watchdog later), so they
all send the same message shape. `alert-processor` reads it from the queue and
validates it again (its own copy of the schema, because it is built from a different
zip).

An alert message is one JSON object:

    schema_version     1
    alert_id           UUID v5 of "<event_ts>|<alert_type>|<severity>|<device_id>"
                       (alert_id_for)
    alert_type         fall, hr_high, hr_low, spo2_low, skin_temp_high, skin_temp_low
    severity           "critical" (falls, device vitals_alert) or "warning" (cloud check)
    resident_id        "res-0003", or "unassigned" when the device has no assignment
    device_id          "wearable-0007"
    event_ts           the triggering message's ts, ISO 8601 ms Z
    received_at        when the IoT rule received it, ISO 8601 ms Z
    source_message_id  the triggering message's message_id
    details            type-specific:
                       fall: confidence, peak_g, still_s (never accel_window)
                       cloud vitals (warning): metric, value, limit, direction,
                         previous_value
                       device vitals (critical): metric, value, limit, direction,
                         held_s

Status updates (docs/features/alert-cancel.md, criterion 6) are a third message kind
on the same queue. `critical-alert` sends one for a wearable `alert_cancel`:

    {"kind": "status_update", "schema_version": 1, "resident_id": "res-0001",
     "new_status": "cancelled", "changed_at": "<when the IoT rule received it>",
     "by_role": "resident", "by": null, "by_name": null,
     "target": {"device_id": "wearable-0001"}}

The builders, `alert_id_for`, and `encode` are pure. `send_alert` and
`send_status_update` are the AWS calls: SQS SendMessage, no group or delay
(`send_alert` adds the `alert_type` message attribute).
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from entries import format_iso_ms, received_at_datetime
from validation import ValidMessage
from vitals_check import ALERT_TYPES, Finding

SCHEMA_VERSION = 1

# Fixed namespace for alert IDs. Never change it: the same event must always give the
# same alert_id, or retries would create duplicate alerts.
ALERT_NAMESPACE = uuid.UUID("8d3c5f2a-6b1e-4f7d-9a0c-2e4b6d8f1a35")

ALERT_TYPE_FALL = "fall"

KIND_STATUS_UPDATE = "status_update"
STATUS_CANCELLED = "cancelled"
BY_ROLE_RESIDENT = "resident"

SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"

# Fall fields copied into details. accel_window is never included.
FALL_DETAIL_FIELDS = ("confidence", "peak_g", "still_s")

# Device vitals_alert fields copied into details (all of them).
VITALS_ALERT_DETAIL_FIELDS = ("metric", "value", "limit", "direction", "held_s")


def alert_id_for(event_ts: str, alert_type: str, severity: str, device_id: str) -> str:
    """Deterministic alert ID: the same event always gives the same ID.

    The one place the formula lives. severity is part of it for every type, falls
    included (docs/features/vitals-alerts.md, decisions table and criterion 2), so a
    cloud warning and a device critical alert with the same ts never collide. Items
    stored before vitals alerts keep their old IDs.
    """
    return str(
        uuid.uuid5(ALERT_NAMESPACE, f"{event_ts}|{alert_type}|{severity}|{device_id}")
    )


def _plain_number(value: Any) -> Any:
    """A Decimal (read back from DynamoDB) as an int or float for JSON.

    Decimal("128") -> 128, Decimal("38.4") -> 38.4 (the shortest float repr, so it
    reaches alert-processor as exactly 38.4). Anything else is returned as it is.
    """
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _alert(
    *,
    alert_type: str,
    severity: str,
    resident_id: str,
    device_id: str,
    event_ts: str,
    received_at: str,
    source_message_id: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "alert_id": alert_id_for(event_ts, alert_type, severity, device_id),
        "alert_type": alert_type,
        "severity": severity,
        "resident_id": resident_id,
        "device_id": device_id,
        "event_ts": event_ts,
        "received_at": received_at,
        "source_message_id": source_message_id,
        "details": details,
    }


def build_fall_alert(msg: ValidMessage, resident_id: str) -> dict[str, Any]:
    """The alert message for a validated `fall`. Falls are critical."""
    return _alert(
        alert_type=ALERT_TYPE_FALL,
        severity=SEVERITY_CRITICAL,
        resident_id=resident_id,
        device_id=msg.device_id,
        event_ts=msg.ts,
        received_at=format_iso_ms(received_at_datetime(msg.received_at_ms)),
        source_message_id=msg.message_id,
        details={name: msg.fields[name] for name in FALL_DETAIL_FIELDS},
    )


def build_device_vitals_alert(msg: ValidMessage, resident_id: str) -> dict[str, Any]:
    """The critical alert for a validated device `vitals_alert` (criterion 1 mapping:
    hr_bpm above -> hr_high, hr_bpm below -> hr_low (including value 0, no pulse),
    spo2_pct below -> spo2_low)."""
    fields = msg.fields
    return _alert(
        alert_type=ALERT_TYPES[(fields["metric"], fields["direction"])],
        severity=SEVERITY_CRITICAL,
        resident_id=resident_id,
        device_id=msg.device_id,
        event_ts=msg.ts,
        received_at=format_iso_ms(received_at_datetime(msg.received_at_ms)),
        source_message_id=msg.message_id,
        details={name: fields[name] for name in VITALS_ALERT_DETAIL_FIELDS},
    )


def build_critical_alert(msg: ValidMessage, resident_id: str) -> dict[str, Any]:
    """The critical alert for a validated device-raised message (fall, vitals_alert)."""
    if msg.message == "vitals_alert":
        return build_device_vitals_alert(msg, resident_id)
    return build_fall_alert(msg, resident_id)


def build_vitals_warning(
    device_id: str,
    resident_id: str,
    newest_entry: dict[str, Any],
    finding: Finding,
) -> dict[str, Any]:
    """The warning for a cloud-check finding (criteria 3, 10).

    event_ts, received_at, and source_message_id come from the newest stored entry,
    so a retry (a duplicate message) builds the same alert.
    """
    return _alert(
        alert_type=finding.alert_type,
        severity=SEVERITY_WARNING,
        resident_id=resident_id,
        device_id=device_id,
        event_ts=newest_entry["ts"],
        received_at=newest_entry["received_at"],
        source_message_id=newest_entry["message_id"],
        details={
            "metric": finding.metric,
            "value": _plain_number(finding.value),
            "limit": _plain_number(finding.limit),
            "direction": finding.direction,
            "previous_value": _plain_number(finding.previous_value),
        },
    )


def encode(alert: dict[str, Any]) -> str:
    """Compact JSON. Floats keep their shortest repr (0.91 -> 0.91)."""
    return json.dumps(alert, separators=(",", ":"), allow_nan=False)


def send_alert(client: Any, queue_url: str, alert: dict[str, Any]) -> None:
    """Send one alert message to the alerts queue. AWS errors are raised as they are."""
    client.send_message(
        QueueUrl=queue_url,
        MessageBody=encode(alert),
        MessageAttributes={
            "alert_type": {"DataType": "String", "StringValue": alert["alert_type"]},
        },
    )


def build_cancel_update(msg: ValidMessage, resident_id: str) -> dict[str, Any]:
    """The status_update for a validated wearable `alert_cancel`: cancel every open
    alert from this device up to the press. changed_at is when the IoT rule received
    the message (server time, received_at), not the device's ts, so a wrong device
    clock can't move the Query bound or the 30-day expiry."""
    return {
        "kind": KIND_STATUS_UPDATE,
        "schema_version": SCHEMA_VERSION,
        "resident_id": resident_id,
        "new_status": STATUS_CANCELLED,
        "changed_at": format_iso_ms(received_at_datetime(msg.received_at_ms)),
        "by_role": BY_ROLE_RESIDENT,
        "by": None,
        "by_name": None,
        "target": {"device_id": msg.device_id},
    }


def send_status_update(client: Any, queue_url: str, update: dict[str, Any]) -> None:
    """Send one status_update to the alerts queue, without delay. AWS errors are
    raised as they are."""
    client.send_message(QueueUrl=queue_url, MessageBody=encode(update))
