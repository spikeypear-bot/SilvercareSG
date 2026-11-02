"""alert-processor Lambda (docs/features/alerts-core.md, criteria 13-20).

Fed by an SQS event source mapping on silvercare-alerts (batch size 10, no batching
window, ReportBatchItemFailures). Each record is handled on its own; a failed record
is reported by its messageId only, so SQS redelivers just that one and, after 5
receives, moves it to silvercare-alerts-dlq (which raises an alarm).

For each record:

0. Parse and validate the alert message (message.py). An invalid body is logged with
   a reason code and reported as a failure, so it ends in the DLQ, never dropped.
1. Enrich (fall only): add details.latest_vitals (hr_bpm, spo2_pct, ts) from the
   device's newest vitals entry in silvercare-latest, if there is one.
2. Store: conditional PutItem on silvercare-alerts (attribute_not_exists(pk)). If the
   alert is already there (a retry), read it back and use it from here on.
   Unassigned alerts get notification = "skipped_unassigned" in the same PutItem.
3. Archive: PutObject alerts/date=<event_ts UTC date>/<alert_id>.json in the history
   bucket (the same key on a retry, so one object per alert).
4. Notify (resident assigned, item has no notified_at): SNS Publish with the
   resident_id message attribute (subscriptions filter on it), log and emit the
   dispatch latency, then set notified_at once. A crash between Publish and the update
   sends the email again on retry: a duplicate is accepted, a missing one isn't.

Any AWS error fails only that record. Logs hold one line per record with alert_id,
alert_type, device_id, resident_id, and the outcome (plus a reason or error code),
never details, vitals, email addresses, or the email body.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from functools import cache
from typing import Any

import boto3
import email_text
from aws_lambda_powertools import Logger
from aws_lambda_powertools.metrics import EphemeralMetrics, MetricUnit
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config
from botocore.exceptions import ClientError
from message import UNASSIGNED, Alert, Rejection, log_ids, parse_alert

# The service name comes from POWERTOOLS_SERVICE_NAME, set by Terraform.
logger = Logger()

LOG_MESSAGE = "alert"
ARCHIVE_PREFIX = "alerts"
CONTENT_TYPE = "application/json"
LATENCY_METRIC = "AlertDispatchLatency"

STORED = "stored"
DUPLICATE = "duplicate"
NOTIFIED = "notified"
SKIPPED_UNASSIGNED = "skipped_unassigned"
INVALID = "invalid"
ERROR = "error"

_serializer = TypeSerializer()
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


@cache
def s3_client() -> Any:
    return boto3.client("s3")


@cache
def sns_client() -> Any:
    return boto3.client("sns")


# ---- pure helpers ----


def format_iso_ms(value: datetime) -> str:
    """ISO 8601 UTC with milliseconds and Z, e.g. 2026-10-18T03:14:07.900Z."""
    value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def alert_key(alert: Alert) -> dict[str, str]:
    return {
        "pk": f"RESIDENT#{alert.resident_id}",
        "sk": f"ALERT#{alert.event_ts}#{alert.alert_id}",
    }


def latest_vitals(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """{"hr_bpm", "spo2_pct", "ts"} from the newest vitals entry (values may be None)."""
    if not isinstance(entry, dict):
        return None
    return {
        "hr_bpm": entry.get("hr_bpm"),
        "spo2_pct": entry.get("spo2_pct"),
        "ts": entry.get("ts"),
    }


def build_item(
    alert: Alert, details: dict[str, Any], created_at: datetime
) -> dict[str, Any]:
    """The silvercare-alerts item (docs/data-model.md). No expires_at until resolved."""
    item: dict[str, Any] = {
        **alert_key(alert),
        "alert_id": alert.alert_id,
        "alert_type": alert.alert_type,
        "device_id": alert.device_id,
        "event_ts": alert.event_ts,
        "received_at": alert.received_at,
        "created_at": format_iso_ms(created_at),
        "status": "open",
        "source_message_id": alert.source_message_id,
        "details": details,
        "open_pk": "OPEN",
    }
    if alert.resident_id == UNASSIGNED:
        item["notification"] = SKIPPED_UNASSIGNED
    return item


def archive_key(alert: Alert) -> str:
    date = alert.event_dt.astimezone(UTC).strftime("%Y-%m-%d")
    return f"{ARCHIVE_PREFIX}/date={date}/{alert.alert_id}.json"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def archive_body(item: dict[str, Any]) -> bytes:
    """The item as one JSON object, keys sorted."""
    return json.dumps(
        item, default=_json_default, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def latency_ms(received_dt: datetime, published_dt: datetime) -> int:
    return round((published_dt - received_dt).total_seconds() * 1000)


def _serialize(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _serializer.serialize(v) for k, v in item.items()}


def _deserialize(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _deserializer.deserialize(v) for k, v in item.items()}


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if isinstance(code, str):
            return code
    return type(exc).__name__


def _is_condition_failure(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code")
    return code == "ConditionalCheckFailedException"


# ---- AWS steps ----


def read_latest_vitals(device_id: str) -> dict[str, Any] | None:
    """Step 1: the device's newest vitals entry, or None if it has none.

    Reads only the entries list (at most 5; read capacity is charged by item size
    anyway) and uses entries[0], the newest.
    """
    response = dynamodb_client().get_item(
        TableName=os.environ["LATEST_TABLE"],
        Key={"pk": {"S": f"DEVICE#{device_id}"}, "sk": {"S": "MSG#vitals"}},
        ProjectionExpression="#entries",
        ExpressionAttributeNames={"#entries": "entries"},
    )
    item = _deserialize(response["Item"]) if "Item" in response else {}
    entries = item.get("entries") or []
    return latest_vitals(entries[0]) if entries else None


def store_alert(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Step 2: (stored, item) or (duplicate, the existing item)."""
    client = dynamodb_client()
    table = os.environ["ALERTS_TABLE"]
    try:
        client.put_item(
            TableName=table,
            Item=_serialize(item),
            ConditionExpression="attribute_not_exists(pk)",
        )
        return STORED, item
    except ClientError as exc:
        if not _is_condition_failure(exc):
            raise
    key = {"pk": {"S": item["pk"]}, "sk": {"S": item["sk"]}}
    response = client.get_item(TableName=table, Key=key, ConsistentRead=True)
    if "Item" not in response:
        # Deleted between the two calls: fail the record so SQS redelivers it.
        raise RuntimeError("alert item missing after a failed put condition")
    return DUPLICATE, _deserialize(response["Item"])


