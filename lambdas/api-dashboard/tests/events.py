"""Builders for GET /dashboard inputs. All IDs and values are synthetic and fixed
(except Cognito subs, which moto generates), so log and body checks never meet a
random UUID by chance.

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
NURSE_SUB = "00000000-0000-4000-8000-000000000004"
OPS_SUB = "00000000-0000-4000-8000-000000000005"
UPDATED_BY_SUB = "00000000-0000-4000-8000-000000000006"
REQUEST_ID = "JpvNMgAbSQ0EJcQ="
EMAIL = "test.staff.zq@example.invalid"
NAME = "Test Staff Name Zq"

CAREGIVER_EMAIL = "test.caregiver.a@example.invalid"
CAREGIVER_NAME = "Test Caregiver A"
CAREGIVER_2_EMAIL = "test.caregiver.b@example.invalid"
CAREGIVER_2_NAME = "Test Caregiver B"

RESIDENT_A = "res-0001"
RESIDENT_B = "res-0002"
RESIDENT_C = "res-0003"
RESIDENT_NO_PROFILE = "res-0009"
NAME_A = "Test Resident A"
NAME_B = "Test Resident B"
NAME_C = "Test Resident C"
HOME_A = "home-0001"
HOME_B = "home-0002"
HOME_C = "home-0003"
PHOTO_A = "photos/test-photo-zz-a.jpg"
PROFILE_NOTE = "Synthetic medical note qx-4471"
ASSIGNED_AT = "2026-10-18T03:00:00Z"

WEARABLE = "wearable-0001"
WEARABLE_2 = "wearable-0002"
WEARABLE_3 = "wearable-0003"
SPARE = "wearable-0009"
BEDROOM = "bedroom-0001"
BEDROOM_2 = "bedroom-0002"

ALERT_1 = "6f1c2e0a-5b3d-5e8f-9a47-0000000000a1"
ALERT_2 = "6f1c2e0a-5b3d-5e8f-9a47-0000000000a2"
ALERT_3 = "6f1c2e0a-5b3d-5e8f-9a47-0000000000a3"
ALERT_4 = "6f1c2e0a-5b3d-5e8f-9a47-0000000000a4"

# Sentinels for projected (or stored) fields the route must drop.
MESSAGE_ID = "00000000-0000-4000-8000-0000000000aa"
SOURCE_MESSAGE_ID = "00000000-0000-4000-8000-0000000000cc"
NOTIFIED_AT = "2026-11-04T03:50:07.317Z"
ESCALATES_AT = "2026-11-04T03:55:07.317Z"
ALERT_RECEIVED_AT = "2026-11-04T03:50:06.911Z"
ALERT_CREATED_AT = "2026-11-04T03:50:07.023Z"
LATEST_RECEIVED_AT = "2026-11-04T03:59:00.421Z"
CLOCK_SKEW_SENTINEL = "clock-skew-sentinel-qx"
DETAILS_SENTINEL = "details-sentinel-qx"
UPDATED_BY_NAME = "Test Doctor Zq"
BED_SENTINEL = "bed-sentinel-qx"
MOTION_SENTINEL = "motion-sentinel-qx"
ACK_NOTE = "Synthetic note: checked the strap qx-3318.\nSecond line."

# Default readings, chosen to be easy to search for in logs.
HR = Decimal(73)
SPO2 = Decimal(96)
SKIN = Decimal("36.4")
ACTIVITY = Decimal(12)
BATTERY = Decimal(63)

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
    """GET /dashboard with a Cognito JWT. `groups` is passed as given (a list, a
    string, or a tuple turned into a list); `claims` overrides claims; `top` sets
    top-level keys (queryStringParameters, body). No query string or body by default."""
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
        "routeKey": "GET /dashboard",
        "rawPath": "/dashboard",
        "rawQueryString": "",
        "headers": {"authorization": "Bearer synthetic-token"},
        "requestContext": {
            "http": {"method": "GET", "path": "/dashboard"},
            "requestId": REQUEST_ID,
            "routeKey": "GET /dashboard",
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
    home_id: Any = HOME_A,
    photo_key: str = PHOTO_A,
) -> None:
    """A registry PROFILE item, with a photo_key and notes the route never returns."""
    item = {
        "pk": f"RESIDENT#{resident_id}",
        "sk": "PROFILE",
        "entity_type": "RESIDENT",
        "resident_id": resident_id,
        "display_name": display_name,
        "home_id": home_id,
        "photo_key": photo_key,
        "medical_notes": PROFILE_NOTE,
        "year_of_birth": 1941,
    }
    put_raw(ddb, os.environ["REGISTRY_TABLE"], item)


def put_device(
    ddb,
    device_id: str = WEARABLE,
    resident_id: str | None = RESIDENT_A,
    home_id: str | None = HOME_A,
    pk: str | None = None,
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
    put_raw(ddb, os.environ["REGISTRY_TABLE"], item)


def put_thresholds(ddb, resident_id: str = RESIDENT_A, **limits: Any) -> None:
    """A THRESHOLDS item, with updated_by (a sub) the route must never read."""
    item: dict[str, Any] = {
        "pk": f"THRESHOLDS#{resident_id}",
        "sk": "THRESHOLDS",
        "updated_at": ASSIGNED_AT,
        "updated_by": UPDATED_BY_SUB,
        "updated_by_name": UPDATED_BY_NAME,
    }
    item.update(limits)
    put_raw(ddb, os.environ["REGISTRY_TABLE"], item)


def _common_entry(ts: str) -> dict[str, Any]:
    return {
        "message_id": MESSAGE_ID,
        "ts": ts,
        "received_at": LATEST_RECEIVED_AT,
        "clock_skew": CLOCK_SKEW_SENTINEL,
    }


def status_entry(device_id: str = WEARABLE, **fields: Any) -> dict[str, Any]:
    """A newest status entry sent 1 minute before NOW: a worn, not charging wearable
    at 63 % (a bedroom unit at 87 %). Fields override."""
    entry = _common_entry(fields.pop("ts", ago(minutes=1)))
    entry["charging"] = False
    if device_id.startswith("wearable"):
        entry.update({"battery_pct": BATTERY, "worn": True, "fw_version": "9.8.7-test"})
    else:
        entry.update({"battery_pct": Decimal(87), "fw_version": "9.8.6-test"})
    entry.update(fields)
    return entry


def _put_latest(ddb, device_id: str, watch_type: str, entry: dict[str, Any]) -> None:
    older = {**entry, "message_id": "00000000-0000-4000-8000-0000000000ab"}
    item: dict[str, Any] = {
        "pk": f"DEVICE#{device_id}",
        "sk": f"MSG#{watch_type}",
        "device_type": device_id.split("-", 1)[0],
        "entries": [entry, older, older, older, older],
        "latest": entry,
        "latest_ts": entry["ts"] if isinstance(entry.get("ts"), str) else "x",
        "version": 5,
        "watch_type": watch_type,
    }
    put_raw(ddb, os.environ["LATEST_TABLE"], item)


def put_status(ddb, device_id: str = WEARABLE, **fields: Any) -> dict[str, Any]:
    """A MSG#status item as device-health writes it."""
    entry = status_entry(device_id, **fields)
    _put_latest(ddb, device_id, "status", entry)
    return entry


