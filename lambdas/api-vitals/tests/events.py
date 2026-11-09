"""Builders for GET /residents/{resident_id}/vitals inputs. All IDs and values are
synthetic and fixed.

"Now" is 2026-11-19 10:00 SGT, so yesterday's Singapore date is 2026-11-18.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

SGT = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 19, 10, 0, tzinfo=SGT).astimezone(UTC)
YESTERDAY = "2026-11-18"
SUB = "00000000-0000-4000-8000-000000000001"
REQUEST_ID = "JpvNMgAbSQ0EJcQ="
RESIDENT = "res-0003"
WEARABLE = "wearable-0003"
BEDROOM = "bedroom-0003"
ASSIGNED_AT = "2026-10-01T00:00:00Z"


def event(
    resident_id: Any = RESIDENT,
    groups: Any = "[doctor]",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if params is None:
        params = {"interval": "day", "from": "2026-11-17", "to": YESTERDAY}
    return {
        "version": "2.0",
        "routeKey": "GET /residents/{resident_id}/vitals",
        "pathParameters": {"resident_id": resident_id},
        "queryStringParameters": params,
        "requestContext": {
            "requestId": REQUEST_ID,
            "authorizer": {"jwt": {"claims": {"sub": SUB, "cognito:groups": groups}}},
        },
    }


def put_device(
    client,
    table: str,
    device_id: str,
    resident_id: str = RESIDENT,
    assigned_at: str = ASSIGNED_AT,
) -> None:
    client.put_item(
        TableName=table,
        Item={
            "pk": {"S": f"DEVICE#{device_id}"},
            "sk": {"S": "ASSIGNMENT"},
            "entity_type": {"S": "DEVICE"},
            "device_type": {"S": device_id.split("-")[0]},
            "resident_id": {"S": resident_id},
            "assigned_at": {"S": assigned_at},
            "gsi1_pk": {"S": f"RESIDENT#{resident_id}"},
            "gsi1_sk": {"S": f"DEVICE#{device_id}"},
        },
    )


def put_thresholds(client, table: str, resident_id: str = RESIDENT) -> None:
    client.put_item(
        TableName=table,
        Item={
            "pk": {"S": f"THRESHOLDS#{resident_id}"},
            "sk": {"S": "THRESHOLDS"},
            "spo2_pct": {"M": {"min": {"N": "88"}}},
        },
    )
