"""The clean job with a fake Athena and moto S3 (criteria 1-5, 24-26)."""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import handler
import pytest
import sql
from botocore.exceptions import ClientError
from helpers import CASES, CLEAN_SRC, LEAKY_REASON, correct_rows

DATE = "2026-10-18"
BUCKET = os.environ["CLEAN_BUCKET"]
INFRA_FILE = Path(__file__).resolve().parents[3] / "infra" / "clean.tf"


def keys(s3, prefix: str = "") -> list[str]:
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix)
    return [item["Key"] for page in pages for item in page.get("Contents", [])]


def put(s3, key: str) -> None:
    s3.put_object(Bucket=BUCKET, Key=key, Body=b"synthetic")


def run(event: dict | None = None) -> dict:
    return handler.lambda_handler({"date": DATE} if event is None else event, None)


# ---- criteria 1, 2: which date ----


@pytest.fixture
def frozen_now(monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 3, 1, 0, 30, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(handler, "datetime", Frozen)


def test_no_date_processes_the_previous_utc_date(athena, s3, frozen_now):
    assert run({}) == {"date": "2026-02-28"}
    assert athena.queries[0] == sql.add_raw_partitions("2026-02-28")


def test_eventbridge_event_without_a_date_processes_the_previous_utc_date(
    athena, s3, frozen_now
):
    event = {"source": "aws.events", "detail-type": "Scheduled Event", "detail": {}}
    assert run(event) == {"date": "2026-02-28"}


def test_previous_utc_date_uses_utc_not_local_time():
    singapore = timezone(timedelta(hours=8))
    # 01:00 on the 18th in Singapore is still the 17th in UTC.
    now = datetime(2026, 10, 18, 1, 0, tzinfo=singapore)
    assert handler.previous_utc_date(now) == "2026-10-16"


@pytest.mark.parametrize(
    "now,expected",
    [
        (datetime(2027, 1, 1, 0, 0, tzinfo=UTC), "2026-12-31"),
        (datetime(2028, 3, 1, 23, 59, tzinfo=UTC), "2028-02-29"),
    ],
)
def test_previous_utc_date_crosses_year_and_leap_day(now, expected):
    assert handler.previous_utc_date(now) == expected


def test_given_date_is_processed(athena, s3):
    assert run({"date": "2024-02-29"}) == {"date": "2024-02-29"}
    assert athena.queries[1] == sql.unload(sql.OUTPUT_TABLES[0], "2024-02-29", BUCKET)


@pytest.mark.parametrize(
    "bad",
    [
        "2026-13-01",
        "2026-02-30",
        "2026-02-29",
        "0000-01-01",
        "2026-1-1",
        "20261018",
        "18-10-2026",
        "2026/10/18",
        " 2026-10-18",
        "2026-10-18 ",
        "2026-10-18\n",
        "2026-10-18T00:00:00Z",
        "2026-10-18' OR 1=1 --",
        "٢٠٢٦-١٠-١٨",
        "",
        20261018,
        1.5,
        True,
        None,
        [],
        ["2026-10-18"],
        {},
    ],
    ids=repr,
)
def test_invalid_date_fails_before_any_aws_call_without_repeating_it(no_aws, bad):
    with pytest.raises(ValueError) as error:
        run({"date": bad})
    if bad:
        assert str(bad) not in str(error.value)


@pytest.mark.parametrize("event", [[], "2026-10-18", 7, None, True], ids=repr)
def test_non_object_event_fails_before_any_aws_call(no_aws, event):
    with pytest.raises(TypeError):
        handler.lambda_handler(event, None)


@pytest.mark.parametrize(
    "event",
    [
        {"self_check": False},
        {"self_check": "true"},
        {"self_check": 1},
        {"self_check": None},
        {"self_check": True, "date": DATE},
        {"self_check": True, "extra": 1},
    ],
    ids=repr,
)
def test_malformed_self_check_fails_before_any_aws_call(no_aws, event):
    with pytest.raises(ValueError):
        handler.lambda_handler(event, None)


# ---- criterion 3: reruns replace the date's output ----


def test_old_output_is_deleted_before_each_unload(athena, s3):
    """The fake UNLOAD fails on a non-empty folder, as Athena's does."""
    for table in sql.OUTPUT_TABLES:
        put(s3, f"tables/{table}/date={DATE}/part-old.parquet")
    run()
    assert not [key for key in keys(s3) if key.endswith("part-old.parquet")]


def test_delete_happens_before_unload_for_every_table(athena, s3, monkeypatch):
    calls = []
    real_delete, real_run = handler.delete_output, handler.run_query

    def delete_output(table, date):
        calls.append(("delete", table))
        real_delete(table, date)

    def run_query(query, table=None):
        if query.startswith("UNLOAD"):
            calls.append(("unload", table))
        return real_run(query, table)

    monkeypatch.setattr(handler, "delete_output", delete_output)
    monkeypatch.setattr(handler, "run_query", run_query)
    run()
    expected = []
    for table in sql.OUTPUT_TABLES:
        expected += [("delete", table), ("unload", table)]
    assert calls == expected


def test_second_run_leaves_one_copy_of_each_table(athena, s3):
    run()
    first = keys(s3)
    run()
    assert len(keys(s3)) == len(first) == len(sql.OUTPUT_TABLES)
    assert not set(first) & set(keys(s3))  # new part names: nothing of run one is left


def test_leftovers_of_a_failed_run_are_removed_by_the_next_run(athena, s3):
    fail_at = sql.OUTPUT_TABLES[3]
    athena.state_for = lambda query: (
        "FAILED" if f"tables/{fail_at}/" in query else "SUCCEEDED"
    )
    with pytest.raises(handler.QueryFailedError):
        run()
    leftovers = keys(s3, f"tables/{fail_at}/")
    assert leftovers
    athena.state_for = lambda query: "SUCCEEDED"
    run()
    assert not set(leftovers) & set(keys(s3))
    assert len(keys(s3)) == len(sql.OUTPUT_TABLES)


def test_rerun_keeps_other_dates_and_files_outside_the_date_folder(athena, s3):
    other_date = "tables/vitals/date=2026-10-17/part-keep.parquet"
    longer_prefix = f"tables/vitals/date={DATE}-synthetic/part-keep.parquet"
    other_table = f"tables/vitals_alert_old/date={DATE}/part-keep.parquet"
    for key in (other_date, longer_prefix, other_table):
        put(s3, key)
    run()
    assert {other_date, longer_prefix, other_table} <= set(keys(s3))


def test_more_than_one_delete_batch_is_all_removed(athena, s3):
    prefix = f"tables/vitals/date={DATE}/"
    for number in range(handler.DELETE_BATCH + 5):
        put(s3, f"{prefix}part-old-{number}.parquet")
    handler.delete_output("vitals", DATE)
    assert keys(s3, prefix) == []


def test_delete_failure_stops_before_unload_and_names_no_key(athena, monkeypatch):
    class Paginator:
        def paginate(self, **kwargs):
            return [{"Contents": [{"Key": "tables/vitals/synthetic-test-key"}]}]

    class FailingS3:
        def get_paginator(self, name):
            return Paginator()

        def delete_objects(self, **kwargs):
            return {"Errors": [{"Key": "tables/vitals/synthetic-test-key"}]}

    monkeypatch.setattr(handler, "s3_client", FailingS3)
    with pytest.raises(RuntimeError) as error:
        run()
    assert "synthetic-test-key" not in str(error.value)
    assert not [query for query in athena.queries if query.startswith("UNLOAD")]


# ---- criterion 4: no raw data ----


def test_day_with_no_raw_data_succeeds_and_leaves_folders_empty(athena, s3):
    athena.empty_day = True
    assert run() == {"date": DATE}
    assert keys(s3) == []


# ---- criterion 26: every table queried, in order ----


def test_every_table_is_unloaded_and_gets_its_partition_after_the_raw_partitions(
    athena, s3
):
    run()
    expected = [sql.add_raw_partitions(DATE)]
    for table in sql.OUTPUT_TABLES:
        expected += [
            sql.unload(table, DATE, BUCKET),
            sql.add_output_partition(table, DATE),
        ]
    assert athena.queries == expected


def test_each_table_ends_up_with_its_output(athena, s3):
    run()
    for table in sql.OUTPUT_TABLES:
        assert len(keys(s3, f"tables/{table}/date={DATE}/")) == 1


def test_queries_run_in_the_job_workgroup_and_database(athena, s3):
    run()
    for request in athena.requests:
        assert request["WorkGroup"] == "silvercare-clean"
        assert request["QueryExecutionContext"] == {"Database": "silvercare_lake"}


# ---- criterion 24: polling, packages, timeout ----


def test_query_is_polled_every_two_seconds(athena, sleeps):
    athena.running_polls = 3
    handler.run_query("SELECT 1")
    assert sleeps == [2, 2, 2]


def test_finished_query_is_not_waited_for(athena, sleeps):
    handler.run_query("SELECT 1")
    assert sleeps == []


@pytest.mark.parametrize("module", ["handler.py", "sql.py", "self_check.py"])
def test_source_imports_only_allowed_packages(module):
    allowed = sys.stdlib_module_names | {
        "boto3",
        "aws_lambda_powertools",
        "sql",
        "self_check",
    }
    tree = ast.parse((CLEAN_SRC / module).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module.split(".")[0])
    assert imported <= allowed


def test_function_timeout_is_five_minutes():
    terraform = INFRA_FILE.read_text(encoding="utf-8")
    module = terraform[terraform.index('module "clean" {') :]
    assert re.search(r"^\s*timeout\s*=\s*300\s*$", module, re.MULTILINE)


# ---- criterion 5: failures ----


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED"])
def test_failed_unload_raises_and_stops_the_job(athena, s3, state):
    table = sql.OUTPUT_TABLES[2]
    athena.state_for = lambda query: (
        state if f"tables/{table}/" in query else "SUCCEEDED"
    )
    with pytest.raises(handler.QueryFailedError):
        run()
    assert athena.queries[-1] == sql.unload(table, DATE, BUCKET)


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED"])
def test_failed_query_logs_its_id_and_final_state(athena, s3, logs, state):
    athena.state_for = lambda query: state
    with pytest.raises(handler.QueryFailedError) as error:
        run()
    finished = [r for r in logs() if r["message"] == "query finished"]
    assert finished[-1]["query_id"] == athena.query_ids[-1]
    assert finished[-1]["state"] == state
    assert athena.query_ids[-1] in str(error.value)
    assert state in str(error.value)


def test_failed_add_raw_partitions_runs_no_unload(athena, s3):
    athena.state_for = lambda query: "FAILED"
    with pytest.raises(handler.QueryFailedError):
        run()
    assert athena.queries == [sql.add_raw_partitions(DATE)]


def test_failed_add_output_partition_raises(athena, s3):
    athena.state_for = lambda query: (
        "FAILED" if query.startswith("ALTER TABLE vitals ") else "SUCCEEDED"
    )
    with pytest.raises(handler.QueryFailedError):
        run()


def test_athena_failure_reason_is_not_raised(athena, s3):
    athena.state_for = lambda query: "FAILED"
    with pytest.raises(handler.QueryFailedError) as error:
        run()
    assert LEAKY_REASON not in str(error.value)


def test_throttled_athena_call_raises_so_the_run_is_retried(athena, s3):
    athena.start_error = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "synthetic"}},
        "StartQueryExecution",
    )
    with pytest.raises(ClientError):
        run()


