"""critical-alert raises a fall alert (docs/features/alerts-core.md, criteria 4-8).

moto for DynamoDB (latest + registry) and SQS (the alerts queue).
"""

from __future__ import annotations

import re

import alerts
import handler
import pytest
from botocore.exceptions import ClientError
from conftest import REGISTRY_TABLE, TABLE_NAME, assign_device
from events import WEARABLE, rule_event

UUID_ANY_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def latest_fall_item(client):
    return client.get_item(
        TableName=TABLE_NAME,
        Key={"pk": {"S": f"DEVICE#{WEARABLE}"}, "sk": {"S": "MSG#fall"}},
        ConsistentRead=True,
    ).get("Item")


class Recorder:
    """Wraps a client and appends (service, operation, table) to a shared list."""

    def __init__(self, client, service, calls, fail_on=None):
        self._client = client
        self._service = service
        self._calls = calls
        self._fail_on = fail_on

    def __getattr__(self, name):
        method = getattr(self._client, name)

        def wrapper(**kwargs):
            self._calls.append((self._service, name, kwargs.get("TableName")))
            if name == self._fail_on:
                raise ClientError(
                    {"Error": {"Code": "ThrottlingException", "Message": "x"}}, name
                )
            return method(**kwargs)

        return wrapper


# ---- the alert message ----


def test_fall_from_assigned_device_queues_alert_for_its_resident(
    latest_table, alerts_queue
):
    assign_device(latest_table, WEARABLE, "res-0001")
    event = rule_event("fall")

    assert handler.critical_alert_handler(event, None) == {"outcome": "stored"}

    (body,) = alerts_queue.bodies()
    assert body == {
        "schema_version": 1,
        "alert_id": alerts.alert_id_for(event["ts"], "fall", "critical", WEARABLE),
        "alert_type": "fall",
        "severity": "critical",
        "resident_id": "res-0001",
        "device_id": WEARABLE,
        "event_ts": event["ts"],
        "received_at": "2026-10-18T03:12:00.410Z",
        "source_message_id": event["message_id"],
        "details": {"confidence": 0.91, "peak_g": 3.8, "still_s": 20},
    }


def test_fall_from_unassigned_device_queues_alert_as_unassigned(
    latest_table, alerts_queue
):
    handler.critical_alert_handler(rule_event("fall"), None)

    (body,) = alerts_queue.bodies()
    assert body["resident_id"] == "unassigned"


def test_queued_message_has_alert_type_attribute(latest_table, alerts_queue):
    handler.critical_alert_handler(rule_event("fall"), None)

    (message,) = alerts_queue.receive_all()
    assert message["MessageAttributes"] == {
        "alert_type": {"DataType": "String", "StringValue": "fall"}
    }


def test_queued_body_keeps_exact_decimals(latest_table, alerts_queue):
    handler.critical_alert_handler(rule_event("fall", confidence=0.91), None)

    (message,) = alerts_queue.receive_all()
    assert '"confidence":0.91' in message["Body"]


def test_duplicate_fall_queues_the_same_alert_id_and_stores_once(
    latest_table, alerts_queue
):
    event = rule_event("fall")
    handler.critical_alert_handler(event, None)

    assert handler.critical_alert_handler(dict(event), None) == {"outcome": "duplicate"}

    first, second = alerts_queue.bodies()
    assert first["alert_id"] == second["alert_id"]


def test_registry_lookup_is_cached_across_invocations(latest_table, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        handler, "dynamodb_client", lambda: Recorder(latest_table, "ddb", calls)
    )

    handler.critical_alert_handler(rule_event("fall"), None)
    handler.critical_alert_handler(
        rule_event("fall", ts="2026-10-18T03:13:00.000Z"), None
    )

    registry_reads = [c for c in calls if c[2] == REGISTRY_TABLE]
    assert len(registry_reads) == 1


# ---- order and failures (criterion 4) ----


def test_lookup_then_send_then_store(latest_table, monkeypatch):
    calls: list = []
    sqs = handler.sqs_client()
    monkeypatch.setattr(
        handler, "dynamodb_client", lambda: Recorder(latest_table, "ddb", calls)
    )
    monkeypatch.setattr(handler, "sqs_client", lambda: Recorder(sqs, "sqs", calls))

    handler.critical_alert_handler(rule_event("fall"), None)

    assert calls == [
        ("ddb", "get_item", REGISTRY_TABLE),
        ("sqs", "send_message", None),
        ("ddb", "get_item", TABLE_NAME),
        ("ddb", "put_item", TABLE_NAME),
    ]


