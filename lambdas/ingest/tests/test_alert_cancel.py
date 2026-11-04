"""critical-alert handles the wearable "I'm OK" alert_cancel (docs/features/alert-cancel.md,
criteria 4, 5, 6 and 19).

Validation is pure; the path uses moto for DynamoDB (latest + registry) and SQS.
"""

from __future__ import annotations

import re

import alerts
import handler
import pytest
from botocore.exceptions import ClientError
from conftest import REGISTRY_TABLE, TABLE_NAME, assign_device
from events import BEDROOM, DELETE, WEARABLE, iso_to_epoch_ms, rule_event
from validation import Rejection, ValidMessage, validate_event

UUID_ANY_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
POWERTOOLS_KEYS = {"level", "location", "message", "timestamp", "service"}
CRITICAL = handler.CRITICAL_ALERT_MESSAGES


def cancel_item(client, device_id=WEARABLE):
    return client.get_item(
        TableName=TABLE_NAME,
        Key={"pk": {"S": f"DEVICE#{device_id}"}, "sk": {"S": "MSG#alert_cancel"}},
        ConsistentRead=True,
    ).get("Item")


class Recorder:
    """Wraps a client and appends (service, operation, kwargs) to a shared list."""

    def __init__(self, client, service, calls, fail_on=None):
        self._client = client
        self._service = service
        self._calls = calls
        self._fail_on = fail_on

    def __getattr__(self, name):
        method = getattr(self._client, name)

        def wrapper(**kwargs):
            self._calls.append((self._service, name, kwargs))
            if name == self._fail_on:
                raise ClientError(
                    {"Error": {"Code": "ThrottlingException", "Message": "x"}}, name
                )
            return method(**kwargs)

        return wrapper


# ======== validation (criterion 4) ========


def test_wearable_alert_cancel_is_valid_with_no_stored_fields():
    result = validate_event(rule_event("alert_cancel"), CRITICAL)

    assert isinstance(result, ValidMessage)
    assert result.message == "alert_cancel"
    assert result.fields == {}


def test_bedroom_alert_cancel_is_rejected_as_invalid_message():
    result = validate_event(rule_event("alert_cancel", device_id=BEDROOM), CRITICAL)

    assert result == Rejection("invalid:message")


@pytest.mark.parametrize(
    "extra",
    [
        {"hr_bpm": 72},
        {"confidence": 0.91},
        {"accel_g": {"x": 0.0, "y": 0.0, "z": 1.0}},
        {"note": "synthetic"},
        {"by": None},
    ],
)
def test_alert_cancel_with_any_extra_field_is_rejected_as_unknown_field(extra):
    result = validate_event(rule_event("alert_cancel", **extra), CRITICAL)

    assert result == Rejection("unknown_field")


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("schema_version", "missing:schema_version"),
        ("message_id", "missing:message_id"),
        ("ts", "missing:ts"),
        ("received_at", "invalid:received_at"),
    ],
)
def test_alert_cancel_missing_envelope_field_is_rejected(field, reason):
    result = validate_event(rule_event("alert_cancel", **{field: DELETE}), CRITICAL)

    assert result == Rejection(reason)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"schema_version": 2}, "invalid:schema_version"),
        ({"schema_version": "1"}, "invalid:schema_version"),
        ({"message_id": "not-a-uuid"}, "invalid:message_id"),
        ({"ts": "2026-10-18 03:12:00"}, "invalid:ts"),
    ],
)
def test_alert_cancel_with_invalid_envelope_is_rejected(overrides, reason):
    result = validate_event(rule_event("alert_cancel", **overrides), CRITICAL)

    assert result == Rejection(reason)


@pytest.mark.parametrize(
    "handled", [handler.TELEMETRY_MESSAGES, handler.DEVICE_HEALTH_MESSAGES]
)
def test_alert_cancel_is_wrong_function_outside_critical_alert(handled):
    assert validate_event(rule_event("alert_cancel"), handled) == Rejection(
        "wrong_function"
    )


# ======== the status_update (criteria 4, 6) ========


