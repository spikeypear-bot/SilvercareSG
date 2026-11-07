"""results Lambda (docs/features/nightly-results.md, the team slide's `loadResults`).

Copies one Singapore date's daily summaries (docs/features/curated-layer.md) from
`summaries/date=<D>/` in the history bucket into the `silvercare-results` DynamoDB table
(docs/data-model.md, pattern 30), one item per resident per night, for the doctors'
"Nightly summary" section. Input (criterion 4):

- Lambda's on-success invocation record from `silvercare-summaries` (its async runs):
  `responsePayload` is summaries' return value, `{"date": D, "summaries": n}`. With
  `summaries: 0` nothing was written, so this logs it and returns without reading S3.
- {"date": "yyyy-mm-dd"}: that Singapore date (a manual run or backfill).

Anything else, or a date that isn't a real date, raises before any AWS call, and the
error never repeats the value.

Steps: list `summaries/date=<D>/` (paginated), read each object, validate it (one JSON
object of the curated-layer summary's fields, `schema_version` 1, `resident_id`
`^res-[0-9]{4}$`, `date` == D), and write each valid one with an unconditional PutItem
(a re-run overwrites the same key: idempotent). Invalid objects are skipped and logged
by key only; after the valid ones are written the run raises, so the errors alarm
fires. No objects for D is a warning, not an error.

Logs: date, counts (listed, loaded, skipped), skipped keys. Never summary values,
thresholds, or object bodies (criterion 11).
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta, timezone
from datetime import date as date_cls
from decimal import Decimal
from functools import cache
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeSerializer
from botocore.config import Config

# The service name comes from POWERTOOLS_SERVICE_NAME, set by Terraform.
logger = Logger()

SCHEMA_VERSION = 1
SUCCESS_CONDITION = "Success"
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
RESIDENT_ID_RE = re.compile(r"res-[0-9]{4}")
# summaries writes summaries/date=<D>/<uuid4>.json (curated-layer criterion 15). Any
# other key is logged as `invalid`: a key is free text and could carry data.
KEY_NAME_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.json"
)
# A summary is well under 2 KB; anything far bigger isn't one.
MAX_OBJECT_BYTES = 64 * 1024
LIST_PAGE = 1000
SGT = timezone(timedelta(hours=8))
# Items live for 30 nights after their own (criterion 2).
EXPIRY_DAYS = 31

# The curated-layer summary's fields (curated-layer.md criterion 11) that go into the
# item's `summary` map, by kind. `resident_id`, `date`, `generated_at`, and
# `schema_version` are checked but not copied into the map.
INT_OR_NULL = (
    "hr_bpm_min",
    "hr_bpm_max",
    "spo2_pct_min",
    "spo2_pct_max",
    "bed_minutes",
)
NUMBER_OR_NULL = (
    "hr_bpm_avg",
    "spo2_pct_avg",
    "skin_temp_c_min",
    "skin_temp_c_avg",
    "skin_temp_c_max",
    "activity_pct_avg",
)
INT_COUNTS = (
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
)
# metric -> the limits it may have (SpO2 has no max).
THRESHOLD_LIMITS = {
    "hr_bpm": ("min", "max"),
    "spo2_pct": ("min",),
    "skin_temp_c": ("min", "max"),
}
NOT_COPIED = ("resident_id", "date", "generated_at", "schema_version")
SUMMARY_FIELDS = frozenset(
    (*INT_OR_NULL, *NUMBER_OR_NULL, *INT_COUNTS, "thresholds", *NOT_COPIED)
)

_serializer = TypeSerializer()


class InvalidSummary(Exception):
    """An object under the date's prefix isn't a valid summary. Never carries a value."""


class SkippedObjectsError(Exception):
    """Some objects were skipped; raised after the valid ones were written."""


# ---- clients (one per container, created on first use) ----


@cache
def s3_client() -> Any:
    return boto3.client("s3")


@cache
def dynamodb_client() -> Any:
    """Adaptive retries, so throttled writes on the 1 WCU table back off and retry
    (criterion 6). DYNAMODB_ENDPOINT points at DynamoDB Local in integration tests; it
    is unset in AWS."""
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


def parse_event(event: Any) -> tuple[str, int | None]:
    """(date, summaries count or None) from either input form (criterion 4). The count
    is only in the on-success record; a manual run gives None."""
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
        count = payload.get("summaries")
        if type(count) is not int or count < 0:
            raise ValueError("invocation record has no valid responsePayload.summaries")
        return parse_date(payload["date"]), count
    if set(event) == {"date"}:
        return parse_date(event["date"]), None
    raise ValueError(
        'event must be {"date": "yyyy-mm-dd"} or an on-success invocation record'
    )


# ---- validation and item (pure, no AWS) ----


def summary_prefix(date: str) -> str:
    return f"summaries/date={parse_date(date)}/"


def loggable_key(key: Any, date: str) -> str:
    """The key itself if it has the form summaries writes, else `invalid`."""
    prefix = summary_prefix(date)
    if (
        isinstance(key, str)
        and key.startswith(prefix)
        and KEY_NAME_RE.fullmatch(key[len(prefix) :])
    ):
        return key
    return "invalid"


def _reject_constant(_: str) -> Any:
    raise InvalidSummary("not finite")


def _is_int(value: Any) -> bool:
    return type(value) is int


def _is_number(value: Any) -> bool:
    return type(value) is int or (isinstance(value, Decimal) and value.is_finite())


def parse_summary(body: bytes, date: str) -> dict[str, Any]:
    """The summary object, with floats as Decimal. Raises InvalidSummary (no value in
    the message) unless it is one JSON object with exactly the summary's fields of the
    right types, `schema_version` 1, a valid `resident_id`, and `date` == D."""
    try:
        text = body.decode("utf-8")
        obj = json.loads(text, parse_float=Decimal, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise InvalidSummary("not JSON") from None
    if not isinstance(obj, dict):
        raise InvalidSummary("not an object")
    if (
        not _is_int(obj.get("schema_version"))
        or obj["schema_version"] != SCHEMA_VERSION
    ):
        raise InvalidSummary("schema_version")
    resident_id = obj.get("resident_id")
    if not isinstance(resident_id, str) or not RESIDENT_ID_RE.fullmatch(resident_id):
        raise InvalidSummary("resident_id")
    if obj.get("date") != date:
        raise InvalidSummary("date")
    if set(obj) != SUMMARY_FIELDS:
        raise InvalidSummary("fields")
    if not isinstance(obj.get("generated_at"), str):
        raise InvalidSummary("generated_at")
    for name in INT_OR_NULL:
        if obj[name] is not None and not _is_int(obj[name]):
            raise InvalidSummary(name)
    for name in NUMBER_OR_NULL:
        if obj[name] is not None and not _is_number(obj[name]):
            raise InvalidSummary(name)
    for name in INT_COUNTS:
        if not _is_int(obj[name]) or obj[name] < 0:
            raise InvalidSummary(name)
    thresholds = obj["thresholds"]
    if not isinstance(thresholds, dict) or set(thresholds) != set(THRESHOLD_LIMITS):
        raise InvalidSummary("thresholds")
    for metric, allowed in THRESHOLD_LIMITS.items():
        limits = thresholds[metric]
        if not isinstance(limits, dict) or not set(limits) <= set(allowed):
            raise InvalidSummary("thresholds")
        if not all(_is_number(v) for v in limits.values()):
            raise InvalidSummary("thresholds")
    return obj


def expires_at(date: str) -> int:
    """Epoch seconds of 00:00 SGT on `date` + 31 days (criterion 2)."""
    night = date_cls.fromisoformat(parse_date(date))
    start = datetime(night.year, night.month, night.day, tzinfo=SGT)
    return int((start + timedelta(days=EXPIRY_DAYS)).timestamp())


def iso_ms(moment: datetime) -> str:
    """ISO 8601 UTC with milliseconds, e.g. 2026-11-19T00:41:07.512Z."""
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def build_item(summary: dict[str, Any], loaded_at: datetime) -> dict[str, Any]:
    """The Results item (criterion 2), as a low-level DynamoDB item. Raises
    InvalidSummary if a number can't be stored in DynamoDB."""
    resident_id = summary["resident_id"]
    date = summary["date"]
    values = {
        "pk": f"RESIDENT#{resident_id}",
        "sk": f"NIGHT#{date}",
        "resident_id": resident_id,
        "date": date,
        "summary": {k: v for k, v in summary.items() if k not in NOT_COPIED},
        "schema_version": summary["schema_version"],
        "loaded_at": iso_ms(loaded_at),
        "expires_at": expires_at(date),
    }
    try:
        return {k: _serializer.serialize(v) for k, v in values.items()}
    except (TypeError, ValueError, ArithmeticError):
        # e.g. a number with more than 38 digits of precision.
        raise InvalidSummary("number out of range") from None


