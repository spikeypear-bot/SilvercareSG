"""What can be checked of the summary SQL without Athena, as strings
(docs/features/curated-layer.md, criteria 9-14 and 18).

Where a rule is a comparison (the day window, assigned_at, threshold counts, the episode
gap), the operator and bound are read out of the generated text and applied in Python to
values at, just below, and just above each boundary, so the tests check what the SQL
says rather than a copy of it. The full query has not been run here: the owner must run
it on real Athena before merging (Athena's older engine can differ from local Trino).
"""

from __future__ import annotations

import operator
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import sql
from helpers import CLEAN_TABLES, DATE, PARTITIONS_FOR_DATE
from registry import Device
from thresholds import DEFAULTS, Limits

OPS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
START = "TIMESTAMP '2026-10-17 16:00:00.000'"
END = "TIMESTAMP '2026-10-18 16:00:00.000'"
ASSIGNED = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
SGT = timezone(timedelta(hours=8))


def device(device_id: str = "wearable-0001", resident_id: str = "res-0001", **kw):
    return Device(
        device_id=device_id,
        device_type=kw.get("device_type", device_id.split("-", 1)[0]),
        resident_id=resident_id,
        assigned_at=kw.get("assigned_at", ASSIGNED),
    )


DEVICES = [
    device("wearable-0001", "res-0001"),
    device("bedroom-0001", "res-0001"),
    device("wearable-0002", "res-0002"),
]
LIMITS = {"res-0001": dict(DEFAULTS), "res-0002": dict(DEFAULTS)}


@pytest.fixture(scope="module")
def query() -> str:
    return sql.summary_query(DATE, DEVICES, LIMITS)


def parse_ts(literal: str) -> datetime:
    text = re.fullmatch(r"TIMESTAMP '([^']+)'", literal).group(1)
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)


def split_top_level(text: str) -> list[str]:
    """Split a SELECT list on commas outside parentheses."""
    parts, depth, current = [], 0, ""
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    parts.append(current.strip())
    return parts


# ---- criterion 10: the Singapore day and the partitions read ----


