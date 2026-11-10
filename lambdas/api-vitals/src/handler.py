"""GET /residents/{resident_id}/vitals (docs/features/vitals-trends.md): a resident's
vitals trends per Singapore hour or day, for doctors. Behind the HTTP API's Cognito JWT
authorizer (infra/api.tf), payload 2.0. Built like lambdas/api-nightly/.

Check order (criteria 1-5): the caller's groups (`doctor`, else 403), then the path and
query parameters (else 400, before any AWS call), then the reads. Doctors see every
resident, so there is no link check: an unknown resident returns 200 with empty rows.

- `interval=day` (criteria 7-8, pattern 35): one Athena query of `daily_summaries`.
- `interval=hour` (criteria 9-13, pattern 36): registry `by-resident` Query (pattern
  10), BatchGetItem of the devices' ASSIGNMENT items and THRESHOLDS#<id> (patterns 29
  and 18), then one Athena query of the clean tables. With no assigned device, no query.

Queries run in the `silvercare-vitals` workgroup, polled every second; one still
running at 25 s is stopped and the route returns 504 (criterion 14). A query's failure
reason is never logged: Athena's text can quote data.

Logs: outcome, route, request ID, the caller's `sub`, the opaque `resident_id`,
`interval`, the number of days, query ID and state, counts. Never values, thresholds,
result rows, or the SQL text.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import UTC, date, datetime, timedelta, timezone
from functools import cache
from typing import Any

import boto3
import registry
import sql
import thresholds
import vitals_sql
from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config

logger = Logger()

LOG_MESSAGE = "get vitals"
ROUTE = "GET /residents/{resident_id}/vitals"
DOCTOR = "doctor"
GROUP_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}\Z")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9=+/_-]{1,64}\Z")
SUB_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
RESIDENT_ID_RE = re.compile(r"^res-[0-9]{4}\Z")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
HEADERS = {"Content-Type": "application/json", "Cache-Control": "no-store"}
SGT = timezone(timedelta(hours=8))
LOG_KEYS = ["request_id", "route", "sub", "resident_id", "interval", "days", "cache"]

# Decisions (user, 2026-11-09).
MAX_DAYS = {"day": 90, "hour": 7}
HOURLY_MAX_AGE_DAYS = 365

POLL_SECONDS = 1.0
QUERY_DEADLINE_S = 25.0
RESULTS_PAGE = 1000
BATCH_MAX_ATTEMPTS = 5
SUCCEEDED = "SUCCEEDED"
FINAL_STATES = frozenset({SUCCEEDED, "FAILED", "CANCELLED"})
KINDS = dict(sql.RESULT_COLUMNS)
EVENT_COUNTS = ("motion_events", "falls", "vitals_alert_episodes", "cancels")

# Results cache (docs/features/api-caching.md, criteria 5-11). Athena data only changes
# when clean (08:30 SGT) and then summaries run; 09:30 leaves room for both, retries
# included. Keyed by the query text, which holds the resident, devices, limits, dates.
CACHE_REFRESH_SGT = (9, 30)
CACHE_MAX_ENTRIES = 200
_results: dict[str, tuple[datetime, list[dict[str, str | None]]]] = {}

_deserializer = TypeDeserializer()


class QueryTimeoutError(Exception):
    """The query was still running at the deadline and has been stopped."""


class QueryFailedError(Exception):
    """The query ended in a state other than SUCCEEDED."""


class BadRowError(Exception):
    """A result row isn't well formed. Never carries a value."""


# ---- clients ----


@cache
def athena_client() -> Any:
    return boto3.client("athena", config=Config(retries={"mode": "standard"}))


@cache
def dynamodb_client() -> Any:
    return boto3.client(
        "dynamodb",
        config=Config(retries={"mode": "adaptive"}),
        endpoint_url=os.environ.get("DYNAMODB_ENDPOINT"),
    )


# ---- request ----


def parse_groups(value: Any) -> frozenset[str]:
    if isinstance(value, list):
        parts = value
    elif isinstance(value, str):
        parts = re.split(r"[\s,]+", value.strip().strip("[]"))
    else:
        return frozenset()
    return frozenset(p for p in parts if isinstance(p, str) and GROUP_RE.match(p))


def claims_of(event: Any) -> dict[str, Any]:
    try:
        claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
    except (KeyError, TypeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def request_id_of(event: Any) -> str:
    try:
        value = event["requestContext"]["requestId"]
    except (KeyError, TypeError):
        return "invalid"
    return value if isinstance(value, str) and REQUEST_ID_RE.match(value) else "invalid"


def response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": dict(HEADERS),
        "body": json.dumps(body, separators=(",", ":")),
    }


