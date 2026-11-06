"""The self-check's comparison, and the self-check run (criterion 17)."""

from __future__ import annotations

import json

import handler
import pytest
import self_check
import sql
from helpers import CASES, case_by_id, correct_rows

CLEAN_TABLES = list(sql.TABLES)
REJECTED = sql.REJECTED_TABLE


def clean_case_of(row: dict) -> str:
    return next(
        case["id"]
        for case in CASES
        if case.get("expected_values", {}).get("message_id") == row["message_id"]
    )


def rejected_case_of(row: dict) -> str:
    # reversed: of two exact duplicate cases, self_check reports the later one.
    return next(
        case["id"]
        for case in reversed(CASES)
        if case["line"] == row["raw_line"] and case["raw_message"] == row["message"]
    )


def case_of(table: str, row: dict) -> str:
    return (rejected_case_of if table == REJECTED else clean_case_of)(row)


def failures(table: str, rows: list[dict]) -> list[str]:
    return self_check.failures(table, CASES, rows)


def row_of_case(rows: list[dict], case_id: str) -> dict:
    message_id = case_by_id(case_id)["expected_values"]["message_id"]
    return next(row for row in rows if row["message_id"] == message_id)


@pytest.mark.parametrize("table", sql.OUTPUT_TABLES)
def test_correct_rows_pass(table):
    assert failures(table, correct_rows(table)) == []


@pytest.mark.parametrize("table", sql.OUTPUT_TABLES)
def test_missing_row_is_detected(table):
    rows = correct_rows(table)
    assert failures(table, rows[1:]) == [case_of(table, rows[0])]


@pytest.mark.parametrize("table", sql.OUTPUT_TABLES)
def test_duplicated_row_is_detected(table):
    rows = correct_rows(table)
    assert failures(table, [*rows, rows[0]]) == [case_of(table, rows[0])]


@pytest.mark.parametrize("message_id", ["extra-test-row", None])
@pytest.mark.parametrize("table", CLEAN_TABLES)
def test_extra_clean_row_is_detected(table, message_id):
    rows = correct_rows(table)
    extra = {**rows[0], "message_id": message_id}
    assert failures(table, [*rows, extra]) == [f"{table}:unexpected_rows"]


def test_extra_rejected_row_is_detected():
    extra = {"message": "vitals", "reason": "invalid:ts", "raw_line": "synthetic"}
    assert failures(REJECTED, [*correct_rows(REJECTED), extra]) == [
        f"{REJECTED}:unexpected_rows"
    ]


@pytest.mark.parametrize(
    "column,wrong",
    [
        ("reason", "synthetic:reason"),
        ("message", "synthetic-partition"),
        ("raw_line", "synthetic line"),
    ],
)
def test_wrong_rejected_row_is_detected(column, wrong):
    rows = correct_rows(REJECTED)
    changed = {**rows[0], column: wrong}
    assert failures(REJECTED, [changed, *rows[1:]]) == [
        rejected_case_of(rows[0]),
        f"{REJECTED}:unexpected_rows",
    ]


@pytest.mark.parametrize(
    "table,column",
    [
        (table, column)
        for table in CLEAN_TABLES
        for column in sql.columns(table)
        if column != "message_id"
    ],
)
def test_wrong_value_in_any_column_is_detected(table, column):
    rows = correct_rows(table)
    # A row that holds a value, so that NULL can stand for the wrong one.
    row = next((r for r in rows if r[column] is not None), rows[0])
    case_id = clean_case_of(row)
    row[column] = None if row[column] is not None else "1"
    assert failures(table, rows) == [case_id]


@pytest.mark.parametrize("table", CLEAN_TABLES)
def test_wrong_message_id_is_detected(table):
    rows = correct_rows(table)
    case_id = clean_case_of(rows[0])
    rows[0]["message_id"] = "wrong-test-id"
    assert failures(table, rows) == [case_id, f"{table}:unexpected_rows"]


