"""clean Lambda (docs/features/clean-data.md).

Turns one UTC day of raw telemetry into Parquet clean tables and a rejected table,
using Athena. Input:

- no date (the daily EventBridge schedule): the previous UTC date
- {"date": "yyyy-mm-dd"}: that date (a re-run or backfill)
- {"self_check": true}: run the real SQL on cases.json as inline VALUES, reading and
  writing no lake data, and raise unless every outcome matches

Bad input raises before any AWS call, and the error never repeats the value. For a date,
each table's objects are deleted first and then written again, because UNLOAD needs an
empty folder and doesn't clean up after a failed run.

Any failed query raises, so the invocation fails and Lambda's async retries apply. A
query's failure reason is never logged or raised: Athena's text can quote data.

Logs: date, table names, query IDs, states, and bytes scanned, plus the IDs of
self-check cases that failed (synthetic names). Never lines, field values, device IDs,
or message IDs.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import boto3
import self_check
import sql
from aws_lambda_powertools import Logger

# The service name comes from POWERTOOLS_SERVICE_NAME, set by Terraform.
logger = Logger()

POLL_SECONDS = 2
DELETE_BATCH = 1000
RESULTS_PAGE = 1000
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
CASES_PATH = Path(__file__).parent / "cases.json"
SUCCEEDED = "SUCCEEDED"
FINAL_STATES = frozenset({SUCCEEDED, "FAILED", "CANCELLED"})


class QueryFailedError(Exception):
    """An Athena query ended in a state other than SUCCEEDED."""


class SelfCheckFailedError(Exception):
    """The SQL gave a different outcome from cases.json for at least one case."""


@cache
def athena_client() -> Any:
    """One Athena client per container, created on first use."""
    return boto3.client("athena")


@cache
def s3_client() -> Any:
    """One S3 client per container, created on first use."""
    return boto3.client("s3")


# ---- input (pure, no AWS) ----


def parse_date(value: Any) -> str:
    """Return a real date in exactly yyyy-mm-dd form, or raise without repeating it."""
    if type(value) is not str or not DATE_RE.fullmatch(value):
        raise ValueError("date must be yyyy-mm-dd")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError("date must be a real date") from None
    return value


def previous_utc_date(now: datetime) -> str:
    return (now.astimezone(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")


def wants_self_check(event: Any) -> bool:
    """True for exactly {"self_check": true}; anything like it but malformed raises."""
    if not isinstance(event, dict):
        raise TypeError("event must be an object")
    if "self_check" not in event:
        return False
    if event["self_check"] is not True or len(event) != 1:
        raise ValueError('self_check must be exactly {"self_check": true}')
    return True


# ---- AWS calls ----


def run_query(query: str, table: str | None = None) -> str:
    """Run one query to its end, polling every 2 seconds. Returns the query ID."""
    athena = athena_client()
    query_id = athena.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": os.environ["GLUE_DATABASE"]},
        WorkGroup=os.environ["ATHENA_WORKGROUP"],
    )["QueryExecutionId"]
    while True:
        execution = athena.get_query_execution(QueryExecutionId=query_id)[
            "QueryExecution"
        ]
        state = execution["Status"]["State"]
        if state in FINAL_STATES:
            break
        time.sleep(POLL_SECONDS)
    logger.info(
        "query finished",
        extra={
            "table": table,
            "query_id": query_id,
            "state": state,
            "bytes_scanned": execution.get("Statistics", {}).get("DataScannedInBytes"),
        },
    )
    if state != SUCCEEDED:
        raise QueryFailedError(f"query {query_id} ended {state}")
    return query_id


def delete_output(table: str, date: str) -> None:
    """Delete everything under the table's folder for this date."""
    bucket = os.environ["CLEAN_BUCKET"]
    s3 = s3_client()
    pages = s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket,
        Prefix=sql.output_prefix(table, date),
        PaginationConfig={"PageSize": DELETE_BATCH},
    )
    for page in pages:
        keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if not keys:
            continue
        response = s3.delete_objects(
            Bucket=bucket, Delete={"Objects": keys, "Quiet": True}
        )
        # DeleteObjects reports per-key failures in a 200 response.
        if response.get("Errors"):
            raise RuntimeError(f"could not delete old output of {table}")


def result_rows(query_id: str) -> list[dict[str, str | None]]:
    """A finished query's rows as {column: text or None}, without the header row."""
    pages = (
        athena_client()
        .get_paginator("get_query_results")
        .paginate(
            QueryExecutionId=query_id, PaginationConfig={"PageSize": RESULTS_PAGE}
        )
    )
    rows = [row["Data"] for page in pages for row in page["ResultSet"]["Rows"]]
    if not rows:
        return []
    header = [cell["VarCharValue"] for cell in rows[0]]
    return [
        dict(zip(header, (cell.get("VarCharValue") for cell in row), strict=True))
        for row in rows[1:]
    ]


# ---- the two jobs ----


def clean_date(date: str) -> None:
    bucket = os.environ["CLEAN_BUCKET"]
    run_query(sql.add_raw_partitions(date), sql.RAW_TABLE)
    for table in sql.OUTPUT_TABLES:
        delete_output(table, date)
        run_query(sql.unload(table, date, bucket), table)
        run_query(sql.add_output_partition(table, date), table)


def run_self_check() -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    source = sql.cases_source(cases)
    failed: list[str] = []
    for table in sql.OUTPUT_TABLES:
        query_id = run_query(sql.output_query(table, source), table)
        failed += self_check.failures(table, cases, result_rows(query_id))
    if failed:
        logger.error("self-check failed", extra={"failed_cases": failed})
        raise SelfCheckFailedError("self-check failed")
    logger.info("self-check passed", extra={"cases": len(cases)})


def lambda_handler(event: Any, context: Any) -> dict[str, str]:
    if wants_self_check(event):
        run_self_check()
        return {"self_check": "passed"}
    if "date" in event:
        date = parse_date(event["date"])
    else:
        date = previous_utc_date(datetime.now(UTC))
    logger.info("clean started", extra={"date": date})
    clean_date(date)
    logger.info("clean finished", extra={"date": date})
    return {"date": date}