def error(status: int, code: str, message: str) -> dict[str, Any]:
    return response(status, {"error": {"code": code, "message": message}})


def _error_code(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = resp.get("Error", {}).get("Code")
        if isinstance(code, str):
            return code
    return type(exc).__name__


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or not DATE_RE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def check_params(
    params: dict[str, Any], now: datetime
) -> tuple[str, date, date] | tuple[str, str]:
    """(interval, from, to), or (error code, message) for a 400 (criteria 2-5)."""
    interval = params.get("interval")
    start, end = _date(params.get("from")), _date(params.get("to"))
    if interval not in MAX_DAYS or start is None or end is None or start > end:
        return (
            "invalid_request",
            "Give interval (hour or day), from, and to (yyyy-mm-dd).",
        )
    today = now.astimezone(SGT).date()
    if end > today - timedelta(days=1):
        return "out_of_range", "Data is available up to yesterday."
    if (end - start).days + 1 > MAX_DAYS[interval]:
        return "range_too_long", f"At most {MAX_DAYS[interval]} days at a time."
    if interval == "hour" and start < today - timedelta(days=HOURLY_MAX_AGE_DAYS):
        return "out_of_range", "Hourly data covers the last 365 days."
    return interval, start, end


# ---- rows (pure, no AWS) ----


def _value(text: str | None, kind: str) -> int | float | None:
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError:
        raise BadRowError from None
    if not math.isfinite(number):
        raise BadRowError
    if kind == "int":
        if not number.is_integer():
            raise BadRowError
        return int(number)
    return round(number, 1)


def _values(row: dict[str, str | None]) -> dict[str, Any]:
    return {
        name: _value(row.get(name), KINDS[name]) for name in vitals_sql.SUMMARY_COLUMNS
    }


def _empty() -> dict[str, Any]:
    return dict.fromkeys(vitals_sql.SUMMARY_COLUMNS)


def _row_thresholds(row: dict[str, str | None]) -> dict[str, dict[str, int | float]]:
    out: dict[str, dict[str, int | float]] = {m: {} for m in thresholds.METRICS}
    for column, (metric, name) in vitals_sql.THRESHOLD_COLUMNS.items():
        value = _value(row.get(column), "int" if metric != "skin_temp_c" else "float")
        if value is not None:
            out[metric][name] = value
    return out


def day_rows(
    rows: list[dict[str, str | None]], start: date, end: date
) -> tuple[list[dict[str, Any]], int]:
    """One row per date, oldest first; dates without a summary have has_data false.
    Returns (rows, invalid rows skipped)."""
    found: dict[str, dict[str, Any]] = {}
    invalid = 0
    for row in rows:
        day = _date(row.get("date"))
        if day is None or not start <= day <= end:
            invalid += 1
            continue
        try:
            values = _values(row) | {"thresholds": _row_thresholds(row)}
        except BadRowError:
            invalid += 1
            continue
        found[day.isoformat()] = values  # ordered by generated_at: the newest wins
    out = []
    for i in range((end - start).days + 1):
        key = (start + timedelta(days=i)).isoformat()
        values = found.get(key)
        has_data = values is not None
        out.append(
            {"date": key, "has_data": has_data}
            | (values if has_data else _empty() | {"thresholds": None})
        )
    return out, invalid


def hour_label(start_utc: datetime) -> str:
    return start_utc.astimezone(SGT).isoformat()


def _has_data(values: dict[str, Any]) -> bool:
    return (
        (values["vitals_readings"] or 0) > 0
        or values["bed_minutes"] is not None
        or any((values[c] or 0) > 0 for c in EVENT_COUNTS)
    )


def hour_rows(
    rows: list[dict[str, str | None]], start: date, end: date
) -> tuple[list[dict[str, Any]], int]:
    """One row per Singapore hour, oldest first. Returns (rows, invalid rows skipped)."""
    window_start = datetime.combine(start, datetime.min.time(), SGT).astimezone(UTC)
    hours = ((end - start).days + 1) * 24
    found: dict[datetime, dict[str, Any]] = {}
    invalid = 0
    for row in rows:
        try:
            hour = datetime.strptime(row.get("hour_utc") or "", "%Y-%m-%d %H:%M:%S")
            found[hour.replace(tzinfo=UTC)] = _values(row)
        except (ValueError, BadRowError):
            invalid += 1
    out = []
    for i in range(hours):
        hour = window_start + timedelta(hours=i)
        values = found.get(hour)
        if values is None:
            values = _empty()
        out.append({"hour": hour_label(hour), "has_data": _has_data(values)} | values)
    return out, invalid


# ---- registry ----


def _deserialize(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _deserializer.deserialize(v) for k, v in item.items()}


def resident_devices(resident_id: str) -> list[str]:
    """Pattern 10: device IDs the by-resident index lists for the resident."""
    request: dict[str, Any] = {
        "TableName": os.environ["REGISTRY_TABLE"],
        "IndexName": "by-resident",
        "Select": "SPECIFIC_ATTRIBUTES",
        "KeyConditionExpression": "#g = :g AND begins_with(#s, :device)",
        "ExpressionAttributeNames": {"#g": "gsi1_pk", "#s": "gsi1_sk"},
        "ExpressionAttributeValues": {
            ":g": {"S": f"RESIDENT#{resident_id}"},
            ":device": {"S": registry.DEVICE_PREFIX},
        },
        "ProjectionExpression": "#g, #s",
    }
    ids: set[str] = set()
    while True:
        result = dynamodb_client().query(**request)
        for item in result.get("Items", []):
            sk = item.get("gsi1_sk", {}).get("S")
            device_id = registry.device_id_from_pk(sk)
            if registry.is_device_id(device_id):
                ids.add(device_id)
        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            return sorted(ids)
        request["ExclusiveStartKey"] = last_key


REGISTRY_PROJECTION = [
    "pk",
    "sk",
    "resident_id",
    "device_type",
    "assigned_at",
    "hr_bpm",
    "spo2_pct",
    "skin_temp_c",
]


def batch_get(keys: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Low-level items for (pk, sk) keys (at most 100 here), UnprocessedKeys retried."""
    table = os.environ["REGISTRY_TABLE"]
    names = {f"#a{i}": n for i, n in enumerate(REGISTRY_PROJECTION)}
    request: dict[str, Any] = {
        table: {
            "Keys": [{"pk": {"S": pk}, "sk": {"S": sk}} for pk, sk in keys],
            "ProjectionExpression": ", ".join(names),
            "ExpressionAttributeNames": names,
            "ConsistentRead": False,
        }
    }
    found: list[dict[str, Any]] = []
    for attempt in range(BATCH_MAX_ATTEMPTS):
        result = dynamodb_client().batch_get_item(RequestItems=request)
        found.extend(result.get("Responses", {}).get(table, []))
        request = result.get("UnprocessedKeys") or {}
        if not request:
            return found
        time.sleep(0.05 * 2**attempt)
    raise RuntimeError("UnprocessedKeys")


def load_hourly_inputs(
    resident_id: str,
) -> tuple[list[registry.Device], dict[str, thresholds.Limits]]:
    """The resident's currently assigned devices and resolved thresholds."""
    device_ids = resident_devices(resident_id)[:99]  # a resident has two devices
    keys = [(f"THRESHOLDS#{resident_id}", "THRESHOLDS")]
    keys += [
        (f"{registry.DEVICE_PREFIX}{d}", registry.ASSIGNMENT_SK) for d in device_ids
    ]
    items = batch_get(keys)
    threshold_item = next(
        (i for i in items if i.get("pk", {}).get("S") == keys[0][0]), None
    )
    limits, invalid = thresholds.limits_from_item(threshold_item)
    if invalid:
        logger.warning(
            LOG_MESSAGE, extra={"outcome": "invalid_thresholds", "fields": invalid}
        )
    devices = []
    for item in items:
        if item is threshold_item:
            continue
        device = registry.device_from_item(_deserialize(item))
        # The base item is the truth: the index may still list a moved device.
        if device is not None and device.resident_id == resident_id:
            devices.append(device)
    return devices, limits


# ---- Athena ----


def run_query(query: str, deadline: float) -> list[dict[str, str | None]]:
    """Run one query in the vitals workgroup; its rows as {column: text or None}."""
    sql.check_size(query)
    athena = athena_client()
    query_id = athena.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": os.environ["GLUE_DATABASE"]},
        WorkGroup=os.environ["ATHENA_WORKGROUP"],
    )["QueryExecutionId"]
    while True:
        state = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"][
            "Status"
        ]["State"]
        if state in FINAL_STATES:
            break
        if time.monotonic() >= deadline:
            athena.stop_query_execution(QueryExecutionId=query_id)
            logger.warning(
                LOG_MESSAGE, extra={"outcome": "timeout", "query_id": query_id}
            )
            raise QueryTimeoutError
        time.sleep(POLL_SECONDS)
    logger.info("query finished", extra={"query_id": query_id, "state": state})
    if state != SUCCEEDED:
        raise QueryFailedError
    pages = athena.get_paginator("get_query_results").paginate(
        QueryExecutionId=query_id, PaginationConfig={"PageSize": RESULTS_PAGE}
    )
    rows = [row["Data"] for page in pages for row in page["ResultSet"]["Rows"]]
    if not rows:
        return []
    header = [cell.get("VarCharValue") for cell in rows[0]]
    return [
        dict(zip(header, (cell.get("VarCharValue") for cell in row), strict=True))
        for row in rows[1:]
    ]


