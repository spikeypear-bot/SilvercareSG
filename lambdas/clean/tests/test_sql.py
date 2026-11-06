"""What can be checked of the SQL without Athena. The self-check proves the rest."""

from __future__ import annotations

import re

import pytest
import sql
from helpers import CASES, load_validation

DATE = "2026-10-18"
BUCKET = "silvercare-clean-test"
EIGHT_TABLES = {
    "vitals",
    "vitals_alert",
    "fall",
    "alert_cancel",
    "wearable_status",
    "bedroom_status",
    "motion",
    "bed",
}
# Athena's limit on one query string.
ATHENA_MAX_QUERY_BYTES = 262_144


def test_tables_json_lists_the_eight_clean_tables():
    assert set(sql.TABLES) == EIGHT_TABLES


def test_output_tables_are_the_eight_plus_rejected():
    assert len(sql.OUTPUT_TABLES) == 9
    assert set(sql.OUTPUT_TABLES) == EIGHT_TABLES | {"rejected"}


def test_raw_partitions_cover_all_nine_message_types():
    assert set(sql.RAW_MESSAGES) == {
        "vitals",
        "vitals_alert",
        "fall",
        "alert_cancel",
        "status",
        "motion",
        "bed",
        "unknown",
        "unparseable",
    }


@pytest.mark.parametrize("message", sql.RAW_MESSAGES)
def test_add_raw_partitions_adds_each_message_for_the_date(message):
    expected = f"PARTITION (message = '{message}', `date` = '{DATE}')"
    assert expected in sql.add_raw_partitions(DATE)


def test_add_raw_partitions_does_not_fail_on_existing_ones():
    assert "ADD IF NOT EXISTS" in sql.add_raw_partitions(DATE)


@pytest.mark.parametrize("table", sql.OUTPUT_TABLES)
def test_unload_writes_snappy_parquet_to_the_tables_date_folder(table):
    query = sql.unload(table, DATE, BUCKET)
    assert query.startswith("UNLOAD (")
    assert query.endswith(
        f"TO 's3://{BUCKET}/tables/{table}/date={DATE}/'"
        " WITH (format = 'PARQUET', compression = 'SNAPPY')"
    )


@pytest.mark.parametrize("table", sql.OUTPUT_TABLES)
def test_unload_reads_only_the_given_date(table):
    expected = f"""FROM raw_telemetry WHERE "date" = '{DATE}'"""
    assert expected in sql.unload(table, DATE, BUCKET)


@pytest.mark.parametrize("table", sql.TABLES)
def test_clean_table_reads_only_its_own_and_the_unknown_partition(table):
    raw_message = sql.TABLES[table]["raw_message"]
    assert sql.raw_source(DATE, table).endswith(
        f"AND message IN ('{raw_message}', 'unknown')"
    )


def test_rejected_reads_every_raw_partition():
    assert "AND message" not in sql.raw_source(DATE, sql.REJECTED_TABLE)


@pytest.mark.parametrize("table", sql.OUTPUT_TABLES)
def test_add_output_partition_names_the_table_and_date(table):
    assert sql.add_output_partition(table, DATE) == (
        f"ALTER TABLE {table} ADD IF NOT EXISTS PARTITION (`date` = '{DATE}')"
    )


@pytest.mark.parametrize("table", sql.TABLES)
def test_clean_query_selects_every_column_and_only_its_valid_lines(table):
    query = sql.clean_query(table, "SELECT 1")
    outer = query.split(" FROM (", 1)[0]
    assert outer == "SELECT " + ", ".join(f'"{c}"' for c in sql.columns(table))
    assert f"WHERE kind = '{table}' AND reason IS NULL" in query


@pytest.mark.parametrize("table", sql.TABLES)
def test_clean_columns_start_with_the_envelope(table):
    assert sql.columns(table)[:6] == [
        "message_id",
        "device_id",
        "ts",
        "received_at",
        "schema_version",
        "clock_skew",
    ]