def test_send_failure_raises_and_stores_nothing(latest_table, monkeypatch, logs):
    calls: list = []
    sqs = handler.sqs_client()
    monkeypatch.setattr(
        handler,
        "sqs_client",
        lambda: Recorder(sqs, "sqs", calls, fail_on="send_message"),
    )

    with pytest.raises(ClientError):
        handler.critical_alert_handler(rule_event("fall"), None)

    assert latest_fall_item(latest_table) is None
    (record,) = logs()
    assert record["outcome"] == "error"
    assert "alert_id" not in record
    assert "alert_outcome" not in record


def test_registry_failure_raises_and_sends_nothing(
    latest_table, alerts_queue, monkeypatch
):
    calls: list = []
    monkeypatch.setattr(
        handler,
        "dynamodb_client",
        lambda: Recorder(latest_table, "ddb", calls, fail_on="get_item"),
    )

    with pytest.raises(ClientError):
        handler.critical_alert_handler(rule_event("fall"), None)

    assert alerts_queue.receive_all() == []


def test_store_failure_after_send_raises_and_the_alert_is_already_queued(
    latest_table, alerts_queue, monkeypatch, logs
):
    calls: list = []
    monkeypatch.setattr(
        handler,
        "dynamodb_client",
        lambda: Recorder(latest_table, "ddb", calls, fail_on="put_item"),
    )

    with pytest.raises(ClientError):
        handler.critical_alert_handler(rule_event("fall"), None)

    assert len(alerts_queue.receive_all()) == 1
    (record,) = logs()
    assert record["outcome"] == "error"
    assert record["alert_outcome"] == "queued"


def test_retry_after_store_failure_sends_the_same_alert_id(
    latest_table, alerts_queue, monkeypatch
):
    event = rule_event("fall")
    calls: list = []
    with monkeypatch.context() as m:
        m.setattr(
            handler,
            "dynamodb_client",
            lambda: Recorder(latest_table, "ddb", calls, fail_on="put_item"),
        )
        with pytest.raises(ClientError):
            handler.critical_alert_handler(event, None)

    assert handler.critical_alert_handler(event, None) == {"outcome": "stored"}

    first, second = alerts_queue.bodies()
    assert first == second


# ---- invalid falls raise no alert (criterion 5) ----


@pytest.mark.parametrize(
    "event",
    [
        rule_event("fall", confidence=1.5),
        rule_event("fall", accel_window=[]),
        rule_event("fall", schema_version=2),
        rule_event("fall", still_s="twenty"),
        "not json",
    ],
)
def test_invalid_fall_raises_no_alert_and_reads_no_registry(
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


# ---- logging (criterion 7) ----


def test_fall_log_line_has_alert_id_and_queued(latest_table, logs):
    assign_device(latest_table, WEARABLE, "res-0001")
    event = rule_event("fall")

    handler.critical_alert_handler(event, None)

    (record,) = logs()
    assert record["alert_id"] == alerts.alert_id_for(
        event["ts"], "fall", "critical", WEARABLE
    )
    assert record["alert_outcome"] == "queued"
    assert record["outcome"] == "stored"


def test_fall_log_has_no_resident_or_fall_values(latest_table, logs):
    assign_device(latest_table, WEARABLE, "res-0007")
    event = rule_event("fall", confidence=0.87, peak_g=4.6, still_s=33)

    handler.critical_alert_handler(event, None)

    # The timestamp and line number can contain "33" or "4.6": leave them out.
    (record,) = logs()
    text = " ".join(
        f"{k}={v}" for k, v in record.items() if k not in {"timestamp", "location"}
    )
    text = UUID_ANY_RE.sub("<uuid>", text)
    for hidden in ("res-0007", "0.87", "4.6", "33", "resident", "confidence"):
        assert hidden not in text, hidden


def test_rejected_fall_logs_no_alert_fields(latest_table, logs):
    handler.critical_alert_handler(rule_event("fall", confidence=1.5), None)

    (record,) = logs()
    assert "alert_id" not in record
    assert "alert_outcome" not in record


# ---- config (criterion 8) ----


def test_registry_table_comes_from_environment(latest_table, alerts_queue, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        handler, "dynamodb_client", lambda: Recorder(latest_table, "ddb", calls)
    )
    monkeypatch.setenv("REGISTRY_TABLE", "synthetic-registry")

    with pytest.raises(ClientError):
        handler.critical_alert_handler(rule_event("fall"), None)

    assert calls[0] == ("ddb", "get_item", "synthetic-registry")
    assert alerts_queue.receive_all() == []


def test_queue_url_comes_from_environment(latest_table, monkeypatch):
    sent: list = []

    class SpySqs:
        def send_message(self, **kwargs):
            sent.append(kwargs["QueueUrl"])

    monkeypatch.setattr(handler, "sqs_client", SpySqs)
    monkeypatch.setenv("ALERTS_QUEUE_URL", "https://example.invalid/synthetic-queue")

    handler.critical_alert_handler(rule_event("fall"), None)

    assert sent == ["https://example.invalid/synthetic-queue"]
