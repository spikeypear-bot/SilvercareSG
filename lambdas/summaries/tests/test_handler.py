"""The summaries job with fake Athena and Glue and moto DynamoDB and S3
(docs/features/curated-layer.md, criteria 3, 4, 9, 15, 16, 18, 27, 28)."""

from __future__ import annotations

import ast
import json
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import handler
import pytest
import sql
from botocore.exceptions import ClientError
from conftest import BUCKET
from helpers import (
    DATE,
    LEAKY_REASON,
    LIMIT_TEXTS,
    PARTITIONS_FOR_DATE,
    READING_TEXTS,
    empty_row,
    put_device,
    put_thresholds,
    reading_row,
    residents_in,
    seed,
)

SRC = Path(__file__).resolve().parent.parent / "src"
INFRA_FILE = Path(__file__).resolve().parents[3] / "infra" / "summaries.tf"
GENERATED_AT = datetime(2026, 10, 19, 0, 40, 5, tzinfo=UTC)
KEY_RE = re.compile(
    r"summaries/date=2026-10-18/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.json"
)
OLD_KEY = f"summaries/date={DATE}/00000000-0000-4000-8000-000000000001.json"
CLEAN_ARN = "arn:aws:lambda:ap-southeast-1:000000000000:function:silvercare-clean"


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(handler, "now_utc", lambda: GENERATED_AT)


@pytest.fixture
def seeded(aws):
    seed(aws.ddb)
    return aws


def run(event: dict | None = None) -> dict:
    return handler.lambda_handler({"date": DATE} if event is None else event, None)


def on_success_record(response_payload, condition: str = "Success") -> dict:
    """Lambda's on-success destination record from silvercare-clean."""
    return {
        "version": "1.0",
        "timestamp": "2026-10-19T00:30:12.345Z",
        "requestContext": {
            "requestId": "00000000-0000-4000-8000-00000000abcd",
            "functionArn": f"{CLEAN_ARN}:$LATEST",
            "condition": condition,
            "approximateInvokeCount": 1,
        },
        "requestPayload": {"source": "aws.events", "detail": {}},
        "responseContext": {"statusCode": 200, "executedVersion": "$LATEST"},
        "responsePayload": response_payload,
    }


def without_context() -> dict:
    record = on_success_record({"date": DATE})
    del record["requestContext"]
    return record


def summary_rows(*rows: dict):
    """A rows_for that gives these rows to the summary query only."""
    return lambda query: list(rows) if query.startswith("WITH") else []


def put_old(aws, key: str = OLD_KEY) -> None:
    aws.s3.put_object(Bucket=BUCKET, Key=key, Body=b'{"synthetic": true}')


def summaries_written(aws) -> list[dict]:
    out = []
    for key in aws.object_keys(f"summaries/date={DATE}/"):
        body = aws.s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        out.append(json.loads(body))
    return sorted(out, key=lambda s: s["resident_id"])


# ---- criterion 3: input ----


def test_date_event_summarises_that_singapore_date(seeded, athena):
    assert run() == {"date": DATE, "summaries": 2}
    start, end = sql.window(DATE)
    assert sql._timestamp(start) in athena.queries[0]
    assert sql._timestamp(end) in athena.queries[0]


def test_on_success_record_summarises_its_response_payload_date(seeded, athena):
    event = on_success_record({"date": DATE})
    assert handler.lambda_handler(event, None) == {"date": DATE, "summaries": 2}


def test_on_success_record_date_wins_over_the_request_payload(seeded, athena):
    event = on_success_record({"date": DATE})
    event["requestPayload"] = {"date": "2026-10-01"}
    assert handler.lambda_handler(event, None)["date"] == DATE


@pytest.mark.parametrize(
    "event",
    [
        on_success_record({"date": DATE}, condition="RetriesExhausted"),
        on_success_record({"date": DATE}, condition="EventAgeExceeded"),
        {**on_success_record({"date": DATE}), "requestContext": None},
        without_context(),
        on_success_record(None),
        on_success_record({"self_check": "passed"}),
        on_success_record([DATE]),
        on_success_record(DATE),
    ],
    ids=[
        "on-failure-retries",
        "on-failure-age",
        "context-null",
        "no-context",
        "payload-null",
        "payload-self-check",
        "payload-list",
        "payload-string",
    ],
)
def test_bad_invocation_record_fails_before_any_aws_call(no_aws, event):
    with pytest.raises(ValueError):
        handler.lambda_handler(event, None)


