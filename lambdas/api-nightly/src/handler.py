"""GET /residents/{resident_id}/nightly (docs/features/nightly-results.md): a resident's
stored nights from the `silvercare-results` table (docs/data-model.md, pattern 31), for
the doctor view's "Nightly summary" section. Behind the HTTP API's Cognito JWT
authorizer (infra/api.tf), payload 2.0. Built like lambdas/api-residents/.

Check order (criterion 16): the caller's groups first (`doctor` required, else 403),
then `resident_id` (`^res-[0-9]{4}$`, else 400), then the read. Doctors see every
resident, so there is no link check and no profile read: an unknown resident returns
200 with no nights.

The read (criterion 17): Query `pk = RESIDENT#<id>`, `begins_with(sk, "NIGHT#")`, newest
first, eventually consistent, paginated. Nights older than 30 days before today's
Singapore date are dropped, because TTL deletes expired items late.

`expected_date` is yesterday's Singapore date; `not_ready` is true only at 09:30 SGT or
later when the newest night isn't `expected_date` (or there are none) (criterion 18).

Logs: outcome, route, request ID, the caller's `sub`, the opaque `resident_id`, the
number of nights, and on a 403 `cognito:groups`. Never summary values or thresholds.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, time, timedelta, timezone
from datetime import date as date_cls
from decimal import Decimal
from functools import cache
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config

logger = Logger()

LOG_MESSAGE = "get nightly"
ROUTE = "GET /residents/{resident_id}/nightly"
DOCTOR = "doctor"
GROUP_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}\Z")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9=+/_-]{1,64}\Z")
SUB_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
RESIDENT_ID_RE = re.compile(r"^res-[0-9]{4}\Z")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
NIGHT_PREFIX = "NIGHT#"
HEADERS = {"Content-Type": "application/json", "Cache-Control": "no-store"}
SGT = timezone(timedelta(hours=8))
READY_AT = time(9, 30)
KEEP_DAYS = 30

# The night's fields in the response, from the item's `summary` map (the curated-layer
# summary, curated-layer.md criterion 11), by JSON type.
INT_FIELDS = (
    "hr_bpm_min",
    "hr_bpm_max",
    "spo2_pct_min",
    "spo2_pct_max",
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
)
FLOAT_FIELDS = (
    "hr_bpm_avg",
    "spo2_pct_avg",
    "skin_temp_c_min",
    "skin_temp_c_avg",
    "skin_temp_c_max",
    "activity_pct_avg",
)
# The order of the contract's example.
NIGHT_FIELDS = (
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
    "thresholds",
    "bed_minutes",
    "motion_events",
    "falls",
    "vitals_alert_episodes",
    "cancels",
)
# metric -> (limits it may have, integer?)
THRESHOLD_LIMITS = {
    "hr_bpm": (("min", "max"), True),
    "spo2_pct": (("min",), True),
    "skin_temp_c": (("min", "max"), False),
}
PROJECTION = ("pk", "sk", "summary")
LOG_KEYS = ["request_id", "route", "sub", "resident_id"]

_deserializer = TypeDeserializer()


class InvalidNight(Exception):
    """A stored item doesn't have the night's shape. Never carries a value."""


# ---- clients ----


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


# ---- nights (pure, no AWS) ----


def _number(value: Any, integer: bool) -> int | float:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvalidNight
    if integer:
        if value != value.to_integral_value():
            raise InvalidNight
        return int(value)
    return float(value)


def _nullable(value: Any, integer: bool) -> int | float | None:
    return None if value is None else _number(value, integer)


def _thresholds(value: Any) -> dict[str, dict[str, int | float]]:
    if not isinstance(value, dict):
        raise InvalidNight
    out: dict[str, dict[str, int | float]] = {}
    for metric, (allowed, integer) in THRESHOLD_LIMITS.items():
        limits = value.get(metric)
        if not isinstance(limits, dict) or not set(limits) <= set(allowed):
            raise InvalidNight
        out[metric] = {k: _number(limits[k], integer) for k in allowed if k in limits}
    return out


def night_from_item(item: dict[str, Any]) -> dict[str, Any]:
    """One response night from a deserialized item: `date` plus the summary's fields,
    built explicitly (never the raw item). Raises InvalidNight."""
    sk = item.get("sk")
    if not isinstance(sk, str) or not sk.startswith(NIGHT_PREFIX):
        raise InvalidNight
    date = sk.removeprefix(NIGHT_PREFIX)
    if not DATE_RE.match(date):
        raise InvalidNight
    try:
        date_cls.fromisoformat(date)
    except ValueError:
        raise InvalidNight from None
    summary = item.get("summary")
    if not isinstance(summary, dict):
        raise InvalidNight
    values: dict[str, Any] = {}
    for name in INT_FIELDS:
        values[name] = _nullable(summary.get(name), True)
    for name in FLOAT_FIELDS:
        values[name] = _nullable(summary.get(name), False)
    values["thresholds"] = _thresholds(summary.get("thresholds"))
    return {"date": date, **{name: values[name] for name in NIGHT_FIELDS}}


def sgt_now(now: datetime) -> datetime:
    return now.astimezone(SGT)


def expected_date(now: datetime) -> str:
    """Yesterday's Singapore date."""
    return (sgt_now(now).date() - timedelta(days=1)).isoformat()


