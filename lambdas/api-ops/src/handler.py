"""GET /ops/device-problems: the ops view (docs/data-model.md, Access patterns, the
**ops** paragraph; pattern 9 filtered to device problems). Behind the HTTP API's
Cognito JWT authorizer (infra/api.tf), payload 2.0. Built like GET /dashboard.

Steps (the api-route skill):

1. Read `sub` and `cognito:groups` from the JWT claims (never from headers or body).
2. `ops` only, else 403. ops see every resident, so there is no resident link check.
   Nurses see device problems on GET /dashboard instead.
3. The route takes no query string and no body: either gives 400.
4. alerts `open` index, `open_pk = OPEN`, newest first, eventually consistent,
   paginated, with a **FilterExpression** `alert_type IN (device_offline, battery_low,
   not_worn)`, projecting only the alert-row fields (never `ack_note`).
5. Every item is checked again (problems.is_device_problem) before anything else is
   done with it, so a health alert's key is never used for the base-table read.
6. BatchGetItem of those alerts' base items for `details` (the index doesn't project
   it) and `resolved_at` / `resolved_by_role`; the type is checked a third time there.
7. Caregiver names from Cognito (actors.py) for caregivers who acknowledged one; ops
   acknowledges are shown as "the ops team".
8. The response is built field by field (problems.py). Errors are
   {"error": {"code", "message"}} with no internal details.

Logs: outcome, counts, API Gateway's request ID, the caller's `sub` and, on a 403,
`cognito:groups`. Never the request, the response, other claims, names, or details.
"""

from __future__ import annotations

import json
import os
import re
import time
from functools import cache
from typing import Any

import actors
import boto3
import problems
from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config

logger = Logger()

LOG_MESSAGE = "get device problems"
ALERTS_INDEX = "open"
OPS_GROUP = "ops"
SUB_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
GROUP_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}\Z")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9=+/_-]{1,64}\Z")

BATCH_GET_MAX_KEYS = 100
BATCH_GET_MAX_ATTEMPTS = 4

HEADERS = {"Content-Type": "application/json", "Cache-Control": "no-store"}

_deserializer = TypeDeserializer()


def clear_caches() -> None:
    actors.clear_cache()


# ---- clients (one per container, created on first use) ----


@cache
def dynamodb_client() -> Any:
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
    """`cognito:groups` as a set: a list, or a string such as "[ops nurse]"."""
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


def _error_code(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = resp.get("Error", {}).get("Code")
        if isinstance(code, str):
            return code
    return type(exc).__name__


# ---- reads ----


def _deserialize(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _deserializer.deserialize(v) for k, v in item.items()}


def read_device_problems() -> list[dict[str, Any]]:
    """Pattern 9, filtered server-side to the three device-problem types."""
    client = dynamodb_client()
    request: dict[str, Any] = {
        "TableName": os.environ["ALERTS_TABLE"],
        "IndexName": ALERTS_INDEX,
        "Select": "SPECIFIC_ATTRIBUTES",
        "ScanIndexForward": False,
        "KeyConditionExpression": "#open = :open",
        "FilterExpression": "#type IN (:offline, :battery, :not_worn)",
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
            "#ack_role": "acknowledged_by_role",
            "#ack_by": "acknowledged_by",
        },
        "ExpressionAttributeValues": {
            ":open": {"S": "OPEN"},
            ":offline": {"S": "device_offline"},
            ":battery": {"S": "battery_low"},
            ":not_worn": {"S": "not_worn"},
        },
        "ProjectionExpression": (
            "#pk, #sk, #type, #device, #status, #event, #severity, #ack_at, "
            "#ack_role, #ack_by"
        ),
    }
    items: list[dict[str, Any]] = []
    while True:
        result = client.query(**request)
        items.extend(_deserialize(item) for item in result.get("Items", []))
        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            return items
        request["ExclusiveStartKey"] = last_key