@pytest.mark.parametrize(
    "bad",
    [
        "2026-13-01",
        "2026-02-30",
        "2026-02-29",
        "2026-1-1",
        "20261018",
        "18-10-2026",
        "2026/10/18",
        " 2026-10-18",
        "2026-10-18 ",
        "2026-10-18\n",
        "2026-10-18T00:00:00Z",
        "2026-10-18' OR 1=1 --",
        "\u0662\u0660\u0662\u0666-\u0661\u0660-\u0661\u0668",
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
@pytest.mark.parametrize("form", ["date", "record"])
def test_invalid_date_fails_before_any_aws_call_without_repeating_it(no_aws, bad, form):
    event = {"date": bad} if form == "date" else on_success_record({"date": bad})
    with pytest.raises(ValueError) as error:
        handler.lambda_handler(event, None)
    if bad not in ("", None, [], {}) and not isinstance(bad, bool):
        assert str(bad).strip() not in str(error.value)


@pytest.mark.parametrize("event", [[], "2026-10-18", 7, None, True], ids=repr)
def test_non_object_event_fails_before_any_aws_call(no_aws, event):
    with pytest.raises(TypeError):
        handler.lambda_handler(event, None)


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"date": DATE, "extra": 1},
        {"Date": DATE},
        {"source": "aws.events", "detail": {}},
        {"self_check": True},
    ],
    ids=repr,
)
def test_other_objects_fail_before_any_aws_call(no_aws, event):
    with pytest.raises(ValueError):
        handler.lambda_handler(event, None)


def test_invalid_input_logs_nothing_and_does_not_echo_the_value(no_aws, logs):
    with pytest.raises(ValueError) as error:
        run({"date": "wearable-0001 hr_bpm 143"})
    assert logs() == []
    assert "wearable-0001" not in str(error.value)
    assert "143" not in str(error.value)


# ---- criterion 4: clean partitions must exist ----


def test_every_required_partition_is_checked_in_the_lake_database(seeded, athena):
    run()
    checked = {
        (p["TableName"], tuple(p["PartitionValues"]))
        for p in seeded.params("glue", "GetPartition")
    }
    assert checked == {(table, (date,)) for table, date in PARTITIONS_FOR_DATE}
    assert {p["DatabaseName"] for p in seeded.params("glue", "GetPartition")} == {
        "silvercare_lake"
    }


@pytest.mark.parametrize("table,partition", sorted(PARTITIONS_FOR_DATE))
def test_missing_partition_fails_naming_table_and_date_and_changes_nothing(
    seeded, athena, table, partition
):
    put_old(seeded)
    seeded.glue.partitions.remove(("silvercare_lake", table, partition))
    with pytest.raises(handler.MissingPartitionError) as error:
        run()
    assert str(error.value) == f"clean partition missing: {table} date={partition}"
    assert seeded.ops("dynamodb") == []
    assert athena.queries == []
    assert seeded.ops("s3") == []
    assert seeded.object_keys() == [OLD_KEY]


def test_partition_check_comes_before_the_registry_read(seeded, athena):
    run()
    services = [svc for svc, _, _ in seeded.calls]
    assert services.index("dynamodb") > max(
        i for i, svc in enumerate(services) if svc == "glue"
    )


def test_glue_error_other_than_not_found_is_raised(seeded, athena, monkeypatch):
    class FailingGlue:
        class exceptions:
            class EntityNotFoundException(Exception):
                pass

        def get_partition(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "ThrottlingException", "Message": "synthetic"}},
                "GetPartition",
            )

    monkeypatch.setattr(handler, "glue_client", FailingGlue)
    with pytest.raises(ClientError):
        run()
    assert athena.queries == []


# ---- no assigned device (builder decision) ----


def test_no_assigned_device_logs_a_warning_and_changes_nothing(aws, athena, logs):
    put_device(aws.ddb, "wearable-0003", resident_id=None)
    put_old(aws)
    assert run() == {"date": DATE, "summaries": 0}
    warnings = [r for r in logs() if r["level"] == "WARNING"]
    assert [r["message"] for r in warnings] == [
        "no assigned devices: summaries left unchanged"
    ]
    assert warnings[0]["date"] == DATE
    assert athena.queries == []
    assert aws.ops("s3") == []
    assert aws.object_keys() == [OLD_KEY]


