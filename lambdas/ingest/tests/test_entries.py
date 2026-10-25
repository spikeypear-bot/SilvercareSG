"""entries.py: received_at conversion, clock skew, decimals, stored fields (15, 18). Pure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from entries import (
    build_entry,
    format_iso_ms,
    is_clock_skewed,
    received_at_datetime,
    to_dynamodb_value,
)
from events import BEDROOM, STORED_FIELDS, WEARABLE, iso_to_epoch_ms, rule_event
from validation import validate_event

ALL = frozenset({"vitals", "fall", "status", "motion", "bed"})


def entry_for(event):
    return build_entry(validate_event(event, ALL))


# ---- received_at ----


@pytest.mark.parametrize(
    ("iso", "epoch_ms"),
    [
        ("2026-10-18T03:12:00.410Z", 1792293120410),
        ("2026-10-18T03:12:00.000Z", 1792293120000),
        ("2026-10-18T03:12:00.999Z", 1792293120999),
        ("2026-10-18T03:12:00.001Z", 1792293120001),
        ("1970-01-01T00:00:00.000Z", 0),
    ],
)
def test_received_at_epoch_ms_becomes_iso_utc_with_millis(iso, epoch_ms):
    assert format_iso_ms(received_at_datetime(epoch_ms)) == iso


def test_received_at_is_exact_to_the_millisecond_without_float_error():
    # 1792293120123 / 1000 as a float is 1792293120.1229999...; must still be .123
    dt = received_at_datetime(1792293120123)

    assert dt.microsecond == 123_000
    assert dt.tzinfo == UTC


def test_entry_received_at_is_iso_string():
    event = rule_event("vitals", received_at=1792293120410)

    assert entry_for(event)["received_at"] == "2026-10-18T03:12:00.410Z"


def test_format_iso_ms_converts_other_timezones_to_utc():
    sgt = timezone(timedelta(hours=8))
    value = datetime(2026, 10, 18, 11, 12, 0, 410_000, tzinfo=sgt)

    assert format_iso_ms(value) == "2026-10-18T03:12:00.410Z"


# ---- clock skew ----

TS = "2026-10-18T03:12:00.000Z"
TS_MS = iso_to_epoch_ms(TS)
FIVE_MIN_MS = 5 * 60 * 1000


@pytest.mark.parametrize(
    ("received_offset_ms", "skewed"),
    [
        (0, False),
        (FIVE_MIN_MS, False),  # exactly 5 min: not more than 5
        (-FIVE_MIN_MS, False),
        (FIVE_MIN_MS + 1, True),  # device clock behind
        (-FIVE_MIN_MS - 1, True),  # device clock ahead
        (3_600_000, True),
        (-3_600_000, True),
    ],
)
def test_clock_skew_both_directions(received_offset_ms, skewed):
    event = rule_event("vitals", ts=TS, received_at=TS_MS + received_offset_ms)

    assert entry_for(event)["clock_skew"] is skewed


def test_is_clock_skewed_is_symmetric():
    a = datetime(2026, 10, 18, 3, 0, tzinfo=UTC)
    b = a + timedelta(minutes=6)

    assert is_clock_skewed(a, b) is True
    assert is_clock_skewed(b, a) is True


# ---- entry fields ----


@pytest.mark.parametrize(
    ("message", "device_id"),
    [
        ("vitals", WEARABLE),
        ("fall", WEARABLE),
        ("status", WEARABLE),
        ("motion", BEDROOM),
        ("bed", BEDROOM),
        ("status", BEDROOM),
    ],
)
def test_entry_has_envelope_and_only_allow_listed_fields(message, device_id):
    event = rule_event(message, device_id, unknown_extra=1, debug="synthetic")

    entry = entry_for(event)

    expected = {"message_id", "ts", "received_at", "clock_skew"} | STORED_FIELDS[
        (device_id.split("-")[0], message)
    ]
    assert set(entry) == expected
    assert entry["message_id"] == event["message_id"]
    assert entry["ts"] == event["ts"]


def test_entry_never_has_rule_fields_or_schema_version():
    entry = entry_for(rule_event("vitals"))

    for name in ("device_id", "message", "schema_version", "accel_g"):
        assert name not in entry


def test_float_becomes_exact_decimal():
    entry = entry_for(rule_event("vitals", skin_temp_c=33.4))

    assert entry["skin_temp_c"] == Decimal("33.4")
    assert isinstance(entry["skin_temp_c"], Decimal)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (33.4, Decimal("33.4")),
        (0.1, Decimal("0.1")),
        (0.91, Decimal("0.91")),
        (72, 72),
        (True, True),
        (False, False),
        (None, None),
        ("1.0.0", "1.0.0"),
    ],
)
def test_to_dynamodb_value(value, expected):
    result = to_dynamodb_value(value)

    assert result == expected
    assert type(result) is type(expected)
