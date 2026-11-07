"""watchdog Lambda (docs/features/watchdog.md, criteria 1-8a, 10, 15-16).

Invoked every 5 minutes by an EventBridge schedule rule (asynchronously; the event's
content is not used). Each run starts fresh from DynamoDB, so nothing is carried over:

1. Read, all eventually consistent and paginated:
   - every device (registry `by-type`, Query entity_type = DEVICE, pattern 4), keeping
     only assigned ones (resident_id present);
   - every device's newest status (latest `by-message`, Query watch_type = status,
     pattern 5);
   - every open alert (alerts `open`, Query open_pk = OPEN, pattern 9);
   - for wearables whose newest status has worn = false, a BatchGetItem of their
     MSG#status items for worn_false_since (written by device-health);
   - every bedroom unit's newest bed and motion entry (latest `by-message`, Query
     watch_type = bed and = motion, pattern 5; docs/features/possible-fall.md,
     criterion 1);
   - the test-mode items (registry `by-type`, Query entity_type = TEST_MODE,
     possible-fall criterion 3a; usually none).
2. Decide (checks.plan): device problems and possible falls to raise, open ones to
   resolve (a possible fall when the resident is back in bed, with reason
   back_in_bed).
3. If 5 or more devices are newly offline, Publish one summary email to silvercare-ops-devices
   first (a failure fails the run before any alert is sent, so the retry sends it
   again rather than never).
4. Send each alert message (alert-processor's A1 format) and each system resolve
   status_update to the alerts queue.

Any AWS error fails the run (raised): Lambda retries the async invocation (at most
twice, within the event age set in Terraform, before the next scheduled run), and the
next run starts fresh. Deterministic alert_ids keep a retried run from making a second
alert; a second resolve of the same alert ends not_open in alert-processor.

Logs: one line per raised or resolved alert (alert_id, alert_type, device_id,
resident_id) and one summary line per run (devices checked, raised and resolved per
type including possible_fall, grouped, test_mode_units). Never battery values,
timestamps (bed or motion times), worn state, or details.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from functools import cache
from typing import Any

import boto3
import checks
from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config

# The service name comes from POWERTOOLS_SERVICE_NAME, set by Terraform.
logger = Logger()

LOG_MESSAGE = "watchdog"
REGISTRY_INDEX = "by-type"
LATEST_INDEX = "by-message"
ALERTS_INDEX = "open"
TEST_MODE = "TEST_MODE"
BATCH_GET_MAX_KEYS = 100
# BatchGetItem may return UnprocessedKeys (e.g. throttling): retry them a few times
# with a short back-off, then fail the run.
BATCH_GET_ATTEMPTS = 5
BATCH_GET_BACKOFF_S = 0.2

RAISED = "raised"
RESOLVED = "resolved"
RUN_FAILED = "watchdog run failed"

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
def sqs_client() -> Any:
    return boto3.client("sqs")


@cache
def sns_client() -> Any:
    return boto3.client("sns")


def _deserialize(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _deserializer.deserialize(v) for k, v in item.items()}


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if isinstance(code, str):
            return code
    return type(exc).__name__


# ---- reads ----


def query_all(request: dict[str, Any]) -> list[dict[str, Any]]:
    """Every item of an eventually consistent Query, following LastEvaluatedKey."""
    client = dynamodb_client()
    request = dict(request)
    items: list[dict[str, Any]] = []
    while True:
        response = client.query(**request)
        items.extend(_deserialize(item) for item in response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        request["ExclusiveStartKey"] = last_key


def read_devices() -> list[checks.Device]:
    """Pattern 4: every assigned device (spares and malformed items are skipped)."""
    items = query_all(
        {
            "TableName": os.environ["REGISTRY_TABLE"],
            "IndexName": REGISTRY_INDEX,
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
    devices = (checks.device_from_item(item) for item in items)
    return [device for device in devices if device is not None]


def read_statuses() -> dict[str, checks.Status]:
    """Pattern 5: every device's newest status, by device_id."""
    items = query_all(
        {
            "TableName": os.environ["LATEST_TABLE"],
            "IndexName": LATEST_INDEX,
            "KeyConditionExpression": "#watch = :status",
            "ExpressionAttributeNames": {
                "#watch": "watch_type",
                "#pk": "pk",
                "#latest": "latest",
                "#latest_ts": "latest_ts",
            },
            "ExpressionAttributeValues": {":status": {"S": "status"}},
            "ProjectionExpression": "#pk, #latest, #latest_ts",
        }
    )
    statuses: dict[str, checks.Status] = {}
    for item in items:
        parsed = checks.status_from_item(item)
        if parsed is not None:
            statuses[parsed[0]] = parsed[1]
    return statuses


