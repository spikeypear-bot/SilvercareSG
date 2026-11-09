"""The vitals queries' text (docs/features/vitals-trends.md, criteria 7-13), and the
copied summaries modules (criterion 23).

As the summaries tests, these check what the SQL says; the full queries have not run
here. The owner runs both on real Athena after the first deploy (go-live checks in the
spec), including the check that one day's 24 hours add up to that day's summary.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import vitals_sql
from registry import Device
from thresholds import DEFAULTS, Limits

SRC = Path(__file__).resolve().parents[1] / "src"
SUMMARIES_SRC = Path(__file__).resolve().parents[2] / "summaries" / "src"

RESIDENT = "res-0003"
DEVICES = [
    Device("wearable-0003", "wearable", RESIDENT, datetime(2026, 10, 1, tzinfo=UTC)),
    Device("bedroom-0003", "bedroom", RESIDENT, datetime(2026, 10, 1, tzinfo=UTC)),
]


@pytest.fixture
def hourly() -> str:
    return vitals_sql.hourly_query(
        RESIDENT, DEVICES, DEFAULTS, "2026-11-17", "2026-11-18"
    )


@pytest.mark.parametrize("name", ["sql.py", "registry.py", "thresholds.py"])
def test_copied_modules_match_the_summaries_lambda(name):
    assert (SRC / name).read_bytes() == (SUMMARIES_SRC / name).read_bytes()


def test_window_is_singapore_days_in_utc():
    start, end = vitals_sql.window("2026-11-17", "2026-11-18")
    assert start == datetime(2026, 11, 16, 16, tzinfo=UTC)
    assert end == datetime(2026, 11, 18, 16, tzinfo=UTC)


def test_reversed_range_raises():
    with pytest.raises(ValueError):
        vitals_sql.window("2026-11-18", "2026-11-17")


@pytest.mark.parametrize(
    ("table", "first"),
    [
        ("vitals", "2026-11-16"),
        ("motion", "2026-11-16"),
        ("bed", "2026-11-15"),
        ("vitals_alert", "2026-11-15"),
    ],
)
def test_partitions_cover_the_range_plus_the_summaries_lookback(table, first):
    dates = vitals_sql.partition_dates(table, "2026-11-17", "2026-11-18")
    assert dates[0] == first and dates[-1] == "2026-11-18"


def test_daily_query_reads_only_daily_summaries_for_the_resident():
    query = vitals_sql.daily_query(RESIDENT, "2026-11-01", "2026-11-18")
    assert re.findall(r"FROM (\w+)", query) == ["daily_summaries"]
    assert "resident_id = 'res-0003'" in query
    assert "thresholds.spo2_pct.min AS thr_spo2_pct_min" in query
    assert query.endswith('ORDER BY "date", generated_at')


@pytest.mark.parametrize("bad", ["res-0003' OR '1'='1", "res-3"])
def test_unvalidated_resident_never_reaches_the_sql(bad):
    with pytest.raises(ValueError):
        vitals_sql.daily_query(bad, "2026-11-01", "2026-11-18")


@pytest.mark.parametrize("bad", ["2026-11-01'", "2026-1-01"])
def test_unvalidated_date_never_reaches_the_sql(bad):
    with pytest.raises(ValueError):
        vitals_sql.daily_query(RESIDENT, bad, "2026-11-18")


def test_hourly_reads_only_the_six_clean_tables(hourly):
    tables = set(re.findall(r"FROM (\w+) WHERE \"date\"", hourly))
    assert tables == {"vitals", "fall", "vitals_alert", "alert_cancel", "bed", "motion"}


def test_hourly_hours_span_the_window(hourly):
    assert "sequence(TIMESTAMP '2026-11-16 16:00:00.000'," in hourly
    assert "TIMESTAMP '2026-11-18 16:00:00.000' - INTERVAL '1' HOUR" in hourly


def test_hourly_keeps_the_summaries_rules(hourly):
    assert "CASE WHEN clock_skew THEN received_at ELSE ts END" in hourly
    assert "e.event_time >= d.assigned_at" in hourly
    assert "PARTITION BY message_id ORDER BY received_at" in hourly
    assert "count_if(v.spo2_pct < l.spo2_min)" in hourly
    assert "> 600000" in hourly
    assert "FROM hours h LEFT JOIN vitals_stats" in hourly


def test_hourly_bed_minutes_are_cut_at_each_hour(hourly):
    assert "greatest(s.point_time, h.hour_start)" in hourly
    assert "least(s.next_time, h.hour_end)" in hourly


def test_hourly_ignores_devices_of_other_residents():
    other = Device(
        "wearable-0009", "wearable", "res-0009", datetime(2026, 10, 1, tzinfo=UTC)
    )
    query = vitals_sql.hourly_query(
        RESIDENT, [*DEVICES, other], DEFAULTS, "2026-11-18", "2026-11-18"
    )
    assert "wearable-0009" not in query


def test_hourly_inlines_the_resolved_limits():
    limits = dict(DEFAULTS) | {"spo2_pct": Limits(min=Decimal(88), max=None)}
    query = vitals_sql.hourly_query(
        RESIDENT, DEVICES, limits, "2026-11-18", "2026-11-18"
    )
    assert "('res-0003', 50, 120, 88," in query


def test_hourly_seven_days_fits_athenas_limit():
    query = vitals_sql.hourly_query(
        RESIDENT, DEVICES, DEFAULTS, "2026-11-12", "2026-11-18"
    )
    assert len(query.encode()) < 262_144