@pytest.mark.parametrize(
    "table,accel", [("vitals", "accel_g"), ("fall", "accel_window")]
)
def test_accel_tables_end_with_the_json_and_its_flag(table, accel):
    assert sql.columns(table)[-2:] == [accel, "accel_valid"]


@pytest.mark.parametrize("table", sorted(EIGHT_TABLES - {"vitals", "fall"}))
def test_other_tables_have_no_accel_columns(table):
    assert "accel_valid" not in sql.columns(table)


@pytest.mark.parametrize("table", sql.TABLES)
def test_duplicates_keep_the_earliest_received_at(table):
    query = sql.clean_query(table, "SELECT 1")
    window = "row_number() OVER (PARTITION BY message_id ORDER BY received_at)"
    assert window in query
    assert query.endswith("WHERE row_num = 1")


def test_rejected_query_keeps_exact_duplicate_lines_once():
    query = sql.rejected_query("SELECT 1")
    assert query.startswith(
        'SELECT min(raw_message) AS "message", reason, line AS raw_line'
    )
    assert query.endswith("WHERE reason IS NOT NULL GROUP BY reason, line")


@pytest.mark.parametrize("message", sql.CONTRACT_MESSAGES)
def test_a_line_under_unknown_can_only_become_a_contract_type(message):
    assert f"""'"{message}"'""" in sql._message_type()


def test_message_type_is_recovered_only_under_the_unknown_partition():
    assert sql._message_type().startswith("CASE WHEN raw_message = 'unknown' AND ")


def test_a_line_must_name_its_message_type_to_be_valid():
    assert """= concat('"', message_type, '"')""" in sql._reason()


@pytest.mark.parametrize(
    "table,accel", [("vitals", "accel_g"), ("fall", "accel_window")]
)
def test_accel_column_is_null_unless_valid(table, accel):
    expected = (
        f"CASE WHEN {sql._accel_valid(accel)} THEN {sql._accel_json(accel)}"
        f" END AS {accel}"
    )
    assert expected in sql.clean_query(table, "SELECT 1")


@pytest.mark.parametrize(
    "table,accel", [("vitals", "accel_g"), ("fall", "accel_window")]
)
def test_accel_object_as_received_is_never_output(table, accel):
    whole = f"json_format(element_at(fields, '{accel}'))"
    assert whole not in sql.clean_query(table, "SELECT 1")


@pytest.mark.parametrize(
    "accel,keys",
    [
        ("accel_g", ["x", "y", "z"]),
        ("accel_window", ["hz", "start_offset_ms", "samples"]),
    ],
)
def test_accel_json_names_only_the_known_keys(accel, keys):
    assert re.findall(r'"(\w+)":', sql._accel_json(accel)) == keys


def test_output_query_routes_rejected_and_clean_tables():
    assert sql.output_query("rejected", "SELECT 1") == sql.rejected_query("SELECT 1")
    assert sql.output_query("bed", "SELECT 1") == sql.clean_query("bed", "SELECT 1")


# ---- SQL in step with ingest ----


def test_every_ingest_message_has_one_clean_table():
    pairs = [(t["device"], t["raw_message"]) for t in sql.TABLES.values()]
    assert sorted(pairs) == sorted(load_validation().STORED_FIELDS)


@pytest.mark.parametrize("table", sql.TABLES)
def test_table_field_rules_match_ingest(table):
    spec = sql.TABLES[table]
    rules = load_validation().STORED_FIELDS[(spec["device"], spec["raw_message"])]
    found = {
        field["name"]: (
            field["kind"],
            field.get("nullable", False),
            field.get("min"),
            field.get("max"),
            field.get("max_len"),
        )
        for field in spec["fields"]
    }
    assert found == {
        name: (rule.kind, rule.nullable, rule.minimum, rule.maximum, rule.max_len)
        for name, rule in rules.items()
    }


