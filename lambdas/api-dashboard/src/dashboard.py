"""Pure logic for GET /dashboard (docs/data-model.md, Dashboard; patterns 4, 5, 8, 9):
no AWS calls.

Builds the nurse and doctor dashboards' response from the handler's reads. Every
resident, device state, vitals entry, and alert is built field by field with exactly
the keys below, so an attribute an index projects but the dashboards don't need
(`photo_key`, `notified_at`, `received_at`, `clock_skew`, `message_id`, an
acknowledger's `sub`) can never reach the response.

- `severity` is "critical" when missing (or not one of the two values): items stored
  before vitals alerts are all falls.
- `escalates_at` = `notified_at` + 300 s, only on an `open` health-alert **warning**
  from an assigned device with no `escalated_at` yet. Never on a device problem.
- Resident `status` / `status_text` (data model, Resident status): `urgent` for an
  open or acknowledged critical alert, else `attention` for an open or acknowledged
  warning (device problems included) or a newest `vitals` entry strictly outside the
  resident's resolved thresholds, else `ok`.
- `acknowledged_by` is a name (looked up by the handler, see actors.py) only for a
  caregiver's acknowledge of a device problem; never a `sub`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import thresholds

WEARABLE = "wearable"
BEDROOM = "bedroom"
UNASSIGNED = "unassigned"

# As the watchdog and GET /devices: offline after more than 15 minutes with no status.
OFFLINE_AFTER = timedelta(minutes=15)
# docs/features/escalation.md, alert-routing.md (delay changed 2026-11-04).
ESCALATION_DELAY = timedelta(seconds=300)
NEVER = datetime.min.replace(tzinfo=UTC)

DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-[0-9]{4}\Z")
RESIDENT_ID_RE = re.compile(r"^res-[0-9]{4}\Z")
HOME_ID_RE = re.compile(r"^home-[0-9]{4}\Z")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
# Device and server times: ISO 8601 UTC with Z, milliseconds optional (alert-processor
# writes ms; a few server times may have none).
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z\Z")
DISPLAY_NAME_MAX_LEN = 100
ACK_NOTE_MAX_LEN = 500

HEALTH_ALERT_TYPES = frozenset(
    {
        "fall",
        "hr_high",
        "hr_low",
        "spo2_low",
        "skin_temp_high",
        "skin_temp_low",
        "possible_fall",
    }
)
DEVICE_PROBLEM_TYPES = frozenset({"device_offline", "battery_low", "not_worn"})
ALERT_TYPES = HEALTH_ALERT_TYPES | DEVICE_PROBLEM_TYPES
OPEN_STATUSES = frozenset({"open", "acknowledged"})
SEVERITIES = frozenset({"critical", "warning"})
ACK_ROLES = frozenset({"caregiver", "ops"})

STATUS_TEXT = {
    "urgent": "Urgent: check now",
    "attention": "Needs attention",
    "ok": "All readings look normal",
}

# The exact keys of each response object (a route test checks them).
RESIDENT_KEYS = (
    "resident_id",
    "display_name",
    "home_id",
    "status",
    "status_text",
    "wearable",
    "bedroom",
    "vitals",
)
WEARABLE_KEYS = ("device_id", "ts", "battery_pct", "worn", "charging", "online")
BEDROOM_KEYS = ("device_id", "ts", "online")
# No activity_pct: the staff dashboards don't show it (data minimisation).
VITALS_KEYS = ("ts", "hr_bpm", "spo2_pct", "skin_temp_c")
ALERT_KEYS = (
    "alert_id",
    "resident_id",
    "device_id",
    "alert_type",
    "severity",
    "status",
    "event_ts",
    "acknowledged_at",
    "acknowledged_by_role",
    "acknowledged_by",
    "ack_note",
    "escalated_at",
    "escalates_at",
)

# Valid ranges (docs/metrics.md, vitals): anything else becomes None.
VITAL_RANGES: dict[str, tuple[Decimal, Decimal, bool]] = {
    "hr_bpm": (Decimal(0), Decimal(250), True),
    "spo2_pct": (Decimal(50), Decimal(100), True),
    "skin_temp_c": (Decimal("20.0"), Decimal("45.0"), False),
}


# ---- small parsers ----


def parse_ts(value: Any) -> datetime | None:
    if type(value) is not str or not TS_RE.match(value):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError:
        return None


def iso_ms(value: datetime) -> str:
    """ISO 8601 UTC with milliseconds and Z."""
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def _ts_or_none(value: Any) -> str | None:
    return value if parse_ts(value) is not None else None


def _number(
    value: Any, low: Decimal, high: Decimal, integer: bool
) -> int | float | None:
    # DynamoDB numbers arrive as Decimal; bool is an int subclass, so reject it first.
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return None
    value = Decimal(value)
    if not value.is_finite() or not low <= value <= high:
        return None
    if integer:
        return int(value) if value == value.to_integral_value() else None
    return float(value)


def _device_id(pk: Any) -> str | None:
    if not isinstance(pk, str) or not pk.startswith("DEVICE#"):
        return None
    device_id = pk.removeprefix("DEVICE#")
    return device_id if DEVICE_ID_RE.match(device_id) else None


def clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > DISPLAY_NAME_MAX_LEN or not value.isprintable():
        return None
    return value


# ---- registry (patterns 4 and 8) ----


@dataclass(frozen=True)
class Resident:
    resident_id: str
    display_name: str
    home_id: str | None


@dataclass(frozen=True)
class Device:
    device_id: str
    device_type: str
    resident_id: str


def resident_from_item(item: Mapping[str, Any]) -> Resident | None:
    pk = item.get("pk")
    if not isinstance(pk, str) or not pk.startswith("RESIDENT#"):
        return None
    resident_id = pk.removeprefix("RESIDENT#")
    name = clean_name(item.get("display_name"))
    if not RESIDENT_ID_RE.match(resident_id) or name is None:
        return None
    home_id = item.get("home_id")
    if not isinstance(home_id, str) or not HOME_ID_RE.match(home_id):
        home_id = None
    return Resident(resident_id, name, home_id)


def device_from_item(item: Mapping[str, Any]) -> Device | None:
    """An **assigned** device from a by-type DEVICE item; None for a spare or a
    malformed item (the dashboards show only residents' devices)."""
    device_id = _device_id(item.get("pk"))
    resident_id = item.get("resident_id")
    if device_id is None or not isinstance(resident_id, str):
        return None
    if not RESIDENT_ID_RE.match(resident_id):
        return None
    # The ID prefix is the type (docs/metrics.md).
    return Device(device_id, device_id.split("-", 1)[0], resident_id)


# ---- latest (pattern 5) ----


@dataclass(frozen=True)
class Status:
    ts: str
    ts_dt: datetime
    battery_pct: int | None
    worn: bool | None
    charging: bool


def status_from_item(item: Mapping[str, Any]) -> tuple[str, Status] | None:
    device_id = _device_id(item.get("pk"))
    latest = item.get("latest")
    if device_id is None or not isinstance(latest, Mapping):
        return None
    ts = latest.get("ts")
    ts_dt = parse_ts(ts)
    if ts_dt is None:
        return None
    worn = latest.get("worn")
    return device_id, Status(
        ts=ts,
        ts_dt=ts_dt,
        battery_pct=_number(latest.get("battery_pct"), Decimal(0), Decimal(100), True),
        worn=worn if isinstance(worn, bool) else None,
        charging=latest.get("charging") is True,
    )


def vitals_from_item(item: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """(device_id, one vitals entry with exactly VITALS_KEYS), or None if the key or
    `ts` is invalid. Out-of-range values become None."""
    device_id = _device_id(item.get("pk"))
    latest = item.get("latest")
    if device_id is None or not isinstance(latest, Mapping):
        return None
    ts = latest.get("ts")
    if parse_ts(ts) is None:
        return None
    entry: dict[str, Any] = {"ts": ts}
    for key, (low, high, integer) in VITAL_RANGES.items():
        entry[key] = _number(latest.get(key), low, high, integer)
    return device_id, entry


# ---- alerts (pattern 9) ----


@dataclass(frozen=True)
class Alert:
    """One open-index alert, validated. `acknowledged_by_sub` is never returned."""

    alert_id: str
    resident_id: str
    device_id: str
    alert_type: str
    severity: str
    status: str
    event_ts: str
    acknowledged_at: str | None
    acknowledged_by_role: str | None
    acknowledged_by_sub: str | None
    ack_note: str | None
    escalated_at: str | None
    notified_at: datetime | None

    @property
    def is_device_problem(self) -> bool:
        return self.alert_type in DEVICE_PROBLEM_TYPES


def _ack_note(value: Any) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= ACK_NOTE_MAX_LEN:
        return None
    return value


def alert_from_item(item: Mapping[str, Any]) -> Alert | None:
    """An alert from the `open` index, or None if its keys, type, status, or device
    ID are invalid."""
    pk, sk = item.get("pk"), item.get("sk")
    if not isinstance(pk, str) or not pk.startswith("RESIDENT#"):
        return None
    resident_id = pk.removeprefix("RESIDENT#")
    if resident_id != UNASSIGNED and not RESIDENT_ID_RE.match(resident_id):
        return None
    if not isinstance(sk, str) or not sk.startswith("ALERT#"):
        return None
    sk_ts, _, alert_id = sk.removeprefix("ALERT#").rpartition("#")
    if not UUID_RE.match(alert_id):
        return None
    alert_type = item.get("alert_type")
    status = item.get("status")
    device_id = item.get("device_id")
    # isinstance first: a list or map value is unhashable and would raise in the set
    # lookup, failing the whole dashboard instead of skipping one item.
    if not isinstance(alert_type, str) or alert_type not in ALERT_TYPES:
        return None
    if not isinstance(status, str) or status not in OPEN_STATUSES:
        return None
    if not isinstance(device_id, str) or not DEVICE_ID_RE.match(device_id):
        return None
    event_ts = _ts_or_none(item.get("event_ts")) or _ts_or_none(sk_ts)
    if event_ts is None:
        return None
    severity = item.get("severity")
    if not isinstance(severity, str) or severity not in SEVERITIES:
        # Missing on falls stored before vitals alerts: treated as critical everywhere.
        severity = "critical"

    role = item.get("acknowledged_by_role")
    by = item.get("acknowledged_by")
    # Who acknowledged is returned only for device problems (a nurse's acknowledge of a
    # health alert is "the care centre nurse", no name).
    if (
        alert_type not in DEVICE_PROBLEM_TYPES
        or not isinstance(role, str)
        or role not in ACK_ROLES
    ):
        role = None
    by_sub = (
        by
        if role == "caregiver" and isinstance(by, str) and UUID_RE.match(by)
        else None
    )

    return Alert(
        alert_id=alert_id,
        resident_id=resident_id,
        device_id=device_id,
        alert_type=alert_type,
        severity=severity,
        status=status,
        event_ts=event_ts,
        acknowledged_at=_ts_or_none(item.get("acknowledged_at")),
        acknowledged_by_role=role,
        acknowledged_by_sub=by_sub,
        ack_note=_ack_note(item.get("ack_note")),
        escalated_at=_ts_or_none(item.get("escalated_at")),
        notified_at=parse_ts(item.get("notified_at")),
    )


def escalates_at(alert: Alert) -> str | None:
    """notified_at + 300 s on an open health-alert warning from an assigned device that
    isn't escalated yet; None otherwise (device problems never)."""
    if (
        alert.status != "open"
        or alert.severity != "warning"
        or alert.is_device_problem
        or alert.resident_id == UNASSIGNED
        or alert.escalated_at is not None
        or alert.notified_at is None
    ):
        return None
    return iso_ms(alert.notified_at + ESCALATION_DELAY)


def alert_view(alert: Alert, names: Mapping[str, str | None]) -> dict[str, Any]:
    """One alert of the response, with exactly ALERT_KEYS."""
    by = None
    if alert.acknowledged_by_role == "caregiver" and alert.acknowledged_by_sub:
        by = names.get(alert.acknowledged_by_sub)
    return {
        "alert_id": alert.alert_id,
        "resident_id": alert.resident_id,
        "device_id": alert.device_id,
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "status": alert.status,
        "event_ts": alert.event_ts,
        "acknowledged_at": alert.acknowledged_at,
        "acknowledged_by_role": alert.acknowledged_by_role,
        "acknowledged_by": by,
        "ack_note": alert.ack_note,
        "escalated_at": alert.escalated_at,
        "escalates_at": escalates_at(alert),
    }


# ---- residents ----


def is_online(status: Status | None, now: datetime) -> bool:
    return status is not None and now - status.ts_dt <= OFFLINE_AFTER


def _pick(devices: list[Device], statuses: Mapping[str, Status]) -> Device | None:
    """The device the card shows: the one with the newest status, else the lowest ID."""
    best: Device | None = None
    best_key: tuple[bool, datetime] | None = None
    for device in sorted(devices, key=lambda d: d.device_id):
        status = statuses.get(device.device_id)
        key = (status is not None, status.ts_dt if status else NEVER)
        if best_key is None or key > best_key:
            best, best_key = device, key
    return best


def wearable_view(
    device: Device | None, status: Status | None, now: datetime
) -> dict[str, Any] | None:
    if device is None:
        return None
    return {
        "device_id": device.device_id,
        "ts": status.ts if status else None,
        "battery_pct": status.battery_pct if status else None,
        "worn": status.worn if status else None,
        "charging": bool(status and status.charging),
        "online": is_online(status, now),
    }


def bedroom_view(
    device: Device | None, status: Status | None, now: datetime
) -> dict[str, Any] | None:
    if device is None:
        return None
    return {
        "device_id": device.device_id,
        "ts": status.ts if status else None,
        "online": is_online(status, now),
    }


def resident_status(
    alerts: Iterable[Alert],
    vitals: Iterable[Mapping[str, Any]],
    limits: Mapping[str, thresholds.Limits] | None,
) -> str:
    """urgent / attention / ok (data model, Resident status). `alerts` are this
    resident's open-index alerts (open or acknowledged); `vitals` the newest entry of
    each assigned wearable; `limits` the resolved thresholds (None: not needed)."""
    severities = {a.severity for a in alerts}
    if "critical" in severities:
        return "urgent"
    if "warning" in severities:
        return "attention"
    if limits is not None:
        for entry in vitals:
            for metric in thresholds.METRICS:
                if thresholds.is_abnormal(entry.get(metric), limits[metric]):
                    return "attention"
    return "ok"


@dataclass
class Dashboard:
    residents: list[dict[str, Any]]
    alerts: list[dict[str, Any]]
    skipped: int = 0
    caregiver_subs: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Reads:
    """The handler's reads, already deserialized."""

    alert_items: list[Mapping[str, Any]]
    device_items: list[Mapping[str, Any]]
    resident_items: list[Mapping[str, Any]]
    status_items: list[Mapping[str, Any]]
    vitals_items: list[Mapping[str, Any]]


@dataclass
class Parsed:
    alerts: list[Alert]
    residents: dict[str, Resident]
    devices: dict[str, list[Device]]
    statuses: dict[str, Status]
    vitals: dict[str, dict[str, Any]]
    skipped: int

    def caregiver_subs(self) -> set[str]:
        return {a.acknowledged_by_sub for a in self.alerts if a.acknowledged_by_sub}

    def residents_needing_thresholds(self) -> list[str]:
        """Residents whose status depends on their vitals: no open alert, and at least
        one wearable with a newest vitals entry. Sorted, for a stable request."""
        with_alerts = {a.resident_id for a in self.alerts}
        out = []
        for resident_id in self.residents:
            if resident_id in with_alerts:
                continue
            if any(
                d.device_type == WEARABLE and d.device_id in self.vitals
                for d in self.devices.get(resident_id, [])
            ):
                out.append(resident_id)
        return sorted(out)


def parse(reads: Reads) -> Parsed:
    skipped = 0
    alerts: list[Alert] = []
    for item in reads.alert_items:
        alert = alert_from_item(item)
        if alert is None:
            skipped += 1
        else:
            alerts.append(alert)

    residents: dict[str, Resident] = {}
    for item in reads.resident_items:
        resident = resident_from_item(item)
        if resident is not None:
            residents[resident.resident_id] = resident

    devices: dict[str, list[Device]] = {}
    known: set[str] = set()
    for item in reads.device_items:
        device = device_from_item(item)
        if device is not None:
            devices.setdefault(device.resident_id, []).append(device)
            known.add(device.device_id)

    statuses: dict[str, Status] = {}
    for item in reads.status_items:
        parsed = status_from_item(item)
        if parsed is not None and parsed[0] in known:
            statuses[parsed[0]] = parsed[1]

    vitals: dict[str, dict[str, Any]] = {}
    for item in reads.vitals_items:
        parsed = vitals_from_item(item)
        if parsed is not None and parsed[0] in known and parsed[0].startswith(WEARABLE):
            vitals[parsed[0]] = parsed[1]

    return Parsed(alerts, residents, devices, statuses, vitals, skipped)


def build_dashboard(
    parsed: Parsed,
    limits_of: Callable[[str], Mapping[str, thresholds.Limits]],
    names: Mapping[str, str | None],
    now: datetime,
) -> Dashboard:
    """Every resident with a profile (sorted by resident_id) and every valid open-index
    alert (newest first, as the index returns them). `limits_of(resident_id)` gives the
    resolved thresholds; it is called only for residents in
    `parsed.residents_needing_thresholds()`."""
    by_resident: dict[str, list[Alert]] = {}
    for alert in parsed.alerts:
        by_resident.setdefault(alert.resident_id, []).append(alert)
    needs_limits = set(parsed.residents_needing_thresholds())

    residents: list[dict[str, Any]] = []
    for resident_id in sorted(parsed.residents):
        resident = parsed.residents[resident_id]
        devices = sorted(parsed.devices.get(resident_id, []), key=lambda d: d.device_id)
        wearables = [d for d in devices if d.device_type == WEARABLE]
        bedrooms = [d for d in devices if d.device_type == BEDROOM]
        wearable = _pick(wearables, parsed.statuses)
        bedroom = _pick(bedrooms, parsed.statuses)
        newest_vitals = [
            parsed.vitals[d.device_id]
            for d in wearables
            if d.device_id in parsed.vitals
        ]
        limits = limits_of(resident_id) if resident_id in needs_limits else None
        status = resident_status(
            by_resident.get(resident_id, []), newest_vitals, limits
        )
        shown_vitals = parsed.vitals.get(wearable.device_id) if wearable else None
        residents.append(
            {
                "resident_id": resident_id,
                "display_name": resident.display_name,
                "home_id": resident.home_id,
                "status": status,
                "status_text": STATUS_TEXT[status],
                "wearable": wearable_view(
                    wearable,
                    parsed.statuses.get(wearable.device_id) if wearable else None,
                    now,
                ),
                "bedroom": bedroom_view(
                    bedroom,
                    parsed.statuses.get(bedroom.device_id) if bedroom else None,
                    now,
                ),
                # Pattern 5 holds only each device's newest entry (`latest`).
                "vitals": [dict(shown_vitals)] if shown_vitals else [],
            }
        )

    return Dashboard(
        residents=residents,
        alerts=[alert_view(a, names) for a in parsed.alerts],
        skipped=parsed.skipped,
        caregiver_subs=parsed.caregiver_subs(),
    )