# ---- result rows ----


def test_result_rows_drops_the_header_and_reads_nulls_across_pages(athena):
    athena.page_size = 2
    athena.results = [
        [
            {"a": "1", "b": None},
            {"a": "2", "b": "x"},
            {"a": None, "b": "y"},
        ]
    ]
    query_id = handler.run_query("SELECT 1")
    assert handler.result_rows(query_id) == athena.results[0]


def test_result_rows_with_no_rows_at_all_is_empty(athena):
    athena.results = [[]]
    query_id = handler.run_query("SELECT 1")
    assert handler.result_rows(query_id) == []


# ---- criterion 25: what the logs hold ----

ALLOWED_LOG_KEYS = {
    "level",
    "location",
    "message",
    "timestamp",
    "service",
    "date",
    "table",
    "query_id",
    "state",
    "bytes_scanned",
    "cases",
    "failed_cases",
}
LOG_MESSAGES = {
    "clean started",
    "clean finished",
    "query finished",
    "self-check passed",
    "self-check failed",
}
# Fixed-format IDs in the cases, plus what Athena's failure text could quote.
FORBIDDEN_IN_LOGS = {LEAKY_REASON, "wearable-", "bedroom-", "00000000-0000-4000"}


def assert_no_data(records: list[dict]) -> None:
    assert records
    # Not the few cases whose whole line is short, like 5: that would match query IDs.
    lines = {case["line"] for case in CASES if len(case["line"]) > 20}
    forbidden = FORBIDDEN_IN_LOGS | lines
    for record in records:
        assert set(record) <= ALLOWED_LOG_KEYS
        assert record["message"] in LOG_MESSAGES
        checked = {
            k: v for k, v in record.items() if k not in {"timestamp", "location"}
        }
        text = json.dumps(checked)
        for value in forbidden:
            assert value not in text


