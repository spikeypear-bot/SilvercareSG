"""Pure logic for GET /residents/{resident_id} and GET /me/residents (docs/data-model.md,
patterns 10, 11, 12, 18): no AWS calls.

Everything is built field by field with fixed keys, so what the reads return but the
pages don't show never reaches the response: profile fields beyond `display_name` and
`home_id` (medical notes, contacts, year of birth, photo key), `received_at`,
`clock_skew`, alert `details`, `notified_at`, `resolve_note`, `source_message_id`, and
every `sub`.

Rules shared with GET /dashboard (lambdas/api-dashboard/src/dashboard.py, keep in step):
`severity` "critical" when missing; `escalates_at` = `notified_at` + 300 s on an open
health-alert warning that isn't escalated; resident `status` / `status_text`; offline
after 15 minutes without a `status`; with two devices of a type, the one with the newest
status is shown.

Latest entries (pattern 11) older than the device's `assigned_at` are dropped (data
model, Device assignment: the second safeguard). An entry whose required fields are
invalid is dropped too, so the page's types hold.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import thresholds

WEARABLE = "wearable"
BEDROOM = "bedroom"
MESSAGES = {
    WEARABLE: ("vitals", "fall", "status"),
    BEDROOM: ("motion", "bed", "status"),
}
MAX_ENTRIES = 5
OFFLINE_AFTER = timedelta(minutes=15)
ESCALATION_DELAY = timedelta(seconds=300)
NEVER = datetime.min.replace(tzinfo=UTC)

DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-[0-9]{4}\Z")
RESIDENT_ID_RE = re.compile(r"^res-[0-9]{4}\Z")
HOME_ID_RE = re.compile(r"^home-[0-9]{4}\Z")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z\Z")
FW_MAX_LEN = 32
NAME_MAX_LEN = 100
NOTE_MAX_LEN = 500

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
ALERT_STATUSES = frozenset({"open", "acknowledged", "cancelled", "resolved"})
OPEN_STATUSES = frozenset({"open", "acknowledged"})
STATUS_TEXT = {
    "urgent": "Urgent: check now",
    "attention": "Needs attention",
    "ok": "All readings look normal",
}

# Exact keys (the route's response).
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
    "resolved_at",
    "resolved_by",
    "cancelled_at",
    "cancelled_by_role",
    "cancelled_by",
)


# ---- parsers ----


def parse_ts(value: Any) -> datetime | None:
    if type(value) is not str or not TS_RE.match(value):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError:
        return None


def iso_ms(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def _ts(value: Any) -> str | None:
    return value if parse_ts(value) is not None else None


def _num(value: Any, low: str, high: str | None, integer: bool) -> int | float | None:
    """A DynamoDB number in [low, high] as int or float, else None. Compared as
    Decimal (lambda-python skill)."""
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return None
    value = Decimal(value)
    if not value.is_finite() or value < Decimal(low):
        return None
    if high is not None and value > Decimal(high):
        return None
    if integer:
        return int(value) if value == value.to_integral_value() else None
    return float(value)


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _fw(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > FW_MAX_LEN:
        return None
    return value if value.isprintable() else None


def clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > NAME_MAX_LEN or not value.isprintable():
        return None
    return value


# ---- latest entries (pattern 11) ----


def _entry(
    message: str, device_type: str, raw: Mapping[str, Any]
) -> dict[str, Any] | None:
    """One entry with exactly the page's keys, or None if a required field is invalid."""
    message_id, ts = raw.get("message_id"), raw.get("ts")
    if not isinstance(message_id, str) or not UUID_RE.match(message_id.lower()):
        return None
    if parse_ts(ts) is None:
        return None
    out: dict[str, Any] = {"message_id": message_id, "ts": ts}
    if message == "vitals":
        out["hr_bpm"] = _num(raw.get("hr_bpm"), "0", "250", True)
        out["spo2_pct"] = _num(raw.get("spo2_pct"), "50", "100", True)
        out["skin_temp_c"] = _num(raw.get("skin_temp_c"), "20.0", "45.0", False)
        out["activity_pct"] = _num(raw.get("activity_pct"), "0", "100", True)
        return out
    if message == "fall":
        fields = {
            "confidence": _num(raw.get("confidence"), "0", "1", False),
            "peak_g": _num(raw.get("peak_g"), "0", None, False),
            "still_s": _num(raw.get("still_s"), "0", None, False),
        }
    elif message == "motion":
        fields = {"motion": _bool(raw.get("motion"))}
    elif message == "bed":
        fields = {"occupied": _bool(raw.get("occupied"))}
    elif device_type == WEARABLE:  # status
        fields = {
            "battery_pct": _num(raw.get("battery_pct"), "0", "100", True),
            "worn": _bool(raw.get("worn")),
            "charging": _bool(raw.get("charging")),
            "fw_version": _fw(raw.get("fw_version")),
        }
    else:  # bedroom status: battery_pct null when mains-powered
        fw = _fw(raw.get("fw_version"))
        if fw is None:
            return None
        out["battery_pct"] = _num(raw.get("battery_pct"), "0", "100", True)
        out["fw_version"] = fw
        return out
    if any(v is None for v in fields.values()):
        return None
    out.update(fields)
    return out


