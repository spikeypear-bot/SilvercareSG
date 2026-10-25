"""Build the stored entry for a validated message (docs/data-model.md, silvercare-latest).

Pure functions, no AWS calls.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from validation import ValidMessage

CLOCK_SKEW_LIMIT = timedelta(minutes=5)


def received_at_datetime(epoch_ms: int) -> datetime:
    """UTC datetime from the rule's timestamp() (epoch milliseconds), exact to the ms."""
    seconds, millis = divmod(epoch_ms, 1000)
    return datetime.fromtimestamp(seconds, UTC).replace(microsecond=millis * 1000)


def format_iso_ms(value: datetime) -> str:
    """ISO 8601 UTC with milliseconds and Z, e.g. 2026-10-18T03:12:00.410Z."""
    value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def is_clock_skewed(ts: datetime, received_at: datetime) -> bool:
    """True when the device time and the receive time are more than 5 minutes apart."""
    return abs(ts - received_at) > CLOCK_SKEW_LIMIT


def to_dynamodb_value(value: Any) -> Any:
    """Make a validated field value safe for DynamoDB without float rounding.

    Floats become Decimal via their shortest repr (33.4 -> Decimal("33.4")). Booleans,
    ints, strings, and None are kept as they are.
    """
    if type(value) is float:
        return Decimal(str(value))
    return value


def build_entry(msg: ValidMessage) -> dict[str, Any]:
    """The entry stored in `entries`: envelope, receive time, skew flag, allow-listed fields."""
    received_at = received_at_datetime(msg.received_at_ms)
    entry: dict[str, Any] = {
        "message_id": msg.message_id,
        "ts": msg.ts,
        "received_at": format_iso_ms(received_at),
        "clock_skew": is_clock_skewed(msg.ts_dt, received_at),
    }
    for name, value in msg.fields.items():
        entry[name] = to_dynamodb_value(value)
    return entry
