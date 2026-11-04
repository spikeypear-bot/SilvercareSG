"""Nurse escalation email subject and body (docs/features/escalation.md, criteria 9-10,
17). Pure, no moto."""

from __future__ import annotations

from decimal import Decimal

import email_text
import pytest
from events import DEVICE, EVENT_TS, NO_PULSE_DETAILS, RESIDENT, VITALS_DETAILS

PORTAL = "https://d0000example.cloudfront.net"
WHEN_LINE = "When: 2026-10-18 11:14:07 SGT"
ESCALATION_LINE = "Escalated: no response within 5 minutes of the first alert."
NURSE_ALERTED_LINE = "The care centre nurse has also been alerted."
FALL_DETAILS_LINE = "Fall details: confidence 0.91, peak 3.8 g, still for 20 s"

LABELS = {
    "fall": "Fall",
    "hr_high": "High heart rate",
    "hr_low": "Low heart rate",
    "spo2_low": "Low blood oxygen",
    "skin_temp_high": "High skin temperature",
    "skin_temp_low": "Low skin temperature",
}


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
    if details is None:
        details = VITALS_DETAILS[(alert_type, severity)]
    return {
        "pk": f"RESIDENT#{RESIDENT}",
        "sk": f"ALERT#{EVENT_TS}#x",
        "alert_type": alert_type,
        "severity": severity,
        "device_id": DEVICE,
        "event_ts": EVENT_TS,
        "details": as_stored(details),
    }


def lines(*body_lines: str) -> str:
    return "\n".join(body_lines) + "\n"


FOOTER = (
    "",
    f"Open the SilverCare portal: {PORTAL}/",
    "",
    "This is an automated message. Do not reply.",
)


# ---- subject (criterion 9) ----


@pytest.mark.parametrize(("alert_type", "label"), sorted(LABELS.items()))
def test_escalation_subject_per_type(alert_type, label):
    assert (
        email_text.escalation_subject(alert_type, RESIDENT)
        == f"SilverCare ESCALATED alert: {label} - {RESIDENT}"
    )


def test_fall_escalation_subject_is_exact():
    assert (
        email_text.escalation_subject("fall", RESIDENT)
        == "SilverCare ESCALATED alert: Fall - res-0003"
    )


def test_unassigned_escalation_subject():
    assert (
        email_text.escalation_subject("hr_high", "unassigned")
        == "SilverCare ESCALATED alert: High heart rate - unassigned"
    )


@pytest.mark.parametrize("alert_type", [*LABELS, "x" * 200])
@pytest.mark.parametrize("resident_id", [RESIDENT, "unassigned"])
def test_escalation_subject_is_at_most_100_characters(alert_type, resident_id):
    assert len(email_text.escalation_subject(alert_type, resident_id)) <= 100


# ---- body (criterion 10) ----


def test_fall_escalation_body_is_exact():
    body = email_text.escalation_body(fall_item(), RESIDENT, PORTAL)

    assert body == lines(
        "SilverCare alert",
        "",
        "Type: Fall",
        "Severity: Critical",
        ESCALATION_LINE,
        f"Resident: {RESIDENT}",
        WHEN_LINE,
        FALL_DETAILS_LINE,
        "Latest vitals: none received",
        *FOOTER,
    )


def test_warning_escalation_body_is_exact():
    body = email_text.escalation_body(
        vitals_item("hr_high", "warning"), RESIDENT, PORTAL
    )

    assert body == lines(
        "SilverCare alert",
        "",
        "Type: High heart rate",
        "Severity: Warning",
        ESCALATION_LINE,
        f"Resident: {RESIDENT}",
        WHEN_LINE,
        "Reading: heart rate 128 bpm, above the limit of 120 (previous reading 125 bpm)",
        *FOOTER,
    )


def test_critical_escalation_body_is_exact():
    body = email_text.escalation_body(
        vitals_item("hr_high", "critical"), RESIDENT, PORTAL
    )

    assert body == lines(
        "SilverCare alert",
        "",
        "Type: High heart rate",
        "Severity: Critical",
        ESCALATION_LINE,
        f"Resident: {RESIDENT}",
        WHEN_LINE,
        "Reading: heart rate 140 bpm for 10 s, above the device limit of 130",
        *FOOTER,
    )


def test_no_pulse_escalation_body_keeps_the_no_pulse_line():
    item = vitals_item("hr_low", "critical", NO_PULSE_DETAILS)

    body = email_text.escalation_body(item, RESIDENT, PORTAL)

    assert "Reading: no pulse detected for 10 s (heart rate 0 bpm)" in body
    assert ESCALATION_LINE in body


def test_unassigned_escalation_body_has_resident_unassigned_and_device_line():
    body = email_text.escalation_body(
        vitals_item("spo2_low", "warning"), "unassigned", PORTAL
    )

    assert body == lines(
        "SilverCare alert",
        "",
        "Type: Low blood oxygen",
        "Severity: Warning",
        ESCALATION_LINE,
        "Resident: unassigned",
        f"Device: {DEVICE}",
        WHEN_LINE,
        "Reading: SpO2 89 %, below the limit of 92 (previous reading 90 %)",
        *FOOTER,
    )


