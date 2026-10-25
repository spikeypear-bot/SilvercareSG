"""Builders for fake SQS events carrying the ingest rules' output.

Each body is a device payload (docs/metrics.md) plus the fields the rule SELECT adds:
`device_id`, `message`, `received_at` (epoch ms). All IDs and values are synthetic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from itertools import count
from typing import Any

WEARABLE = "wearable-0001"
BEDROOM = "bedroom-0001"

# 2026-10-18T03:12:00.000Z
BASE_MS = int(datetime(2026, 10, 18, 3, 12, tzinfo=UTC).timestamp() * 1000)

PAYLOADS: dict[str, dict[str, Any]] = {
    "vitals": {"hr_bpm": 72, "spo2_pct": 97, "skin_temp_c": 33.4, "activity_pct": 12},
    "fall": {"confidence": 0.91, "peak_g": 3.8, "still_s": 20},
    "status": {
        "battery_pct": 64,
        "worn": True,
        "charging": False,
        "fw_version": "1.0.0",
    },
    "motion": {"motion": True},
    "bed": {"occupied": False},
}

_seq = count(1)


def message_id(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def body(
    message_type: str = "vitals", received_at: Any = BASE_MS, **overrides: Any
) -> dict:
    """A rule-output body. Pass a field as `_DELETE` to remove it."""
    n = next(_seq)
    device_id = BEDROOM if message_type in ("motion", "bed") else WEARABLE
    data: dict[str, Any] = {
        "schema_version": 1,
        "message_id": message_id(n),
        "ts": "2026-10-18T03:12:00.000Z",
        **PAYLOADS.get(message_type, {}),
        "device_id": device_id,
        "message": message_type,
        "received_at": received_at,
    }
    data.update(overrides)
    return {k: v for k, v in data.items() if v != "_DELETE"}


def record(
    raw_body: Any,
    sqs_id: str | None = None,
    sent_timestamp: str | None = None,
) -> dict:
    """One SQS record. raw_body is a dict (JSON-encoded) or a raw string."""
    n = next(_seq)
    text = raw_body if isinstance(raw_body, str) else json.dumps(raw_body)
    return {
        "messageId": sqs_id or f"sqs-test-{n:06d}",
        "receiptHandle": f"receipt-test-{n:06d}",
        "body": text,
        "attributes": {
            "ApproximateReceiveCount": "1",
            "SentTimestamp": sent_timestamp or str(BASE_MS),
            "SenderId": "AIDATESTSENDER",
            "ApproximateFirstReceiveTimestamp": str(BASE_MS),
        },
        "messageAttributes": {},
        "md5OfBody": "0" * 32,
        "eventSource": "aws:sqs",
        "eventSourceARN": "arn:aws:sqs:ap-southeast-1:000000000000:silvercare-history",
        "awsRegion": "ap-southeast-1",
    }


def sqs_event(*records: dict) -> dict:
    return {"Records": list(records)}


def epoch_ms(*args: int) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp() * 1000)
