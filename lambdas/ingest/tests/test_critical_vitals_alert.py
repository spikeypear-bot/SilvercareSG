"""critical-alert raises a critical alert for a device `vitals_alert`
(docs/features/vitals-alerts.md, criteria 1-3, 16, 16a/16b, 29).

moto for DynamoDB (latest + registry) and SQS (the alerts queue). Falls are covered in
test_critical_alert.py; this file adds the vitals_alert path and checks falls still go
through the renamed entry point.
"""

from __future__ import annotations

import re

import alerts
import handler
import pytest
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
from conftest import REGISTRY_TABLE, TABLE_NAME, assign_device
from events import BEDROOM, WEARABLE, rule_event, ts_at

RESIDENT = "res-0002"
UUID_ANY_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_d = TypeDeserializer()


def vitals_alert(**overrides) -> dict:
    return rule_event("vitals_alert", **overrides)


def no_pulse(**overrides) -> dict:
    return vitals_alert(value=0, limit=45, direction="below", **overrides)


def stored_item(client, message: str) -> dict | None:
    item = client.get_item(
        TableName=TABLE_NAME,
        Key={"pk": {"S": f"DEVICE#{WEARABLE}"}, "sk": {"S": f"MSG#{message}"}},
        ConsistentRead=True,
    ).get("Item")
    return {k: _d.deserialize(v) for k, v in item.items()} if item else None


class Recorder:
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


def test_vitals_alert_queues_a_critical_alert_for_the_resident(
    latest_table, alerts_queue
):
    assign_device(latest_table, WEARABLE, RESIDENT)
    event = vitals_alert()

    assert handler.critical_alert_handler(event, None) == {"outcome": "stored"}

    (body,) = alerts_queue.bodies()
    assert body == {
        "schema_version": 1,
        "alert_id": alerts.alert_id_for(event["ts"], "hr_high", "critical", WEARABLE),
        "alert_type": "hr_high",
        "severity": "critical",
        "resident_id": RESIDENT,
        "device_id": WEARABLE,
        "event_ts": event["ts"],
        "received_at": "2026-10-18T03:12:00.410Z",
        "source_message_id": event["message_id"],
        "details": {
            "metric": "hr_bpm",
            "value": 140,
            "limit": 130,
            "direction": "above",
            "held_s": 10,
        },
    }


@pytest.mark.parametrize(
    ("overrides", "alert_type"),
    [
        ({}, "hr_high"),
        ({"value": 40, "limit": 45, "direction": "below"}, "hr_low"),
        ({"value": 0, "limit": 45, "direction": "below"}, "hr_low"),
        (
            {"metric": "spo2_pct", "value": 85, "limit": 88, "direction": "below"},
            "spo2_low",
        ),
    ],
    ids=["hr_high", "hr_low", "no_pulse", "spo2_low"],
)
def test_vitals_alert_maps_to_alert_type(
    latest_table, alerts_queue, overrides, alert_type
):
    handler.critical_alert_handler(vitals_alert(**overrides), None)

    (message,) = alerts_queue.receive_all()
    assert message["MessageAttributes"]["alert_type"]["StringValue"] == alert_type


def test_no_pulse_details_keep_value_zero(latest_table, alerts_queue):
    handler.critical_alert_handler(no_pulse(), None)

    (body,) = alerts_queue.bodies()
    assert body["alert_type"] == "hr_low"
    assert body["severity"] == "critical"
    assert body["details"]["value"] == 0
    assert body["details"]["direction"] == "below"


@pytest.mark.parametrize(
    "overrides",
    [
        {"value": 125, "limit": 130, "direction": "above"},
        {"value": 140, "limit": 45, "direction": "above"},
    ],
    ids=["value_not_past_limit", "limit_direction_mismatch"],
)
def test_untidy_vitals_alert_still_raises_a_critical_alert(
    latest_table, alerts_queue, overrides
):
    assert handler.critical_alert_handler(vitals_alert(**overrides), None) == {
        "outcome": "stored"
    }

    (body,) = alerts_queue.bodies()
    assert body["alert_type"] == "hr_high"
    assert body["severity"] == "critical"


@pytest.mark.parametrize(
    ("overrides", "alert_type"),
    [
        ({"value": 115, "limit": 110, "direction": "above"}, "hr_high"),
        (
            {"metric": "spo2_pct", "value": 90, "limit": 92, "direction": "below"},
            "spo2_low",
        ),
    ],
    ids=["hr_limit_110", "spo2_limit_92"],
)
def test_tightened_device_limit_raises_a_critical_alert_with_that_limit(
    latest_table, alerts_queue, overrides, alert_type
):
    # docs/features/device-alert-limits.md, criterion 2.
    assert handler.critical_alert_handler(vitals_alert(**overrides), None) == {
        "outcome": "stored"
    }

    (body,) = alerts_queue.bodies()
    assert body["alert_type"] == alert_type
    assert body["severity"] == "critical"
    assert body["details"]["limit"] == overrides["limit"]


def test_unassigned_device_queues_alert_as_unassigned(latest_table, alerts_queue):
    handler.critical_alert_handler(vitals_alert(), None)

    (body,) = alerts_queue.bodies()
    assert body["resident_id"] == "unassigned"


# ---- storage (criterion 16) ----


def test_vitals_alert_is_stored_in_latest_without_watch_type(latest_table):
    event = vitals_alert()

    handler.critical_alert_handler(event, None)

    item = stored_item(latest_table, "vitals_alert")
    assert item is not None
    assert "watch_type" not in item
    (entry,) = item["entries"]
    assert entry["message_id"] == event["message_id"]
    assert {k: entry[k] for k in ("metric", "value", "limit", "direction")} == {
        "metric": "hr_bpm",
        "value": 140,
        "limit": 130,
        "direction": "above",
    }