def test_assigned_escalation_body_has_no_device_line():
    body = email_text.escalation_body(fall_item(), RESIDENT, PORTAL)

    assert "Device:" not in body
    assert DEVICE not in body


def test_escalation_line_comes_right_after_severity():
    body_lines = email_text.escalation_body(fall_item(), RESIDENT, PORTAL).splitlines()

    i = body_lines.index("Severity: Critical")
    assert body_lines[i + 1] == ESCALATION_LINE


@pytest.mark.parametrize(
    "item",
    [
        fall_item(),
        vitals_item("hr_high", "warning"),
        vitals_item("spo2_low", "critical"),
    ],
    ids=["fall", "warning", "critical"],
)
def test_escalation_body_is_the_caregiver_body_plus_one_line(item):
    """Plus the escalation line; minus the caregiver-only "nurse has also been
    alerted" line of a critical alert (alert-routing criterion 8)."""
    caregiver = email_text.body(item, RESIDENT, PORTAL).splitlines()
    nurse = email_text.escalation_body(item, RESIDENT, PORTAL).splitlines()

    if NURSE_ALERTED_LINE in caregiver:
        j = caregiver.index(NURSE_ALERTED_LINE)
        caregiver = caregiver[: j - 1] + caregiver[j + 1 :]
    i = caregiver.index(next(x for x in caregiver if x.startswith("Severity: ")))
    assert nurse == caregiver[: i + 1] + [ESCALATION_LINE] + caregiver[i + 1 :]


@pytest.mark.parametrize("resident_id", [RESIDENT, "unassigned"])
def test_escalation_body_never_has_the_nurse_alerted_line(resident_id):
    body = email_text.escalation_body(fall_item(), resident_id, PORTAL)

    assert NURSE_ALERTED_LINE not in body


# ---- caregiver email unchanged (criterion 10: escalation only adds to nurse email) ----


@pytest.mark.parametrize("resident_id", [RESIDENT, "unassigned"])
def test_caregiver_body_has_no_escalation_or_device_line(resident_id):
    body = email_text.body(vitals_item("hr_high", "warning"), resident_id, PORTAL)

    assert "Escalated" not in body
    assert "Device:" not in body
    assert DEVICE not in body


def test_caregiver_subjects_are_unchanged():
    assert (
        email_text.subject("fall", RESIDENT, "critical")
        == "SilverCare CRITICAL alert: Fall - res-0003"
    )
    assert (
        email_text.subject("hr_high", RESIDENT, "warning")
        == "SilverCare alert: High heart rate - res-0003"
    )


# ---- hand-edited items (criterion 10) ----


@pytest.mark.parametrize("device_id", ["Test Resident A", "wearable-07", "", 7, None])
def test_unassigned_device_line_is_invalid_for_a_bad_device_id(device_id):
    item = vitals_item("hr_high", "warning")
    if device_id is None:
        del item["device_id"]
    else:
        item["device_id"] = device_id

    body_lines = email_text.escalation_body(item, "unassigned", PORTAL).splitlines()

    assert body_lines[5:7] == ["Resident: unassigned", "Device: invalid"]


@pytest.mark.parametrize("device_id", ["wearable-0007", "bedroom-0001"])
def test_unassigned_device_line_prints_a_valid_device_id(device_id):
    item = {**vitals_item("hr_high", "warning"), "device_id": device_id}

    body = email_text.escalation_body(item, "unassigned", PORTAL)

    assert f"\nDevice: {device_id}\n" in body


@pytest.mark.parametrize("alert_type", [None, "", "hr_weird", "Test Resident A", 7])
def test_missing_or_unknown_alert_type_uses_label_alert(alert_type):
    item = vitals_item("hr_high", "warning")
    if alert_type is None:
        del item["alert_type"]
    else:
        item["alert_type"] = alert_type

    subject = email_text.escalation_subject(item.get("alert_type"), RESIDENT)
    body = email_text.escalation_body(item, RESIDENT, PORTAL)

    assert subject == f"SilverCare ESCALATED alert: Alert - {RESIDENT}"
    assert body.splitlines()[2] == "Type: Alert"
    assert "Test Resident" not in subject + body


@pytest.mark.parametrize("details", ["free text", ["x"], None, 5])
@pytest.mark.parametrize("alert_type", ["fall", "hr_high", None])
@pytest.mark.parametrize("resident_id", [RESIDENT, "unassigned"])
def test_non_dict_details_do_not_crash(details, alert_type, resident_id):
    item = fall_item() if alert_type == "fall" else vitals_item("hr_high", "warning")
    item["details"] = details
    if alert_type is None:
        del item["alert_type"]

    body = email_text.escalation_body(item, resident_id, PORTAL)

    assert ESCALATION_LINE in body
    assert body.endswith("This is an automated message. Do not reply.\n")