def cutoff_date(now: datetime) -> str:
    """The oldest night still shown: 30 days before today's Singapore date."""
    return (sgt_now(now).date() - timedelta(days=KEEP_DAYS)).isoformat()


def is_not_ready(nights: list[dict[str, Any]], now: datetime) -> bool:
    """True only from 09:30 SGT when the newest night isn't yesterday (or none)."""
    if sgt_now(now).time() < READY_AT:
        return False
    return not nights or nights[0]["date"] != expected_date(now)


def build_nights(
    items: list[dict[str, Any]], now: datetime
) -> tuple[list[dict[str, Any]], int]:
    """(nights newest first, within the last 30 days; number of invalid items)."""
    cutoff = cutoff_date(now)
    nights: list[dict[str, Any]] = []
    invalid = 0
    for item in items:
        try:
            night = night_from_item(item)
        except InvalidNight:
            invalid += 1
            continue
        if night["date"] >= cutoff:
            nights.append(night)
    nights.sort(key=lambda n: n["date"], reverse=True)
    return nights, invalid


# ---- read ----


def _deserialize(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _deserializer.deserialize(v) for k, v in item.items()}


def query_nights(resident_id: str) -> list[dict[str, Any]]:
    """Pattern 31: the resident's stored nights, newest first, eventually consistent,
    following LastEvaluatedKey."""
    client = dynamodb_client()
    names = {f"#a{i}": name for i, name in enumerate(PROJECTION)}
    request: dict[str, Any] = {
        "TableName": os.environ["RESULTS_TABLE"],
        "Select": "SPECIFIC_ATTRIBUTES",
        "ConsistentRead": False,
        "ScanIndexForward": False,
        "KeyConditionExpression": "#a0 = :pk AND begins_with(#a1, :night)",
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": {
            ":pk": {"S": f"RESIDENT#{resident_id}"},
            ":night": {"S": NIGHT_PREFIX},
        },
        "ProjectionExpression": ", ".join(names),
    }
    items: list[dict[str, Any]] = []
    while True:
        result = client.query(**request)
        items.extend(_deserialize(i) for i in result.get("Items", []))
        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            return items
        request["ExclusiveStartKey"] = last_key


# ---- handler ----


def handle(event: Any, now: datetime) -> dict[str, Any]:
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
        return error(403, "forbidden", "Only doctors can see nightly summaries.")
    resident_id = (event.get("pathParameters") or {}).get("resident_id")
    if not isinstance(resident_id, str) or not RESIDENT_ID_RE.match(resident_id):
        logger.info(LOG_MESSAGE, extra={"outcome": "invalid_resident_id"})
        return error(400, "invalid_resident_id", "The resident ID is not valid.")
    logger.append_keys(resident_id=resident_id)
    nights, invalid = build_nights(query_nights(resident_id), now)
    if invalid:
        logger.warning(
            LOG_MESSAGE, extra={"outcome": "invalid_items", "items": invalid}
        )
    body = {
        "resident_id": resident_id,
        "expected_date": expected_date(now),
        "not_ready": is_not_ready(nights, now),
        "nights": nights,
    }
    logger.info(LOG_MESSAGE, extra={"outcome": "ok", "nights": len(nights)})
    return response(200, body)


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    try:
        return handle(event, datetime.now(UTC))
    except Exception as exc:  # noqa: BLE001 - every failure becomes a clean 500
        logger.error(LOG_MESSAGE, extra={"outcome": "error", "error": _error_code(exc)})
        return error(500, "internal_error", "Could not load the nightly summaries.")