@pytest.mark.parametrize(
    "column,wrong",
    [
        ("hr_bpm", "73"),
        ("skin_temp_c", "33.5"),
        ("clock_skew", "true"),
        ("accel_valid", "false"),
        ("accel_g", '{"x":0,"y":0,"z":0}'),
        ("ts", "2026-10-18 03:12:00.001"),
    ],
)
def test_near_miss_value_is_detected(column, wrong):
    rows = correct_rows("vitals")
    row_of_case(rows, "vitals_valid")[column] = wrong
    assert failures("vitals", rows) == ["vitals_valid"]


def test_equal_number_in_another_form_passes():
    rows = correct_rows("vitals")
    row_of_case(rows, "vitals_valid")["hr_bpm"] = "72.0"
    assert failures("vitals", rows) == []


def test_accel_json_in_another_key_order_passes():
    rows = correct_rows("vitals")
    row_of_case(rows, "vitals_valid")["accel_g"] = '{"z":0.99,"y":-0.11,"x":0.02}'
    assert failures("vitals", rows) == []


def test_row_in_the_wrong_clean_table_is_detected():
    moved = correct_rows("motion")[0]
    assert failures("bed", [*correct_rows("bed"), moved]) == ["bed:unexpected_rows"]
    assert failures("motion", correct_rows("motion")[1:]) == [clean_case_of(moved)]


def test_valid_line_in_rejected_is_detected():
    leaked = {
        "message": "vitals",
        "reason": "invalid:ts",
        "raw_line": case_by_id("vitals_valid")["line"],
    }
    assert failures(REJECTED, [*correct_rows(REJECTED), leaked]) == [
        f"{REJECTED}:unexpected_rows"
    ]


def test_later_copy_of_a_duplicate_being_kept_is_detected():
    rows = correct_rows("vitals")
    row_of_case(rows, "duplicate_earliest_copy")["received_at"] = (
        "2026-10-18 03:12:06.000"
    )
    assert failures("vitals", rows) == ["duplicate_earliest_copy"]


def test_both_copies_of_a_duplicate_surviving_is_detected():
    rows = correct_rows("vitals")
    survivor = row_of_case(rows, "duplicate_earliest_copy")
    assert failures("vitals", [*rows, survivor]) == ["duplicate_earliest_copy"]


def test_exact_duplicate_rejected_line_kept_once_passes():
    line = case_by_id("rejected_exact_duplicate_1")["line"]
    rows = correct_rows(REJECTED)
    assert [row["raw_line"] for row in rows].count(line) == 1
    assert failures(REJECTED, rows) == []


def test_both_copies_across_own_and_unknown_partitions_surviving_is_detected():
    case_id = "across_partitions_own_first_earliest"
    rows = correct_rows("vitals_alert")
    survivor = row_of_case(rows, case_id)
    assert failures("vitals_alert", [*rows, survivor]) == [case_id]


def test_rejected_line_in_own_and_unknown_partitions_kept_once_passes():
    line = case_by_id("across_partitions_rejected_kept")["line"]
    rows = correct_rows(REJECTED)
    assert [row["raw_line"] for row in rows].count(line) == 1
    assert failures(REJECTED, rows) == []


def test_rejected_line_kept_once_per_partition_is_detected():
    line = case_by_id("across_partitions_rejected_dropped")["line"]
    rows = correct_rows(REJECTED)
    row = next(row for row in rows if row["raw_line"] == line)
    assert failures(REJECTED, [*rows, {**row, "message": "unknown"}]) == [
        f"{REJECTED}:unexpected_rows"
    ]


def test_recovered_valid_line_in_rejected_is_detected():
    case = case_by_id("recovered_vitals_valid")
    leaked = {
        "message": sql.UNKNOWN_MESSAGE,
        "reason": "unknown_message",
        "raw_line": case["line"],
    }
    assert failures(REJECTED, [*correct_rows(REJECTED), leaked]) == [
        f"{REJECTED}:unexpected_rows"
    ]


def test_recovered_valid_line_missing_from_its_table_is_detected():
    rows = correct_rows("vitals")
    kept = [row for row in rows if row != row_of_case(rows, "recovered_vitals_valid")]
    assert failures("vitals", kept) == ["recovered_vitals_valid"]


