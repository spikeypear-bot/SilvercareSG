"""Alert email subject and body (docs/features/alerts-core.md, criterion 18).

Plain text. Times in Singapore time (SGT, UTC+8, no daylight saving) only.
No names, no device ID, no email addresses. Pure functions, no AWS calls.

    Subject: SilverCare alert: Fall - res-0003

    SilverCare alert

    Type: Fall
    Resident: res-0003
    When: 2026-10-18 11:14:07 SGT
    Fall details: confidence 0.91, peak 3.8 g, still for 20 s
    Latest vitals (taken 11:13:00 SGT): heart rate 72 bpm, SpO2 97 %

    Open the SilverCare portal: https://<cloudfront domain>/

    This is an automated message. Do not reply.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

# Singapore has been UTC+8 all year since 1982. A fixed offset avoids depending on the
# runtime's time zone database.
SGT = timezone(timedelta(hours=8), "SGT")
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

SUBJECT_MAX_LEN = 100
TYPE_LABELS = {"fall": "Fall"}
NO_READING = "no reading"


def _type_label(alert_type: str) -> str:
    return TYPE_LABELS.get(alert_type, alert_type)


def format_number(value: Any) -> str:
    """0.91 -> "0.91", Decimal("20") -> "20", no exponent notation."""
    if isinstance(value, Decimal):
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text
    return str(value)


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, TS_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def format_when(value: datetime) -> str:
    """2026-10-18 11:14:07 SGT"""
    return value.astimezone(SGT).strftime("%Y-%m-%d %H:%M:%S") + " SGT"


def format_taken(value: datetime) -> str:
    """11:13:00 SGT"""
    return value.astimezone(SGT).strftime("%H:%M:%S") + " SGT"


def subject(alert_type: str, resident_id: str) -> str:
    """At most 100 characters (SNS email subject limit)."""
    return f"SilverCare alert: {_type_label(alert_type)} - {resident_id}"[
        :SUBJECT_MAX_LEN
    ]


def _fall_details_line(details: dict[str, Any]) -> str:
    return (
        f"Fall details: confidence {format_number(details.get('confidence'))}, "
        f"peak {format_number(details.get('peak_g'))} g, "
        f"still for {format_number(details.get('still_s'))} s"
    )


def _vital(value: Any, unit: str) -> str:
    return NO_READING if value is None else f"{format_number(value)} {unit}"


def _vitals_line(details: dict[str, Any]) -> str:
    vitals = details.get("latest_vitals")
    if not isinstance(vitals, dict):
        return "Latest vitals: none received"
    taken_dt = _parse_ts(vitals.get("ts"))
    taken = format_taken(taken_dt) if taken_dt else "unknown time"
    return (
        f"Latest vitals (taken {taken}): "
        f"heart rate {_vital(vitals.get('hr_bpm'), 'bpm')}, "
        f"SpO2 {_vital(vitals.get('spo2_pct'), '%')}"
    )


def body(item: dict[str, Any], resident_id: str, portal_url: str) -> str:
    """The email body for a stored alert item (the item has no resident_id attribute;
    it is in the key)."""
    details = item.get("details") or {}
    event_dt = _parse_ts(item.get("event_ts"))
    when = format_when(event_dt) if event_dt else "unknown time"
    lines = [
        "SilverCare alert",
        "",
        f"Type: {_type_label(item['alert_type'])}",
        f"Resident: {resident_id}",
        f"When: {when}",
    ]
    if item["alert_type"] == "fall":
        lines += [_fall_details_line(details), _vitals_line(details)]
    lines += [
        "",
        f"Open the SilverCare portal: {portal_url.rstrip('/')}/",
        "",
        "This is an automated message. Do not reply.",
    ]
    return "\n".join(lines) + "\n"
