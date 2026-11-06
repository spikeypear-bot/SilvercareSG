"""Nurse email sent at once and the caregiver "nurse alerted" line
(docs/features/alert-routing.md, criteria 6-8 and 19). Pure, no moto."""

from __future__ import annotations

from decimal import Decimal

import email_text
import pytest
from events import DEVICE, EVENT_TS, NO_PULSE_DETAILS, RESIDENT, VITALS_DETAILS

PORTAL = "https://d0000example.cloudfront.net"
WHEN_LINE = "When: 2026-10-18 11:14:07 SGT"
AT_ONCE_CRITICAL = "Sent to the care centre at once: critical alert."
AT_ONCE_UNASSIGNED = (
    "Sent to the care centre at once: device not assigned to a resident."
)
NURSE_ALERTED = "The care centre nurse has also been alerted."
FALL_DETAILS_LINE = "Fall details: confidence 0.91, peak 3.8 g, still for 20 s"
FOOTER = (
    "",
    f"Open the SilverCare portal: {PORTAL}/",
    "",
    "This is an automated message. Do not reply.",
)


def as_stored(details: dict) -> dict:
    return {
        k: Decimal(str(v))
        if isinstance(v, int | float) and not isinstance(v, bool)
        else v
        for k, v in details.items()
    }


def fall_item(**extra) -> dict:
    return {
        "pk": f"RESIDENT#{RESIDENT}",
        "sk": f"ALERT#{EVENT_TS}#x",
        "alert_type": "fall",
        "severity": "critical",
        "device_id": DEVICE,
        "event_ts": EVENT_TS,
        "details": {
            "confidence": Decimal("0.91"),
            "peak_g": Decimal("3.8"),
            "still_s": 20,
        },
        **extra,
    }


def vitals_item(alert_type: str, severity: str, details: dict | None = None) -> dict:
    return {
        "pk": f"RESIDENT#{RESIDENT}",
        "sk": f"ALERT#{EVENT_TS}#x",
        "alert_type": alert_type,
        "severity": severity,
        "device_id": DEVICE,
        "event_ts": EVENT_TS,
        "details": as_stored(details or VITALS_DETAILS[(alert_type, severity)]),
    }


def lines(*body_lines: str) -> str:
    return "\n".join(body_lines) + "\n"


# ---- nurse email at once (criterion 6) ----


def test_critical_fall_nurse_body_is_exact():
    body = email_text.nurse_body(fall_item(), RESIDENT, PORTAL)

    assert body == lines(
        "SilverCare alert",
        "",
        "Type: Fall",
        "Severity: Critical",
        AT_ONCE_CRITICAL,
        f"Resident: {RESIDENT}",
        WHEN_LINE,
        FALL_DETAILS_LINE,
        "Latest vitals: none received",
        *FOOTER,
    )


def test_critical_vitals_nurse_body_is_exact():
    body = email_text.nurse_body(vitals_item("hr_high", "critical"), RESIDENT, PORTAL)

    assert body == lines(
        "SilverCare alert",
        "",
        "Type: High heart rate",
        "Severity: Critical",
        AT_ONCE_CRITICAL,
        f"Resident: {RESIDENT}",
        WHEN_LINE,
        "Reading: heart rate 140 bpm for 10 s, above the device limit of 130",
        *FOOTER,
    )


def test_no_pulse_nurse_body_keeps_the_no_pulse_line():
    item = vitals_item("hr_low", "critical", NO_PULSE_DETAILS)

    body = email_text.nurse_body(item, RESIDENT, PORTAL)

    assert "Reading: no pulse detected for 10 s (heart rate 0 bpm)" in body
    assert AT_ONCE_CRITICAL in body


@pytest.mark.parametrize("severity", ["warning", "critical"])
def test_unassigned_nurse_body_is_exact_for_any_severity(severity):
    item = vitals_item("spo2_low", severity)

    body = email_text.nurse_body(item, "unassigned", PORTAL)

    reading = (
        "Reading: SpO2 89 %, below the limit of 92 (previous reading 90 %)"
        if severity == "warning"
        else "Reading: SpO2 85 % for 12 s, below the device limit of 88"
    )
    assert body == lines(
        "SilverCare alert",
        "",
        "Type: Low blood oxygen",
        f"Severity: {severity.capitalize()}",
        AT_ONCE_UNASSIGNED,
        "Resident: unassigned",
        f"Device: {DEVICE}",
        WHEN_LINE,
        reading,
        *FOOTER,
    )