# ---- AWS ----


def list_keys(date: str) -> list[str]:
    """Every object key under summaries/date=<D>/, following continuation tokens."""
    pages = (
        s3_client()
        .get_paginator("list_objects_v2")
        .paginate(
            Bucket=os.environ["HISTORY_BUCKET"],
            Prefix=summary_prefix(date),
            PaginationConfig={"PageSize": LIST_PAGE},
        )
    )
    return [item["Key"] for page in pages for item in page.get("Contents", [])]


def read_object(key: str) -> bytes:
    """The object's body. Raises InvalidSummary if it is too big to be a summary."""
    response = s3_client().get_object(Bucket=os.environ["HISTORY_BUCKET"], Key=key)
    body = response["Body"]
    try:
        data = body.read(MAX_OBJECT_BYTES + 1)
    finally:
        body.close()
    if len(data) > MAX_OBJECT_BYTES:
        raise InvalidSummary("too big")
    return data


def put_item(item: dict[str, Any]) -> None:
    """Unconditional PutItem: a re-run overwrites the same key (criterion 6)."""
    dynamodb_client().put_item(TableName=os.environ["RESULTS_TABLE"], Item=item)


def now_utc() -> datetime:
    return datetime.now(UTC)


# ---- the job ----


def load(date: str) -> int:
    """Load Singapore date D's summaries. Returns how many were written; raises
    SkippedObjectsError after writing if any object was skipped (criterion 7)."""
    keys = list_keys(date)
    if not keys:
        logger.warning("no summaries for date", extra={"date": date, "listed": 0})
        return 0
    loaded = 0
    skipped: list[str] = []
    for key in keys:
        try:
            summary = parse_summary(read_object(key), date)
            item = build_item(summary, now_utc())
        except InvalidSummary:
            skipped.append(loggable_key(key, date))
            continue
        put_item(item)
        loaded += 1
    counts = {
        "date": date,
        "listed": len(keys),
        "loaded": loaded,
        "skipped": len(skipped),
    }
    if skipped:
        logger.error("summaries skipped", extra={**counts, "skipped_keys": skipped})
        raise SkippedObjectsError(
            f"{len(skipped)} of {len(keys)} summary objects were skipped"
        )
    logger.info("results loaded", extra=counts)
    return loaded


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    date, count = parse_event(event)
    if count == 0:
        logger.info("summaries wrote nothing: nothing to load", extra={"date": date})
        return {"date": date, "loaded": 0}
    logger.info("results started", extra={"date": date})
    loaded = load(date)
    return {"date": date, "loaded": loaded}
