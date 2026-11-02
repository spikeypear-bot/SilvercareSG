"""Alert messages for the alerts queue (docs/features/alerts-core.md, criteria 1-3).

Shared by every detector that raises alerts (`fall-alert` now; vitals alerts and the
watchdog later), so they all send the same message shape. `alert-processor` reads it
from the queue and validates it again (its own copy of the schema, because it is built
from a different zip).

An alert message is one JSON object:

    schema_version     1
    alert_id           UUID v5 of "<event_ts>|<alert_type>|<device_id>" (alert_id_for)
    alert_type         "fall" (only type so far)
    resident_id        "res-0003", or "unassigned" when the device has no assignment
    device_id          "wearable-0007"
    event_ts           the triggering message's ts, ISO 8601 ms Z
    received_at        when the IoT rule received it, ISO 8601 ms Z
    source_message_id  the triggering message's message_id
    details            type-specific; for a fall: confidence, peak_g, still_s only

`build_fall_alert`, `alert_id_for`, and `encode` are pure. `send_alert` is the one AWS
call: SQS SendMessage with the `alert_type` message attribute, no group or delay.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from entries import format_iso_ms, received_at_datetime
from validation import ValidMessage

SCHEMA_VERSION = 1

# Fixed namespace for alert IDs. Never change it: the same event must always give the
# same alert_id, or retries would create duplicate alerts.
ALERT_NAMESPACE = uuid.UUID("8d3c5f2a-6b1e-4f7d-9a0c-2e4b6d8f1a35")

ALERT_TYPE_FALL = "fall"

# Fall fields copied into details. accel_window is never included.
FALL_DETAIL_FIELDS = ("confidence", "peak_g", "still_s")


def alert_id_for(event_ts: str, alert_type: str, device_id: str) -> str:
    """Deterministic alert ID: the same event always gives the same ID."""
    return str(uuid.uuid5(ALERT_NAMESPACE, f"{event_ts}|{alert_type}|{device_id}"))


def build_fall_alert(msg: ValidMessage, resident_id: str) -> dict[str, Any]:
    """The alert message for a validated `fall`."""
    return {
        "schema_version": SCHEMA_VERSION,
        "alert_id": alert_id_for(msg.ts, ALERT_TYPE_FALL, msg.device_id),
        "alert_type": ALERT_TYPE_FALL,
        "resident_id": resident_id,
        "device_id": msg.device_id,
        "event_ts": msg.ts,
        "received_at": format_iso_ms(received_at_datetime(msg.received_at_ms)),
        "source_message_id": msg.message_id,
        "details": {name: msg.fields[name] for name in FALL_DETAIL_FIELDS},
    }


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