def test_unassigned_fall_nurse_body_uses_the_unassigned_line_not_critical():
    body = email_text.nurse_body(fall_item(), "unassigned", PORTAL)

    assert AT_ONCE_UNASSIGNED in body
    assert AT_ONCE_CRITICAL not in body
    assert f"\nResident: unassigned\nDevice: {DEVICE}\n" in body


@pytest.mark.parametrize("item", [fall_item(), vitals_item("hr_high", "critical")])
def test_nurse_body_never_has_the_caregiver_nurse_alerted_line(item):
    for resident_id in (RESIDENT, "unassigned"):
        assert NURSE_ALERTED not in email_text.nurse_body(item, resident_id, PORTAL)


def test_assigned_nurse_body_has_no_device_line():
    body = email_text.nurse_body(fall_item(), RESIDENT, PORTAL)

    assert "Device:" not in body
    assert DEVICE not in body


@pytest.mark.parametrize(
    "item",
    [
        fall_item(),
        vitals_item("hr_high", "critical"),
        vitals_item("spo2_low", "critical"),
    ],
    ids=["fall", "hr_high", "spo2_low"],
)
def test_critical_nurse_body_is_the_caregiver_body_with_the_at_once_line(item):
    caregiver = email_text.body(item, RESIDENT, PORTAL).splitlines()
    nurse = email_text.nurse_body(item, RESIDENT, PORTAL).splitlines()

    j = caregiver.index(NURSE_ALERTED)
    caregiver = caregiver[: j - 1] + caregiver[j + 1 :]
    i = caregiver.index("Severity: Critical")
    assert nurse == caregiver[: i + 1] + [AT_ONCE_CRITICAL] + caregiver[i + 1 :]


def test_nurse_body_of_item_without_severity_is_critical():
    item = fall_item()
    del item["severity"]

    body = email_text.nurse_body(item, RESIDENT, PORTAL)

    assert (
        "\nSeverity: Critical\nSent to the care centre at once: critical alert.\n"
        in (body)
    )


@pytest.mark.parametrize("device_id", ["Test Resident A", "wearable-07", 7, None])
def test_unassigned_nurse_body_with_bad_device_id_says_invalid(device_id):
    item = fall_item()
    if device_id is None:
        del item["device_id"]
    else:
        item["device_id"] = device_id

    body = email_text.nurse_body(item, "unassigned", PORTAL)

    assert "\nResident: unassigned\nDevice: invalid\n" in body
    assert "Test Resident" not in body


@pytest.mark.parametrize("alert_type", [None, "hr_weird", "Test Resident A"])
def test_nurse_body_with_missing_or_unknown_type_uses_label_alert(alert_type):
    item = vitals_item("hr_high", "critical")
    if alert_type is None:
        del item["alert_type"]
    else:
        item["alert_type"] = alert_type

    body = email_text.nurse_body(item, RESIDENT, PORTAL)

    assert body.splitlines()[2] == "Type: Alert"
    assert "Test Resident" not in body


# ---- caregiver email (criterion 8) ----


@pytest.mark.parametrize(
    "item",
    [
        fall_item(),
        vitals_item("hr_high", "critical"),
        vitals_item("hr_low", "critical", NO_PULSE_DETAILS),
    ],
    ids=["fall", "hr_high", "no-pulse"],
)
def test_critical_caregiver_body_adds_the_nurse_line_after_the_details(item):
    body_lines = email_text.body(item, RESIDENT, PORTAL).splitlines()

    i = body_lines.index(NURSE_ALERTED)
    assert body_lines[i - 1] == ""
    assert body_lines[i - 2] != ""
    assert body_lines[i + 1 :] == list(FOOTER)
    assert body_lines.index(WHEN_LINE) < i


def test_caregiver_body_of_item_without_severity_has_the_nurse_line():
    item = fall_item()
    del item["severity"]

    assert NURSE_ALERTED in email_text.body(item, RESIDENT, PORTAL)


@pytest.mark.parametrize(
    "alert_type", ["hr_high", "hr_low", "spo2_low", "skin_temp_high", "skin_temp_low"]
)
def test_warning_caregiver_body_has_no_nurse_line(alert_type):
    body = email_text.body(vitals_item(alert_type, "warning"), RESIDENT, PORTAL)

    assert NURSE_ALERTED not in body
    assert "care centre" not in body


def test_caregiver_body_never_has_an_at_once_line():
    for item in (fall_item(), vitals_item("hr_high", "warning")):
        body = email_text.body(item, RESIDENT, PORTAL)
        assert "Sent to the care centre" not in body
