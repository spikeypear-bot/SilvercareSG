"""GET /residents/{resident_id} (the resident page) and GET /me/residents (the caregiver
view), docs/data-model.md patterns 10, 11, 12, 13, 14, 18. Behind the HTTP API's Cognito
JWT authorizer (infra/api.tf), payload 2.0. Built like GET /dashboard.

Access:
- GET /residents/{resident_id}: `doctor` / `nurse` any resident; `caregiver` only with
  the pattern 13 link (else 404, so IDs can't be probed); anyone else 403. A resident
  without a profile is 404.
- GET /me/residents: `caregiver` only (else 403): the residents linked to the caller
  (pattern 14), each built as on the resident page.

Reads per resident, eventually consistent:
- registry `by-resident` Query (pattern 10): the resident's devices;
- registry BatchGetItem (pattern 18 and the devices' ASSIGNMENT items): profile
  (`display_name`, `home_id` only), thresholds, and each device's `assigned_at` and
  current `resident_id` (the index can lag a reassignment);
- latest BatchGetItem (pattern 11): wearable vitals / fall / status, bedroom motion /
  bed / status (`vitals_alert` and `alert_cancel` aren't shown, so aren't read);
- alerts Query (pattern 12), newest first, without `details` or notes other than
  `ack_note`;
- Cognito AdminGetUser for actor names (actors.py).

Threshold change history (pattern 27) is served by GET .../thresholds/history
(lambdas/api-thresholds/), so `threshold_history` is always [] here and isn't read.

Logs: outcome, route, counts, request ID, the caller's `sub`, the opaque resident ID,
and on a 403 `cognito:groups`. Never names, notes, readings, or the body.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from functools import cache
from typing import Any

import actors
import boto3
import resident as model
import thresholds
from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config

logger = Logger()

LOG_MESSAGE = "get residents"
ROUTE_DETAIL = "GET /residents/{resident_id}"
ROUTE_MINE = "GET /me/residents"
STAFF_GROUPS = frozenset({"doctor", "nurse"})
GROUP_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}\Z")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9=+/_-]{1,64}\Z")
SUB_RE = model.UUID_RE
HEADERS = {"Content-Type": "application/json", "Cache-Control": "no-store"}
BATCH_MAX_ATTEMPTS = 4
# A caregiver is linked to a handful of residents; more is a data error, not a page.
MAX_LINKED = 20

_deserializer = TypeDeserializer()


class NotFound(Exception):
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


def query_all(request: dict[str, Any]) -> list[dict[str, Any]]:
    client = dynamodb_client()
    request = dict(request)
    items: list[dict[str, Any]] = []
    while True:
        result = client.query(**request)
        items.extend(_deserialize(i) for i in result.get("Items", []))
        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            return items
        request["ExclusiveStartKey"] = last_key


def batch_get(
    table: str, keys: list[tuple[str, str]], projection: list[str]
) -> list[dict[str, Any]]:
    """Every found item for (pk, sk) keys, UnprocessedKeys retried. Raises if keys
    stay unprocessed."""
    client = dynamodb_client()
    names = {f"#a{i}": name for i, name in enumerate(projection)}
    found: list[dict[str, Any]] = []
    for start in range(0, len(keys), 100):
        request: dict[str, Any] = {
            table: {
                "Keys": [
                    {"pk": {"S": pk}, "sk": {"S": sk}}
                    for pk, sk in keys[start : start + 100]
                ],
                "ProjectionExpression": ", ".join(names),
                "ExpressionAttributeNames": names,
            }
        }
        for attempt in range(BATCH_MAX_ATTEMPTS):
            result = client.batch_get_item(RequestItems=request)
            found.extend(
                _deserialize(i) for i in result.get("Responses", {}).get(table, [])
            )
            request = result.get("UnprocessedKeys") or {}
            if not request:
                break
            time.sleep(0.05 * 2**attempt)
        else:
            raise RuntimeError("UnprocessedKeys")
    return found


def is_linked(sub: str, resident_id: str) -> bool:
    """Pattern 13."""
    result = dynamodb_client().get_item(
        TableName=os.environ["REGISTRY_TABLE"],
        Key={"pk": {"S": f"USER#{sub}"}, "sk": {"S": f"RESIDENT#{resident_id}"}},
        ProjectionExpression="#pk",
        ExpressionAttributeNames={"#pk": "pk"},
    )
    return "Item" in result


def linked_residents(sub: str) -> list[str]:
    """Pattern 14: the caller's linked resident IDs, sorted."""
    items = query_all(
        {
            "TableName": os.environ["REGISTRY_TABLE"],
            "Select": "SPECIFIC_ATTRIBUTES",
            "KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :prefix)",
            "ExpressionAttributeNames": {"#pk": "pk", "#sk": "sk"},
            "ExpressionAttributeValues": {
                ":pk": {"S": f"USER#{sub}"},
                ":prefix": {"S": "RESIDENT#"},
            },
            "ProjectionExpression": "#pk, #sk",
        }
    )
    ids = {
        i["sk"].removeprefix("RESIDENT#")
        for i in items
        if isinstance(i.get("sk"), str)
        and model.RESIDENT_ID_RE.match(i["sk"].removeprefix("RESIDENT#"))
    }
    return sorted(ids)


