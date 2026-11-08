"""silvercare-shadow-sync: the hourly reconcile of every wearable's device shadow
(docs/features/device-alert-limits.md, criteria 6-12). Invoked by an EventBridge
schedule every hour, or by hand with `{}` (the event is ignored).

Each run:
1. Registry `by-type` `entity_type = DEVICE` (as pattern 4: eventually consistent,
   paginated, projecting pk, device_type, resident_id); keep wearables.
2. BatchGetItem `THRESHOLDS#<resident_id>` / `THRESHOLDS` for their residents (as
   pattern 29: up to 100 keys, UnprocessedKeys retried with back-off, projecting pk,
   hr_bpm, spo2_pct) and compute each wearable's limits (device_limits.py).
3. Per wearable: GetThingShadow (classic). No shadow (404), or `state.desired.limits`
   not equal to the computed limits: UpdateThingShadow with
   `{"state": {"desired": {"limits": ...}}}` only. Equal: nothing is written. Never
   `reported`, never a bedroom shadow, never a delete.

A failure on one wearable doesn't stop the others; the run then raises, so the Lambda's
Errors metric counts it (alarm silvercare-shadow-sync-errors, infra/shadow_sync.tf).
A failed read of the registry fails the whole run.

Logs: counts (wearables, updated, unchanged, failed, skipped) and failed device IDs
with an error code. Never limit values, thresholds, or resident IDs.
"""

from __future__ import annotations

import json
import os
import re
import time
from functools import cache
from typing import Any

import boto3
import device_limits
import thresholds
from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config

logger = Logger()

REGISTRY_INDEX = "by-type"
THRESHOLDS_SK = "THRESHOLDS"
WEARABLE = "wearable"
WEARABLE_ID_RE = re.compile(r"^wearable-[0-9]{4}\Z")
RESIDENT_ID_RE = re.compile(r"^res-[0-9]{4}\Z")
BATCH_GET_MAX_KEYS = 100
BATCH_GET_ATTEMPTS = 6
BATCH_GET_BACKOFF_S = 0.2
SHADOW_TIMEOUT_S = 2

_deserializer = TypeDeserializer()


class UnprocessedKeysError(Exception):
    """BatchGetItem still had unprocessed keys after every retry."""


class SyncFailedError(Exception):
    """At least one wearable's shadow could not be read or written."""


# ---- clients (one per container, created on first use) ----


@cache
def dynamodb_client() -> Any:
    return boto3.client(
        "dynamodb",
        config=Config(retries={"mode": "adaptive"}),
        endpoint_url=os.environ.get("DYNAMODB_ENDPOINT"),
    )


@cache
def iot_data_client() -> Any:
    """The account's IoT data endpoint (iot:Data-ATS), set by Terraform."""
    return boto3.client(
        "iot-data",
        endpoint_url=f"https://{os.environ['IOT_DATA_ENDPOINT']}",
        config=Config(
            connect_timeout=SHADOW_TIMEOUT_S,
            read_timeout=SHADOW_TIMEOUT_S,
            retries={"mode": "standard"},
        ),
    )


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


def read_devices() -> list[dict[str, Any]]:
    """Pattern 4: every device, only pk, device_type, resident_id."""
    return query_all(
        {
            "TableName": os.environ["REGISTRY_TABLE"],
            "IndexName": REGISTRY_INDEX,
            "Select": "SPECIFIC_ATTRIBUTES",
            "KeyConditionExpression": "#type = :device",
            "ExpressionAttributeNames": {
                "#type": "entity_type",
                "#pk": "pk",
                "#device_type": "device_type",
                "#resident": "resident_id",
            },
            "ExpressionAttributeValues": {":device": {"S": "DEVICE"}},
            "ProjectionExpression": "#pk, #device_type, #resident",
        }
    )


def batch_get(keys: list[dict[str, Any]], projection: list[str]) -> list[dict]:
    """Eventually consistent BatchGetItem of `keys` on the registry, at most 100 keys
    per call, retrying UnprocessedKeys with back-off. Returns deserialized items."""
    table = os.environ["REGISTRY_TABLE"]
    client = dynamodb_client()
    names = {f"#a{i}": name for i, name in enumerate(projection)}
    items: list[dict[str, Any]] = []
    for start in range(0, len(keys), BATCH_GET_MAX_KEYS):
        pending: dict[str, Any] | None = {
            table: {
                "Keys": keys[start : start + BATCH_GET_MAX_KEYS],
                "ProjectionExpression": ", ".join(names),
                "ExpressionAttributeNames": names,
            }
        }
        for attempt in range(BATCH_GET_ATTEMPTS):
            if attempt:
                time.sleep(BATCH_GET_BACKOFF_S * 2 ** (attempt - 1))
            response = client.batch_get_item(RequestItems=pending)
            items.extend(
                _deserialize(i) for i in response.get("Responses", {}).get(table, [])
            )
            pending = response.get("UnprocessedKeys") or None
            if not pending:
                break
        if pending:
            raise UnprocessedKeysError("BatchGetItem left unprocessed keys")
    return items


