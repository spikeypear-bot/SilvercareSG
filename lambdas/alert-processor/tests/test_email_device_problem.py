"""Device-problem email text (docs/features/watchdog.md, criteria 14 and 14a): exact
subjects and bodies, the acknowledge follow-up, and hand-edited items. Pure."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import email_text
import pytest
from events import CAREGIVER_MASKED, CAREGIVER_NAME, DEVICE, DP_EVENT_TS, RESIDENT

PORTAL = "https://d0000example.cloudfront.net"
CHANGED = datetime(2026, 11, 4, 2, 5, tzinfo=UTC)
FOOTER = [
    "",
    "Open the SilverCare portal: https://d0000example.cloudfront.net/",
    "",
    "This is an automated message. Do not reply.",
]
DETAIL_LINES = {
    "device_offline": ("Device offline", "Last heartbeat: 2026-11-04 09:40:00 SGT"),
    "battery_low": ("Low battery", "Battery: 15 %"),
    "not_worn": ("Wearable not worn", "Not worn since: 2026-11-04 09:40:00 SGT"),
}
DETAILS = {
    "device_offline": {"last_seen": DP_EVENT_TS},
    "battery_low": {"battery_pct": 15},
    "not_worn": {"since": DP_EVENT_TS},
}


def item(alert_type: str, **overrides) -> dict:
    base = {
        "alert_type": alert_type,
        "device_id": DEVICE,
        "severity": "warning",
        "details": dict(DETAILS[alert_type]),
    }
    base.update(overrides)
    return base


def lines(alert_type: str) -> list[str]:
    label, detail = DETAIL_LINES[alert_type]
    return [f"Type: {label}", f"Device: {DEVICE}", f"Resident: {RESIDENT}", detail]


@pytest.mark.parametrize("alert_type", list(DETAIL_LINES))
def test_device_problem_subject_names_label_device_and_resident(alert_type):
    label = DETAIL_LINES[alert_type][0]

    subject = email_text.device_problem_subject(alert_type, DEVICE, RESIDENT)

    assert subject == f"SilverCare device problem: {label} - {DEVICE} ({RESIDENT})"
    assert len(subject) <= 100


@pytest.mark.parametrize("alert_type", list(DETAIL_LINES))
def test_device_problem_body_is_exact(alert_type):
    body = email_text.device_problem_body(item(alert_type), RESIDENT, PORTAL)

    expected = ["SilverCare device problem", "", *lines(alert_type), *FOOTER]
    assert body == "\n".join(expected) + "\n"


def test_battery_read_back_as_decimal_is_shown_as_an_integer():
    body = email_text.device_problem_body(
        item("battery_low", details={"battery_pct": Decimal(15)}), RESIDENT, PORTAL
    )

    assert "Battery: 15 %\n" in body


def test_grouped_flag_does_not_change_the_body():
    grouped = item(
        "device_offline", details={"last_seen": DP_EVENT_TS, "grouped": True}
    )

    assert email_text.device_problem_body(
        grouped, RESIDENT, PORTAL
    ) == email_text.device_problem_body(item("device_offline"), RESIDENT, PORTAL)


@pytest.mark.parametrize(
    ("alert_type", "details", "line"),
    [
        ("battery_low", {"battery_pct": "fifteen"}, "Battery: unknown %"),
        ("battery_low", {}, "Battery: unknown %"),
        ("device_offline", {"last_seen": "yesterday"}, "Last heartbeat: unknown time"),
        ("not_worn", {}, "Not worn since: unknown time"),
    ],
)
def test_hand_edited_details_show_unknown(alert_type, details, line):
    body = email_text.device_problem_body(
        item(alert_type, details=details), RESIDENT, PORTAL
    )

    assert f"\n{line}\n" in body
    assert "fifteen" not in body
    assert "yesterday" not in body


def test_hand_edited_device_id_shows_invalid():
    body = email_text.device_problem_body(
        item("battery_low", device_id="Test Resident A"), RESIDENT, PORTAL
    )

    assert "Device: invalid\n" in body
    assert "Test Resident A" not in body


def test_unknown_type_is_labelled_alert():
    assert (
        email_text.device_problem_subject("fall", DEVICE, RESIDENT)
        == f"SilverCare device problem: Alert - {DEVICE} ({RESIDENT})"
    )


def test_body_has_no_health_reading_lines():
    body = email_text.device_problem_body(item("battery_low"), RESIDENT, PORTAL)

    for word in ("Heart", "SpO2", "Severity", "bpm"):
        assert word not in body


# ---- acknowledge follow-up (criterion 14a) ----


@pytest.mark.parametrize("alert_type", list(DETAIL_LINES))
def test_follow_up_subject_ends_with_acknowledged(alert_type):
    label = DETAIL_LINES[alert_type][0]

    subject = email_text.device_problem_follow_up_subject(alert_type, DEVICE, RESIDENT)

    assert subject == (
        f"SilverCare update: {label} - {DEVICE} ({RESIDENT}) acknowledged"
    )
    assert len(subject) <= 100


def follow_up(alert_type="battery_low", by_role="ops", by_name=None) -> str:
    return email_text.device_problem_follow_up_body(
        item(alert_type),
        RESIDENT,
        PORTAL,
        changed_at=CHANGED,
        by_role=by_role,
        by_name=by_name,
    )


@pytest.mark.parametrize("alert_type", list(DETAIL_LINES))
def test_ops_follow_up_body_is_exact(alert_type):
    expected = [
        "SilverCare update",
        "",
        (
            "The device problem below was acknowledged by the ops team at"
            " 2026-11-04 10:05:00 SGT."
        ),
        "",
        *lines(alert_type),
        *FOOTER,
    ]

    assert follow_up(alert_type) == "\n".join(expected) + "\n"


def test_caregiver_follow_up_names_the_caregiver_masked():
    body = follow_up(by_role="caregiver", by_name=CAREGIVER_NAME)

    assert body.splitlines()[2] == (
        f"The device problem below was acknowledged by {CAREGIVER_MASKED}"
        " (caregiver) at 2026-11-04 10:05:00 SGT."
    )
    assert CAREGIVER_NAME not in body


def test_ops_follow_up_never_names_a_person():
    body = follow_up(by_role="ops", by_name=CAREGIVER_NAME)

    assert "the ops team" in body
    assert CAREGIVER_MASKED not in body
