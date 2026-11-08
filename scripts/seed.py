"""Seed silvercare-registry and Cognito with synthetic test data (docs/features/seed-data.md).

Live account (run on the host with your own AWS credentials, region ap-southeast-1):

    pip install -r scripts/requirements.txt
    python scripts/seed.py                      # seed, asks you to type "seed"
    python scripts/seed.py --delete             # remove the seed, asks you to type "delete"
    python scripts/seed.py --reset-passwords    # seed and give all 3 test users new passwords

DynamoDB Local (dev container, no prompt, no Cognito or STS):

    make seed-local                             # python scripts/seed.py --local
    make seed-local ARGS=--delete

Every value comes from scripts/seed-data.json (20 residents, their thresholds, the 40
device assignments, 3 test users without passwords, and the user <-> resident links);
the device list is checked against infra/devices/devices.json. The seed file is
validated before any AWS client is created.

Live steps, after the confirmation prompt: create or update the 3 test users (one per
group, no email sent), write their new passwords to secrets/test-users.txt (merged,
written atomically, mode 600), then write the registry items with BatchWriteItem,
paced for the table's 1 WCU. Re-runs overwrite the seeded items.

Clean-up on every seed run (live and --local): after the links are written, seed links
(linked_by = "seed") to seeded residents that are no longer in the seed file are
deleted; links with any other linked_by are never touched. On the live account the
retired test user test-family@example.com (the family group was removed) is then
deleted if it still exists, and its line is removed from the passwords file. No other
user is deleted outside --delete.

--delete removes the seeded items, every link to a seeded resident with
linked_by = "seed", the 3 test users and the retired user, and their lines in the
passwords file.

Prints only steps, counts, and IDs: never item contents, names, or passwords. AWS
errors are shown as operation name and error code only. Exit codes: 0 success, 1 AWS
failure or aborted prompt, 2 usage or validation error.

Creates no AWS client when imported, so the tests can load it without credentials;
seed_local(client, table=...) lets integration tests seed their own DynamoDB Local
tables.
"""

from __future__ import annotations

import sys

# Before any import that needs 3.11 (datetime.UTC), so older Pythons get a clear message.
# Everything in this file must still parse on 3.9 so this check can run.
if sys.version_info < (3, 11):  # noqa: UP036
    print(
        "seed.py needs Python 3.11 or newer (you have"
        f" {sys.version_info[0]}.{sys.version_info[1]}); see README",
        file=sys.stderr,
    )
    sys.exit(2)

import argparse
import contextlib
import json
import os
import random
import re
import secrets
import string
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TextIO

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEED_FILE = REPO_ROOT / "scripts" / "seed-data.json"
DEFAULT_DEVICES_FILE = REPO_ROOT / "infra" / "devices" / "devices.json"
SECRETS_DIR = REPO_ROOT / "secrets"
DEFAULT_PASSWORDS_FILE = SECRETS_DIR / "test-users.txt"

REGION = "ap-southeast-1"
TABLE = "silvercare-registry"
USER_POOL_NAME = "silvercare-users"
DEFAULT_LOCAL_ENDPOINT = "http://dynamodb-local:8000"
SEED_MARKER = "seed"  # updated_by / linked_by on every seeded item that has one
# Every seeded device assignment's assigned_at, on every run (2026-02-01 00:00 SGT;
# docs/features/seed-data.md criterion 6): summaries count readings only from
# assigned_at, so backfilled history from earlier months is counted.
SEED_ASSIGNED_AT = "2026-01-31T16:00:00Z"

GROUPS = ("doctor", "nurse", "caregiver")
LINK_GROUPS = ("caregiver",)
# Test users earlier seeds created that this one no longer has (the family group was
# removed 2026-11-04): (user_id, exact username, the name the seed gave it, the groups
# it may be in). Deleted on every live seed run and on --delete, but only while it is
# still as the seed left it; nothing else is ever deleted by name outside --delete.
RETIRED_USERS: tuple[tuple[str, str, str, frozenset[str]], ...] = (
    ("test-family", "test-family@example.com", "Test Family", frozenset({"family"})),
)
DEVICE_TYPES = ("wearable", "bedroom")

# Writes are paced for the registry's 1 WCU (criterion 21).
BATCH_WRITE_MAX = 25
BATCH_GET_MAX = 100
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 20.0
MAX_ATTEMPTS_WITHOUT_PROGRESS = 10
CLIENT_MAX_ATTEMPTS = 10
THROTTLE_CODES = frozenset(
    {
        "ProvisionedThroughputExceededException",
        "ThrottlingException",
        "RequestLimitExceeded",
    }
)

# Pool policy (infra/cognito.tf): min 12, lower, upper, number, symbol. No spaces, so
# a password is one field in the passwords file.
PASSWORD_LENGTH = 20
PASSWORD_SYMBOLS = "!#%*+-.=?@^_~"
PASSWORD_ALPHABET = (
    string.ascii_lowercase + string.ascii_uppercase + string.digits + PASSWORD_SYMBOLS
)

RESIDENT_ID_RE = re.compile(r"^res-([0-9]{4})\Z")
DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-[0-9]{4}\Z")
USER_ID_RE = re.compile(r"^test-[a-z]+\Z")
EMAIL_RE = re.compile(r"^[a-z0-9.-]+@example\.com\Z")  # reserved domain, can't get mail
USER_POOL_ID_RE = re.compile(r"^ap-southeast-1_[0-9A-Za-z]+\Z")
NOTE_PREFIX = "Synthetic note: "
MAX_NOTES_LEN = 2000
MAX_CONTACTS = 2
YEAR_OF_BIRTH_RANGE = (1935, 1955)

# Reserved for synthetic seed data only (docs/data-model.md, Seed data): never given to
# real residents, homes, or users.
RESERVED_RESIDENT_NUMBERS = range(
    1, 21
)  # res-0001 ... res-0020, home-0001 ... home-0020
RESERVED_LOCAL_SUBS = frozenset(
    f"00000000-0000-4000-8000-{n:012d}" for n in range(1, 4)
)
# Synthetic markers every seeded personal-data field must carry.
DISPLAY_NAME_RE = re.compile(r"^Test Resident [A-Z]\Z")
CONTACT_NAME_RE = re.compile(r"^Test Contact [A-Z][0-9]\Z")
PHONE_RE = re.compile(r"^\+65 0000 [0-9]{4}\Z")
USER_NAME_RE = re.compile(r"^Test [A-Z][a-z]+\Z")

