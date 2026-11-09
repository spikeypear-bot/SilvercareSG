"""Athena SQL for the daily summaries (docs/features/curated-layer.md, criteria 9-14).
Pure strings, no AWS.

One query summarises one Singapore date D for every resident with an assigned device.
The device -> resident mapping and each resident's resolved limits are inline `VALUES`
lists (`devices`, `limits`), so no mapping file is stored. Only validated IDs,
timestamps, and numbers reach the text: each is checked again here before it is
quoted, and anything else raises.

Rules the query encodes:

- A reading's time is `ts`, or `received_at` when `clock_skew` is true. It belongs to D
  when that time is in [D-1 16:00 UTC, D 16:00 UTC), i.e. D 00:00-24:00 SGT.
- It reads clean partitions D-1 and D, plus D-2 for `bed` and `vitals_alert` (the
  look-back for bed_minutes and episodes). Clean partitions are UTC dates of
  `received_at`, so these cover the window.
- A reading is used only if its time is at or after its device's `assigned_at`, and
  only from devices in the mapping. Within the read, the earliest-received copy of a
  `message_id` is kept (clean removes duplicates per date only).
- Vitals, falls, vitals alerts, and cancels come from the resident's wearables;
  `bed` and `motion` from their bedroom sensors.
- Averages ignore nulls (SQL `avg`); counts below / above use strict comparisons, and a
  null reading compares as unknown, so it is never counted.
- bed_minutes: the state at 00:00 SGT is the last `bed` event before it in the read;
  with none, minutes count from the day's first event. Null when the resident has no
  bedroom sensor or no `bed` event in the read (no row from `bed_day`). Rounded to the
  nearest minute.
- vitals_alert_episodes: per wearable and metric, an alert starts an episode when the
  previous one came more than 10 minutes (600,000 ms) earlier, or there was none.
  Only starts inside D count, so an episode crossing midnight counts on its start day.

Every resident in the mapping gets a row, even with no readings (counts 0, statistics
null).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from registry import BEDROOM, WEARABLE, Device, is_device_id, is_resident_id
from thresholds import Limits

# Athena's limit on one query string, not adjustable (criterion 9).
ATHENA_MAX_QUERY_BYTES = 262_144

SUMMARY_TABLE = "daily_summaries"
SGT_OFFSET = timedelta(hours=8)
EPISODE_GAP_MS = 600_000
VITALS_ALERT_METRICS = ("hr_bpm", "spo2_pct")

# The clean partitions each table is read from, as days before D (criterion 10).
READ_DAYS: dict[str, tuple[int, ...]] = {
    "vitals": (1, 0),
    "fall": (1, 0),
    "alert_cancel": (1, 0),
    "motion": (1, 0),
    "bed": (2, 1, 0),
    "vitals_alert": (2, 1, 0),
}
DEVICE_TYPE: dict[str, str] = {
    "vitals": WEARABLE,
    "fall": WEARABLE,
    "alert_cancel": WEARABLE,
    "vitals_alert": WEARABLE,
    "motion": BEDROOM,
    "bed": BEDROOM,
}

# Output columns in order, with how handler.py reads each (Athena returns text).
RESULT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("resident_id", "str"),
    ("hr_bpm_min", "int"),
    ("hr_bpm_avg", "float"),
    ("hr_bpm_max", "int"),
    ("spo2_pct_min", "int"),
    ("spo2_pct_avg", "float"),
    ("spo2_pct_max", "int"),
    ("skin_temp_c_min", "float"),
    ("skin_temp_c_avg", "float"),
    ("skin_temp_c_max", "float"),
    ("activity_pct_avg", "float"),
    ("vitals_readings", "int"),
    ("hr_bpm_below", "int"),
    ("hr_bpm_above", "int"),
    ("spo2_pct_below", "int"),
    ("skin_temp_c_below", "int"),
    ("skin_temp_c_above", "int"),
    ("bed_minutes", "int"),
    ("motion_events", "int"),
    ("falls", "int"),
    ("vitals_alert_episodes", "int"),
    ("cancels", "int"),
)

EVENT_TIME = "CASE WHEN clock_skew THEN received_at ELSE ts END"

_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_NUMBER_RE = re.compile(r"-?[0-9]+(\.[0-9]+)?")


class QueryTooLargeError(Exception):
    """The query text would exceed Athena's 262,144-byte limit."""


