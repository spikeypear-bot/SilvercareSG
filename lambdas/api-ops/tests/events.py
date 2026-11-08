"""Builders for GET /ops/device-problems inputs. All IDs and values are synthetic and
fixed (except Cognito subs, which moto generates). Health alerts carry sentinels in
`details` and `ack_note` so a test can search the whole response body for them.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import TypeSerializer

SUB = "00000000-0000-4000-8000-000000000005"
REQUEST_ID = "JpvNMgAbSQ0EJcQ="
EMAIL = "test.ops.zq@example.invalid"
NAME = "Test Ops Name Zq"

RESIDENT_A = "res-0001"
RESIDENT_B = "res-0002"
WEARABLE = "wearable-0001"
WEARABLE_2 = "wearable-0002"
BEDROOM = "bedroom-0001"

# Device problems.
OFFLINE = "6f1c2e0a-5b3d-5e8f-9a47-0000000000d1"
BATTERY = "6f1c2e0a-5b3d-5e8f-9a47-0000000000d2"
NOT_WORN = "6f1c2e0a-5b3d-5e8f-9a47-0000000000d3"
# Health alerts: none of these may ever appear in an ops response.
FALL = "6f1c2e0a-5b3d-5e8f-9a47-0000000000f1"
HR_HIGH = "6f1c2e0a-5b3d-5e8f-9a47-0000000000f2"
SPO2_LOW = "6f1c2e0a-5b3d-5e8f-9a47-0000000000f3"
POSSIBLE_FALL = "6f1c2e0a-5b3d-5e8f-9a47-0000000000f4"
SKIN_HIGH = "6f1c2e0a-5b3d-5e8f-9a47-0000000000f5"
HEALTH_IDS = (FALL, HR_HIGH, SPO2_LOW, POSSIBLE_FALL, SKIN_HIGH)
HEALTH_TYPES = ("fall", "hr_high", "spo2_low", "possible_fall", "skin_temp_high")

HEALTH_DETAILS_SENTINEL = "health-details-sentinel-qx"
HEALTH_ACK_NOTE = "Synthetic nurse note qx-9911"
DEVICE_DETAILS_EXTRA = "undocumented-details-sentinel-qx"
CAREGIVER_EMAIL = "test.caregiver.a@example.invalid"
CAREGIVER_NAME = "Test Caregiver A"
TS = "2026-11-04T03:00:00.000Z"

_serializer = TypeSerializer()


def ts(minute: int) -> str:
    return f"2026-11-04T03:{minute:02d}:00.000Z"


def api_event(
    groups: Any = ("ops",),
    claims: dict[str, Any] | None = None,
    drop_groups: bool = False,
    **top: Any,
) -> dict[str, Any]:
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
        "routeKey": "GET /ops/device-problems",
        "rawPath": "/ops/device-problems",
        "rawQueryString": "",
        "headers": {"authorization": "Bearer synthetic-token"},
        "requestContext": {
            "http": {"method": "GET", "path": "/ops/device-problems"},
            "requestId": REQUEST_ID,
            "routeKey": "GET /ops/device-problems",
            "stage": "$default",
            "authorizer": {"jwt": {"claims": jwt_claims, "scopes": None}},
        },
        "isBase64Encoded": False,
    }
    event.update(top)
    return event


def _ser(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _serializer.serialize(v) for k, v in item.items()}


def put_alert(
    ddb,
    *,
    alert_id: str,
    alert_type: str,
    resident_id: str = RESIDENT_A,
    device_id: str = WEARABLE,
    event_ts: str = TS,
    status: str = "open",
    severity: str = "warning",
    details: dict[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """An alerts item as alert-processor writes it, on the open list."""
    item: dict[str, Any] = {
        "pk": f"RESIDENT#{resident_id}",
        "sk": f"ALERT#{event_ts}#{alert_id}",
        "alert_id": alert_id,
        "alert_type": alert_type,
        "severity": severity,
        "device_id": device_id,
        "event_ts": event_ts,
        "status": status,
        "notified_at": event_ts,
        "source_message_id": "00000000-0000-4000-8000-0000000000cc",
        "open_pk": "OPEN",
    }
    if details is not None:
        item["details"] = details
    item.update(fields)
    ddb.put_item(TableName=os.environ["ALERTS_TABLE"], Item=_ser(item))
    return item


def put_health_alerts(ddb, resident_id: str = RESIDENT_A) -> None:
    """One of every kind of health alert, acknowledged by a nurse with a note."""
    for minute, (alert_id, alert_type) in enumerate(
        zip(HEALTH_IDS, HEALTH_TYPES, strict=True), start=30
    ):
        put_alert(
            ddb,
            alert_id=alert_id,
            alert_type=alert_type,
            resident_id=resident_id,
            event_ts=ts(minute),
            severity="critical",
            status="acknowledged",
            acknowledged_at=ts(minute + 1),
            acknowledged_by="00000000-0000-4000-8000-000000000002",
            ack_note=HEALTH_ACK_NOTE,
            details={
                "metric": "hr_bpm",
                "value": Decimal(150),
                "text": HEALTH_DETAILS_SENTINEL,
            },
        )


def put_device_problems(ddb) -> None:
    """One of each device problem, across two residents."""
    put_alert(
        ddb,
        alert_id=OFFLINE,
        alert_type="device_offline",
        device_id=BEDROOM,
        event_ts=ts(10),
        details={"last_seen": ts(1), "grouped": True, "extra": DEVICE_DETAILS_EXTRA},
    )
    put_alert(
        ddb,
        alert_id=BATTERY,
        alert_type="battery_low",
        resident_id=RESIDENT_B,
        device_id=WEARABLE_2,
        event_ts=ts(20),
        details={"battery_pct": Decimal(15)},
    )
    put_alert(
        ddb,
        alert_id=NOT_WORN,
        alert_type="not_worn",
        event_ts=ts(25),
        details={"since": ts(5)},
    )
