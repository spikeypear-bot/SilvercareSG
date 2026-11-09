"""Device -> resident mapping from the registry (docs/features/curated-layer.md,
criteria 6-7). Pure functions, no AWS calls: handler.py reads the items.

Two reads make the mapping:

1. `by-type`, entity_type = DEVICE (pattern 4): every device's `pk`, and `resident_id`
   when it is assigned. Devices without `resident_id` are spares and are skipped.
2. BatchGetItem `pk = DEVICE#<device_id>`, `sk = ASSIGNMENT` (the curated-layer access
   pattern), for `assigned_at`, which the index doesn't project. The base item's
   `resident_id` and `device_type` are used, since the index can lag behind it.

Only validated values reach the SQL: `device_id` must match
`^(wearable|bedroom)-[0-9]{4}$`, `resident_id` `^res-[0-9]{4}$` (as `scripts/seed.py`),
and `assigned_at` must be an ISO 8601 UTC timestamp. A device's type is its ID prefix
(docs/metrics.md); an item whose `device_type` says otherwise is skipped as invalid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

DEVICE_PREFIX = "DEVICE#"
ASSIGNMENT_SK = "ASSIGNMENT"
WEARABLE = "wearable"
BEDROOM = "bedroom"

DEVICE_ID_RE = re.compile(r"(wearable|bedroom)-[0-9]{4}")
RESIDENT_ID_RE = re.compile(r"res-[0-9]{4}")
# ISO 8601 UTC, as the seed script and the API write it: 2026-11-01T03:00:00Z, with up
# to 6 fractional digits.
ASSIGNED_AT_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z"
)

# How a skipped device is logged when its own ID isn't valid: a free-text "ID" could
# carry anything.
INVALID_ID = "invalid"


@dataclass(frozen=True)
class Device:
    """One assigned device, every field validated."""

    device_id: str
    device_type: str
    resident_id: str
    assigned_at: datetime


def is_device_id(value: Any) -> bool:
    return isinstance(value, str) and DEVICE_ID_RE.fullmatch(value) is not None


def is_resident_id(value: Any) -> bool:
    return isinstance(value, str) and RESIDENT_ID_RE.fullmatch(value) is not None


def device_id_from_pk(pk: Any) -> str | None:
    """The device ID of a `DEVICE#<id>` key, or None if it isn't one."""
    if not isinstance(pk, str) or not pk.startswith(DEVICE_PREFIX):
        return None
    return pk.removeprefix(DEVICE_PREFIX)


def loggable_id(device_id: Any) -> str:
    """The device ID if it is well formed, else `invalid`."""
    return device_id if is_device_id(device_id) else INVALID_ID


def parse_assigned_at(value: Any) -> datetime | None:
    """An aware UTC datetime from an ISO 8601 UTC string, or None."""
    if not isinstance(value, str) or ASSIGNED_AT_RE.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class IndexScan:
    """What the by-type items gave: devices to look up, and skipped device IDs (each
    already made safe to log)."""

    assigned: list[str]
    unassigned: int
    invalid: list[str]


def assigned_from_index(items: list[dict[str, Any]]) -> IndexScan:
    """Device IDs with a `resident_id` on the index (criterion 6). Spares are counted,
    not returned; items whose device ID isn't valid are skipped (criterion 7)."""
    assigned: list[str] = []
    invalid: list[str] = []
    unassigned = 0
    seen: set[str] = set()
    for item in items:
        device_id = device_id_from_pk(item.get("pk"))
        if not is_device_id(device_id):
            invalid.append(INVALID_ID)
            continue
        if "resident_id" not in item:
            unassigned += 1
            continue
        if device_id in seen:
            continue
        seen.add(device_id)
        assigned.append(device_id)
    return IndexScan(assigned=assigned, unassigned=unassigned, invalid=invalid)


def device_from_item(item: dict[str, Any]) -> Device | None:
    """A Device from a deserialized ASSIGNMENT item, or None if any field fails
    validation or the device has been unassigned since the index was read."""
    device_id = device_id_from_pk(item.get("pk"))
    if not is_device_id(device_id):
        return None
    resident_id = item.get("resident_id")
    if not is_resident_id(resident_id):
        return None
    device_type = device_id.split("-", 1)[0]
    stored_type = item.get("device_type")
    if stored_type is not None and stored_type != device_type:
        return None
    assigned_at = parse_assigned_at(item.get("assigned_at"))
    if assigned_at is None:
        return None
    return Device(
        device_id=device_id,
        device_type=device_type,
        resident_id=resident_id,
        assigned_at=assigned_at,
    )
