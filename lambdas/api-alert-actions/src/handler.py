"""POST /residents/{resident_id}/alerts/{alert_id}/acknowledge, .../cancel, and
.../resolve (docs/data-model.md, Status updates and Track B route: resolve; patterns 13,
16, 25). Behind the HTTP API's
Cognito JWT authorizer (infra/api.tf), payload 2.0. Built like GET /devices and
GET /dashboard.

The API never writes the alert: it checks, then sends a `status_update` to the alerts
queue and answers 202 {alert_id, requested_status}; `alert-processor` applies it.
Because alert-processor drops an update it can't apply without telling anyone, every
check is here, in the data model's order:

1. the caller's current group (403): acknowledge `nurse`, `ops`, or `caregiver`;
   cancel `caregiver`; resolve `nurse` or `doctor`. `family` alone gets 403 everywhere,
   `doctor` alone on acknowledge and cancel.
2. the path and body (400): only `event_ts` and, for acknowledge and resolve, `note`
   (required on a resolve).
3. for a caregiver, the link to the resident (pattern 13, 404; never `unassigned`).
   Staff resolve any resident.
4. GetItem of the alert, strongly consistent (404 if missing).
5. the type rule (409; ops on a health alert 404, so ops can't learn it exists; a
   device problem can't be resolved by staff, 409).
6. the status: acknowledge needs `open`, cancel and resolve `open` or `acknowledged`
   (409).

The actor (`by` = sub, `by_name` = the `name` claim for a caregiver, null otherwise)
comes only from the token; a resolve's `by_role` is `nurse` if the caller is in `nurse`,
else `doctor`. `resident_id`, `alert_id`, and `event_ts` in the message come from the
stored alert's key.

Logs: outcome, action, by_role, the opaque resident and alert IDs, a fixed refusal
reason, the request ID, the caller's `sub`, and on a 403 `cognito:groups`. Never the
body, the note, the `name` claim, headers, or other claims.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from typing import Any

import boto3
import rules
from aws_lambda_powertools import Logger
from botocore.config import Config

logger = Logger()

LOG_MESSAGE = "alert action"
ALLOWED_GROUPS = {
    rules.ACKNOWLEDGE: frozenset({"nurse", "ops", "caregiver"}),
    rules.CANCEL: frozenset({"caregiver"}),
    rules.RESOLVE: frozenset({"nurse", "doctor"}),
}
GROUP_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}\Z")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9=+/_-]{1,64}\Z")
HEADERS = {"Content-Type": "application/json", "Cache-Control": "no-store"}


# ---- clients ----


@cache
def dynamodb_client() -> Any:
    return boto3.client(
        "dynamodb",
        config=Config(retries={"mode": "adaptive"}),
        endpoint_url=os.environ.get("DYNAMODB_ENDPOINT"),
    )


@cache
def sqs_client() -> Any:
    return boto3.client("sqs", config=Config(retries={"mode": "standard"}))


# ---- request ----


def parse_groups(value: Any) -> frozenset[str]:
    """`cognito:groups` as a set: a list, or a string such as "[nurse ops]"."""
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


def action_of(event: Any) -> str | None:
    route = event.get("routeKey") if isinstance(event, dict) else None
    if route == "POST /residents/{resident_id}/alerts/{alert_id}/acknowledge":
        return rules.ACKNOWLEDGE
    if route == "POST /residents/{resident_id}/alerts/{alert_id}/cancel":
        return rules.CANCEL
    if route == "POST /residents/{resident_id}/alerts/{alert_id}/resolve":
        return rules.RESOLVE
    return None


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


def iso_ms(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


# ---- AWS ----


def is_linked(sub: str, resident_id: str) -> bool:
    """Pattern 13. Never true for `unassigned` (no read)."""
    if resident_id == rules.UNASSIGNED:
        return False
    result = dynamodb_client().get_item(
        TableName=os.environ["REGISTRY_TABLE"],
        Key={"pk": {"S": f"USER#{sub}"}, "sk": {"S": f"RESIDENT#{resident_id}"}},
        ProjectionExpression="#pk",
        ExpressionAttributeNames={"#pk": "pk"},
    )
    return "Item" in result


def get_alert(request: rules.Request) -> dict[str, Any] | None:
    """Pattern 25's GetItem, strongly consistent, only the key, type, and status."""
    result = dynamodb_client().get_item(
        TableName=os.environ["ALERTS_TABLE"],
        Key={
            "pk": {"S": f"RESIDENT#{request.resident_id}"},
            "sk": {"S": f"ALERT#{request.event_ts}#{request.alert_id}"},
        },
        ConsistentRead=True,
        ProjectionExpression="#pk, #sk, #type, #status",
        ExpressionAttributeNames={
            "#pk": "pk",
            "#sk": "sk",
            "#type": "alert_type",
            "#status": "status",
        },
    )
    item = result.get("Item")
    if not item:
        return None
    return {k: next(iter(v.values()), None) for k, v in item.items()}


