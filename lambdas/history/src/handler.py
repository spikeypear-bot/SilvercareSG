"""History Lambda (docs/features/ingest-history.md).

Fed by an SQS event source mapping on silvercare-history, whose messages are the ingest
IoT rules' output: the device payload plus `device_id`, `message`, and `received_at`.
For each batch it groups the records by message type and UTC date and writes one
gzipped JSON Lines object per group to the HISTORY_BUCKET bucket:

    telemetry/message=<message>/date=<yyyy-mm-dd>/<yyyymmddTHHMMSSZ>-<uuid4>.jsonl.gz

Messages are stored as received (invalid ones too; cleaning happens later in Lake
Formation). A body that isn't a JSON object is stored under `message=unparseable` as
`{"raw_body": "<body>"}`. If a group's PutObject fails, that group's records are
returned as batchItemFailures so only they are retried; nothing is raised for a record
that can't be parsed.

Logs hold only counts, message types, dates, and object keys: never bodies, field
values, device IDs, or message IDs.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from typing import Any

import boto3
from aws_lambda_powertools import Logger

# The service name comes from POWERTOOLS_SERVICE_NAME, set by Terraform.
logger = Logger()

KEY_PREFIX = "telemetry"
MESSAGE_RE = re.compile(r"[a-z]{1,16}")
UNKNOWN_MESSAGE = "unknown"
UNPARSEABLE_MESSAGE = "unparseable"
CONTENT_TYPE = "application/gzip"


@cache
def s3_client() -> Any:
    """One S3 client per container, created on first use."""
    return boto3.client("s3")


# ---- parsing and grouping (pure, no AWS) ----


@dataclass
class Group:
    """Records of one message type and UTC date, in the order received."""

    message: str
    date: str
    message_ids: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


def _reject_constant(value: str) -> Any:
    # NaN and Infinity aren't JSON; re-serialized they would make an invalid line.
    raise ValueError(f"non-standard JSON constant {value}")


def parse_body(body: Any) -> tuple[str, dict[str, Any]]:
    """Return (message type, object to store) for one SQS record body."""
    text = body if isinstance(body, str) else ("" if body is None else str(body))
    try:
        parsed = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        parsed = None
    if not isinstance(parsed, dict):
        return UNPARSEABLE_MESSAGE, {"raw_body": text}
    message = parsed.get("message")
    if isinstance(message, str) and MESSAGE_RE.fullmatch(message):
        return message, parsed
    return UNKNOWN_MESSAGE, parsed


def _date_from_epoch_ms(value: Any) -> str | None:
    """yyyy-mm-dd (UTC) of a non-negative integer of epoch milliseconds, else None."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def record_date(stored: dict[str, Any], record: dict[str, Any], now: datetime) -> str:
    """UTC date from received_at, else the SQS SentTimestamp, else the time of writing."""
    date = _date_from_epoch_ms(stored.get("received_at"))
    if date is not None:
        return date
    sent = (record.get("attributes") or {}).get("SentTimestamp")
    if isinstance(sent, str) and sent.isdigit():
        date = _date_from_epoch_ms(int(sent))
        if date is not None:
            return date
    return now.astimezone(UTC).strftime("%Y-%m-%d")


def serialize(stored: dict[str, Any]) -> str:
    """One compact JSON line (no extra whitespace)."""
    return json.dumps(stored, separators=(",", ":"))


def group_records(records: list[dict[str, Any]], now: datetime) -> list[Group]:
    """Group records by (message type, UTC date), keeping arrival order."""
    groups: dict[tuple[str, str], Group] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        message, stored = parse_body(record.get("body"))
        date = record_date(stored, record, now)
        group = groups.setdefault((message, date), Group(message, date))
        message_id = record.get("messageId")
        if isinstance(message_id, str):
            group.message_ids.append(message_id)
        group.lines.append(serialize(stored))
    return list(groups.values())


def object_key(message: str, date: str, now: datetime, unique: str) -> str:
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{KEY_PREFIX}/message={message}/date={date}/{stamp}-{unique}.jsonl.gz"


def encode_lines(lines: list[str]) -> bytes:
    """gzip of the lines as UTF-8 JSON Lines, ending with a newline."""
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


# ---- AWS ----


def write_group(bucket: str, group: Group) -> str:
    """Write one group's object and return its key. Raises on failure."""
    key = object_key(group.message, group.date, datetime.now(UTC), str(uuid.uuid4()))
    s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=encode_lines(group.lines),
        ContentType=CONTENT_TYPE,
    )
    return key


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if isinstance(code, str):
            return code
    return type(exc).__name__


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    bucket = os.environ["HISTORY_BUCKET"]
    records = (event.get("Records") or []) if isinstance(event, dict) else []
    logger.info("history batch", extra={"record_count": len(records)})

    failures: list[dict[str, str]] = []
    for group in group_records(records, datetime.now(UTC)):
        details = {
            "message_type": group.message,
            "date": group.date,
            "record_count": len(group.lines),
        }
        try:
            key = write_group(bucket, group)
        except Exception as exc:  # noqa: BLE001 - retry only this group, whatever failed
            # Any failure (AWS error, timeout, bug): retry only this group's records.
            # Only the error code is logged; exception text could quote the request.
            logger.warning(
                "history object write failed",
                extra={**details, "error": _error_code(exc)},
            )
            failures.extend({"itemIdentifier": mid} for mid in group.message_ids)
            continue
        logger.info("history object written", extra={**details, "key": key})

    return {"batchItemFailures": failures}
