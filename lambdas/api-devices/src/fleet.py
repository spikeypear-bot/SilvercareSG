"""Pure logic for GET /devices (docs/data-model.md, Devices view): no AWS calls.

Builds the response from the three reads the handler makes (patterns 4, 5, 8). Every
device dict is built field by field with exactly the keys the data model fixes, so an
attribute the indexes project but the view doesn't need (`photo_key`, `worn`,
`received_at`, `clock_skew`, `message_id`) can never reach the response.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

WEARABLE = "wearable"
BEDROOM = "bedroom"

# As the watchdog (docs/features/watchdog.md, criterion 4): offline after more than
# 15 minutes with no status, so exactly 15 minutes is still online.
OFFLINE_AFTER = timedelta(minutes=15)

DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-[0-9]{4}\Z")
RESIDENT_ID_RE = re.compile(r"^res-[0-9]{4}\Z")
HOME_ID_RE = re.compile(r"^home-[0-9]{4}\Z")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
FW_VERSION_MAX_LEN = 32  # docs/metrics.md, status
DISPLAY_NAME_MAX_LEN = 100

# The keys of one device and of its assigned_to, exactly (data model, Devices view).
DEVICE_KEYS = (
    "device_id",
    "device_type",
    "online",
    "last_seen",
    "battery_pct",
    "charging",
    "fw_version",
    "assigned_to",
)
ASSIGNED_TO_KEYS = ("resident_id", "display_name", "home_id")


@dataclass(frozen=True)
class Device:
    device_id: str
    device_type: str
    resident_id: str | None
    home_id: str | None


@dataclass(frozen=True)
class Status:
    last_seen: str
    last_seen_dt: datetime
    battery_pct: int | None
    charging: bool
    fw_version: str | None


def parse_ts(value: Any) -> datetime | None:
    """UTC datetime of an ISO 8601 ms Z string, or None if invalid."""
    if type(value) is not str or not TS_RE.match(value):
        return None
    try:
        return datetime.strptime(value, TS_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _device_id(pk: Any) -> str | None:
    if not isinstance(pk, str) or not pk.startswith("DEVICE#"):
        return None
    device_id = pk.removeprefix("DEVICE#")
    return device_id if DEVICE_ID_RE.match(device_id) else None


def device_from_item(item: Mapping[str, Any]) -> Device | None:
    """A device from a registry by-type DEVICE item, or None if it's malformed (an
    invalid ID, or a resident_id that is present but not an opaque res-NNNN)."""
    device_id = _device_id(item.get("pk"))
    if device_id is None:
        return None
    device_type = item.get("device_type")
    if device_type not in (WEARABLE, BEDROOM):
        # The ID prefix is the type (docs/metrics.md).
        device_type = device_id.split("-", 1)[0]
    resident_id = item.get("resident_id")
    if resident_id is None:
        return Device(device_id, device_type, None, None)
    if not isinstance(resident_id, str) or not RESIDENT_ID_RE.match(resident_id):
        return None
    home_id = item.get("home_id")
    if not isinstance(home_id, str) or not HOME_ID_RE.match(home_id):
        home_id = None
    return Device(device_id, device_type, resident_id, home_id)


def resident_name_from_item(item: Mapping[str, Any]) -> tuple[str, str] | None:
    """(resident_id, display_name) from a registry by-type RESIDENT item, or None."""
    pk = item.get("pk")
    if not isinstance(pk, str) or not pk.startswith("RESIDENT#"):
        return None
    resident_id = pk.removeprefix("RESIDENT#")
    name = item.get("display_name")
    if not RESIDENT_ID_RE.match(resident_id) or not isinstance(name, str):
        return None
    name = name.strip()
    if not name or len(name) > DISPLAY_NAME_MAX_LEN or not name.isprintable():
        return None
    return resident_id, name


def _battery(value: Any) -> int | None:
    # DynamoDB numbers arrive as Decimal; bool is an int subclass, so reject it first.
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return None
    if value != int(value) or not 0 <= value <= 100:
        return None
    return int(value)


def _fw_version(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if len(value) > FW_VERSION_MAX_LEN or not value.isprintable():
        return None
    return value


def status_from_item(item: Mapping[str, Any]) -> tuple[str, Status] | None:
    """(device_id, newest status) from a latest by-message `status` item, or None if
    its key or `ts` is invalid. Invalid optional fields become None / False."""
    device_id = _device_id(item.get("pk"))
    latest = item.get("latest")
    if device_id is None or not isinstance(latest, Mapping):
        return None
    last_seen = latest.get("ts")
    last_seen_dt = parse_ts(last_seen)
    if last_seen_dt is None:
        return None
    return device_id, Status(
        last_seen=last_seen,
        last_seen_dt=last_seen_dt,
        battery_pct=_battery(latest.get("battery_pct")),
        charging=latest.get("charging") is True,
        fw_version=_fw_version(latest.get("fw_version")),
    )


def is_online(status: Status | None, now: datetime) -> bool:
    return status is not None and now - status.last_seen_dt <= OFFLINE_AFTER


def device_view(
    device: Device, status: Status | None, names: Mapping[str, str], now: datetime
) -> dict[str, Any]:
    """One device of the response, with exactly DEVICE_KEYS."""
    assigned_to = None
    if device.resident_id is not None:
        assigned_to = {
            "resident_id": device.resident_id,
            # None only if the resident has no valid profile item.
            "display_name": names.get(device.resident_id),
            "home_id": device.home_id,
        }
    return {
        "device_id": device.device_id,
        "device_type": device.device_type,
        "online": is_online(status, now),
        "last_seen": status.last_seen if status else None,
        "battery_pct": status.battery_pct if status else None,
        # Bedroom units have no charger (docs/metrics.md): always false.
        "charging": bool(status and status.charging and device.device_type == WEARABLE),
        "fw_version": status.fw_version if status else None,
        "assigned_to": assigned_to,
    }


@dataclass(frozen=True)
class Fleet:
    devices: list[dict[str, Any]]
    skipped: int


def build_fleet(
    device_items: Iterable[Mapping[str, Any]],
    status_items: Iterable[Mapping[str, Any]],
    resident_items: Iterable[Mapping[str, Any]],
    now: datetime,
) -> Fleet:
    """Every valid device, sorted by device_id, with its newest status and, if
    assigned, its resident's name. Names are kept only for residents on a device, so
    residents without one are never returned. Status items of devices that aren't in
    the registry are ignored."""
    devices: dict[str, Device] = {}
    skipped = 0
    for item in device_items:
        device = device_from_item(item)
        if device is None:
            skipped += 1
        else:
            devices[device.device_id] = device

    statuses: dict[str, Status] = {}
    for item in status_items:
        parsed = status_from_item(item)
        if parsed is not None and parsed[0] in devices:
            statuses[parsed[0]] = parsed[1]

    wanted = {d.resident_id for d in devices.values() if d.resident_id is not None}
    names: dict[str, str] = {}
    for item in resident_items:
        parsed = resident_name_from_item(item)
        if parsed is not None and parsed[0] in wanted:
            names[parsed[0]] = parsed[1]

    views = [
        device_view(devices[device_id], statuses.get(device_id), names, now)
        for device_id in sorted(devices)
    ]
    return Fleet(views, skipped)