def entries(
    message: str, device_type: str, raw_entries: Any, assigned_at: datetime | None
) -> list[dict[str, Any]]:
    """The stored entries, newest first, at most 5, none older than `assigned_at`."""
    if not isinstance(raw_entries, list):
        return []
    out = []
    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            continue
        entry = _entry(message, device_type, raw)
        if entry is None:
            continue
        ts = parse_ts(entry["ts"])
        if assigned_at is not None and ts is not None and ts < assigned_at:
            continue
        out.append(entry)
    out.sort(key=lambda e: e["ts"], reverse=True)
    return out[:MAX_ENTRIES]


@dataclass(frozen=True)
class Device:
    device_id: str
    device_type: str
    latest: dict[str, list[dict[str, Any]]]

    @property
    def last_status(self) -> dict[str, Any] | None:
        status = self.latest.get("status") or []
        return status[0] if status else None

    def status_dt(self) -> datetime:
        s = self.last_status
        return parse_ts(s["ts"]) or NEVER if s else NEVER

    def online(self, now: datetime) -> bool:
        s = self.last_status
        return s is not None and now - self.status_dt() <= OFFLINE_AFTER


def build_devices(
    device_rows: Iterable[tuple[str, str, Any]],
    latest_items: Mapping[tuple[str, str], Any],
) -> list[Device]:
    """Devices from (device_id, device_type, assigned_at) rows, each with its entries
    by message type. `latest_items[(device_id, message)]` is the stored `entries`."""
    out = []
    for device_id, device_type, assigned_at in device_rows:
        if not DEVICE_ID_RE.match(device_id) or device_type not in MESSAGES:
            continue
        since = parse_ts(assigned_at)
        latest = {
            m: entries(m, device_type, latest_items.get((device_id, m)), since)
            for m in MESSAGES[device_type]
        }
        out.append(Device(device_id, device_type, latest))
    return sorted(out, key=lambda d: d.device_id)


def pick(devices: Iterable[Device], device_type: str) -> Device | None:
    """The device of that type with the newest status, else the lowest ID."""
    best: Device | None = None
    best_key: tuple[bool, datetime] | None = None
    for d in devices:
        if d.device_type != device_type:
            continue
        key = (d.last_status is not None, d.status_dt())
        if best_key is None or key > best_key:
            best, best_key = d, key
    return best


# ---- alerts (pattern 12) ----


@dataclass(frozen=True)
class Alert:
    item: dict[str, Any]  # validated fields, subs included (never returned)

    def get(self, key: str) -> Any:
        return self.item.get(key)


