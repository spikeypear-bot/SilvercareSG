"""validation.validate_event against docs/metrics.md (criteria 12, 13, 21). Pure, no moto."""

from __future__ import annotations

import pytest
from events import BEDROOM, DELETE, STORED_FIELDS, WEARABLE, rule_event
from validation import Rejection, ValidMessage, validate_event

ALL = frozenset({"vitals", "fall", "status", "motion", "bed"})

CASES = [
    ("vitals", WEARABLE),
    ("fall", WEARABLE),
    ("status", WEARABLE),
    ("motion", BEDROOM),
    ("bed", BEDROOM),
    ("status", BEDROOM),
]


def reason(event, handled=ALL) -> str | None:
    result = validate_event(event, handled)
    return result.reason if isinstance(result, Rejection) else None


# ---- valid messages ----


@pytest.mark.parametrize(("message", "device_id"), CASES)
def test_example_payload_is_valid(message, device_id):
    result = validate_event(rule_event(message, device_id), ALL)

    assert isinstance(result, ValidMessage)
    assert result.device_type == device_id.split("-")[0]
    assert result.message == message


@pytest.mark.parametrize(("message", "device_id"), CASES)
def test_valid_message_keeps_only_allow_listed_fields(message, device_id):
    event = rule_event(message, device_id, extra_field="ignored", note="synthetic")

    result = validate_event(event, ALL)

    assert set(result.fields) == STORED_FIELDS[(device_id.split("-")[0], message)]


# ---- event shape ----


@pytest.mark.parametrize("event", [None, [], "text", 7, b"{}"])
def test_non_dict_event_is_rejected(event):
    assert reason(event) == "invalid:event"


# ---- device_id ----


@pytest.mark.parametrize(
    "device_id",
    [
        "wearable-001",
        "wearable-00011",
        "watch-0001",
        "Wearable-0001",
        "wearable_0001",
        "wearable-000a",
        "wearable-0001\n",
        " wearable-0001",
        "",
        7,
        None,
        True,
    ],
)
def test_bad_device_id_is_rejected(device_id):
    event = rule_event("vitals") | {"device_id": device_id}

    assert reason(event) == "invalid:device_id"


def test_missing_device_id_is_rejected():
    event = rule_event("vitals")
    del event["device_id"]

    assert reason(event) == "invalid:device_id"


# ---- message type per device type ----


@pytest.mark.parametrize(
    ("device_id", "message"),
    [
        (WEARABLE, "motion"),
        (WEARABLE, "bed"),
        (BEDROOM, "vitals"),
        (BEDROOM, "fall"),
        (WEARABLE, "temperature"),
        (WEARABLE, "Vitals"),
        (WEARABLE, 1),
        (WEARABLE, None),
    ],
)
def test_message_not_allowed_for_device_type_is_rejected(device_id, message):
    event = rule_event("status", device_id) | {"message": message}

    assert reason(event) == "invalid:message"


def test_missing_message_is_rejected():
    event = rule_event("vitals")
    del event["message"]

    assert reason(event) == "invalid:message"


# ---- wrong_function ----


@pytest.mark.parametrize(
    ("message", "device_id", "handled"),
    [
        ("vitals", WEARABLE, frozenset({"status"})),
        ("fall", WEARABLE, frozenset({"vitals", "motion", "bed"})),
        ("status", BEDROOM, frozenset({"fall"})),
        ("motion", BEDROOM, frozenset({"status"})),
    ],
)
def test_message_type_not_handled_by_function_is_wrong_function(
    message, device_id, handled
):
    assert reason(rule_event(message, device_id), handled) == "wrong_function"


# ---- schema_version ----


def test_missing_schema_version_is_rejected():
    assert reason(rule_event("vitals", schema_version=DELETE)) == (
        "missing:schema_version"
    )


@pytest.mark.parametrize("value", [2, 0, "1", 1.0, True, None, [1]])
def test_schema_version_other_than_integer_1_is_rejected(value):
    assert reason(rule_event("vitals", schema_version=value)) == (
        "invalid:schema_version"
    )


# ---- message_id ----


def test_missing_message_id_is_rejected():
    assert reason(rule_event("vitals", message_id=DELETE)) == "missing:message_id"


