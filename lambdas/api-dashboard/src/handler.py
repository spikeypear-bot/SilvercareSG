"""GET /dashboard: the nurse and doctor dashboards (docs/data-model.md, Dashboard;
patterns 9, 5, 8, and 4). Behind the HTTP API's Cognito JWT authorizer (infra/api.tf),
payload 2.0. Built like GET /devices (lambdas/api-devices/).

Steps (the api-route skill):

1. Read `sub` and `cognito:groups` from the JWT claims (never from headers or body).
2. Staff only: `doctor` or `nurse`, else 403. Staff see every resident, so there is no
   resident link check.
3. The route takes no query string and no body: either gives 400.
4. Eventually consistent, paginated Queries, each projecting only what the response
   needs:
   - alerts `open` `open_pk = OPEN`, newest first (pattern 9): the alert-row fields
     and `notified_at` (for `escalates_at`), never `details`;
   - registry `by-type` `entity_type = RESIDENT` (pattern 8): pk, display_name,
     home_id, never `photo_key`;
   - registry `by-type` `entity_type = DEVICE` (pattern 4): which devices each
     resident has;
   - latest `by-message` `watch_type = status` and `vitals` (pattern 5): the newest
     entry's fields the cards show, never `received_at`, `clock_skew`, or
     `message_id`. `bed` and `motion` aren't read: the dashboards don't show them.
5. Resolved thresholds (BatchGetItem `THRESHOLDS#<resident_id>`, cached 5 minutes per
   container, as telemetry's pattern 2) only for residents whose status depends on a
   reading (no open alert).
6. Caregiver names from Cognito (AdminGetUser, actors.py) only for caregivers who acknowledged a
   device problem.
7. The response is built field by field (dashboard.py), never from raw items.
8. Errors are {"error": {"code", "message"}} with no internal details.

Logs: outcome, counts, API Gateway's request ID, the caller's `sub` and, on a 403,
`cognito:groups`; resident IDs and field names (never values) for invalid thresholds.
Never the request, the response, other claims, names, notes, or readings.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import cache
from typing import Any

import actors
import boto3
import dashboard
import thresholds
from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config

# The service name comes from POWERTOOLS_SERVICE_NAME, set by Terraform.
logger = Logger()

LOG_MESSAGE = "get dashboard"
REGISTRY_INDEX = "by-type"
LATEST_INDEX = "by-message"
ALERTS_INDEX = "open"
STAFF_GROUPS = frozenset({"doctor", "nurse"})
SUB_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
GROUP_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}\Z")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9=+/_-]{1,64}\Z")

THRESHOLDS_CACHE_TTL_S = 300.0
BATCH_GET_MAX_KEYS = 100
BATCH_GET_MAX_ATTEMPTS = 4

HEADERS = {"Content-Type": "application/json", "Cache-Control": "no-store"}

_deserializer = TypeDeserializer()

# resident_id -> (resolved limits, monotonic expiry)
_thresholds_cache: dict[str, tuple[dict[str, thresholds.Limits], float]] = {}


def clear_caches() -> None:
    _thresholds_cache.clear()
    actors.clear_cache()


# ---- clients (one per container, created on first use) ----


@cache
def dynamodb_client() -> Any:
    """Adaptive retries. DYNAMODB_ENDPOINT points at DynamoDB Local in integration
    tests; it is unset in AWS."""
    return boto3.client(
        "dynamodb",
        config=Config(retries={"mode": "adaptive"}),
        endpoint_url=os.environ.get("DYNAMODB_ENDPOINT"),
    )


@cache
def cognito_client() -> Any:
    return boto3.client("cognito-idp", config=Config(retries={"mode": "adaptive"}))


# ---- request ----


def parse_groups(value: Any) -> frozenset[str]:
    """`cognito:groups` as a set. API Gateway may pass it as a list or as a string such
    as "[doctor nurse]" or "doctor,nurse"; anything else is no groups."""
    if isinstance(value, list):
        parts = value
    elif isinstance(value, str):
        parts = re.split(r"[\s,]+", value.strip().strip("[]"))
    else:
        return frozenset()
    return frozenset(p for p in parts if isinstance(p, str) and GROUP_RE.match(p))


def claims_of(event: Any) -> dict[str, Any] | None:
    try:
        claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
    except (KeyError, TypeError):
        return None
    return claims if isinstance(claims, dict) else None


def safe_sub(claims: dict[str, Any]) -> str:
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and SUB_RE.match(sub) else "invalid"


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


# ---- reads ----


def _deserialize(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _deserializer.deserialize(v) for k, v in item.items()}


def _error_code(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = resp.get("Error", {}).get("Code")
        if isinstance(code, str):
            return code
    return type(exc).__name__


def query_all(request: dict[str, Any]) -> list[dict[str, Any]]:
    """Every item of an eventually consistent Query, following LastEvaluatedKey."""
    client = dynamodb_client()
    request = dict(request)
    items: list[dict[str, Any]] = []
    while True:
        result = client.query(**request)
        items.extend(_deserialize(item) for item in result.get("Items", []))
        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            return items
        request["ExclusiveStartKey"] = last_key


def read_alerts() -> list[dict[str, Any]]:
    """Pattern 9: every open and acknowledged alert, newest first."""
    return query_all(
        {
            "TableName": os.environ["ALERTS_TABLE"],
            "IndexName": ALERTS_INDEX,
            "Select": "SPECIFIC_ATTRIBUTES",
            "ScanIndexForward": False,
            "KeyConditionExpression": "#open = :open",
            "ExpressionAttributeNames": {
                "#open": "open_pk",
                "#pk": "pk",
                "#sk": "sk",
                "#type": "alert_type",
                "#device": "device_id",
                "#status": "status",
                "#event": "event_ts",
                "#severity": "severity",
                "#ack_at": "acknowledged_at",
                "#ack_note": "ack_note",
                "#ack_role": "acknowledged_by_role",
                "#ack_by": "acknowledged_by",
                "#escalated": "escalated_at",
                "#notified": "notified_at",
            },
            "ExpressionAttributeValues": {":open": {"S": "OPEN"}},
            "ProjectionExpression": (
                "#pk, #sk, #type, #device, #status, #event, #severity, #ack_at, "
                "#ack_note, #ack_role, #ack_by, #escalated, #notified"
            ),
        }
    )


def read_residents() -> list[dict[str, Any]]:
    """Pattern 8: every resident's display name and home."""
    return query_all(
        {
            "TableName": os.environ["REGISTRY_TABLE"],
            "IndexName": REGISTRY_INDEX,
            "Select": "SPECIFIC_ATTRIBUTES",
            "KeyConditionExpression": "#type = :resident",
            "ExpressionAttributeNames": {
                "#type": "entity_type",
                "#pk": "pk",
                "#name": "display_name",
                "#home": "home_id",
            },
            "ExpressionAttributeValues": {":resident": {"S": "RESIDENT"}},
            "ProjectionExpression": "#pk, #name, #home",
        }
    )


