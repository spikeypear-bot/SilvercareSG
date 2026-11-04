"""Email subject and body per severity and alert type (docs/features/vitals-alerts.md,
criteria 21-22, 29). Pure, no moto."""

from __future__ import annotations

from decimal import Decimal

import email_text
import pytest
from events import DEVICE, EVENT_TS, NO_PULSE_DETAILS, RESIDENT, VITALS_DETAILS

PORTAL = "https://d0000example.cloudfront.net"
WHEN_LINE = "When: 2026-10-18 11:14:07 SGT"


def as_stored(details: dict) -> dict:
    """Details as read back from DynamoDB (numbers are Decimal)."""
    return {
        k: Decimal(str(v))
        if isinstance(v, int | float) and not isinstance(v, bool)
        else v
        for k, v in details.items()
    }


def item(alert_type: str, severity: str, details: dict | None = None) -> dict:
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


def expected(type_label: str, severity_label: str, reading: str) -> str:
    return (
        "\n".join(
            [
                "SilverCare alert",
                "",
                f"Type: {type_label}",
                f"Severity: {severity_label}",
                f"Resident: {RESIDENT}",
                WHEN_LINE,
                reading,
                "",
                f"Open the SilverCare portal: {PORTAL}/",
                "",
                "This is an automated message. Do not reply.",
            ]
        )
        + "\n"
    )


# ---- subject (criterion 21) ----


@pytest.mark.parametrize(
    ("alert_type", "severity", "subject"),
    [
        ("hr_high", "warning", "SilverCare alert: High heart rate - res-0003"),
        ("hr_low", "warning", "SilverCare alert: Low heart rate - res-0003"),
        ("spo2_low", "warning", "SilverCare alert: Low blood oxygen - res-0003"),
        (
            "skin_temp_high",
            "warning",
            "SilverCare alert: High skin temperature - res-0003",
        ),
        (
            "skin_temp_low",
            "warning",
            "SilverCare alert: Low skin temperature - res-0003",
        ),
        (
            "hr_high",
            "critical",
            "SilverCare CRITICAL alert: High heart rate - res-0003",
        ),
        ("hr_low", "critical", "SilverCare CRITICAL alert: Low heart rate - res-0003"),
        (
            "spo2_low",
            "critical",
            "SilverCare CRITICAL alert: Low blood oxygen - res-0003",
        ),
        ("fall", "critical", "SilverCare CRITICAL alert: Fall - res-0003"),
    ],
)
def test_subject_per_type_and_severity(alert_type, severity, subject):
    assert email_text.subject(alert_type, RESIDENT, severity) == subject


@pytest.mark.parametrize("alert_type", list(email_text.TYPE_LABELS))
@pytest.mark.parametrize("severity", ["warning", "critical"])
def test_every_subject_fits_in_100_characters(alert_type, severity):
    assert len(email_text.subject(alert_type, "unassigned", severity)) <= 100


# ---- body: warning (criterion 22) ----


def test_warning_body_is_exact():
    body = email_text.body(item("hr_high", "warning"), RESIDENT, PORTAL)

    assert body == expected(
        "High heart rate",
        "Warning",
        "Reading: heart rate 128 bpm, above the limit of 120 "
        "(previous reading 125 bpm)",
    )


@pytest.mark.parametrize(
    ("alert_type", "label", "reading"),
    [
        (
            "hr_low",
            "Low heart rate",
            (
                "Reading: heart rate 44 bpm, below the limit of 50 "
                "(previous reading 46 bpm)"
            ),
        ),
        (
            "spo2_low",
            "Low blood oxygen",
            "Reading: SpO2 89 %, below the limit of 92 (previous reading 90 %)",
        ),
        (
            "skin_temp_high",
            "High skin temperature",
            (
                "Reading: skin temperature 38.4 °C, above the limit of 38.0 "
                "(previous reading 38.2 °C)"
            ),
        ),
        (
            "skin_temp_low",
            "Low skin temperature",
            (
                "Reading: skin temperature 29.5 °C, below the limit of 30.0 "
                "(previous reading 29.8 °C)"
            ),
        ),
    ],
)
def test_warning_reading_line_per_type(alert_type, label, reading):
    body = email_text.body(item(alert_type, "warning"), RESIDENT, PORTAL)

    assert body == expected(label, "Warning", reading)