def test_window_is_the_singapore_day_in_utc():
    assert sql.window(DATE) == (
        datetime(2026, 10, 17, 16, 0, tzinfo=UTC),
        datetime(2026, 10, 18, 16, 0, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "date,start",
    [
        ("2026-01-01", datetime(2025, 12, 31, 16, 0, tzinfo=UTC)),
        ("2028-03-01", datetime(2028, 2, 29, 16, 0, tzinfo=UTC)),
        ("2026-11-01", datetime(2026, 10, 31, 16, 0, tzinfo=UTC)),
    ],
)
def test_window_crosses_year_leap_day_and_month(date, start):
    assert sql.window(date) == (start, start + timedelta(days=1))


def test_day_bounds_reach_the_query_as_utc_timestamps(query):
    assert START in query
    assert END in query


# Groups: column, lower operator, lower bound, upper operator, upper bound.
DAY_FILTER = re.compile(
    r"(\w+(?:\.\w+)?) (>=|>) (TIMESTAMP '[^']+') AND \1 (<|<=) (TIMESTAMP '[^']+')"
)


def test_every_day_filter_is_start_inclusive_end_exclusive(query):
    filters = [(m[2], m[3], m[4], m[5]) for m in DAY_FILTER.finditer(query)]
    # vitals, motion, falls, cancels, episodes
    assert len(filters) == 5
    assert set(filters) == {(">=", START, "<", END)}


@pytest.mark.parametrize(
    "event_time,in_day",
    [
        (datetime(2026, 10, 17, 15, 59, 59, 999000, tzinfo=UTC), False),
        (datetime(2026, 10, 17, 16, 0, 0, tzinfo=UTC), True),
        (datetime(2026, 10, 17, 16, 0, 0, 1000, tzinfo=UTC), True),
        (datetime(2026, 10, 18, 15, 59, 59, 999000, tzinfo=UTC), True),
        (datetime(2026, 10, 18, 16, 0, 0, tzinfo=UTC), False),
    ],
)
def test_day_filter_boundaries(query, event_time, in_day):
    match = DAY_FILTER.search(query)
    low = OPS[match[2]](event_time, parse_ts(match[3]))
    high = OPS[match[4]](event_time, parse_ts(match[5]))
    assert (low and high) is in_day


def test_reading_time_is_received_at_when_the_clock_is_skewed():
    assert sql.EVENT_TIME == "CASE WHEN clock_skew THEN received_at ELSE ts END"


@pytest.mark.parametrize("table", CLEAN_TABLES)
def test_every_table_uses_the_reading_time(table):
    assert f"{sql.EVENT_TIME} AS event_time" in sql.events(table, DATE)


def test_day_filters_use_the_reading_time_not_ts(query):
    assert not re.search(r"\bts (>=|<)", query)
    assert not re.search(r"\breceived_at (>=|<)", query)


def test_required_partitions_are_d_minus_1_and_d_plus_d_minus_2_for_lookbacks():
    partitions = sql.required_partitions(DATE)
    assert set(partitions) == PARTITIONS_FOR_DATE
    assert len(partitions) == len(PARTITIONS_FOR_DATE)


@pytest.mark.parametrize(
    "table,dates",
    [
        ("vitals", ("2026-10-31", "2026-11-01")),
        ("fall", ("2026-10-31", "2026-11-01")),
        ("alert_cancel", ("2026-10-31", "2026-11-01")),
        ("motion", ("2026-10-31", "2026-11-01")),
        ("bed", ("2026-10-30", "2026-10-31", "2026-11-01")),
        ("vitals_alert", ("2026-10-30", "2026-10-31", "2026-11-01")),
    ],
)
def test_partition_dates_cross_a_month(table, dates):
    assert sql.partition_dates("2026-11-01", table) == dates


@pytest.mark.parametrize("table", CLEAN_TABLES)
def test_each_table_reads_exactly_its_partitions(table):
    dates = ", ".join(f"'{d}'" for d in sql.partition_dates(DATE, table))
    assert f'FROM {table} WHERE "date" IN ({dates})' in sql.events(table, DATE)


def test_query_reads_only_the_six_clean_tables(query):
    read = set(re.findall(r'FROM (\w+) WHERE "date" IN', query))
    assert read == set(CLEAN_TABLES)
    assert "raw_telemetry" not in query
    assert "rejected" not in query
    assert "status" not in query


# ---- criterion 9: the mapping inline, assigned_at, unassigned devices ----


def test_devices_are_an_inline_values_list():
    assert sql.devices_values([device()]) == (
        "SELECT * FROM (VALUES ('wearable-0001', 'wearable', 'res-0001',"
        " TIMESTAMP '2026-10-01 03:00:00.000'))"
        " AS t (device_id, device_type, resident_id, assigned_at)"
    )


def test_limits_are_an_inline_values_list():
    limits = {
        "res-0002": {
            "hr_bpm": Limits(Decimal(45), Decimal(110)),
            "spo2_pct": Limits(Decimal(88), None),
            "skin_temp_c": Limits(Decimal("31.5"), Decimal("37.5")),
        },
        "res-0001": dict(DEFAULTS),
    }
    assert sql.limits_values(limits) == (
        "SELECT * FROM (VALUES"
        " ('res-0001', 50, 120, 92, CAST('30.0' AS double), CAST('38.0' AS double)),"
        " ('res-0002', 45, 110, 88, CAST('31.5' AS double), CAST('37.5' AS double)))"
        " AS t (resident_id, hr_min, hr_max, spo2_min, skin_min, skin_max)"
    )


def test_query_starts_with_the_mapping_and_limits(query):
    assert query.startswith(f"WITH devices AS ({sql.devices_values(DEVICES)}),")
    assert f" limits AS ({sql.limits_values(LIMITS)})," in query


@pytest.mark.parametrize(
    "bad",
    [
        device("wearable-0001'; DROP TABLE vitals --"),
        device("sensor-0001"),
        device(resident_id="res-0001' OR '1'='1"),
        device(resident_id="Test Resident A"),
        device(device_type="kitchen"),
    ],
    ids=["device-quote", "device-prefix", "resident-quote", "resident-name", "type"],
)
def test_unvalidated_device_never_reaches_the_sql(bad):
    with pytest.raises(ValueError) as error:
        sql.devices_values([bad])
    assert bad.device_id not in str(error.value)
    assert bad.resident_id not in str(error.value)


@pytest.mark.parametrize(
    "metric,limits",
    [
        ("hr_bpm", Limits(Decimal("50.5"), Decimal(120))),
        ("hr_bpm", Limits(Decimal(50), None)),
        ("spo2_pct", Limits(None, None)),
        ("skin_temp_c", Limits(Decimal("NaN"), Decimal(38))),
        ("skin_temp_c", Limits(Decimal(30), Decimal("Infinity"))),
        ("skin_temp_c", Limits(30.0, Decimal(38))),
    ],
)
def test_unvalidated_limit_never_reaches_the_sql(metric, limits):
    resident = {**DEFAULTS, metric: limits}
    with pytest.raises(ValueError):
        sql.limits_values({"res-0001": resident})


def test_invalid_resident_id_in_limits_never_reaches_the_sql():
    with pytest.raises(ValueError):
        sql.limits_values({"res-0001'--": dict(DEFAULTS)})


def test_no_devices_or_limits_raise():
    with pytest.raises(ValueError):
        sql.devices_values([])
    with pytest.raises(ValueError):
        sql.limits_values({})


@pytest.mark.parametrize(
    "assigned_at,literal",
    [
        (datetime(2026, 10, 1, 3, 0, tzinfo=UTC), "2026-10-01 03:00:00.000"),
        (datetime(2026, 10, 1, 3, 0, 0, 1000, tzinfo=UTC), "2026-10-01 03:00:00.001"),
        (datetime(2026, 10, 1, 3, 0, 0, 1, tzinfo=UTC), "2026-10-01 03:00:00.001"),
        (datetime(2026, 10, 1, 3, 0, 0, 123456, tzinfo=UTC), "2026-10-01 03:00:00.124"),
        (datetime(2026, 10, 1, 3, 0, 0, 999001, tzinfo=UTC), "2026-10-01 03:00:01.000"),
        (datetime(2026, 10, 1, 11, 0, tzinfo=SGT), "2026-10-01 03:00:00.000"),
    ],
)
def test_assigned_at_is_a_utc_millisecond_timestamp_rounded_up(assigned_at, literal):
    # Rounded up, so `>= assigned_at` never takes a reading from before it.
    assert sql._timestamp(assigned_at) == f"TIMESTAMP '{literal}'"


ASSIGNED_FILTER = re.compile(r"e\.event_time (>=|>) d\.assigned_at")


@pytest.mark.parametrize("table", CLEAN_TABLES)
def test_every_table_keeps_only_readings_at_or_after_assigned_at(table):
    assert ASSIGNED_FILTER.search(sql.events(table, DATE))


@pytest.mark.parametrize(
    "offset_ms,used",
    [(-1, False), (0, True), (1, True)],
)
def test_assigned_at_boundary(offset_ms, used):
    op = OPS[ASSIGNED_FILTER.search(sql.events("vitals", DATE))[1]]
    assert op(ASSIGNED + timedelta(milliseconds=offset_ms), ASSIGNED) is used


@pytest.mark.parametrize("table", CLEAN_TABLES)
def test_only_mapped_devices_are_read(table):
    # An inner join on the mapping: a device not in it (spare, invalid) adds nothing.
    text = sql.events(table, DATE)
    assert ") e JOIN devices d ON e.device_id = d.device_id" in text
    assert "LEFT JOIN devices" not in text


@pytest.mark.parametrize("table", CLEAN_TABLES)
def test_duplicate_messages_keep_the_earliest_received_copy(table):
    text = sql.events(table, DATE)
    assert (
        "row_number() OVER (PARTITION BY message_id ORDER BY received_at) AS copy_num"
        in text
    )
    assert "WHERE e.copy_num = 1" in text


# ---- criterion 9: the size guard ----


def test_query_at_the_limit_passes_and_one_byte_more_fails():
    sql.check_size("S" * sql.ATHENA_MAX_QUERY_BYTES)
    with pytest.raises(sql.QueryTooLargeError):
        sql.check_size("S" * (sql.ATHENA_MAX_QUERY_BYTES + 1))


def test_limit_is_athenas_262144_bytes():
    assert sql.ATHENA_MAX_QUERY_BYTES == 262_144


def test_size_is_counted_in_utf8_bytes():
    # 131,073 two-byte characters: under the limit in characters, over it in bytes.
    with pytest.raises(sql.QueryTooLargeError):
        sql.check_size("\u00e9" * (sql.ATHENA_MAX_QUERY_BYTES // 2 + 1))


def many(count: int) -> tuple[list[Device], dict]:
    """`count` residents, each with a wearable and a bedroom sensor."""
    devices, limits = [], {}
    for number in range(1, count + 1):
        resident = f"res-{number:04d}"
        devices.append(device(f"wearable-{number:04d}", resident))
        devices.append(device(f"bedroom-{number:04d}", resident))
        limits[resident] = dict(DEFAULTS)
    return devices, limits


def test_a_thousand_devices_fit():
    devices, limits = many(500)
    sql.check_size(sql.summary_query(DATE, devices, limits))


def test_eight_thousand_devices_do_not_fit_and_the_error_has_no_query_text():
    devices, limits = many(4000)
    text = sql.summary_query(DATE, devices, limits)
    with pytest.raises(sql.QueryTooLargeError) as error:
        sql.check_size(text)
    message = str(error.value)
    assert str(len(text.encode("utf-8"))) in message
    for fragment in ("SELECT", "VALUES", "res-0001", "wearable-0001", "TIMESTAMP"):
        assert fragment not in message


# ---- criteria 11, 12: one row per resident, statistics, strict counts ----


def test_every_mapped_resident_gets_a_row(query):
    assert " residents AS (SELECT DISTINCT resident_id FROM devices)," in query
    tail = query[query.rindex(" FROM residents r") :]
    for cte, alias in [
        ("vitals_stats", "vs"),
        ("bed_day", "b"),
        ("motion_day", "m"),
        ("falls_day", "f"),
        ("episodes_day", "ep"),
        ("cancels_day", "c"),
    ]:
        join = f" LEFT JOIN {cte} {alias} ON {alias}.resident_id = r.resident_id"
        assert join in tail
    assert tail.endswith(" ORDER BY r.resident_id")


def final_select(query: str) -> list[str]:
    final = query[query.rindex(" SELECT r.resident_id") + len(" SELECT ") :]
    return split_top_level(final[: final.index(" FROM residents r")])


def output_name(item: str) -> str:
    """The column name of one SELECT item: its alias, else the column it names."""
    return item.rsplit(" AS ", 1)[1] if " AS " in item else item.split(".")[1]


def test_result_columns_are_in_criterion_11_order(query):
    names = [output_name(item) for item in final_select(query)]
    assert names == [name for name, _ in sql.RESULT_COLUMNS]
    assert names == [
        "resident_id",
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
        "bed_minutes",
        "motion_events",
        "falls",
        "vitals_alert_episodes",
        "cancels",
    ]


def test_counts_are_zero_and_statistics_null_without_readings(query):
    items = final_select(query)
    counts = {
        "vitals_readings",
        "hr_bpm_below",
        "hr_bpm_above",
        "spo2_pct_below",
        "skin_temp_c_below",
        "skin_temp_c_above",
        "motion_events",
        "falls",
        "vitals_alert_episodes",
        "cancels",
    }
    for item in items[1:]:
        name = output_name(item)
        if name in counts:
            assert item.startswith("COALESCE(") and item.endswith(f", 0) AS {name}")
        else:
            assert "COALESCE" not in item, name  # statistics and bed_minutes stay null


def test_statistics_ignore_nulls_and_round_to_one_decimal(query):
    for column in ("hr_bpm", "spo2_pct", "skin_temp_c", "activity_pct"):
        assert f"round(avg(v.{column}), 1) AS {column}_avg" in query
    assert "min(v.hr_bpm) AS hr_bpm_min" in query
    assert "max(v.spo2_pct) AS spo2_pct_max" in query
    assert "round(min(v.skin_temp_c), 1) AS skin_temp_c_min" in query
    assert "round(max(v.skin_temp_c), 1) AS skin_temp_c_max" in query
    assert "count(*) AS vitals_readings" in query


COUNT_IF = re.compile(r"count_if\(v\.(\w+) (<=|>=|<|>) l\.(\w+)\) AS (\w+)")


def threshold_counts(query: str) -> dict[str, tuple[str, str, str]]:
    return {m[4]: (m[1], m[2], m[3]) for m in COUNT_IF.finditer(query)}


def test_threshold_counts_compare_strictly(query):
    assert threshold_counts(query) == {
        "hr_bpm_below": ("hr_bpm", "<", "hr_min"),
        "hr_bpm_above": ("hr_bpm", ">", "hr_max"),
        "spo2_pct_below": ("spo2_pct", "<", "spo2_min"),
        "skin_temp_c_below": ("skin_temp_c", "<", "skin_min"),
        "skin_temp_c_above": ("skin_temp_c", ">", "skin_max"),
    }
    assert "JOIN limits l ON v.resident_id = l.resident_id" in query


LIMIT_COLUMN = {
    "hr_min": DEFAULTS["hr_bpm"].min,
    "hr_max": DEFAULTS["hr_bpm"].max,
    "spo2_min": DEFAULTS["spo2_pct"].min,
    "skin_min": DEFAULTS["skin_temp_c"].min,
    "skin_max": DEFAULTS["skin_temp_c"].max,
}


@pytest.mark.parametrize(
    "count,value,counted",
    [
        ("hr_bpm_below", 49, True),
        ("hr_bpm_below", 50, False),
        ("hr_bpm_below", 51, False),
        ("hr_bpm_above", 119, False),
        ("hr_bpm_above", 120, False),
        ("hr_bpm_above", 121, True),
        ("spo2_pct_below", 91, True),
        ("spo2_pct_below", 92, False),
        ("spo2_pct_below", 93, False),
        ("skin_temp_c_below", Decimal("29.9"), True),
        ("skin_temp_c_below", Decimal("30.0"), False),
        ("skin_temp_c_below", Decimal("30.1"), False),
        ("skin_temp_c_above", Decimal("37.9"), False),
        ("skin_temp_c_above", Decimal("38.0"), False),
        ("skin_temp_c_above", Decimal("38.1"), True),
    ],
)
def test_reading_is_counted_only_strictly_past_a_limit(query, count, value, counted):
    _, op, limit = threshold_counts(query)[count]
    assert OPS[op](value, LIMIT_COLUMN[limit]) is counted


def test_null_reading_is_never_counted(query):
    # count_if(x < y) is false-or-null for a null x, so it isn't counted; nothing in
    # the counts turns a null reading into a value.
    for column in ("hr_bpm", "spo2_pct", "skin_temp_c"):
        assert f"COALESCE(v.{column}" not in query


@pytest.mark.parametrize(
    "table,device_type",
    [
        ("vitals", "wearable"),
        ("fall", "wearable"),
        ("vitals_alert", "wearable"),
        ("alert_cancel", "wearable"),
        ("bed", "bedroom"),
        ("motion", "bedroom"),
    ],
)
def test_each_table_reads_only_its_device_type(table, device_type):
    assert f"d.device_type = '{device_type}'" in sql.events(table, DATE)


def test_motion_events_count_only_motion_true(query):
    assert 'count_if("motion") AS motion_events' in query


def test_falls_and_cancels_count_their_messages(query):
    assert sql._count("fall", DATE, START, END, "falls").startswith(
        "SELECT resident_id, count(*) AS falls"
    )
    assert f"falls_day AS ({sql._count('fall', DATE, START, END, 'falls')})" in query
    cancels = sql._count("alert_cancel", DATE, START, END, "cancels")
    assert f"cancels_day AS ({cancels})" in query


# ---- criterion 13: bed_minutes ----


@pytest.fixture(scope="module")
def bed() -> str:
    return sql._bed_minutes(DATE, START, END)


def test_bed_reads_back_to_d_minus_2_and_stops_at_the_day_end(bed):
    assert "\"date\" IN ('2026-10-16', '2026-10-17', '2026-10-18')" in bed
    assert f"WHERE event_time < {END}" in bed


def test_bed_carries_the_last_state_before_the_day_to_its_start(bed):
    carried = (
        f"SELECT resident_id, device_id, {START} AS point_time, 0 AS point_order,"
        ' "occupied" FROM (SELECT resident_id, device_id, "occupied",'
        " row_number() OVER (PARTITION BY device_id"
        ' ORDER BY event_time DESC, "occupied" DESC) AS recency'
    )
    assert carried in bed
    assert f"WHERE event_time < {START}) WHERE recency = 1" in bed


def test_bed_events_in_the_day_follow_the_carried_state(bed):
    assert (
        "SELECT resident_id, device_id, event_time AS point_time, 1 AS point_order,"
        ' "occupied" FROM ('
    ) in bed
    assert f"WHERE event_time >= {START}" in bed


def test_bed_spans_run_to_the_next_point_or_the_day_end(bed):
    assert (
        "COALESCE(lead(point_time) OVER (PARTITION BY device_id"
        f' ORDER BY point_time, point_order, "occupied"), {END}) AS next_time'
    ) in bed
    assert " UNION ALL " in bed


def test_bed_minutes_sum_occupied_spans_rounded_to_minutes(bed):
    assert bed.startswith(
        'SELECT resident_id, CAST(round(sum(CASE WHEN "occupied"'
        " THEN date_diff('millisecond', point_time, next_time) ELSE 0 END)"
        " / 60000.0) AS integer) AS bed_minutes"
    )
    assert bed.endswith(" GROUP BY resident_id")


def test_bed_minutes_is_null_without_a_bed_row(query):
    assert " b.bed_minutes," in query
    assert "COALESCE(b.bed_minutes" not in query


def test_with_no_earlier_event_minutes_count_from_the_first_event(bed):
    # Without a carried row the earliest point is the day's first event: nothing is
    # added from 00:00 to it (no row starts at the day start except a carried one).
    assert bed.count(f"{START} AS point_time") == 1


# ---- criterion 14: vitals alert episodes ----


@pytest.fixture(scope="module")
def episodes() -> str:
    return sql._episodes(DATE, START, END)


GAP = re.compile(r"date_diff\('millisecond', previous_time, event_time\) (>=|>) (\d+)")


def test_episodes_lag_per_wearable_and_metric(episodes):
    assert (
        'lag(event_time) OVER (PARTITION BY device_id, "metric" ORDER BY event_time)'
        " AS previous_time"
    ) in episodes
    assert "\"metric\" IN ('hr_bpm', 'spo2_pct')" in episodes


def test_episodes_read_back_to_d_minus_2_and_stop_at_the_day_end(episodes):
    assert "\"date\" IN ('2026-10-16', '2026-10-17', '2026-10-18')" in episodes
    assert f"WHERE event_time < {END} AND" in episodes


def test_only_episode_starts_inside_the_day_count(episodes):
    # The day filter is applied after lag(), so an alert's previous one can be from
    # the day before; the start bound appears only in that outer filter.
    assert episodes.count(START) == 1
    assert episodes.endswith(
        f" WHERE event_time >= {START} AND event_time < {END}"
        " AND (previous_time IS NULL"
        " OR date_diff('millisecond', previous_time, event_time) > 600000)"
        " GROUP BY resident_id"
    )
    assert episodes.index(START) > episodes.index("lag(event_time)")


def episode_starts(alerts: list[datetime], sql_text: str) -> list[datetime]:
    """Which alerts start an episode on the day, by the rule in `sql_text`: the
    operator and gap come from the SQL, the day bounds from its outer filter."""
    gap_op, gap_ms = GAP.search(sql_text).groups()
    day = DAY_FILTER.search(sql_text)
    low, high = parse_ts(day[3]), parse_ts(day[5])
    starts, previous = [], None
    for alert in sorted(alerts):
        if previous is None:
            is_start = True
        else:
            gap = (alert - previous) // timedelta(milliseconds=1)
            is_start = OPS[gap_op](gap, int(gap_ms))
        in_day = OPS[day[2]](alert, low) and OPS[day[4]](alert, high)
        if is_start and in_day:
            starts.append(alert)
        previous = alert
    return starts


def at(hour: int, minute: int, second: int = 0, day: int = 18) -> datetime:
    return datetime(2026, 10, day, hour, minute, second, tzinfo=SGT).astimezone(UTC)


@pytest.mark.parametrize(
    "alerts,count",
    [
        ([at(9, 0)], 1),
        ([at(9, 0), at(9, 10)], 1),  # exactly 10 minutes: the same episode
        ([at(9, 0), at(9, 10, 1)], 2),  # 10 min 1 s: a new episode
        ([at(9, 0), at(9, 5), at(9, 14), at(9, 23)], 1),  # each gap under 10 min
        ([at(9, 0), at(12, 0), at(15, 0)], 3),
        ([at(23, 55, day=17), at(0, 3)], 0),  # started the day before
        ([at(23, 55), at(0, 3, day=19)], 1),  # crosses midnight, counted on its start
        ([at(23, 49, 59, day=17), at(0, 0)], 1),  # 10 min 1 s across midnight
        ([at(0, 0)], 1),  # 00:00 SGT is in the day
        ([at(0, 0, day=19)], 0),  # 24:00 SGT is not
    ],
)
def test_episode_rule(episodes, alerts, count):
    assert len(episode_starts(alerts, episodes)) == count


def test_episode_gap_is_ten_minutes():
    assert sql.EPISODE_GAP_MS == 600_000


def test_episodes_of_both_metrics_are_summed(episodes):
    # One count over the rows of both metrics; lag() keeps them apart.
    assert episodes.startswith("SELECT resident_id, count(*) AS vitals_alert_episodes")


# ---- criterion 18 and the key prefix ----


def test_add_partition_is_add_if_not_exists_on_daily_summaries():
    assert sql.add_partition(DATE) == (
        "ALTER TABLE daily_summaries ADD IF NOT EXISTS"
        " PARTITION (`date` = '2026-10-18')"
    )


def test_summary_prefix_is_the_singapore_date_folder():
    assert sql.summary_prefix(DATE) == "summaries/date=2026-10-18/"


@pytest.mark.parametrize("bad", ["2026-10-18'", "2026-1-18", "", None, "2026-02-30"])
def test_sql_helpers_refuse_a_bad_date(bad):
    for build in (sql.add_partition, sql.summary_prefix, sql.window):
        with pytest.raises((ValueError, TypeError)):
            build(bad)


def test_quotes_are_doubled():
    assert sql._quote("a'b") == "'a''b'"
