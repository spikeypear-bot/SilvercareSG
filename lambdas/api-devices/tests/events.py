"""Builders for GET /devices inputs. All IDs and values are synthetic and fixed, so
log and body checks never meet a random UUID.

"Now" is 2026-11-04 04:00:00 UTC.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import TypeSerializer

NOW = datetime(2026, 11, 4, 4, 0, 0, tzinfo=UTC)

SUB = "00000000-0000-4000-8000-000000000001"
REQUEST_ID = "JpvNMgAbSQ0EJcQ="
EMAIL = "test.staff.zq@example.invalid"
NAME = "Test Staff Name Zq"

RESIDENT_A = "res-0001"
RESIDENT_B = "res-0002"
NAME_A = "Test Resident A"
NAME_B = "Test Resident B"
HOME_A = "home-0001"
HOME_B = "home-0002"
PHOTO_A = "photos/test-photo-zz-a.jpg"
PHOTO_B = "photos/test-photo-zz-b.jpg"
ASSIGNED_AT = "2026-10-18T03:00:00Z"

WEARABLE = "wearable-0001"
WEARABLE_2 = "wearable-0002"
BEDROOM = "bedroom-0001"

MESSAGE_ID = "00000000-0000-4000-8000-0000000000aa"
WORN_SENTINEL = "worn-sentinel-qx"

_serializer = TypeSerializer()


def iso(value: datetime) -> str:
    """ISO 8601 UTC with milliseconds and Z, as the devices send `ts`."""
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def ago(**delta: float) -> str:
    return iso(NOW - timedelta(**delta))


# ---- API Gateway HTTP API, payload 2.0 ----


def api_event(
    groups: Any = ("doctor",),
    claims: dict[str, Any] | None = None,
    drop_groups: bool = False,
    **top: Any,
) -> dict[str, Any]:
    """GET /devices with a Cognito JWT. `groups` is passed as given (a list, a string,
    or a tuple turned into a list); `claims` overrides claims; `top` sets top-level
    keys (queryStringParameters, body). No query string and no body by default."""
    jwt_claims: dict[str, Any] = {
        "sub": SUB,
        "email": EMAIL,
        "name": NAME,
        "cognito:groups": list(groups) if isinstance(groups, tuple) else groups,
        "token_use": "id",
    }
    if drop_groups:
        del jwt_claims["cognito:groups"]
    jwt_claims.update(claims or {})
    event: dict[str, Any] = {
        "version": "2.0",
        "routeKey": "GET /devices",
        "rawPath": "/devices",
        "rawQueryString": "",
        "headers": {"authorization": "Bearer synthetic-token"},
        "requestContext": {
            "http": {"method": "GET", "path": "/devices"},
            "requestId": REQUEST_ID,
            "routeKey": "GET /devices",
            "stage": "$default",
            "authorizer": {"jwt": {"claims": jwt_claims, "scopes": None}},
        },
        "isBase64Encoded": False,
    }
    event.update(top)
    return event


# ---- DynamoDB items ----


def _ser(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _serializer.serialize(v) for k, v in item.items()}


def put_raw(ddb, table: str, item: dict[str, Any]) -> None:
    ddb.put_item(TableName=table, Item=_ser(item))


def put_resident(
    ddb,
    resident_id: str = RESIDENT_A,
    display_name: Any = NAME_A,
    home_id: str = HOME_A,
    photo_key: str = PHOTO_A,
) -> None:
    """A registry PROFILE item, with a photo_key the route must never return."""
    item = {
        "pk": f"RESIDENT#{resident_id}",
        "sk": "PROFILE",
        "entity_type": "RESIDENT",
        "resident_id": resident_id,
        "display_name": display_name,
        "home_id": home_id,
        "photo_key": photo_key,
        "year_of_birth": 1941,
    }
    put_raw(ddb, os.environ["REGISTRY_TABLE"], item)


def put_device(
    ddb,
    device_id: str = WEARABLE,
    resident_id: str | None = RESIDENT_A,
    home_id: str | None = HOME_A,
    pk: str | None = None,
    **fields: Any,
) -> None:
    """A registry ASSIGNMENT item (a spare without resident_id/home_id/gsi1)."""
    item: dict[str, Any] = {
        "pk": pk or f"DEVICE#{device_id}",
        "sk": "ASSIGNMENT",
        "entity_type": "DEVICE",
        "device_type": device_id.split("-", 1)[0],
        "assigned_at": ASSIGNED_AT,
    }
    if resident_id is not None:
        item.update(
            {
                "resident_id": resident_id,
                "gsi1_pk": f"RESIDENT#{resident_id}",
                "gsi1_sk": f"DEVICE#{device_id}",
            }
        )
        if home_id is not None:
            item["home_id"] = home_id
    item.update(fields)
    put_raw(ddb, os.environ["REGISTRY_TABLE"], item)


def status_entry(device_id: str = WEARABLE, **fields: Any) -> dict[str, Any]:
    """A newest status entry sent 1 minute before NOW: a worn, not charging wearable
    at 63 % on fw 9.8.7-test (a bedroom unit at 87 % on 9.8.6-test). Fields override."""
    ts = fields.pop("ts", ago(minutes=1))
    entry: dict[str, Any] = {
        "message_id": MESSAGE_ID,
        "ts": ts,
        "received_at": iso(NOW - timedelta(seconds=59, milliseconds=579)),
        "clock_skew": False,
        "charging": False,
    }
    if device_id.startswith("wearable"):
        entry.update(
            {"battery_pct": Decimal(63), "worn": True, "fw_version": "9.8.7-test"}
        )
    else:
        entry.update({"battery_pct": Decimal(87), "fw_version": "9.8.6-test"})
    entry.update(fields)
    return entry


def put_status(ddb, device_id: str = WEARABLE, **fields: Any) -> dict[str, Any]:
    """A MSG#status item as device-health writes it, newest entry from status_entry."""
    entry = status_entry(device_id, **fields)
    older = {**entry, "message_id": "00000000-0000-4000-8000-0000000000ab"}
    item: dict[str, Any] = {
        "pk": f"DEVICE#{device_id}",
        "sk": "MSG#status",
        "device_type": device_id.split("-", 1)[0],
        "entries": [entry, older],
        "latest": entry,
        "latest_ts": entry["ts"],
        "version": 2,
        "watch_type": "status",
    }
    put_raw(ddb, os.environ["LATEST_TABLE"], item)
    return entry


def seed_fleet(ddb) -> None:
    """Two residents (B has no device), an assigned wearable and bedroom unit with
    statuses, and a spare wearable with no status."""
    put_resident(ddb, RESIDENT_A, NAME_A, HOME_A, PHOTO_A)
    put_resident(ddb, RESIDENT_B, NAME_B, HOME_B, PHOTO_B)
    put_device(ddb, WEARABLE)
    put_device(ddb, BEDROOM)
    put_device(ddb, WEARABLE_2, resident_id=None)
    put_status(ddb, WEARABLE)
    put_status(ddb, BEDROOM)