def test_empty_registry_changes_nothing(aws, athena):
    put_old(aws)
    assert run() == {"date": DATE, "summaries": 0}
    assert athena.queries == []
    assert aws.object_keys() == [OLD_KEY]


def test_only_invalid_devices_count_as_none_assigned(aws, athena):
    put_device(aws.ddb, "wearable-0001", resident_id="Test Resident A")
    put_old(aws)
    assert run()["summaries"] == 0
    assert athena.queries == []
    assert aws.object_keys() == [OLD_KEY]


def test_no_assigned_device_reads_no_thresholds(aws, athena):
    put_device(aws.ddb, "wearable-0003", resident_id=None)
    run()
    assert aws.ops("dynamodb") == ["query"]


# ---- criterion 9: the query-size guard ----


def test_query_over_athenas_limit_is_never_started(athena):
    with pytest.raises(sql.QueryTooLargeError):
        handler.run_query("S" * (sql.ATHENA_MAX_QUERY_BYTES + 1), "summary")
    assert athena.queries == []


def test_query_at_athenas_limit_is_started(athena):
    handler.run_query("S" * sql.ATHENA_MAX_QUERY_BYTES, "summary")
    assert len(athena.queries) == 1


def test_too_many_devices_fail_before_anything_is_deleted(aws, athena, monkeypatch):
    seed(aws.ddb)
    put_old(aws)
    monkeypatch.setattr(sql, "ATHENA_MAX_QUERY_BYTES", 500)
    with pytest.raises(sql.QueryTooLargeError) as error:
        run()
    assert athena.queries == []
    assert aws.ops("s3") == []
    assert aws.object_keys() == [OLD_KEY]
    assert "VALUES" not in str(error.value)
    assert "res-0001" not in str(error.value)
    assert "wearable-0001" not in str(error.value)


# ---- criteria 9, 11: what reaches the query ----


def test_query_names_every_mapped_resident_and_no_spare(seeded, athena):
    run()
    query = athena.queries[0]
    assert residents_in(query) == ["res-0001", "res-0002"]
    assert "'wearable-0001'" in query and "'bedroom-0001'" in query
    assert "'wearable-0003'" not in query


def test_query_runs_in_the_summaries_workgroup_and_lake_database(seeded, athena):
    run()
    for request in athena.requests:
        assert request["WorkGroup"] == "silvercare-summaries"
        assert request["QueryExecutionContext"] == {"Database": "silvercare_lake"}


def test_query_carries_each_residents_resolved_limits(seeded, athena):
    run()
    query = athena.queries[0]
    assert (
        "('res-0001', 50, 120, 92, CAST('30.0' AS double), CAST('38.0' AS double))"
        in query
    )
    assert (
        "('res-0002', 47, 113, 89, CAST('31.7' AS double), CAST('37.3' AS double))"
        in query
    )


def test_query_carries_assigned_at_as_a_utc_timestamp(aws, athena):
    put_device(
        aws.ddb,
        "wearable-0001",
        resident_id="res-0001",
        assigned_at="2026-10-17T20:15:30.123456Z",
    )
    run()
    assert (
        "('wearable-0001', 'wearable', 'res-0001',"
        " TIMESTAMP '2026-10-17 20:15:30.124')" in athena.queries[0]
    )


# ---- criteria 11, 15: what is written ----


def test_one_summary_per_resident_even_without_readings(seeded, athena):
    run()
    written = summaries_written(seeded)
    assert [s["resident_id"] for s in written] == ["res-0001", "res-0002"]
    first = written[0]
    assert first["vitals_readings"] == 0
    assert first["hr_bpm_min"] is None
    assert first["bed_minutes"] is None
    assert first["falls"] == 0


