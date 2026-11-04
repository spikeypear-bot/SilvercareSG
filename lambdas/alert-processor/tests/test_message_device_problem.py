"""Device-problem alert validation (docs/features/watchdog.md, criteria 8, 8a, 12 and
20). Pure."""

from __future__ import annotations

import pytest
from events import (
    DELETE,
    DEVICE_PROBLEM_DETAILS,
    DEVICE_PROBLEM_TYPES,
    DP_EVENT_TS,
    device_problem_body,
)
from message import Alert, Rejection, log_ids, parse_message


def parse(alert_type="battery_low", **overrides):
    return parse_message(device_problem_body(alert_type, **overrides))


@pytest.mark.parametrize("alert_type", DEVICE_PROBLEM_TYPES)
def test_each_device_problem_type_is_valid_with_severity_warning(alert_type):
    result = parse(alert_type)

    assert isinstance(result, Alert)
    assert result.alert_type == alert_type
    assert result.severity == "warning"
    assert result.details == DEVICE_PROBLEM_DETAILS[alert_type]


@pytest.mark.parametrize("alert_type", DEVICE_PROBLEM_TYPES)
def test_critical_device_problem_is_rejected(alert_type):
    assert parse(alert_type, severity="critical") == Rejection("invalid:severity")


@pytest.mark.parametrize(
    ("alert_type", "field"),
    [
        ("device_offline", "last_seen"),
        ("battery_low", "battery_pct"),
        ("not_worn", "since"),
    ],
)
def test_missing_details_field_is_rejected(alert_type, field):
    assert parse(alert_type, details={}) == Rejection(f"missing:details.{field}")


@pytest.mark.parametrize("alert_type", DEVICE_PROBLEM_TYPES)
def test_unknown_details_field_is_rejected(alert_type):
    details = {**DEVICE_PROBLEM_DETAILS[alert_type], "value": 1}

    assert parse(alert_type, details=details) == Rejection("unknown_field:details")


@pytest.mark.parametrize(
    ("alert_type", "details"),
    [
        ("battery_low", {"last_seen": DP_EVENT_TS}),
        ("device_offline", {"since": DP_EVENT_TS}),
        ("not_worn", {"battery_pct": 15}),
    ],
)
def test_another_types_details_are_rejected(alert_type, details):
    assert parse(alert_type, details=details) == Rejection("unknown_field:details")


@pytest.mark.parametrize("details", [None, [], "battery 15", 15])
def test_details_that_are_not_an_object_are_rejected(details):
    assert parse("battery_low", details=details) == Rejection("wrong_type:details")


def test_grouped_offline_alert_is_valid():
    details = {"last_seen": DP_EVENT_TS, "grouped": True}

    result = parse("device_offline", details=details)

    assert isinstance(result, Alert)
    assert result.details == details


def test_grouped_false_is_valid():
    result = parse(
        "device_offline", details={"last_seen": DP_EVENT_TS, "grouped": False}
    )

    assert isinstance(result, Alert)


@pytest.mark.parametrize("grouped", ["true", 1, None, 0])
def test_grouped_that_is_not_a_boolean_is_rejected(grouped):
    details = {"last_seen": DP_EVENT_TS, "grouped": grouped}

    assert parse("device_offline", details=details) == Rejection(
        "wrong_type:details.grouped"
    )


@pytest.mark.parametrize("alert_type", ["battery_low", "not_worn"])
def test_grouped_is_only_for_offline_alerts(alert_type):
    details = {**DEVICE_PROBLEM_DETAILS[alert_type], "grouped": True}

    assert parse(alert_type, details=details) == Rejection("unknown_field:details")


@pytest.mark.parametrize("battery", [0, 19, 100])
def test_battery_pct_from_0_to_100_is_valid(battery):
    result = parse("battery_low", details={"battery_pct": battery})

    assert isinstance(result, Alert)


@pytest.mark.parametrize("battery", [-1, 101])
def test_battery_pct_outside_0_to_100_is_rejected(battery):
    result = parse("battery_low", details={"battery_pct": battery})

    assert isinstance(result, Rejection)
    assert result.reason.endswith("battery_pct")


@pytest.mark.parametrize("battery", [15.5, "15", True, None])
def test_battery_pct_that_is_not_an_integer_is_rejected(battery):
    result = parse("battery_low", details={"battery_pct": battery})

    assert isinstance(result, Rejection)
    assert "battery_pct" in result.reason


@pytest.mark.parametrize(
    ("alert_type", "field"), [("device_offline", "last_seen"), ("not_worn", "since")]
)
@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("2026-11-04", "invalid"),
        ("2026-11-04T01:40:00Z", "invalid"),
        ("yesterday", "invalid"),
        (1793670000000, "wrong_type"),
        (None, "wrong_type"),
    ],
)
def test_bad_timestamp_in_details_is_rejected(alert_type, field, value, reason):
    result = parse(alert_type, details={field: value})

    assert result == Rejection(f"{reason}:details.{field}")


def test_device_problem_from_unassigned_device_is_valid():
    result = parse("device_offline", resident_id="unassigned")

    assert isinstance(result, Alert)


def test_device_problem_missing_top_level_field_is_rejected():
    assert parse("not_worn", details=DELETE) == Rejection("missing:details")


@pytest.mark.parametrize("alert_type", DEVICE_PROBLEM_TYPES)
def test_log_ids_name_the_device_problem_type(alert_type):
    ids = log_ids(device_problem_body(alert_type, severity="critical"))

    assert ids["alert_type"] == alert_type
