"""Athena SQL for GET /residents/{resident_id}/vitals (docs/features/vitals-trends.md,
criteria 7-13). Pure strings, no AWS.

`sql.py`, `registry.py`, and `thresholds.py` are byte-for-byte copies of the summaries
Lambda's (each function is zipped from its own src/; tests/test_copies.py fails if they
drift). This module reuses their validated quoting and the summaries rules, applied to
one resident and to Singapore hours instead of days:

- `daily_query`: rows of `daily_summaries` for one resident and a date range.
- `hourly_query`: the summaries query's rules per hour, for one resident's current
  devices, over [from 00:00 SGT, to + 1 day 00:00 SGT). Singapore is UTC+8 with no
  daylight saving, so a UTC hour is exactly a Singapore hour.

Only validated IDs, dates, timestamps, and numbers reach the text.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta

import sql
from registry import Device
from thresholds import Limits

SUMMARY_COLUMNS = tuple(name for name, _ in sql.RESULT_COLUMNS if name != "resident_id")
# The bed and vitals_alert look-backs, as the summaries query (sql.READ_DAYS).
LOOKBACK_DAYS = {table: max(backs) for table, backs in sql.READ_DAYS.items()}
# daily_summaries' thresholds struct, one column per limit: column -> (metric, limit).
THRESHOLD_COLUMNS = {
    "thr_hr_bpm_min": ("hr_bpm", "min"),
    "thr_hr_bpm_max": ("hr_bpm", "max"),
    "thr_spo2_pct_min": ("spo2_pct", "min"),
    "thr_skin_temp_c_min": ("skin_temp_c", "min"),
    "thr_skin_temp_c_max": ("skin_temp_c", "max"),
}


def window(from_date: str, to_date: str) -> tuple[datetime, datetime]:
    """[from 00:00 SGT, to + 1 day 00:00 SGT) as UTC."""
    start = sql._checked_date(from_date) - sql.SGT_OFFSET
    end = sql._checked_date(to_date) - sql.SGT_OFFSET + timedelta(days=1)
    if end <= start:
        raise ValueError("from must not be after to")
    return start, end


def partition_dates(table: str, from_date: str, to_date: str) -> list[str]:
    """The clean partitions (UTC dates of received_at) `table` is read from."""
    first = sql._checked_date(from_date) - timedelta(days=LOOKBACK_DAYS[table])
    last = sql._checked_date(to_date)
    days = (last - first).days
    return [(first + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days + 1)]


# ---- daily ----


def daily_query(resident_id: str, from_date: str, to_date: str) -> str:
    """One resident's stored summaries for the dates, oldest first. The `thresholds`
    struct's fields come back as their own columns (no ROW-to-JSON cast, whose output
    differs between engine versions). A re-run date could briefly hold two objects:
    ordered by generated_at, so the newest wins."""
    window(from_date, to_date)
    columns = ", ".join(f'"{c}"' for c in SUMMARY_COLUMNS)
    limits = ", ".join(
        f"thresholds.{metric}.{name} AS {column}"
        for column, (metric, name) in THRESHOLD_COLUMNS.items()
    )
    return (
        f'SELECT "date", {columns}, {limits}'
        f" FROM {sql.SUMMARY_TABLE}"
        f' WHERE "date" BETWEEN {sql._quote(from_date)} AND {sql._quote(to_date)}'
        f" AND resident_id = {sql._resident_id(resident_id)}"
        ' ORDER BY "date", generated_at'
    )


# ---- hourly ----


def events(
    table: str, from_date: str, to_date: str, columns: tuple[str, ...] = ()
) -> str:
    """As sql.events, over the range's partitions."""
    dates = ", ".join(sql._quote(d) for d in partition_dates(table, from_date, to_date))
    inner_columns = "".join(f', "{c}"' for c in columns)
    outer_columns = "".join(f', e."{c}"' for c in columns)
    inner = (
        f"SELECT device_id, {sql.EVENT_TIME} AS event_time{inner_columns},"
        " row_number() OVER (PARTITION BY message_id ORDER BY received_at) AS copy_num"
        f' FROM {table} WHERE "date" IN ({dates})'
    )
    return (
        f"SELECT d.device_id, e.event_time{outer_columns}"
        f" FROM ({inner}) e JOIN devices d ON e.device_id = d.device_id"
        f" WHERE e.copy_num = 1 AND d.device_type = {sql._quote(sql.DEVICE_TYPE[table])}"
        " AND e.event_time >= d.assigned_at"
    )


