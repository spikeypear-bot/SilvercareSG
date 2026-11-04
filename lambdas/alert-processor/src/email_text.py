"""Alert email subject and body (docs/features/alerts-core.md, criterion 18;
docs/features/vitals-alerts.md, criteria 21-22).

Plain text. Times in Singapore time (SGT, UTC+8, no daylight saving) only.
No email addresses, no names except a caregiver's masked name in a follow-up, and no
device ID except in the nurse emails of an unassigned alert (below). Pure functions,
no AWS calls.

    Subject: SilverCare CRITICAL alert: Fall - res-0003

    SilverCare alert

    Type: Fall
    Severity: Critical
    Resident: res-0003
    When: 2026-10-18 11:14:07 SGT
    Fall details: confidence 0.91, peak 3.8 g, still for 20 s
    Latest vitals (taken 11:13:00 SGT): heart rate 72 bpm, SpO2 97 %

    The care centre nurse has also been alerted.

    Open the SilverCare portal: https://<cloudfront domain>/

    This is an automated message. Do not reply.

Vitals alerts replace the two fall lines with one reading line:

    warning:   Reading: heart rate 128 bpm, above the limit of 120 (previous reading 125 bpm)
    critical:  Reading: heart rate 140 bpm for 10 s, above the device limit of 130
    no pulse:  Reading: no pulse detected for 10 s (heart rate 0 bpm)

Warnings have the subject `SilverCare alert: <label> - <resident_id>`, critical alerts
`SilverCare CRITICAL alert: <label> - <resident_id>`. A critical caregiver email adds,
after the reading lines, a blank line and `The care centre nurse has also been
alerted.` (docs/features/alert-routing.md, criterion 8).

Nurse emails sent at once (alert-routing, criterion 6), for critical alerts and every
alert from an unassigned device, have the caregiver subject and body with one line
after Severity:

    Sent to the care centre at once: critical alert.
    Sent to the care centre at once: device not assigned to a resident.

Nurse escalation emails (docs/features/escalation.md, criteria 9-10; warnings only
since alert-routing) have the subject `SilverCare ESCALATED alert: <label> -
<resident_id>`, and the same body with one line after Severity:

    Escalated: no response within 5 minutes of the first alert.

A nurse email for an unassigned alert shows `Resident: unassigned` followed by
`Device: <device_id>` (an opaque ID; caregiver emails never show the device), or
`Device: invalid` if the stored device_id doesn't have the device ID format. A missing
or unknown alert_type in a nurse email gets the label `Alert`.

Follow-up emails (docs/features/alert-cancel.md, criteria 13-14; alert-routing,
criteria 13-15), when an alert is cancelled or acknowledged:

    Subject: SilverCare update: Fall - res-0001 cancelled

    SilverCare update

    The alert below was cancelled by the resident at 2026-11-03 10:00:05 SGT.

    Type: Fall
    Severity: Critical
    Resident: res-0001
    When: 2026-11-03 10:00:00 SGT

    Open the SilverCare portal: https://<cloudfront domain>/

    This is an automated message. Do not reply.

A caregiver cancel says `was cancelled by Ta* Me* Li** (caregiver) at ...`, with the
masked name (mask_name), never the full name. A nurse acknowledge says `was
acknowledged by the care centre nurse at ...` (a shared account, so no name). A cancel
of an acknowledged alert adds `It had been acknowledged by the care centre nurse at
...` after the first line. An acknowledge with a note adds, after When, a blank line,
`Message from the care centre:`, and the note's lines, each quoted with `> `. No reading or details lines. A
missing or unknown alert_type gets the label `Alert`. An unassigned alert's follow-up
(nurses only) adds `Device: <device_id>` after `Resident: unassigned`, or
`Device: invalid`, as in the nurse emails.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from message import DEVICE_ID_RE

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

# Nurse emails (docs/features/escalation.md, criteria 9-10; alert-routing, 6-8).
ESCALATION_LINE = "Escalated: no response within 5 minutes of the first alert."
AT_ONCE_CRITICAL_LINE = "Sent to the care centre at once: critical alert."
AT_ONCE_UNASSIGNED_LINE = (
    "Sent to the care centre at once: device not assigned to a resident."
)
NURSE_ALERTED_LINE = "The care centre nurse has also been alerted."
UNASSIGNED = "unassigned"
UNKNOWN_TYPE_LABEL = "Alert"

# Follow-up emails (docs/features/alert-cancel.md, criteria 13-14).
MASK_KEEP = 2
MASK_CHAR = "*"
UNKNOWN_SEVERITY_LABEL = "Unknown"
CARE_CENTRE_NURSE = "the care centre nurse"
NOTE_HEADING = "Message from the care centre:"
NOTE_QUOTE = "> "


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


def escalation_subject(alert_type: str, resident_id: str) -> str:
    """Nurse email subject, both severities (escalation criterion 9). At most 100
    characters."""
    label = _escalation_label(alert_type)
    return f"SilverCare ESCALATED alert: {label} - {resident_id}"[:SUBJECT_MAX_LEN]


def _escalation_label(alert_type: Any) -> str:
    """The type label, or "Alert" for a missing or unknown alert_type (a hand-edited
    item still escalates, criterion 10)."""
    if isinstance(alert_type, str) and alert_type in TYPE_LABELS:
        return TYPE_LABELS[alert_type]
    return UNKNOWN_TYPE_LABEL


def _device_line(device_id: Any) -> str:
    """Device: <device_id> only if it has the device ID format, else "invalid"."""
    ok = isinstance(device_id, str) and DEVICE_ID_RE.match(device_id)
    return f"Device: {device_id if ok else 'invalid'}"


def body(item: dict[str, Any], resident_id: str, portal_url: str) -> str:
    """The caregiver email body for a stored alert item (the item has no resident_id
    attribute; it is in the key). A critical alert says the nurses were alerted too
    (alert-routing criterion 8)."""
    return _body(item, resident_id, portal_url, nurse_line=None)


def nurse_body(item: dict[str, Any], resident_id: str, portal_url: str) -> str:
    """The nurse email sent at once (alert-routing criterion 6): the caregiver body
    with the at-once line after Severity (the unassigned line for an unassigned device,
    any severity) and, for an unassigned alert, a Device line."""
    line = (
        AT_ONCE_UNASSIGNED_LINE if resident_id == UNASSIGNED else AT_ONCE_CRITICAL_LINE
    )
    return _body(item, resident_id, portal_url, nurse_line=line)


def escalation_body(item: dict[str, Any], resident_id: str, portal_url: str) -> str:
    """The nurse escalation email body (escalation criterion 10): the caregiver body
    with the escalation line after Severity, and, for an unassigned alert, a Device
    line (an opaque ID nurses need to find the device)."""
    return _body(item, resident_id, portal_url, nurse_line=ESCALATION_LINE)


def _body(
    item: dict[str, Any], resident_id: str, portal_url: str, *, nurse_line: str | None
) -> str:
    """nurse_line None: the caregiver email; else a nurse email with that line."""
    details = item.get("details")
    details = details if isinstance(details, dict) else {}
    severity = item.get("severity") or DEFAULT_SEVERITY
    event_dt = _parse_ts(item.get("event_ts"))
    when = format_when(event_dt) if event_dt else "unknown time"
    escalated = nurse_line is not None
    if escalated:
        alert_type = item.get("alert_type")
        label = _escalation_label(alert_type)
    else:
        alert_type = item["alert_type"]
        label = _type_label(alert_type)
    lines = [
        "SilverCare alert",
        "",
        f"Type: {label}",
        f"Severity: {_severity_label(severity)}",
    ]
    if nurse_line is not None:
        lines.append(nurse_line)
    lines.append(f"Resident: {resident_id}")
    if escalated and resident_id == UNASSIGNED:
        lines.append(_device_line(item.get("device_id")))
    lines.append(f"When: {when}")
    if alert_type == "fall":
        lines += [_fall_details_line(details), _vitals_line(details)]
    else:
        lines.append(_reading_line(details, severity))
    if not escalated and severity == "critical":
        lines += ["", NURSE_ALERTED_LINE]
    lines += [
        "",
        f"Open the SilverCare portal: {portal_url.rstrip('/')}/",
        "",
        "This is an automated message. Do not reply.",
    ]
    return "\n".join(lines) + "\n"


# ---- follow-up emails (docs/features/alert-cancel.md) ----


def mask_name(name: str) -> str:
    """PayNow-style masked name (decisions table, criterion 14): each word keeps its
    first 2 characters and every further character becomes "*"; a 1-2 character word
    is shown as is. Words are split on spaces; runs of spaces and leading or trailing
    spaces are dropped. "Tan Mei Ling" -> "Ta* Me* Li**"."""
    words = [word for word in name.split(" ") if word]
    return " ".join(
        word[:MASK_KEEP] + MASK_CHAR * max(len(word) - MASK_KEEP, 0) for word in words
    )


def follow_up_subject(alert_type: Any, resident_id: str, new_status: str) -> str:
    """`SilverCare update: <label> - <resident_id> cancelled` (or acknowledged), no
    name. At most 100 characters."""
    label = _escalation_label(alert_type)
    return f"SilverCare update: {label} - {resident_id} {new_status}"[:SUBJECT_MAX_LEN]


def _changed_by(by_role: str, by_name: str | None) -> str:
    """ "the resident", "the care centre nurse", or a caregiver's masked name plus
    " (caregiver)"."""
    if by_role == "nurse":
        return CARE_CENTRE_NURSE
    if by_role == "caregiver" and by_name is not None:
        return f"{mask_name(by_name)} (caregiver)"
    return "the resident"


def follow_up_body(
    item: dict[str, Any],
    resident_id: str,
    portal_url: str,
    *,
    new_status: str,
    changed_at: datetime,
    by_role: str,
    by_name: str | None,
    note: str | None = None,
) -> str:
    """The follow-up body (alert-routing criterion 15): who changed the alert and when
    (for a cancel of an acknowledged alert, also when the nurse acknowledged it), then
    the alert's type, severity, resident, and time, then the nurse's note if there is
    one (each line quoted with "> "). No reading or details; a Device line only for an
    unassigned alert."""
    severity = item.get("severity") or DEFAULT_SEVERITY
    event_dt = _parse_ts(item.get("event_ts"))
    when = format_when(event_dt) if event_dt else "unknown time"
    changed_by = _changed_by(by_role, by_name)
    lines = [
        "SilverCare update",
        "",
        f"The alert below was {new_status} by {changed_by} at {format_when(changed_at)}.",
    ]
    if new_status == "cancelled" and "acknowledged_at" in item:
        acked_dt = _parse_ts(item.get("acknowledged_at"))
        acked = format_when(acked_dt) if acked_dt else "an unknown time"
        lines.append(f"It had been acknowledged by {CARE_CENTRE_NURSE} at {acked}.")
    lines += [
        "",
        f"Type: {_escalation_label(item.get('alert_type'))}",
        f"Severity: {SEVERITY_LABELS.get(severity, UNKNOWN_SEVERITY_LABEL)}",
        f"Resident: {resident_id}",
    ]
    if resident_id == UNASSIGNED:
        lines.append(_device_line(item.get("device_id")))
    lines.append(f"When: {when}")
    if note is not None:
        # Quoted, so a note can't pass for SilverCare's own lines (e.g. a fake portal
        # link): alert-routing criterion 15.
        lines += ["", NOTE_HEADING, *(NOTE_QUOTE + line for line in note.split("\n"))]
    lines += [
        "",
        f"Open the SilverCare portal: {portal_url.rstrip('/')}/",
        "",
        "This is an automated message. Do not reply.",
    ]
    return "\n".join(lines) + "\n"
