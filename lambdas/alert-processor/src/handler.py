"""alert-processor Lambda (docs/features/alerts-core.md, criteria 13-20;
docs/features/vitals-alerts.md, criteria 17-22).

Fed by an SQS event source mapping on silvercare-alerts (batch size 10, no batching
window, ReportBatchItemFailures). Each record is handled on its own; a failed record
is reported by its messageId only, so SQS redelivers just that one and, after 5
receives, moves it to silvercare-alerts-dlq (which raises an alarm).

For each record:

0. Parse and validate the alert message (message.py). An invalid body is logged with
   a reason code and reported as a failure, so it ends in the DLQ, never dropped.
0b. Hold back repeats (every type except fall): Query the resident's alerts with
   event_ts after (event_ts - 30 min) and up to this one's, same event_ts included
   (pattern 7). If another alert with the
   same alert_type and severity (and, for unassigned, the same device) is there, this
   one is suppressed: not stored, archived, or emailed, logged as "suppressed", and the
   SQS message is deleted. Times come from event_ts, so a retry decides the same way;
   an alert that is already stored is never suppressed afterwards.
1. Enrich (fall only): add details.latest_vitals (hr_bpm, spo2_pct, ts) from the
   device's newest vitals entry in silvercare-latest, if there is one.
2. Store: conditional PutItem on silvercare-alerts (attribute_not_exists(pk)). If the
   alert is already there (a retry), read it back and use it from here on.
   Unassigned alerts get notification = "skipped_unassigned" in the same PutItem.
3. Archive: PutObject alerts/date=<event_ts UTC date>/<alert_id>.json in the history
   bucket (the same key on a retry, so one object per alert).
4. Notify caregivers (resident assigned, item has no notified_at): SNS Publish with
   the resident_id message attribute (subscriptions filter on it), log and emit the
   dispatch latency, then set notified_at once. A crash between Publish and the update
   sends the email again on retry: a duplicate is accepted, a missing one isn't.
5. Route to the nurses (docs/features/alert-routing.md, criteria 1-4), by the stored
   item's severity (missing = critical, as old falls):
   - assigned critical, or unassigned (any severity): if the item has no escalated_at,
     Publish the at-once email to the nurses topic, then set escalated_at once. A
     failed Publish fails the record; the retry doesn't email caregivers again
     (notified_at is set). No escalation check.
   - assigned warning (docs/features/escalation.md, criteria 2-4): if the item is open
     and has no escalation_queued_at, SendMessage the check to this same queue with
     DelaySeconds 300, then set escalation_queued_at once. A failed send fails the
     record. A crash between the send and the update can queue a second check, which
     the check handling below makes harmless.
   The log line's `nurses` field says which: immediate, escalation_queued, or none
   (suppressed).

Escalation checks (docs/features/escalation.md, criteria 5-8), 300 s later, skip the
repeat check: strongly consistent GetItem of the alert (pattern 23). No item: failure.
A present status other than open (acknowledged, cancelled, resolved): not_escalated;
a missing status escalates (fail-safe). escalated_at
already set: duplicate. More than 20 minutes after created_at: not_escalated, reason
stale (an invalid created_at is not stale). Otherwise Publish to the nurses topic, then set escalated_at
once (a failed condition after the Publish is a duplicate). status stays open.

Status updates (docs/features/alert-cancel.md, criteria 8-14; changed by
docs/features/alert-routing.md, criteria 10-15) skip the repeat check. A resident
cancel (wearable) has a device target and changed_at = when the IoT rule received the
alert_cancel (server time); a caregiver cancel and a nurse acknowledge (Track B's API)
have an alert target. Find the targets: for a device target, a strongly consistent
Query of the resident's alerts up to changed_at (sk <= "ALERT#<changed_at>#~"),
filtered to that device and to items open or acknowledged, or waiting for this update's
follow-up (pattern 24), paginated; for an alert target, a strongly consistent GetItem
(pattern 25). A cancel changes open and acknowledged alerts, an acknowledge only open
ones. None: outcome no_open_alerts (device target) or not_open (alert target). For each
target: UpdateItem conditional on the statuses it may change from. A cancel sets
cancelled_at, cancelled_by_role, cancelled_by (caregiver sub), expires_at = changed_at +
30 days, and REMOVEs open_pk and the acknowledge's status_notified_at (so the cancel's
own follow-up is tracked); an acknowledge sets acknowledged_at, acknowledged_by, and
ack_note if there is a note. A failed condition is skipped without email. Then the
follow-up: caregivers through the alerts topic with the resident_id attribute (not for
unassigned); for a cancel, nurses too if the item has escalated_at or acknowledged_at;
then SET status_notified_at once (also for an unassigned acknowledge, which emails
nobody). A target whose status already equals new_status (set by this same update: the
same by_role, and for a caregiver or nurse the same sub) but has no
status_notified_at is a retry after a crash: it gets its follow-up now (a duplicate is
accepted, a missing one isn't).

Any AWS error fails only that record. Logs hold one line per record with alert_id,
alert_type, severity, device_id, resident_id, and the outcome (plus a reason or error
code; new alerts add nurses; checks add kind and escalation_delay_ms; status updates log
one line per alert with kind, new_status, by_role, and, for an acknowledge, has_note),
never details, vitals, limits, email addresses, the email body, or a status update's
by, by_name, or note.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
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
from message import (
    ALERT_TYPES,
    DEVICE_ID_RE,
    KIND_ESCALATION_CHECK,
    KIND_STATUS_UPDATE,
    ROLE_RESIDENT,
    SEVERITIES,
    SEVERITY_CRITICAL,
    STATUS_ACKNOWLEDGED,
    STATUS_CANCELLED,
    UNASSIGNED,
    UUID_RE,
    Alert,
    EscalationCheck,
    Rejection,
    StatusUpdate,
    check_body,
    log_ids,
    parse_message,
    parse_ts,
)

# The service name comes from POWERTOOLS_SERVICE_NAME, set by Terraform.
logger = Logger()

LOG_MESSAGE = "alert"
ARCHIVE_PREFIX = "alerts"
CONTENT_TYPE = "application/json"
LATENCY_METRIC = "AlertDispatchLatency"

# Repeat suppression (vitals-alerts criteria 17-19).
REPEAT_WINDOW = timedelta(minutes=30)
NEVER_SUPPRESSED = frozenset({"fall"})

STORED = "stored"
DUPLICATE = "duplicate"
NOTIFIED = "notified"
SKIPPED_UNASSIGNED = "skipped_unassigned"
SUPPRESSED = "suppressed"
INVALID = "invalid"
ERROR = "error"

# Escalation (docs/features/escalation.md; 5 minutes since alert-routing).
ESCALATION_DELAY_S = 300
STATUS_OPEN = "open"
# The new alert log line's `nurses` field (alert-routing criterion 5).
NURSES_IMMEDIATE = "immediate"
NURSES_ESCALATION_QUEUED = "escalation_queued"
NURSES_NONE = "none"
# Above the last retry of a failing check (300 s delay + 4 x 180 s visibility = 17
# min), so a check that keeps failing reaches the DLQ and its alarm instead of being
# dropped as stale. Only a late redrive is stale.
STALE_AFTER = timedelta(minutes=20)
STALE = "stale"
ESCALATED = "escalated"
NOT_ESCALATED = "not_escalated"
# Status values logged as they are; anything else is logged as "other", so a free-text
# value written by mistake never reaches the logs.
KNOWN_STATUSES = frozenset({"open", "acknowledged", "cancelled", "resolved"})

# Status updates (docs/features/alert-cancel.md, docs/features/alert-routing.md).
CANCELLED_RETENTION = timedelta(days=30)
UPDATED = "updated"
SKIPPED = "skipped"
NO_OPEN_ALERTS = "no_open_alerts"
NOT_OPEN = "not_open"
# The statuses each new_status may change (alert-routing criterion 10).
CHANGEABLE_FROM = {
    STATUS_CANCELLED: (STATUS_OPEN, STATUS_ACKNOWLEDGED),
    STATUS_ACKNOWLEDGED: (STATUS_OPEN,),
}
# What the device-target Query reads: enough for the update, the follow-up email, the
# retry rule, and the log line (never details or ack_note).
TARGET_ATTRIBUTES = (
    "pk",
    "sk",
    "alert_id",
    "alert_type",
    "severity",
    "device_id",
    "event_ts",
    "#status",
    "escalated_at",
    "status_notified_at",
    "acknowledged_at",
    "acknowledged_by",
    "cancelled_by",
    "cancelled_by_role",
)

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


@cache
def sqs_client() -> Any:
    return boto3.client("sqs")


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
        "severity": alert.severity,
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


def repeat_window(alert: Alert) -> tuple[str, str]:
    """The sort-key bounds of pattern 7: "ALERT#<event_ts - 30 min>" to
    "ALERT#<event_ts>#~" (vitals-alerts criterion 17).

    Keys are "ALERT#<event_ts>#<alert_id>", and "~" sorts after every alert_id
    character, so items at the same event_ts are returned too, including this alert's
    own item on a retry (which is_repeat needs to see). An item exactly 30 minutes
    before is returned by the lower bound but not counted by is_repeat.
    """
    start = format_iso_ms(alert.event_dt - REPEAT_WINDOW)
    return f"ALERT#{start}", f"ALERT#{alert.event_ts}#~"


def is_repeat(alert: Alert, items: list[dict[str, Any]]) -> bool:
    """True if another alert in the window (same event_ts included) holds this one
    back.

    Matches need the same alert_type and severity (a warning never holds back a
    critical alert, or the other way round), a different alert_id, and, for an
    unassigned device, the same device_id. An earlier alert at exactly 30 minutes
    before is outside the window. If this alert itself is already stored (a retry
    after it passed the check), it is never held back.
    """
    if alert.alert_type in NEVER_SUPPRESSED:
        return False
    window_start = alert.event_dt - REPEAT_WINDOW
    if any(item.get("alert_id") == alert.alert_id for item in items):
        return False
    for item in items:
        if item.get("alert_type") != alert.alert_type:
            continue
        if item.get("severity") != alert.severity:
            continue
        if alert.resident_id == UNASSIGNED and item.get("device_id") != alert.device_id:
            continue
        item_dt = parse_ts(item.get("event_ts"))
        if item_dt is None or not window_start < item_dt <= alert.event_dt:
            continue
        return True
    return False


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


def needs_escalation_check(item: dict[str, Any]) -> bool:
    """Criteria 3-4: queue a check only for an open item that has none queued yet."""
    return item.get("status") == STATUS_OPEN and "escalation_queued_at" not in item


def nurses_at_once(resident_id: str, item: dict[str, Any]) -> bool:
    """Alert-routing criterion 1: nurses are emailed at once for every unassigned
    alert and for critical ones; a stored item with no severity (falls stored before
    vitals alerts) counts as critical. Warnings from assigned devices escalate."""
    if resident_id == UNASSIGNED:
        return True
    return (item.get("severity") or SEVERITY_CRITICAL) == SEVERITY_CRITICAL


def check_key(check: EscalationCheck) -> dict[str, str]:
    return {
        "pk": f"RESIDENT#{check.resident_id}",
        "sk": f"ALERT#{check.event_ts}#{check.alert_id}",
    }


def status_reason(status: Any) -> str:
    """Reason for the not_escalated log line: "status:<value>" for a known status,
    "status:other" for any other present value (masked: it could be free text)."""
    if isinstance(status, str) and status in KNOWN_STATUSES:
        return f"status:{status}"
    return "status:other"


def escalation_decision(
    item: dict[str, Any], now: datetime | None = None
) -> tuple[str, str | None] | None:
    """Criterion 5: (outcome, reason) when the check sends no email, None to escalate.

    Order: status, then escalated_at, then staleness (`now` defaults to the current
    time).
    """
    now = now or datetime.now(UTC)
    status = item.get("status")
    # Fail-safe: a missing status escalates. Only a present value other than "open"
    # (acknowledged, cancelled, resolved, or anything else) stops it.
    if status is not None and status != STATUS_OPEN:
        return NOT_ESCALATED, status_reason(status)
    if "escalated_at" in item:
        return DUPLICATE, None
    # A check handled long after the alert was stored (e.g. redriven from the DLQ)
    # emails nobody. An invalid created_at is not treated as stale (fail-safe).
    created_dt = parse_ts(item.get("created_at"))
    if created_dt is not None and now - created_dt > STALE_AFTER:
        return NOT_ESCALATED, STALE
    return None


def escalation_delay_ms(item: dict[str, Any], published_dt: datetime) -> int | None:
    """Criterion 7: Publish time minus notified_at, or minus created_at when there is
    no notified_at (unassigned alerts). None if neither is a valid timestamp."""
    start = parse_ts(item.get("notified_at")) or parse_ts(item.get("created_at"))
    return latency_ms(start, published_dt) if start else None


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


def recent_alerts(alert: Alert) -> list[dict[str, Any]]:
    """Step 0b (pattern 7): the resident's alerts with event_ts in the 30 minutes up to
    this one. Strongly consistent, so an alert stored a moment ago is seen. Reads only
    the attributes the repeat check compares."""
    low, high = repeat_window(alert)
    client = dynamodb_client()
    items: list[dict[str, Any]] = []
    request: dict[str, Any] = {
        "TableName": os.environ["ALERTS_TABLE"],
        "KeyConditionExpression": "pk = :pk AND sk BETWEEN :low AND :high",
        "ExpressionAttributeValues": {
            ":pk": {"S": f"RESIDENT#{alert.resident_id}"},
            ":low": {"S": low},
            ":high": {"S": high},
        },
        "ProjectionExpression": "alert_id, alert_type, severity, device_id, event_ts",
        "ConsistentRead": True,
    }
    while True:
        response = client.query(**request)
        items.extend(_deserialize(item) for item in response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        request["ExclusiveStartKey"] = last_key


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
        Subject=email_text.subject(alert.alert_type, alert.resident_id, alert.severity),
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


def queue_escalation_check(alert: Alert, item: dict[str, Any]) -> None:
    """Step 5: send the delayed check, then set escalation_queued_at once.

    Send first: if the send fails the record fails and is retried; if the update fails
    after a successful send, the retry may queue a second check (harmless, criterion 6).
    """
    if not needs_escalation_check(item):
        return
    sqs_client().send_message(
        QueueUrl=os.environ["ALERTS_QUEUE_URL"],
        MessageBody=check_body(alert.alert_id, alert.resident_id, alert.event_ts),
        DelaySeconds=ESCALATION_DELAY_S,
    )
    try:
        dynamodb_client().update_item(
            TableName=os.environ["ALERTS_TABLE"],
            Key={"pk": {"S": item["pk"]}, "sk": {"S": item["sk"]}},
            UpdateExpression="SET escalation_queued_at = :now",
            ConditionExpression="attribute_not_exists(escalation_queued_at)",
            ExpressionAttributeValues={":now": {"S": format_iso_ms(datetime.now(UTC))}},
        )
    except ClientError as exc:
        # Another delivery already queued a check and set it: nothing more to do.
        if not _is_condition_failure(exc):
            raise


def notify_nurses(alert: Alert, item: dict[str, Any]) -> None:
    """Step 5 (alert-routing criteria 2-3, 6): publish the at-once nurse email, then
    set escalated_at once. Nothing if the nurses were already emailed (a retry), or
    if a redelivery finds the alert already acknowledged, cancelled, or resolved (a
    missing status counts as open, as for escalation checks).

    A failed Publish raises and fails the record; a crash between the Publish and the
    update emails the nurses again on retry (a duplicate is accepted).
    """
    if "escalated_at" in item:
        return
    if item.get("status", STATUS_OPEN) != STATUS_OPEN:
        return
    severity = item.get("severity") or SEVERITY_CRITICAL
    sns_client().publish(
        TopicArn=os.environ["NURSES_TOPIC_ARN"],
        Subject=email_text.subject(alert.alert_type, alert.resident_id, severity),
        Message=email_text.nurse_body(
            item, alert.resident_id, os.environ["PORTAL_URL"]
        ),
    )
    try:
        dynamodb_client().update_item(
            TableName=os.environ["ALERTS_TABLE"],
            Key={"pk": {"S": item["pk"]}, "sk": {"S": item["sk"]}},
            UpdateExpression="SET escalated_at = :now",
            ConditionExpression="attribute_not_exists(escalated_at)",
            ExpressionAttributeValues={":now": {"S": format_iso_ms(datetime.now(UTC))}},
        )
    except ClientError as exc:
        # Another delivery of the same message already set it: nothing more to do.
        if not _is_condition_failure(exc):
            raise


def process(alert: Alert) -> tuple[str, int | None, str]:
    """Steps 0b-5 for one valid alert. Returns (outcome, dispatch latency or None,
    the nurses route for the log line)."""
    if alert.alert_type not in NEVER_SUPPRESSED and is_repeat(
        alert, recent_alerts(alert)
    ):
        return SUPPRESSED, None, NURSES_NONE

    details = dict(alert.details)
    if alert.alert_type == "fall":
        vitals = read_latest_vitals(alert.device_id)
        if vitals is not None:
            details["latest_vitals"] = vitals

    outcome, item = store_alert(build_item(alert, details, datetime.now(UTC)))
    archive_alert(alert, item)

    latency = None
    if alert.resident_id == UNASSIGNED:
        outcome = SKIPPED_UNASSIGNED if outcome == STORED else DUPLICATE
    elif "notified_at" not in item:
        latency = notify(alert, item)
        outcome = NOTIFIED
    # else: a retry after the caregiver email went out (e.g. the nurse Publish or the
    # check's send failed last time); only the nurse step runs.

    if nurses_at_once(alert.resident_id, item):
        notify_nurses(alert, item)
        return outcome, latency, NURSES_IMMEDIATE
    queue_escalation_check(alert, item)
    return outcome, latency, NURSES_ESCALATION_QUEUED


# ---- escalation checks ----


def read_alert(check: EscalationCheck) -> dict[str, Any]:
    """The alert item the check points to (pattern 23), strongly consistent, so a
    status change made just before is seen. A missing item fails the record."""
    key = check_key(check)
    response = dynamodb_client().get_item(
        TableName=os.environ["ALERTS_TABLE"],
        Key={"pk": {"S": key["pk"]}, "sk": {"S": key["sk"]}},
        ConsistentRead=True,
    )
    if "Item" not in response:
        raise RuntimeError("alert item missing for escalation check")
    return _deserialize(response["Item"])


def escalate(check: EscalationCheck, item: dict[str, Any]) -> tuple[str, int | None]:
    """Publish the nurse email, then set escalated_at once. Returns (outcome, delay)."""
    sns_client().publish(
        TopicArn=os.environ["NURSES_TOPIC_ARN"],
        Subject=email_text.escalation_subject(
            item.get("alert_type"), check.resident_id
        ),
        Message=email_text.escalation_body(
            item, check.resident_id, os.environ["PORTAL_URL"]
        ),
    )
    published_dt = datetime.now(UTC)
    delay = escalation_delay_ms(item, published_dt)

    try:
        dynamodb_client().update_item(
            TableName=os.environ["ALERTS_TABLE"],
            Key={"pk": {"S": item["pk"]}, "sk": {"S": item["sk"]}},
            UpdateExpression="SET escalated_at = :now",
            ConditionExpression="attribute_not_exists(escalated_at)",
            ExpressionAttributeValues={":now": {"S": format_iso_ms(published_dt)}},
        )
    except ClientError as exc:
        # Another delivery of a check for this alert escalated it first.
        if not _is_condition_failure(exc):
            raise
        return DUPLICATE, delay
    return ESCALATED, delay


def _safe_item_fields(item: dict[str, Any]) -> dict[str, str]:
    """alert_type, severity, and device_id from a stored item for a log line, each
    only if it has its expected format (else "invalid"), so a hand-edited item can't
    put free text in the logs."""
    alert_type = item.get("alert_type")
    severity = item.get("severity") or email_text.DEFAULT_SEVERITY
    device_id = item.get("device_id")
    return {
        "alert_type": alert_type if alert_type in ALERT_TYPES else "invalid",
        "severity": severity if severity in SEVERITIES else "invalid",
        "device_id": (
            device_id
            if isinstance(device_id, str) and DEVICE_ID_RE.match(device_id)
            else "invalid"
        ),
    }


def _item_log_ids(check: EscalationCheck, item: dict[str, Any]) -> dict[str, Any]:
    """IDs for a check's log line: alert_id and resident_id from the validated check,
    type, severity, and device from the stored item (_safe_item_fields)."""
    return {
        "alert_id": check.alert_id,
        **_safe_item_fields(item),
        "resident_id": check.resident_id,
        "kind": KIND_ESCALATION_CHECK,
    }


def handle_check(check: EscalationCheck) -> bool:
    """Handle one escalation check. Returns False if it must be reported as failed."""
    ids: dict[str, Any] = {
        "alert_id": check.alert_id,
        "resident_id": check.resident_id,
        "kind": KIND_ESCALATION_CHECK,
    }
    try:
        item = read_alert(check)
        ids = _item_log_ids(check, item)
        decision = escalation_decision(item, datetime.now(UTC))
        if decision is not None:
            outcome, reason = decision
            delay = None
        else:
            reason = None
            outcome, delay = escalate(check, item)
    except Exception as exc:  # noqa: BLE001 - any failure: retry only this record
        logger.error(
            LOG_MESSAGE, extra={**ids, "outcome": ERROR, "error": _error_code(exc)}
        )
        return False

    extra: dict[str, Any] = {**ids, "outcome": outcome}
    if reason is not None:
        extra["reason"] = reason
    if delay is not None:
        extra["escalation_delay_ms"] = delay
    logger.info(LOG_MESSAGE, extra=extra)
    return True


# ---- status updates (docs/features/alert-cancel.md) ----


def device_target_upper_bound(update: StatusUpdate) -> str:
    """Criterion 8: "ALERT#<changed_at>#~", so alerts at exactly changed_at are
    included ("~" sorts after every alert_id character) and later ones are not."""
    return f"ALERT#{update.changed_at}#~"


def cancel_expires_at(changed_dt: datetime) -> int:
    """Criterion 9: changed_at + 30 days, epoch seconds (the TTL attribute)."""
    return int((changed_dt + CANCELLED_RETENTION).timestamp())


def is_follow_up_retry(item: dict[str, Any], update: StatusUpdate) -> bool:
    """Alert-routing criterion 11: the item already has this update's status but no
    status_notified_at (a crash after the update, before the follow-up was recorded),
    set by the same kind of update: an acknowledge by the same sub, a caregiver cancel
    by the same sub, a resident cancel by the resident."""
    if item.get("status") != update.new_status or "status_notified_at" in item:
        return False
    if update.new_status == STATUS_ACKNOWLEDGED:
        return item.get("acknowledged_by") == update.by
    if item.get("cancelled_by_role") != update.by_role:
        return False
    return update.by_role == ROLE_RESIDENT or item.get("cancelled_by") == update.by


def is_changeable(item: dict[str, Any], update: StatusUpdate) -> bool:
    """Criterion 10: a cancel changes open and acknowledged alerts, an acknowledge
    only open ones."""
    return item.get("status") in CHANGEABLE_FROM[update.new_status]


def is_target(item: dict[str, Any], update: StatusUpdate) -> bool:
    """An item this update changes or owes a follow-up (a retry)."""
    if update.device_id is not None and item.get("device_id") != update.device_id:
        return False
    return is_changeable(item, update) or is_follow_up_retry(item, update)


def query_device_targets(update: StatusUpdate) -> list[dict[str, Any]]:
    """Pattern 24: the resident's alerts up to changed_at from this device that are
    open or acknowledged, or already have this status but no status_notified_at
    (retry). Strongly consistent, paginated. Only resident cancels have a device
    target."""
    client = dynamodb_client()
    items: list[dict[str, Any]] = []
    request: dict[str, Any] = {
        "TableName": os.environ["ALERTS_TABLE"],
        "KeyConditionExpression": "pk = :pk AND sk <= :high",
        "FilterExpression": (
            "device_id = :device AND (#status IN (:open, :acknowledged) OR "
            "(#status = :new AND attribute_not_exists(status_notified_at)))"
        ),
        "ExpressionAttributeNames": {"#status": "status"},
        "ExpressionAttributeValues": {
            ":pk": {"S": f"RESIDENT#{update.resident_id}"},
            ":high": {"S": device_target_upper_bound(update)},
            ":device": {"S": update.device_id or ""},
            ":open": {"S": STATUS_OPEN},
            ":acknowledged": {"S": STATUS_ACKNOWLEDGED},
            ":new": {"S": update.new_status},
        },
        "ProjectionExpression": ", ".join(TARGET_ATTRIBUTES),
        "ConsistentRead": True,
    }
    while True:
        response = client.query(**request)
        items.extend(_deserialize(item) for item in response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        request["ExclusiveStartKey"] = last_key


def read_alert_target(update: StatusUpdate) -> dict[str, Any] | None:
    """Pattern 25: the one alert a caregiver cancel or nurse acknowledge names,
    strongly consistent."""
    response = dynamodb_client().get_item(
        TableName=os.environ["ALERTS_TABLE"],
        Key={
            "pk": {"S": f"RESIDENT#{update.resident_id}"},
            "sk": {"S": f"ALERT#{update.event_ts}#{update.alert_id}"},
        },
        ConsistentRead=True,
    )
    return _deserialize(response["Item"]) if "Item" in response else None


def find_targets(update: StatusUpdate) -> list[dict[str, Any]]:
    """Criterion 8: the items to change (or to send a missing follow-up for)."""
    if update.device_id is not None:
        items = query_device_targets(update)
    else:
        item = read_alert_target(update)
        items = [item] if item is not None else []
    return [item for item in items if is_target(item, update)]


def apply_status(update: StatusUpdate, item: dict[str, Any]) -> dict[str, Any] | None:
    """Alert-routing criterion 11: UpdateItem conditional on the statuses it may change
    from. Returns the item after the update, or None if the condition failed (it
    changed meanwhile).

    A cancel keeps the acknowledge's fields (acknowledged_at/_by, ack_note) and REMOVEs
    the acknowledge's status_notified_at, so the cancel's follow-up is tracked on its
    own (else a crash before it would leave it unsent)."""
    names = {"#status": "status"}
    values: dict[str, Any] = {
        ":new": {"S": update.new_status},
        ":at": {"S": update.changed_at},
        ":open": {"S": STATUS_OPEN},
    }
    if update.new_status == STATUS_CANCELLED:
        sets = [
            "#status = :new",
            "cancelled_at = :at",
            "cancelled_by_role = :role",
            "expires_at = :expires",
        ]
        values[":role"] = {"S": update.by_role}
        values[":expires"] = {"N": str(cancel_expires_at(update.changed_dt))}
        if update.by is not None:
            sets.append("cancelled_by = :by")
            values[":by"] = {"S": update.by}
        expression = f"SET {', '.join(sets)} REMOVE open_pk, status_notified_at"
        condition = "#status IN (:open, :acknowledged)"
        values[":acknowledged"] = {"S": STATUS_ACKNOWLEDGED}
    else:
        sets = ["#status = :new", "acknowledged_at = :at", "acknowledged_by = :by"]
        values[":by"] = {"S": update.by or ""}
        if update.note is not None:
            sets.append("ack_note = :note")
            values[":note"] = {"S": update.note}
        expression = f"SET {', '.join(sets)}"
        condition = "#status = :open"
    try:
        response = dynamodb_client().update_item(
            TableName=os.environ["ALERTS_TABLE"],
            Key={"pk": {"S": item["pk"]}, "sk": {"S": item["sk"]}},
            UpdateExpression=expression,
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if not _is_condition_failure(exc):
            raise
        return None
    # ALL_NEW: an escalated_at set just before this update is seen by the follow-up.
    attributes = response.get("Attributes")
    if attributes:
        return _deserialize(attributes)
    return {**item, "status": update.new_status}


def follow_up_to_nurses(update: StatusUpdate, item: dict[str, Any]) -> bool:
    """Alert-routing criterion 13: a cancel goes to the nurses if they were emailed
    about the alert (escalated_at) or had acknowledged it (acknowledged_at, which only
    an acknowledge sets and a cancel keeps); an acknowledge never does."""
    if update.new_status != STATUS_CANCELLED:
        return False
    return "escalated_at" in item or "acknowledged_at" in item


def send_follow_up(update: StatusUpdate, item: dict[str, Any]) -> None:
    """Alert-routing criteria 13-15: email caregivers (resident_id filter; not for
    unassigned) and, for a cancel the nurses were involved in, the nurses; then SET
    status_notified_at once (also when nobody was emailed: an unassigned
    acknowledge)."""
    subject = email_text.follow_up_subject(
        item.get("alert_type"), update.resident_id, update.new_status
    )
    body = email_text.follow_up_body(
        item,
        update.resident_id,
        os.environ["PORTAL_URL"],
        new_status=update.new_status,
        changed_at=update.changed_dt,
        by_role=update.by_role,
        by_name=update.by_name,
        note=update.note,
    )
    if update.resident_id != UNASSIGNED:
        sns_client().publish(
            TopicArn=os.environ["ALERTS_TOPIC_ARN"],
            Subject=subject,
            Message=body,
            MessageAttributes={
                "resident_id": {
                    "DataType": "String",
                    "StringValue": update.resident_id,
                },
            },
        )
    if follow_up_to_nurses(update, item):
        sns_client().publish(
            TopicArn=os.environ["NURSES_TOPIC_ARN"], Subject=subject, Message=body
        )
    try:
        dynamodb_client().update_item(
            TableName=os.environ["ALERTS_TABLE"],
            Key={"pk": {"S": item["pk"]}, "sk": {"S": item["sk"]}},
            UpdateExpression="SET status_notified_at = :now",
            ConditionExpression="attribute_not_exists(status_notified_at)",
            ExpressionAttributeValues={":now": {"S": format_iso_ms(datetime.now(UTC))}},
        )
    except ClientError as exc:
        # Another delivery of the same update already set it: nothing more to do.
        if not _is_condition_failure(exc):
            raise


def apply_to_target(update: StatusUpdate, item: dict[str, Any]) -> str:
    """One target: update it and send the follow-up (updated), skip it if it changed
    meanwhile (skipped), or send the missing follow-up of a retry (updated)."""
    if is_changeable(item, update):
        updated = apply_status(update, item)
        if updated is None:
            return SKIPPED
        item = updated
    send_follow_up(update, item)
    return UPDATED


def _status_update_ids(update: StatusUpdate) -> dict[str, Any]:
    """IDs every status update log line has, plus has_note for an acknowledge
    (alert-routing criterion 12). Never by, by_name, or note."""
    target = (
        {"device_id": update.device_id}
        if update.device_id is not None
        else {"alert_id": update.alert_id}
    )
    ids: dict[str, Any] = {
        **target,
        "resident_id": update.resident_id,
        "kind": KIND_STATUS_UPDATE,
        "new_status": update.new_status,
        "by_role": update.by_role,
    }
    if update.new_status == STATUS_ACKNOWLEDGED:
        ids["has_note"] = update.note is not None
    return ids


def _status_item_log_ids(update: StatusUpdate, item: dict[str, Any]) -> dict[str, Any]:
    """Criterion 11: alert_id, alert_type, severity, device_id, resident_id, kind,
    new_status, by_role; item values only if they have their expected format."""
    alert_id = item.get("alert_id")
    return {
        **_status_update_ids(update),
        "alert_id": (
            alert_id
            if isinstance(alert_id, str) and UUID_RE.match(alert_id)
            else "invalid"
        ),
        **_safe_item_fields(item),
    }


def handle_status_update(update: StatusUpdate) -> bool:
    """Handle one status update. Returns False if it must be reported as failed.

    One log line per target alert, or one line with no_open_alerts / not_open when
    there is none. The first failure stops and fails the record; the retry skips the
    alerts already done (their status changed and status_notified_at is set).
    """
    base = _status_update_ids(update)
    try:
        targets = find_targets(update)
    except Exception as exc:  # noqa: BLE001 - any failure: retry only this record
        logger.error(
            LOG_MESSAGE, extra={**base, "outcome": ERROR, "error": _error_code(exc)}
        )
        return False
    if not targets:
        none = NO_OPEN_ALERTS if update.device_id is not None else NOT_OPEN
        logger.info(LOG_MESSAGE, extra={**base, "outcome": none})
        return True
    for item in targets:
        ids = _status_item_log_ids(update, item)
        try:
            outcome = apply_to_target(update, item)
        except Exception as exc:  # noqa: BLE001 - any failure: retry only this record
            logger.error(
                LOG_MESSAGE, extra={**ids, "outcome": ERROR, "error": _error_code(exc)}
            )
            return False
        logger.info(LOG_MESSAGE, extra={**ids, "outcome": outcome})
    return True


def handle_record(record: Any) -> bool:
    """Process one SQS record. Returns False if it must be reported as failed."""
    body = record.get("body") if isinstance(record, dict) else None
    result = parse_message(body)
    if isinstance(result, Rejection):
        logger.warning(
            LOG_MESSAGE,
            extra={**log_ids(body), "outcome": INVALID, "reason": result.reason},
        )
        return False
    if isinstance(result, EscalationCheck):
        return handle_check(result)
    if isinstance(result, StatusUpdate):
        return handle_status_update(result)

    ids = {
        "alert_id": result.alert_id,
        "alert_type": result.alert_type,
        "severity": result.severity,
        "device_id": result.device_id,
        "resident_id": result.resident_id,
    }
    try:
        outcome, latency, nurses = process(result)
    except Exception as exc:  # noqa: BLE001 - any failure: retry only this record
        # Only the error code is logged; exception text could quote the request.
        logger.error(
            LOG_MESSAGE, extra={**ids, "outcome": ERROR, "error": _error_code(exc)}
        )
        return False

    extra: dict[str, Any] = {**ids, "outcome": outcome, "nurses": nurses}
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
