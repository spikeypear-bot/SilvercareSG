"""PUT /residents/{resident_id}/thresholds (change a resident's vitals thresholds) and
GET /residents/{resident_id}/thresholds/history (the change history), docs/data-model.md
patterns 26 and 27, "Resident thresholds" and "Threshold change history". Behind the
HTTP API's Cognito JWT authorizer (infra/api.tf), payload 2.0. Built like
lambdas/api-residents/.

Access:
- PUT: `doctor` only (the `cognito:groups` claim of the ID token), any resident; else
  403. Changes only an existing THRESHOLDS item (404 otherwise).
- GET history: `doctor` / `nurse`, any resident; else 403.

PUT body: exactly
    {"hr_bpm": {"min": int, "max": int}, "spo2_pct": {"min": int},
     "skin_temp_c": {"min": number, "max": number}}
and nothing else (400 for any other key, including updated_by / updated_at). Ranges are
the `vitals` valid ranges in docs/metrics.md (heart rate 20-250, SpO2 50-100, skin
temperature 20.0-45.0), heart rate and SpO2 whole numbers, skin temperature at most 1
decimal place, each min below its max. The item is built here, never copied from the
body.

PUT steps: a strongly consistent GetItem of THRESHOLDS#<id> (404 if none), the diff
against the limits in effect (thresholds.resolve: the stored value or the default, as
the resident page showed them), and, if anything changed, one TransactWriteItems:
UpdateItem of the thresholds (condition: the item exists and `updated_at` is what was
read, else 409) and PutItem of the THRESHOLD_LOG# change item. A save that changes
nothing writes nothing (no log item, `updated_at` unchanged) and returns 200 with
`"changed": false`. The actor is the token's `sub` (`updated_by`, `changed_by`, never
returned) and that user's Cognito `name` (`updated_by_name`, `changed_by_name`), read
with AdminGetUser when the change is written.

GET history: Query THRESHOLD_LOG#<id>, newest first, the newest MAX_HISTORY items,
projected without `changed_by` (IAM enforces the projection, infra/api.tf).

Logs: outcome, route, request ID, the caller's `sub`, the opaque resident ID, the names
of changed fields, and on a 403 `cognito:groups`. Never values, names, or the body.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from functools import cache
from typing import Any

import boto3
import thresholds
from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config

logger = Logger()

LOG_MESSAGE = "thresholds"
ROUTE_PUT = "PUT /residents/{resident_id}/thresholds"
ROUTE_HISTORY = "GET /residents/{resident_id}/thresholds/history"
STAFF_GROUPS = frozenset({"doctor", "nurse"})
GROUP_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}\Z")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9=+/_-]{1,64}\Z")
RESIDENT_ID_RE = re.compile(r"^res-[0-9]{4}\Z")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z\Z")
HEADERS = {"Content-Type": "application/json", "Cache-Control": "no-store"}
MAX_BODY_BYTES = 1024
NAME_MAX_LEN = 100
UNNAMED = "Staff member"
# One page of the newest changes; older ones stay stored (retention: lifetime + 6 y).
MAX_HISTORY = 100
ONE_DP = Decimal("0.1")

# (metric, limit) in display order: the five accepted fields.
FIELDS: tuple[tuple[str, str], ...] = (
    ("hr_bpm", "min"),
    ("hr_bpm", "max"),
    ("spo2_pct", "min"),
    ("skin_temp_c", "min"),
    ("skin_temp_c", "max"),
)
FIELD_NAMES = frozenset(f"{m}.{n}" for m, n in FIELDS)

THRESHOLDS_PROJECTION = [
    "pk",
    "sk",
    "hr_bpm",
    "spo2_pct",
    "skin_temp_c",
    "updated_at",
    "updated_by_name",
]
HISTORY_PROJECTION = [
    "pk",
    "sk",
    "change_id",
    "changed_at",
    "changed_by_name",
    "changes",
]

_deserializer = TypeDeserializer()
_serializer = TypeSerializer()


class BadRequest(Exception):
    pass


# ---- clients ----


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


def request_id_of(event: Any) -> str:
    try:
        value = event["requestContext"]["requestId"]
    except (KeyError, TypeError):
        return "invalid"
    return value if isinstance(value, str) and REQUEST_ID_RE.match(value) else "invalid"


def body_text_of(event: dict[str, Any]) -> Any:
    body = event.get("body")
    if body is None or not event.get("isBase64Encoded"):
        return body
    try:
        return base64.b64decode(body, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, TypeError):
        return None


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


def resident_id_of(event: dict[str, Any]) -> str | None:
    value = (event.get("pathParameters") or {}).get("resident_id")
    return value if isinstance(value, str) and RESIDENT_ID_RE.match(value) else None


# ---- validation (PUT body) ----


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise BadRequest
        out[key] = value
    return out


def _reject_constant(_: str) -> Any:
    raise BadRequest  # NaN, Infinity


def _limit_value(value: Any, metric: str) -> Decimal:
    rule = thresholds.RULES[metric]
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise BadRequest
    if rule.integer and not isinstance(value, int):
        raise BadRequest  # 72.0 is not a whole number in the API
    number = Decimal(value)
    if not number.is_finite() or not rule.minimum <= number <= rule.maximum:
        raise BadRequest
    if not rule.integer:
        rounded = number.quantize(ONE_DP)
        if rounded != number:
            raise BadRequest  # more than 1 decimal place
        number = rounded
    return number


def parse_body(text: Any) -> dict[str, dict[str, Decimal]]:
    """The five limits as Decimals, or BadRequest."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BODY_BYTES:
        raise BadRequest
    try:
        body = json.loads(
            text,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_constant,
            object_pairs_hook=_no_duplicates,
        )
    except ValueError as exc:
        raise BadRequest from exc
    if not isinstance(body, dict) or set(body) != {"hr_bpm", "spo2_pct", "skin_temp_c"}:
        raise BadRequest
    out: dict[str, dict[str, Decimal]] = {}
    for metric, rule in thresholds.RULES.items():
        limits = body[metric]
        if not isinstance(limits, dict) or set(limits) != set(rule.names):
            raise BadRequest
        out[metric] = {name: _limit_value(limits[name], metric) for name in rule.names}
    for metric in ("hr_bpm", "skin_temp_c"):
        if not out[metric]["min"] < out[metric]["max"]:
            raise BadRequest
    return out