# ---- values (validated again before they are quoted) ----


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _checked_date(date: str) -> datetime:
    if not isinstance(date, str) or not _DATE_RE.fullmatch(date):
        raise ValueError("date must be yyyy-mm-dd")
    return datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)


def _timestamp(value: datetime) -> str:
    """A TIMESTAMP literal in UTC, to the millisecond. Any sub-millisecond part is
    rounded up, so `>= assigned_at` never takes a reading from before it."""
    value = value.astimezone(UTC)
    remainder = value.microsecond % 1000
    if remainder:
        value += timedelta(microseconds=1000 - remainder)
    text = value.strftime("%Y-%m-%d %H:%M:%S")
    return f"TIMESTAMP '{text}.{value.microsecond // 1000:03d}'"


def _integer(value: Decimal | None) -> str:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value != value.to_integral_value()
    ):
        raise ValueError("limit must be an integer")
    return str(int(value))


def _double(value: Decimal | None) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("limit must be a number")
    text = format(value, "f")
    if not _NUMBER_RE.fullmatch(text):
        raise ValueError("limit must be a number")
    return f"CAST({_quote(text)} AS double)"


def _device_id(value: str) -> str:
    if not is_device_id(value):
        raise ValueError("invalid device_id")
    return _quote(value)


def _resident_id(value: str) -> str:
    if not is_resident_id(value):
        raise ValueError("invalid resident_id")
    return _quote(value)


def _device_type(value: str) -> str:
    if value not in (WEARABLE, BEDROOM):
        raise ValueError("invalid device_type")
    return _quote(value)


# ---- window and partitions ----


def window(date: str) -> tuple[datetime, datetime]:
    """[D 00:00 SGT, D+1 00:00 SGT) as UTC: [D-1 16:00, D 16:00)."""
    start = _checked_date(date) - SGT_OFFSET
    return start, start + timedelta(days=1)


def partition_dates(date: str, table: str) -> tuple[str, ...]:
    """The clean partitions `table` is read from for Singapore date D, oldest first."""
    day = _checked_date(date)
    return tuple(
        (day - timedelta(days=back)).strftime("%Y-%m-%d") for back in READ_DAYS[table]
    )


def required_partitions(date: str) -> list[tuple[str, str]]:
    """Every (table, date) the query reads (criterion 4)."""
    return [
        (table, partition)
        for table in READ_DAYS
        for partition in partition_dates(date, table)
    ]


# ---- query parts ----


def devices_values(devices: Iterable[Device]) -> str:
    rows = [
        f"({_device_id(d.device_id)}, {_device_type(d.device_type)},"
        f" {_resident_id(d.resident_id)}, {_timestamp(d.assigned_at)})"
        for d in devices
    ]
    if not rows:
        raise ValueError("no devices")
    return (
        f"SELECT * FROM (VALUES {', '.join(rows)})"
        " AS t (device_id, device_type, resident_id, assigned_at)"
    )


def limits_values(limits: Mapping[str, Mapping[str, Limits]]) -> str:
    rows = []
    for resident_id, metrics in sorted(limits.items()):
        hr, spo2, skin = metrics["hr_bpm"], metrics["spo2_pct"], metrics["skin_temp_c"]
        rows.append(
            f"({_resident_id(resident_id)}, {_integer(hr.min)}, {_integer(hr.max)},"
            f" {_integer(spo2.min)}, {_double(skin.min)}, {_double(skin.max)})"
        )
    if not rows:
        raise ValueError("no limits")
    return (
        f"SELECT * FROM (VALUES {', '.join(rows)})"
        " AS t (resident_id, hr_min, hr_max, spo2_min, skin_min, skin_max)"
    )