# Same keys, indexes, projections, and capacity as infra/dynamodb.tf (registry_table).
REGISTRY_TABLE_DEFINITION: dict[str, Any] = {
    "AttributeDefinitions": [
        {"AttributeName": name, "AttributeType": "S"}
        for name in ("pk", "sk", "entity_type", "gsi1_pk", "gsi1_sk")
    ],
    "KeySchema": [
        {"AttributeName": "pk", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"},
    ],
    "BillingMode": "PROVISIONED",
    "ProvisionedThroughput": {"ReadCapacityUnits": 3, "WriteCapacityUnits": 1},
    "GlobalSecondaryIndexes": [
        {
            "IndexName": "by-type",
            "KeySchema": [
                {"AttributeName": "entity_type", "KeyType": "HASH"},
                {"AttributeName": "pk", "KeyType": "RANGE"},
            ],
            "Projection": {
                "ProjectionType": "INCLUDE",
                "NonKeyAttributes": [
                    "display_name",
                    "home_id",
                    "photo_key",
                    "device_type",
                    "resident_id",
                ],
            },
            "ProvisionedThroughput": {"ReadCapacityUnits": 2, "WriteCapacityUnits": 1},
        },
        {
            "IndexName": "by-resident",
            "KeySchema": [
                {"AttributeName": "gsi1_pk", "KeyType": "HASH"},
                {"AttributeName": "gsi1_sk", "KeyType": "RANGE"},
            ],
            "Projection": {
                "ProjectionType": "INCLUDE",
                "NonKeyAttributes": ["device_type", "relationship"],
            },
            "ProvisionedThroughput": {"ReadCapacityUnits": 2, "WriteCapacityUnits": 1},
        },
    ],
}


class SeedError(Exception):
    """A step failed. The message holds IDs and counts only, never data or passwords."""


# ---- seed file (pure, unit tested) ----


def load_json(path: Path) -> Any:
    """Read a JSON file; numbers with a fraction become Decimal (DynamoDB N, no floats)."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle, parse_float=Decimal)


def load_seed(path: Path | str = DEFAULT_SEED_FILE) -> dict[str, Any]:
    """Load scripts/seed-data.json.

    Raises OSError, ValueError, or TypeError if it can't be read.
    """
    seed = load_json(Path(path))
    if not isinstance(seed, dict):
        raise TypeError("seed file must hold a JSON object")
    return seed


def load_devices(path: Path | str = DEFAULT_DEVICES_FILE) -> list[dict[str, Any]]:
    """Load infra/devices/devices.json (its "devices" list)."""
    data = load_json(Path(path))
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        raise TypeError('devices file must hold {"devices": [...]}')
    return data["devices"]


def _password_paths(value: Any, path: str = "") -> Iterator[str]:
    """Paths of every key named like "password" anywhere in value."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if "password" in str(key).lower():
                yield child_path
            yield from _password_paths(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _password_paths(child, f"{path}[{index}]")


def _is_number(value: Any) -> bool:
    return isinstance(value, int | Decimal) and not isinstance(value, bool)


def _id_or(value: Any, pattern: re.Pattern[str], fallback: str) -> str:
    """value if it is a string matching its ID pattern, else fallback. Problem messages
    never echo a raw value: a malformed "ID" could hold anything."""
    if isinstance(value, str) and pattern.match(value):
        return value
    return fallback


def _label(entry: Any, key: str, index: int, pattern: re.Pattern[str]) -> str:
    """The entry's ID if it matches its ID pattern, else its position (#N)."""
    value = entry.get(key) if isinstance(entry, dict) else None
    return _id_or(value, pattern, f"#{index + 1}")


def _section(seed: dict[str, Any], name: str, problems: list[str]) -> list[Any]:
    value = seed.get(name)
    if not isinstance(value, list):
        problems.append(f"seed file: {name} must be a list")
        return []
    return value


def _check_range(
    owner: str, name: str, value: Any, need_max: bool, problems: list[str]
) -> None:
    keys = ("min", "max") if need_max else ("min",)
    if not isinstance(value, dict) or set(value) != set(keys):
        problems.append(f"resident {owner}: thresholds.{name} needs exactly {keys}")
        return
    if not all(_is_number(value[key]) for key in keys):
        problems.append(f"resident {owner}: thresholds.{name} values must be numbers")
    elif need_max and value["min"] >= value["max"]:
        problems.append(f"resident {owner}: thresholds.{name} min must be below max")


def _validate_resident(resident: Any, label: str, problems: list[str]) -> None:
    if not isinstance(resident, dict):
        problems.append(f"resident {label}: must be an object")
        return
    resident_id = resident.get("resident_id")
    match = RESIDENT_ID_RE.match(resident_id) if isinstance(resident_id, str) else None
    if not match or int(match.group(1)) not in RESERVED_RESIDENT_NUMBERS:
        problems.append(
            f"resident {label}: resident_id must be one of the reserved IDs"
            " res-0001 to res-0020"
        )
    elif resident.get("home_id") != f"home-{match.group(1)}":
        problems.append(f"resident {label}: home_id must be home-{match.group(1)}")
    name = resident.get("display_name")
    if not isinstance(name, str) or not DISPLAY_NAME_RE.match(name):
        problems.append(
            f"resident {label}: display_name must be 'Test Resident <letter>'"
        )
    year = resident.get("year_of_birth")
    low, high = YEAR_OF_BIRTH_RANGE
    if not isinstance(year, int) or isinstance(year, bool) or not low <= year <= high:
        problems.append(f"resident {label}: year_of_birth must be {low} to {high}")
    notes = resident.get("medical_notes")
    if (
        not isinstance(notes, str)
        or not notes.startswith(NOTE_PREFIX)
        or len(notes) > MAX_NOTES_LEN
    ):
        problems.append(
            f"resident {label}: medical_notes must start with {NOTE_PREFIX!r}"
            f" and be at most {MAX_NOTES_LEN} characters"
        )
    contacts = resident.get("emergency_contacts")
    if not isinstance(contacts, list) or not 1 <= len(contacts) <= MAX_CONTACTS:
        problems.append(
            f"resident {label}: emergency_contacts must hold 1 to {MAX_CONTACTS} contacts"
        )
    else:
        for contact in contacts:
            if not isinstance(contact, dict) or set(contact) != {
                "name",
                "relationship",
                "phone",
            }:
                problems.append(
                    f"resident {label}: each contact needs exactly name,"
                    " relationship, phone"
                )
            elif not all(isinstance(v, str) and v for v in contact.values()):
                problems.append(f"resident {label}: contact fields must be strings")
            else:
                if not CONTACT_NAME_RE.match(contact["name"]):
                    problems.append(
                        f"resident {label}: contact name must be"
                        " 'Test Contact <letter><digit>'"
                    )
                if not PHONE_RE.match(contact["phone"]):
                    problems.append(
                        f"resident {label}: contact phone must be '+65 0000 NNNN'"
                    )
    thresholds = resident.get("thresholds")
    if not isinstance(thresholds, dict) or set(thresholds) != {
        "hr_bpm",
        "spo2_pct",
        "skin_temp_c",
    }:
        problems.append(
            f"resident {label}: thresholds needs exactly hr_bpm, spo2_pct, skin_temp_c"
        )
    else:
        _check_range(label, "hr_bpm", thresholds["hr_bpm"], True, problems)
        _check_range(label, "spo2_pct", thresholds["spo2_pct"], False, problems)
        _check_range(label, "skin_temp_c", thresholds["skin_temp_c"], True, problems)


def _validate_devices(
    seed_devices: list[Any],
    registry_devices: list[Any],
    resident_ids: set[str],
    problems: list[str],
) -> None:
    expected: dict[str, Any] = {}
    for entry in registry_devices:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            expected[entry["id"]] = entry.get("type")
    seen: set[str] = set()
    for index, entry in enumerate(seed_devices):
        label = _label(entry, "device_id", index, DEVICE_ID_RE)
        if not isinstance(entry, dict):
            problems.append(f"device {label}: must be an object")
            continue
        device_id = entry.get("device_id")
        if not isinstance(device_id, str) or not DEVICE_ID_RE.match(device_id):
            problems.append(
                f"device {label}: device_id must match (wearable|bedroom)-NNNN"
            )
            continue
        if device_id in seen:
            problems.append(f"device {device_id}: listed more than once")
            continue
        seen.add(device_id)
        if device_id not in expected:
            problems.append(f"device {device_id}: not in infra/devices/devices.json")
        elif entry.get("device_type") != expected[device_id]:
            problems.append(
                f"device {device_id}: device_type does not match"
                " infra/devices/devices.json"
            )
        resident_id = entry.get("resident_id")
        if not isinstance(resident_id, str) or resident_id not in resident_ids:
            problems.append(f"device {device_id}: assigned to an unknown resident")
    for device_id in sorted(set(expected) - seen):
        problems.append(
            f"device {device_id}: in infra/devices/devices.json but not seeded"
        )


def _validate_users(users: list[Any], problems: list[str]) -> dict[str, str]:
    """Returns user_id -> group for the valid users."""
    groups: dict[str, str] = {}
    seen_emails: set[str] = set()
    seen_groups: set[str] = set()
    seen_subs: set[str] = set()
    for index, user in enumerate(users):
        label = _label(user, "user_id", index, USER_ID_RE)
        if not isinstance(user, dict):
            problems.append(f"user {label}: must be an object")
            continue
        user_id = user.get("user_id")
        if not isinstance(user_id, str) or not USER_ID_RE.match(user_id):
            problems.append(f"user {label}: user_id must match test-<role>")
            continue
        if user_id in groups:
            problems.append(f"user {user_id}: listed more than once")
            continue
        email = user.get("email")
        if not isinstance(email, str) or not EMAIL_RE.match(email):
            problems.append(f"user {user_id}: email must be an @example.com address")
        elif email in seen_emails:
            problems.append(f"user {user_id}: email used by another user")
        elif email in {entry[1] for entry in RETIRED_USERS}:
            problems.append(f"user {user_id}: email belongs to a retired test user")
        else:
            seen_emails.add(email)
        name = user.get("name")
        if not isinstance(name, str) or not USER_NAME_RE.match(name):
            problems.append(f"user {user_id}: name must be 'Test <Role>'")
        group = user.get("group")
        if not isinstance(group, str) or group not in GROUPS:
            problems.append(f"user {user_id}: group must be one of {', '.join(GROUPS)}")
        elif group in seen_groups:
            problems.append(
                f"user {user_id}: group {group} has more than one test user"
            )
        else:
            seen_groups.add(group)
        sub = user.get("local_sub")
        if not isinstance(sub, str) or sub not in RESERVED_LOCAL_SUBS:
            problems.append(
                f"user {user_id}: local_sub must be one of the reserved"
                " 00000000-0000-4000-8000-00000000000N subs (N = 1 to 3)"
            )
        elif sub in seen_subs:
            problems.append(f"user {user_id}: local_sub used by another user")
        else:
            seen_subs.add(sub)
        groups[user_id] = group if isinstance(group, str) else ""
    return groups


def _validate_links(
    links: list[Any],
    user_groups: dict[str, str],
    resident_ids: set[str],
    problems: list[str],
) -> None:
    seen: set[tuple[str, str]] = set()
    for index, link in enumerate(links):
        if not isinstance(link, dict):
            problems.append(f"link #{index + 1}: must be an object")
            continue
        user_id = link.get("user_id")
        resident_id = link.get("resident_id")
        user_ok = isinstance(user_id, str)
        resident_ok = isinstance(resident_id, str)
        label = (
            f"link #{index + 1}"
            f" ({_id_or(user_id, USER_ID_RE, 'invalid user')}"
            f" -> {_id_or(resident_id, RESIDENT_ID_RE, 'invalid resident')})"
        )
        if not user_ok or user_id not in user_groups:
            problems.append(f"{label}: unknown user")
        elif user_groups[user_id] not in LINK_GROUPS:
            problems.append(f"{label}: only caregiver users can be linked")
        if not resident_ok or resident_id not in resident_ids:
            problems.append(f"{label}: unknown resident")
        if user_ok and resident_ok:
            key = (user_id, resident_id)
            if key in seen:
                problems.append(f"{label}: listed more than once")
            seen.add(key)


def validate_seed(seed: Any, registry_devices: list[Any]) -> list[str]:
    """Every problem in the seed file, each naming the ID it is about (empty = valid).

    registry_devices is the "devices" list of infra/devices/devices.json.
    """
    if not isinstance(seed, dict):
        return ["seed file: must hold a JSON object"]
    problems: list[str] = [
        f"seed file: password field not allowed ({path})"
        for path in _password_paths(seed)
    ]
    residents = _section(seed, "residents", problems)
    resident_ids: set[str] = set()
    for index, resident in enumerate(residents):
        label = _label(resident, "resident_id", index, RESIDENT_ID_RE)
        _validate_resident(resident, label, problems)
        if isinstance(resident, dict) and isinstance(resident.get("resident_id"), str):
            if resident["resident_id"] in resident_ids:
                problems.append(f"resident {label}: duplicate resident ID")
            resident_ids.add(resident["resident_id"])
    _validate_devices(
        _section(seed, "devices", problems), registry_devices, resident_ids, problems
    )
    user_groups = _validate_users(_section(seed, "users", problems), problems)
    _validate_links(
        _section(seed, "links", problems), user_groups, resident_ids, problems
    )
    return problems


# ---- registry items (pure, unit tested) ----


def to_attribute(value: Any) -> dict[str, Any]:
    """Python value -> DynamoDB low-level attribute. Numbers become N, never floats."""
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, int | Decimal):
        return {"N": str(value)}
    if isinstance(value, dict):
        return {"M": {str(k): to_attribute(v) for k, v in value.items()}}
    if isinstance(value, list):
        return {"L": [to_attribute(v) for v in value]}
    raise TypeError(f"unsupported seed value type {type(value).__name__}")


def to_item(values: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {key: to_attribute(value) for key, value in values.items()}


def format_ts(now: datetime) -> str:
    """ISO 8601 UTC, seconds, Z: 2026-11-01T03:00:00Z."""
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_subs(seed: dict[str, Any]) -> dict[str, str]:
    """user_id -> the fixed synthetic sub used with --local."""
    return {user["user_id"]: user["local_sub"] for user in seed["users"]}


def profile_key(resident_id: str) -> dict[str, dict[str, str]]:
    return {"pk": {"S": f"RESIDENT#{resident_id}"}, "sk": {"S": "PROFILE"}}


def thresholds_key(resident_id: str) -> dict[str, dict[str, str]]:
    return {"pk": {"S": f"THRESHOLDS#{resident_id}"}, "sk": {"S": "THRESHOLDS"}}


def device_key(device_id: str) -> dict[str, dict[str, str]]:
    return {"pk": {"S": f"DEVICE#{device_id}"}, "sk": {"S": "ASSIGNMENT"}}


def build_registry_items(
    seed: dict[str, Any], subs: dict[str, str], now: datetime | str
) -> list[dict[str, dict[str, Any]]]:
    """Every registry item the seed owns (low-level format), shapes from docs/data-model.md.

    subs maps each linked user_id to its Cognito sub (or local_subs(seed) for --local).
    """
    ts = now if isinstance(now, str) else format_ts(now)
    items = []
    homes = {}
    for resident in seed["residents"]:
        resident_id = resident["resident_id"]
        homes[resident_id] = resident["home_id"]
        items.append(
            to_item(
                {
                    "pk": f"RESIDENT#{resident_id}",
                    "sk": "PROFILE",
                    "entity_type": "RESIDENT",
                    "resident_id": resident_id,
                    "display_name": resident["display_name"],
                    "home_id": resident["home_id"],
                    "year_of_birth": resident["year_of_birth"],
                    "medical_notes": resident["medical_notes"],
                    "emergency_contacts": resident["emergency_contacts"],
                    "created_at": ts,
                    "updated_at": ts,
                }
            )
        )
        thresholds = resident["thresholds"]
        items.append(
            to_item(
                {
                    "pk": f"THRESHOLDS#{resident_id}",
                    "sk": "THRESHOLDS",
                    "hr_bpm": thresholds["hr_bpm"],
                    "spo2_pct": thresholds["spo2_pct"],
                    "skin_temp_c": thresholds["skin_temp_c"],
                    "updated_at": ts,
                    "updated_by": SEED_MARKER,
                }
            )
        )
    for device in seed["devices"]:
        device_id = device["device_id"]
        resident_id = device["resident_id"]
        items.append(
            to_item(
                {
                    "pk": f"DEVICE#{device_id}",
                    "sk": "ASSIGNMENT",
                    "entity_type": "DEVICE",
                    "device_type": device["device_type"],
                    "resident_id": resident_id,
                    "home_id": homes[resident_id],
                    "assigned_at": SEED_ASSIGNED_AT,
                    "gsi1_pk": f"RESIDENT#{resident_id}",
                    "gsi1_sk": f"DEVICE#{device_id}",
                }
            )
        )
    groups = {user["user_id"]: user["group"] for user in seed["users"]}
    for link in seed["links"]:
        sub = subs[link["user_id"]]
        resident_id = link["resident_id"]
        items.append(
            to_item(
                {
                    "pk": f"USER#{sub}",
                    "sk": f"RESIDENT#{resident_id}",
                    "relationship": groups[link["user_id"]],
                    "linked_at": ts,
                    "linked_by": SEED_MARKER,
                    "gsi1_pk": f"RESIDENT#{resident_id}",
                    "gsi1_sk": f"USER#{sub}",
                }
            )
        )
    return items


def owned_keys(seed: dict[str, Any]) -> list[dict[str, dict[str, str]]]:
    """Keys of the seeded profiles, thresholds, and device assignments (not links)."""
    keys = []
    for resident in seed["residents"]:
        keys.append(profile_key(resident["resident_id"]))
        keys.append(thresholds_key(resident["resident_id"]))
    keys.extend(device_key(device["device_id"]) for device in seed["devices"])
    return keys


# ---- passwords (pure + local file) ----


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    """Random password from `secrets` with a lowercase, uppercase, number, and symbol."""
    if length < 4:
        raise ValueError("password length must be at least 4")
    while True:
        password = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))
        if (
            any(c in string.ascii_lowercase for c in password)
            and any(c in string.ascii_uppercase for c in password)
            and any(c in string.digits for c in password)
            and any(c in PASSWORD_SYMBOLS for c in password)
        ):
            return password