def read_bedroom_entries(
    watch_type: str, field_name: str
) -> dict[str, checks.BedroomEntry]:
    """Pattern 5 (possible-fall criterion 1): every bedroom unit's newest `bed` or
    `motion` entry, by device_id (malformed items are skipped)."""
    items = query_all(
        {
            "TableName": os.environ["LATEST_TABLE"],
            "IndexName": LATEST_INDEX,
            "KeyConditionExpression": "#watch = :type",
            "ExpressionAttributeNames": {
                "#watch": "watch_type",
                "#pk": "pk",
                "#latest": "latest",
            },
            "ExpressionAttributeValues": {":type": {"S": watch_type}},
            "ProjectionExpression": "#pk, #latest",
        }
    )
    entries: dict[str, checks.BedroomEntry] = {}
    for item in items:
        parsed = checks.bedroom_entry_from_item(item, field_name)
        if parsed is not None:
            entries[parsed[0]] = parsed[1]
    return entries


def read_test_mode(now: datetime) -> frozenset[str]:
    """Possible-fall criterion 3a: the bedroom units in test mode (registry by-type,
    entity_type = TEST_MODE; invalid, expired, and future items are ignored)."""
    items = query_all(
        {
            "TableName": os.environ["REGISTRY_TABLE"],
            "IndexName": REGISTRY_INDEX,
            "KeyConditionExpression": "#type = :test_mode",
            "ExpressionAttributeNames": {
                "#type": "entity_type",
                "#pk": "pk",
                "#sk": "sk",
            },
            "ExpressionAttributeValues": {":test_mode": {"S": TEST_MODE}},
            "ProjectionExpression": "#pk, #sk",
        }
    )
    return checks.units_in_test_mode(items, now)


def read_open_alerts() -> list[checks.OpenAlert]:
    """Pattern 9: every open or acknowledged device-problem and possible_fall alert
    (other types are read but dropped). The role may read only these attributes, with
    Select = SPECIFIC_ATTRIBUTES (infra/watchdog.tf): the index also projects the
    nurse's ack_note, which the watchdog must never read."""
    items = query_all(
        {
            "TableName": os.environ["ALERTS_TABLE"],
            "IndexName": ALERTS_INDEX,
            "Select": "SPECIFIC_ATTRIBUTES",
            "KeyConditionExpression": "#open = :open",
            "ExpressionAttributeNames": {
                "#open": "open_pk",
                "#pk": "pk",
                "#sk": "sk",
                "#type": "alert_type",
                "#device": "device_id",
            },
            "ExpressionAttributeValues": {":open": {"S": "OPEN"}},
            "ProjectionExpression": "#pk, #sk, #type, #device",
        }
    )
    alerts = (checks.open_alert_from_item(item) for item in items)
    return [alert for alert in alerts if alert is not None]


def read_worn_since(device_ids: list[str]) -> dict[str, str]:
    """Criterion 2: worn_false_since of these wearables' MSG#status items, by
    device_id (absent when the item has none). BatchGetItem, eventually consistent,
    at most 100 keys per call; UnprocessedKeys are retried, then the run fails."""
    table = os.environ["LATEST_TABLE"]
    client = dynamodb_client()
    result: dict[str, str] = {}
    for start in range(0, len(device_ids), BATCH_GET_MAX_KEYS):
        chunk = device_ids[start : start + BATCH_GET_MAX_KEYS]
        pending: dict[str, Any] | None = {
            table: {
                "Keys": [
                    {"pk": {"S": f"DEVICE#{device_id}"}, "sk": {"S": "MSG#status"}}
                    for device_id in chunk
                ],
                "ProjectionExpression": "#pk, #since",
                "ExpressionAttributeNames": {"#pk": "pk", "#since": "worn_false_since"},
            }
        }
        for attempt in range(BATCH_GET_ATTEMPTS):
            if attempt:
                time.sleep(BATCH_GET_BACKOFF_S * 2 ** (attempt - 1))
            response = client.batch_get_item(RequestItems=pending)
            for raw in response.get("Responses", {}).get(table, []):
                item = _deserialize(raw)
                since = item.get("worn_false_since")
                pk = item.get("pk")
                if isinstance(since, str) and isinstance(pk, str):
                    result[pk.removeprefix("DEVICE#")] = since
            pending = response.get("UnprocessedKeys") or None
            if not pending:
                break
        if pending:
            raise RuntimeError("BatchGetItem left unprocessed keys")
    return result


