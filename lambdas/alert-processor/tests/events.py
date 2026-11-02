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


def alert_body(**overrides: Any) -> str:
    """The SQS body text, as fall-alert sends it (compact JSON)."""
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