def _fold(path: Path) -> str:
    return os.path.normcase(str(path)).casefold()


def _levels_below(path: Path, roots: Iterable[Path], fold: bool) -> int | None:
    """How many levels below one of roots path is (0 = path is a root), or None.

    Compares path strings only (no stat), case-folded if fold is set.
    """
    key = _fold if fold else str
    root_keys = {key(root) for root in roots}
    for level, candidate in enumerate([path, *path.parents]):
        if key(candidate) in root_keys:
            return level
    return None


def check_passwords_path(path: Path | str, repo_root: Path = REPO_ROOT) -> Path:
    """Absolute path of the passwords file. Raises ValueError if it is inside the
    repository but not below secrets/ (paths outside the repository are allowed).

    Checked on the path as given and with symlinks resolved. "Inside the repository"
    is compared case-insensitively (macOS file systems are), "below secrets/" exactly,
    so a differently cased path can only be refused, never let through.
    """
    lexical = Path(os.path.abspath(path))
    repo_lexical = Path(os.path.abspath(repo_root))
    repos = (repo_lexical, repo_lexical.resolve())
    secrets_dirs = (repo_lexical / "secrets", (repo_lexical / "secrets").resolve())
    for candidate in (lexical, lexical.resolve()):
        if _levels_below(candidate, repos, fold=True) is None:
            continue
        below_secrets = _levels_below(candidate, secrets_dirs, fold=False)
        if below_secrets is None or below_secrets == 0:
            raise ValueError(
                "--passwords-file inside the repository must be under secrets/"
            )
    return lexical