def _in_range(start: str, end: str, column: str = "event_time") -> str:
    return f"{column} >= {start} AND {column} < {end}"


HOUR = "date_trunc('hour', event_time)"


def _vitals_stats(f: str, t: str, start: str, end: str) -> str:
    columns = ("hr_bpm", "spo2_pct", "skin_temp_c", "activity_pct")
    return (
        f"SELECT date_trunc('hour', v.event_time) AS hour_start,"
        " min(v.hr_bpm) AS hr_bpm_min, round(avg(v.hr_bpm), 1) AS hr_bpm_avg,"
        " max(v.hr_bpm) AS hr_bpm_max,"
        " min(v.spo2_pct) AS spo2_pct_min, round(avg(v.spo2_pct), 1) AS spo2_pct_avg,"
        " max(v.spo2_pct) AS spo2_pct_max,"
        " round(min(v.skin_temp_c), 1) AS skin_temp_c_min,"
        " round(avg(v.skin_temp_c), 1) AS skin_temp_c_avg,"
        " round(max(v.skin_temp_c), 1) AS skin_temp_c_max,"
        " round(avg(v.activity_pct), 1) AS activity_pct_avg,"
        " count(*) AS vitals_readings,"
        " count_if(v.hr_bpm < l.hr_min) AS hr_bpm_below,"
        " count_if(v.hr_bpm > l.hr_max) AS hr_bpm_above,"
        " count_if(v.spo2_pct < l.spo2_min) AS spo2_pct_below,"
        " count_if(v.skin_temp_c < l.skin_min) AS skin_temp_c_below,"
        " count_if(v.skin_temp_c > l.skin_max) AS skin_temp_c_above"
        f" FROM ({events('vitals', f, t, columns)}) v CROSS JOIN limits l"
        f" WHERE {_in_range(start, end, 'v.event_time')}"
        " GROUP BY 1"
    )


def _count(table: str, f: str, t: str, start: str, end: str, name: str) -> str:
    return (
        f"SELECT {HOUR} AS hour_start, count(*) AS {name}"
        f" FROM ({events(table, f, t)}) WHERE {_in_range(start, end)} GROUP BY 1"
    )


def _motion(f: str, t: str, start: str, end: str) -> str:
    return (
        f'SELECT {HOUR} AS hour_start, count_if("motion") AS motion_events'
        f" FROM ({events('motion', f, t, ('motion',))})"
        f" WHERE {_in_range(start, end)} GROUP BY 1"
    )


def _episodes(f: str, t: str, start: str, end: str) -> str:
    metrics = ", ".join(sql._quote(m) for m in sql.VITALS_ALERT_METRICS)
    read = (
        f"SELECT * FROM ({events('vitals_alert', f, t, ('metric',))})"
        f' WHERE event_time < {end} AND "metric" IN ({metrics})'
    )
    lagged = (
        "SELECT event_time, lag(event_time) OVER"
        ' (PARTITION BY device_id, "metric" ORDER BY event_time) AS previous_time'
        f" FROM ({read})"
    )
    return (
        f"SELECT {HOUR} AS hour_start, count(*) AS vitals_alert_episodes"
        f" FROM ({lagged}) WHERE {_in_range(start, end)}"
        " AND (previous_time IS NULL"
        f" OR date_diff('millisecond', previous_time, event_time) > {sql.EPISODE_GAP_MS})"
        " GROUP BY 1"
    )