@pytest.mark.parametrize(
    "table,column,case_id",
    [
        ("vitals", "accel_g", "accel_g_extra_nested_key"),
        ("fall", "accel_window", "accel_window_extra_nested_key"),
    ],
)
def test_stored_extra_nested_accel_key_is_detected(table, column, case_id):
    rows = correct_rows(table)
    row = row_of_case(rows, case_id)
    row[column] = json.dumps(json.loads(case_by_id(case_id)["line"])[column])
    assert failures(table, rows) == [case_id]


def test_bad_accel_stored_instead_of_null_is_detected():
    case_id = "accel_window_wrong_hz"
    rows = correct_rows("fall")
    row = row_of_case(rows, case_id)
    row["accel_window"] = json.dumps(
        json.loads(case_by_id(case_id)["line"])["accel_window"]
    )
    assert failures("fall", rows) == [case_id]


def test_fall_with_bad_accel_missing_from_the_clean_table_is_detected():
    case_id = "accel_window_wrong_hz"
    rows = correct_rows("fall")
    kept = [row for row in rows if row != row_of_case(rows, case_id)]
    assert failures("fall", kept) == [case_id]


@pytest.mark.parametrize("column", ["hr_bpm", "accel_g"])
def test_garbled_value_stops_the_check_with_an_error(column):
    """Not reported per case, but the job still fails, which criterion 17 requires."""
    rows = correct_rows("vitals")
    row_of_case(rows, "vitals_valid")[column] = "garbled-text"
    with pytest.raises(ValueError):
        failures("vitals", rows)


# ---- the run itself, with a fake Athena ----


def queue_results(athena, wrong_table: str | None = None) -> None:
    athena.results = [
        correct_rows(table)[1:] if table == wrong_table else correct_rows(table)
        for table in sql.OUTPUT_TABLES
    ]


@pytest.fixture
def no_s3(monkeypatch):
    def fail():
        raise AssertionError("the self-check must not touch S3")

    monkeypatch.setattr(handler, "s3_client", fail)


def test_self_check_passes_when_every_outcome_matches(athena, no_s3):
    queue_results(athena)
    result = handler.lambda_handler({"self_check": True}, None)
    assert result == {"self_check": "passed"}


def test_self_check_runs_the_real_sql_on_every_case_for_every_table(athena, no_s3):
    queue_results(athena)
    handler.lambda_handler({"self_check": True}, None)
    source = sql.cases_source(CASES)
    assert athena.queries == [
        sql.output_query(table, source) for table in sql.OUTPUT_TABLES
    ]


def test_self_check_reads_and_writes_no_lake_data(athena, no_s3):
    queue_results(athena)
    handler.lambda_handler({"self_check": True}, None)
    for query in athena.queries:
        assert sql.RAW_TABLE not in query
        assert "UNLOAD" not in query
        assert "ALTER TABLE" not in query
        assert "s3://" not in query


@pytest.mark.parametrize("table", sql.OUTPUT_TABLES)
def test_self_check_raises_when_any_table_differs(athena, no_s3, table):
    queue_results(athena, wrong_table=table)
    with pytest.raises(handler.SelfCheckFailedError):
        handler.lambda_handler({"self_check": True}, None)


def test_self_check_checks_all_tables_before_raising(athena, no_s3):
    queue_results(athena, wrong_table=sql.OUTPUT_TABLES[0])
    with pytest.raises(handler.SelfCheckFailedError):
        handler.lambda_handler({"self_check": True}, None)
    assert len(athena.queries) == len(sql.OUTPUT_TABLES)


def test_self_check_raises_when_a_query_fails(athena, no_s3):
    queue_results(athena)
    athena.state_for = lambda query: "FAILED"
    with pytest.raises(handler.QueryFailedError):
        handler.lambda_handler({"self_check": True}, None)


def test_self_check_raises_when_queries_return_nothing(athena, no_s3):
    athena.results = [[] for _ in sql.OUTPUT_TABLES]
    with pytest.raises(handler.SelfCheckFailedError):
        handler.lambda_handler({"self_check": True}, None)