def test_summary_holds_the_query_values_thresholds_and_metadata(seeded, athena):
    athena.rows_for = summary_rows(reading_row("res-0001"), empty_row("res-0002"))
    run()
    first, second = summaries_written(seeded)
    assert first["date"] == DATE
    assert first["hr_bpm_avg"] == 76.5
    assert first["hr_bpm_max"] == 143
    assert first["bed_minutes"] == 417
    assert first["generated_at"] == "2026-10-19T00:40:05Z"
    assert first["schema_version"] == 1
    assert first["thresholds"] == {
        "hr_bpm": {"min": 50, "max": 120},
        "spo2_pct": {"min": 92},
        "skin_temp_c": {"min": 30.0, "max": 38.0},
    }
    assert second["thresholds"] == {
        "hr_bpm": {"min": 47, "max": 113},
        "spo2_pct": {"min": 89},
        "skin_temp_c": {"min": 31.7, "max": 37.3},
    }


def test_object_key_is_a_random_uuid4_under_the_singapore_date(seeded, athena):
    run()
    keys = seeded.object_keys()
    assert len(keys) == 2
    for key in keys:
        assert KEY_RE.fullmatch(key)
        assert uuid.UUID(key.rsplit("/", 1)[1].removesuffix(".json")).version == 4
        assert "res-" not in key


def test_each_object_is_tagged_with_its_resident_in_the_put_call(seeded, athena):
    run()
    puts = seeded.params("s3", "PutObject")
    assert [p["Tagging"] for p in puts] == [
        "resident_id=res-0001",
        "resident_id=res-0002",
    ]
    assert seeded.ops("s3").count("PutObjectTagging") == 0
    for key in seeded.object_keys():
        tags = seeded.s3.get_object_tagging(Bucket=BUCKET, Key=key)["TagSet"]
        body = json.loads(seeded.s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
        assert tags == [{"Key": "resident_id", "Value": body["resident_id"]}]


def test_object_is_one_line_of_utf8_json(seeded, athena):
    run()
    for key in seeded.object_keys():
        obj = seeded.s3.get_object(Bucket=BUCKET, Key=key)
        assert obj["ContentType"] == "application/json"
        body = obj["Body"].read()
        assert b"\n" not in body
        json.loads(body.decode("utf-8"))


def test_objects_go_to_the_history_bucket(seeded, athena):
    run()
    assert {p["Bucket"] for p in seeded.params("s3", "PutObject")} == {BUCKET}


# ---- criterion 16: replace order ----


def test_replace_order_is_query_build_list_delete_write_then_partition(seeded, athena):
    put_old(seeded)
    run()
    steps = []
    for svc, op, params in seeded.calls:
        if svc == "athena":
            steps.append(params["step"])
        elif svc == "build":
            steps.append("build")
        elif svc == "s3":
            steps.append(op)
    assert steps == [
        "summary",
        "build",
        "ListObjectsV2",
        "DeleteObjects",
        "PutObject",
        "PutObject",
        "add_partition",
    ]


def test_rerun_replaces_the_dates_summaries(seeded, athena):
    run()
    first = set(seeded.object_keys())
    run()
    second = set(seeded.object_keys())
    assert len(second) == 2
    assert not first & second


def test_old_summaries_are_deleted(seeded, athena):
    put_old(seeded)
    run()
    assert OLD_KEY not in seeded.object_keys()


def test_rerun_keeps_other_dates_and_other_prefixes(seeded, athena):
    keep = [
        "summaries/date=2026-10-17/00000000-0000-4000-8000-000000000002.json",
        "summaries/date=2026-10-19/00000000-0000-4000-8000-000000000003.json",
        f"alerts/date={DATE}/synthetic.json",
        f"telemetry/date={DATE}/synthetic.json",
    ]
    for key in keep:
        put_old(seeded, key)
    run()
    assert set(keep) <= set(seeded.object_keys())


def test_more_than_one_delete_batch_is_all_removed(seeded, athena):
    prefix = f"summaries/date={DATE}/"
    for number in range(handler.DELETE_BATCH + 5):
        put_old(seeded, f"{prefix}old-{number:05d}.json")
    assert handler.delete_existing(DATE) == handler.DELETE_BATCH + 5
    assert seeded.object_keys(prefix) == []


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED"])
def test_failed_summary_query_deletes_and_writes_nothing(seeded, athena, state):
    put_old(seeded)
    athena.state_for = lambda q: state if q.startswith("WITH") else "SUCCEEDED"
    with pytest.raises(handler.QueryFailedError):
        run()
    assert seeded.ops("s3") == []
    assert seeded.object_keys() == [OLD_KEY]
    assert athena.steps() == ["summary"]


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [empty_row("res-0001")],
        [empty_row("res-0001"), empty_row("res-0001"), empty_row("res-0002")],
        [empty_row("res-0001"), empty_row("res-0009")],
        [{**empty_row("res-0001"), "hr_bpm_max": "x"}, empty_row("res-0002")],
    ],
    ids=["no-rows", "missing-resident", "duplicate", "unknown-resident", "bad-value"],
)
def test_bad_query_result_deletes_and_writes_nothing(seeded, athena, rows):
    put_old(seeded)
    athena.rows_for = summary_rows(*rows)
    with pytest.raises(handler.summary.BadResultError):
        run()
    assert seeded.ops("s3") == []
    assert seeded.object_keys() == [OLD_KEY]