def resident_devices(resident_id: str) -> list[str]:
    """Pattern 10: device IDs the by-resident index lists for the resident."""
    items = query_all(
        {
            "TableName": os.environ["REGISTRY_TABLE"],
            "IndexName": "by-resident",
            "Select": "SPECIFIC_ATTRIBUTES",
            "KeyConditionExpression": "#g = :g AND begins_with(#s, :device)",
            "ExpressionAttributeNames": {"#g": "gsi1_pk", "#s": "gsi1_sk"},
            "ExpressionAttributeValues": {
                ":g": {"S": f"RESIDENT#{resident_id}"},
                ":device": {"S": "DEVICE#"},
            },
            "ProjectionExpression": "#g, #s",
        }
    )
    out = []
    for i in items:
        sk = i.get("gsi1_sk")
        if isinstance(sk, str) and model.DEVICE_ID_RE.match(sk.removeprefix("DEVICE#")):
            out.append(sk.removeprefix("DEVICE#"))
    return sorted(set(out))


REGISTRY_PROJECTION = [
    "pk",
    "sk",
    "display_name",
    "home_id",
    "hr_bpm",
    "spo2_pct",
    "skin_temp_c",
    "updated_at",
    "updated_by_name",
    "resident_id",
    "assigned_at",
]

ALERT_PROJECTION = [
    "pk",
    "sk",
    "alert_type",
    "device_id",
    "status",
    "event_ts",
    "severity",
    "acknowledged_at",
    "acknowledged_by_role",
    "acknowledged_by",
    "ack_note",
    "escalated_at",
    "notified_at",
    "resolved_at",
    "resolved_by",
    "resolved_by_role",
    "cancelled_at",
    "cancelled_by_role",
    "cancelled_by",
]


def resident_alerts(resident_id: str) -> list[dict[str, Any]]:
    """Pattern 12, newest first."""
    names = {f"#a{i}": name for i, name in enumerate(ALERT_PROJECTION)}
    return query_all(
        {
            "TableName": os.environ["ALERTS_TABLE"],
            "Select": "SPECIFIC_ATTRIBUTES",
            "ScanIndexForward": False,
            "KeyConditionExpression": "#a0 = :pk",
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": {":pk": {"S": f"RESIDENT#{resident_id}"}},
            "ProjectionExpression": ", ".join(names),
        }
    )


def load(resident_id: str) -> dict[str, Any]:
    """Every read for one resident. Raises NotFound if it has no profile."""
    device_ids = resident_devices(resident_id)
    keys = [
        (f"RESIDENT#{resident_id}", "PROFILE"),
        (f"THRESHOLDS#{resident_id}", "THRESHOLDS"),
    ]
    keys += [(f"DEVICE#{d}", "ASSIGNMENT") for d in device_ids]
    registry = {
        (i.get("pk"), i.get("sk")): i
        for i in batch_get(os.environ["REGISTRY_TABLE"], keys, REGISTRY_PROJECTION)
    }
    profile = registry.get((f"RESIDENT#{resident_id}", "PROFILE"))
    if profile is None:
        raise NotFound

    rows = []
    for d in device_ids:
        assignment = registry.get((f"DEVICE#{d}", "ASSIGNMENT"))
        # The base item is the truth: the index may still list a moved device.
        if assignment is not None and assignment.get("resident_id") == resident_id:
            rows.append((d, d.split("-", 1)[0], assignment.get("assigned_at")))

    latest_keys = [
        (f"DEVICE#{d}", f"MSG#{m}") for d, t, _ in rows for m in model.MESSAGES[t]
    ]
    latest = {}
    if latest_keys:
        for item in batch_get(
            os.environ["LATEST_TABLE"], latest_keys, ["pk", "sk", "entries"]
        ):
            pk, sk = item.get("pk"), item.get("sk")
            if isinstance(pk, str) and isinstance(sk, str):
                latest[(pk.removeprefix("DEVICE#"), sk.removeprefix("MSG#"))] = (
                    item.get("entries")
                )

    thresholds_item = registry.get((f"THRESHOLDS#{resident_id}", "THRESHOLDS"))
    limits, invalid = thresholds.resolve(thresholds_item)
    if invalid:
        logger.warning(
            "invalid thresholds", extra={"resident_id": resident_id, "fields": invalid}
        )

    alerts = [a for a in map(model.alert_from_item, resident_alerts(resident_id)) if a]
    return {
        "profile": profile,
        "devices": model.build_devices(rows, latest),
        "thresholds_item": thresholds_item,
        "limits": limits,
        "alerts": alerts,
    }


