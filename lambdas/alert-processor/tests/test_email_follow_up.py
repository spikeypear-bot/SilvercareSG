"""Follow-up email subjects, bodies, and name masking (docs/features/alert-cancel.md,
criteria 13, 14 and 19). Pure, no moto."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import email_text
import pytest

PORTAL = "https://d0000example.cloudfront.net"
CHANGED = datetime(2026, 11, 3, 2, 0, 5, tzinfo=UTC)
EVENT_TS = "2026-11-03T02:00:00.000Z"
TYPES = ("fall", "hr_high", "hr_low", "spo2_low", "skin_temp_high", "skin_temp_low")


def item(**overrides) -> dict:
    base = {
        "pk": "RESIDENT#res-0001",
        "sk": f"ALERT#{EVENT_TS}#x",
        "alert_type": "fall",
        "severity": "critical",
        "device_id": "wearable-0001",
        "event_ts": EVENT_TS,
        "details": {"confidence": Decimal("0.91"), "peak_g": Decimal("3.8")},
    }
    for name, value in overrides.items():
        if value is None:
            base.pop(name, None)
        else:
            base[name] = value
    return base


def body(it=None, resident_id="res-0001", **kw) -> str:
    args = {
        "new_status": "cancelled",
        "changed_at": CHANGED,
        "by_role": "resident",
        "by_name": None,
    }
    args.update(kw)
    return email_text.follow_up_body(it or item(), resident_id, PORTAL, **args)


def expected(first_line: str, *middle: str) -> str:
    return "\n".join(
        [
            "SilverCare update",
            "",
            first_line,
            "",
            *middle,
            "",
            "Open the SilverCare portal: https://d0000example.cloudfront.net/",
            "",
            "This is an automated message. Do not reply.",
            "",
        ]
    )


# ---- subjects (criterion 13) ----


def test_cancel_subject_is_exact():
    assert (
        email_text.follow_up_subject("fall", "res-0001", "cancelled")
        == "SilverCare update: Fall - res-0001 cancelled"
    )


def test_acknowledge_subject_is_exact_and_has_no_name():
    assert (
        email_text.follow_up_subject("hr_high", "res-0001", "acknowledged")
        == "SilverCare update: High heart rate - res-0001 acknowledged"
    )


@pytest.mark.parametrize("alert_type", [None, "", "unknown_type", 5])
def test_subject_for_unknown_type_uses_alert_label(alert_type):
    assert (
        email_text.follow_up_subject(alert_type, "res-0001", "cancelled")
        == "SilverCare update: Alert - res-0001 cancelled"
    )


@pytest.mark.parametrize("alert_type", [*TYPES, None])
@pytest.mark.parametrize("resident_id", ["res-0001", "unassigned"])
@pytest.mark.parametrize("new_status", ["cancelled", "acknowledged"])
def test_every_subject_is_at_most_100_characters(alert_type, resident_id, new_status):
    subject = email_text.follow_up_subject(alert_type, resident_id, new_status)

    assert len(subject) <= 100


# ---- bodies (criterion 14) ----


def test_cancel_body_is_exact():
    assert body() == expected(
        "The alert below was cancelled by the resident at 2026-11-03 10:00:05 SGT.",
        "Type: Fall",
        "Severity: Critical",
        "Resident: res-0001",
        "When: 2026-11-03 10:00:00 SGT",
    )


def test_staff_acknowledge_body_has_masked_name_and_staff_role():
    text = body(
        it=item(alert_type="hr_high", severity="warning"),
        new_status="acknowledged",
        by_role="staff",
        by_name="Test Nurse Alpha",
    )

    assert text == expected(
        "The alert below was acknowledged by Te** Nu*** Al*** (staff) at"
        " 2026-11-03 10:00:05 SGT.",
        "Type: High heart rate",
        "Severity: Warning",
        "Resident: res-0001",
        "When: 2026-11-03 10:00:00 SGT",
    )


def test_caregiver_acknowledge_body_has_masked_name_and_caregiver_role():
    text = body(
        new_status="acknowledged", by_role="caregiver", by_name="Test Caregiver Beta"
    )

    assert text == expected(
        "The alert below was acknowledged by Te** Ca******* Be** (caregiver) at"
        " 2026-11-03 10:00:05 SGT.",
        "Type: Fall",
        "Severity: Critical",
        "Resident: res-0001",
        "When: 2026-11-03 10:00:00 SGT",
    )


def test_acknowledge_body_never_has_the_full_name():
    text = body(new_status="acknowledged", by_role="staff", by_name="Test Nurse Alpha")

    assert "Test Nurse Alpha" not in text
    assert "Nurse" not in text
    assert "Alpha" not in text


def test_unassigned_body_adds_device_line_after_resident():
    text = body(resident_id="unassigned")

    assert text == expected(
        "The alert below was cancelled by the resident at 2026-11-03 10:00:05 SGT.",
        "Type: Fall",
        "Severity: Critical",
        "Resident: unassigned",
        "Device: wearable-0001",
        "When: 2026-11-03 10:00:00 SGT",
    )


@pytest.mark.parametrize("device_id", ["Test Resident A", "wearable-1", None, 7])
def test_unassigned_body_with_bad_device_id_says_device_invalid(device_id):
    text = body(it=item(device_id=device_id), resident_id="unassigned")

    assert "\nDevice: invalid\n" in text
    if isinstance(device_id, str):
        assert device_id not in text


def test_assigned_body_has_no_device_line():
    assert "Device:" not in body()


@pytest.mark.parametrize("alert_type", [None, "unknown_type"])
def test_unknown_type_body_uses_alert_label(alert_type):
    assert "\nType: Alert\n" in body(it=item(alert_type=alert_type))


@pytest.mark.parametrize("severity", ["urgent", "CRITICAL", 3])
def test_unknown_severity_body_says_unknown(severity):
    assert "\nSeverity: Unknown\n" in body(it=item(severity=severity))


def test_missing_severity_body_defaults_to_critical():
    assert "\nSeverity: Critical\n" in body(it=item(severity=None))


def test_body_has_no_details_or_reading_lines():
    text = body()

    for hidden in ("0.91", "3.8", "details", "Reading", "Fall details", "Vitals"):
        assert hidden not in text, hidden


@pytest.mark.parametrize("alert_type", TYPES)
def test_body_type_label_matches_the_alert_email(alert_type):
    severity = "warning" if alert_type.startswith("skin") else "critical"
    text = body(it=item(alert_type=alert_type, severity=severity))

    assert f"\nType: {email_text.TYPE_LABELS[alert_type]}\n" in text


def test_portal_url_with_trailing_slash_has_one_slash():
    text = email_text.follow_up_body(
        item(),
        "res-0001",
        PORTAL + "/",
        new_status="cancelled",
        changed_at=CHANGED,
        by_role="resident",
        by_name=None,
    )

    assert "Open the SilverCare portal: https://d0000example.cloudfront.net/\n" in text


# ---- mask_name (decisions table, criterion 14) ----


@pytest.mark.parametrize(
    ("name", "masked"),
    [
        ("Tan", "Ta*"),
        ("Alpha", "Al***"),
        ("Tan Mei Ling", "Ta* Me* Li**"),
        ("Test Nurse Alpha", "Te** Nu*** Al***"),
        ("Li", "Li"),
        ("A", "A"),
        ("A Li Tan", "A Li Ta*"),
        ("Bo Al", "Bo Al"),
        ("  Test   Nurse  ", "Te** Nu***"),
        (" Tan", "Ta*"),
        ("Tan ", "Ta*"),
        ("陈美玲", "陈美*"),
        ("陈 美玲", "陈 美玲"),
        ("Zoë Åström", "Zo* Ås****"),
        ("Ñúñez", "Ñú***"),
    ],
)
def test_mask_name(name, masked):
    assert email_text.mask_name(name) == masked


def test_mask_name_keeps_word_lengths():
    name = "Test Caregiver Beta"

    masked = email_text.mask_name(name)

    assert [len(w) for w in masked.split(" ")] == [len(w) for w in name.split(" ")]