def alert_from_item(item: Mapping[str, Any]) -> Alert | None:
    pk, sk = item.get("pk"), item.get("sk")
    if (
        not isinstance(pk, str)
        or not isinstance(sk, str)
        or not sk.startswith("ALERT#")
    ):
        return None
    resident_id = pk.removeprefix("RESIDENT#")
    sk_ts, _, alert_id = sk.removeprefix("ALERT#").rpartition("#")
    alert_type, status, device_id = (
        item.get("alert_type"),
        item.get("status"),
        item.get("device_id"),
    )
    if (
        not UUID_RE.match(alert_id)
        or not isinstance(alert_type, str)
        or not isinstance(status, str)
    ):
        return None
    if (
        alert_type not in HEALTH_ALERT_TYPES | DEVICE_PROBLEM_TYPES
        or status not in ALERT_STATUSES
    ):
        return None
    if not isinstance(device_id, str) or not DEVICE_ID_RE.match(device_id):
        return None
    event_ts = _ts(item.get("event_ts")) or _ts(sk_ts)
    if event_ts is None:
        return None
    severity = item.get("severity")
    if not isinstance(severity, str) or severity not in ("critical", "warning"):
        severity = "critical"
    device_problem = alert_type in DEVICE_PROBLEM_TYPES

    ack_role = item.get("acknowledged_by_role")
    if (
        not device_problem
        or not isinstance(ack_role, str)
        or ack_role not in ("caregiver", "ops")
    ):
        ack_role = None
    cancel_role = item.get("cancelled_by_role")
    if not isinstance(cancel_role, str) or cancel_role not in ("resident", "caregiver"):
        cancel_role = None
    resolve_role = item.get("resolved_by_role")
    note = item.get("ack_note")

    def sub(value: Any) -> str | None:
        return value if isinstance(value, str) and UUID_RE.match(value) else None

    return Alert(
        {
            "alert_id": alert_id,
            "resident_id": resident_id,
            "device_id": device_id,
            "alert_type": alert_type,
            "severity": severity,
            "status": status,
            "event_ts": event_ts,
            "device_problem": device_problem,
            "acknowledged_at": _ts(item.get("acknowledged_at")),
            "acknowledged_by_role": ack_role,
            "ack_by_sub": sub(item.get("acknowledged_by"))
            if ack_role == "caregiver"
            else None,
            "ack_note": note
            if isinstance(note, str) and 1 <= len(note) <= NOTE_MAX_LEN
            else None,
            "escalated_at": _ts(item.get("escalated_at")),
            "notified_at": parse_ts(item.get("notified_at")),
            "resolved_at": _ts(item.get("resolved_at")),
            "resolved_by_sub": (
                sub(item.get("resolved_by"))
                if resolve_role in ("nurse", "doctor")
                else None
            ),
            "cancelled_at": _ts(item.get("cancelled_at")),
            "cancelled_by_role": cancel_role,
            "cancelled_by_sub": sub(item.get("cancelled_by"))
            if cancel_role == "caregiver"
            else None,
        }
    )


def actors(alerts: Iterable[Alert]) -> set[tuple[str, str]]:
    """(sub, kind) pairs whose names the response needs."""
    out = set()
    for a in alerts:
        for key, kind in (
            ("ack_by_sub", "caregiver"),
            ("cancelled_by_sub", "caregiver"),
            ("resolved_by_sub", "staff"),
        ):
            if a.get(key):
                out.add((a.get(key), kind))
    return out


def escalates_at(a: Alert) -> str | None:
    if (
        a.get("status") != "open"
        or a.get("severity") != "warning"
        or a.get("device_problem")
        or a.get("escalated_at") is not None
        or a.get("notified_at") is None
    ):
        return None
    return iso_ms(a.get("notified_at") + ESCALATION_DELAY)


def alert_view(a: Alert, names: Mapping[tuple[str, str], str | None]) -> dict[str, Any]:
    def name(key: str, kind: str) -> str | None:
        sub = a.get(key)
        return names.get((sub, kind)) if sub else None

    return {
        "alert_id": a.get("alert_id"),
        "resident_id": a.get("resident_id"),
        "device_id": a.get("device_id"),
        "alert_type": a.get("alert_type"),
        "severity": a.get("severity"),
        "status": a.get("status"),
        "event_ts": a.get("event_ts"),
        "acknowledged_at": a.get("acknowledged_at"),
        "acknowledged_by_role": a.get("acknowledged_by_role"),
        "acknowledged_by": name("ack_by_sub", "caregiver"),
        "ack_note": a.get("ack_note"),
        "escalated_at": a.get("escalated_at"),
        "escalates_at": escalates_at(a),
        "resolved_at": a.get("resolved_at"),
        "resolved_by": name("resolved_by_sub", "staff"),
        "cancelled_at": a.get("cancelled_at"),
        "cancelled_by_role": a.get("cancelled_by_role"),
        "cancelled_by": name("cancelled_by_sub", "caregiver"),
    }