def read_base_items(
    keys: list[problems.Key],
) -> dict[tuple[str, str], dict[str, Any]]:
    """{(pk, sk): base item} with only the type, `details`, and `resolved_*`
    (eventually consistent, UnprocessedKeys retried). Raises if keys stay
    unprocessed. Called only with keys of device problems."""
    client = dynamodb_client()
    table = os.environ["ALERTS_TABLE"]
    found: dict[tuple[str, str], dict[str, Any]] = {}
    unique = sorted({(k.pk, k.sk) for k in keys})
    for start in range(0, len(unique), BATCH_GET_MAX_KEYS):
        chunk = unique[start : start + BATCH_GET_MAX_KEYS]
        request: dict[str, Any] = {
            table: {
                "Keys": [{"pk": {"S": pk}, "sk": {"S": sk}} for pk, sk in chunk],
                "ProjectionExpression": "#pk, #sk, #type, #details, #res_at, #res_role",
                "ExpressionAttributeNames": {
                    "#pk": "pk",
                    "#sk": "sk",
                    "#type": "alert_type",
                    "#details": "details",
                    "#res_at": "resolved_at",
                    "#res_role": "resolved_by_role",
                },
            }
        }
        for attempt in range(BATCH_GET_MAX_ATTEMPTS):
            result = client.batch_get_item(RequestItems=request)
            for raw in result.get("Responses", {}).get(table, []):
                item = _deserialize(raw)
                pk, sk = item.get("pk"), item.get("sk")
                if isinstance(pk, str) and isinstance(sk, str):
                    found[(pk, sk)] = item
            request = result.get("UnprocessedKeys") or {}
            if not request:
                break
            time.sleep(0.05 * 2**attempt)
        else:
            raise RuntimeError("UnprocessedKeys")
    return found


# ---- handler ----


def handle(event: Any) -> dict[str, Any]:
    logger.append_keys(request_id=request_id_of(event))
    claims = claims_of(event)
    if claims is None:
        logger.warning(LOG_MESSAGE, extra={"outcome": "no_claims"})
        return error(401, "unauthorized", "Sign in again.")

    sub = safe_sub(claims)
    groups = parse_groups(claims.get("cognito:groups"))
    if OPS_GROUP not in groups:
        logger.warning(
            LOG_MESSAGE,
            extra={"outcome": "forbidden", "sub": sub, "groups": sorted(groups)},
        )
        return error(403, "forbidden", "Only the ops team can see this list.")

    if event.get("queryStringParameters") or event.get("body"):
        logger.warning(LOG_MESSAGE, extra={"outcome": "bad_request", "sub": sub})
        return error(400, "bad_request", "This request takes no parameters or body.")

    # Second filter: never trust the FilterExpression alone. A health alert dropped
    # here is never read from the base table.
    raw_items = read_device_problems()
    index_items = [i for i in raw_items if problems.is_device_problem(i)]
    dropped = len(raw_items) - len(index_items)
    keys = [k for i in index_items if (k := problems.key_of(i)) is not None]
    base_items = read_base_items(keys) if keys else {}
    names, name_failures = actors.caregiver_names(
        cognito_client, os.environ["USER_POOL_ID"], problems.caregiver_subs(index_items)
    )
    built = problems.build(index_items, base_items, names)
    logger.info(
        LOG_MESSAGE,
        extra={
            "outcome": "ok",
            "sub": sub,
            "alerts": len(built.alerts),
            "not_device_problems": dropped + built.not_device_problems,
            "invalid": built.invalid,
            "name_lookup_failures": name_failures,
        },
    )
    return response(200, {"alerts": built.alerts})


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    logger.remove_keys(["request_id"])
    try:
        return handle(event)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a clean 500
        logger.error(LOG_MESSAGE, extra={"outcome": "error", "error": _error_code(exc)})
        return error(
            500, "internal_error", "Could not load device problems. Try again."
        )