def test_throttled_registry_read_raises_before_any_query_or_delete(seeded, athena):
    put_old(seeded)
    seeded.dynamodb.fail["query"] = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}},
        "Query",
    )
    with pytest.raises(ClientError):
        run()
    assert athena.queries == []
    assert seeded.object_keys() == [OLD_KEY]


def test_throttled_athena_start_raises_before_any_delete(seeded, athena):
    put_old(seeded)
    athena.start_error = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "synthetic"}},
        "StartQueryExecution",
    )
    with pytest.raises(ClientError):
        run()
    assert seeded.ops("s3") == []
    assert seeded.object_keys() == [OLD_KEY]


def test_delete_failure_writes_nothing_and_names_no_key(seeded, athena, monkeypatch):
    class Paginator:
        def paginate(self, **kwargs):
            return [{"Contents": [{"Key": "summaries/date=x/synthetic-test-key"}]}]

    class FailingS3:
        def __init__(self):
            self.puts = 0

        def get_paginator(self, name):
            return Paginator()

        def delete_objects(self, **kwargs):
            return {"Errors": [{"Key": "summaries/date=x/synthetic-test-key"}]}

        def put_object(self, **kwargs):
            self.puts += 1

    s3 = FailingS3()
    monkeypatch.setattr(handler, "s3_client", lambda: s3)
    with pytest.raises(RuntimeError) as error:
        run()
    assert "synthetic-test-key" not in str(error.value)
    assert s3.puts == 0
    assert "add_partition" not in athena.steps()


# ---- criterion 18: the daily_summaries partition ----


def test_partition_is_added_after_the_writes(seeded, athena):
    run()
    assert athena.queries[-1] == sql.add_partition(DATE)
    assert athena.steps() == ["summary", "add_partition"]


def test_failed_add_partition_raises(seeded, athena):
    athena.state_for = lambda q: "FAILED" if q.startswith("ALTER") else "SUCCEEDED"
    with pytest.raises(handler.QueryFailedError):
        run()


# ---- Athena polling and results ----


def test_query_is_polled_every_two_seconds(athena, sleeps):
    athena.running_polls = 3
    handler.run_query("SELECT 1", "summary")
    assert sleeps == [2, 2, 2]


def test_finished_query_is_not_waited_for(athena, sleeps):
    handler.run_query("SELECT 1", "summary")
    assert sleeps == []


def test_result_rows_drop_the_header_and_read_nulls_across_pages(athena):
    athena.page_size = 2
    rows = [empty_row("res-0001"), reading_row("res-0002"), empty_row("res-0003")]
    athena.rows_for = lambda query: rows
    query_id = handler.run_query("SELECT 1", "summary")
    assert handler.result_rows(query_id) == rows


def test_athena_failure_reason_is_not_raised(athena):
    athena.state_for = lambda q: "FAILED"
    with pytest.raises(handler.QueryFailedError) as error:
        handler.run_query("SELECT 1", "summary")
    assert LEAKY_REASON not in str(error.value)
    assert str(error.value) == "query query-test-0000 ended FAILED"


# ---- criterion 27: logs ----

ALLOWED_LOG_KEYS = {
    "level",
    "message",
    "service",
    "date",
    "step",
    "query_id",
    "state",
    "bytes_scanned",
    "devices_assigned",
    "devices_unassigned",
    "devices_invalid",
    "devices",
    "residents",
    "objects_deleted",
    "summaries_written",
    "summaries",
    "device_id",
    "resident_id",
    "fields",
}
LOG_MESSAGES = {
    "summaries started",
    "summaries finished",
    "registry read",
    "query finished",
    "summaries replaced",
    "device skipped",
    "invalid thresholds, defaults used",
    "no assigned devices: summaries left unchanged",
}
QUERY_TEXT = ("SELECT", "VALUES", "TIMESTAMP", "CAST(", "count_if", "ALTER TABLE")