def archive_alert(alert: Alert, item: dict[str, Any]) -> None:
    """Step 3: one object per alert, overwritten on a retry."""
    s3_client().put_object(
        Bucket=os.environ["HISTORY_BUCKET"],
        Key=archive_key(alert),
        Body=archive_body(item),
        ContentType=CONTENT_TYPE,
    )


def emit_latency_metric(alert_type: str, latency: int) -> None:
    """AlertDispatchLatency (Milliseconds, dimension alert_type only), printed by
    Powertools as one CloudWatch EMF log line (no PutMetricData call).

    service="" leaves out the service dimension Powertools would add from
    POWERTOOLS_SERVICE_NAME, so alert_type is the metric's only dimension (criterion
    19). A new instance per call keeps no state between records. A metric failure must
    not fail the record: the email is already sent, and a retry would send it again.
    """
    try:
        metrics = EphemeralMetrics(
            namespace=os.environ.get("POWERTOOLS_METRICS_NAMESPACE"), service=""
        )
        metrics.add_dimension(name="alert_type", value=alert_type)
        metrics.add_metric(
            name=LATENCY_METRIC, unit=MetricUnit.Milliseconds, value=latency
        )
        metrics.flush_metrics()
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("metric failed", extra={"error": type(exc).__name__})


def notify(alert: Alert, item: dict[str, Any]) -> int:
    """Step 4: publish the email, then set notified_at. Returns the dispatch latency."""
    sns_client().publish(
        TopicArn=os.environ["ALERTS_TOPIC_ARN"],
        Subject=email_text.subject(alert.alert_type, alert.resident_id),
        Message=email_text.body(item, alert.resident_id, os.environ["PORTAL_URL"]),
        MessageAttributes={
            "resident_id": {"DataType": "String", "StringValue": alert.resident_id},
        },
    )
    published_dt = datetime.now(UTC)
    latency = latency_ms(alert.received_dt, published_dt)
    emit_latency_metric(alert.alert_type, latency)

    try:
        dynamodb_client().update_item(
            TableName=os.environ["ALERTS_TABLE"],
            Key={"pk": {"S": item["pk"]}, "sk": {"S": item["sk"]}},
            UpdateExpression="SET notified_at = :now",
            ConditionExpression="attribute_not_exists(notified_at)",
            ExpressionAttributeValues={":now": {"S": format_iso_ms(published_dt)}},
        )
    except ClientError as exc:
        # Another delivery of the same message already set it: nothing more to do.
        if not _is_condition_failure(exc):
            raise
    return latency


def process(alert: Alert) -> tuple[str, int | None]:
    """Steps 1-4 for one valid alert. Returns (outcome, dispatch latency or None)."""
    details = dict(alert.details)
    if alert.alert_type == "fall":
        vitals = read_latest_vitals(alert.device_id)
        if vitals is not None:
            details["latest_vitals"] = vitals

    outcome, item = store_alert(build_item(alert, details, datetime.now(UTC)))
    archive_alert(alert, item)

    if alert.resident_id == UNASSIGNED:
        return (SKIPPED_UNASSIGNED if outcome == STORED else DUPLICATE), None
    if "notified_at" in item:
        return outcome, None
    return NOTIFIED, notify(alert, item)


def handle_record(record: Any) -> bool:
    """Process one SQS record. Returns False if it must be reported as failed."""
    body = record.get("body") if isinstance(record, dict) else None
    result = parse_alert(body)
    if isinstance(result, Rejection):
        logger.warning(
            LOG_MESSAGE,
            extra={**log_ids(body), "outcome": INVALID, "reason": result.reason},
        )
        return False

    ids = {
        "alert_id": result.alert_id,
        "alert_type": result.alert_type,
        "device_id": result.device_id,
        "resident_id": result.resident_id,
    }
    try:
        outcome, latency = process(result)
    except Exception as exc:  # noqa: BLE001 - any failure: retry only this record
        # Only the error code is logged; exception text could quote the request.
        logger.error(
            LOG_MESSAGE, extra={**ids, "outcome": ERROR, "error": _error_code(exc)}
        )
        return False

    extra: dict[str, Any] = {**ids, "outcome": outcome}
    if latency is not None:
        extra["dispatch_latency_ms"] = latency
    logger.info(LOG_MESSAGE, extra=extra)
    return True


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    records = (event.get("Records") or []) if isinstance(event, dict) else []
    failures: list[dict[str, str]] = []
    for record in records:
        if not handle_record(record):
            message_id = record.get("messageId") if isinstance(record, dict) else None
            if isinstance(message_id, str):
                failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}