@pytest.mark.parametrize(
    "value",
    [
        "not-a-uuid",
        "5f0c9a52-3c1e-1c8e-9f57-0d6f2b7a1e11",  # version 1
        "5f0c9a52-3c1e-4c8e-7f57-0d6f2b7a1e11",  # bad variant
        "5f0c9a523c1e4c8e9f570d6f2b7a1e11",  # no dashes
        "5f0c9a52-3c1e-4c8e-9f57-0d6f2b7a1e11\n",
        "{5f0c9a52-3c1e-4c8e-9f57-0d6f2b7a1e11}",
        "",
        12345,
        None,
        True,
    ],
)
def test_message_id_that_is_not_a_uuid_v4_string_is_rejected(value):
    assert reason(rule_event("vitals", message_id=value)) == "invalid:message_id"


def test_uppercase_uuid_v4_is_accepted():
    event = rule_event("vitals", message_id="5F0C9A52-3C1E-4C8E-9F57-0D6F2B7A1E11")

    assert reason(event) is None


# ---- ts ----


def test_missing_ts_is_rejected():
    assert reason(rule_event("vitals", ts=DELETE)) == "missing:ts"


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-18T03:12:00Z",  # no millis
        "2026-10-18T03:12:00.000",  # no Z
        "2026-10-18T03:12:00.000+00:00",
        "2026-10-18T03:12:00.000000Z",  # micros
        "2026-10-18 03:12:00.000Z",
        "2026-10-18T03:12:00.000z",
        "2026-02-30T03:12:00.000Z",  # not a real date
        "2026-13-01T03:12:00.000Z",
        "2026-10-18T24:00:00.000Z",  # not a real time
        "2026-10-18T03:60:00.000Z",
        "2026-10-18T03:12:00.000Z\n",
        1792293120000,
        None,
        True,
    ],
)
def test_bad_ts_is_rejected(value):
    assert reason(rule_event("vitals", ts=value)) == "invalid:ts"


def test_leap_day_ts_is_accepted():
    assert reason(rule_event("vitals", ts="2028-02-29T23:59:59.999Z")) is None


# ---- received_at (added by the rule) ----


@pytest.mark.parametrize("value", [DELETE, -1, "1792293120410", True, 1.5, None])
def test_bad_received_at_is_rejected(value):
    assert reason(rule_event("vitals", received_at=value)) == "invalid:received_at"


# ---- required fields: missing ----

REQUIRED = (
    [
        ("vitals", WEARABLE, name)
        for name in ("hr_bpm", "spo2_pct", "skin_temp_c", "activity_pct", "accel_g")
    ]
    + [
        ("fall", WEARABLE, name)
        for name in ("confidence", "peak_g", "still_s", "accel_window")
    ]
    + [
        ("status", WEARABLE, name)
        for name in ("battery_pct", "worn", "charging", "fw_version")
    ]
    + [
        ("motion", BEDROOM, "motion"),
        ("bed", BEDROOM, "occupied"),
        ("status", BEDROOM, "battery_pct"),
        ("status", BEDROOM, "fw_version"),
    ]
)


@pytest.mark.parametrize(("message", "device_id", "field"), REQUIRED)
def test_missing_required_field_is_rejected(message, device_id, field):
    event = rule_event(message, device_id, **{field: DELETE})

    assert reason(event) == f"missing:{field}"


# ---- wrong types ----

