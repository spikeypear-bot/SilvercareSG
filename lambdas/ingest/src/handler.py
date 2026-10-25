"""Ingest Lambdas, live path (docs/features/ingest-live.md).

Three functions are built from this one zip, each with its own entry point:

- `device_health_handler` (silvercare-device-health): `status`
- `telemetry_handler` (silvercare-telemetry): `vitals`, `motion`, `bed`
- `fall_alert_handler` (silvercare-fall-alert): `fall`

Each is invoked asynchronously by its IoT topic rule with the device payload plus
`device_id`, `message`, and `received_at` added by the rule. It validates the message
(validation.py), builds the stored entry (entries.py), and adds it to the device's
latest 5 in silvercare-latest (latest_store.py). No registry reads, threshold checks,
or alerts.

Invalid messages are logged with a reason code and dropped without raising, so they
aren't retried. AWS errors are raised, so Lambda's async retry tries again. Logs hold
only device_id, message, message_id, and the outcome or reason, never field values.
"""

from __future__ import annotations

import os
from functools import cache
from typing import Any

import boto3
import latest_store
from aws_lambda_powertools import Logger
from botocore.config import Config
from entries import build_entry
from validation import UUID_V4_RE, Rejection, validate_event

# The service name comes from POWERTOOLS_SERVICE_NAME, set per function by Terraform.
logger = Logger()

DEVICE_HEALTH_MESSAGES = frozenset({"status"})
TELEMETRY_MESSAGES = frozenset({"vitals", "motion", "bed"})
FALL_ALERT_MESSAGES = frozenset({"fall"})

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


def _log_id(event: Any, name: str) -> str | None:
    """An ID from the event for logging: strings only, truncated."""
    if not isinstance(event, dict):
        return None
    value = event.get(name)
    return value[:LOG_ID_MAX_LEN] if isinstance(value, str) else None


def _process(event: Any, handled_messages: frozenset[str]) -> dict[str, str]:
    # Every log line is {"message": <message type>, "device_id", "message_id", plus
    # "outcome" or "reason"}. The message type goes in the log record's own "message"
    # field, because Python logging doesn't allow "message" as an extra key.
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

    try:
        outcome = latest_store.add_message(
            dynamodb_client(),
            os.environ["LATEST_TABLE"],
            result.device_id,
            result.device_type,
            result.message,
            build_entry(result),
        )
    except Exception as exc:
        # Raised on purpose (criterion 20): Lambda's async retry tries again. Log only
        # the error type, so nothing from the item can reach the logs from here.
        logger.error(
            message, extra={**ids, "outcome": "error", "error": type(exc).__name__}
        )
        raise
    logger.info(message, extra={**ids, "outcome": outcome})
    return {"outcome": outcome}


def device_health_handler(event: Any, context: Any) -> dict[str, str]:
    """silvercare-device-health: stores status messages."""
    return _process(event, DEVICE_HEALTH_MESSAGES)


def telemetry_handler(event: Any, context: Any) -> dict[str, str]:
    """silvercare-telemetry: stores vitals, motion, and bed messages."""
    return _process(event, TELEMETRY_MESSAGES)


def fall_alert_handler(event: Any, context: Any) -> dict[str, str]:
    """silvercare-fall-alert: stores fall messages. Nobody is notified yet."""
    return _process(event, FALL_ALERT_MESSAGES)