def _line_email(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    return stripped.split()[0]


def merge_password_lines(
    existing: list[str], entries: dict[str, tuple[str, str]]
) -> list[str]:
    """Replace the lines of the users in entries (email -> (group, password)) and keep
    every other line; new users are appended."""
    lines = []
    done: set[str] = set()
    for line in existing:
        email = _line_email(line)
        if email in entries:
            if email not in done:
                group, password = entries[email]
                lines.append(f"{email} {group} {password}")
                done.add(email)
            continue
        lines.append(line)
    for email, (group, password) in entries.items():
        if email not in done:
            lines.append(f"{email} {group} {password}")
    return lines


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def _atomic_write(path: Path, lines: list[str]) -> None:
    """Write via a mode-600 temporary file in the same folder, then os.replace."""
    folder = path.parent
    if not folder.exists():
        folder.mkdir(mode=0o700, parents=True)
        os.chmod(folder, 0o700)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("".join(f"{line}\n" for line in lines))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_passwords_file(path: Path | str, entries: dict[str, tuple[str, str]]) -> int:
    """Merge entries (email -> (group, password)) into the file; returns how many lines
    were set. Never truncates other users' lines. Does nothing if entries is empty."""
    path = Path(path)
    if not entries:
        return 0
    _atomic_write(path, merge_password_lines(_read_lines(path), entries))
    return len(entries)


def remove_password_lines(path: Path | str, emails: Iterable[str]) -> int:
    """Remove the lines of these users; returns how many were removed. Leaves the file
    alone if it is missing or no line matches; deletes it if no user lines are left."""
    path = Path(path)
    if not path.exists():
        return 0
    targets = set(emails)
    existing = _read_lines(path)
    kept = [line for line in existing if _line_email(line) not in targets]
    removed = len(existing) - len(kept)
    if removed == 0:
        return 0
    if any(_line_email(line) for line in kept):
        _atomic_write(path, kept)
    else:
        path.unlink()
    return removed


# ---- DynamoDB ----


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None) or {}
    return str(response.get("Error", {}).get("Code", type(exc).__name__))


