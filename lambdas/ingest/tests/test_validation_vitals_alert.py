"""validation of the device `vitals_alert` (docs/features/vitals-alerts.md, criteria 13,
16, 29 and the 2026-11-03 device validation decision). Pure, no moto.

Types and ranges only: a `value` that isn't past its `limit`, or a `limit` that doesn't
match the `direction`, is still accepted (safety over tidiness).
"""

from __future__ import annotations

import pytest
from events import BEDROOM, DELETE, WEARABLE, rule_event
from validation import Rejection, ValidMessage, validate_event

CRITICAL = frozenset({"fall", "vitals_alert"})
FIELDS = ("metric", "value", "limit", "direction", "held_s")


def reason(event, handled=CRITICAL) -> str | None:
    result = validate_event(event, handled)
    return result.reason if isinstance(result, Rejection) else None


def hr(**overrides) -> dict:
    return rule_event("vitals_alert", **overrides)


def spo2(**overrides) -> dict:
    base = {"metric": "spo2_pct", "value": 85, "limit": 88, "direction": "below"}
    return rule_event("vitals_alert", **{**base, **overrides})


def no_pulse(**overrides) -> dict:
    base = {"metric": "hr_bpm", "value": 0, "limit": 45, "direction": "below"}
    return rule_event("vitals_alert", **{**base, **overrides})


# ---- valid ----


@pytest.mark.parametrize(
    "event",
    [
        hr(),
        hr(value=40, limit=45, direction="below"),
        spo2(),
        no_pulse(),
    ],
    ids=["hr_high", "hr_low", "spo2_low", "no_pulse"],
)
def test_contract_examples_are_valid(event):
    result = validate_event(event, CRITICAL)

    assert isinstance(result, ValidMessage), result
    assert result.message == "vitals_alert"


def test_stored_fields_are_exactly_the_contract_fields():
    result = validate_event(hr(extra="ignored"), CRITICAL)

    assert result.fields == {
        "metric": "hr_bpm",
        "value": 140,
        "limit": 130,
        "direction": "above",
        "held_s": 10,
    }


# ---- accepted on purpose (decisions table, 2026-11-03) ----


@pytest.mark.parametrize(
    "event",
    [
        hr(value=125, limit=130, direction="above"),  # value not past limit
        hr(value=50, limit=45, direction="below"),  # value not past limit
        spo2(value=95, limit=88),  # value not past limit
        hr(value=140, limit=45, direction="above"),  # limit doesn't match direction
        hr(value=40, limit=130, direction="below"),  # limit doesn't match direction
        no_pulse(limit=130),  # no pulse with the high limit
    ],
)
def test_untidy_but_typed_alerts_are_accepted(event):
    assert isinstance(validate_event(event, CRITICAL), ValidMessage)


# ---- each field: missing ----


@pytest.mark.parametrize("field", FIELDS)
def test_missing_field_is_rejected(field):
    assert reason(hr(**{field: DELETE})) == f"missing:{field}"


# ---- each field: wrong type ----


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("metric", 1),
        ("metric", None),
        ("value", "140"),
        ("value", 140.5),
        ("value", 140.0),
        ("value", True),
        ("value", None),
        ("limit", "130"),
        ("limit", 130.0),
        ("limit", False),
        ("direction", None),
        ("direction", ["above"]),
        ("held_s", 10.5),
        ("held_s", True),
        ("held_s", None),
    ],
)
def test_wrong_type_is_rejected(field, value):
    assert reason(hr(**{field: value})) == f"wrong_type:{field}"


# ---- metric ----


@pytest.mark.parametrize(
    "metric", ["skin_temp_c", "activity_pct", "HR_BPM", "temperature"]
)
def test_unknown_metric_is_rejected(metric):
    assert reason(hr(metric=metric)) == "invalid:metric"


# ---- direction ----


@pytest.mark.parametrize("direction", ["up", "ABOVE", "sideways"])
def test_unknown_direction_is_rejected(direction):
    assert reason(hr(direction=direction)) == "invalid:direction"


@pytest.mark.parametrize("field", ["metric", "direction"])
@pytest.mark.parametrize("value", ["", "x" * 17])
def test_empty_or_long_string_is_out_of_range(field, value):
    assert reason(hr(**{field: value})) == f"out_of_range:{field}"


def test_spo2_above_is_rejected():
    assert reason(spo2(value=99, direction="above")) == "invalid:direction"


@pytest.mark.parametrize("limit", [45, 130])
def test_no_pulse_above_is_rejected(limit):
    assert reason(no_pulse(direction="above", limit=limit)) == "invalid:direction"


# ---- value ranges ----


@pytest.mark.parametrize(
    ("metric_event", "value", "ok"),
    [
        (hr, 0, True),  # no pulse (with below)
        (hr, 1, False),
        (hr, 10, False),
        (hr, 19, False),
        (hr, 20, True),
        (hr, 250, True),
        (hr, 251, False),
        (hr, -1, False),
        (spo2, 0, False),
        (spo2, 49, False),
        (spo2, 50, True),
        (spo2, 100, True),
        (spo2, 101, False),
    ],
)
def test_value_range_boundaries(metric_event, value, ok):
    direction = "below"
    event = metric_event(value=value, direction=direction)
    if metric_event is hr:
        event["limit"] = 45

    result = reason(event)

    assert (result is None) is ok, result
    if not ok:
        assert result == "out_of_range:value"


# ---- limit ----


@pytest.mark.parametrize(
    ("metric_event", "limit", "ok"),
    [
        (hr, 45, True),
        (hr, 130, True),
        (hr, 50, False),
        (hr, 120, False),
        (hr, 0, False),
        (spo2, 88, True),
        (spo2, 92, False),
        (spo2, 45, False),
    ],
)
def test_only_the_fixed_device_limits_are_accepted(metric_event, limit, ok):
    result = reason(metric_event(limit=limit))

    assert (result is None) is ok, result
    if not ok:
        assert result == "invalid:limit"


# ---- held_s ----


@pytest.mark.parametrize(
    ("held_s", "ok"), [(9, False), (10, True), (600, True), (601, False), (0, False)]
)
def test_held_s_range(held_s, ok):
    result = reason(hr(held_s=held_s))

    assert (result is None) is ok, result
    if not ok:
        assert result == "out_of_range:held_s"


# ---- device type and function ----


def test_bedroom_vitals_alert_is_rejected():
    assert reason(hr(device_id=BEDROOM)) == "invalid:message"


def test_vitals_alert_is_wrong_function_for_telemetry():
    handled = frozenset({"vitals", "motion", "bed"})

    assert reason(hr(), handled) == "wrong_function"


def test_unknown_schema_version_is_rejected():
    assert reason(hr(schema_version=2)) is not None


def test_rejection_reason_never_contains_the_value():
    result = reason(hr(value=187, limit=999))

    assert "187" not in result
    assert "999" not in result
    assert result == "invalid:limit"


def test_device_id_is_the_wearable():
    assert validate_event(hr(), CRITICAL).device_id == WEARABLE