def names_for(alerts: list[model.Alert]) -> tuple[dict, int]:
    return actors.names(
        cognito_client, os.environ["USER_POOL_ID"], model.actors(alerts)
    )


# ---- handler ----


def detail(
    sub: str, groups: frozenset[str], event: Any, now: datetime
) -> dict[str, Any]:
    is_staff = bool(groups & STAFF_GROUPS)
    if not is_staff and "caregiver" not in groups:
        logger.warning(
            LOG_MESSAGE, extra={"outcome": "forbidden", "groups": sorted(groups)}
        )
        return error(403, "forbidden", "You can't see this resident.")
    if event.get("queryStringParameters") or event.get("body"):
        return error(400, "bad_request", "This request takes no parameters or body.")
    resident_id = (event.get("pathParameters") or {}).get("resident_id")
    if not isinstance(resident_id, str) or not model.RESIDENT_ID_RE.match(resident_id):
        return error(400, "bad_request", "The request is not valid.")
    logger.append_keys(resident_id=resident_id)
    not_found = error(404, "not_found", "No such resident.")
    if not is_staff and not is_linked(sub, resident_id):
        logger.info(LOG_MESSAGE, extra={"outcome": "not_found", "reason": "link"})
        return not_found
    try:
        data = load(resident_id)
    except NotFound:
        logger.info(LOG_MESSAGE, extra={"outcome": "not_found", "reason": "profile"})
        return not_found

    names, failures = names_for(data["alerts"])
    devices = data["devices"]
    body = {
        "resident": model.resident_view(
            resident_id, data["profile"], devices, data["alerts"], data["limits"], now
        ),
        "wearable": model.device_view(model.pick(devices, model.WEARABLE), now),
        "bedroom": model.device_view(model.pick(devices, model.BEDROOM), now),
        "alerts": [model.alert_view(a, names) for a in data["alerts"]],
        "thresholds": model.thresholds_view(
            data["limits"], data["thresholds_item"], is_staff
        ),
        "threshold_history": [],
    }
    logger.info(
        LOG_MESSAGE,
        extra={
            "outcome": "ok",
            "devices": len(devices),
            "alerts": len(data["alerts"]),
            "name_lookup_failures": failures,
        },
    )
    return response(200, body)


def mine(sub: str, groups: frozenset[str], event: Any, now: datetime) -> dict[str, Any]:
    if "caregiver" not in groups:
        logger.warning(
            LOG_MESSAGE, extra={"outcome": "forbidden", "groups": sorted(groups)}
        )
        return error(403, "forbidden", "Only caregivers have linked residents.")
    if event.get("queryStringParameters") or event.get("body"):
        return error(400, "bad_request", "This request takes no parameters or body.")
    resident_ids = linked_residents(sub)[:MAX_LINKED]
    residents, alerts = [], []
    for resident_id in resident_ids:
        try:
            data = load(resident_id)
        except NotFound:
            continue  # a link to an offboarded resident
        residents.append(
            model.resident_view(
                resident_id,
                data["profile"],
                data["devices"],
                data["alerts"],
                data["limits"],
                now,
            )
        )
        alerts.extend(data["alerts"])
    names, failures = names_for(alerts)
    views = [model.alert_view(a, names) for a in alerts]
    views.sort(key=lambda a: (a["event_ts"], a["alert_id"]), reverse=True)
    logger.info(
        LOG_MESSAGE,
        extra={
            "outcome": "ok",
            "residents": len(residents),
            "alerts": len(views),
            "name_lookup_failures": failures,
        },
    )
    return response(200, {"residents": residents, "alerts": views})


def handle(event: Any, now: datetime) -> dict[str, Any]:
    logger.append_keys(request_id=request_id_of(event))
    claims = claims_of(event)
    sub = claims.get("sub") if claims else None
    if not isinstance(sub, str) or not SUB_RE.match(sub):
        logger.warning(LOG_MESSAGE, extra={"outcome": "no_claims"})
        return error(401, "unauthorized", "Sign in again.")
    logger.append_keys(sub=sub)
    groups = parse_groups(claims.get("cognito:groups"))
    route = event.get("routeKey")
    if route == ROUTE_DETAIL:
        return detail(sub, groups, event, now)
    if route == ROUTE_MINE:
        return mine(sub, groups, event, now)
    return error(404, "not_found", "No such route.")


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    logger.remove_keys(["request_id", "sub", "resident_id"])
    try:
        return handle(event, datetime.now(UTC))
    except Exception as exc:  # noqa: BLE001 - every failure becomes a clean 500
        logger.error(LOG_MESSAGE, extra={"outcome": "error", "error": _error_code(exc)})
        return error(500, "internal_error", "Could not load the resident. Try again.")