# ---- reads and writes ----


def _deserialize(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _deserializer.deserialize(v) for k, v in item.items()}


def _serialize(value: Any) -> dict[str, Any]:
    return _serializer.serialize(value)


def read_thresholds(resident_id: str) -> dict[str, Any] | None:
    names = {f"#a{i}": name for i, name in enumerate(THRESHOLDS_PROJECTION)}
    result = dynamodb_client().get_item(
        TableName=os.environ["REGISTRY_TABLE"],
        Key={"pk": {"S": f"THRESHOLDS#{resident_id}"}, "sk": {"S": "THRESHOLDS"}},
        ConsistentRead=True,
        ProjectionExpression=", ".join(names),
        ExpressionAttributeNames=names,
    )
    item = result.get("Item")
    return _deserialize(item) if item else None


def clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > NAME_MAX_LEN or not value.isprintable():
        return None
    return value


def actor_name(sub: str) -> str | None:
    """The caller's Cognito `name` ("Staff member" if it has none), or None if the
    account no longer exists. Other errors are raised (500: the change needs a name).
    The user's other attributes are dropped at once."""
    try:
        result = cognito_client().admin_get_user(
            UserPoolId=os.environ["USER_POOL_ID"], Username=sub
        )
    except Exception as exc:
        if _error_code(exc) == "UserNotFoundException":
            return None
        raise
    for attribute in result.get("UserAttributes") or []:
        if attribute.get("Name") == "name":
            return clean_name(attribute.get("Value")) or UNNAMED
    return UNNAMED


def diff(
    old: dict[str, thresholds.Limits], new: dict[str, dict[str, Decimal]]
) -> list[dict[str, Any]]:
    """Only the values that changed, in display order."""
    changes = []
    for metric, name in FIELDS:
        before = getattr(old[metric], name)
        after = new[metric][name]
        if before is None or before != after:
            changes.append(
                {"field": f"{metric}.{name}", "old_value": before, "new_value": after}
            )
    return changes