def clear_cache() -> None:
    _results.clear()


def cache_expiry(stored: datetime) -> datetime:
    """The first 09:30 SGT strictly after `stored`."""
    local = stored.astimezone(SGT)
    hour, minute = CACHE_REFRESH_SGT
    refresh = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return refresh if refresh > local else refresh + timedelta(days=1)


def cached_query(
    query: str, now: datetime, deadline: float
) -> list[dict[str, str | None]]:
    """run_query through the cache. A failed or timed-out query raises: nothing stored."""
    hit = _results.get(query)
    if hit is not None and now < hit[0]:
        logger.append_keys(cache="hit")
        return hit[1]
    rows = run_query(query, deadline)
    _results.pop(query, None)
    while len(_results) >= CACHE_MAX_ENTRIES:
        del _results[next(iter(_results))]  # oldest stored first
    _results[query] = (cache_expiry(now), rows)
    logger.append_keys(cache="miss")
    return rows


# ---- handler ----


def handle(event: Any, now: datetime, deadline: float) -> dict[str, Any]:
    # A warm container keeps appended keys: drop the last request's first.
    logger.remove_keys(LOG_KEYS)
    logger.append_keys(request_id=request_id_of(event), route=ROUTE)
    claims = claims_of(event)
    sub = claims.get("sub")
    logger.append_keys(
        sub=sub if isinstance(sub, str) and SUB_RE.match(sub) else "invalid"
    )
    groups = parse_groups(claims.get("cognito:groups"))
    if DOCTOR not in groups:
        logger.warning(
            LOG_MESSAGE, extra={"outcome": "forbidden", "groups": sorted(groups)}
        )
        return error(403, "forbidden", "Only doctors can see vitals trends.")
    resident_id = (event.get("pathParameters") or {}).get("resident_id")
    if not isinstance(resident_id, str) or not RESIDENT_ID_RE.match(resident_id):
        logger.info(LOG_MESSAGE, extra={"outcome": "invalid_request"})
        return error(400, "invalid_request", "The resident ID is not valid.")
    logger.append_keys(resident_id=resident_id)
    checked = check_params(event.get("queryStringParameters") or {}, now)
    if len(checked) == 2:
        code, message = checked
        logger.info(LOG_MESSAGE, extra={"outcome": code})
        return error(400, code, message)
    interval, start, end = checked
    logger.append_keys(interval=interval, days=(end - start).days + 1)

    if interval == "day":
        query = vitals_sql.daily_query(resident_id, start.isoformat(), end.isoformat())
        rows, invalid = day_rows(cached_query(query, now, deadline), start, end)
        used = None
    else:
        devices, limits = load_hourly_inputs(resident_id)
        result: list[dict[str, str | None]] = []
        if devices:
            query = vitals_sql.hourly_query(
                resident_id, devices, limits, start.isoformat(), end.isoformat()
            )
            result = cached_query(query, now, deadline)
        rows, invalid = hour_rows(result, start, end)
        used = thresholds.as_json(limits)
    if invalid:
        logger.warning(LOG_MESSAGE, extra={"outcome": "invalid_rows", "rows": invalid})
    body = {
        "resident_id": resident_id,
        "interval": interval,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "thresholds": used,
        "rows": rows,
    }
    logger.info(
        LOG_MESSAGE,
        extra={"outcome": "ok", "rows": sum(1 for r in rows if r["has_data"])},
    )
    return response(200, body)


def now_utc() -> datetime:
    return datetime.now(UTC)


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    # Keep-warm ping (docs/features/api-caching.md): API Gateway events never have a
    # top-level `source`, so only the EventBridge rule can send this. No checks, no logs.
    if event.get("source") == "silvercare.keep-warm":
        return {"warm": True}
    deadline = time.monotonic() + QUERY_DEADLINE_S
    try:
        return handle(event, now_utc(), deadline)
    except QueryTimeoutError:
        return error(504, "query_timeout", "This took too long. Try a shorter range.")
    except Exception as exc:  # noqa: BLE001 - every failure becomes a clean 500
        logger.error(LOG_MESSAGE, extra={"outcome": "error", "error": _error_code(exc)})
        return error(500, "internal_error", "Could not load the vitals trends.")
