"""GET /devices: the devices view for staff and ops (docs/data-model.md, Devices view;
patterns 4, 5, and 8). Behind the HTTP API's Cognito JWT authorizer (infra/api.tf), payload 2.0.

Steps (the api-route skill):

1. Read `sub` and `cognito:groups` from the JWT claims (never from headers or body).
2. `doctor` or `nurse` (staff), or `ops`, else 403. Both see every device, so there is
   no resident link check. A user in a staff group and `ops` is answered as staff
   (view precedence nurse > doctor > ops). ops get `403` on pattern 8 (the ops
   paragraph of Access patterns), so for ops the resident Query is never sent and
   `home_id` is never read: `assigned_to` keeps only `resident_id`, with
   `display_name` and `home_id` null (user's decision 2026-11-08 to give ops the
   devices tab).
3. The route takes no query string and no body: either gives 400.
4. Three eventually consistent, paginated Queries, as the watchdog's (reference code):
   - registry `by-type` `entity_type = DEVICE` (pattern 4): pk, device_type,
     resident_id, and (staff only) home_id;
   - latest `by-message` `watch_type = status` (pattern 5): pk and only latest.ts,
     latest.battery_pct, latest.charging, latest.fw_version, so `worn`, `received_at`,
     `clock_skew`, and `message_id` are never read;
   - staff only: registry `by-type` `entity_type = RESIDENT` (pattern 8): pk and
     display_name, never `photo_key` (IAM allows only these attributes on by-type,
     infra/api.tf).
5. The response is built field by field (fleet.py), never from raw items.
6. Errors are {"error": {"code", "message"}} with no internal details.

Logs: outcome, counts, the caller kind (`staff` or `ops`), API Gateway's request ID,
the caller's `sub` and, on a 403, `cognito:groups`. Never the
request, the response, other claims, names, or readings.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from functools import cache
from typing import Any

import boto3
import fleet
from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config

# The service name comes from POWERTOOLS_SERVICE_NAME, set by Terraform.
logger = Logger()

LOG_MESSAGE = "get devices"
REGISTRY_INDEX = "by-type"
LATEST_INDEX = "by-message"
STAFF_GROUPS = frozenset({"doctor", "nurse"})
OPS_GROUP = "ops"
SUB_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
GROUP_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}\Z")
# API Gateway request IDs (e.g. "JpvNMgAbSQ0EJcQ="): logged so a Lambda log line can be
# joined to the stage's access log.
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9=+/_-]{1,64}\Z")

HEADERS = {"Content-Type": "application/json", "Cache-Control": "no-store"}

_deserializer = TypeDeserializer()


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
    """The `sub` for logs, or "invalid" if it isn't a Cognito UUID."""
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and SUB_RE.match(sub) else "invalid"


def request_id_of(event: Any) -> str:
    """API Gateway's request ID for logs, or "invalid"."""
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


def read_devices(*, include_home: bool = True) -> list[dict[str, Any]]:
    """Pattern 4: every device, assigned or not. `include_home` False (ops) leaves
    `home_id` out of the projection, so it is never read."""
    names = {
        "#type": "entity_type",
        "#pk": "pk",
        "#device_type": "device_type",
        "#resident": "resident_id",
    }
    projection = "#pk, #device_type, #resident"
    if include_home:
        names["#home"] = "home_id"
        projection += ", #home"
    return query_all(
        {
            "TableName": os.environ["REGISTRY_TABLE"],
            "IndexName": REGISTRY_INDEX,
            "Select": "SPECIFIC_ATTRIBUTES",
            "KeyConditionExpression": "#type = :device",
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": {":device": {"S": "DEVICE"}},
            "ProjectionExpression": projection,
        }
    )


def read_statuses() -> list[dict[str, Any]]:
    """Pattern 5 (status only): every device's newest status, only the fields the view
    shows."""
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
                "#charging": "charging",
                "#fw": "fw_version",
            },
            "ExpressionAttributeValues": {":status": {"S": "status"}},
            "ProjectionExpression": (
                "#pk, #latest.#ts, #latest.#battery, #latest.#charging, #latest.#fw"
            ),
        }
    )


def read_residents() -> list[dict[str, Any]]:
    """Pattern 8: every resident's display name (fleet.py keeps only those on a
    device)."""
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
            },
            "ExpressionAttributeValues": {":resident": {"S": "RESIDENT"}},
            "ProjectionExpression": "#pk, #name",
        }
    )


# ---- handler ----


def handle(event: Any, now: datetime) -> dict[str, Any]:
    logger.append_keys(request_id=request_id_of(event))
    claims = claims_of(event)
    if claims is None:
        logger.warning(LOG_MESSAGE, extra={"outcome": "no_claims"})
        return error(401, "unauthorized", "Sign in again.")

    sub = safe_sub(claims)
    groups = parse_groups(claims.get("cognito:groups"))
    if groups & STAFF_GROUPS:
        caller = "staff"
    elif OPS_GROUP in groups:
        caller = "ops"
    else:
        logger.warning(
            LOG_MESSAGE,
            extra={"outcome": "forbidden", "sub": sub, "groups": sorted(groups)},
        )
        return error(403, "forbidden", "Only staff and ops can see the devices.")

    if event.get("queryStringParameters") or event.get("body"):
        logger.warning(
            LOG_MESSAGE, extra={"outcome": "bad_request", "caller": caller, "sub": sub}
        )
        return error(400, "bad_request", "This request takes no parameters or body.")

    if caller == "staff":
        result = fleet.build_fleet(
            read_devices(), read_statuses(), read_residents(), now
        )
    else:
        # ops: no pattern 8 (names) and no home_id; resident IDs only.
        result = fleet.build_fleet(
            read_devices(include_home=False), read_statuses(), [], now
        )
    logger.info(
        LOG_MESSAGE,
        extra={
            "outcome": "ok",
            "caller": caller,
            "sub": sub,
            "devices": len(result.devices),
            "skipped": result.skipped,
        },
    )
    return response(200, {"devices": result.devices})


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    logger.remove_keys(["request_id"])
    try:
        return handle(event, datetime.now(UTC))
    except Exception as exc:  # noqa: BLE001 - every failure becomes a clean 500
        # Only the error code is logged: an exception's text could quote a request.
        # The caller retries (the devices page has a Retry button).
        logger.error(LOG_MESSAGE, extra={"outcome": "error", "error": _error_code(exc)})
        return error(500, "internal_error", "Could not load the devices. Try again.")