def events(table: str, date: str, columns: tuple[str, ...] = ()) -> str:
    """Readings of one clean table from mapped devices of the right type, at or after
    the device's assigned_at, with their time (criterion 10). No day window yet: the
    look-back tables need readings before D."""
    dates = ", ".join(_quote(d) for d in partition_dates(date, table))
    inner_columns = "".join(f', "{c}"' for c in columns)
    outer_columns = "".join(f', e."{c}"' for c in columns)
    inner = (
        f"SELECT device_id, {EVENT_TIME} AS event_time{inner_columns},"
        " row_number() OVER (PARTITION BY message_id ORDER BY received_at) AS copy_num"
        f' FROM {table} WHERE "date" IN ({dates})'
    )
    return (
        f"SELECT d.resident_id, d.device_id, e.event_time{outer_columns}"
        f" FROM ({inner}) e JOIN devices d ON e.device_id = d.device_id"
        f" WHERE e.copy_num = 1 AND d.device_type = {_quote(DEVICE_TYPE[table])}"
        " AND e.event_time >= d.assigned_at"
    )


def _in_day(start: str, end: str, column: str = "event_time") -> str:
    return f"{column} >= {start} AND {column} < {end}"


def _vitals_stats(date: str, start: str, end: str) -> str:
    columns = ("hr_bpm", "spo2_pct", "skin_temp_c", "activity_pct")
    return (
        "SELECT v.resident_id,"
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
        f" FROM ({events('vitals', date, columns)}) v"
        " JOIN limits l ON v.resident_id = l.resident_id"
        f" WHERE {_in_day(start, end, 'v.event_time')}"
        " GROUP BY v.resident_id"
    )


def _count(table: str, date: str, start: str, end: str, name: str) -> str:
    return (
        f"SELECT resident_id, count(*) AS {name}"
        f" FROM ({events(table, date)}) WHERE {_in_day(start, end)}"
        " GROUP BY resident_id"
    )


def _motion(date: str, start: str, end: str) -> str:
    return (
        'SELECT resident_id, count_if("motion") AS motion_events'
        f" FROM ({events('motion', date, ('motion',))}) WHERE {_in_day(start, end)}"
        " GROUP BY resident_id"
    )


def _episodes(date: str, start: str, end: str) -> str:
    metrics = ", ".join(_quote(m) for m in VITALS_ALERT_METRICS)
    read = (
        f"SELECT * FROM ({events('vitals_alert', date, ('metric',))})"
        f' WHERE event_time < {end} AND "metric" IN ({metrics})'
    )
    lagged = (
        "SELECT resident_id, event_time, lag(event_time) OVER"
        ' (PARTITION BY device_id, "metric" ORDER BY event_time) AS previous_time'
        f" FROM ({read})"
    )
    return (
        "SELECT resident_id, count(*) AS vitals_alert_episodes"
        f" FROM ({lagged}) WHERE {_in_day(start, end)}"
        " AND (previous_time IS NULL"
        f" OR date_diff('millisecond', previous_time, event_time) > {EPISODE_GAP_MS})"
        " GROUP BY resident_id"
    )


def _bed_minutes(date: str, start: str, end: str) -> str:
    read = (
        f"SELECT * FROM ({events('bed', date, ('occupied',))}) WHERE event_time < {end}"
    )
    # The state at 00:00 SGT: the newest event before it, moved to 00:00 (order 0).
    carried = (
        f"SELECT resident_id, device_id, {start} AS point_time, 0 AS point_order,"
        ' "occupied" FROM (SELECT resident_id, device_id, "occupied",'
        " row_number() OVER (PARTITION BY device_id"
        ' ORDER BY event_time DESC, "occupied" DESC) AS recency'
        f" FROM ({read}) WHERE event_time < {start}) WHERE recency = 1"
    )
    in_day = (
        "SELECT resident_id, device_id, event_time AS point_time, 1 AS point_order,"
        f' "occupied" FROM ({read}) WHERE event_time >= {start}'
    )
    spans = (
        'SELECT resident_id, "occupied", point_time,'
        " COALESCE(lead(point_time) OVER (PARTITION BY device_id"
        f' ORDER BY point_time, point_order, "occupied"), {end}) AS next_time'
        f" FROM ({carried} UNION ALL {in_day})"
    )
    return (
        'SELECT resident_id, CAST(round(sum(CASE WHEN "occupied"'
        " THEN date_diff('millisecond', point_time, next_time) ELSE 0 END)"
        " / 60000.0) AS integer) AS bed_minutes"
        f" FROM ({spans}) GROUP BY resident_id"
    )