WRONG_TYPE = [
    # integer fields: no booleans, no decimals, no strings
    ("vitals", WEARABLE, "hr_bpm", True),
    ("vitals", WEARABLE, "hr_bpm", False),
    ("vitals", WEARABLE, "hr_bpm", 72.0),
    ("vitals", WEARABLE, "hr_bpm", 72.5),
    ("vitals", WEARABLE, "hr_bpm", "72"),
    ("vitals", WEARABLE, "spo2_pct", True),
    ("vitals", WEARABLE, "spo2_pct", 97.0),
    ("vitals", WEARABLE, "activity_pct", True),
    ("vitals", WEARABLE, "activity_pct", 12.0),
    ("vitals", WEARABLE, "activity_pct", None),  # not nullable
    ("fall", WEARABLE, "still_s", True),
    ("fall", WEARABLE, "still_s", 20.0),
    ("fall", WEARABLE, "still_s", None),
    ("status", WEARABLE, "battery_pct", True),
    ("status", WEARABLE, "battery_pct", 64.0),
    ("status", WEARABLE, "battery_pct", None),  # nullable on bedroom only
    ("status", BEDROOM, "battery_pct", True),
    ("status", BEDROOM, "battery_pct", 88.5),
    # number fields: no booleans, no strings, finite only
    ("vitals", WEARABLE, "skin_temp_c", True),
    ("vitals", WEARABLE, "skin_temp_c", "33.4"),
    ("vitals", WEARABLE, "skin_temp_c", float("nan")),
    ("fall", WEARABLE, "confidence", True),
    ("fall", WEARABLE, "confidence", "0.9"),
    ("fall", WEARABLE, "confidence", None),
    ("fall", WEARABLE, "peak_g", False),
    ("fall", WEARABLE, "peak_g", float("inf")),
    # booleans
    ("status", WEARABLE, "worn", 1),
    ("status", WEARABLE, "worn", "true"),
    ("status", WEARABLE, "worn", None),
    ("status", WEARABLE, "charging", 0),
    ("motion", BEDROOM, "motion", 1),
    ("motion", BEDROOM, "motion", None),
    ("bed", BEDROOM, "occupied", "yes"),
    ("bed", BEDROOM, "occupied", 0),
    # strings
    ("status", WEARABLE, "fw_version", 1),
    ("status", WEARABLE, "fw_version", None),
    ("status", BEDROOM, "fw_version", ["1.0.0"]),
]


@pytest.mark.parametrize(("message", "device_id", "field", "value"), WRONG_TYPE)
def test_wrong_type_is_rejected(message, device_id, field, value):
    event = rule_event(message, device_id, **{field: value})

    assert reason(event) == f"wrong_type:{field}"


# ---- ranges ----

OUT_OF_RANGE = [
    ("vitals", WEARABLE, "hr_bpm", 19),
    ("vitals", WEARABLE, "hr_bpm", 251),
    ("vitals", WEARABLE, "hr_bpm", 999),
    ("vitals", WEARABLE, "spo2_pct", 49),
    ("vitals", WEARABLE, "spo2_pct", 101),
    ("vitals", WEARABLE, "skin_temp_c", 19.9),
    ("vitals", WEARABLE, "skin_temp_c", 45.1),
    ("vitals", WEARABLE, "activity_pct", -1),
    ("vitals", WEARABLE, "activity_pct", 101),
    ("fall", WEARABLE, "confidence", -0.01),
    ("fall", WEARABLE, "confidence", 1.01),
    ("fall", WEARABLE, "peak_g", -0.1),
    ("fall", WEARABLE, "peak_g", 16.1),
    ("fall", WEARABLE, "still_s", -1),
    ("fall", WEARABLE, "still_s", 601),
    ("status", WEARABLE, "battery_pct", -1),
    ("status", WEARABLE, "battery_pct", 101),
    ("status", BEDROOM, "battery_pct", -1),
    ("status", BEDROOM, "battery_pct", 101),
    ("status", WEARABLE, "fw_version", ""),
    ("status", WEARABLE, "fw_version", "1" * 33),
    ("status", BEDROOM, "fw_version", ""),
    ("status", BEDROOM, "fw_version", "x" * 33),
]


@pytest.mark.parametrize(("message", "device_id", "field", "value"), OUT_OF_RANGE)
def test_out_of_range_value_is_rejected(message, device_id, field, value):
    event = rule_event(message, device_id, **{field: value})

    assert reason(event) == f"out_of_range:{field}"


IN_RANGE_BOUNDARIES = [
    ("vitals", WEARABLE, "hr_bpm", 20),
    ("vitals", WEARABLE, "hr_bpm", 250),
    ("vitals", WEARABLE, "spo2_pct", 50),
    ("vitals", WEARABLE, "spo2_pct", 100),
    ("vitals", WEARABLE, "skin_temp_c", 20.0),
    ("vitals", WEARABLE, "skin_temp_c", 45),  # integer in a number field
    ("vitals", WEARABLE, "activity_pct", 0),
    ("vitals", WEARABLE, "activity_pct", 100),
    ("fall", WEARABLE, "confidence", 0),
    ("fall", WEARABLE, "confidence", 1.0),
    ("fall", WEARABLE, "peak_g", 0),
    ("fall", WEARABLE, "peak_g", 16),
    ("fall", WEARABLE, "still_s", 0),
    ("fall", WEARABLE, "still_s", 600),
    ("status", WEARABLE, "battery_pct", 0),
    ("status", WEARABLE, "battery_pct", 100),
    ("status", WEARABLE, "fw_version", "1"),
    ("status", WEARABLE, "fw_version", "1" * 32),
    ("status", BEDROOM, "battery_pct", 0),
    ("status", BEDROOM, "battery_pct", 100),
]


