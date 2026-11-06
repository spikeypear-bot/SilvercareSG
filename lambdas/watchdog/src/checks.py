"""The watchdog's decisions (docs/features/watchdog.md, criteria 3-10;
docs/features/possible-fall.md, criteria 2-7). Pure functions, no AWS calls:
handler.py reads the inputs and sends what `plan` decides.

Conditions, all against "now" (the run's start) and the device's newest `status`:

    device_offline  now - latest_ts > 15 min (exactly 15 min is not offline)
    battery_low     battery_pct a number below 20 (20 is not low), charging not true;
                    null (mains-powered bedroom units) never
    not_worn        wearable, worn false, charging not true, worn_false_since at least
                    2 h before now, now in 08:00-22:00 SGT (08:00 yes, 22:00 no)

A device that has never sent a `status`, and a device without a resident (a spare), is
not checked. An offline device is not checked for battery or worn (its data is stale).
At most one open alert per device and type. 5 or more offline alerts raised in one run
are grouped (details.grouped, one summary email to ops).

Resolve (criterion 10): an open device-problem alert whose condition no longer holds:
the device is back online, the battery is at or above 20 or charging, the wearable is
worn again or charging, or the device is no longer assigned to that alert's resident.
Outside 08:00-22:00 nothing new is raised for not_worn, but nothing is resolved either.
While a device is offline its open battery_low / not_worn alerts stay open (stale data
can't show the problem cleared); its offline alert covers it.

Possible fall (docs/features/possible-fall.md), a critical health alert, for each
assigned bedroom unit that is not offline (a unit without a status is not offline:
A4a's rule needs a heartbeat to call a device offline) and has a `bed` entry. Times are
event times: an entry's `ts`, or its `received_at` when `clock_skew` is true.

    bed           newest entry occupied false; its event time is vacated_at
    night         vacated_at in 22:00:00-06:59:59.999 SGT (the alert may fire later)
    wait          now - vacated_at >= 20 min (exactly 20 min alerts)
    motion        newest entry motion false, or no motion entry
    wearable      none of the resident's wearables has a newest status with worn true
                  and latest_ts less than 15 min before now (exactly 15 min is old)

Test mode (criterion 3a): a registry TEST_MODE item for a bedroom unit, set at most
2 hours before now (exactly 2 hours still counts) and not in the future, skips the
night check and makes the wait 2 minutes for that unit only. An alert that only test
mode allowed carries details.test_mode = true; one the real rule allows doesn't, so a
real night-time fall is never labelled a test.

At most one open possible_fall per unit (never grouped). An open one is resolved
(`reason` back_in_bed) when the unit's newest bed entry is occupied true with an event
time after the alert's event_ts, which is its out_of_bed_since (the same vacated_at).
Nothing else resolves it.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

SCHEMA_VERSION = 1

# Same namespace and formula as lambdas/ingest/src/alerts.py (alert_id_for). Never
# change it: the same event must always give the same alert_id.
ALERT_NAMESPACE = uuid.UUID("8d3c5f2a-6b1e-4f7d-9a0c-2e4b6d8f1a35")

SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"
DEVICE_OFFLINE = "device_offline"
BATTERY_LOW = "battery_low"
NOT_WORN = "not_worn"
DEVICE_PROBLEM_TYPES = (DEVICE_OFFLINE, BATTERY_LOW, NOT_WORN)
POSSIBLE_FALL = "possible_fall"
# Every type the watchdog raises and resolves (the summary log counts each).
WATCHDOG_TYPES = (*DEVICE_PROBLEM_TYPES, POSSIBLE_FALL)

WEARABLE = "wearable"
BEDROOM = "bedroom"

OFFLINE_AFTER = timedelta(minutes=15)
BATTERY_LOW_BELOW = 20
NOT_WORN_AFTER = timedelta(hours=2)
NOT_WORN_FROM_HOUR = 8  # 08:00 SGT, included
NOT_WORN_UNTIL_HOUR = 22  # 22:00 SGT, excluded
GROUP_OFFLINE_AT = 5

# Possible fall (docs/features/possible-fall.md, criteria 3 and 3a).
POSSIBLE_FALL_AFTER = timedelta(minutes=20)
TEST_MODE_AFTER = timedelta(minutes=2)
TEST_MODE_VALID_FOR = timedelta(hours=2)
NIGHT_FROM_HOUR = 22  # 22:00:00 SGT, included
NIGHT_UNTIL_HOUR = 7  # 07:00:00 SGT, excluded
WORN_RECENT = timedelta(minutes=15)
WEARABLE_NOT_WORN = "not_worn"
WEARABLE_NO_RECENT_STATUS = "no_recent_status"
WEARABLE_NONE = "no_wearable"
TEST_MODE_PREFIX = "TEST_MODE#"
BEDROOM_ID_RE = re.compile(r"^bedroom-[0-9]{4}\Z")

# Singapore has been UTC+8 all year since 1982 (as alert-processor's email_text).
SGT = timezone(timedelta(hours=8), "SGT")

KIND_STATUS_UPDATE = "status_update"
STATUS_RESOLVED = "resolved"
ROLE_SYSTEM = "system"
REASON_BACK_IN_BED = "back_in_bed"

DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-[0-9]{4}\Z")
ASSIGNED_RESIDENT_RE = re.compile(r"^res-[0-9]{4}\Z")
RESIDENT_ID_RE = re.compile(r"^(res-[0-9]{4}|unassigned)\Z")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

SUMMARY_SUBJECT_MAX_LEN = 100


@dataclass(frozen=True)
class Device:
    """An assigned device from the registry (pattern 4)."""

    device_id: str
    device_type: str
    resident_id: str


@dataclass(frozen=True)
class Status:
    """A device's newest status entry (pattern 5)."""

    latest: dict[str, Any]
    latest_ts: str
    latest_dt: datetime


