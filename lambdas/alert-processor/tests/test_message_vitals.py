"""Validation of vitals alert messages and `severity` (docs/features/vitals-alerts.md,
criteria 1-4 and the 2026-11-03 decisions). Pure, no moto."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from events import (
    DELETE,
    NO_PULSE_DETAILS,
    VITALS_DETAILS,
    alert_body,
    alert_message,
    vitals_body,
    vitals_message,
)
from message import Alert, Rejection, log_ids, parse_alert


def body_with_details(alert_type: str, severity: str, **changes) -> str:
    details = dict(VITALS_DETAILS[(alert_type, severity)])
    for name, value in changes.items():
        if value is DELETE:
            details.pop(name, None)
        else:
            details[name] = value
    return vitals_body(alert_type, severity, details=details)


# ---- valid ----


@pytest.mark.parametrize(("alert_type", "severity"), list(VITALS_DETAILS))
def test_every_vitals_type_and_severity_parses(alert_type, severity):
    result = parse_alert(vitals_body(alert_type, severity))

    assert isinstance(result, Alert), result
    assert result.alert_type == alert_type
    assert result.severity == severity


def test_no_pulse_critical_hr_low_parses():
    result = parse_alert(vitals_body("hr_low", "critical", details=NO_PULSE_DETAILS))

    assert isinstance(result, Alert), result
    assert result.details["value"] == 0


def test_skin_temperature_parses_as_exact_decimal():
    result = parse_alert(vitals_body("skin_temp_high", "warning"))

    assert result.details["value"] == Decimal("38.4")
    assert type(result.details["value"]) is Decimal


@pytest.mark.parametrize(
    ("alert_type", "severity", "changes"),
    [
        ("hr_high", "warning", {"value": 110}),  # value not past limit
        ("hr_high", "critical", {"limit": 45}),  # limit doesn't match direction
        ("hr_low", "critical", {"value": 60, "limit": 130}),
        ("spo2_low", "warning", {"value": 99}),
    ],
)
def test_value_and_limit_are_not_checked_against_each_other(
    alert_type, severity, changes
):
    assert isinstance(
        parse_alert(body_with_details(alert_type, severity, **changes)), Alert
    )


# ---- severity (criterion 2, 4) ----


def test_missing_severity_is_rejected():
    assert parse_alert(alert_body(severity=DELETE)) == Rejection("missing:severity")


@pytest.mark.parametrize("value", [1, None, True, ["critical"]])
def test_non_string_severity_is_rejected(value):
    assert parse_alert(alert_body(severity=value)) == Rejection("wrong_type:severity")


@pytest.mark.parametrize("value", ["info", "Critical", "WARNING", ""])
def test_unknown_severity_is_rejected(value):
    assert parse_alert(alert_body(severity=value)) == Rejection("invalid:severity")


def test_fall_must_be_critical():
    assert parse_alert(alert_body(severity="warning")) == Rejection("invalid:severity")


@pytest.mark.parametrize("alert_type", ["skin_temp_high", "skin_temp_low"])
def test_skin_temperature_alerts_are_warning_only(alert_type):
    details = dict(VITALS_DETAILS[(alert_type, "warning")])
    details.pop("previous_value")
    details["held_s"] = 10

    body = alert_message(alert_type=alert_type, severity="critical", details=details)

    result = parse_alert(json.dumps(body))

    assert result == Rejection("invalid:severity")


@pytest.mark.parametrize(
    "alert_type", ["vitals_alert", "hr", "HR_HIGH", "spo2_high", "no_pulse"]
)
def test_unknown_alert_type_is_rejected(alert_type):
    result = parse_alert(alert_body(alert_type=alert_type))

    assert result == Rejection("invalid:alert_type")


# ---- details shape per severity (criterion 3) ----


@pytest.mark.parametrize(
    ("severity", "missing"),
    [
        ("warning", "previous_value"),
        ("warning", "metric"),
        ("warning", "value"),
        ("warning", "limit"),
        ("warning", "direction"),
        ("critical", "held_s"),
        ("critical", "metric"),
    ],
)
def test_missing_detail_is_rejected(severity, missing):
    result = parse_alert(body_with_details("hr_high", severity, **{missing: DELETE}))

    assert result == Rejection(f"missing:details.{missing}")


def test_warning_with_held_s_is_rejected():
    result = parse_alert(body_with_details("hr_high", "warning", held_s=10))

    assert result == Rejection("unknown_field:details")


def test_critical_with_previous_value_is_rejected():
    result = parse_alert(body_with_details("hr_high", "critical", previous_value=125))

    assert result == Rejection("unknown_field:details")


def test_fall_details_on_a_vitals_type_are_rejected():
    result = parse_alert(
        vitals_body(details={"confidence": 0.9, "peak_g": 3.8, "still_s": 20})
    )

    assert result == Rejection("unknown_field:details")


@pytest.mark.parametrize(
    ("alert_type", "field", "value", "reason"),
    [
        ("hr_high", "metric", "spo2_pct", "invalid:details.metric"),
        ("hr_high", "direction", "below", "invalid:details.direction"),
        ("spo2_low", "direction", "above", "invalid:details.direction"),
        ("skin_temp_low", "metric", "hr_bpm", "invalid:details.metric"),
        ("hr_high", "metric", 1, "wrong_type:details.metric"),
        ("hr_high", "direction", None, "wrong_type:details.direction"),
    ],
)
def test_metric_and_direction_must_match_the_alert_type(
    alert_type, field, value, reason
):
    result = parse_alert(body_with_details(alert_type, "warning", **{field: value}))

    assert result == Rejection(reason)


# ---- ranges (criterion 4) ----


@pytest.mark.parametrize(
    ("alert_type", "severity", "field", "value", "ok"),
    [
        ("hr_high", "warning", "value", 250, True),
        ("hr_high", "warning", "value", 251, False),
        ("hr_low", "warning", "value", 20, True),
        ("hr_low", "warning", "value", 19, False),
        ("hr_low", "warning", "previous_value", 19, False),
        ("hr_high", "warning", "limit", 251, False),
        ("spo2_low", "warning", "value", 50, True),
        ("spo2_low", "warning", "value", 49, False),
        ("spo2_low", "warning", "previous_value", 101, False),
        ("spo2_low", "critical", "limit", 49, False),
        ("skin_temp_high", "warning", "value", 45.0, True),
        ("skin_temp_high", "warning", "value", 45.1, False),
        ("skin_temp_low", "warning", "value", 20.0, True),
        ("skin_temp_low", "warning", "value", 19.9, False),
        ("hr_high", "critical", "held_s", 10, True),
        ("hr_high", "critical", "held_s", 600, True),
        ("hr_high", "critical", "held_s", 9, False),
        ("hr_high", "critical", "held_s", 601, False),
    ],
)
def test_detail_range_boundaries(alert_type, severity, field, value, ok):
    result = parse_alert(body_with_details(alert_type, severity, **{field: value}))

    if ok:
        assert isinstance(result, Alert), result
    else:
        assert result == Rejection(f"out_of_range:details.{field}")


@pytest.mark.parametrize(
    ("alert_type", "severity", "field", "value"),
    [
        ("hr_high", "warning", "value", 128.5),
        ("hr_high", "warning", "value", True),
        ("hr_high", "warning", "value", "128"),
        ("hr_high", "warning", "value", None),
        ("spo2_low", "warning", "limit", 92.0),
        ("hr_high", "critical", "held_s", 10.5),
    ],
)
def test_integer_details_reject_other_types(alert_type, severity, field, value):
    result = parse_alert(body_with_details(alert_type, severity, **{field: value}))

    assert result == Rejection(f"wrong_type:details.{field}")


# ---- no pulse (criterion 4) ----


def test_value_zero_is_rejected_for_a_warning_hr_low():
    result = parse_alert(body_with_details("hr_low", "warning", value=0))

    assert result == Rejection("out_of_range:details.value")


def test_value_zero_is_rejected_for_critical_hr_high():
    result = parse_alert(body_with_details("hr_high", "critical", value=0))

    assert result == Rejection("out_of_range:details.value")


@pytest.mark.parametrize("value", [1, 19])
def test_values_1_to_19_are_rejected_even_for_critical_hr_low(value):
    result = parse_alert(body_with_details("hr_low", "critical", value=value))

    assert result == Rejection("out_of_range:details.value")


def test_no_pulse_with_out_of_range_held_s_is_rejected():
    details = {**NO_PULSE_DETAILS, "held_s": 5}

    result = parse_alert(vitals_body("hr_low", "critical", details=details))

    assert result == Rejection("out_of_range:details.held_s")


def test_value_zero_float_is_not_no_pulse():
    details = {**NO_PULSE_DETAILS, "value": 0.0}

    result = parse_alert(vitals_body("hr_low", "critical", details=details))

    assert isinstance(result, Rejection)


# ---- log ids ----


def test_log_ids_include_valid_severity():
    assert log_ids(vitals_body("hr_high", "warning"))["severity"] == "warning"


def test_log_ids_mask_invalid_severity():
    body = json.dumps(vitals_message() | {"severity": "hr 187"})

    assert log_ids(body)["severity"] == "invalid"


def test_rejection_reason_never_contains_the_value():
    result = parse_alert(body_with_details("hr_high", "warning", value=987))

    assert "987" not in result.reason