def vitals_entry(**fields: Any) -> dict[str, Any]:
    """A newest vitals entry sent 1 minute before NOW, all values normal by default."""
    entry = _common_entry(fields.pop("ts", ago(minutes=1)))
    entry.update(
        {"hr_bpm": HR, "spo2_pct": SPO2, "skin_temp_c": SKIN, "activity_pct": ACTIVITY}
    )
    entry.update(fields)
    return entry


def put_vitals(ddb, device_id: str = WEARABLE, **fields: Any) -> dict[str, Any]:
    entry = vitals_entry(**fields)
    _put_latest(ddb, device_id, "vitals", entry)
    return entry


def put_bed_and_motion(ddb, device_id: str = BEDROOM) -> None:
    """bed and motion items with sentinels: the dashboards never read them."""
    bed = {**_common_entry(ago(minutes=1)), "occupied": BED_SENTINEL}
    motion = {**_common_entry(ago(minutes=1)), "zone": MOTION_SENTINEL}
    _put_latest(ddb, device_id, "bed", bed)
    _put_latest(ddb, device_id, "motion", motion)


def alert_item(
    resident_id: str = RESIDENT_A,
    alert_id: str = ALERT_1,
    alert_type: Any = "hr_high",
    severity: Any = "warning",
    status: Any = "open",
    event_ts: Any = None,
    device_id: Any = WEARABLE,
    notified_at: Any = NOTIFIED_AT,
    sk: str | None = None,
    pk: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """An alerts item as alert-processor writes it, still on the open list. `None`
    leaves an attribute out. `event_ts` defaults to 10 minutes before NOW."""
    event_ts = ago(minutes=10) if event_ts is None else event_ts
    item: dict[str, Any] = {
        "pk": pk or f"RESIDENT#{resident_id}",
        "sk": sk or f"ALERT#{event_ts}#{alert_id}",
        "alert_id": alert_id,
        "alert_type": alert_type,
        "severity": severity,
        "device_id": device_id,
        "event_ts": event_ts,
        "received_at": ALERT_RECEIVED_AT,
        "created_at": ALERT_CREATED_AT,
        "status": status,
        "source_message_id": SOURCE_MESSAGE_ID,
        "details": {
            "metric": "hr_bpm",
            "value": Decimal(131),
            "limit": Decimal(120),
            "direction": "above",
            "text": DETAILS_SENTINEL,
        },
        "notified_at": notified_at,
        "open_pk": "OPEN",
    }
    item.update(fields)
    return {k: v for k, v in item.items() if v is not None}


def put_alert(ddb, **kwargs: Any) -> dict[str, Any]:
    item = alert_item(**kwargs)
    put_raw(ddb, os.environ["ALERTS_TABLE"], item)
    return item


# ---- home items (docs/features/alerts-map.md) ----

# Distinctive synthetic values (inside the nature-reserve box), easy to search for in
# logs.
HOME_ADDRESS = "Test address qz-5521 (synthetic)"
HOME_LAT = Decimal("1.3612")
HOME_LNG = Decimal("103.8031")


def put_home(
    ddb,
    home_id: str = HOME_A,
    address: Any = HOME_ADDRESS,
    lat: Any = HOME_LAT,
    lng: Any = HOME_LNG,
) -> None:
    """A registry HOME item (`None` leaves an attribute out), with timestamps the route
    never returns."""
    item = {
        "pk": f"HOME#{home_id}",
        "sk": "PROFILE",
        "home_id": home_id,
        "address": address,
        "lat": lat,
        "lng": lng,
        "created_at": ASSIGNED_AT,
        "updated_at": ASSIGNED_AT,
    }
    put_raw(
        ddb,
        os.environ["REGISTRY_TABLE"],
        {k: v for k, v in item.items() if v is not None},
    )