def test_constants_match_ingest():
    validation = load_validation()
    assert sql._ACCEL_AXIS_LIMIT == validation.ACCEL_AXIS_LIMIT
    assert sql._FALL_WINDOW_HZ == validation.FALL_WINDOW_HZ
    assert sql._FALL_WINDOW_START_OFFSET_MS == validation.FALL_WINDOW_START_OFFSET_MS
    assert sql._FALL_WINDOW_SAMPLES == validation.FALL_WINDOW_SAMPLES
    assert set(sql._ENVELOPE_AND_RULE_FIELDS) == validation.ENVELOPE_AND_RULE_FIELDS


def test_vitals_alert_limits_match_ingest():
    validation = load_validation()
    checks = " ".join(sql._vitals_alert_checks())
    for metric, rules in validation.VITALS_ALERT_METRICS.items():
        low, high = rules["values"]
        assert f"BETWEEN {low} AND {high}" in checks
        assert f"'\"{metric}\"'" in checks
        for limit in rules["limits"]:
            assert f"'{limit}'" in checks
    for direction in validation.VITALS_ALERT_DIRECTIONS:
        assert f"'\"{direction}\"'" in checks


def test_every_reason_in_the_sql_exists_in_ingest_or_the_spec():
    names = {name for spec in load_validation().STORED_FIELDS.values() for name in spec}
    known = {
        "unknown_message",
        "unparseable",
        "unknown_field",
        "missing:schema_version",
        "missing:message_id",
        "missing:ts",
    }
    for name in (
        "event",
        "device_id",
        "message",
        "schema_version",
        "message_id",
        "ts",
        "received_at",
        "metric",
        "direction",
        "limit",
    ):
        known.add(f"invalid:{name}")
    for name in names:
        known |= {f"missing:{name}", f"wrong_type:{name}", f"out_of_range:{name}"}
    used = set(re.findall(r"THEN '([^']+)'", sql._reason()))
    assert used
    assert used <= known


@pytest.mark.parametrize("table", sql.TABLES)
def test_every_field_of_a_table_has_a_missing_and_wrong_type_reason(table):
    reason_sql = sql._reason()
    for field in sql.TABLES[table]["fields"]:
        assert f"'missing:{field['name']}'" in reason_sql
        assert f"'wrong_type:{field['name']}'" in reason_sql


@pytest.mark.parametrize("pattern", [sql._INT, sql._DEVICE_ID, sql._UUID_V4, sql._TS])
def test_full_value_regexes_cannot_match_before_a_final_newline(pattern):
    assert pattern.startswith(r"\A")
    assert pattern.endswith(r"\z")


# ---- the self-check's inline source ----


def test_cases_source_quotes_a_single_quote_in_a_line():
    source = sql.cases_source(
        [{"line": "it's'); DROP TABLE vitals; --", "raw_message": "vitals"}]
    )
    assert "('it''s''); DROP TABLE vitals; --', 'vitals')" in source


def test_cases_source_holds_every_case():
    source = sql.cases_source(CASES)
    assert source.startswith("SELECT line, raw_message FROM (VALUES ")
    for case in CASES:
        assert sql._quote(case["line"]) in source


@pytest.mark.parametrize("table", sql.OUTPUT_TABLES)
def test_self_check_query_fits_athenas_query_size_limit(table):
    query = sql.output_query(table, sql.cases_source(CASES))
    assert len(query.encode()) < ATHENA_MAX_QUERY_BYTES


@pytest.mark.parametrize("table", sql.OUTPUT_TABLES)
def test_dated_query_fits_athenas_query_size_limit(table):
    assert len(sql.unload(table, DATE, BUCKET).encode()) < ATHENA_MAX_QUERY_BYTES


def test_received_at_is_not_added_to_the_epoch_in_one_step():
    # Real Athena returned NULL for this form while local Trino accepted it, which sent
    # every valid line to rejected.
    query = sql.output_query("vitals", "SELECT 1")
    single_step = re.compile(r"date_add\('millisecond', try_cast\([^%]*? AS bigint\),")
    assert not single_step.search(query)
    assert "date_add('day', try_cast(element_at(tokens, 'received_at')" in query