def off_wrist_wearables(
    devices: list[checks.Device], statuses: dict[str, checks.Status]
) -> list[str]:
    """Wearables whose newest status has worn = false: only these need
    worn_false_since."""
    return sorted(
        device.device_id
        for device in devices
        if device.device_type == checks.WEARABLE
        and device.device_id in statuses
        and statuses[device.device_id].latest.get("worn") is False
    )


# ---- sends ----


def publish_summary(problems: list[checks.Problem]) -> None:
    """Criterion 8a: one ops email for 5 or more newly offline devices."""
    count = sum(1 for p in problems if p.alert_type == checks.DEVICE_OFFLINE)
    sns_client().publish(
        TopicArn=os.environ["OPS_TOPIC_ARN"],
        Subject=checks.summary_subject(count),
        Message=checks.summary_body(problems, os.environ["PORTAL_URL"]),
    )


def send_alert(message: dict[str, Any]) -> None:
    """Criterion 8: the alert message to the alerts queue (same attribute as the other
    detectors)."""
    sqs_client().send_message(
        QueueUrl=os.environ["ALERTS_QUEUE_URL"],
        MessageBody=checks.encode(message),
        MessageAttributes={
            "alert_type": {"DataType": "String", "StringValue": message["alert_type"]},
        },
    )


def send_status_update(message: dict[str, Any]) -> None:
    """Criterion 10: the system resolve to the alerts queue, without delay."""
    sqs_client().send_message(
        QueueUrl=os.environ["ALERTS_QUEUE_URL"], MessageBody=checks.encode(message)
    )


def _counts(alert_types: list[str]) -> dict[str, int]:
    return {t: alert_types.count(t) for t in checks.WATCHDOG_TYPES}


def run(now: datetime) -> dict[str, Any]:
    """One watchdog run. Returns the summary that is logged."""
    devices = read_devices()
    statuses = read_statuses()
    open_alerts = read_open_alerts()
    worn_since = read_worn_since(off_wrist_wearables(devices, statuses))
    beds = read_bedroom_entries("bed", "occupied")
    motions = read_bedroom_entries("motion", "motion")
    test_mode = read_test_mode(now)

    decided = checks.plan(
        devices,
        statuses,
        worn_since,
        open_alerts,
        now,
        beds=beds,
        motions=motions,
        test_mode=test_mode,
    )

    if decided.grouped:
        publish_summary(decided.raise_alerts)

    for problem in decided.raise_alerts:
        message = checks.alert_message(problem, now)
        send_alert(message)
        logger.info(
            LOG_MESSAGE,
            extra={
                "action": RAISED,
                "alert_id": message["alert_id"],
                "alert_type": message["alert_type"],
                "device_id": message["device_id"],
                "resident_id": message["resident_id"],
            },
        )

    for alert in decided.resolve:
        send_status_update(checks.resolve_message(alert, now))
        logger.info(
            LOG_MESSAGE,
            extra={
                "action": RESOLVED,
                "alert_id": alert.alert_id,
                "alert_type": alert.alert_type,
                "device_id": alert.device_id,
                "resident_id": alert.resident_id,
            },
        )

    return {
        "devices_checked": decided.checked,
        "raised": _counts([p.alert_type for p in decided.raise_alerts]),
        "resolved": _counts([a.alert_type for a in decided.resolve]),
        "grouped": decided.grouped,
        "test_mode_units": decided.test_mode_units,
    }


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    try:
        summary = run(now)
    except Exception as exc:  # noqa: BLE001 - any failure fails the run (re-raised)
        # Raised on purpose (criterion 15): Lambda's async retry, then the next run.
        # Only the error code is logged here. The runtime logs the raised exception's
        # message and stack, so a fixed message is raised instead of the original
        # (whose text could quote a request), with the cause chain suppressed.
        logger.error(LOG_MESSAGE, extra={"outcome": "error", "error": _error_code(exc)})
        raise RuntimeError(RUN_FAILED) from None
    logger.info(LOG_MESSAGE, extra={"outcome": "done", **summary})
    return summary
