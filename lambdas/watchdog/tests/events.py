"""Builders for the watchdog's inputs. All IDs and values are synthetic and fixed, so
log checks never meet a random UUID.

"Now" is 2026-11-04 04:00:00 UTC = 12:00 SGT (inside the not-worn hours).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import checks
from boto3.dynamodb.types import TypeSerializer
from conftest import ALERTS_TABLE, LATEST_TABLE, REGISTRY_TABLE

NOW = datetime(2026, 11, 4, 4, 0, 0, tzinfo=UTC)
NOW_ISO = "2026-11-04T04:00:00.000Z"

WEARABLE = "wearable-0001"
WEARABLE_2 = "wearable-0002"
BEDROOM = "bedroom-0001"
RESIDENT = "res-0001"
RESIDENT_2 = "res-0002"

STATUS_ID = "00000000-0000-4000-8000-000000000001"
ALERT_ID = "6f1c2e0a-5b3d-5e8f-9a47-0000000000a1"
ALERT_ID_2 = "6f1c2e0a-5b3d-5e8f-9a47-0000000000a2"

_serializer = TypeSerializer()


def iso(value: datetime) -> str:
    return checks.format_iso_ms(value)


def ago(**delta: float) -> str:
    """ISO ts `delta` before NOW."""
    return iso(NOW - timedelta(**delta))


def _ser(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _serializer.serialize(v) for k, v in item.items()}


# ---- pure objects for checks.py ----


def device(device_id: str = WEARABLE, resident_id: str = RESIDENT) -> checks.Device:
    return checks.Device(device_id, device_id.split("-", 1)[0], resident_id)


def latest(device_id: str = WEARABLE, **fields: Any) -> dict[str, Any]:
    """A newest status entry: a worn, not charging wearable at 64 % (or a bedroom unit
    at 88 %), sent 1 minute before NOW. Fields override."""
    entry: dict[str, Any] = {
        "message_id": STATUS_ID,
        "ts": ago(minutes=1),
        "received_at": ago(minutes=1),
        "clock_skew": False,
        "fw_version": "1.0.0",
    }
    if device_id.startswith("wearable"):
        entry.update({"battery_pct": 64, "worn": True, "charging": False})
    else:
        entry.update({"battery_pct": 88})
    entry.update(fields)
    return entry


def status(device_id: str = WEARABLE, **fields: Any) -> checks.Status:
    entry = latest(device_id, **fields)
    return checks.Status(entry, entry["ts"], checks.parse_ts(entry["ts"]))


def open_alert(
    alert_type: str,
    device_id: str = WEARABLE,
    resident_id: str = RESIDENT,
    alert_id: str = ALERT_ID,
    event_ts: str | None = None,
) -> checks.OpenAlert:
    return checks.OpenAlert(
        alert_id, alert_type, device_id, resident_id, event_ts or ago(hours=1)
    )


# ---- DynamoDB items ----


def put_device(ddb, device_id: str, resident_id: str | None = RESIDENT) -> None:
    """A registry ASSIGNMENT item (or a spare without resident_id)."""
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
            }
        )
    ddb.put_item(TableName=REGISTRY_TABLE, Item=_ser(item))


def put_status(
    ddb, device_id: str, worn_false_since: str | None = None, **fields: Any
) -> dict[str, Any]:
    """A MSG#status item as device-health writes it, newest entry from `latest`."""
    entry = latest(device_id, **fields)
    item: dict[str, Any] = {
        "pk": f"DEVICE#{device_id}",
        "sk": "MSG#status",
        "device_type": device_id.split("-", 1)[0],
        "entries": [entry],
        "latest": entry,
        "latest_ts": entry["ts"],
        "version": 1,
        "watch_type": "status",
    }
    if worn_false_since is not None:
        item["worn_false_since"] = worn_false_since
    ddb.put_item(TableName=LATEST_TABLE, Item=_ser(item))
    return entry


def put_alert(
    ddb,
    alert_type: str,
    device_id: str = WEARABLE,
    resident_id: str = RESIDENT,
    alert_id: str = ALERT_ID,
    event_ts: str | None = None,
    status: str = "open",
    open_pk: bool = True,
) -> dict[str, Any]:
    """A silvercare-alerts item as alert-processor stores it (open_pk while open or
    acknowledged)."""
    event_ts = event_ts or ago(hours=1)
    item: dict[str, Any] = {
        "pk": f"RESIDENT#{resident_id}",
        "sk": f"ALERT#{event_ts}#{alert_id}",
        "alert_id": alert_id,
        "alert_type": alert_type,
        "severity": "warning",
        "device_id": device_id,
        "event_ts": event_ts,
        "status": status,
        "details": {"synthetic": True},
    }
    if open_pk:
        item["open_pk"] = "OPEN"
    ddb.put_item(TableName=ALERTS_TABLE, Item=_ser(item))
    return item
