"""watchdog runs against moto (docs/features/watchdog.md, criteria 2, 8, 8a, 10, 15,
16, 20): what is read, what is sent to the alerts queue and the ops topic, in which
order, the log lines, AWS errors, and pagination."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import checks
import handler
import pytest
from botocore.exceptions import ClientError
from checks import BATTERY_LOW, DEVICE_OFFLINE, NOT_WORN, POSSIBLE_FALL
from conftest import ALERTS_TABLE, LATEST_TABLE, REGISTRY_TABLE
from events import (
    ALERT_ID,
    ALERT_ID_2,
    BEDROOM,
    NOW,
    NOW_ISO,
    RESIDENT,
    RESIDENT_2,
    STATUS_ID,
    WEARABLE,
    WEARABLE_2,
    ago,
    put_alert,
    put_device,
    put_status,
)

POWERTOOLS_KEYS = {"level", "location", "message", "timestamp", "service"}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ZERO = {DEVICE_OFFLINE: 0, BATTERY_LOW: 0, NOT_WORN: 0, POSSIBLE_FALL: 0}


def counts(**kw) -> dict[str, int]:
    return {**ZERO, **kw}


def throttle(op: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "synthetic"}}, op
    )


class DdbSpy:
    """Wraps the handler's DynamoDB client: records calls, can change a request (e.g.
    add Limit) or raise on one operation."""

    def __init__(self, client, limit=None, fail_on=None):
        self._client = client
        self.calls: list[tuple[str, dict]] = []
        self._limit = limit
        self._fail_on = fail_on

    def __getattr__(self, op):
        method = getattr(self._client, op)

        def wrapper(**kwargs):
            self.calls.append((op, kwargs))
            if op == self._fail_on:
                raise throttle(op)
            if op == "query" and self._limit:
                kwargs = {**kwargs, "Limit": self._limit}
            return method(**kwargs)

        return wrapper

    def ops(self, op):
        return [kw for name, kw in self.calls if name == op]


def ddb_spy(monkeypatch, **kw) -> DdbSpy:
    real = handler.dynamodb_client()
    spy = DdbSpy(real, **kw)
    monkeypatch.setattr(handler, "dynamodb_client", lambda: spy)
    return spy


def assigned(aws, device_id=WEARABLE, resident_id=RESIDENT, **status_fields):
    put_device(aws.ddb, device_id, resident_id)
    return put_status(aws.ddb, device_id, **status_fields)


# ======== raising (criteria 4-8) ========


def test_healthy_device_sends_nothing(aws):
    assigned(aws)

    summary = handler.run(NOW)

    assert aws.calls == []
    assert summary == {
        "devices_checked": 1,
        "raised": counts(),
        "resolved": counts(),
        "grouped": False,
        "test_mode_units": 0,
    }


def test_offline_device_sends_one_alert_message(aws):
    entry = assigned(aws, ts=ago(minutes=16))

    handler.run(NOW)

    (message,) = aws.alerts()
    assert message == {
        "schema_version": 1,
        "alert_id": checks.alert_id_for(
            entry["ts"], DEVICE_OFFLINE, "warning", WEARABLE
        ),
        "alert_type": DEVICE_OFFLINE,
        "severity": "warning",
        "resident_id": RESIDENT,
        "device_id": WEARABLE,
        "event_ts": entry["ts"],
        "received_at": NOW_ISO,
        "source_message_id": STATUS_ID,
        "details": {"last_seen": entry["ts"]},
    }


def test_alert_message_has_the_alert_type_attribute(aws):
    assigned(aws, battery_pct=12)

    handler.run(NOW)

    (send,) = aws.sends()
    assert send["QueueUrl"] == aws.queue_url
    assert send["MessageAttributes"] == {
        "alert_type": {"DataType": "String", "StringValue": BATTERY_LOW}
    }
    assert "DelaySeconds" not in send


def test_exactly_15_minutes_since_the_heartbeat_sends_nothing(aws):
    assigned(aws, ts=ago(minutes=15))

    handler.run(NOW)

    assert aws.sends() == []


def test_low_battery_at_19_is_raised_and_20_is_not(aws):
    assigned(aws, WEARABLE, battery_pct=19)
    assigned(aws, WEARABLE_2, RESIDENT_2, battery_pct=20)

    handler.run(NOW)

    (message,) = aws.alerts()
    assert (message["device_id"], message["details"]) == (
        WEARABLE,
        {"battery_pct": 19},
    )


def test_bedroom_unit_with_null_battery_sends_nothing(aws):
    assigned(aws, BEDROOM, battery_pct=None)

    handler.run(NOW)

    assert aws.sends() == []


def test_charging_wearable_with_low_battery_off_wrist_sends_nothing(aws):
    assigned(
        aws, battery_pct=5, worn=False, charging=True, worn_false_since=ago(hours=5)
    )

    handler.run(NOW)

    assert aws.sends() == []


def test_not_worn_reads_worn_false_since_from_the_status_item(aws):
    since = ago(hours=2)
    assigned(aws, worn=False, worn_false_since=since)

    handler.run(NOW)

    (message,) = aws.alerts()
    assert message["alert_type"] == NOT_WORN
    assert message["event_ts"] == since
    assert message["details"] == {"since": since}


def test_off_wrist_wearable_without_worn_false_since_sends_nothing(aws):
    assigned(aws, worn=False)

    handler.run(NOW)

    assert aws.sends() == []


def test_batch_get_reads_only_off_wrist_wearables(aws, monkeypatch):
    assigned(aws, WEARABLE, worn=False, worn_false_since=ago(minutes=30))
    assigned(aws, WEARABLE_2, RESIDENT_2, worn=True)
    assigned(aws, BEDROOM)
    spy = ddb_spy(monkeypatch)

    handler.run(NOW)

    (call,) = spy.ops("batch_get_item")
    request = call["RequestItems"][LATEST_TABLE]
    assert request["Keys"] == [
        {"pk": {"S": f"DEVICE#{WEARABLE}"}, "sk": {"S": "MSG#status"}}
    ]
    assert "ConsistentRead" not in request


def test_no_batch_get_when_no_wearable_is_off_wrist(aws, monkeypatch):
    assigned(aws)
    spy = ddb_spy(monkeypatch)

    handler.run(NOW)

    assert spy.ops("batch_get_item") == []


def test_spare_device_is_not_checked(aws):
    put_device(aws.ddb, WEARABLE, resident_id=None)
    put_status(aws.ddb, WEARABLE, ts=ago(hours=3), battery_pct=3)

    summary = handler.run(NOW)

    assert aws.sends() == []
    assert summary["devices_checked"] == 0


def test_device_that_never_sent_a_status_is_not_checked(aws):
    put_device(aws.ddb, WEARABLE, RESIDENT)

    summary = handler.run(NOW)

    assert aws.sends() == []
    assert summary["devices_checked"] == 1


def test_open_alert_of_the_same_device_and_type_blocks_a_second(aws):
    assigned(aws, ts=ago(minutes=30))
    put_alert(aws.ddb, DEVICE_OFFLINE)

    handler.run(NOW)

    assert aws.sends() == []


def test_acknowledged_alert_with_open_pk_also_blocks_a_second(aws):
    assigned(aws, battery_pct=10)
    put_alert(aws.ddb, BATTERY_LOW, status="acknowledged")

    handler.run(NOW)

    assert aws.sends() == []


def test_resolved_alert_without_open_pk_does_not_block(aws):
    assigned(aws, battery_pct=10)
    put_alert(aws.ddb, BATTERY_LOW, status="resolved", open_pk=False)

    handler.run(NOW)

    assert [m["alert_type"] for m in aws.alerts()] == [BATTERY_LOW]


def test_rerun_of_the_same_run_sends_the_same_alert_id(aws):
    assigned(aws, ts=ago(minutes=20))

    handler.run(NOW)
    handler.run(NOW)

    first, second = aws.alerts()
    assert first == second


# ======== resolving (criterion 10) ========


def test_cleared_battery_alert_sends_a_system_resolve(aws):
    assigned(aws, battery_pct=80)
    item = put_alert(aws.ddb, BATTERY_LOW)

    summary = handler.run(NOW)

    assert aws.resolves() == [
        {
            "kind": "status_update",
            "schema_version": 1,
            "resident_id": RESIDENT,
            "new_status": "resolved",
            "changed_at": NOW_ISO,
            "by_role": "system",
            "by": None,
            "by_name": None,
            "target": {"alert_id": ALERT_ID, "event_ts": item["event_ts"]},
        }
    ]
    assert summary["resolved"] == counts(battery_low=1)
    assert aws.alerts() == []


def test_resolve_has_no_delay_and_no_attributes(aws):
    assigned(aws)
    put_alert(aws.ddb, DEVICE_OFFLINE)

    handler.run(NOW)

    (send,) = aws.sends()
    assert set(send) == {"QueueUrl", "MessageBody"}


@pytest.mark.parametrize(
    ("alert_type", "status_fields"),
    [
        (DEVICE_OFFLINE, {}),
        (BATTERY_LOW, {"battery_pct": 20}),
        (NOT_WORN, {"worn": True}),
    ],
)
def test_each_type_is_resolved_when_cleared(aws, alert_type, status_fields):
    assigned(aws, **status_fields)
    put_alert(aws.ddb, alert_type)

    handler.run(NOW)

    (resolve,) = aws.resolves()
    assert resolve["target"]["alert_id"] == ALERT_ID


def test_acknowledged_device_problem_is_resolved_when_cleared(aws):
    assigned(aws, worn=True)
    put_alert(aws.ddb, NOT_WORN, status="acknowledged")

    handler.run(NOW)

    assert len(aws.resolves()) == 1


@pytest.mark.parametrize("alert_type", [DEVICE_OFFLINE, BATTERY_LOW, NOT_WORN])
def test_alert_of_a_device_made_spare_is_resolved(aws, alert_type):
    put_device(aws.ddb, WEARABLE, resident_id=None)
    put_status(aws.ddb, WEARABLE, ts=ago(minutes=40), battery_pct=5, worn=False)
    put_alert(aws.ddb, alert_type)

    handler.run(NOW)

    (resolve,) = aws.resolves()
    assert resolve["resident_id"] == RESIDENT


def test_alert_of_a_device_reassigned_to_another_resident_is_resolved(aws):
    assigned(aws, WEARABLE, RESIDENT_2, ts=ago(minutes=40))
    put_alert(aws.ddb, DEVICE_OFFLINE, resident_id=RESIDENT)

    handler.run(NOW)

    (resolve,) = aws.resolves()
    assert resolve["resident_id"] == RESIDENT
    # The old alert still blocks by device and type in this run.
    assert aws.alerts() == []


def test_alert_of_a_device_without_a_status_item_is_resolved(aws):
    put_device(aws.ddb, WEARABLE, RESIDENT)
    put_alert(aws.ddb, BATTERY_LOW)

    handler.run(NOW)

    assert len(aws.resolves()) == 1


@pytest.mark.parametrize("alert_type", [BATTERY_LOW, NOT_WORN])
def test_battery_and_not_worn_alerts_stay_open_while_offline(aws, alert_type):
    assigned(aws, ts=ago(minutes=30), battery_pct=90, worn=True)
    put_alert(aws.ddb, alert_type)

    handler.run(NOW)

    assert aws.resolves() == []
    assert [m["alert_type"] for m in aws.alerts()] == [DEVICE_OFFLINE]


def test_open_health_alert_of_the_device_is_never_resolved(aws):
    assigned(aws)
    put_alert(aws.ddb, "fall")

    handler.run(NOW)

    assert aws.calls == []


# ======== grouping (criterion 8a) ========


def offline_fleet(aws, n: int) -> None:
    for i in range(1, n + 1):
        assigned(aws, f"wearable-{i:04d}", f"res-{i:04d}", ts=ago(minutes=20))


def test_four_offline_devices_send_four_alerts_and_no_summary(aws):
    offline_fleet(aws, 4)

    summary = handler.run(NOW)

    assert aws.publishes() == []
    assert len(aws.alerts()) == 4
    assert all("grouped" not in m["details"] for m in aws.alerts())
    assert summary["grouped"] is False


def test_five_offline_devices_send_one_summary_and_five_grouped_alerts(aws):
    offline_fleet(aws, 5)

    summary = handler.run(NOW)

    (publish,) = aws.publishes()
    assert publish["TopicArn"] == aws.ops_topic_arn
    assert publish["Subject"] == "SilverCare device problem: 5 devices offline"
    for i in range(1, 6):
        assert f"wearable-{i:04d} (res-{i:04d})" in publish["Message"]
    assert "MessageAttributes" not in publish
    assert len(aws.alerts()) == 5
    assert all(m["details"]["grouped"] is True for m in aws.alerts())
    assert summary["grouped"] is True
    assert summary["raised"] == counts(device_offline=5)


def test_summary_is_published_before_any_alert_is_sent(aws):
    offline_fleet(aws, 5)

    handler.run(NOW)

    assert [op for op, _ in aws.calls] == ["publish"] + ["send_message"] * 5


def test_failed_summary_publish_sends_no_alert_and_raises(aws):
    offline_fleet(aws, 5)
    aws.sns.fail["publish"] = throttle("Publish")

    with pytest.raises(ClientError):
        handler.run(NOW)

    assert aws.sends() == []


def test_five_offline_with_one_already_open_is_not_grouped(aws):
    offline_fleet(aws, 5)
    put_alert(aws.ddb, DEVICE_OFFLINE, "wearable-0001", "res-0001")

    handler.run(NOW)

    assert aws.publishes() == []
    assert len(aws.alerts()) == 4


# ======== logs (criterion 16) ========


def strip(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in ("timestamp", "location")}


def test_one_log_line_per_raised_alert_with_ids_only(aws, logs):
    assigned(aws, battery_pct=13)

    handler.run(NOW)

    (line,) = [r for r in logs() if r.get("action") == "raised"]
    assert set(line) - POWERTOOLS_KEYS == {
        "action",
        "alert_id",
        "alert_type",
        "device_id",
        "resident_id",
    }
    assert line["alert_type"] == BATTERY_LOW
    assert line["device_id"] == WEARABLE
    assert line["resident_id"] == RESIDENT
    assert line["alert_id"] == aws.alerts()[0]["alert_id"]


def test_one_log_line_per_resolved_alert_with_ids_only(aws, logs):
    assigned(aws)
    put_alert(aws.ddb, DEVICE_OFFLINE, alert_id=ALERT_ID_2)

    handler.run(NOW)

    (line,) = [r for r in logs() if r.get("action") == "resolved"]
    assert set(line) - POWERTOOLS_KEYS == {
        "action",
        "alert_id",
        "alert_type",
        "device_id",
        "resident_id",
    }
    assert line["alert_id"] == ALERT_ID_2


def run_handler(monkeypatch, now: datetime = NOW):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(handler, "datetime", FixedDatetime)
    return handler.lambda_handler({"source": "aws.events"}, None)


def test_lambda_handler_logs_one_summary_line_with_counts(aws, logs, monkeypatch):
    assigned(aws, WEARABLE, battery_pct=13)
    assigned(aws, WEARABLE_2, RESIDENT_2, ts=ago(minutes=40))
    put_alert(aws.ddb, NOT_WORN, alert_id=ALERT_ID_2)

    result = run_handler(monkeypatch)

    (line,) = [r for r in logs() if r.get("outcome") == "done"]
    assert set(line) - POWERTOOLS_KEYS == {
        "outcome",
        "devices_checked",
        "raised",
        "resolved",
        "grouped",
        "test_mode_units",
    }
    assert line["devices_checked"] == 2
    assert line["raised"] == counts(battery_low=1, device_offline=1)
    assert line["resolved"] == counts(not_worn=1)
    assert line["grouped"] is False
    assert line["test_mode_units"] == 0
    assert result["devices_checked"] == 2


def test_lambda_handler_uses_the_run_start_as_now(aws, monkeypatch):
    assigned(aws, ts=ago(minutes=10))

    run_handler(monkeypatch, NOW + timedelta(minutes=6))

    (message,) = aws.alerts()
    assert message["received_at"] == "2026-11-04T04:06:00.000Z"


def test_logs_never_contain_battery_values_timestamps_or_details(
    aws, logs, monkeypatch
):
    assigned(aws, WEARABLE, battery_pct=13, worn=False, worn_false_since=ago(hours=3))
    assigned(aws, WEARABLE_2, RESIDENT_2, ts=ago(minutes=47))
    put_alert(aws.ddb, DEVICE_OFFLINE, BEDROOM, alert_id=ALERT_ID_2)
    put_device(aws.ddb, BEDROOM, RESIDENT)
    put_status(aws.ddb, BEDROOM, battery_pct=None)

    run_handler(monkeypatch)

    records = [strip(r) for r in logs()]
    assert records
    text = json.dumps(records)
    ids = {ALERT_ID_2, STATUS_ID, *(m["alert_id"] for m in aws.alerts())}
    for value in ids:
        text = text.replace(value, "<id>")
    assert not UUID.search(text)
    for value in (
        "13",
        "battery_pct",
        "last_seen",
        "since",
        "details",
        "2026",
        ago(minutes=47),
        "worn_false_since",
        '"worn"',
    ):
        assert value not in text, value


# ======== AWS errors (criterion 15) ========


@pytest.mark.parametrize("op", ["query", "batch_get_item"])
def test_dynamodb_error_fails_the_run_and_sends_nothing(aws, monkeypatch, op, logs):
    assigned(aws, ts=ago(minutes=20), worn=False, worn_false_since=ago(hours=3))
    ddb_spy(monkeypatch, fail_on=op)

    with pytest.raises(RuntimeError):
        run_handler(monkeypatch)

    assert aws.sends() == []
    (line,) = [r for r in logs() if r.get("outcome") == "error"]
    assert line["error"] == "ThrottlingException"
    assert line["level"] == "ERROR"


def test_failed_alert_send_fails_the_run(aws, monkeypatch):
    assigned(aws, battery_pct=5)
    aws.sqs.fail["send_message"] = throttle("SendMessage")

    with pytest.raises(RuntimeError):
        run_handler(monkeypatch)


def test_failed_resolve_send_fails_the_run(aws, monkeypatch):
    assigned(aws)
    put_alert(aws.ddb, BATTERY_LOW)
    aws.sqs.fail["send_message"] = throttle("SendMessage")

    with pytest.raises(RuntimeError):
        run_handler(monkeypatch)


def test_raised_error_has_a_fixed_message_and_no_cause(aws, monkeypatch):
    assigned(aws, battery_pct=5)
    aws.sqs.fail["send_message"] = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "synthetic res-0001"}},
        "SendMessage",
    )

    with pytest.raises(RuntimeError) as raised:
        run_handler(monkeypatch)

    assert str(raised.value) == "watchdog run failed"
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


def test_failed_run_logs_no_done_line(aws, monkeypatch, logs):
    assigned(aws, battery_pct=5)
    aws.sqs.fail["send_message"] = throttle("SendMessage")

    with pytest.raises(RuntimeError):
        run_handler(monkeypatch)

    assert [r for r in logs() if r.get("outcome") == "done"] == []


class FakeBatchClient:
    """Answers batch_get_item with scripted responses (no moto)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def batch_get_item(self, RequestItems):
        self.requests.append(RequestItems)
        return self.responses.pop(0)


