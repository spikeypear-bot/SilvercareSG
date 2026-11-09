"""AppSync Events onSubscribe check (docs/features/live-alerts.md, criteria 7-12).

Direct Lambda integration on the `health` and `device` namespaces. AppSync has already
verified the caller's Cognito token; this decides which channels they may hear:

- doctor or nurse: `/<ns>/*` or `/<ns>/<resident_id>`, no DynamoDB call.
- caregiver: `/<ns>/<resident_id>` only, and only if the registry link item
  `USER#<sub>` / `RESIDENT#<resident_id>` exists (data-model pattern 13).
- anyone else (ops, family, no group): refused.

Check order: group, then channel shape, then the link read. Every refusal returns the
same text, so a refusal can't tell a caregiver whether a resident exists. Returning
None allows the subscription; {"error": ...} refuses it (AppSync Events direct Lambda
integration). A DynamoDB error refuses (fail closed).

Logs: outcome, namespace, sub (if valid), groups, resident_id (if valid). Never the
token, claims, or headers.
"""

from __future__ import annotations

import os
import re
from functools import cache
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from botocore.config import Config

logger = Logger()

LOG_MESSAGE = "subscribe"
REFUSED = {"error": "Unauthorized"}
NAMESPACES = frozenset({"health", "device"})
STAFF = frozenset({"doctor", "nurse"})
CAREGIVER = "caregiver"
WILDCARD = "*"
RESIDENT_ID_RE = re.compile(r"^res-[0-9]{4}\Z")
SUB_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
GROUP_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}\Z")


@cache
def dynamodb_client() -> Any:
    return boto3.client(
        "dynamodb",
        config=Config(retries={"mode": "standard"}, connect_timeout=1, read_timeout=2),
        endpoint_url=os.environ.get("DYNAMODB_ENDPOINT"),
    )


def groups_of(identity: Any) -> frozenset[str]:
    value = identity.get("groups") if isinstance(identity, dict) else None
    if not isinstance(value, list):
        return frozenset()
    return frozenset(g for g in value if isinstance(g, str) and GROUP_RE.match(g))


def parse_channel(path: Any) -> tuple[str, str] | None:
    """(namespace, target) for `/<ns>/<resident_id>` or `/<ns>/*`, else None."""
    if not isinstance(path, str):
        return None
    parts = path.split("/")
    if len(parts) != 3 or parts[0] != "":
        return None
    namespace, target = parts[1], parts[2]
    if namespace not in NAMESPACES:
        return None
    if target != WILDCARD and not RESIDENT_ID_RE.match(target):
        return None
    return namespace, target


def is_linked(sub: str, resident_id: str) -> bool:
    item = (
        dynamodb_client()
        .get_item(
            TableName=os.environ["REGISTRY_TABLE"],
            Key={"pk": {"S": f"USER#{sub}"}, "sk": {"S": f"RESIDENT#{resident_id}"}},
            ProjectionExpression="pk, sk",
        )
        .get("Item")
    )
    return item is not None


def decide(event: Any) -> tuple[bool, str]:
    """(allowed, outcome) for one subscribe request."""
    identity = event.get("identity") if isinstance(event, dict) else None
    groups = groups_of(identity)
    sub = identity.get("sub") if isinstance(identity, dict) else None
    valid_sub = isinstance(sub, str) and SUB_RE.match(sub) is not None
    logger.append_keys(
        sub=sub if valid_sub else "invalid", groups=sorted(groups), resident_id=None
    )

    staff = bool(groups & STAFF)
    if not staff and CAREGIVER not in groups:
        return False, "forbidden_group"

    info = event.get("info") if isinstance(event, dict) else None
    channel = info.get("channel") if isinstance(info, dict) else None
    parsed = parse_channel(channel.get("path") if isinstance(channel, dict) else None)
    if parsed is None:
        return False, "bad_channel"
    namespace, target = parsed
    logger.append_keys(
        namespace=namespace, resident_id=None if target == WILDCARD else target
    )

    if staff:
        return True, "allowed_staff"
    if target == WILDCARD:
        return False, "caregiver_wildcard"
    if not valid_sub:
        return False, "no_sub"
    if not is_linked(sub, target):
        return False, "not_linked"
    return True, "allowed_linked"


def _error_code(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = resp.get("Error", {}).get("Code")
        if isinstance(code, str):
            return code
    return type(exc).__name__


def lambda_handler(event: Any, context: Any) -> dict[str, str] | None:
    logger.remove_keys(["sub", "groups", "namespace", "resident_id"])
    try:
        allowed, outcome = decide(event)
    except Exception as exc:  # noqa: BLE001 - fail closed on any error
        logger.error(LOG_MESSAGE, extra={"outcome": "error", "error": _error_code(exc)})
        return dict(REFUSED)
    logger.info(LOG_MESSAGE, extra={"outcome": outcome})
    return None if allowed else dict(REFUSED)