def _bed_minutes(f: str, t: str, start: str, end: str) -> str:
    """Occupied minutes per hour. The state at the range start is the newest event
    before it (moved to the start); spans then run event to event and are cut at each
    hour. An hour no span reaches has no row, so its bed_minutes is null (unknown)."""
    read = (
        f"SELECT * FROM ({events('bed', f, t, ('occupied',))}) WHERE event_time < {end}"
    )
    carried = (
        f"SELECT device_id, {start} AS point_time, 0 AS point_order,"
        ' "occupied" FROM (SELECT device_id, "occupied",'
        " row_number() OVER (PARTITION BY device_id"
        ' ORDER BY event_time DESC, "occupied" DESC) AS recency'
        f" FROM ({read}) WHERE event_time < {start}) WHERE recency = 1"
    )
    in_range = (
        "SELECT device_id, event_time AS point_time, 1 AS point_order,"
        f' "occupied" FROM ({read}) WHERE event_time >= {start}'
    )
    spans = (
        'SELECT "occupied", point_time,'
        " COALESCE(lead(point_time) OVER (PARTITION BY device_id"
        f' ORDER BY point_time, point_order, "occupied"), {end}) AS next_time'
        f" FROM ({carried} UNION ALL {in_range})"
    )
    return (
        'SELECT h.hour_start, CAST(round(sum(CASE WHEN s."occupied"'
        " THEN date_diff('millisecond', greatest(s.point_time, h.hour_start),"
        " least(s.next_time, h.hour_end)) ELSE 0 END) / 60000.0) AS integer)"
        f" AS bed_minutes FROM ({spans}) s JOIN hours h"
        " ON s.point_time < h.hour_end AND s.next_time > h.hour_start"
        " GROUP BY h.hour_start"
    )


def hourly_query(
    resident_id: str,
    devices: Iterable[Device],
    limits: Mapping[str, Limits],
    from_date: str,
    to_date: str,
) -> str:
    """One row per Singapore hour of the range, oldest first (criteria 10-13)."""
    devices = [d for d in devices if d.resident_id == resident_id]
    window_start, window_end = window(from_date, to_date)
    start, end = sql._timestamp(window_start), sql._timestamp(window_end)
    f, t = from_date, to_date
    stats = ", ".join(f"vs.{c}" for c in sql._STAT_COLUMNS)
    counts = ", ".join(f"COALESCE(vs.{c}, 0) AS {c}" for c in sql._COUNT_COLUMNS)
    return (
        f"WITH devices AS ({sql.devices_values(devices)}),"
        f" limits AS ({sql.limits_values({resident_id: limits})}),"
        " hours AS (SELECT h AS hour_start, h + INTERVAL '1' HOUR AS hour_end"
        f" FROM UNNEST(sequence({start}, {end} - INTERVAL '1' HOUR,"
        " INTERVAL '1' HOUR)) AS t (h)),"
        f" vitals_stats AS ({_vitals_stats(f, t, start, end)}),"
        f" bed_hour AS ({_bed_minutes(f, t, start, end)}),"
        f" motion_hour AS ({_motion(f, t, start, end)}),"
        f" falls_hour AS ({_count('fall', f, t, start, end, 'falls')}),"
        f" episodes_hour AS ({_episodes(f, t, start, end)}),"
        f" cancels_hour AS ({_count('alert_cancel', f, t, start, end, 'cancels')})"
        " SELECT date_format(h.hour_start, '%Y-%m-%d %H:%i:%s') AS hour_utc,"
        f" {stats}, {counts},"
        " b.bed_minutes,"
        " COALESCE(m.motion_events, 0) AS motion_events,"
        " COALESCE(fa.falls, 0) AS falls,"
        " COALESCE(ep.vitals_alert_episodes, 0) AS vitals_alert_episodes,"
        " COALESCE(c.cancels, 0) AS cancels"
        " FROM hours h"
        " LEFT JOIN vitals_stats vs ON vs.hour_start = h.hour_start"
        " LEFT JOIN bed_hour b ON b.hour_start = h.hour_start"
        " LEFT JOIN motion_hour m ON m.hour_start = h.hour_start"
        " LEFT JOIN falls_hour fa ON fa.hour_start = h.hour_start"
        " LEFT JOIN episodes_hour ep ON ep.hour_start = h.hour_start"
        " LEFT JOIN cancels_hour c ON c.hour_start = h.hour_start"
        " ORDER BY h.hour_start"
    )