def keys_request(device_id: str) -> dict:
    return {
        LATEST_TABLE: {
            "Keys": [{"pk": {"S": f"DEVICE#{device_id}"}, "sk": {"S": "MSG#status"}}],
            "ProjectionExpression": "#pk, #since",
            "ExpressionAttributeNames": {"#pk": "pk", "#since": "worn_false_since"},
        }
    }


def found(device_id: str, since: str) -> dict:
    return {
        "pk": {"S": f"DEVICE#{device_id}"},
        "worn_false_since": {"S": since},
    }


def test_unprocessed_keys_are_retried_until_read(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(handler.time, "sleep", sleeps.append)
    since = ago(hours=3)
    fake = FakeBatchClient(
        [
            {
                "Responses": {LATEST_TABLE: []},
                "UnprocessedKeys": keys_request(WEARABLE),
            },
            {"Responses": {LATEST_TABLE: [found(WEARABLE, since)]}},
        ]
    )
    monkeypatch.setattr(handler, "dynamodb_client", lambda: fake)

    result = handler.read_worn_since([WEARABLE])

    assert result == {WEARABLE: since}
    assert fake.requests[1] == keys_request(WEARABLE)
    assert len(sleeps) == 1


def test_unprocessed_keys_after_every_attempt_fail_the_run(monkeypatch):
    monkeypatch.setattr(handler.time, "sleep", lambda s: None)
    stuck = {"Responses": {LATEST_TABLE: []}, "UnprocessedKeys": keys_request(WEARABLE)}
    fake = FakeBatchClient([stuck] * handler.BATCH_GET_ATTEMPTS)
    monkeypatch.setattr(handler, "dynamodb_client", lambda: fake)

    with pytest.raises(RuntimeError):
        handler.read_worn_since([WEARABLE])

    assert len(fake.requests) == handler.BATCH_GET_ATTEMPTS


def test_item_without_worn_false_since_is_left_out(monkeypatch):
    fake = FakeBatchClient(
        [{"Responses": {LATEST_TABLE: [{"pk": {"S": f"DEVICE#{WEARABLE}"}}]}}]
    )
    monkeypatch.setattr(handler, "dynamodb_client", lambda: fake)

    assert handler.read_worn_since([WEARABLE]) == {}


# ======== pagination (criterion 2) ========


def test_every_query_is_followed_to_the_last_page(aws, monkeypatch):
    for i in range(1, 4):
        assigned(aws, f"wearable-{i:04d}", f"res-{i:04d}", battery_pct=10)
    put_device(aws.ddb, "wearable-0004", "res-0004")
    put_status(aws.ddb, "wearable-0004", battery_pct=80)
    put_alert(aws.ddb, BATTERY_LOW, "wearable-0004", "res-0004", alert_id=ALERT_ID)
    put_alert(aws.ddb, BATTERY_LOW, "wearable-0003", "res-0003", alert_id=ALERT_ID_2)
    spy = ddb_spy(monkeypatch, limit=1)

    summary = handler.run(NOW)

    assert summary["devices_checked"] == 4
    assert sorted(m["device_id"] for m in aws.alerts()) == [
        "wearable-0001",
        "wearable-0002",
    ]
    assert [r["target"]["alert_id"] for r in aws.resolves()] == [ALERT_ID]
    pages = spy.ops("query")
    assert any("ExclusiveStartKey" in q for q in pages)
    assert len(pages) > 3


def test_queries_are_eventually_consistent_on_the_three_indexes(aws, monkeypatch):
    assigned(aws)
    spy = ddb_spy(monkeypatch)

    handler.run(NOW)

    queries = spy.ops("query")
    reads = sorted(
        (
            q["TableName"],
            q["IndexName"],
            next(iter(q["ExpressionAttributeValues"].values()))["S"],
        )
        for q in queries
    )
    assert reads == [
        (ALERTS_TABLE, "open", "OPEN"),
        (LATEST_TABLE, "by-message", "bed"),
        (LATEST_TABLE, "by-message", "motion"),
        (LATEST_TABLE, "by-message", "status"),
        (REGISTRY_TABLE, "by-type", "DEVICE"),
        (REGISTRY_TABLE, "by-type", "TEST_MODE"),
    ]
    assert all("ConsistentRead" not in q for q in queries)


def test_batch_get_is_split_at_100_keys(aws, monkeypatch):
    for i in range(1, 102):
        assigned(
            aws,
            f"wearable-{i:04d}",
            f"res-{i:04d}",
            worn=False,
            worn_false_since=ago(minutes=30),
        )
    spy = ddb_spy(monkeypatch)

    handler.run(NOW)

    sizes = [
        len(c["RequestItems"][LATEST_TABLE]["Keys"]) for c in spy.ops("batch_get_item")
    ]
    assert sizes == [100, 1]