def test_assigned_cancel_queues_status_update_for_its_resident(
    latest_table, alerts_queue
):
    assign_device(latest_table, WEARABLE, "res-0001")
    event = rule_event("alert_cancel")

    assert handler.critical_alert_handler(event, None) == {"outcome": "stored"}

    (body,) = alerts_queue.bodies()
    assert body == {
        "kind": "status_update",
        "schema_version": 1,
        "resident_id": "res-0001",
        "new_status": "cancelled",
        "changed_at": "2026-10-18T03:12:00.410Z",
        "by_role": "resident",
        "by": None,
        "by_name": None,
        "target": {"device_id": WEARABLE},
    }


def test_unassigned_cancel_queues_status_update_as_unassigned(
    latest_table, alerts_queue
):
    handler.critical_alert_handler(rule_event("alert_cancel"), None)

    (body,) = alerts_queue.bodies()
    assert body["resident_id"] == "unassigned"
    assert body["target"] == {"device_id": WEARABLE}


def test_changed_at_is_the_rule_received_at_not_the_device_ts(
    latest_table, alerts_queue
):
    # The device clock is three hours behind: changed_at must follow received_at.
    received_at = iso_to_epoch_ms("2026-10-18T06:12:05.123Z")
    event = rule_event("alert_cancel", received_at=received_at)

    handler.critical_alert_handler(event, None)

    (body,) = alerts_queue.bodies()
    assert body["changed_at"] == "2026-10-18T06:12:05.123Z"
    assert body["changed_at"] != event["ts"]


def test_changed_at_keeps_milliseconds_when_received_on_a_whole_second(
    latest_table, alerts_queue
):
    received_at = iso_to_epoch_ms("2026-10-18T03:12:01.000Z")

    handler.critical_alert_handler(
        rule_event("alert_cancel", received_at=received_at), None
    )

    (body,) = alerts_queue.bodies()
    assert body["changed_at"] == "2026-10-18T03:12:01.000Z"


def test_status_update_is_sent_without_delay_or_attributes(latest_table, monkeypatch):
    calls: list = []
    sqs = handler.sqs_client()
    monkeypatch.setattr(handler, "sqs_client", lambda: Recorder(sqs, "sqs", calls))

    handler.critical_alert_handler(rule_event("alert_cancel"), None)

    ((_, op, kwargs),) = calls
    assert op == "send_message"
    assert set(kwargs) == {"QueueUrl", "MessageBody"}


def test_build_cancel_update_is_pure_and_uses_received_at():
    event = rule_event(
        "alert_cancel", received_at=iso_to_epoch_ms("2026-11-03T02:00:05.000Z")
    )
    msg = validate_event(event, CRITICAL)

    update = alerts.build_cancel_update(msg, "res-0001")

    assert update["changed_at"] == "2026-11-03T02:00:05.000Z"
    assert update["target"] == {"device_id": WEARABLE}


# ======== order, storage, and failures (criterion 4) ========


def test_cancel_looks_up_then_sends_then_stores(latest_table, monkeypatch):
    calls: list = []
    sqs = handler.sqs_client()
    monkeypatch.setattr(
        handler, "dynamodb_client", lambda: Recorder(latest_table, "ddb", calls)
    )
    monkeypatch.setattr(handler, "sqs_client", lambda: Recorder(sqs, "sqs", calls))

    handler.critical_alert_handler(rule_event("alert_cancel"), None)

    assert [(s, op, kw.get("TableName")) for s, op, kw in calls] == [
        ("ddb", "get_item", REGISTRY_TABLE),
        ("sqs", "send_message", None),
        ("ddb", "get_item", TABLE_NAME),
        ("ddb", "put_item", TABLE_NAME),
    ]


def test_cancel_is_stored_as_msg_alert_cancel_without_watch_type(latest_table):
    event = rule_event("alert_cancel")

    handler.critical_alert_handler(event, None)

    item = cancel_item(latest_table)
    assert item is not None
    assert "watch_type" not in item
    assert item["device_type"] == {"S": "wearable"}
    latest = item["latest"]["M"]
    assert set(latest) == {"message_id", "ts", "received_at", "clock_skew"}
    assert latest["message_id"] == {"S": event["message_id"]}


