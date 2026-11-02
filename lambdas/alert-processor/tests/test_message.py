"""Alert message validation (alerts-core criteria 1, 3, 15). Pure, no moto."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from events import ALERT_ID, DELETE, DEVICE, RESIDENT, alert_body, alert_message
from message import Alert, Rejection, log_ids, parse_alert


def test_valid_message_parses():
    result = parse_alert(alert_body())

    assert isinstance(result, Alert)
    assert result.alert_id == ALERT_ID
    assert result.alert_type == "fall"
    assert result.resident_id == RESIDENT
    assert result.device_id == DEVICE
    assert result.event_dt.isoformat() == "2026-10-18T03:14:07.360000+00:00"
    assert result.received_dt.isoformat() == "2026-10-18T03:14:07.410000+00:00"


def test_floats_are_parsed_as_exact_decimals():
    result = parse_alert(alert_body())

    assert result.details["confidence"] == Decimal("0.91")
    assert type(result.details["confidence"]) is Decimal
    assert result.details["peak_g"] == Decimal("3.8")
    assert result.details["still_s"] == 20


def test_unassigned_resident_is_valid():
    assert isinstance(parse_alert(alert_body(resident_id="unassigned")), Alert)


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("not json", "invalid:json"),
        (None, "invalid:json"),
        (b"{}", "invalid:json"),
        ('{"confidence": NaN}', "invalid:json"),
        ("[]", "invalid:body"),
        ("42", "invalid:body"),
        (alert_body(alert_id=DELETE), "missing:alert_id"),
        (alert_body(details=DELETE), "missing:details"),
        (alert_body(extra="x"), "unknown_field"),
        (alert_body(schema_version=2), "invalid:schema_version"),
        (alert_body(schema_version="1"), "wrong_type:schema_version"),
        (alert_body(alert_type="vitals_alert"), "invalid:alert_type"),
        (alert_body(alert_type=1), "wrong_type:alert_type"),
        (alert_body(alert_id="not-a-uuid"), "invalid:alert_id"),
        (alert_body(alert_id=ALERT_ID.upper()), "invalid:alert_id"),
        (alert_body(source_message_id="x"), "invalid:source_message_id"),
        (alert_body(resident_id="Test Resident A"), "invalid:resident_id"),
        (alert_body(resident_id=3), "wrong_type:resident_id"),
        (alert_body(device_id="phone-0001"), "invalid:device_id"),
        (alert_body(event_ts="2026-10-18T03:14:07Z"), "invalid:event_ts"),
        (alert_body(event_ts="2026-13-40T03:14:07.360Z"), "invalid:event_ts"),
        (alert_body(received_at=1792293247410), "wrong_type:received_at"),
        (alert_body(details=[]), "wrong_type:details"),
        (
            alert_body(details={"confidence": 0.9, "peak_g": 3.8}),
            "missing:details.still_s",
        ),
        (
            alert_body(
                details={"confidence": 0.9, "peak_g": 3.8, "still_s": 20, "x": 1}
            ),
            "unknown_field:details",
        ),
        (
            alert_body(details={"confidence": "high", "peak_g": 3.8, "still_s": 20}),
            "wrong_type:details.confidence",
        ),
        (
            alert_body(details={"confidence": True, "peak_g": 3.8, "still_s": 20}),
            "wrong_type:details.confidence",
        ),
        (
            alert_body(details={"confidence": 0.9, "peak_g": 3.8, "still_s": 2.5}),
            "wrong_type:details.still_s",
        ),
    ],
)
def test_invalid_body_is_rejected_with_reason(body, reason):
    assert parse_alert(body) == Rejection(reason)


@pytest.mark.parametrize(
    ("name", "value", "ok"),
    [
        ("confidence", 0, True),
        ("confidence", 1, True),
        ("confidence", 1.01, False),
        ("confidence", -0.01, False),
        ("peak_g", 16, True),
        ("peak_g", 16.01, False),
        ("still_s", 0, True),
        ("still_s", 600, True),
        ("still_s", 601, False),
        ("still_s", -1, False),
    ],
)
def test_detail_range_boundaries(name, value, ok):
    details = {"confidence": 0.5, "peak_g": 3.8, "still_s": 20, name: value}

    result = parse_alert(alert_body(details=details))

    if ok:
        assert isinstance(result, Alert)
    else:
        assert result == Rejection(f"out_of_range:details.{name}")


def test_rejection_reason_never_contains_the_value():
    result = parse_alert(alert_body(resident_id="hr_bpm=187"))

    assert "187" not in result.reason


def test_log_ids_keep_valid_ids_and_mask_the_rest():
    body = json.dumps(
        alert_message(resident_id="Test Resident A", device_id="hr 187", alert_type=7)
    )

    assert log_ids(body) == {
        "alert_id": ALERT_ID,
        "alert_type": "invalid",
        "device_id": "invalid",
        "resident_id": "invalid",
    }


def test_log_ids_of_non_json_are_all_absent():
    assert log_ids("not json") == {
        "alert_id": None,
        "alert_type": None,
        "device_id": None,
        "resident_id": None,
    }
