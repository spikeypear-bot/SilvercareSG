"""summaries Lambda (docs/features/curated-layer.md): the curated layer.

Turns one Singapore day of clean telemetry into one JSON summary per resident with an
assigned device, written to `summaries/date=<D>/<uuid4>.json` in the history bucket and
catalogued as the `daily_summaries` table. Input (criterion 3):

- Lambda's on-success invocation record from `silvercare-clean` (its async runs): the
  date is the record's `responsePayload.date`, clean's return value. Clean finished UTC
  date D, so Singapore date D is fully cleaned and is summarised.
- {"date": "yyyy-mm-dd"}: that Singapore date (a manual re-run or backfill).

Anything else raises before any AWS call, and the error never repeats the value.

Steps, in this order:

1. Check that every clean partition the query reads exists in the Glue catalog
   (criterion 4); a missing one fails the run, naming the table and date only.
2. Read the device -> resident mapping (registry `by-type`, then BatchGetItem of the
   ASSIGNMENT items) and each mapped resident's thresholds (criteria 6-8).
3. Build the query with the mapping and limits inline, refuse it if it is over Athena's
   size limit, run it in the summaries workgroup, and build every summary (9-14).
4. Only then list `summaries/date=<D>/`, delete every object there, and write the new
   summaries, each tagged `resident_id=<id>` in the same PutObject (15-16). A failure
   before the delete leaves the old summaries in place.
5. Add the `date=<D>` partition to `daily_summaries` (18).

If no device is assigned, nothing is queried, deleted, or written (the old summaries of
that date, if any, stay).

Any AWS error or failed query raises, so Lambda's async retries apply. A query's failure
reason is never logged or raised: Athena's text can quote data.

Logs: date, query IDs, states, bytes scanned, counts, skipped device IDs (or `invalid`),
and the resident_id and field names of invalid thresholds. Never summary values,
readings, thresholds, or the query text (criterion 27).
"""

from __future__ import annotations

import os
import re
import time
import uuid
from datetime import UTC, datetime
from functools import cache
from typing import Any
from urllib.parse import urlencode

import boto3
import registry
import sql
import summary
import thresholds
from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config

# The service name comes from POWERTOOLS_SERVICE_NAME, set by Terraform.
logger = Logger()

POLL_SECONDS = 2
DELETE_BATCH = 1000
RESULTS_PAGE = 1000
REGISTRY_INDEX = "by-type"
THRESHOLDS_SK = "THRESHOLDS"
BATCH_GET_MAX_KEYS = 100
# BatchGetItem may return UnprocessedKeys (e.g. throttling on the 3-RCU registry):
# retry them with an exponential back-off, then fail the run.
BATCH_GET_ATTEMPTS = 6
BATCH_GET_BACKOFF_S = 0.2
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
SUCCEEDED = "SUCCEEDED"
FINAL_STATES = frozenset({SUCCEEDED, "FAILED", "CANCELLED"})
SUCCESS_CONDITION = "Success"

_deserializer = TypeDeserializer()


class QueryFailedError(Exception):
    """An Athena query ended in a state other than SUCCEEDED."""


class MissingPartitionError(Exception):
    """A clean partition the summary reads isn't in the Glue catalog."""


class UnprocessedKeysError(Exception):
    """BatchGetItem still had unprocessed keys after every retry."""


# ---- clients (one per container, created on first use) ----


@cache
def athena_client() -> Any:
    return boto3.client("athena")


@cache
def s3_client() -> Any:
    return boto3.client("s3")


@cache
def glue_client() -> Any:
    return boto3.client("glue")


@cache
def dynamodb_client() -> Any:
    """Adaptive retries. DYNAMODB_ENDPOINT points at DynamoDB Local in integration
    tests; it is unset in AWS."""
    return boto3.client(
        "dynamodb",
        config=Config(retries={"mode": "adaptive"}),
        endpoint_url=os.environ.get("DYNAMODB_ENDPOINT"),
    )


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


def parse_event(event: Any) -> str:
    """The Singapore date to summarise, from either input form (criterion 3)."""
    if not isinstance(event, dict):
        raise TypeError("event must be an object")
    if "responsePayload" in event:
        context = event.get("requestContext")
        if (
            not isinstance(context, dict)
            or context.get("condition") != SUCCESS_CONDITION
        ):
            raise ValueError("invocation record must be an on-success record")
        payload = event["responsePayload"]
        if not isinstance(payload, dict) or "date" not in payload:
            raise ValueError("invocation record has no responsePayload.date")
        return parse_date(payload["date"])
    if set(event) == {"date"}:
        return parse_date(event["date"])
    raise ValueError(
        'event must be {"date": "yyyy-mm-dd"} or an on-success invocation record'
    )


