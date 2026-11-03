"""Seed silvercare-registry with synthetic residents, thresholds, and device assignments
(docs/features/alerts-core.md, criterion 30; docs/features/vitals-alerts.md,
criterion 26; docs/data-model.md, pattern 17).

Run on the host with your own AWS credentials (region ap-southeast-1):

    pip install -r scripts/requirements.txt
    python scripts/seed-registry.py --dry-run     # print what it would write
    python scripts/seed-registry.py               # write

Creates:

- RESIDENT#res-0001 ... res-0003 / PROFILE: entity_type RESIDENT, display_name
  "Test Resident A" ... "C", home_id home-0001 ... home-0003, created_at, updated_at.
- THRESHOLDS#res-0002 / THRESHOLDS: spo2_pct {"min": 88} (a synthetic resident with
  a lower normal SpO2, to show personal thresholds; the other limits keep the
  defaults), updated_at. No updated_by (no staff user made it).
- DEVICE#<id> / ASSIGNMENT for every device in infra/devices/devices.json:
  entity_type DEVICE, device_type. wearable-000N and bedroom-000N (N = 1-3) are
  assigned to res-000N (resident_id, home_id, assigned_at, gsi1_pk, gsi1_sk); the
  rest are unassigned spares.

Every item is written with PutItem conditioned on attribute_not_exists(pk): an item
that already exists is left as it is and reported as "exists". Running it twice
changes nothing, and it never reassigns a device.

Prints only item keys and outcomes. Creates no AWS client when imported, so the tests
can load it without credentials.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

REGION = "ap-southeast-1"
DEFAULT_TABLE = "silvercare-registry"
DEFAULT_DEVICES_FILE = (
    Path(__file__).resolve().parent.parent / "infra" / "devices" / "devices.json"
)

DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-[0-9]{4}\Z")
TABLE_RE = re.compile(r"^[A-Za-z0-9_.-]{3,255}\Z")

# Obviously synthetic residents: (resident_id, display_name, home_id).
RESIDENTS = (
    ("res-0001", "Test Resident A", "home-0001"),
    ("res-0002", "Test Resident B", "home-0002"),
    ("res-0003", "Test Resident C", "home-0003"),
)

# Synthetic personal thresholds: resident_id -> limits (docs/data-model.md, "Resident
# thresholds"). Only the limits that differ from the defaults.
THRESHOLDS = {
    "res-0002": {"spo2_pct": {"min": 88}},
}

CREATED = "created"
EXISTS = "exists"
DRY_RUN = "dry-run"


# ---- pure helpers (unit tested, no network) ----


def iso_now(now: datetime) -> str:
    """ISO 8601 UTC to the second, e.g. 2026-10-18T03:00:00Z."""
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_devices(path: Path) -> list[dict[str, str]]:
    """[{"id", "type"}] from devices.json. Raises ValueError if it is malformed."""
    data = json.loads(path.read_text(encoding="utf-8"))
    devices = data.get("devices") if isinstance(data, dict) else None
    if not isinstance(devices, list):
        raise ValueError("devices.json must be an object with a devices list")  # noqa: TRY004 - bad file content
    result = []
    seen: set[str] = set()
    for device in devices:
        device_id = device.get("id") if isinstance(device, dict) else None
        if not isinstance(device_id, str) or not DEVICE_ID_RE.match(device_id):
            raise ValueError("every device id must match ^(wearable|bedroom)-[0-9]{4}$")
        device_type = device_id.split("-", 1)[0]
        if device.get("type") != device_type:
            raise ValueError(f"{device_id}: type must be {device_type}")
        if device_id in seen:
            raise ValueError(f"{device_id}: listed twice")
        seen.add(device_id)
        result.append({"id": device_id, "type": device_type})
    return result


def assignments() -> dict[str, tuple[str, str]]:
    """device_id -> (resident_id, home_id) for the assigned devices."""
    result = {}
    for n, (resident_id, _, home_id) in enumerate(RESIDENTS, start=1):
        for kind in ("wearable", "bedroom"):
            result[f"{kind}-{n:04d}"] = (resident_id, home_id)
    return result


def resident_items(now: datetime) -> list[dict[str, str]]:
    stamp = iso_now(now)
    return [
        {
            "pk": f"RESIDENT#{resident_id}",
            "sk": "PROFILE",
            "entity_type": "RESIDENT",
            "display_name": display_name,
            "home_id": home_id,
            "created_at": stamp,
            "updated_at": stamp,
        }
        for resident_id, display_name, home_id in RESIDENTS
    ]


def threshold_items(now: datetime) -> list[dict[str, Any]]:
    stamp = iso_now(now)
    return [
        {
            "pk": f"THRESHOLDS#{resident_id}",
            "sk": "THRESHOLDS",
            **limits,
            "updated_at": stamp,
        }
        for resident_id, limits in THRESHOLDS.items()
    ]


def device_items(devices: list[dict[str, str]], now: datetime) -> list[dict[str, str]]:
    stamp = iso_now(now)
    assigned = assignments()
    items = []
    for device in devices:
        item = {
            "pk": f"DEVICE#{device['id']}",
            "sk": "ASSIGNMENT",
            "entity_type": "DEVICE",
            "device_type": device["type"],
        }
        if device["id"] in assigned:
            resident_id, home_id = assigned[device["id"]]
            item.update(
                {
                    "resident_id": resident_id,
                    "home_id": home_id,
                    "assigned_at": stamp,
                    "gsi1_pk": f"RESIDENT#{resident_id}",
                    "gsi1_sk": f"DEVICE#{device['id']}",
                }
            )
        items.append(item)
    return items


def build_items(devices: list[dict[str, str]], now: datetime) -> list[dict[str, Any]]:
    """Every item to write: residents, then thresholds, then devices in devices.json
    order."""
    return resident_items(now) + threshold_items(now) + device_items(devices, now)


def _attribute(value: Any) -> dict[str, Any]:
    """One low-level DynamoDB attribute: strings, integers, and maps of them."""
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"N": str(value)}
    if isinstance(value, dict):
        return {"M": {name: _attribute(v) for name, v in value.items()}}
    raise TypeError(f"unsupported attribute type {type(value).__name__}")


def serialize(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Low-level DynamoDB format (strings as S, integers as N, maps as M)."""
    return {name: _attribute(value) for name, value in item.items()}