def read_thresholds(resident_ids: list[str]) -> dict[str, dict[str, Any]]:
    """The THRESHOLDS items of these residents, by resident ID (missing: no item)."""
    keys = [
        {"pk": {"S": f"THRESHOLDS#{r}"}, "sk": {"S": THRESHOLDS_SK}}
        for r in resident_ids
    ]
    items = batch_get(keys, ["pk", "hr_bpm", "spo2_pct"])
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        pk = item.get("pk")
        if isinstance(pk, str) and pk.startswith("THRESHOLDS#"):
            out[pk.removeprefix("THRESHOLDS#")] = item
    return out


# ---- pure logic ----


def wearables_from_items(
    items: list[dict[str, Any]],
) -> tuple[dict[str, str | None], int]:
    """({device_id: resident_id or None}, skipped). A wearable with a malformed ID or
    resident_id is skipped: its shadow is left as it is rather than given limits that
    may be looser than its resident's."""
    wearables: dict[str, str | None] = {}
    skipped = 0
    for item in items:
        if item.get("device_type") != WEARABLE:
            continue
        pk = item.get("pk")
        resident_id = item.get("resident_id")
        id_ok = isinstance(pk, str) and bool(WEARABLE_ID_RE.match(pk[len("DEVICE#") :]))
        resident_ok = resident_id is None or (
            isinstance(resident_id, str) and bool(RESIDENT_ID_RE.match(resident_id))
        )
        if not (id_ok and pk.startswith("DEVICE#") and resident_ok):
            skipped += 1
            continue
        wearables[pk.removeprefix("DEVICE#")] = resident_id
    return wearables, skipped


def limits_for(
    resident_id: str | None, items: dict[str, dict[str, Any]]
) -> dict[str, dict[str, int]]:
    """The wearable's limits: fixed when unassigned, else from the resident's resolved
    thresholds (no item or invalid limits: the defaults)."""
    if resident_id is None:
        return device_limits.compute_limits(None)
    resolved, _ = thresholds.resolve(items.get(resident_id))
    return device_limits.compute_limits(resolved)


def desired_limits(document: dict[str, Any] | None) -> Any:
    """`state.desired.limits` of a shadow document, or None."""
    if not isinstance(document, dict):
        return None
    state = document.get("state")
    desired = state.get("desired") if isinstance(state, dict) else None
    return desired.get("limits") if isinstance(desired, dict) else None


def needs_update(document: dict[str, Any] | None, limits: dict[str, Any]) -> bool:
    current = desired_limits(document)
    return not (device_limits.is_valid(current) and current == limits)


# ---- shadows ----


def get_shadow(device_id: str) -> dict[str, Any] | None:
    """The classic shadow document, or None if the thing has no shadow (404)."""
    try:
        result = iot_data_client().get_thing_shadow(thingName=device_id)
    except Exception as exc:
        if _error_code(exc) == "ResourceNotFoundException":
            return None
        raise
    document = json.loads(result["payload"].read())
    return document if isinstance(document, dict) else None


def write_desired(device_id: str, limits: dict[str, Any]) -> None:
    payload = json.dumps({"state": {"desired": {"limits": limits}}})
    iot_data_client().update_thing_shadow(
        thingName=device_id, payload=payload.encode("utf-8")
    )


def sync_one(device_id: str, limits: dict[str, Any]) -> bool:
    """True if the shadow was written, False if it already had these limits."""
    if not needs_update(get_shadow(device_id), limits):
        return False
    write_desired(device_id, limits)
    return True


# ---- handler ----


def run() -> dict[str, int]:
    wearables, skipped = wearables_from_items(read_devices())
    resident_ids = sorted({r for r in wearables.values() if r is not None})
    items = read_thresholds(resident_ids) if resident_ids else {}

    updated = unchanged = 0
    failed: list[dict[str, str]] = []
    for device_id in sorted(wearables):
        limits = limits_for(wearables[device_id], items)
        try:
            if sync_one(device_id, limits):
                updated += 1
            else:
                unchanged += 1
        except Exception as exc:  # noqa: BLE001 - one wearable never stops the others
            failed.append({"device_id": device_id, "error": _error_code(exc)})

    counts = {
        "wearables": len(wearables),
        "updated": updated,
        "unchanged": unchanged,
        "failed": len(failed),
        "skipped": skipped,
    }
    if failed:
        logger.error("shadow sync", extra={**counts, "failures": failed})
        raise SyncFailedError(f"{len(failed)} wearable shadows failed")
    logger.info("shadow sync", extra=counts)
    return counts


def lambda_handler(event: Any, context: Any) -> dict[str, int]:
    return run()
