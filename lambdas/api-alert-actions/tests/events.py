"""Builders for the alert action routes' inputs. All IDs and values are synthetic and
fixed, so log and body checks never meet a random UUID.

"Now" is 2026-11-05 03:10:00.123456 UTC.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from typing import Any

from boto3.dynamodb.types import TypeSerializer

NOW = datetime(2026, 11, 5, 3, 10, 0, 123456, tzinfo=UTC)
NOW_ISO = "2026-11-05T03:10:00.123Z"

SUB = "00000000-0000-4000-8000-000000000001"
OTHER_SUB = "00000000-0000-4000-8000-000000000002"
REQUEST_ID = "JpvNMgAbSQ0EJcQ="
EMAIL = "test.staff.zq@example.invalid"
NAME = "Test Staff Name Zq"
CAREGIVER_NAME = "Test Caregiver Name Qx"

RESIDENT = "res-0001"
OTHER_RESIDENT = "res-0002"
UNASSIGNED = "unassigned"
ALERT_ID = "6f1c2e0a-5b3d-5e8f-9a47-000000000001"
EVENT_TS = "2026-11-05T02:50:00.000Z"
DEVICE = "wearable-0001"

NOTE = "Synthetic note: resident checked, false alarm."

HEALTH_TYPES = (
    "fall",
    "hr_high",
    "hr_low",
    "spo2_low",
    "skin_temp_high",
    "skin_temp_low",
    "possible_fall",
)
DEVICE_PROBLEM_TYPES = ("device_offline", "battery_low", "not_worn")

ROUTES = {
    "acknowledge": "POST /residents/{resident_id}/alerts/{alert_id}/acknowledge",
    "cancel": "POST /residents/{resident_id}/alerts/{alert_id}/cancel",
    "resolve": "POST /residents/{resident_id}/alerts/{alert_id}/resolve",
}

_serializer = TypeSerializer()
_UNSET: Any = object()


# ---- API Gateway HTTP API, payload 2.0 ----


def api_event(
    action: str = "resolve",
    groups: Any = ("nurse",),
    body: Any = _UNSET,
    resident_id: str = RESIDENT,
    alert_id: str = ALERT_ID,
    claims: dict[str, Any] | None = None,
    base64_body: bool = False,
    **top: Any,
) -> dict[str, Any]:
    """POST .../<action> with a Cognito ID token's claims. `groups` is passed as
    given (a tuple becomes a list, None drops the claim); `body` is a dict (JSON-
    encoded), a raw string, or None; by default {event_ts, note} for a resolve and
    {event_ts} otherwise. `top` overrides top-level keys (routeKey, pathParameters)."""
    if body is _UNSET:
        body = {"event_ts": EVENT_TS}
        if action == "resolve":
            body["note"] = NOTE
    text = body if body is None or isinstance(body, str) else json.dumps(body)
    if base64_body and text is not None:
        text = base64.b64encode(text.encode("utf-8")).decode("ascii")
    jwt_claims: dict[str, Any] = {
        "sub": SUB,
        "email": EMAIL,
        "name": NAME,
        "token_use": "id",
    }
    if groups is not None:
        jwt_claims["cognito:groups"] = (
            list(groups) if isinstance(groups, tuple) else groups
        )
    jwt_claims.update(claims or {})
    route = ROUTES.get(action, action)
    path = f"/residents/{resident_id}/alerts/{alert_id}/{action}"
    event: dict[str, Any] = {
        "version": "2.0",
        "routeKey": route,
        "rawPath": path,
        "rawQueryString": "",
        "headers": {
            "authorization": "Bearer synthetic-token",
            "content-type": "application/json",
        },
        "pathParameters": {"resident_id": resident_id, "alert_id": alert_id},
        "requestContext": {
            "http": {"method": "POST", "path": path},
            "requestId": REQUEST_ID,
            "routeKey": route,
            "stage": "$default",
            "authorizer": {"jwt": {"claims": jwt_claims, "scopes": None}},
        },
        "body": text,
        "isBase64Encoded": base64_body,
    }
    event.update(top)
    return event


# ---- DynamoDB items ----


def _ser(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _serializer.serialize(v) for k, v in item.items()}


def put_alert(
    ddb,
    alert_type: str = "fall",
    status: Any = "open",
    resident_id: str = RESIDENT,
    alert_id: str = ALERT_ID,
    event_ts: str = EVENT_TS,
    **fields: Any,
) -> dict[str, Any]:
    """An alerts item as alert-processor stores it (synthetic details). `status`
    None leaves the attribute out."""
    item: dict[str, Any] = {
        "pk": f"RESIDENT#{resident_id}",
        "sk": f"ALERT#{event_ts}#{alert_id}",
        "alert_id": alert_id,
        "alert_type": alert_type,
        "severity": "critical",
        "resident_id": resident_id,
        "device_id": DEVICE,
        "event_ts": event_ts,
        "details": {"synthetic": "detail-zq"},
    }
    if status is not None:
        item["status"] = status
    if status in ("open", "acknowledged"):
        item["open_pk"] = "OPEN"
    item.update(fields)
    ddb.put_item(TableName=os.environ["ALERTS_TABLE"], Item=_ser(item))
    return item


def put_link(ddb, sub: str = SUB, resident_id: str = RESIDENT) -> None:
    """Pattern 13's user-resident link item."""
    item = {
        "pk": f"USER#{sub}",
        "sk": f"RESIDENT#{resident_id}",
        "entity_type": "USER_LINK",
    }
    ddb.put_item(TableName=os.environ["REGISTRY_TABLE"], Item=_ser(item))


# ---- responses ----


def status_of(result: dict[str, Any]) -> int:
    return result["statusCode"]


def body_of(result: dict[str, Any]) -> Any:
    return json.loads(result["body"])