def key_text(item: dict[str, Any]) -> str:
    return f"{item['pk']} / {item['sk']}"


# ---- AWS ----


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if isinstance(code, str):
            return code
    return type(exc).__name__


def put_if_absent(client: Any, table: str, item: dict[str, Any]) -> str:
    """created, or exists if an item with this key is already there."""
    try:
        client.put_item(
            TableName=table,
            Item=serialize(item),
            ConditionExpression="attribute_not_exists(pk)",
        )
    except Exception as exc:
        if _error_code(exc) == "ConditionalCheckFailedException":
            return EXISTS
        raise
    return CREATED


def seed(
    items: list[dict[str, Any]],
    table: str,
    client: Any | None,
    out: TextIO,
) -> dict[str, int]:
    """Write (or, with client None, only print) every item. Returns outcome counts."""
    counts = {CREATED: 0, EXISTS: 0, DRY_RUN: 0}
    for item in items:
        outcome = DRY_RUN if client is None else put_if_absent(client, table, item)
        counts[outcome] += 1
        print(f"{key_text(item)}: {outcome}", file=out)
    return counts


def main(
    argv: list[str] | None = None,
    client: Any | None = None,
    out: TextIO | None = None,
    now: datetime | None = None,
) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(
        description="Seed silvercare-registry with synthetic residents, thresholds, and devices."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the item keys instead of writing",
    )
    parser.add_argument("--table", default=DEFAULT_TABLE, help="registry table name")
    parser.add_argument(
        "--devices-file",
        type=Path,
        default=DEFAULT_DEVICES_FILE,
        help="device list (default: infra/devices/devices.json)",
    )
    args = parser.parse_args(argv)

    if not TABLE_RE.match(args.table):
        print("error: invalid table name", file=out)
        return 2
    try:
        devices = load_devices(args.devices_file)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=out)
        return 2

    items = build_items(devices, now or datetime.now(UTC))

    if not args.dry_run and client is None:
        import boto3  # only when run for real, never at import

        client = boto3.client("dynamodb", region_name=REGION)

    try:
        counts = seed(items, args.table, None if args.dry_run else client, out)
    except Exception as exc:  # noqa: BLE001 - report the AWS error code only
        print(f"error: {_error_code(exc)}", file=out)
        return 1

    summary = ", ".join(f"{count} {name}" for name, count in counts.items() if count)
    print(f"done: {summary}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