def _is_throttle(exc: Exception) -> bool:
    from botocore.exceptions import ClientError

    return isinstance(exc, ClientError) and _error_code(exc) in THROTTLE_CODES


def backoff_delay(attempt: int, rng: Callable[[], float] = random.random) -> float:
    """Full-jitter exponential backoff for retry attempt 1, 2, ..."""
    return rng() * min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** (attempt - 1)))


def _chunks(values: list[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def batch_write(
    client: Any,
    table: str,
    requests: list[dict[str, Any]],
    sleep: Callable[[float], None] = time.sleep,
    max_attempts_without_progress: int = MAX_ATTEMPTS_WITHOUT_PROGRESS,
    rng: Callable[[], float] = random.random,
) -> int:
    """BatchWriteItem in calls of at most 25 requests (PutRequest / DeleteRequest).

    Unprocessed items and throttled calls are retried with jittered exponential backoff;
    raises SeedError only after max_attempts_without_progress retries in a row that
    processed nothing. Returns the number of requests processed.
    """
    done = 0
    for chunk in _chunks(requests, BATCH_WRITE_MAX):
        pending = chunk
        stalled = 0
        while pending:
            try:
                response = client.batch_write_item(RequestItems={table: pending})
                unprocessed = response.get("UnprocessedItems", {}).get(table, [])
            except Exception as exc:
                if not _is_throttle(exc):
                    raise
                unprocessed = pending
            processed = len(pending) - len(unprocessed)
            done += processed
            pending = unprocessed
            if not pending:
                break
            stalled = 0 if processed else stalled + 1
            if stalled >= max_attempts_without_progress:
                raise SeedError(
                    f"BatchWriteItem: {len(pending)} items still unprocessed after"
                    f" {stalled} retries without progress"
                )
            sleep(backoff_delay(stalled + 1, rng))
    return done


def put_requests(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"PutRequest": {"Item": item}} for item in items]


def delete_requests(keys: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"DeleteRequest": {"Key": key}} for key in keys]


