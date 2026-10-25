"""Write rule for silvercare-latest (docs/data-model.md, "Write rule (idempotent)").

`merge_entries` and `build_item` are pure. `add_message` does the AWS calls: a strongly
consistent GetItem, then a PutItem conditioned on the version that was read, retried on
a lost race at most MAX_ATTEMPTS times in total.
"""

from __future__ import annotations

from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

MAX_ENTRIES = 5
MAX_ATTEMPTS = 3

# Item types without watch_type stay out of the sparse by-message index.
UNWATCHED_MESSAGES = frozenset({"fall"})

STORED = "stored"
DUPLICATE = "duplicate"
TOO_OLD = "too_old"

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


class WriteConflictError(Exception):
    """Every attempt lost the version race. Raised so Lambda's async retry tries again."""


def item_key(device_id: str, message: str) -> dict[str, str]:
    return {"pk": f"DEVICE#{device_id}", "sk": f"MSG#{message}"}


def merge_entries(
    existing: list[dict[str, Any]], entry: dict[str, Any]
) -> tuple[str, list[dict[str, Any]] | None]:
    """Return (outcome, new entries). New entries is None unless the outcome is stored."""
    if any(e.get("message_id") == entry["message_id"] for e in existing):
        return DUPLICATE, None
    # sorted() is stable, also with reverse=True: on equal ts, existing entries stay
    # ahead of the new one.
    merged = sorted([*existing, entry], key=lambda e: e["ts"], reverse=True)
    kept = merged[:MAX_ENTRIES]
    if not any(e is entry for e in kept):
        return TOO_OLD, None
    return STORED, kept


def build_item(
    device_id: str,
    device_type: str,
    message: str,
    entries: list[dict[str, Any]],
    version: int,
) -> dict[str, Any]:
    """The full item for PutItem (attributes from docs/data-model.md)."""
    item: dict[str, Any] = {
        **item_key(device_id, message),
        "device_type": device_type,
        "entries": entries,
        "latest": entries[0],
        "latest_ts": entries[0]["ts"],
        "version": version,
    }
    if message not in UNWATCHED_MESSAGES:
        item["watch_type"] = message
    return item


def _serialize(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _serializer.serialize(v) for k, v in item.items()}


def _deserialize(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _deserializer.deserialize(v) for k, v in item.items()}


def add_message(
    client: Any,
    table_name: str,
    device_id: str,
    device_type: str,
    message: str,
    entry: dict[str, Any],
) -> str:
    """Add one entry to the device's latest 5. Returns stored, duplicate, or too_old.

    Raises WriteConflictError after MAX_ATTEMPTS lost races. Any other AWS error
    (throttling, timeouts, access denied) is raised as it is.
    """
    key = _serialize(item_key(device_id, message))
    for _ in range(MAX_ATTEMPTS):
        response = client.get_item(TableName=table_name, Key=key, ConsistentRead=True)
        current = _deserialize(response["Item"]) if "Item" in response else None
        read_version = int(current["version"]) if current else 0
        existing = list(current.get("entries", [])) if current else []

        outcome, entries = merge_entries(existing, entry)
        if entries is None:
            return outcome

        item = build_item(device_id, device_type, message, entries, read_version + 1)
        try:
            client.put_item(
                TableName=table_name,
                Item=_serialize(item),
                ConditionExpression="attribute_not_exists(pk) OR version = :read_version",
                ExpressionAttributeValues={":read_version": {"N": str(read_version)}},
            )
        except ClientError as exc:
            if (
                exc.response.get("Error", {}).get("Code")
                != "ConditionalCheckFailedException"
            ):
                raise
            continue
        return STORED
    raise WriteConflictError(f"version conflict after {MAX_ATTEMPTS} attempts")