def test_duplicate_vitals_alert_queues_the_same_alert_id_and_stores_once(
    latest_table, alerts_queue
):
    event = vitals_alert()
    handler.critical_alert_handler(event, None)

    assert handler.critical_alert_handler(dict(event), None) == {"outcome": "duplicate"}

    first, second = alerts_queue.bodies()
    assert first["alert_id"] == second["alert_id"]
    assert len(stored_item(latest_table, "vitals_alert")["entries"]) == 1


def test_lookup_then_send_then_store(latest_table, monkeypatch):
    calls: list = []
    sqs = handler.sqs_client()
    monkeypatch.setattr(
        handler, "dynamodb_client", lambda: Recorder(latest_table, "ddb", calls)
    )
    monkeypatch.setattr(handler, "sqs_client", lambda: Recorder(sqs, "sqs", calls))

    handler.critical_alert_handler(vitals_alert(), None)

    assert calls == [
        ("ddb", "get_item", REGISTRY_TABLE),
        ("sqs", "send_message", None),
        ("ddb", "get_item", TABLE_NAME),
        ("ddb", "put_item", TABLE_NAME),
    ]


def test_send_failure_raises_and_stores_nothing(latest_table, monkeypatch, logs):
    sqs = handler.sqs_client()
    monkeypatch.setattr(
        handler,
        "sqs_client",
        lambda: Recorder(sqs, "sqs", [], fail_on="send_message"),
    )

    with pytest.raises(ClientError):
        handler.critical_alert_handler(vitals_alert(), None)

    assert stored_item(latest_table, "vitals_alert") is None
    assert logs()[-1]["outcome"] == "error"


def test_vitals_alert_reads_no_thresholds(latest_table, monkeypatch):
    # The device's limits are fixed; no per-resident check in critical-alert.
    assign_device(latest_table, WEARABLE, RESIDENT)
    keys: list = []

    class KeyRecorder:
        def __getattr__(self, name):
            method = getattr(latest_table, name)

            def wrapper(**kwargs):
                keys.append(kwargs.get("Key", {}).get("pk", {}).get("S"))
                return method(**kwargs)

            return wrapper

    monkeypatch.setattr(handler, "dynamodb_client", KeyRecorder)

    handler.critical_alert_handler(vitals_alert(), None)

    assert not any(k and k.startswith("THRESHOLDS#") for k in keys)


# ---- invalid input ----


@pytest.mark.parametrize(
    ("event", "reason"),
    [
        (vitals_alert(metric="spo2_pct", value=99, limit=88), "invalid:direction"),
        (vitals_alert(value=0, limit=45, direction="above"), "invalid:direction"),
        (vitals_alert(value=15, limit=45, direction="below"), "out_of_range:value"),
        (vitals_alert(device_id=BEDROOM), "invalid:message"),
        (vitals_alert(held_s=9), "out_of_range:held_s"),
        (vitals_alert(schema_version=2), "invalid:schema_version"),
    ],
)
def test_invalid_vitals_alert_raises_no_alert_and_stores_nothing(
    latest_table, alerts_queue, logs, event, reason
):
    assert handler.critical_alert_handler(event, None) == {
        "outcome": "rejected",
        "reason": reason,
    }

    assert alerts_queue.receive_all() == []
    assert stored_item(latest_table, "vitals_alert") is None
    (record,) = logs()
    assert "alert_id" not in record


def test_vitals_alert_sent_to_telemetry_is_wrong_function(latest_table, alerts_queue):
    assert handler.telemetry_handler(vitals_alert(), None) == {
        "outcome": "rejected",
        "reason": "wrong_function",
    }
    assert alerts_queue.receive_all() == []


# ---- falls still handled by the renamed entry point (criterion 16a) ----


def test_fall_is_still_handled_by_critical_alert_handler(latest_table, alerts_queue):
    assign_device(latest_table, WEARABLE, RESIDENT)
    event = rule_event("fall")

    assert handler.critical_alert_handler(event, None) == {"outcome": "stored"}

    (body,) = alerts_queue.bodies()
    assert body["alert_type"] == "fall"
    assert body["severity"] == "critical"
    assert body["alert_id"] == alerts.alert_id_for(
        event["ts"], "fall", "critical", WEARABLE
    )


def test_fall_alert_handler_name_is_gone():
    assert not hasattr(handler, "fall_alert_handler")
    assert callable(handler.critical_alert_handler)


def test_fall_and_vitals_alert_are_stored_in_separate_items(latest_table):
    handler.critical_alert_handler(rule_event("fall", ts=ts_at(13)), None)
    handler.critical_alert_handler(vitals_alert(ts=ts_at(14)), None)

    assert len(stored_item(latest_table, "fall")["entries"]) == 1
    assert len(stored_item(latest_table, "vitals_alert")["entries"]) == 1


# ---- logs ----


def test_log_line_has_alert_id_and_no_values(latest_table, alerts_queue, logs):
    event = vitals_alert(value=187, held_s=33)

    handler.critical_alert_handler(event, None)

    (record,) = logs()
    (body,) = alerts_queue.bodies()
    assert record["alert_id"] == body["alert_id"]
    assert record["alert_outcome"] == "queued"
    text = " ".join(
        f"{k}={v}" for k, v in record.items() if k not in {"timestamp", "location"}
    )
    text = UUID_ANY_RE.sub("<id>", text)
    for hidden in ("187", "130", "33", "hr_bpm", "above", "limit", "unassigned"):
        assert hidden not in text, hidden