def write_registry(
    client: Any,
    items: list[dict[str, Any]],
    table: str = TABLE,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """PutItem-overwrite every seeded item (BatchWriteItem). Returns the count."""
    return batch_write(client, table, put_requests(items), sleep=sleep)


def _query_all(client: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
    while True:
        response = client.query(**kwargs)
        yield from response.get("Items", [])
        if "LastEvaluatedKey" not in response:
            return
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def linked_user_keys(client: Any, table: str, resident_id: str) -> list[dict[str, Any]]:
    """Keys of every link to this resident (pattern 15, by-resident index)."""
    keys = []
    for item in _query_all(
        client,
        TableName=table,
        IndexName="by-resident",
        KeyConditionExpression="#gpk = :gpk AND begins_with(#gsk, :user)",
        ExpressionAttributeNames={"#gpk": "gsi1_pk", "#gsk": "gsi1_sk"},
        ExpressionAttributeValues={
            ":gpk": {"S": f"RESIDENT#{resident_id}"},
            ":user": {"S": "USER#"},
        },
    ):
        keys.append({"pk": item["pk"], "sk": item["sk"]})
    return keys


def batch_get(
    client: Any,
    table: str,
    keys: list[dict[str, Any]],
    projection: list[str],
    sleep: Callable[[float], None] = time.sleep,
    max_attempts_without_progress: int = MAX_ATTEMPTS_WITHOUT_PROGRESS,
    rng: Callable[[], float] = random.random,
) -> list[dict[str, Any]]:
    """BatchGetItem (at most 100 keys per call), UnprocessedKeys retried with backoff."""
    names = {f"#a{i}": name for i, name in enumerate(projection)}
    found: list[dict[str, Any]] = []
    for chunk in _chunks(keys, BATCH_GET_MAX):
        pending = chunk
        stalled = 0
        while pending:
            request = {
                "Keys": pending,
                "ProjectionExpression": ", ".join(names),
                "ExpressionAttributeNames": names,
                "ConsistentRead": True,
            }
            try:
                response = client.batch_get_item(RequestItems={table: request})
                found.extend(response.get("Responses", {}).get(table, []))
                unprocessed = (
                    response.get("UnprocessedKeys", {}).get(table, {}).get("Keys", [])
                )
            except Exception as exc:
                if not _is_throttle(exc):
                    raise
                unprocessed = pending
            processed = len(pending) - len(unprocessed)
            pending = unprocessed
            if not pending:
                break
            stalled = 0 if processed else stalled + 1
            if stalled >= max_attempts_without_progress:
                raise SeedError(
                    f"BatchGetItem: {len(pending)} keys still unprocessed after"
                    f" {stalled} retries without progress"
                )
            sleep(backoff_delay(stalled + 1, rng))
    return found


def find_seed_links(
    client: Any,
    resident_ids: Iterable[str],
    table: str = TABLE,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Keys of every link to these residents with linked_by = "seed".

    Found through by-resident (pattern 15) and then read, because linked_by isn't
    projected. Independent of Cognito, so links of users deleted by hand are found too.
    """
    candidates: list[dict[str, Any]] = []
    for resident_id in resident_ids:
        candidates.extend(linked_user_keys(client, table, resident_id))
    if not candidates:
        return []
    items = batch_get(client, table, candidates, ["pk", "sk", "linked_by"], sleep=sleep)
    return [
        {"pk": item["pk"], "sk": item["sk"]}
        for item in items
        if item.get("linked_by", {}).get("S") == SEED_MARKER
    ]


def link_keys(items: Iterable[dict[str, Any]]) -> set[tuple[str, str]]:
    """(pk, sk) of the link items (pk = USER#...) among these registry items."""
    return {
        (item["pk"]["S"], item["sk"]["S"])
        for item in items
        if item["pk"]["S"].startswith("USER#")
    }


def prune_seed_links(
    client: Any,
    seed: dict[str, Any],
    written: Iterable[dict[str, Any]],
    table: str = TABLE,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Delete the seed links (linked_by = "seed", to seeded residents, found as
    --delete finds them) whose key is not one of the links just written. Run after
    the links are written. Links with any other linked_by are never touched: each
    delete is conditional on linked_by still being "seed", so a link re-made by
    someone else since it was read is kept. Returns how many were deleted."""
    from botocore.exceptions import ClientError

    keep = link_keys(written)
    resident_ids = [resident["resident_id"] for resident in seed["residents"]]
    stale = [
        key
        for key in find_seed_links(client, resident_ids, table=table, sleep=sleep)
        if (key["pk"]["S"], key["sk"]["S"]) not in keep
    ]
    deleted = 0
    for key in stale:  # a few at most; adaptive retries pace them for 1 WCU
        try:
            client.delete_item(
                TableName=table,
                Key=key,
                ConditionExpression="linked_by = :seed",
                ExpressionAttributeValues={":seed": {"S": SEED_MARKER}},
            )
        except ClientError as exc:
            if _error_code(exc) != "ConditionalCheckFailedException":
                raise
            continue
        deleted += 1
    return deleted


def foreign_devices(
    client: Any,
    seed: dict[str, Any],
    table: str = TABLE,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """IDs of the seed's devices that are assigned to a resident the seed doesn't own.
    Missing items and unassigned items (no resident_id, e.g. spares written by
    scripts/seed-registry.py) aren't listed: they hold no resident's data, so the seed
    may assign them.

    The device IDs are real hardware, so an assignment made outside the seed may be a
    real one: the seed never overwrites or deletes it.
    """
    keys = [device_key(device["device_id"]) for device in seed["devices"]]
    if not keys:
        return []
    seeded = {resident["resident_id"] for resident in seed["residents"]}
    items = batch_get(client, table, keys, ["pk", "resident_id"], sleep=sleep)
    foreign = []
    for item in items:
        resident_id = item.get("resident_id", {}).get("S")
        if resident_id and resident_id not in seeded:
            foreign.append(item["pk"]["S"].removeprefix("DEVICE#"))
    return sorted(foreign)


def check_device_ownership(
    client: Any,
    seed: dict[str, Any],
    table: str = TABLE,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Raise SeedError (device IDs only) if a seed write would move a device that is
    assigned outside the seed."""
    foreign = foreign_devices(client, seed, table=table, sleep=sleep)
    if foreign:
        raise SeedError(
            f"registry: {len(foreign)} devices are assigned to residents the seed"
            f" doesn't own ({', '.join(foreign)}); the seed refuses to move real"
            " assignments, nothing was written"
        )


def delete_seed(
    client: Any,
    seed: dict[str, Any],
    table: str = TABLE,
    sleep: Callable[[float], None] = time.sleep,
    skipped: list[str] | None = None,
) -> tuple[int, int]:
    """Delete the seed's links (linked_by = seed), then its devices, thresholds, and
    profiles. Items already gone are fine. Device assignments to a resident the seed
    doesn't own are kept; their IDs are appended to skipped if given.
    Returns (links deleted, other items deleted or already gone)."""
    resident_ids = [resident["resident_id"] for resident in seed["residents"]]
    foreign = set(foreign_devices(client, seed, table=table, sleep=sleep))
    if skipped is not None:
        skipped.extend(sorted(foreign))
    links = find_seed_links(client, resident_ids, table=table, sleep=sleep)
    link_count = batch_write(client, table, delete_requests(links), sleep=sleep)
    # Devices first, then thresholds and profiles (the offboarding order).
    keys = owned_keys(seed)
    devices = [
        k
        for k in keys
        if k["pk"]["S"].startswith("DEVICE#")
        and k["pk"]["S"].removeprefix("DEVICE#") not in foreign
    ]
    others = [k for k in keys if not k["pk"]["S"].startswith("DEVICE#")]
    other_count = batch_write(
        client, table, delete_requests(devices + others), sleep=sleep
    )
    return link_count, other_count


def table_exists(client: Any, table: str = TABLE) -> bool:
    """DescribeTable; False if the table doesn't exist."""
    from botocore.exceptions import ClientError

    try:
        client.describe_table(TableName=table)
    except ClientError as exc:
        if _error_code(exc) == "ResourceNotFoundException":
            return False
        raise
    return True


def ensure_table(
    client: Any, table: str = TABLE, sleep: Callable[[float], None] = time.sleep
) -> bool:
    """Create the registry table (as in infra/dynamodb.tf) if missing. Local only.
    Returns True if it was created."""
    from botocore.exceptions import ClientError

    try:
        client.describe_table(TableName=table)
        return False
    except ClientError as exc:
        if _error_code(exc) != "ResourceNotFoundException":
            raise
    client.create_table(TableName=table, **REGISTRY_TABLE_DEFINITION)
    for _ in range(60):
        status = client.describe_table(TableName=table)["Table"].get("TableStatus")
        if status == "ACTIVE":
            return True
        sleep(1.0)
    raise SeedError(f"table {table} did not become ACTIVE")


def seed_local(
    client: Any,
    table: str = TABLE,
    seed: dict[str, Any] | None = None,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Seed a DynamoDB Local table (created if missing) with the local subs.

    For integration tests: pass a client pointing at DynamoDB Local and your own table
    name. Like a --local run, it then removes seed links no longer in the seed file.
    Returns the number of items written (not counting removed links).
    """
    seed = load_seed() if seed is None else seed
    ensure_table(client, table, sleep=sleep)
    check_device_ownership(client, seed, table=table, sleep=sleep)
    items = build_registry_items(seed, local_subs(seed), now or datetime.now(UTC))
    count = write_registry(client, items, table=table, sleep=sleep)
    prune_seed_links(client, seed, items, table=table, sleep=sleep)
    return count


# ---- Cognito ----


def find_user_pool(client: Any, name: str = USER_POOL_NAME) -> str:
    """ID of the one pool with this name (ListUserPools, all pages). Raises SeedError
    if there is none or more than one."""
    ids = []
    kwargs: dict[str, Any] = {"MaxResults": 60}
    while True:
        response = client.list_user_pools(**kwargs)
        ids.extend(
            p["Id"] for p in response.get("UserPools", []) if p.get("Name") == name
        )
        token = response.get("NextToken")
        if not token:
            break
        kwargs["NextToken"] = token
    if len(ids) != 1:
        raise SeedError(f"expected 1 user pool named {name}, found {len(ids)}")
    return ids[0]


def _sub(attributes: list[dict[str, str]]) -> str | None:
    for attribute in attributes:
        if attribute.get("Name") == "sub":
            return attribute.get("Value")
    return None


def get_user(client: Any, pool_id: str, email: str) -> tuple[str | None, str] | None:
    """(sub, UserStatus) from AdminGetUser, or None if the user doesn't exist."""
    from botocore.exceptions import ClientError

    try:
        response = client.admin_get_user(UserPoolId=pool_id, Username=email)
    except ClientError as exc:
        if _error_code(exc) == "UserNotFoundException":
            return None
        raise
    return _sub(response.get("UserAttributes", [])), str(response.get("UserStatus"))


def get_user_sub(client: Any, pool_id: str, email: str) -> str | None:
    """The user's sub, or None if the user doesn't exist (AdminGetUser)."""
    user = get_user(client, pool_id, email)
    return None if user is None else user[0]


def user_groups(client: Any, pool_id: str, email: str) -> set[str]:
    groups: set[str] = set()
    kwargs: dict[str, Any] = {"UserPoolId": pool_id, "Username": email, "Limit": 60}
    while True:
        response = client.admin_list_groups_for_user(**kwargs)
        groups.update(g["GroupName"] for g in response.get("Groups", []))
        token = response.get("NextToken")
        if not token:
            return groups
        kwargs["NextToken"] = token


def set_group(client: Any, pool_id: str, email: str, group: str, new: bool) -> None:
    """Put the user in group and out of the other seed groups (GROUPS: doctor, nurse,
    caregiver). Any other group, including a leftover family group, is left alone."""
    current = set() if new else user_groups(client, pool_id, email)
    for other in GROUPS:
        if other != group and other in current:
            client.admin_remove_user_from_group(
                UserPoolId=pool_id, Username=email, GroupName=other
            )
    if group not in current:
        client.admin_add_user_to_group(
            UserPoolId=pool_id, Username=email, GroupName=group
        )


def ensure_users(
    client: Any,
    pool_id: str,
    users: list[dict[str, Any]],
    reset_passwords: bool,
    passwords: dict[str, tuple[str, str]],
    out: TextIO,
) -> dict[str, str]:
    """Create or update the test users. Fills passwords (email -> (group, password))
    with every password set, as it is set, so the caller can save them even if a later
    user fails. Returns user_id -> sub."""
    subs: dict[str, str] = {}
    for user in users:
        user_id, email, group = user["user_id"], user["email"], user["group"]
        existing = get_user(client, pool_id, email)
        new = existing is None
        sub, status = existing if existing is not None else (None, "")
        # A user left in FORCE_CHANGE_PASSWORD (e.g. a run that stopped between
        # AdminCreateUser and AdminSetUserPassword) has no usable password: set one.
        unfinished = status == "FORCE_CHANGE_PASSWORD"
        if new:
            client.admin_create_user(
                UserPoolId=pool_id,
                Username=email,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                    {"Name": "name", "Value": user["name"]},
                ],
                MessageAction="SUPPRESS",
            )
            sub = get_user_sub(client, pool_id, email)
            if sub is None:
                raise SeedError(f"cognito: {user_id} has no sub after creation")
        if new or unfinished or reset_passwords:
            password = generate_password()
            client.admin_set_user_password(
                UserPoolId=pool_id, Username=email, Password=password, Permanent=True
            )
            passwords[email] = (group, password)
        set_group(client, pool_id, email, group, new)
        if sub is None:
            raise SeedError(f"cognito: {user_id} has no sub")
        if new:
            state = "created"
        elif unfinished:
            state = "exists, password set"
        elif reset_passwords:
            state = "exists, password reset"
        else:
            state = "exists, kept"
        print(f"cognito: {user_id} {state}", file=out)
        subs[user_id] = sub
    return subs


def delete_users(
    client: Any, pool_id: str, users: list[dict[str, Any]], out: TextIO
) -> None:
    from botocore.exceptions import ClientError

    for user in users:
        try:
            client.admin_delete_user(UserPoolId=pool_id, Username=user["email"])
            state = "deleted"
        except ClientError as exc:
            if _error_code(exc) != "UserNotFoundException":
                raise
            state = "already gone"
        print(f"cognito: {user['user_id']} {state}", file=out)


def delete_retired_users(client: Any, pool_id: str, out: TextIO) -> list[str]:
    """Delete the retired test users (RETIRED_USERS, exact usernames only) that still
    exist and are as the seed left them (its name, no group outside the allowed ones).
    One changed by hand (e.g. made a caregiver) is kept: `cognito: <id> kept (changed
    by hand)`. An absent one prints nothing. Returns the usernames whose
    passwords-file lines may be removed (deleted or absent, never kept)."""
    from botocore.exceptions import ClientError

    cleared = []
    for user_id, username, name, allowed_groups in RETIRED_USERS:
        try:
            response = client.admin_get_user(UserPoolId=pool_id, Username=username)
        except ClientError as exc:
            if _error_code(exc) != "UserNotFoundException":
                raise
            cleared.append(username)
            continue
        attributes = {
            a["Name"]: a.get("Value") for a in response.get("UserAttributes", [])
        }
        groups = user_groups(client, pool_id, username)
        if attributes.get("name") != name or not groups <= allowed_groups:
            print(f"cognito: {user_id} kept (changed by hand)", file=out)
            continue
        try:
            client.admin_delete_user(UserPoolId=pool_id, Username=username)
        except ClientError as exc:
            if _error_code(exc) != "UserNotFoundException":
                raise
        else:
            print(f"cognito: {user_id} deleted (retired)", file=out)
        cleared.append(username)
    return cleared


# ---- CLI ----


def make_client(service: str, local: bool = False) -> Any:
    """boto3 client, region fixed to ap-southeast-1 whatever the environment says."""
    import boto3  # only when run for real, never at import
    from botocore.config import Config

    retries = {"mode": "adaptive", "max_attempts": CLIENT_MAX_ATTEMPTS}
    if local:
        return boto3.client(
            service,
            region_name=REGION,
            endpoint_url=os.environ.get("DYNAMODB_ENDPOINT") or DEFAULT_LOCAL_ENDPOINT,
            aws_access_key_id="local",
            aws_secret_access_key="local",
            config=Config(region_name=REGION, retries=retries),
        )
    return boto3.client(
        service,
        region_name=REGION,
        config=Config(
            region_name=REGION, retries=retries, ignore_configured_endpoint_urls=True
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed silvercare-registry and Cognito with synthetic test data."
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="DynamoDB Local at DYNAMODB_ENDPOINT; no Cognito, STS, or prompt",
    )
    parser.add_argument(
        "--delete", action="store_true", help="remove everything the seed owns"
    )
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help="give all 3 test users new passwords",
    )
    parser.add_argument(
        "--user-pool-id",
        help=f"user pool ID (default: the pool named {USER_POOL_NAME})",
    )
    parser.add_argument(
        "--passwords-file",
        default=str(DEFAULT_PASSWORDS_FILE),
        help="where test passwords are saved (default secrets/test-users.txt;"
        " inside the repository it must be under secrets/)",
    )
    parser.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt"
    )
    parser.add_argument(
        "--seed-file", default=str(DEFAULT_SEED_FILE), help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--devices-file", default=str(DEFAULT_DEVICES_FILE), help=argparse.SUPPRESS
    )
    return parser


def usage_problems(args: argparse.Namespace) -> list[str]:
    problems = []
    if args.local and args.user_pool_id is not None:
        problems.append("--local can't be used with --user-pool-id")
    if args.local and args.reset_passwords:
        problems.append("--local can't be used with --reset-passwords")
    if args.delete and args.reset_passwords:
        problems.append("--delete can't be used with --reset-passwords")
    if args.user_pool_id is not None and not USER_POOL_ID_RE.match(args.user_pool_id):
        problems.append("--user-pool-id must start with ap-southeast-1_")
    return problems


def _aws_error(exc: Exception) -> str:
    """Operation name and error code only."""
    operation = getattr(exc, "operation_name", None)
    if operation:
        return f"{operation} failed ({_error_code(exc)})"
    return f"AWS call failed ({type(exc).__name__})"


def _run_local(
    args: argparse.Namespace,
    seed: dict[str, Any],
    client_factory: Callable[..., Any],
    out: TextIO,
    sleep: Callable[[float], None],
    now: datetime,
) -> int:
    client = client_factory("dynamodb", local=True)
    if args.delete:
        if not table_exists(client):
            print(f"registry: table {TABLE} not found, nothing to delete", file=out)
            return 0
        _delete_registry(client, seed, out, sleep)
        return 0
    if ensure_table(client, sleep=sleep):
        print(f"registry: table {TABLE} created", file=out)
    check_device_ownership(client, seed, sleep=sleep)
    items = build_registry_items(seed, local_subs(seed), now)
    _write_and_prune(client, seed, items, out, sleep)
    return 0


def _write_and_prune(
    client: Any,
    seed: dict[str, Any],
    items: list[dict[str, Any]],
    out: TextIO,
    sleep: Callable[[float], None],
) -> None:
    """Write the seeded items, then (only after the new links exist) remove the seed
    links that are no longer in the seed file."""
    print(
        f"registry: {write_registry(client, items, sleep=sleep)} items written",
        file=out,
    )
    removed = prune_seed_links(client, seed, items, sleep=sleep)
    print(f"registry: {removed} old seed links removed", file=out)


def _delete_registry(
    client: Any, seed: dict[str, Any], out: TextIO, sleep: Callable[[float], None]
) -> None:
    skipped: list[str] = []
    links, others = delete_seed(client, seed, sleep=sleep, skipped=skipped)
    print(f"registry: {links} seed links deleted", file=out)
    print(f"registry: {others} items deleted or already gone", file=out)
    if skipped:
        print(
            f"registry: {len(skipped)} devices skipped, assigned to residents the"
            f" seed doesn't own ({', '.join(skipped)})",
            file=out,
        )


def _confirm(word: str, input_fn: Callable[[str], str], out: TextIO) -> bool:
    try:
        answer = input_fn(f"Type {word} to continue: ")
    except EOFError:
        answer = None
    if answer is None or answer.strip() != word:
        print("aborted: nothing was written", file=out)
        return False
    return True


def _run_live(
    args: argparse.Namespace,
    seed: dict[str, Any],
    passwords_path: Path,
    client_factory: Callable[..., Any],
    input_fn: Callable[[str], str],
    out: TextIO,
    err: TextIO,
    sleep: Callable[[float], None],
    now: datetime,
) -> int:
    sts = client_factory("sts")
    account = sts.get_caller_identity()["Account"]
    cognito = client_factory("cognito-idp")
    pool_id = args.user_pool_id or find_user_pool(cognito)
    dynamodb = client_factory("dynamodb")

    users = seed["users"]
    retired = ", ".join(entry[0] for entry in RETIRED_USERS)
    print(f"account: {account}", file=out)
    print(f"region: {REGION}", file=out)
    print(f"user pool: {pool_id}", file=out)
    if args.delete:
        word = "delete"
        print(
            f"plan: delete the seeded items in {TABLE} ({len(seed['residents'])}"
            f" residents, {len(seed['devices'])} devices, seed links), the"
            f" {len(users)} test users and the retired test user ({retired}) if it"
            f" exists, and their lines in {passwords_path}",
            file=out,
        )
    else:
        word = "seed"
        action = "reset passwords of" if args.reset_passwords else "create or update"
        print(
            f"plan: {action} {len(users)} test users, then write"
            f" {len(build_registry_items(seed, local_subs(seed), now))} items to {TABLE}",
            file=out,
        )
        print(
            "plan: then clean up: remove seed links that are no longer in the seed"
            f" file, and delete the retired test user ({retired}) if it exists and"
            f" its line in {passwords_path}",
            file=out,
        )
    if not args.delete:
        # Read-only check before the prompt: never move a real device assignment.
        check_device_ownership(dynamodb, seed, sleep=sleep)
    if not args.yes and not _confirm(word, input_fn, out):
        return 1

    if args.delete:
        _delete_registry(dynamodb, seed, out, sleep)
        delete_users(cognito, pool_id, users, out)
        cleared = delete_retired_users(cognito, pool_id, out)
        removed = remove_password_lines(
            passwords_path, [u["email"] for u in users] + cleared
        )
        print(f"passwords: {removed} lines removed from {passwords_path}", file=out)
        return 0

    passwords: dict[str, tuple[str, str]] = {}
    try:
        subs = ensure_users(
            cognito, pool_id, users, args.reset_passwords, passwords, out
        )
    finally:
        # Save every password that was set, even if a later user failed.
        if passwords:
            _save_passwords(passwords_path, passwords, out, err)
    items = build_registry_items(seed, subs, now)
    _write_and_prune(dynamodb, seed, items, out, sleep)
    # Only after the current users exist and their links are written.
    cleared = delete_retired_users(cognito, pool_id, out)
    removed = remove_password_lines(passwords_path, cleared)
    if removed:
        print(
            f"passwords: {removed} retired lines removed from {passwords_path}",
            file=out,
        )
    return 0


def _save_passwords(
    path: Path, passwords: dict[str, tuple[str, str]], out: TextIO, err: TextIO
) -> None:
    try:
        count = write_passwords_file(path, passwords)
    except OSError as exc:
        print(
            f"error: could not write the passwords file ({type(exc).__name__});"
            " run again with --reset-passwords",
            file=err,
        )
        raise SeedError("passwords file not written") from None
    print(f"passwords: {count} written to {path}", file=out)


def main(
    argv: list[str] | None = None,
    client_factory: Callable[..., Any] | None = None,
    clients: dict[str, Any] | None = None,
    input_fn: Callable[[str], str] = input,
    out: TextIO | None = None,
    err: TextIO | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> int:
    """Run the seed. Tests inject clients ({"dynamodb": ..., "cognito-idp": ...,
    "sts": ...}) or client_factory(service, local=False), plus input_fn, out, err, sleep."""
    out = out or sys.stdout
    err = err or sys.stderr
    parser = build_parser()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    problems = usage_problems(args)
    if problems:
        for problem in problems:
            print(f"usage error: {problem}", file=err)
        return 2

    passwords_path = Path(args.passwords_file)
    if not args.local:
        try:
            passwords_path = check_passwords_path(args.passwords_file)
        except ValueError as exc:
            print(f"usage error: {exc}", file=err)
            return 2

    try:
        seed = load_seed(args.seed_file)
        devices = load_devices(args.devices_file)
    except (OSError, ValueError, TypeError) as exc:
        print(
            f"validation error: could not read input ({type(exc).__name__})", file=err
        )
        return 2
    problems = validate_seed(seed, devices)
    if problems:
        for problem in problems:
            print(f"validation error: {problem}", file=err)
        print(f"{len(problems)} problems; nothing was written", file=err)
        return 2

    if client_factory is None:
        if clients is not None:
            injected = clients

            def client_factory(service: str, local: bool = False) -> Any:
                return injected[service]
        else:
            client_factory = make_client

    from botocore.exceptions import BotoCoreError, ClientError

    timestamp = now or datetime.now(UTC)
    try:
        if args.local:
            return _run_local(args, seed, client_factory, out, sleep, timestamp)
        return _run_live(
            args,
            seed,
            passwords_path,
            client_factory,
            input_fn,
            out,
            err,
            sleep,
            timestamp,
        )
    except ClientError as exc:
        print(f"error: {_aws_error(exc)}", file=err)
    except BotoCoreError as exc:
        print(f"error: AWS call failed ({type(exc).__name__})", file=err)
    except SeedError as exc:
        print(f"error: {exc}", file=err)
    except OSError as exc:
        print(f"error: local file operation failed ({type(exc).__name__})", file=err)
    return 1


if __name__ == "__main__":
    sys.exit(main())