def test_duplicate_cancel_is_stored_once_and_sends_the_same_update(
    latest_table, alerts_queue
):
    event = rule_event("alert_cancel")
    handler.critical_alert_handler(event, None)

    assert handler.critical_alert_handler(dict(event), None) == {"outcome": "duplicate"}

    first, second = alerts_queue.bodies()
    assert first == second
    assert len(cancel_item(latest_table)["entries"]["L"]) == 1


def test_cancel_send_failure_raises_and_stores_nothing(latest_table, monkeypatch, logs):
    sqs = handler.sqs_client()
    monkeypatch.setattr(
        handler,
        "sqs_client",
        lambda: Recorder(sqs, "sqs", [], fail_on="send_message"),
    )

    with pytest.raises(ClientError):
        handler.critical_alert_handler(rule_event("alert_cancel"), None)

    assert cancel_item(latest_table) is None
    (record,) = logs()
    assert record["outcome"] == "error"
    assert "status_update" not in record


def test_cancel_registry_failure_raises_and_sends_nothing(
    latest_table, alerts_queue, monkeypatch
):
    monkeypatch.setattr(
        handler,
        "dynamodb_client",
        lambda: Recorder(latest_table, "ddb", [], fail_on="get_item"),
    )

    with pytest.raises(ClientError):
        handler.critical_alert_handler(rule_event("alert_cancel"), None)

    assert alerts_queue.receive_all() == []


def test_cancel_store_failure_raises_after_the_update_is_queued(
    latest_table, alerts_queue, monkeypatch, logs
):
    monkeypatch.setattr(
        handler,
        "dynamodb_client",
        lambda: Recorder(latest_table, "ddb", [], fail_on="put_item"),
    )

    with pytest.raises(ClientError):
        handler.critical_alert_handler(rule_event("alert_cancel"), None)

    assert len(alerts_queue.receive_all()) == 1
    (record,) = logs()
    assert record["outcome"] == "error"
    assert record["status_update"] == "queued"


@pytest.mark.parametrize(
    "event",
    [
        rule_event("alert_cancel", extra_field=1),
        rule_event("alert_cancel", device_id=BEDROOM),
        rule_event("alert_cancel", schema_version=2),
        "not json",
    ],
)
def test_invalid_cancel_reads_no_registry_and_sends_nothing(
    latest_table, alerts_queue, monkeypatch, event
):
    calls: list = []
    monkeypatch.setattr(
        handler, "dynamodb_client", lambda: Recorder(latest_table, "ddb", calls)
    )

    result = handler.critical_alert_handler(event, None)

    assert result["outcome"] == "rejected"
    assert calls == []
    assert alerts_queue.receive_all() == []


# ======== logging (criterion 5) ========


def test_cancel_log_line_has_exactly_the_ids_outcome_and_queued(latest_table, logs):
    assign_device(latest_table, WEARABLE, "res-0001")
    event = rule_event("alert_cancel")

    handler.critical_alert_handler(event, None)

    (record,) = logs()
    assert set(record) - POWERTOOLS_KEYS == {
        "device_id",
        "message_id",
        "outcome",
        "status_update",
    }
    assert record["message"] == "alert_cancel"
    assert record["device_id"] == WEARABLE
    assert record["message_id"] == event["message_id"]
    assert record["outcome"] == "stored"
    assert record["status_update"] == "queued"


def test_cancel_log_has_no_resident_or_alert_fields(latest_table, logs):
    assign_device(latest_table, WEARABLE, "res-0007")

    handler.critical_alert_handler(rule_event("alert_cancel"), None)

    (record,) = logs()
    text = " ".join(
        f"{k}={v}" for k, v in record.items() if k not in {"timestamp", "location"}
    )
    assert "res-0007" not in text
    text = UUID_ANY_RE.sub("<uuid>", text)
    for hidden in ("resident", "alert_id", "alert_outcome", "changed_at", "by_role"):
        assert hidden not in text, hidden


def test_rejected_cancel_logs_reason_without_status_update(latest_table, logs):
    handler.critical_alert_handler(rule_event("alert_cancel", note="synthetic"), None)

    (record,) = logs()
    assert record["reason"] == "unknown_field"
    assert "status_update" not in record
    assert "synthetic" not in UUID_ANY_RE.sub("<uuid>", str(record))