def _deserialize(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _deserializer.deserialize(v) for k, v in item.items()}


# ---- Glue (criterion 4) ----


def check_partitions(date: str) -> None:
    """Fail unless every clean partition the query reads exists. The error names the
    table and partition date only."""
    glue = glue_client()
    database = os.environ["GLUE_DATABASE"]
    for table, partition in sql.required_partitions(date):
        try:
            glue.get_partition(
                DatabaseName=database, TableName=table, PartitionValues=[partition]
            )
        except glue.exceptions.EntityNotFoundException:
            raise MissingPartitionError(
                f"clean partition missing: {table} date={partition}"
            ) from None


# ---- registry (criteria 6-8) ----


def query_all(request: dict[str, Any]) -> list[dict[str, Any]]:
    """Every item of an eventually consistent Query, following LastEvaluatedKey."""
    client = dynamodb_client()
    request = dict(request)
    items: list[dict[str, Any]] = []
    while True:
        response = client.query(**request)
        items.extend(_deserialize(item) for item in response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        request["ExclusiveStartKey"] = last_key


def batch_get(keys: list[dict[str, Any]], projection: list[str]) -> list[dict]:
    """Eventually consistent BatchGetItem of `keys` on the registry, at most 100 keys
    per call, retrying UnprocessedKeys with back-off. Returns low-level items."""
    table = os.environ["REGISTRY_TABLE"]
    client = dynamodb_client()
    names = {f"#a{i}": name for i, name in enumerate(projection)}
    items: list[dict[str, Any]] = []
    for start in range(0, len(keys), BATCH_GET_MAX_KEYS):
        pending: dict[str, Any] | None = {
            table: {
                "Keys": keys[start : start + BATCH_GET_MAX_KEYS],
                "ProjectionExpression": ", ".join(names),
                "ExpressionAttributeNames": names,
            }
        }
        for attempt in range(BATCH_GET_ATTEMPTS):
            if attempt:
                time.sleep(BATCH_GET_BACKOFF_S * 2 ** (attempt - 1))
            response = client.batch_get_item(RequestItems=pending)
            items.extend(response.get("Responses", {}).get(table, []))
            pending = response.get("UnprocessedKeys") or None
            if not pending:
                break
        if pending:
            raise UnprocessedKeysError("BatchGetItem left unprocessed keys")
    return items


def read_index() -> registry.IndexScan:
    """Pattern 4: every device on `by-type`, eventually consistent, paginated."""
    items = query_all(
        {
            "TableName": os.environ["REGISTRY_TABLE"],
            "IndexName": REGISTRY_INDEX,
            "KeyConditionExpression": "#type = :device",
            "ExpressionAttributeNames": {
                "#type": "entity_type",
                "#pk": "pk",
                "#resident": "resident_id",
            },
            "ExpressionAttributeValues": {":device": {"S": "DEVICE"}},
            "ProjectionExpression": "#pk, #resident",
            # Required by the role's dynamodb:Select condition (infra/summaries.tf).
            "Select": "SPECIFIC_ATTRIBUTES",
        }
    )
    return registry.assigned_from_index(items)


def read_devices(device_ids: list[str]) -> list[registry.Device]:
    """The ASSIGNMENT items of these devices, validated (criteria 6-7). Skipped ones
    are logged by device_id only."""
    keys = [
        {
            "pk": {"S": f"{registry.DEVICE_PREFIX}{device_id}"},
            "sk": {"S": registry.ASSIGNMENT_SK},
        }
        for device_id in device_ids
    ]
    items = batch_get(keys, ["pk", "resident_id", "device_type", "assigned_at"])
    devices: list[registry.Device] = []
    found: set[str] = set()
    for item in map(_deserialize, items):
        device_id = registry.device_id_from_pk(item.get("pk"))
        found.add(str(device_id))
        device = registry.device_from_item(item)
        if device is None:
            logger.warning(
                "device skipped", extra={"device_id": registry.loggable_id(device_id)}
            )
            continue
        devices.append(device)
    for device_id in device_ids:
        if device_id not in found:
            # Listed on the index but gone from the table (unassigned since).
            logger.warning("device skipped", extra={"device_id": device_id})
    return sorted(devices, key=lambda d: d.device_id)


def read_limits(resident_ids: list[str]) -> dict[str, dict[str, thresholds.Limits]]:
    """Each resident's resolved limits (criterion 8). No item gives the defaults."""
    keys = [
        {"pk": {"S": f"THRESHOLDS#{resident_id}"}, "sk": {"S": THRESHOLDS_SK}}
        for resident_id in resident_ids
    ]
    items = batch_get(keys, ["pk", *thresholds.METRICS])
    by_resident: dict[str, dict[str, Any]] = {}
    for item in items:
        pk = item.get("pk", {}).get("S")
        if isinstance(pk, str) and pk.startswith("THRESHOLDS#"):
            by_resident[pk.removeprefix("THRESHOLDS#")] = item
    limits: dict[str, dict[str, thresholds.Limits]] = {}
    for resident_id in resident_ids:
        # limits_from_item takes a low-level item, as BatchGetItem returns it.
        low_level = by_resident.get(resident_id)
        resolved, invalid = thresholds.limits_from_item(low_level)
        if invalid:
            logger.warning(
                "invalid thresholds, defaults used",
                extra={"resident_id": resident_id, "fields": invalid},
            )
        limits[resident_id] = resolved
    return limits


# ---- Athena ----


def run_query(query: str, step: str) -> str:
    """Run one query in the summaries workgroup to its end, polling every 2 seconds.
    Refuses text over Athena's limit before starting it. Returns the query ID."""
    sql.check_size(query)
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
            "step": step,
            "query_id": query_id,
            "state": state,
            "bytes_scanned": execution.get("Statistics", {}).get("DataScannedInBytes"),
        },
    )
    if state != SUCCEEDED:
        raise QueryFailedError(f"query {query_id} ended {state}")
    return query_id


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
    header = [cell.get("VarCharValue") for cell in rows[0]]
    return [
        dict(zip(header, (cell.get("VarCharValue") for cell in row), strict=True))
        for row in rows[1:]
    ]