def assert_no_values(records: list[dict]) -> None:
    assert records
    for record in records:
        assert set(record) <= ALLOWED_LOG_KEYS, record
        assert record["message"] in LOG_MESSAGES
        text = json.dumps(record)
        for value in (*LIMIT_TEXTS, *READING_TEXTS, *QUERY_TEXT, LEAKY_REASON):
            assert value not in text, value


def test_logs_of_a_run_hold_no_values_thresholds_or_query_text(seeded, athena, logs):
    athena.rows_for = summary_rows(reading_row("res-0001"), reading_row("res-0002"))
    run()
    assert_no_values(logs())


def test_logs_of_a_failed_query_hold_no_values(seeded, athena, logs):
    athena.state_for = lambda q: "FAILED"
    with pytest.raises(handler.QueryFailedError):
        run()
    assert_no_values(logs())


def test_logs_of_invalid_thresholds_hold_field_names_only(aws, athena, logs):
    put_device(aws.ddb, "wearable-0001", resident_id="res-0001")
    put_thresholds(aws.ddb, "res-0001", hr_bpm={"min": 47, "max": 251})
    run()
    assert_no_values(logs())
    (record,) = [r for r in logs() if r["message"].startswith("invalid thresholds")]
    assert record["resident_id"] == "res-0001"
    assert record["fields"] == ["hr_bpm.max"]
    assert "251" not in json.dumps(record)


def test_logs_hold_the_date_query_ids_states_bytes_and_counts(seeded, athena, logs):
    put_old(seeded)
    run()
    records = logs()
    finished = [r for r in records if r["message"] == "query finished"]
    assert [r["query_id"] for r in finished] == athena.query_ids
    assert [r["step"] for r in finished] == ["summary", "add_partition"]
    assert {r["state"] for r in finished} == {"SUCCEEDED"}
    assert {r["bytes_scanned"] for r in finished} == {2048}
    registry = next(r for r in records if r["message"] == "registry read")
    assert registry["devices_assigned"] == 3
    assert registry["devices_unassigned"] == 1
    assert registry["devices"] == 3
    assert registry["residents"] == 2
    replaced = next(r for r in records if r["message"] == "summaries replaced")
    assert replaced["objects_deleted"] == 1
    assert replaced["summaries_written"] == 2
    assert {r["date"] for r in records if "date" in r} == {DATE}


def test_failed_query_logs_its_id_and_final_state(seeded, athena, logs):
    athena.state_for = lambda q: "CANCELLED"
    with pytest.raises(handler.QueryFailedError):
        run()
    finished = [r for r in logs() if r["message"] == "query finished"]
    assert finished[-1]["query_id"] == athena.query_ids[-1]
    assert finished[-1]["state"] == "CANCELLED"


def test_bad_result_error_does_not_repeat_the_value(seeded, athena):
    athena.rows_for = summary_rows(
        {**empty_row("res-0001"), "hr_bpm_max": "143.5"}, empty_row("res-0002")
    )
    with pytest.raises(handler.summary.BadResultError) as error:
        run()
    assert "143" not in str(error.value)


# ---- criterion 28: packages and settings ----


@pytest.mark.parametrize(
    "module", ["handler.py", "registry.py", "thresholds.py", "sql.py", "summary.py"]
)
def test_source_imports_only_allowed_packages(module):
    allowed = sys.stdlib_module_names | {
        "boto3",
        "botocore",
        "aws_lambda_powertools",
        "registry",
        "thresholds",
        "sql",
        "summary",
    }
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module.split(".")[0])
    assert imported <= allowed


def test_function_timeout_memory_and_log_retention():
    terraform = INFRA_FILE.read_text(encoding="utf-8")
    module = terraform[terraform.index('module "summaries" {') :]
    module = module[: module.index("\n}\n")]
    assert re.search(r"^\s*timeout\s*=\s*300\s*$", module, re.MULTILINE)
    assert re.search(r"^\s*memory_size\s*=\s*128\s*$", module, re.MULTILINE)
    assert re.search(r"^\s*log_retention_days\s*=\s*30\s*$", module, re.MULTILINE)