def read_devices() -> list[dict[str, Any]]:
    """Pattern 4: which resident each device is assigned to."""
    return query_all(
        {
            "TableName": os.environ["REGISTRY_TABLE"],
            "IndexName": REGISTRY_INDEX,
            "Select": "SPECIFIC_ATTRIBUTES",
            "KeyConditionExpression": "#type = :device",
            "ExpressionAttributeNames": {
                "#type": "entity_type",
                "#pk": "pk",
                "#resident": "resident_id",
            },
            "ExpressionAttributeValues": {":device": {"S": "DEVICE"}},
            "ProjectionExpression": "#pk, #resident",
        }
    )


def read_statuses() -> list[dict[str, Any]]:
    """Pattern 5 (`status`): newest ts, battery, worn, charging."""
    return query_all(
        {
            "TableName": os.environ["LATEST_TABLE"],
            "IndexName": LATEST_INDEX,
            "Select": "SPECIFIC_ATTRIBUTES",
            "KeyConditionExpression": "#watch = :status",
            "ExpressionAttributeNames": {
                "#watch": "watch_type",
                "#pk": "pk",
                "#latest": "latest",
                "#ts": "ts",
                "#battery": "battery_pct",
                "#worn": "worn",
                "#charging": "charging",
            },
            "ExpressionAttributeValues": {":status": {"S": "status"}},
            "ProjectionExpression": (
                "#pk, #latest.#ts, #latest.#battery, #latest.#worn, #latest.#charging"
            ),
        }
    )


def read_vitals() -> list[dict[str, Any]]:
    """Pattern 5 (`vitals`): the newest entry's ts and the three values the cards
    show (not activity_pct)."""
    return query_all(
        {
            "TableName": os.environ["LATEST_TABLE"],
            "IndexName": LATEST_INDEX,
            "Select": "SPECIFIC_ATTRIBUTES",
            "KeyConditionExpression": "#watch = :vitals",
            "ExpressionAttributeNames": {
                "#watch": "watch_type",
                "#pk": "pk",
                "#latest": "latest",
                "#ts": "ts",
                "#hr": "hr_bpm",
                "#spo2": "spo2_pct",
                "#skin": "skin_temp_c",
            },
            "ExpressionAttributeValues": {":vitals": {"S": "vitals"}},
            "ProjectionExpression": "#pk, #latest.#ts, #latest.#hr, #latest.#spo2, #latest.#skin",
        }
    )