@dataclass(frozen=True)
class OpenAlert:
    """An open (or acknowledged) alert from the `open` index (pattern 9)."""

    alert_id: str
    alert_type: str
    device_id: str
    resident_id: str
    event_ts: str


@dataclass(frozen=True)
class BedroomEntry:
    """A bedroom unit's newest `bed` (occupied) or `motion` entry (pattern 5), with its
    event time: `ts`, or `received_at` when the entry has clock_skew true."""

    value: bool
    event_ts: str
    event_dt: datetime
    message_id: str | None


@dataclass(frozen=True)
class Problem:
    """An alert to raise: a device problem (warning) or a possible fall (critical)."""

    alert_type: str
    device: Device
    event_ts: str
    details: dict[str, Any]
    source_message_id: str | None
    severity: str = SEVERITY_WARNING


@dataclass
class Plan:
    """What one run does."""

    checked: int = 0
    raise_alerts: list[Problem] = field(default_factory=list)
    resolve: list[OpenAlert] = field(default_factory=list)
    grouped: bool = False
    test_mode_units: int = 0


# ---- parsing ----


def parse_ts(value: Any) -> datetime | None:
    """UTC datetime of an ISO 8601 ms Z string, or None if invalid."""
    if type(value) is not str or not TS_RE.match(value):
        return None
    try:
        return datetime.strptime(value, TS_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def format_iso_ms(value: datetime) -> str:
    """ISO 8601 UTC with milliseconds and Z, e.g. 2026-11-04T02:00:00.000Z."""
    value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def device_from_item(item: dict[str, Any]) -> Device | None:
    """An assigned device from a by-type item (pk, device_type, resident_id), or None
    for a spare or an item without the expected formats."""
    pk = item.get("pk")
    if not isinstance(pk, str) or not pk.startswith("DEVICE#"):
        return None
    device_id = pk.removeprefix("DEVICE#")
    if not DEVICE_ID_RE.match(device_id):
        return None
    resident_id = item.get("resident_id")
    if not isinstance(resident_id, str) or not ASSIGNED_RESIDENT_RE.match(resident_id):
        return None
    device_type = item.get("device_type")
    if device_type not in (WEARABLE, BEDROOM):
        # The ID prefix is the type (docs/metrics.md).
        device_type = device_id.split("-", 1)[0]
    return Device(device_id, device_type, resident_id)


def status_from_item(item: dict[str, Any]) -> tuple[str, Status] | None:
    """(device_id, newest status) from a by-message item, or None if malformed."""
    pk = item.get("pk")
    if not isinstance(pk, str) or not pk.startswith("DEVICE#"):
        return None
    device_id = pk.removeprefix("DEVICE#")
    latest = item.get("latest")
    latest_ts = item.get("latest_ts")
    latest_dt = parse_ts(latest_ts)
    if not DEVICE_ID_RE.match(device_id) or not isinstance(latest, dict):
        return None
    if latest_dt is None:
        return None
    return device_id, Status(latest, latest_ts, latest_dt)


def open_alert_from_item(item: dict[str, Any]) -> OpenAlert | None:
    """A device-problem or possible_fall alert from an `open` index item, or None for
    any other type or a key without the expected formats (pk RESIDENT#<id>, sk
    ALERT#<event_ts>#<alert_id>)."""
    alert_type = item.get("alert_type")
    if alert_type not in WATCHDOG_TYPES:
        return None
    pk, sk = item.get("pk"), item.get("sk")
    if not isinstance(pk, str) or not isinstance(sk, str):
        return None
    resident_id = pk.removeprefix("RESIDENT#")
    parts = sk.split("#")
    if not pk.startswith("RESIDENT#") or len(parts) != 3 or parts[0] != "ALERT":
        return None
    _, event_ts, alert_id = parts
    device_id = item.get("device_id")
    if not RESIDENT_ID_RE.match(resident_id) or not UUID_RE.match(alert_id):
        return None
    if parse_ts(event_ts) is None:
        return None
    if not isinstance(device_id, str) or not DEVICE_ID_RE.match(device_id):
        return None
    return OpenAlert(alert_id, alert_type, device_id, resident_id, event_ts)


def event_time(entry: dict[str, Any]) -> tuple[str, datetime] | None:
    """Criterion 2: (event_ts, datetime) of an entry: its `ts`, or its `received_at`
    when clock_skew is true (a wrong device clock must not move the night window or
    the wait). None if that timestamp is missing or invalid."""
    name = "received_at" if entry.get("clock_skew") is True else "ts"
    value = entry.get(name)
    parsed = parse_ts(value)
    if parsed is None:
        return None
    return value, parsed


def bedroom_entry_from_item(
    item: dict[str, Any], field_name: str
) -> tuple[str, BedroomEntry] | None:
    """(device_id, newest entry) from a by-message `bed` (field_name "occupied") or
    `motion` (field_name "motion") item of a bedroom unit, or None if malformed (a
    value that isn't a boolean, or no valid event time)."""
    pk = item.get("pk")
    if not isinstance(pk, str) or not pk.startswith("DEVICE#"):
        return None
    device_id = pk.removeprefix("DEVICE#")
    latest = item.get("latest")
    if not BEDROOM_ID_RE.match(device_id) or not isinstance(latest, dict):
        return None
    value = latest.get(field_name)
    if type(value) is not bool:
        return None
    when = event_time(latest)
    if when is None:
        return None
    message_id = latest.get("message_id")
    source = message_id if isinstance(message_id, str) else None
    return device_id, BedroomEntry(value, when[0], when[1], source)


def unit_in_test_mode(item: dict[str, Any], now: datetime) -> str | None:
    """Criterion 3a: the bedroom unit a TEST_MODE item puts in test mode, or None if
    the item is ignored: a pk that isn't DEVICE#bedroom-NNNN, an sk that isn't
    TEST_MODE#<ISO 8601 ms Z>, a time in the future, or one more than 2 hours before
    now (exactly 2 hours still counts)."""
    pk, sk = item.get("pk"), item.get("sk")
    if not isinstance(pk, str) or not isinstance(sk, str):
        return None
    device_id = pk.removeprefix("DEVICE#")
    if not pk.startswith("DEVICE#") or not BEDROOM_ID_RE.match(device_id):
        return None
    if not sk.startswith(TEST_MODE_PREFIX):
        return None
    set_at = parse_ts(sk.removeprefix(TEST_MODE_PREFIX))
    if set_at is None or set_at > now or now - set_at > TEST_MODE_VALID_FOR:
        return None
    return device_id


def units_in_test_mode(items: list[dict[str, Any]], now: datetime) -> frozenset[str]:
    """The bedroom units in test mode now (criterion 3a)."""
    units = (unit_in_test_mode(item, now) for item in items)
    return frozenset(unit for unit in units if unit is not None)


def _as_int(value: Any) -> int | None:
    """An integer from the payload (int) or DynamoDB (Decimal), else None."""
    if type(value) is int:
        return value
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    return None


# ---- conditions ----


def is_offline(status: Status, now: datetime) -> bool:
    """Criterion 4: more than 15 minutes since the last heartbeat."""
    return now - status.latest_dt > OFFLINE_AFTER


def low_battery_pct(status: Status) -> int | None:
    """Criterion 5: battery_pct below 20 and not charging, else None."""
    battery = _as_int(status.latest.get("battery_pct"))
    if battery is None or status.latest.get("charging") is True:
        return None
    return battery if battery < BATTERY_LOW_BELOW else None


def is_off_wrist(device: Device, status: Status) -> bool:
    """A wearable whose newest status has worn false and charging not true."""
    return (
        device.device_type == WEARABLE
        and status.latest.get("worn") is False
        and status.latest.get("charging") is not True
    )


def in_not_worn_hours(now: datetime) -> bool:
    """Criterion 6: 08:00 (included) to 22:00 (excluded) SGT."""
    return NOT_WORN_FROM_HOUR <= now.astimezone(SGT).hour < NOT_WORN_UNTIL_HOUR


def problems_for(
    device: Device, status: Status, since: str | None, now: datetime
) -> list[Problem]:
    """Criteria 4-7 for one assigned device that has a status."""
    message_id = status.latest.get("message_id")
    source = message_id if isinstance(message_id, str) else None
    if is_offline(status, now):
        return [
            Problem(
                DEVICE_OFFLINE,
                device,
                status.latest_ts,
                {"last_seen": status.latest_ts},
                source,
            )
        ]
    problems: list[Problem] = []
    battery = low_battery_pct(status)
    if battery is not None:
        problems.append(
            Problem(
                BATTERY_LOW,
                device,
                status.latest_ts,
                {"battery_pct": battery},
                source,
            )
        )
    since_dt = parse_ts(since)
    if (
        since_dt is not None
        and is_off_wrist(device, status)
        and now - since_dt >= NOT_WORN_AFTER
        and in_not_worn_hours(now)
    ):
        problems.append(Problem(NOT_WORN, device, since, {"since": since}, source))
    return problems


def condition_holds(
    alert: OpenAlert,
    device: Device | None,
    status: Status | None,
    now: datetime,
) -> bool:
    """Criterion 10: True while the open alert's problem is still there."""
    if device is None or device.resident_id != alert.resident_id:
        return False  # no longer assigned (to this resident)
    if status is None:
        return False  # nothing to compare (e.g. its latest items were deleted)
    offline = is_offline(status, now)
    if alert.alert_type == DEVICE_OFFLINE:
        return offline
    if offline:
        return True  # stale data can't show the problem cleared
    if alert.alert_type == BATTERY_LOW:
        return low_battery_pct(status) is not None
    return is_off_wrist(device, status)


def in_night_window(value: datetime) -> bool:
    """Possible-fall criterion 3: 22:00:00 (included) to 07:00:00 (excluded) SGT, on
    any date."""
    hour = value.astimezone(SGT).hour
    return hour >= NIGHT_FROM_HOUR or hour < NIGHT_UNTIL_HOUR


def wearable_state(
    wearables: list[Device], statuses: dict[str, Status], now: datetime
) -> str | None:
    """Possible-fall criteria 3-4: None when a wearable of the resident is worn
    (newest status worn true, latest_ts less than 15 minutes before now), else the
    details value: not_worn (a recent status with worn false), no_recent_status
    (wearable assigned, nothing recent), or no_wearable (none assigned)."""
    if not wearables:
        return WEARABLE_NONE
    recent = [
        statuses[w.device_id]
        for w in wearables
        if w.device_id in statuses
        and now - statuses[w.device_id].latest_dt < WORN_RECENT
    ]
    if any(status.latest.get("worn") is True for status in recent):
        return None
    if any(status.latest.get("worn") is False for status in recent):
        return WEARABLE_NOT_WORN
    return WEARABLE_NO_RECENT_STATUS


def possible_fall_for(
    unit: Device,
    status: Status | None,
    bed: BedroomEntry | None,
    motion: BedroomEntry | None,
    wearable: str | None,
    test_mode: bool,
    now: datetime,
) -> Problem | None:
    """Possible-fall criteria 3, 3a, and 4 for one assigned bedroom unit. `wearable`
    is wearable_state() for the unit's resident."""
    if status is not None and is_offline(status, now):
        return None  # stale data; A4a's offline alert covers it
    if bed is None or bed.value is not False:
        return None
    waited = now - bed.event_dt
    real = in_night_window(bed.event_dt) and waited >= POSSIBLE_FALL_AFTER
    test = test_mode and waited >= TEST_MODE_AFTER
    if not (real or test):
        return None
    if motion is not None and motion.value is True:
        return None
    if wearable is None:
        return None
    details: dict[str, Any] = {"out_of_bed_since": bed.event_ts, "wearable": wearable}
    if not real:
        details["test_mode"] = True
    return Problem(
        POSSIBLE_FALL,
        unit,
        bed.event_ts,
        details,
        bed.message_id,
        SEVERITY_CRITICAL,
    )


def back_in_bed(alert: OpenAlert, bed: BedroomEntry | None) -> bool:
    """Possible-fall criterion 7: the unit's newest bed entry is occupied true with an
    event time after the alert's out_of_bed_since (its event_ts)."""
    since = parse_ts(alert.event_ts)
    if bed is None or bed.value is not True or since is None:
        return False
    return bed.event_dt > since


def possible_falls(
    devices: list[Device],
    statuses: dict[str, Status],
    beds: dict[str, BedroomEntry],
    motions: dict[str, BedroomEntry],
    test_mode: frozenset[str],
    already_open: set[tuple[str, str]],
    now: datetime,
) -> list[Problem]:
    """Possible-fall criteria 3-5: one per assigned bedroom unit at most, none for a
    unit that already has an open one or has no bed entry."""
    wearables: dict[str, list[Device]] = {}
    for device in devices:
        if device.device_type == WEARABLE:
            wearables.setdefault(device.resident_id, []).append(device)
    problems: list[Problem] = []
    for unit in sorted(devices, key=lambda d: d.device_id):
        if unit.device_type != BEDROOM or not BEDROOM_ID_RE.match(unit.device_id):
            continue
        bed = beds.get(unit.device_id)
        if bed is None or (unit.device_id, POSSIBLE_FALL) in already_open:
            continue
        problem = possible_fall_for(
            unit,
            statuses.get(unit.device_id),
            bed,
            motions.get(unit.device_id),
            wearable_state(wearables.get(unit.resident_id, []), statuses, now),
            unit.device_id in test_mode,
            now,
        )
        if problem is not None:
            problems.append(problem)
    return problems


def plan(
    devices: list[Device],
    statuses: dict[str, Status],
    worn_since: dict[str, str],
    open_alerts: list[OpenAlert],
    now: datetime,
    beds: dict[str, BedroomEntry] | None = None,
    motions: dict[str, BedroomEntry] | None = None,
    test_mode: frozenset[str] = frozenset(),
) -> Plan:
    """Watchdog criteria 2-10 and possible-fall criteria 3-7: what to raise and what
    to resolve in this run. `test_mode` holds the bedroom units in test mode."""
    beds = beds or {}
    result = Plan(checked=len(devices), test_mode_units=len(test_mode))
    by_id = {device.device_id: device for device in devices}
    already_open = {(alert.device_id, alert.alert_type) for alert in open_alerts}

    for device in sorted(devices, key=lambda d: d.device_id):
        status = statuses.get(device.device_id)
        if status is None:
            continue  # never sent a status
        for problem in problems_for(
            device, status, worn_since.get(device.device_id), now
        ):
            if (device.device_id, problem.alert_type) not in already_open:
                result.raise_alerts.append(problem)

    offline = [p for p in result.raise_alerts if p.alert_type == DEVICE_OFFLINE]
    if len(offline) >= GROUP_OFFLINE_AT:
        result.grouped = True
        for problem in offline:
            problem.details["grouped"] = True

    # Possible falls are never grouped (only offline alerts count above).
    result.raise_alerts += possible_falls(
        devices, statuses, beds, motions or {}, test_mode, already_open, now
    )

    for alert in open_alerts:
        if alert.alert_type == POSSIBLE_FALL:
            # Only back in bed resolves it (not unassignment or offline), and only while
            # the unit is still this resident's: after a reassignment its bed data is
            # another resident's, which must not close (or be emailed about) this alert.
            unit = by_id.get(alert.device_id)
            if (
                unit is not None
                and unit.resident_id == alert.resident_id
                and back_in_bed(alert, beds.get(alert.device_id))
            ):
                result.resolve.append(alert)
            continue
        device = by_id.get(alert.device_id)
        if not condition_holds(alert, device, statuses.get(alert.device_id), now):
            result.resolve.append(alert)
    return result


# ---- messages (alert-processor's contract, docs/data-model.md) ----


def alert_id_for(event_ts: str, alert_type: str, severity: str, device_id: str) -> str:
    """UUID v5 of "<event_ts>|<alert_type>|<severity>|<device_id>" (the usual formula),
    so a retry of the same run builds the same alert."""
    return str(
        uuid.uuid5(ALERT_NAMESPACE, f"{event_ts}|{alert_type}|{severity}|{device_id}")
    )


def alert_message(problem: Problem, now: datetime) -> dict[str, Any]:
    """The A1 alert message for one problem. received_at is the run's start (when the
    watchdog detected it); source_message_id is the newest status's (a possible fall:
    the bed entry's) message_id, or the alert_id if that isn't a UUID."""
    alert_id = alert_id_for(
        problem.event_ts, problem.alert_type, problem.severity, problem.device.device_id
    )
    source = problem.source_message_id
    if source is None or not UUID_RE.match(source):
        source = alert_id
    return {
        "schema_version": SCHEMA_VERSION,
        "alert_id": alert_id,
        "alert_type": problem.alert_type,
        "severity": problem.severity,
        "resident_id": problem.device.resident_id,
        "device_id": problem.device.device_id,
        "event_ts": problem.event_ts,
        "received_at": format_iso_ms(now),
        "source_message_id": source,
        "details": dict(problem.details),
    }


def resolve_message(alert: OpenAlert, now: datetime) -> dict[str, Any]:
    """The system resolve status_update (criterion 10): no `note` key. A possible fall
    adds reason back_in_bed (possible-fall criterion 7); a device problem has no
    `reason` key."""
    message: dict[str, Any] = {
        "kind": KIND_STATUS_UPDATE,
        "schema_version": SCHEMA_VERSION,
        "resident_id": alert.resident_id,
        "new_status": STATUS_RESOLVED,
        "changed_at": format_iso_ms(now),
        "by_role": ROLE_SYSTEM,
        "by": None,
        "by_name": None,
        "target": {"alert_id": alert.alert_id, "event_ts": alert.event_ts},
    }
    if alert.alert_type == POSSIBLE_FALL:
        message["reason"] = REASON_BACK_IN_BED
    return message


def encode(message: dict[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":"), allow_nan=False)


# ---- mass offline summary (criterion 8a) ----


def format_when(value: datetime) -> str:
    """2026-11-04 10:00:00 SGT"""
    return value.astimezone(SGT).strftime("%Y-%m-%d %H:%M:%S") + " SGT"


def summary_subject(count: int) -> str:
    return f"SilverCare device problem: {count} devices offline"[
        :SUMMARY_SUBJECT_MAX_LEN
    ]


def summary_body(problems: list[Problem], portal_url: str) -> str:
    """One line per offline device: ID, resident, last heartbeat (SGT)."""
    offline = sorted(
        (p for p in problems if p.alert_type == DEVICE_OFFLINE),
        key=lambda p: p.device.device_id,
    )
    lines = [
        "SilverCare device problem",
        "",
        f"{len(offline)} devices stopped sending heartbeats (no status for 15 minutes):",
        "",
    ]
    for problem in offline:
        seen = parse_ts(problem.event_ts)
        when = format_when(seen) if seen else "unknown time"
        lines.append(
            f"  {problem.device.device_id} ({problem.device.resident_id}):"
            f" last heartbeat {when}"
        )
    note = (
        "Each device has its own alert in the portal, and each resident's caregivers"
        " are emailed about their own device."
    )
    lines += [
        "",
        note,
        "",
        f"Open the SilverCare portal: {portal_url.rstrip('/')}/",
        "",
        "This is an automated message. Do not reply.",
    ]
    return "\n".join(lines) + "\n"
