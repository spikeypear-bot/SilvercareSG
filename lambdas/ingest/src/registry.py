"""Device -> resident lookup in silvercare-registry (docs/data-model.md, pattern 1).

GetItem `pk = DEVICE#<device_id>`, `sk = ASSIGNMENT`, eventually consistent, reading
only `resident_id`. A missing item, or one without a string `resident_id`, means the
device is unassigned (docs/features/alerts-core.md, criterion 6). Every result,
including "unassigned", is cached per container for 5 minutes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

UNASSIGNED = "unassigned"
CACHE_TTL_S = 300.0

# device_id -> (resident_id, monotonic time the entry expires)
_cache: dict[str, tuple[str, float]] = {}


def clear_cache() -> None:
    _cache.clear()


def resident_from_item(item: dict[str, Any] | None) -> str:
    """resident_id from a low-level GetItem item, or UNASSIGNED."""
    if not item:
        return UNASSIGNED
    value = item.get("resident_id", {}).get("S")
    if not isinstance(value, str) or not value:
        return UNASSIGNED
    return value


def resident_for_device(
    client: Any,
    table_name: str,
    device_id: str,
    clock: Callable[[], float] = time.monotonic,
    fresh: bool = False,
) -> str:
    """The device's resident_id or UNASSIGNED. AWS errors are raised as they are.

    fresh=True skips the cache (still refreshing it): an "I'm OK" cancel can close the
    resident's possible-fall alerts too, so a just-reassigned wearable must not act for
    its previous resident (docs/features/possible-fall.md)."""
    now = clock()
    cached = _cache.get(device_id)
    if not fresh and cached is not None and cached[1] > now:
        return cached[0]

    response = client.get_item(
        TableName=table_name,
        Key={"pk": {"S": f"DEVICE#{device_id}"}, "sk": {"S": "ASSIGNMENT"}},
        ProjectionExpression="resident_id",
    )
    resident_id = resident_from_item(response.get("Item"))
    _cache[device_id] = (resident_id, now + CACHE_TTL_S)
    return resident_id
