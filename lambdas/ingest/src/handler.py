"""Ingest Lambdas, live path (docs/features/ingest-live.md).

Three functions are built from this one zip, each with its own entry point:

- `device_health_handler` (silvercare-device-health): `status`
- `telemetry_handler` (silvercare-telemetry): `vitals`, `motion`, `bed`
- `critical_alert_handler` (silvercare-critical-alert): `fall`, `vitals_alert`,
  `alert_cancel`

Each is invoked asynchronously by its IoT topic rule with the device payload plus
`device_id`, `message`, and `received_at` added by the rule. It validates the message
(validation.py), builds the stored entry (entries.py), and adds it to the device's
latest 5 in silvercare-latest (latest_store.py).

`critical_alert_handler` raises a critical alert for every device-raised alert (falls,
docs/features/alerts-core.md criteria 4-8; device `vitals_alert`s,
docs/features/vitals-alerts.md criterion 16), in this order: look up the device's
resident in silvercare-registry (registry.py, cached 5 minutes), send the alert message
to the alerts queue (alerts.py), then store the message. Sending first keeps the alert
off the slower write path. If the send or the write fails, the function raises and
Lambda's async retry runs it again; the repeated alert message has the same alert_id,
so alert-processor stores it once. After the retries, the event goes to
silvercare-critical-alert-dlq (Terraform).

A wearable `alert_cancel` (the "I'm OK" button, docs/features/alert-cancel.md,
criteria 4-5) takes the same path in the same order: look up the resident, send a
`status_update` (alerts.build_cancel_update) to the alerts queue, then store the
message as MSG#alert_cancel. alert-processor cancels the device's open alerts. A failed
send raises, like a failed alert send. Its log line has `status_update` = `queued`
instead of alert_id and alert_outcome.

`telemetry_handler` stores the message first, then, for a wearable `vitals` that is the
device's newest entry, runs the cloud check (vitals-alerts criteria 7-11): look up the
resident (registry.py) and their thresholds (thresholds.py, both cached), compare the
newest two entries (vitals_check.py), and send a warning per finding. A failed send
raises, so Lambda's async retry runs it again. `device_health_handler` reads no
registry and raises no alerts.

Invalid messages are logged with a reason code and dropped without raising, so they
aren't retried (and raise no alert). AWS errors are raised, so Lambda's async retry
tries again. Logs hold only device_id, message, message_id, alert IDs, and the outcome
or reason, never field values, limits, or thresholds. The one line that names a
resident is the invalid-thresholds warning (criterion 5: resident_id and field names).
"""

from __future__ import annotations

import os
from functools import cache
from typing import Any

import alerts
import boto3
import latest_store
import registry
import thresholds
import vitals_check
from aws_lambda_powertools import Logger
from botocore.config import Config
from entries import build_entry
from validation import UUID_V4_RE, Rejection, ValidMessage, validate_event

# The service name comes from POWERTOOLS_SERVICE_NAME, set per function by Terraform.
logger = Logger()

DEVICE_HEALTH_MESSAGES = frozenset({"status"})
TELEMETRY_MESSAGES = frozenset({"vitals", "motion", "bed"})
CRITICAL_ALERT_MESSAGES = frozenset({"fall", "vitals_alert", "alert_cancel"})
CANCEL_MESSAGE = "alert_cancel"

# What _process does after validation, per function.
MODE_STORE_ONLY = "store_only"  # device-health
MODE_TELEMETRY = "telemetry"  # store, then the cloud vitals check
MODE_CRITICAL_ALERT = "critical_alert"  # queue the critical alert, then store

LOG_ID_MAX_LEN = 64


@cache
def dynamodb_client() -> Any:
    """One DynamoDB client per container, created on first use.

    Adaptive retry mode (criterion 11). DYNAMODB_ENDPOINT points at DynamoDB Local in
    integration tests; it is unset in AWS.
    """
    return boto3.client(
        "dynamodb",
        config=Config(retries={"mode": "adaptive"}),
        endpoint_url=os.environ.get("DYNAMODB_ENDPOINT"),
    )


@cache
def sqs_client() -> Any:
    """One SQS client per container, created on first use (telemetry and
    critical-alert)."""
    return boto3.client("sqs")


def _queue_critical_alert(msg: ValidMessage) -> str:
    """Look up the resident and send the critical alert for a device-raised message
    (fall, vitals_alert). Returns the alert_id."""
    resident_id = registry.resident_for_device(
        dynamodb_client(), os.environ["REGISTRY_TABLE"], msg.device_id
    )
    alert = alerts.build_critical_alert(msg, resident_id)
    alerts.send_alert(sqs_client(), os.environ["ALERTS_QUEUE_URL"], alert)
    return alert["alert_id"]


def _queue_cancel(msg: ValidMessage) -> None:
    """Look up the resident and send the status_update for a wearable alert_cancel."""
    resident_id = registry.resident_for_device(
        dynamodb_client(), os.environ["REGISTRY_TABLE"], msg.device_id
    )
    update = alerts.build_cancel_update(msg, resident_id)
    alerts.send_status_update(sqs_client(), os.environ["ALERTS_QUEUE_URL"], update)


def _log_invalid_thresholds(resident_id: str, fields: list[str]) -> None:
    """A THRESHOLDS item with invalid limits: the resident and field names, never the
    values (vitals-alerts criterion 5)."""
    logger.warning(
        "thresholds",
        extra={"resident_id": resident_id, "reason": "invalid_limit", "fields": fields},
    )