# ---- resident ----


def resident_status(
    alerts: Iterable[Alert],
    devices: Iterable[Device],
    limits: Mapping[str, thresholds.Limits],
) -> str:
    """urgent / attention / ok (data model, Resident status)."""
    severities = {a.get("severity") for a in alerts if a.get("status") in OPEN_STATUSES}
    if "critical" in severities:
        return "urgent"
    if "warning" in severities:
        return "attention"
    for d in devices:
        vitals = d.latest.get("vitals") or []
        if d.device_type == WEARABLE and vitals:
            for metric in thresholds.METRICS:
                if thresholds.is_abnormal(vitals[0].get(metric), limits[metric]):
                    return "attention"
    return "ok"


def _limit(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def thresholds_view(
    limits: Mapping[str, thresholds.Limits],
    item: Mapping[str, Any] | None,
    is_staff: bool,
) -> dict[str, Any]:
    """Resolved limits; `updated_at` / `updated_by` (the stored name, never the sub) for
    staff only, null otherwise or when never changed."""
    updated_at = _ts((item or {}).get("updated_at")) if is_staff else None
    updated_by = clean_name((item or {}).get("updated_by_name")) if is_staff else None
    return {
        "hr_bpm": {
            "min": _limit(limits["hr_bpm"].min),
            "max": _limit(limits["hr_bpm"].max),
        },
        "spo2_pct": {"min": _limit(limits["spo2_pct"].min)},
        "skin_temp_c": {
            "min": _limit(limits["skin_temp_c"].min),
            "max": _limit(limits["skin_temp_c"].max),
        },
        "updated_at": updated_at,
        "updated_by": updated_by,
    }


def resident_view(
    resident_id: str,
    profile: Mapping[str, Any],
    devices: list[Device],
    alerts: list[Alert],
    limits: Mapping[str, thresholds.Limits],
    now: datetime,
) -> dict[str, Any]:
    """The dashboard-shaped resident (GET /dashboard's keys), with up to 5 vitals."""
    wearable, bedroom = pick(devices, WEARABLE), pick(devices, BEDROOM)
    home_id = profile.get("home_id")
    status = resident_status(alerts, devices, limits)
    ws = wearable.last_status if wearable else None
    bs = bedroom.last_status if bedroom else None
    return {
        "resident_id": resident_id,
        "display_name": clean_name(profile.get("display_name")) or resident_id,
        "home_id": home_id
        if isinstance(home_id, str) and HOME_ID_RE.match(home_id)
        else None,
        "status": status,
        "status_text": STATUS_TEXT[status],
        "wearable": wearable
        and {
            "device_id": wearable.device_id,
            "ts": ws["ts"] if ws else None,
            "battery_pct": ws["battery_pct"] if ws else None,
            "worn": ws["worn"] if ws else None,
            "charging": bool(ws and ws["charging"]),
            "online": wearable.online(now),
        },
        "bedroom": bedroom
        and {
            "device_id": bedroom.device_id,
            "ts": bs["ts"] if bs else None,
            "online": bedroom.online(now),
        },
        "vitals": [
            {
                k: e[k]
                for k in ("ts", "hr_bpm", "spo2_pct", "skin_temp_c", "activity_pct")
            }
            for e in (wearable.latest.get("vitals", []) if wearable else [])
        ],
    }


def device_view(device: Device | None, now: datetime) -> dict[str, Any] | None:
    if device is None:
        return None
    return {
        "device_id": device.device_id,
        "device_type": device.device_type,
        "online": device.online(now),
        "latest": {m: list(device.latest[m]) for m in MESSAGES[device.device_type]},
    }
