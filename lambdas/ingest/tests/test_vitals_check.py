"""The cloud vitals check (docs/features/vitals-alerts.md, criteria 1, 6, 9-10, 29).

Pure, no moto: entries are built here as the write rule stores them (newest first,
values as Decimal or None).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import vitals_check
from thresholds import DEFAULTS, Limits

NEWEST_TS = "2026-10-18T03:14:00.000Z"
PREVIOUS_TS = "2026-10-18T03:13:00.000Z"


def entry(ts: str, hr=72, spo2=97, skin="33.4") -> dict:
    return {
        "message_id": "00000000-0000-4000-8000-000000000001",
        "ts": ts,
        "received_at": ts,
        "clock_skew": False,
        "hr_bpm": None if hr is None else Decimal(hr),
        "spo2_pct": None if spo2 is None else Decimal(spo2),
        "skin_temp_c": None if skin is None else Decimal(skin),
        "activity_pct": Decimal(12),
    }


def pair(newest: dict, previous: dict) -> list[dict]:
    return [
        entry(NEWEST_TS, **newest),
        entry(PREVIOUS_TS, **previous),
    ]


def types(findings) -> list[str]:
    return [f.alert_type for f in findings]


# ---- 2 in a row, same direction (criterion 9) ----


def test_two_high_heart_rates_in_a_row_raise_hr_high():
    (finding,) = vitals_check.check(pair({"hr": 128}, {"hr": 125}), DEFAULTS)

    assert finding == vitals_check.Finding(
        alert_type="hr_high",
        metric="hr_bpm",
        direction="above",
        value=Decimal(128),
        previous_value=Decimal(125),
        limit=Decimal(120),
    )


@pytest.mark.parametrize(
    ("newest", "previous", "alert_type", "limit"),
    [
        ({"hr": 121}, {"hr": 121}, "hr_high", Decimal(120)),
        ({"hr": 49}, {"hr": 45}, "hr_low", Decimal(50)),
        ({"spo2": 91}, {"spo2": 88}, "spo2_low", Decimal(92)),
        ({"skin": "38.1"}, {"skin": "39.0"}, "skin_temp_high", Decimal("38.0")),
        ({"skin": "29.9"}, {"skin": "25.0"}, "skin_temp_low", Decimal("30.0")),
    ],
)
def test_each_metric_and_direction_maps_to_its_alert_type(
    newest, previous, alert_type, limit
):
    (finding,) = vitals_check.check(pair(newest, previous), DEFAULTS)

    assert finding.alert_type == alert_type
    assert finding.limit == limit


def test_one_abnormal_reading_raises_nothing():
    assert vitals_check.check(pair({"hr": 128}, {"hr": 72}), DEFAULTS) == []


def test_previous_abnormal_newest_normal_raises_nothing():
    assert vitals_check.check(pair({"hr": 72}, {"hr": 128}), DEFAULTS) == []


@pytest.mark.parametrize(
    ("newest", "previous"),
    [
        ({"hr": 130}, {"hr": 40}),
        ({"hr": 40}, {"hr": 130}),
        ({"skin": "39.0"}, {"skin": "29.0"}),
        ({"skin": "29.0"}, {"skin": "39.0"}),
    ],
)
def test_abnormal_in_opposite_directions_raises_nothing(newest, previous):
    assert vitals_check.check(pair(newest, previous), DEFAULTS) == []


def test_only_one_entry_raises_nothing():
    assert vitals_check.check([entry(NEWEST_TS, hr=130)], DEFAULTS) == []


@pytest.mark.parametrize("entries", [None, []])
def test_no_entries_raise_nothing(entries):
    assert vitals_check.check(entries, DEFAULTS) == []


def test_only_the_newest_two_entries_are_compared():
    entries = [
        entry(NEWEST_TS, hr=128),
        entry(PREVIOUS_TS, hr=72),
        entry("2026-10-18T03:12:00.000Z", hr=130),
    ]

    assert vitals_check.check(entries, DEFAULTS) == []


# ---- strict boundaries (criterion 6) ----


@pytest.mark.parametrize(
    ("field", "value"),
    [("hr", 50), ("hr", 120), ("spo2", 92), ("skin", "30.0"), ("skin", "38.0")],
)
def test_two_readings_at_a_limit_are_normal(field, value):
    entries = pair({field: value}, {field: value})

    assert vitals_check.check(entries, DEFAULTS) == []


@pytest.mark.parametrize(
    ("field", "value", "alert_type"),
    [
        ("hr", 49, "hr_low"),
        ("hr", 121, "hr_high"),
        ("spo2", 91, "spo2_low"),
        ("skin", "29.9", "skin_temp_low"),
        ("skin", "38.1", "skin_temp_high"),
    ],
)
def test_two_readings_just_past_a_limit_alert(field, value, alert_type):
    entries = pair({field: value}, {field: value})

    assert types(vitals_check.check(entries, DEFAULTS)) == [alert_type]


@pytest.mark.parametrize(
    ("field", "value"),
    [("hr", 51), ("hr", 119), ("spo2", 93), ("skin", "30.1"), ("skin", "37.9")],
)
def test_two_readings_just_inside_a_limit_are_normal(field, value):
    entries = pair({field: value}, {field: value})

    assert vitals_check.check(entries, DEFAULTS) == []


# ---- nulls (criterion 6) ----


@pytest.mark.parametrize(
    ("newest", "previous"),
    [
        ({"hr": None}, {"hr": 130}),
        ({"hr": 130}, {"hr": None}),
        ({"spo2": None}, {"spo2": 85}),
        ({"skin": None}, {"skin": "39.0"}),
        ({"hr": None}, {"hr": None}),
    ],
)
def test_null_breaks_a_streak(newest, previous):
    assert vitals_check.check(pair(newest, previous), DEFAULTS) == []


def test_null_in_one_metric_does_not_stop_another():
    entries = pair({"hr": None, "spo2": 85}, {"hr": 130, "spo2": 86})

    assert types(vitals_check.check(entries, DEFAULTS)) == ["spo2_low"]


# ---- 3-minute pairing (criterion 9) ----


@pytest.mark.parametrize(
    "previous_ts",
    ["2026-10-18T03:11:00.000Z", "2026-10-18T03:13:59.999Z"],
)
def test_entries_at_most_3_minutes_apart_pair(previous_ts):
    entries = [entry(NEWEST_TS, hr=130), entry(previous_ts, hr=130)]

    assert types(vitals_check.check(entries, DEFAULTS)) == ["hr_high"]


@pytest.mark.parametrize(
    "previous_ts",
    ["2026-10-18T03:10:59.999Z", "2026-10-18T01:14:00.000Z"],
)
def test_entries_more_than_3_minutes_apart_do_not_pair(previous_ts):
    entries = [entry(NEWEST_TS, hr=130), entry(previous_ts, hr=130)]

    assert vitals_check.check(entries, DEFAULTS) == []


@pytest.mark.parametrize("bad_ts", [None, "not a ts", 1792293240000])
def test_unparseable_ts_does_not_pair(bad_ts):
    entries = [entry(NEWEST_TS, hr=130), entry(PREVIOUS_TS, hr=130)]
    entries[1]["ts"] = bad_ts

    assert vitals_check.check(entries, DEFAULTS) == []


# ---- several alerts from one message (criterion 9) ----


def test_one_message_can_raise_several_alerts_in_metric_order():
    entries = pair(
        {"hr": 130, "spo2": 85, "skin": "38.5"},
        {"hr": 125, "spo2": 88, "skin": "38.2"},
    )

    findings = vitals_check.check(entries, DEFAULTS)

    assert types(findings) == ["hr_high", "spo2_low", "skin_temp_high"]
    assert [f.previous_value for f in findings] == [
        Decimal(125),
        Decimal(88),
        Decimal("38.2"),
    ]


# ---- per-resident limits ----


def test_personal_limits_are_used():
    limits = {**DEFAULTS, "spo2_pct": Limits(min=Decimal(88), max=None)}

    assert vitals_check.check(pair({"spo2": 89}, {"spo2": 90}), limits) == []
    (finding,) = vitals_check.check(pair({"spo2": 87}, {"spo2": 86}), limits)
    assert finding.limit == Decimal(88)


# ---- mapping table (criterion 1) ----


def test_alert_type_mapping_is_exactly_criterion_1():
    assert vitals_check.ALERT_TYPES == {
        ("hr_bpm", "above"): "hr_high",
        ("hr_bpm", "below"): "hr_low",
        ("spo2_pct", "below"): "spo2_low",
        ("skin_temp_c", "above"): "skin_temp_high",
        ("skin_temp_c", "below"): "skin_temp_low",
    }