def test_logs_of_a_dated_run_hold_no_data(athena, s3, logs):
    run()
    assert_no_data(logs())


def test_logs_of_a_dated_run_hold_the_date_tables_ids_states_and_bytes(
    athena, s3, logs
):
    run()
    finished = [r for r in logs() if r["message"] == "query finished"]
    assert {r["table"] for r in finished} == {*sql.OUTPUT_TABLES, sql.RAW_TABLE}
    assert [r["query_id"] for r in finished] == athena.query_ids
    assert {r["state"] for r in finished} == {"SUCCEEDED"}
    assert {r["bytes_scanned"] for r in finished} == {1234}
    assert {r["date"] for r in logs() if "date" in r} == {DATE}


def test_logs_of_a_failed_query_hold_no_data(athena, s3, logs):
    athena.state_for = lambda query: "FAILED"
    with pytest.raises(handler.QueryFailedError):
        run()
    assert_no_data(logs())


def test_logs_of_a_passing_self_check_hold_no_data(athena, logs):
    athena.results = [correct_rows(table) for table in sql.OUTPUT_TABLES]
    handler.lambda_handler({"self_check": True}, None)
    assert_no_data(logs())


def test_logs_of_a_failing_self_check_hold_only_case_ids(athena, logs):
    # The rows hold device IDs and lines. The failure must name cases, never quote rows.
    athena.results = [correct_rows(table)[1:] for table in sql.OUTPUT_TABLES]
    with pytest.raises(handler.SelfCheckFailedError) as error:
        handler.lambda_handler({"self_check": True}, None)
    assert_no_data(logs())
    assert str(error.value) == "self-check failed"
    failed = next(r for r in logs() if r["message"] == "self-check failed")
    assert failed["failed_cases"]
    assert set(failed["failed_cases"]) <= {case["id"] for case in CASES}


def test_invalid_input_logs_nothing_and_does_not_echo_the_value(no_aws, logs):
    with pytest.raises(ValueError) as error:
        run({"date": "wearable-0001 hr_bpm 72"})
    assert logs() == []
    assert "wearable-0001" not in str(error.value)