def batch_get_thresholds(resident_ids: list[str]) -> dict[str, dict[str, Any]]:
    """{resident_id: THRESHOLDS item} for those that have one (eventually consistent,
    UnprocessedKeys retried). Raises if keys stay unprocessed."""
    client = dynamodb_client()
    table = os.environ["REGISTRY_TABLE"]
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(resident_ids), BATCH_GET_MAX_KEYS):
        chunk = resident_ids[start : start + BATCH_GET_MAX_KEYS]
        request: dict[str, Any] = {
            table: {
                "Keys": [
                    {"pk": {"S": f"THRESHOLDS#{r}"}, "sk": {"S": "THRESHOLDS"}}
                    for r in chunk
                ],
                "ProjectionExpression": "#pk, #hr, #spo2, #skin",
                "ExpressionAttributeNames": {
                    "#pk": "pk",
                    "#hr": "hr_bpm",
                    "#spo2": "spo2_pct",
                    "#skin": "skin_temp_c",
                },
            }
        }
        for attempt in range(BATCH_GET_MAX_ATTEMPTS):
            result = client.batch_get_item(RequestItems=request)
            for raw in result.get("Responses", {}).get(table, []):
                item = _deserialize(raw)
                pk = item.get("pk")
                if isinstance(pk, str) and pk.startswith("THRESHOLDS#"):
                    found[pk.removeprefix("THRESHOLDS#")] = item
            request = result.get("UnprocessedKeys") or {}
            if not request:
                break
            time.sleep(0.05 * 2**attempt)
        else:
            raise RuntimeError("UnprocessedKeys")
    return found


def thresholds_loader(
    resident_ids: list[str], clock: Callable[[], float] = time.monotonic
) -> Callable[[str], Mapping[str, thresholds.Limits]]:
    """Reads (or takes from the cache) the resolved thresholds of `resident_ids` and
    returns a lookup. A resident with no item gets the defaults."""
    now = clock()
    missing = [
        r
        for r in resident_ids
        if (c := _thresholds_cache.get(r)) is None or c[1] <= now
    ]
    if missing:
        items = batch_get_thresholds(missing)
        for resident_id in missing:
            limits, invalid = thresholds.resolve(items.get(resident_id))
            if invalid:
                logger.warning(
                    "invalid thresholds",
                    extra={"resident_id": resident_id, "fields": invalid},
                )
            _thresholds_cache[resident_id] = (limits, now + THRESHOLDS_CACHE_TTL_S)

    def limits_of(resident_id: str) -> Mapping[str, thresholds.Limits]:
        cached = _thresholds_cache.get(resident_id)
        return cached[0] if cached else dict(thresholds.DEFAULTS)

    return limits_of


# ---- handler ----


def handle(event: Any, now: datetime) -> dict[str, Any]:
    logger.append_keys(request_id=request_id_of(event))
    claims = claims_of(event)
    if claims is None:
        logger.warning(LOG_MESSAGE, extra={"outcome": "no_claims"})
        return error(401, "unauthorized", "Sign in again.")

    sub = safe_sub(claims)
    groups = parse_groups(claims.get("cognito:groups"))
    if not groups & STAFF_GROUPS:
        logger.warning(
            LOG_MESSAGE,
            extra={"outcome": "forbidden", "sub": sub, "groups": sorted(groups)},
        )
        return error(403, "forbidden", "Only staff can see the dashboard.")

    if event.get("queryStringParameters") or event.get("body"):
        logger.warning(LOG_MESSAGE, extra={"outcome": "bad_request", "sub": sub})
        return error(400, "bad_request", "This request takes no parameters or body.")

    parsed = dashboard.parse(
        dashboard.Reads(
            alert_items=read_alerts(),
            device_items=read_devices(),
            resident_items=read_residents(),
            status_items=read_statuses(),
            vitals_items=read_vitals(),
        )
    )
    limits_of = thresholds_loader(parsed.residents_needing_thresholds())
    names, name_failures = actors.caregiver_names(
        cognito_client, os.environ["USER_POOL_ID"], parsed.caregiver_subs()
    )
    result = dashboard.build_dashboard(parsed, limits_of, names, now)
    logger.info(
        LOG_MESSAGE,
        extra={
            "outcome": "ok",
            "sub": sub,
            "residents": len(result.residents),
            "alerts": len(result.alerts),
            "skipped": result.skipped,
            "name_lookup_failures": name_failures,
        },
    )
    return response(200, {"residents": result.residents, "alerts": result.alerts})


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    logger.remove_keys(["request_id"])
    try:
        return handle(event, datetime.now(UTC))
    except Exception as exc:  # noqa: BLE001 - every failure becomes a clean 500
        # Only the error code is logged: an exception's text could quote a request.
        logger.error(LOG_MESSAGE, extra={"outcome": "error", "error": _error_code(exc)})
        return error(500, "internal_error", "Could not load the dashboard. Try again.")
