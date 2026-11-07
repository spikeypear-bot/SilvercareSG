"""Summary objects from the query's rows (docs/features/curated-layer.md, criteria 11
and 15). Pure, no AWS."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
import summary
from helpers import DATE, empty_row, reading_row
from thresholds import DEFAULTS, Limits

GENERATED_AT = "2026-10-19T00:40:05Z"
LIMITS = {"res-0001": dict(DEFAULTS), "res-0002": dict(DEFAULTS)}

FIELD_ORDER = [
    "resident_id",
    "date",
    "hr_bpm_min",
    "hr_bpm_avg",
    "hr_bpm_max",
    "spo2_pct_min",
    "spo2_pct_avg",
    "spo2_pct_max",
    "skin_temp_c_min",
    "skin_temp_c_avg",
    "skin_temp_c_max",
    "activity_pct_avg",
    "vitals_readings",
    "hr_bpm_below",
    "hr_bpm_above",
    "spo2_pct_below",
    "skin_temp_c_below",
    "skin_temp_c_above",
    "thresholds",
    "bed_minutes",
    "motion_events",
    "falls",
    "vitals_alert_episodes",
    "cancels",
    "generated_at",
    "schema_version",
]


def build_one(row: dict, limits: dict | None = None) -> dict:
    parsed = summary.parse_row(row)
    return summary.build(parsed, DATE, limits or dict(DEFAULTS), GENERATED_AT)


def test_fields_are_in_criterion_11_order():
    assert list(build_one(reading_row("res-0001"))) == FIELD_ORDER


def test_values_are_typed_as_criterion_11():
    built = build_one(reading_row("res-0001"))
    assert built["resident_id"] == "res-0001"
    assert built["date"] == DATE
    for name in ("hr_bpm_min", "hr_bpm_max", "spo2_pct_min", "spo2_pct_max"):
        assert type(built[name]) is int
    for name in (
        "hr_bpm_avg",
        "spo2_pct_avg",
        "skin_temp_c_min",
        "skin_temp_c_avg",
        "skin_temp_c_max",
        "activity_pct_avg",
    ):
        assert type(built[name]) is float
    for name in (
        "vitals_readings",
        "hr_bpm_below",
        "hr_bpm_above",
        "spo2_pct_below",
        "skin_temp_c_below",
        "skin_temp_c_above",
        "bed_minutes",
        "motion_events",
        "falls",
        "vitals_alert_episodes",
        "cancels",
    ):
        assert type(built[name]) is int
    assert built["generated_at"] == GENERATED_AT
    assert built["schema_version"] == 1


def test_resident_without_readings_has_null_statistics_and_zero_counts():
    built = build_one(empty_row("res-0001"))
    for name in (
        "hr_bpm_min",
        "hr_bpm_avg",
        "hr_bpm_max",
        "spo2_pct_min",
        "spo2_pct_avg",
        "spo2_pct_max",
        "skin_temp_c_min",
        "skin_temp_c_avg",
        "skin_temp_c_max",
        "activity_pct_avg",
        "bed_minutes",
    ):
        assert built[name] is None, name
    for name in ("vitals_readings", "motion_events", "falls", "cancels"):
        assert built[name] == 0


@pytest.mark.parametrize(
    "text,expected",
    [("76.46", 76.5), ("76.44", 76.4), ("76", 76.0), ("7.65E1", 76.5)],
)
def test_averages_have_one_decimal(text, expected):
    row = {**empty_row("res-0001"), "hr_bpm_avg": text}
    assert build_one(row)["hr_bpm_avg"] == expected


@pytest.mark.parametrize("text,expected", [("72", 72), ("72.0", 72), ("1.44E3", 1440)])
def test_integer_columns_accept_integral_text(text, expected):
    row = {**empty_row("res-0001"), "vitals_readings": text}
    assert build_one(row)["vitals_readings"] == expected


@pytest.mark.parametrize(
    "column,text",
    [
        ("hr_bpm_max", "72.5"),
        ("hr_bpm_max", "high"),
        ("falls", ""),
        ("hr_bpm_avg", "NaN"),
        ("hr_bpm_avg", "Infinity"),
        ("skin_temp_c_max", "warm"),
        ("vitals_readings", "inf"),
    ],
)
def test_bad_value_raises_without_repeating_it(column, text):
    row = {**empty_row("res-0001"), column: text}
    with pytest.raises(summary.BadResultError) as error:
        summary.parse_row(row)
    if text:
        assert text not in str(error.value)


def test_thresholds_are_the_limits_used():
    limits = {
        "hr_bpm": Limits(Decimal(45), Decimal(110)),
        "spo2_pct": Limits(Decimal(88), None),
        "skin_temp_c": Limits(Decimal("31.5"), Decimal("37.5")),
    }
    built = build_one(reading_row("res-0001"), limits)
    assert built["thresholds"] == {
        "hr_bpm": {"min": 45, "max": 110},
        "spo2_pct": {"min": 88},
        "skin_temp_c": {"min": 31.5, "max": 37.5},
    }


# ---- build_all: exactly one row per mapped resident ----


def test_build_all_builds_every_resident():
    rows = [empty_row("res-0001"), reading_row("res-0002")]
    built = summary.build_all(rows, DATE, LIMITS, GENERATED_AT)
    assert [b["resident_id"] for b in built] == ["res-0001", "res-0002"]


def test_build_all_uses_each_residents_own_limits():
    limits = {
        "res-0001": dict(DEFAULTS),
        "res-0002": {**DEFAULTS, "spo2_pct": Limits(Decimal(88), None)},
    }
    rows = [empty_row("res-0001"), empty_row("res-0002")]
    first, second = summary.build_all(rows, DATE, limits, GENERATED_AT)
    assert first["thresholds"]["spo2_pct"] == {"min": 92}
    assert second["thresholds"]["spo2_pct"] == {"min": 88}


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [empty_row("res-0001")],
        [empty_row("res-0001"), empty_row("res-0001")],
        [empty_row("res-0001"), empty_row("res-0002"), empty_row("res-0003")],
        [empty_row("res-0001"), empty_row("res-0003")],
        [empty_row("res-0001"), {**empty_row("res-0002"), "resident_id": None}],
        [empty_row("res-0001"), empty_row("Test Resident A")],
    ],
    ids=["none", "missing", "duplicate", "extra", "other", "null-id", "bad-id"],
)
def test_build_all_refuses_anything_but_one_row_per_resident(rows):
    with pytest.raises(summary.BadResultError) as error:
        summary.build_all(rows, DATE, LIMITS, GENERATED_AT)
    assert "Test Resident A" not in str(error.value)


# ---- the object body (criterion 15) ----


def test_body_is_one_line_of_compact_utf8_json():
    built = build_one(reading_row("res-0001"))
    body = summary.body(built)
    assert isinstance(body, bytes)
    assert b"\n" not in body
    assert b", " not in body and b": " not in body
    assert json.loads(body.decode("utf-8")) == built


def test_body_keeps_field_order():
    built = build_one(empty_row("res-0001"))
    assert list(json.loads(summary.body(built))) == FIELD_ORDER


def test_body_writes_nulls_as_json_null():
    body = summary.body(build_one(empty_row("res-0001")))
    assert b'"hr_bpm_min":null' in body
    assert b'"bed_minutes":null' in body