# ---- S3 (criteria 15-16) ----


def delete_existing(date: str) -> int:
    """Delete every object under summaries/date=<D>/. Returns how many."""
    bucket = os.environ["HISTORY_BUCKET"]
    s3 = s3_client()
    pages = s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket,
        Prefix=sql.summary_prefix(date),
        PaginationConfig={"PageSize": DELETE_BATCH},
    )
    deleted = 0
    for page in pages:
        keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if not keys:
            continue
        response = s3.delete_objects(
            Bucket=bucket, Delete={"Objects": keys, "Quiet": True}
        )
        # DeleteObjects reports per-key failures in a 200 response.
        if response.get("Errors"):
            raise RuntimeError("could not delete old summaries")
        deleted += len(keys)
    return deleted


def summary_key(date: str) -> str:
    """summaries/date=<D>/<uuid4>.json: a random name, no resident ID in the key."""
    return f"{sql.summary_prefix(date)}{uuid.uuid4()}.json"


def write_summaries(date: str, summaries: list[dict[str, Any]]) -> int:
    bucket = os.environ["HISTORY_BUCKET"]
    s3 = s3_client()
    for item in summaries:
        s3.put_object(
            Bucket=bucket,
            Key=summary_key(date),
            Body=summary.body(item),
            ContentType="application/json",
            Tagging=urlencode({"resident_id": item["resident_id"]}),
        )
    return len(summaries)


# ---- the job ----


def now_utc() -> datetime:
    return datetime.now(UTC)


def summarise(date: str) -> int:
    """Summarise Singapore date D. Returns how many summaries were written."""
    check_partitions(date)
    scan = read_index()
    for device_id in scan.invalid:
        logger.warning("device skipped", extra={"device_id": device_id})
    devices = read_devices(scan.assigned)
    resident_ids = sorted({device.resident_id for device in devices})
    logger.info(
        "registry read",
        extra={
            "date": date,
            "devices_assigned": len(scan.assigned),
            "devices_unassigned": scan.unassigned,
            "devices_invalid": len(scan.invalid),
            "devices": len(devices),
            "residents": len(resident_ids),
        },
    )
    if not devices:
        logger.warning(
            "no assigned devices: summaries left unchanged", extra={"date": date}
        )
        return 0
    limits = read_limits(resident_ids)

    query_id = run_query(sql.summary_query(date, devices, limits), "summary")
    generated_at = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    summaries = summary.build_all(result_rows(query_id), date, limits, generated_at)

    deleted = delete_existing(date)
    written = write_summaries(date, summaries)
    run_query(sql.add_partition(date), "add_partition")
    logger.info(
        "summaries replaced",
        extra={
            "date": date,
            "residents": len(resident_ids),
            "objects_deleted": deleted,
            "summaries_written": written,
        },
    )
    return written


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    date = parse_event(event)
    logger.info("summaries started", extra={"date": date})
    written = summarise(date)
    logger.info("summaries finished", extra={"date": date, "summaries": written})
    return {"date": date, "summaries": written}