@pytest.mark.parametrize(
    ("message", "device_id", "field", "value"), IN_RANGE_BOUNDARIES
)
def test_boundary_value_is_accepted(message, device_id, field, value):
    event = rule_event(message, device_id, **{field: value})

    assert reason(event) is None


@pytest.mark.parametrize(
    ("message", "device_id", "field"),
    [
        ("vitals", WEARABLE, "hr_bpm"),
        ("vitals", WEARABLE, "spo2_pct"),
        ("vitals", WEARABLE, "skin_temp_c"),
        ("status", BEDROOM, "battery_pct"),
    ],
)
def test_nullable_field_accepts_null(message, device_id, field):
    result = validate_event(rule_event(message, device_id, **{field: None}), ALL)

    assert isinstance(result, ValidMessage)
    assert result.fields[field] is None


# ---- accel_g (vitals) and accel_window (fall): validated, not stored ----


@pytest.mark.parametrize(
    "value",
    [
        None,
        [0.02, -0.11, 0.99],
        "x",
        {"x": 0.02, "y": -0.11},
        {"x": 0.02, "y": -0.11, "z": "0.99"},
        {"x": True, "y": -0.11, "z": 0.99},
        {"x": 16.1, "y": -0.11, "z": 0.99},
        {"x": 0.02, "y": -16.1, "z": 0.99},
        {"x": 0.02, "y": -0.11, "z": None},
        {"x": float("nan"), "y": -0.11, "z": 0.99},
    ],
)
def test_malformed_accel_g_is_rejected(value):
    assert reason(rule_event("vitals", accel_g=value)) == "invalid:accel_g"


def test_accel_g_at_limits_is_accepted():
    event = rule_event("vitals", accel_g={"x": -16, "y": 16, "z": 0})

    assert reason(event) is None


def _window(**overrides):
    window = {"hz": 25, "start_offset_ms": -1000, "samples": [[0.0, 0.0, 1.0]] * 50}
    window.update(overrides)
    return window


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        "window",
        _window(hz=50),
        _window(hz=25.0),
        _window(hz="25"),
        _window(start_offset_ms=-500),
        _window(start_offset_ms=-1000.0),
        _window(samples=[[0.0, 0.0, 1.0]] * 49),
        _window(samples=[[0.0, 0.0, 1.0]] * 51),
        _window(samples="samples"),
        _window(samples=[[0.0, 0.0]] + [[0.0, 0.0, 1.0]] * 49),
        _window(samples=[[0.0, 0.0, 1.0, 0.0]] + [[0.0, 0.0, 1.0]] * 49),
        _window(samples=[[0.0, 0.0, 17.0]] + [[0.0, 0.0, 1.0]] * 49),
        _window(samples=[[0.0, True, 1.0]] + [[0.0, 0.0, 1.0]] * 49),
        _window(samples=[[0.0, "0", 1.0]] + [[0.0, 0.0, 1.0]] * 49),
        _window(samples=[{"x": 0}] + [[0.0, 0.0, 1.0]] * 49),
        {"start_offset_ms": -1000, "samples": [[0.0, 0.0, 1.0]] * 50},
        {"hz": 25, "samples": [[0.0, 0.0, 1.0]] * 50},
        {"hz": 25, "start_offset_ms": -1000},
    ],
)
def test_malformed_accel_window_is_rejected(value):
    assert reason(rule_event("fall", accel_window=value)) == "invalid:accel_window"


def test_hz_true_is_not_accepted_as_integer():
    # True == 1, not 25, but make sure booleans never count as integers here either.
    assert reason(rule_event("fall", accel_window=_window(hz=True))) == (
        "invalid:accel_window"
    )


def test_accel_g_and_accel_window_are_not_in_stored_fields():
    vitals = validate_event(rule_event("vitals"), ALL)
    fall = validate_event(rule_event("fall"), ALL)

    assert "accel_g" not in vitals.fields
    assert "accel_window" not in fall.fields
