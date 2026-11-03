"""Alert email subject and body (docs/features/alerts-core.md, criterion 18;
docs/features/vitals-alerts.md, criteria 21-22).

Plain text. Times in Singapore time (SGT, UTC+8, no daylight saving) only.
No names, no device ID, no email addresses. Pure functions, no AWS calls.

    Subject: SilverCare CRITICAL alert: Fall - res-0003

    SilverCare alert

    Type: Fall
    Severity: Critical
    Resident: res-0003
    When: 2026-10-18 11:14:07 SGT
    Fall details: confidence 0.91, peak 3.8 g, still for 20 s
    Latest vitals (taken 11:13:00 SGT): heart rate 72 bpm, SpO2 97 %

    Open the SilverCare portal: https://<cloudfront domain>/

    This is an automated message. Do not reply.

Vitals alerts replace the two fall lines with one reading line:

    warning:   Reading: heart rate 128 bpm, above the limit of 120 (previous reading 125 bpm)
    critical:  Reading: heart rate 140 bpm for 10 s, above the device limit of 130
    no pulse:  Reading: no pulse detected for 10 s (heart rate 0 bpm)

Warnings have the subject `SilverCare alert: <label> - <resident_id>`, critical alerts
`SilverCare CRITICAL alert: <label> - <resident_id>`.
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
TYPE_LABELS = {
    "fall": "Fall",
    "hr_high": "High heart rate",
    "hr_low": "Low heart rate",
    "spo2_low": "Low blood oxygen",
    "skin_temp_high": "High skin temperature",
    "skin_temp_low": "Low skin temperature",
}
SEVERITY_LABELS = {"warning": "Warning", "critical": "Critical"}
NO_READING = "no reading"

# metric -> (name in the reading line, unit, decimals or None to print as is)
METRICS: dict[str, tuple[str, str, int | None]] = {
    "hr_bpm": ("heart rate", "bpm", None),
    "spo2_pct": ("SpO2", "%", None),
    "skin_temp_c": ("skin temperature", "°C", 1),
}
NO_PULSE_VALUE = 0

# Alert items stored before severity existed are all falls, which are critical.
DEFAULT_SEVERITY = "critical"


def _type_label(alert_type: str) -> str:
    return TYPE_LABELS.get(alert_type, alert_type)


def _severity_label(severity: str) -> str:
    return SEVERITY_LABELS.get(severity, severity)


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


def subject(alert_type: str, resident_id: str, severity: str) -> str:
    """At most 100 characters (SNS email subject limit)."""
    prefix = (
        "SilverCare CRITICAL alert" if severity == "critical" else "SilverCare alert"
    )
    return f"{prefix}: {_type_label(alert_type)} - {resident_id}"[:SUBJECT_MAX_LEN]


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


def _metric_number(metric: str, value: Any) -> str:
    """A reading or limit: skin temperature with one decimal, the others as they are."""
    decimals = METRICS[metric][2]
    if decimals is not None and isinstance(value, int | float | Decimal):
        return f"{Decimal(str(value)):.{decimals}f}"
    return format_number(value)


def _reading(metric: str, value: Any) -> str:
    """heart rate 128 bpm, SpO2 86 %, skin temperature 38.4 °C"""
    name, unit, _ = METRICS[metric]
    return f"{name} {_metric_number(metric, value)} {unit}"


def _reading_line(details: dict[str, Any], severity: str) -> str:
    """The one reading line of a vitals alert (criterion 22)."""
    metric = details.get("metric")
    if metric not in METRICS:
        return "Reading: unknown"
    value = details.get("value")
    direction = details.get("direction")
    limit = _metric_number(metric, details.get("limit"))
    if severity == "critical":
        held = format_number(details.get("held_s"))
        if metric == "hr_bpm" and value == NO_PULSE_VALUE:
            return f"Reading: no pulse detected for {held} s (heart rate 0 bpm)"
        return (
            f"Reading: {_reading(metric, value)} for {held} s, "
            f"{direction} the device limit of {limit}"
        )
    unit = METRICS[metric][1]
    previous = _metric_number(metric, details.get("previous_value"))
    return (
        f"Reading: {_reading(metric, value)}, {direction} the limit of {limit} "
        f"(previous reading {previous} {unit})"
    )


def body(item: dict[str, Any], resident_id: str, portal_url: str) -> str:
    """The email body for a stored alert item (the item has no resident_id attribute;
    it is in the key)."""
    details = item.get("details") or {}
    severity = item.get("severity") or DEFAULT_SEVERITY
    event_dt = _parse_ts(item.get("event_ts"))
    when = format_when(event_dt) if event_dt else "unknown time"
    lines = [
        "SilverCare alert",
        "",
        f"Type: {_type_label(item['alert_type'])}",
        f"Severity: {_severity_label(severity)}",
        f"Resident: {resident_id}",
        f"When: {when}",
    ]
    if item["alert_type"] == "fall":
        lines += [_fall_details_line(details), _vitals_line(details)]
    else:
        lines.append(_reading_line(details, severity))
    lines += [
        "",
        f"Open the SilverCare portal: {portal_url.rstrip('/')}/",
        "",
        "This is an automated message. Do not reply.",
    ]
    return "\n".join(lines) + "\n"