_COUNT_COLUMNS = (
    "vitals_readings",
    "hr_bpm_below",
    "hr_bpm_above",
    "spo2_pct_below",
    "skin_temp_c_below",
    "skin_temp_c_above",
)
_STAT_COLUMNS = (
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
)


def summary_query(
    date: str,
    devices: Iterable[Device],
    limits: Mapping[str, Mapping[str, Limits]],
) -> str:
    """The one query that summarises Singapore date D (criteria 9-14)."""
    window_start, window_end = window(date)
    start, end = _timestamp(window_start), _timestamp(window_end)
    stats = ", ".join(f"vs.{c}" for c in _STAT_COLUMNS)
    counts = ", ".join(f"COALESCE(vs.{c}, 0) AS {c}" for c in _COUNT_COLUMNS)
    return (
        f"WITH devices AS ({devices_values(devices)}),"
        f" limits AS ({limits_values(limits)}),"
        " residents AS (SELECT DISTINCT resident_id FROM devices),"
        f" vitals_stats AS ({_vitals_stats(date, start, end)}),"
        f" bed_day AS ({_bed_minutes(date, start, end)}),"
        f" motion_day AS ({_motion(date, start, end)}),"
        f" falls_day AS ({_count('fall', date, start, end, 'falls')}),"
        f" episodes_day AS ({_episodes(date, start, end)}),"
        f" cancels_day AS ({_count('alert_cancel', date, start, end, 'cancels')})"
        f" SELECT r.resident_id, {stats}, {counts},"
        " b.bed_minutes,"
        " COALESCE(m.motion_events, 0) AS motion_events,"
        " COALESCE(f.falls, 0) AS falls,"
        " COALESCE(ep.vitals_alert_episodes, 0) AS vitals_alert_episodes,"
        " COALESCE(c.cancels, 0) AS cancels"
        " FROM residents r"
        " LEFT JOIN vitals_stats vs ON vs.resident_id = r.resident_id"
        " LEFT JOIN bed_day b ON b.resident_id = r.resident_id"
        " LEFT JOIN motion_day m ON m.resident_id = r.resident_id"
        " LEFT JOIN falls_day f ON f.resident_id = r.resident_id"
        " LEFT JOIN episodes_day ep ON ep.resident_id = r.resident_id"
        " LEFT JOIN cancels_day c ON c.resident_id = r.resident_id"
        " ORDER BY r.resident_id"
    )


def check_size(query: str) -> None:
    """Raise before StartQueryExecution if Athena would refuse the text (criterion 9).
    The message holds the sizes only, never the text."""
    size = len(query.encode("utf-8"))
    if size > ATHENA_MAX_QUERY_BYTES:
        raise QueryTooLargeError(
            f"query is {size} bytes, over Athena's {ATHENA_MAX_QUERY_BYTES}-byte limit:"
            " too many devices to pass inline (docs/features/curated-layer.md, criterion 9)"
        )


def add_partition(date: str) -> str:
    _checked_date(date)
    return (
        f"ALTER TABLE {SUMMARY_TABLE} ADD IF NOT EXISTS"
        f" PARTITION (`date` = {_quote(date)})"
    )


def summary_prefix(date: str) -> str:
    """Key prefix of one Singapore date's summaries, with the trailing slash."""
    _checked_date(date)
    return f"summaries/date={date}/"
