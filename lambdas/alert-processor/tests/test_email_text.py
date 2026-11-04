"""Alert email subject and body (alerts-core criterion 18). Pure, no moto.

Times are Singapore time only (user's decision, 2026-11-02). The two time lines are
kept in WHEN_LINE and VITALS_TAKEN so a format change is a one-line edit here.
"""

from __future__ import annotations

from decimal import Decimal

import email_text
import pytest
from events import DEVICE, EVENT_TS, RESIDENT

PORTAL = "https://d0000example.cloudfront.net"

# Expected time text (criterion 18 example: 03:14:07 UTC is 11:14:07 SGT).
WHEN_LINE = "When: 2026-10-18 11:14:07 SGT"
VITALS_TAKEN = "taken 11:13:00 SGT"

VITALS = {
    "hr_bpm": Decimal(72),
    "spo2_pct": Decimal(97),
    "ts": "2026-10-18T03:13:00.000Z",
}


def item(**details_extra) -> dict:
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
            **details_extra,
        },
    }


def expected_body(vitals_line: str) -> str:
    return "\n".join(
        [
            "SilverCare alert",
            "",
            "Type: Fall",
            "Severity: Critical",
            f"Resident: {RESIDENT}",
            WHEN_LINE,
            "Fall details: confidence 0.91, peak 3.8 g, still for 20 s",
            vitals_line,
            "",
            # A fall is critical: the caregiver email says the nurses were alerted
            # too (alert-routing criterion 8).
            "The care centre nurse has also been alerted.",
            "",
            f"Open the SilverCare portal: {PORTAL}/",
            "",
            "This is an automated message. Do not reply.",
        ]
    )


def test_fall_subject_is_critical():
    assert (
        email_text.subject("fall", RESIDENT, "critical")
        == "SilverCare CRITICAL alert: Fall - res-0003"
    )


@pytest.mark.parametrize("severity", ["warning", "critical"])
def test_subject_is_at_most_100_characters(severity):
    long_type = "x" * 200

    assert len(email_text.subject(long_type, RESIDENT, severity)) <= 100
    assert len(email_text.subject("fall", "unassigned", severity)) <= 100


def test_body_with_latest_vitals_is_exact():
    body = email_text.body(item(latest_vitals=VITALS), RESIDENT, PORTAL)

    assert body.rstrip("\n") == expected_body(
        f"Latest vitals ({VITALS_TAKEN}): heart rate 72 bpm, SpO2 97 %"
    )


def test_null_vitals_print_no_reading():
    vitals = {**VITALS, "hr_bpm": None, "spo2_pct": None}

    body = email_text.body(item(latest_vitals=vitals), RESIDENT, PORTAL)

    assert body.rstrip("\n") == expected_body(
        f"Latest vitals ({VITALS_TAKEN}): heart rate no reading, SpO2 no reading"
    )


def test_one_null_vital_prints_no_reading_for_that_one_only():
    vitals = {**VITALS, "spo2_pct": None}

    body = email_text.body(item(latest_vitals=vitals), RESIDENT, PORTAL)

    assert "heart rate 72 bpm, SpO2 no reading" in body


def test_absent_vitals_print_none_received():
    body = email_text.body(item(), RESIDENT, PORTAL)

    assert body.rstrip("\n") == expected_body("Latest vitals: none received")


def test_times_are_singapore_time():
    body = email_text.body(item(latest_vitals=VITALS), RESIDENT, PORTAL)

    assert WHEN_LINE in body.splitlines()
    assert "11:13:00 SGT" in body
    assert "UTC" not in body


def test_portal_url_trailing_slash_is_not_doubled():
    body = email_text.body(item(), RESIDENT, PORTAL + "/")

    assert f"Open the SilverCare portal: {PORTAL}/\n" in body
    assert f"{PORTAL}//" not in body


def test_body_has_no_device_id_name_or_html():
    body = email_text.body(item(latest_vitals=VITALS), RESIDENT, PORTAL)

    assert DEVICE not in body
    assert "Test Resident" not in body
    assert "<" not in body
    assert "@" not in body


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (Decimal("0.91"), "0.91"),
        (Decimal(20), "20"),
        (Decimal("3.80"), "3.8"),
        (Decimal("1E+1"), "10"),
        (20, "20"),
    ],
)
def test_numbers_print_without_exponent_or_trailing_zeros(value, text):
    assert email_text.format_number(value) == text