def test_skin_temperature_prints_one_decimal():
    details = {
        "metric": "skin_temp_c",
        "value": 39,
        "limit": 38,
        "direction": "above",
        "previous_value": 38.24,
    }

    body = email_text.body(item("skin_temp_high", "warning", details), RESIDENT, PORTAL)

    assert (
        "Reading: skin temperature 39.0 °C, above the limit of 38.0 "
        "(previous reading 38.2 °C)"
    ) in body


# ---- body: critical (criterion 22) ----


def test_critical_body_is_exact():
    body = email_text.body(item("hr_high", "critical"), RESIDENT, PORTAL)

    assert body == expected(
        "High heart rate",
        "Critical",
        "Reading: heart rate 140 bpm for 10 s, above the device limit of 130",
    )


@pytest.mark.parametrize(
    ("alert_type", "label", "reading"),
    [
        (
            "hr_low",
            "Low heart rate",
            "Reading: heart rate 40 bpm for 10 s, below the device limit of 45",
        ),
        (
            "spo2_low",
            "Low blood oxygen",
            "Reading: SpO2 85 % for 12 s, below the device limit of 88",
        ),
    ],
)
def test_critical_reading_line_per_type(alert_type, label, reading):
    body = email_text.body(item(alert_type, "critical"), RESIDENT, PORTAL)

    assert body == expected(label, "Critical", reading)


def test_no_pulse_body_is_exact():
    body = email_text.body(
        item("hr_low", "critical", NO_PULSE_DETAILS), RESIDENT, PORTAL
    )

    assert body == expected(
        "Low heart rate",
        "Critical",
        "Reading: no pulse detected for 10 s (heart rate 0 bpm)",
    )


def test_no_pulse_line_uses_held_s():
    details = {**NO_PULSE_DETAILS, "held_s": 25}

    body = email_text.body(item("hr_low", "critical", details), RESIDENT, PORTAL)

    assert "Reading: no pulse detected for 25 s (heart rate 0 bpm)" in body


# ---- fall and shared lines ----


def test_fall_body_keeps_fall_lines_and_is_critical():
    fall = {
        "pk": f"RESIDENT#{RESIDENT}",
        "sk": f"ALERT#{EVENT_TS}#x",
        "alert_type": "fall",
        "severity": "critical",
        "event_ts": EVENT_TS,
        "details": {
            "confidence": Decimal("0.91"),
            "peak_g": Decimal("3.8"),
            "still_s": 20,
        },
    }

    lines = email_text.body(fall, RESIDENT, PORTAL).splitlines()

    assert lines[2:4] == ["Type: Fall", "Severity: Critical"]
    assert "Fall details: confidence 0.91, peak 3.8 g, still for 20 s" in lines
    assert "Latest vitals: none received" in lines
    assert not any(line.startswith("Reading:") for line in lines)


def test_item_stored_before_severity_existed_reads_as_critical():
    old = item("hr_high", "critical")
    del old["severity"]

    assert "Severity: Critical" in email_text.body(old, RESIDENT, PORTAL)


def test_vitals_body_has_no_fall_lines_device_id_or_html():
    body = email_text.body(item("hr_high", "warning"), RESIDENT, PORTAL)

    assert "Fall details" not in body
    assert "Latest vitals" not in body
    assert DEVICE not in body
    assert "<" not in body
    assert "@" not in body
    assert "UTC" not in body


def test_severity_line_comes_right_after_type():
    lines = email_text.body(item("spo2_low", "warning"), RESIDENT, PORTAL).splitlines()

    assert lines.index("Severity: Warning") == lines.index("Type: Low blood oxygen") + 1
