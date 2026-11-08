"""Builders for api-thresholds inputs. All IDs and values are synthetic and fixed, so
log and body checks never meet a random UUID. "Now" is 2026-11-08 04:00:00 UTC."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import TypeSerializer

NOW = datetime(2026, 11, 8, 4, 0, 0, tzinfo=UTC)
SUB = "00000000-0000-4000-8000-000000000001"
REQUEST_ID = "JpvNMgAbSQ0EJcQ="
NAME = "Test Doctor Zq"

RESIDENT_A = "res-0001"
RESIDENT_B = "res-0002"
WEARABLE_1 = "wearable-0001"
WEARABLE_2 = "wearable-0002"
WEARABLE_3 = "wearable-0003"
BEDROOM = "bedroom-0001"
UPDATED_AT = "2026-11-01T00:00:00.000Z"

PUT = "PUT /residents/{resident_id}/thresholds"
HISTORY = "GET /residents/{resident_id}/thresholds/history"

_serializer = TypeSerializer()


def body(hr=(50, 120), spo2=92, skin=(30.0, 38.0)) -> dict[str, Any]:
    return {
        "hr_bpm": {"min": hr[0], "max": hr[1]},
        "spo2_pct": {"min": spo2},
        "skin_temp_c": {"min": skin[0], "max": skin[1]},
    }


def api_event(
    route: str = PUT,
    groups: Any = ("doctor",),
    resident_id: Any = RESIDENT_A,
    payload: Any = None,
    claims: dict[str, Any] | None = None,
    **top: Any,
) -> dict[str, Any]:
    """An HTTP API payload 2.0 event with a Cognito JWT. `payload` is JSON-encoded
    unless it is already a string; PUT defaults to the unchanged thresholds."""
    if payload is None and route == PUT:
        payload = body()
    jwt_claims: dict[str, Any] = {
        "sub": SUB,
        "name": NAME,
        "cognito:groups": list(groups) if isinstance(groups, tuple) else groups,
        "token_use": "id",
    }
    jwt_claims.update(claims or {})
    method, path = route.split(" ", 1)
    event: dict[str, Any] = {
        "version": "2.0",
        "routeKey": route,
        "rawPath": path.replace("{resident_id}", str(resident_id)),
        "rawQueryString": "",
        "headers": {"authorization": "Bearer synthetic-token"},
        "pathParameters": {"resident_id": resident_id},
        "requestContext": {
            "http": {"method": method, "path": path},
            "requestId": REQUEST_ID,
            "routeKey": route,
            "stage": "$default",
            "authorizer": {"jwt": {"claims": jwt_claims, "scopes": None}},
        },
        "isBase64Encoded": False,
    }
    if payload is not None:
        event["body"] = payload if isinstance(payload, str) else json.dumps(payload)
    event.update(top)
    return event


# ---- DynamoDB items ----


def put_raw(ddb, item: dict[str, Any]) -> None:
    ddb.put_item(
        TableName=os.environ["REGISTRY_TABLE"],
        Item={k: _serializer.serialize(v) for k, v in item.items()},
    )


def put_thresholds(
    ddb, resident_id: str = RESIDENT_A, hr=(50, 120), spo2=92, **fields: Any
) -> None:
    item = {
        "pk": f"THRESHOLDS#{resident_id}",
        "sk": "THRESHOLDS",
        "hr_bpm": {"min": hr[0], "max": hr[1]},
        "spo2_pct": {"min": spo2},
        "skin_temp_c": {"min": Decimal("30.0"), "max": Decimal("38.0")},
        "updated_at": UPDATED_AT,
        "updated_by": "00000000-0000-4000-8000-0000000000ff",
        "updated_by_name": "Test Doctor Before",
    }
    item.update(fields)
    put_raw(ddb, item)


def put_device(ddb, device_id: str, resident_id: str | None = RESIDENT_A) -> None:
    item: dict[str, Any] = {
        "pk": f"DEVICE#{device_id}",
        "sk": "ASSIGNMENT",
        "entity_type": "DEVICE",
        "device_type": device_id.split("-", 1)[0],
    }
    if resident_id is not None:
        item.update(
            {
                "resident_id": resident_id,
                "home_id": "home-0001",
                "gsi1_pk": f"RESIDENT#{resident_id}",
                "gsi1_sk": f"DEVICE#{device_id}",
                "relationship": "synthetic-not-for-shadows",
            }
        )
    put_raw(ddb, item)


def get_item(ddb, pk: str, sk: str) -> dict | None:
    from boto3.dynamodb.types import TypeDeserializer

    item = ddb.get_item(
        TableName=os.environ["REGISTRY_TABLE"],
        Key={"pk": {"S": pk}, "sk": {"S": sk}},
        ConsistentRead=True,
    ).get("Item")
    d = TypeDeserializer()
    return {k: d.deserialize(v) for k, v in item.items()} if item else None


def log_items(ddb, resident_id: str = RESIDENT_A) -> list[dict]:
    result = ddb.query(
        TableName=os.environ["REGISTRY_TABLE"],
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": {"S": f"THRESHOLD_LOG#{resident_id}"}},
    )
    return result["Items"]
