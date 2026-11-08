"""Registry items for the shadow-sync tests. All IDs and values are synthetic and
fixed, so log checks never meet a random UUID."""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import TypeSerializer

RESIDENT_A = "res-0001"
RESIDENT_B = "res-0002"
WEARABLE_1 = "wearable-0001"
WEARABLE_2 = "wearable-0002"
WEARABLE_3 = "wearable-0003"
BEDROOM = "bedroom-0001"

FIXED = {"hr_bpm": {"min": 45, "max": 130}, "spo2_pct": {"min": 88}}
DEFAULTS = {"hr_bpm": {"min": 50, "max": 120}, "spo2_pct": {"min": 92}}

_serializer = TypeSerializer()


def put_raw(ddb, item: dict[str, Any]) -> None:
    ddb.put_item(
        TableName=os.environ["REGISTRY_TABLE"],
        Item={k: _serializer.serialize(v) for k, v in item.items()},
    )


def put_device(
    ddb,
    device_id: str,
    resident_id: Any = RESIDENT_A,
    pk: str | None = None,
    **fields: Any,
) -> None:
    """A registry ASSIGNMENT item (a spare when resident_id is None)."""
    item: dict[str, Any] = {
        "pk": pk or f"DEVICE#{device_id}",
        "sk": "ASSIGNMENT",
        "entity_type": "DEVICE",
        "device_type": device_id.split("-", 1)[0],
        "assigned_at": "2026-10-18T03:00:00Z",
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
    item.update(fields)
    put_raw(ddb, item)


def put_thresholds(
    ddb,
    resident_id: str = RESIDENT_A,
    hr: tuple[Any, Any] = (50, 120),
    spo2: Any = 92,
    skin: tuple[Any, Any] = (Decimal("30.0"), Decimal("38.0")),
) -> None:
    put_raw(
        ddb,
        {
            "pk": f"THRESHOLDS#{resident_id}",
            "sk": "THRESHOLDS",
            "entity_type": "THRESHOLDS",
            "hr_bpm": {"min": hr[0], "max": hr[1]},
            "spo2_pct": {"min": spo2},
            "skin_temp_c": {"min": skin[0], "max": skin[1]},
            "updated_at": "2026-11-01T00:00:00.000Z",
        },
    )


def limits(hr_min: int, hr_max: int, spo2_min: int) -> dict:
    return {"hr_bpm": {"min": hr_min, "max": hr_max}, "spo2_pct": {"min": spo2_min}}