def now_iso(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{now.microsecond // 1000:03d}Z"
    )


def write_change(
    resident_id: str,
    previous_updated_at: Any,
    new: dict[str, dict[str, Decimal]],
    changes: list[dict[str, Any]],
    sub: str,
    name: str,
    changed_at: str,
) -> None:
    """One transaction: the thresholds and their change-log item. Raises the client's
    TransactionCanceledException if the item is gone or was changed since the read."""
    table = os.environ["REGISTRY_TABLE"]
    names = {
        "#pk": "pk",
        "#h": "hr_bpm",
        "#s": "spo2_pct",
        "#t": "skin_temp_c",
        "#ua": "updated_at",
        "#ub": "updated_by",
        "#ubn": "updated_by_name",
    }
    values = {
        ":h": _serialize(new["hr_bpm"]),
        ":s": _serialize(new["spo2_pct"]),
        ":t": _serialize(new["skin_temp_c"]),
        ":ua": {"S": changed_at},
        ":ub": {"S": sub},
        ":ubn": {"S": name},
    }
    if isinstance(previous_updated_at, str):
        condition = "attribute_exists(#pk) AND #ua = :prev"
        values[":prev"] = {"S": previous_updated_at}
    else:
        condition = "attribute_exists(#pk) AND attribute_not_exists(#ua)"
    change_id = str(uuid.uuid4())
    log_item = {
        "pk": f"THRESHOLD_LOG#{resident_id}",
        "sk": f"CHANGE#{changed_at}#{change_id}",
        "change_id": change_id,
        "changed_at": changed_at,
        "changed_by": sub,
        "changed_by_name": name,
        "changes": changes,
    }
    dynamodb_client().transact_write_items(
        TransactItems=[
            {
                "Update": {
                    "TableName": table,
                    "Key": {
                        "pk": {"S": f"THRESHOLDS#{resident_id}"},
                        "sk": {"S": "THRESHOLDS"},
                    },
                    "UpdateExpression": (
                        "SET #h = :h, #s = :s, #t = :t, #ua = :ua, #ub = :ub, "
                        "#ubn = :ubn"
                    ),
                    "ConditionExpression": condition,
                    "ExpressionAttributeNames": names,
                    "ExpressionAttributeValues": values,
                }
            },
            {
                "Put": {
                    "TableName": table,
                    "Item": {k: _serialize(v) for k, v in log_item.items()},
                    "ConditionExpression": "attribute_not_exists(#pk)",
                    "ExpressionAttributeNames": {"#pk": "pk"},
                }
            },
        ]
    )


def _cancellation_codes(exc: Exception) -> list[str]:
    resp = getattr(exc, "response", None)
    reasons = resp.get("CancellationReasons") if isinstance(resp, dict) else None
    if not isinstance(reasons, list):
        return []
    return [r.get("Code", "") if isinstance(r, dict) else "" for r in reasons]


def query_history(resident_id: str) -> list[dict[str, Any]]:
    names = {f"#a{i}": name for i, name in enumerate(HISTORY_PROJECTION)}
    result = dynamodb_client().query(
        TableName=os.environ["REGISTRY_TABLE"],
        Select="SPECIFIC_ATTRIBUTES",
        ScanIndexForward=False,
        Limit=MAX_HISTORY,
        KeyConditionExpression="#a0 = :pk",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues={":pk": {"S": f"THRESHOLD_LOG#{resident_id}"}},
        ProjectionExpression=", ".join(names),
    )
    return [_deserialize(i) for i in result.get("Items", [])]


# ---- views ----


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return None
    value = Decimal(value)
    if not value.is_finite():
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def _ts(value: Any) -> str | None:
    return value if isinstance(value, str) and TS_RE.match(value) else None


def thresholds_view(
    limits: dict[str, thresholds.Limits], updated_at: Any, updated_by_name: Any
) -> dict[str, Any]:
    """The resident page's `thresholds` shape (lambdas/api-residents, staff view)."""
    return {
        "hr_bpm": {
            "min": _number(limits["hr_bpm"].min),
            "max": _number(limits["hr_bpm"].max),
        },
        "spo2_pct": {"min": _number(limits["spo2_pct"].min)},
        "skin_temp_c": {
            "min": _number(limits["skin_temp_c"].min),
            "max": _number(limits["skin_temp_c"].max),
        },
        "updated_at": _ts(updated_at),
        "updated_by": clean_name(updated_by_name),
    }


def history_view(item: dict[str, Any]) -> dict[str, Any] | None:
    """One change for the portal: the stored name, never the sub. None if malformed."""
    change_id = item.get("change_id")
    changed_at = _ts(item.get("changed_at"))
    raw_changes = item.get("changes")
    if not (isinstance(change_id, str) and UUID_RE.match(change_id)) or not changed_at:
        return None
    if not isinstance(raw_changes, list):
        return None
    changes = []
    for c in raw_changes:
        if not isinstance(c, dict) or c.get("field") not in FIELD_NAMES:
            return None
        old, new = _number(c.get("old_value")), _number(c.get("new_value"))
        if old is None or new is None:
            return None
        changes.append({"field": c["field"], "old_value": old, "new_value": new})
    return {
        "change_id": change_id,
        "changed_at": changed_at,
        "changed_by_name": clean_name(item.get("changed_by_name")) or UNNAMED,
        "changes": changes,
    }


# ---- handler ----


def put_thresholds(
    sub: str, groups: frozenset[str], event: dict[str, Any], now: datetime
) -> dict[str, Any]:
    if "doctor" not in groups:
        logger.warning(
            LOG_MESSAGE, extra={"outcome": "forbidden", "groups": sorted(groups)}
        )
        return error(403, "forbidden", "Only doctors can change thresholds.")
    if event.get("queryStringParameters"):
        return error(400, "bad_request", "This request takes no query parameters.")
    resident_id = resident_id_of(event)
    if resident_id is None:
        return error(400, "bad_request", "The request is not valid.")
    logger.append_keys(resident_id=resident_id)
    try:
        new = parse_body(body_text_of(event))
    except BadRequest:
        logger.info(LOG_MESSAGE, extra={"outcome": "bad_request"})
        return error(400, "bad_request", "The thresholds are not valid.")

    item = read_thresholds(resident_id)
    if item is None:
        logger.info(LOG_MESSAGE, extra={"outcome": "not_found"})
        return error(404, "not_found", "No thresholds for this resident.")
    old, invalid = thresholds.resolve(item)
    if invalid:
        logger.warning("invalid thresholds", extra={"fields": invalid})

    changes = diff(old, new)
    if not changes:
        logger.info(LOG_MESSAGE, extra={"outcome": "unchanged"})
        return response(
            200,
            {
                "changed": False,
                "thresholds": thresholds_view(
                    old, item.get("updated_at"), item.get("updated_by_name")
                ),
            },
        )

    name = actor_name(sub)
    if name is None:
        logger.warning(LOG_MESSAGE, extra={"outcome": "forbidden", "reason": "no_user"})
        return error(403, "forbidden", "Sign in again.")
    changed_at = now_iso(now)
    try:
        write_change(
            resident_id,
            item.get("updated_at"),
            new,
            changes,
            sub,
            name,
            changed_at,
        )
    except Exception as exc:
        codes = _cancellation_codes(exc)
        if _error_code(exc) == "TransactionCanceledException" and codes[:1] in (
            ["ConditionalCheckFailed"],
            ["TransactionConflict"],
        ):
            logger.info(LOG_MESSAGE, extra={"outcome": "conflict", "reason": codes[0]})
            return error(
                409,
                "conflict",
                "These thresholds were changed by someone else. Reload and try again.",
            )
        raise

    resolved, _ = thresholds.resolve(new)
    logger.info(
        LOG_MESSAGE,
        extra={"outcome": "ok", "fields": [c["field"] for c in changes]},
    )
    return response(
        200,
        {"changed": True, "thresholds": thresholds_view(resolved, changed_at, name)},
    )


def get_history(groups: frozenset[str], event: dict[str, Any]) -> dict[str, Any]:
    if not groups & STAFF_GROUPS:
        logger.warning(
            LOG_MESSAGE, extra={"outcome": "forbidden", "groups": sorted(groups)}
        )
        return error(403, "forbidden", "Only care staff can see the change history.")
    if event.get("queryStringParameters") or event.get("body"):
        return error(400, "bad_request", "This request takes no parameters or body.")
    resident_id = resident_id_of(event)
    if resident_id is None:
        return error(400, "bad_request", "The request is not valid.")
    logger.append_keys(resident_id=resident_id)
    items = query_history(resident_id)
    views = [v for v in map(history_view, items) if v]
    logger.info(
        LOG_MESSAGE,
        extra={
            "outcome": "ok",
            "changes": len(views),
            "malformed": len(items) - len(views),
        },
    )
    return response(200, {"history": views})


def handle(event: Any, now: datetime) -> dict[str, Any]:
    logger.append_keys(request_id=request_id_of(event))
    claims = claims_of(event)
    sub = claims.get("sub") if claims else None
    if not isinstance(sub, str) or not UUID_RE.match(sub):
        logger.warning(LOG_MESSAGE, extra={"outcome": "no_claims"})
        return error(401, "unauthorized", "Sign in again.")
    logger.append_keys(sub=sub)
    groups = parse_groups(claims.get("cognito:groups"))
    route = event.get("routeKey")
    logger.append_keys(route=route if route in (ROUTE_PUT, ROUTE_HISTORY) else "other")
    if route == ROUTE_PUT:
        return put_thresholds(sub, groups, event, now)
    if route == ROUTE_HISTORY:
        return get_history(groups, event)
    return error(404, "not_found", "No such route.")


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    logger.remove_keys(["request_id", "sub", "resident_id", "route"])
    try:
        return handle(event, datetime.now(UTC))
    except Exception as exc:  # noqa: BLE001 - every failure becomes a clean 500
        logger.error(LOG_MESSAGE, extra={"outcome": "error", "error": _error_code(exc)})
        return error(500, "internal_error", "Could not save or load. Try again.")
