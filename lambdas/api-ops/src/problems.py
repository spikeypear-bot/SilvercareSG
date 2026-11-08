"""Pure rules for GET /ops/device-problems (docs/data-model.md, Access patterns, the
**ops** paragraph): which alerts an ops user may see and the exact response. No AWS
calls.

ops see **only device problems** (`device_offline`, `battery_low`, `not_worn`), for
every resident. The handler filters twice: a FilterExpression on the `open` index
Query, and `is_device_problem` here on every index item and again on every base-table
item. A health alert reaching an ops response is a privacy breach, so neither check
relies on the other.

The response per alert is built field by field from only: `pk` (as `resident_id`),
`sk`, `alert_id`, `alert_type`, `severity`, `device_id`, `event_ts`, `status`,
`details`, `acknowledged_at`, `acknowledged_by_role`, `acknowledged_by` ("the ops team"
or the caregiver's name, never a `sub`), `resolved_at`, `resolved_by_role`.
`details` keeps only the documented keys of each device-problem type (alerts table,
`details` row), so a field added to `details` later doesn't reach ops unreviewed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

DEVICE_PROBLEM_TYPES = ("device_offline", "battery_low", "not_worn")
OPS_TEAM = "the ops team"

# The documented `details` keys per device-problem type (docs/data-model.md, alerts
# table): device_offline {last_seen, grouped?}, battery_low {battery_pct}, not_worn
# {since}.
DETAILS_KEYS: Mapping[str, tuple[str, ...]] = {
    "device_offline": ("last_seen", "grouped"),
    "battery_low": ("battery_pct",),
    "not_worn": ("since",),
}

RESPONSE_KEYS = (
    "resident_id",
    "sk",
    "alert_id",
    "alert_type",
    "severity",
    "device_id",
    "event_ts",
    "status",
    "details",
    "acknowledged_at",
    "acknowledged_by_role",
    "acknowledged_by",
    "resolved_at",
    "resolved_by_role",
)

RESIDENT_ID_RE = re.compile(r"^(res-[0-9]{4}|unassigned)\Z")
ALERT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z\Z")
DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-[0-9]{4}\Z")
STATUSES = frozenset({"open", "acknowledged", "resolved", "cancelled"})
SEVERITIES = frozenset({"warning", "critical"})
ACK_ROLES = frozenset({"ops", "caregiver"})


def is_device_problem(item: Mapping[str, Any]) -> bool:
    """True only for the three device-problem types. Anything else, including a
    missing or non-string `alert_type`, is not shown to ops."""
    alert_type = item.get("alert_type")
    return isinstance(alert_type, str) and alert_type in DEVICE_PROBLEM_TYPES


@dataclass(frozen=True)
class Key:
    pk: str
    sk: str
    resident_id: str
    event_ts: str
    alert_id: str


def key_of(item: Mapping[str, Any]) -> Key | None:
    """The alert's key parts, or None if `pk` / `sk` are not an alert's."""
    pk, sk = item.get("pk"), item.get("sk")
    if not isinstance(pk, str) or not isinstance(sk, str):
        return None
    if not pk.startswith("RESIDENT#") or not sk.startswith("ALERT#"):
        return None
    resident_id = pk.removeprefix("RESIDENT#")
    event_ts, _, alert_id = sk.removeprefix("ALERT#").rpartition("#")
    if not RESIDENT_ID_RE.match(resident_id) or not ALERT_ID_RE.match(alert_id):
        return None
    if not TS_RE.match(event_ts):
        return None
    return Key(pk, sk, resident_id, event_ts, alert_id)


def _str(value: Any, pattern: re.Pattern[str] | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    if pattern is not None and not pattern.match(value):
        return None
    return value


def _one_of(value: Any, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _json_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    number = Decimal(value)
    return int(number) if number == number.to_integral_value() else float(number)


def clean_details(alert_type: str, details: Any) -> dict[str, Any] | None:
    """Only the documented keys of this type's `details`, with checked values."""
    if not isinstance(details, Mapping):
        return None
    out: dict[str, Any] = {}
    for key in DETAILS_KEYS.get(alert_type, ()):
        value = details.get(key)
        if key in ("last_seen", "since"):
            if (cleaned := _str(value, TS_RE)) is not None:
                out[key] = cleaned
        elif key == "grouped":
            if value is True:
                out[key] = True
        elif key == "battery_pct":
            number = _json_number(value)
            if number is not None and 0 <= number <= 100:
                out[key] = number
    return out


def acknowledged_by(
    role: str | None, sub: Any, names: Mapping[str, str | None]
) -> str | None:
    """ "the ops team" for ops; the caregiver's name (None if the lookup failed);
    None otherwise. Never the `sub`."""
    if role == "ops":
        return OPS_TEAM
    if role == "caregiver" and isinstance(sub, str):
        return names.get(sub)
    return None


def caregiver_subs(index_items: list[Mapping[str, Any]]) -> list[str]:
    """`acknowledged_by` of the device problems a caregiver acknowledged."""
    return sorted(
        {
            item["acknowledged_by"]
            for item in index_items
            if is_device_problem(item)
            and item.get("acknowledged_by_role") == "caregiver"
            and isinstance(item.get("acknowledged_by"), str)
        }
    )


@dataclass
class Built:
    alerts: list[dict[str, Any]]
    # Index or base items dropped because they were not device problems. Should be 0:
    # the FilterExpression already removes them. Logged as a count only.
    not_device_problems: int
    # Items dropped for a malformed key.
    invalid: int


def build(
    index_items: list[Mapping[str, Any]],
    base_items: Mapping[tuple[str, str], Mapping[str, Any]],
    names: Mapping[str, str | None],
) -> Built:
    """The ops response, in the index's order (newest first).

    `index_items` are pattern 9's items; `base_items` maps (pk, sk) to the base-table
    item read for `details` and `resolved_*`. An item is dropped if either copy is not
    a device problem (the type can't change, so a mismatch means something is wrong).
    """
    alerts: list[dict[str, Any]] = []
    not_device = 0
    invalid = 0
    for item in index_items:
        if not is_device_problem(item):
            not_device += 1
            continue
        key = key_of(item)
        if key is None:
            invalid += 1
            continue
        base = base_items.get((key.pk, key.sk), {})
        if base and not is_device_problem(base):
            not_device += 1
            continue
        alert_type = item["alert_type"]
        role = _one_of(item.get("acknowledged_by_role"), ACK_ROLES)
        alerts.append(
            {
                "resident_id": key.resident_id,
                "sk": key.sk,
                "alert_id": key.alert_id,
                "alert_type": alert_type,
                "severity": _one_of(item.get("severity"), SEVERITIES),
                "device_id": _str(item.get("device_id"), DEVICE_ID_RE),
                "event_ts": key.event_ts,
                "status": _one_of(item.get("status"), STATUSES),
                "details": clean_details(alert_type, base.get("details")),
                "acknowledged_at": _str(item.get("acknowledged_at"), TS_RE),
                "acknowledged_by_role": role,
                "acknowledged_by": acknowledged_by(
                    role, item.get("acknowledged_by"), names
                ),
                "resolved_at": _str(base.get("resolved_at"), TS_RE),
                "resolved_by_role": _one_of(
                    base.get("resolved_by_role"), frozenset({"system"})
                ),
            }
        )
    return Built(alerts, not_device, invalid)