def send_update(message: dict[str, Any]) -> None:
    sqs_client().send_message(
        QueueUrl=os.environ["ALERTS_QUEUE_URL"],
        MessageBody=json.dumps(message, separators=(",", ":"), ensure_ascii=False),
    )


# ---- handler ----


def _linked_once(sub: str, resident_id: str) -> Callable[[], bool]:
    memo: list[bool] = []

    def linked() -> bool:
        if not memo:
            memo.append(is_linked(sub, resident_id))
        return memo[0]

    return linked


def _key_parts(item: dict[str, Any], request: rules.Request) -> tuple[str, str, str]:
    """(resident_id, event_ts, alert_id) from the stored item's key."""
    pk, sk = item.get("pk"), item.get("sk")
    if not isinstance(pk, str) or not isinstance(sk, str):
        raise rules.not_found("item_key")
    resident_id = pk.removeprefix("RESIDENT#")
    event_ts, _, alert_id = sk.removeprefix("ALERT#").rpartition("#")
    if (resident_id, event_ts, alert_id) != (
        request.resident_id,
        request.event_ts,
        request.alert_id,
    ):
        raise rules.not_found("item_key")
    return resident_id, event_ts, alert_id


def handle(event: Any, now: datetime) -> dict[str, Any]:
    logger.append_keys(request_id=request_id_of(event))
    action = action_of(event)
    claims = claims_of(event)
    if claims is None or action is None:
        logger.warning(LOG_MESSAGE, extra={"outcome": "no_claims"})
        return error(401, "unauthorized", "Sign in again.")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not rules.SUB_RE.match(sub):
        logger.warning(LOG_MESSAGE, extra={"outcome": "invalid_sub", "action": action})
        return error(401, "unauthorized", "Sign in again.")
    logger.append_keys(sub=sub, action=action)

    groups = parse_groups(claims.get("cognito:groups")) & ALLOWED_GROUPS[action]
    if not groups:
        all_groups = sorted(parse_groups(claims.get("cognito:groups")))
        logger.warning(
            LOG_MESSAGE, extra={"outcome": "forbidden", "groups": all_groups}
        )
        return error(403, "forbidden", "You can't do this to an alert.")

    try:
        request = rules.parse_request(
            action, event.get("pathParameters"), body_text_of(event)
        )
        logger.append_keys(resident_id=request.resident_id, alert_id=request.alert_id)
        linked = _linked_once(sub, request.resident_id)

        # A caregiver (with no nurse or ops group) is checked for the link before the
        # alert is read, so an unlinked caregiver learns nothing about it.
        if groups == {"caregiver"} and not linked():
            raise rules.not_found("link")

        item = get_alert(request)
        if item is None:
            raise rules.not_found("alert")
        alert_type = item.get("alert_type")
        if not isinstance(alert_type, str):
            raise rules.not_found("alert_type")

        if action == rules.ACKNOWLEDGE:
            by_role = rules.acknowledge_role(groups, alert_type, linked)
        elif action == rules.RESOLVE:
            by_role = rules.resolve_role(groups)
            rules.check_resolve(alert_type)
        else:
            by_role = "caregiver"
            rules.check_cancel(alert_type)
        rules.check_status(action, item.get("status"))

        if request.note is not None and not rules.note_allowed(action, by_role):
            raise rules.bad_request("note_not_allowed")
        by_name = None
        if by_role == "caregiver":
            by_name = claims.get("name")
            if not rules.valid_by_name(by_name):
                # alert-processor would drop the update: the account needs a name.
                raise rules.Refused(
                    403,
                    "account_incomplete",
                    "Your account has no name set. Ask the care centre to add it.",
                    "by_name",
                )
        resident_id, event_ts, alert_id = _key_parts(item, request)
    except rules.Refused as refused:
        logger.warning(
            LOG_MESSAGE,
            extra={"outcome": f"refused_{refused.status}", "reason": refused.reason},
        )
        return error(refused.status, refused.code, refused.message)

    send_update(
        rules.status_update(
            resident_id=resident_id,
            alert_id=alert_id,
            event_ts=event_ts,
            action=action,
            by_role=by_role,
            by=sub,
            by_name=by_name,
            note=request.note,
            changed_at=iso_ms(now),
        )
    )
    logger.info(
        LOG_MESSAGE,
        extra={
            "outcome": "sent",
            "by_role": by_role,
            "has_note": request.note is not None,
        },
    )
    requested = rules.REQUESTED_STATUS[action]
    return response(202, {"alert_id": alert_id, "requested_status": requested})


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    logger.remove_keys(["request_id", "sub", "action", "resident_id", "alert_id"])
    try:
        return handle(event, datetime.now(UTC))
    except Exception as exc:  # noqa: BLE001 - every failure becomes a clean 500
        logger.error(LOG_MESSAGE, extra={"outcome": "error", "error": _error_code(exc)})
        return error(500, "internal_error", "Could not update the alert. Try again.")