def _queue_vitals_warnings(
    msg: ValidMessage, outcome: str, entries: list[dict[str, Any]] | None
) -> list[str]:
    """The cloud check (vitals-alerts criteria 7-10). Returns the queued alert_ids.

    Runs only for a stored or duplicate wearable `vitals` that is the device's newest
    entry; a duplicate re-runs it, and the deterministic alert_id keeps the alert
    single. The registry and thresholds are read only when the newest two entries are
    close enough to pair. A failed send raises, so Lambda's async retry runs it again.
    """
    if msg.message != "vitals" or outcome not in (
        latest_store.STORED,
        latest_store.DUPLICATE,
    ):
        return []
    if not entries or entries[0].get("message_id") != msg.message_id:
        return []
    pair = vitals_check.recent_pair(entries)
    if pair is None:
        return []

    client = dynamodb_client()
    registry_table = os.environ["REGISTRY_TABLE"]
    resident_id = registry.resident_for_device(client, registry_table, msg.device_id)
    limits = thresholds.limits_for_resident(
        client, registry_table, resident_id, on_invalid=_log_invalid_thresholds
    )
    alert_ids: list[str] = []
    for finding in vitals_check.check_pair(pair[0], pair[1], limits):
        alert = alerts.build_vitals_warning(
            msg.device_id, resident_id, pair[0], finding
        )
        alerts.send_alert(sqs_client(), os.environ["ALERTS_QUEUE_URL"], alert)
        alert_ids.append(alert["alert_id"])
    return alert_ids


def _log_id(event: Any, name: str) -> str | None:
    """An ID from the event for logging: strings only, truncated."""
    if not isinstance(event, dict):
        return None
    value = event.get(name)
    return value[:LOG_ID_MAX_LEN] if isinstance(value, str) else None


def _process(
    event: Any, handled_messages: frozenset[str], mode: str = MODE_STORE_ONLY
) -> dict[str, str]:
    # Every log line is {"message": <message type>, "device_id", "message_id", plus
    # "outcome" or "reason"}, plus "alert_id" and "alert_outcome" once critical-alert
    # has queued an alert, "status_update" once it has queued a cancel, or
    # "alert_ids" (a list, maybe empty) for telemetry. The
    # message type goes in the log record's own "message" field, because Python
    # logging doesn't allow "message" as an extra key.
    message = _log_id(event, "message") or "-"
    # message_id comes from the device, so it is logged only if it is a UUID v4: any
    # other string could carry free text (e.g. a health value) into the logs.
    message_id = _log_id(event, "message_id")
    if message_id is not None and not UUID_V4_RE.match(message_id):
        message_id = "invalid"
    ids = {
        "device_id": _log_id(event, "device_id"),
        "message_id": message_id,
    }

    result = validate_event(event, handled_messages)
    if isinstance(result, Rejection):
        logger.warning(message, extra={**ids, "reason": result.reason})
        return {"outcome": "rejected", "reason": result.reason}

    # critical-alert: alert_id and alert_outcome once the alert is queued, or
    # status_update once a cancel is queued.
    # telemetry: alert_ids, the warnings queued (empty when none).
    alert_log: dict[str, Any] = {}
    if mode == MODE_TELEMETRY:
        alert_log["alert_ids"] = []
    try:
        if mode == MODE_CRITICAL_ALERT and result.message == CANCEL_MESSAGE:
            _queue_cancel(result)
            alert_log = {"status_update": "queued"}
        elif mode == MODE_CRITICAL_ALERT:
            alert_log = {
                "alert_id": _queue_critical_alert(result),
                "alert_outcome": "queued",
            }
        outcome, entries = latest_store.add_message_with_entries(
            dynamodb_client(),
            os.environ["LATEST_TABLE"],
            result.device_id,
            result.device_type,
            result.message,
            build_entry(result),
        )
        if mode == MODE_TELEMETRY:
            alert_log["alert_ids"] = _queue_vitals_warnings(result, outcome, entries)
    except Exception as exc:
        # Raised on purpose (criterion 20): Lambda's async retry tries again. Log only
        # the error type, so nothing from the item can reach the logs from here.
        logger.error(
            message,
            extra={
                **ids,
                **alert_log,
                "outcome": "error",
                "error": type(exc).__name__,
            },
        )
        raise
    logger.info(message, extra={**ids, **alert_log, "outcome": outcome})
    return {"outcome": outcome}


def device_health_handler(event: Any, context: Any) -> dict[str, str]:
    """silvercare-device-health: stores status messages. Reads no registry and sends
    nothing."""
    return _process(event, DEVICE_HEALTH_MESSAGES)


def telemetry_handler(event: Any, context: Any) -> dict[str, str]:
    """silvercare-telemetry: stores vitals, motion, and bed messages, then runs the
    cloud vitals check on wearable vitals."""
    return _process(event, TELEMETRY_MESSAGES, mode=MODE_TELEMETRY)


def critical_alert_handler(event: Any, context: Any) -> dict[str, str]:
    """silvercare-critical-alert: queues a critical alert for a fall or a device
    vitals_alert, or a status_update for an alert_cancel, then stores the message."""
    return _process(event, CRITICAL_ALERT_MESSAGES, mode=MODE_CRITICAL_ALERT)
