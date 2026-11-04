"""Escalation to nurses end to end against moto (docs/features/escalation.md, criteria
2-10 and 17; changed by docs/features/alert-routing.md, criteria 1 and 4): queuing the
delayed check on the alert path, and handling it.

Since alert-routing only warnings from assigned devices get a check (critical and
unassigned alerts go to the nurses at once: test_routing.py), so these tests use a
warning. Checks for critical or unassigned items stored before alert-routing (no
escalated_at) are still handled the same way; those tests store the item directly."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import email_text
import handler
import pytest
from botocore.exceptions import ClientError
from conftest import ALERTS_TABLE
from events import (
    ALERT_ID,
    ALERT_ID_2,
    ALERT_ID_3,
    DEVICE,
    EVENT_TS,
    RESIDENT,
    alert_body,
    check_body,
    record,
    sqs_event,
    vitals_body,
)
from message import parse_alert

POWERTOOLS_KEYS = {"level", "location", "message", "timestamp", "service"}
ALERT_KEYS = {
    "alert_id",
    "alert_type",
    "severity",
    "device_id",
    "resident_id",
    "outcome",
    "reason",
    "error",
    "dispatch_latency_ms",
    "nurses",
}
CHECK_KEYS = ALERT_KEYS | {"kind", "escalation_delay_ms"}
PORTAL = "https://d0000example.cloudfront.net"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ISO_MS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")

T0 = datetime(2026, 11, 3, 2, 0, 0, 0, tzinfo=UTC)
EVENT_TS_2 = "2026-10-18T03:20:00.000Z"
EVENT_TS_3 = "2026-10-18T03:25:00.000Z"
DELAY_S = 300
# A warning from an assigned device: the only alert that still gets a check.
WARNING = vitals_body("hr_high", "warning")
UNASSIGNED_WARNING = vitals_body("hr_high", "warning", resident_id="unassigned")
ESCALATION_LINE = "Escalated: no response within 5 minutes of the first alert."


def exact_check_body(alert_id=ALERT_ID, resident_id=RESIDENT, event_ts=EVENT_TS):
    return (
        '{"kind":"escalation_check","schema_version":1,'
        f'"alert_id":"{alert_id}","resident_id":"{resident_id}",'
        f'"event_ts":"{event_ts}"}}'
    )


# ---- helpers ----


def run(*bodies):
    records = [record(b, f"sqs-msg-{i:04d}") for i, b in enumerate(bodies, start=1)]
    return handler.lambda_handler(sqs_event(*records), None)


def key(resident_id=RESIDENT, event_ts=EVENT_TS, alert_id=ALERT_ID) -> dict:
    return {
        "pk": {"S": f"RESIDENT#{resident_id}"},
        "sk": {"S": f"ALERT#{event_ts}#{alert_id}"},
    }


def get_item(aws, **kw) -> dict | None:
    resp = aws.ddb.get_item(TableName=ALERTS_TABLE, Key=key(**kw), ConsistentRead=True)
    return handler._deserialize(resp["Item"]) if "Item" in resp else None


def put_item(aws, body: str | None = None, **attrs) -> dict:
    """Store an alert item directly, as alert-processor would have, plus `attrs`
    (an attribute set to None is removed)."""
    alert = parse_alert(body or alert_body())
    item = handler.build_item(alert, dict(alert.details), T0)
    for name, value in attrs.items():
        if value is None:
            item.pop(name, None)
        else:
            item[name] = value
    aws.ddb.put_item(TableName=ALERTS_TABLE, Item=handler._serialize(item))
    return item


def set_attr(aws, name: str, value, **kw) -> None:
    aws.ddb.update_item(
        TableName=ALERTS_TABLE,
        Key=key(**kw),
        UpdateExpression="SET #n = :v",
        ExpressionAttributeNames={"#n": name},
        ExpressionAttributeValues={":v": handler._serializer.serialize(value)},
    )


def remove_attr(aws, name: str, **kw) -> None:
    aws.ddb.update_item(
        TableName=ALERTS_TABLE,
        Key=key(**kw),
        UpdateExpression="REMOVE #n",
        ExpressionAttributeNames={"#n": name},
    )


class Spy:
    """Wraps a client: records (client name, operation, kwargs) into a shared list and
    optionally raises a ClientError on one operation."""

    def __init__(
        self,
        client,
        name,
        events,
        fail_on=None,
        code="ThrottlingException",
        fail_nth=None,
    ):
        self._client = client
        self._name = name
        self._events = events
        self._fail_on = fail_on
        self._code = code
        self._fail_nth = fail_nth
        self._count = 0

    def __getattr__(self, op):
        method = getattr(self._client, op)

        def wrapper(**kwargs):
            self._events.append((self._name, op, kwargs))
            if op == self._fail_on:
                self._count += 1
            if op == self._fail_on and self._fail_nth in (None, self._count):
                raise ClientError(
                    {"Error": {"Code": self._code, "Message": "synthetic"}}, op
                )
            return method(**kwargs)

        return wrapper


def spy(monkeypatch, client_fn: str, events: list, **kw) -> Spy:
    real = getattr(handler, client_fn)()
    wrapped = Spy(real, client_fn, events, **kw)
    monkeypatch.setattr(handler, client_fn, lambda: wrapped)
    return wrapped


def ops(events) -> list[tuple[str, str]]:
    return [(name, op) for name, op, _ in events]


def check_records(logs) -> list[dict]:
    return [r for r in logs() if r.get("kind") == "escalation_check"]


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Fixes handler's datetime.now(UTC) at T0 for every test here, so no test depends
    on the real clock (a check more than 20 min after created_at is stale). Set
    clock["now"] to move time."""
    state = {"now": T0}

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"]

    monkeypatch.setattr(handler, "datetime", FixedDatetime)
    return state


# ======== queuing the check (criteria 2-4) ========


def test_assigned_warning_queues_one_check_with_300_s_delay(aws):
    assert run(WARNING) == {"batchItemFailures": []}

    assert aws.sqs.sends == [
        {
            "QueueUrl": aws.queue_url,
            "MessageBody": exact_check_body(),
            "DelaySeconds": DELAY_S,
        }
    ]
    assert aws.nurse_publishes() == []


def test_assigned_check_is_queued_after_the_caregiver_email(aws, monkeypatch):
    events: list = []
    spy(monkeypatch, "sns_client", events)
    spy(monkeypatch, "sqs_client", events)
    spy(monkeypatch, "dynamodb_client", events)

    run(WARNING)

    assert ops(events) == [
        ("dynamodb_client", "query"),  # repeat check (not for falls)
        ("dynamodb_client", "put_item"),
        ("sns_client", "publish"),
        ("dynamodb_client", "update_item"),  # notified_at
        ("sqs_client", "send_message"),
        ("dynamodb_client", "update_item"),  # escalation_queued_at
    ]
    assert events[2][2]["TopicArn"] == aws.topic_arn
    assert "notified_at" in events[3][2]["UpdateExpression"]
    assert "escalation_queued_at" in events[5][2]["UpdateExpression"]


@pytest.mark.parametrize(
    "body",
    [alert_body(), vitals_body("hr_high", "critical"), UNASSIGNED_WARNING],
    ids=["fall", "critical-vitals", "unassigned-warning"],
)
def test_critical_or_unassigned_alert_queues_no_check(aws, body):
    assert run(body) == {"batchItemFailures": []}

    assert aws.sqs.sends == []


def test_vitals_alert_queues_check_too(aws):
    run(vitals_body("hr_high", "warning"))

    assert [s["MessageBody"] for s in aws.sqs.sends] == [exact_check_body()]


def test_escalation_queued_at_is_set_with_a_condition_after_the_send(aws, monkeypatch):
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)

    run(WARNING)

    _, update = [kw for _, op, kw in events if op == "update_item"]
    assert update["UpdateExpression"] == "SET escalation_queued_at = :now"
    assert update["ConditionExpression"] == (
        "attribute_not_exists(escalation_queued_at)"
    )
    assert update["Key"] == key()
    assert ISO_MS.match(get_item(aws)["escalation_queued_at"])


def test_escalation_queued_at_is_the_send_time(aws, clock):
    clock["now"] = T0 + timedelta(milliseconds=1234)

    run(WARNING)

    assert get_item(aws)["escalation_queued_at"] == "2026-11-03T02:00:01.234Z"


def test_suppressed_alert_queues_no_check(aws, logs):
    run(
        vitals_body("hr_high", "warning"),
        vitals_body("hr_high", "warning", alert_id=ALERT_ID_2, event_ts=EVENT_TS_2),
    )

    assert [r["outcome"] for r in logs()] == ["notified", "suppressed"]
    assert [s["MessageBody"] for s in aws.sqs.sends] == [exact_check_body()]


@pytest.mark.parametrize(
    "body",
    ["not json", None, alert_body(schema_version=2), alert_body(extra="x")],
)
def test_invalid_alert_body_queues_no_check(aws, body):
    run(body)

    assert aws.sqs.sends == []


def test_redelivered_alert_queues_no_second_check(aws):
    run(WARNING)
    run(WARNING)

    assert len(aws.sqs.sends) == 1


def test_item_with_escalation_queued_at_queues_nothing(aws):
    put_item(
        aws,
        WARNING,
        notified_at="2026-11-03T02:00:00.100Z",
        escalation_queued_at="2026-11-03T02:00:00.200Z",
    )

    assert run(WARNING) == {"batchItemFailures": []}

    assert aws.sqs.sends == []
    assert aws.sns.publishes == []


@pytest.mark.parametrize("status", ["acknowledged", "cancelled", "resolved"])
def test_redelivered_warning_whose_item_is_not_open_queues_no_check(aws, status):
    put_item(aws, WARNING, status=status, notified_at="2026-11-03T02:00:00.100Z")

    assert run(WARNING) == {"batchItemFailures": []}

    assert aws.sqs.sends == []
    assert aws.sns.publishes == []
    assert "escalation_queued_at" not in get_item(aws)


def test_failed_send_fails_the_record_and_sets_no_escalation_queued_at(
    aws, monkeypatch, logs
):
    with monkeypatch.context() as m:
        spy(m, "sqs_client", [], fail_on="send_message")
        result = run(WARNING)

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}
    item = get_item(aws)
    assert "notified_at" in item
    assert "escalation_queued_at" not in item
    (rec,) = logs()
    assert rec["outcome"] == "error"
    assert rec["error"] == "ThrottlingException"


def test_retry_after_failed_send_queues_check_without_second_caregiver_email(
    aws, monkeypatch
):
    with monkeypatch.context() as m:
        spy(m, "sqs_client", [], fail_on="send_message")
        run(WARNING)

    assert run(WARNING) == {"batchItemFailures": []}

    assert len(aws.alert_publishes()) == 1
    assert aws.nurse_publishes() == []
    assert [s["MessageBody"] for s in aws.sqs.sends] == [exact_check_body()]
    assert "escalation_queued_at" in get_item(aws)


def test_escalation_queued_at_already_set_by_another_delivery_is_not_an_error(
    aws, monkeypatch
):
    # The 2nd UpdateItem is escalation_queued_at (the 1st is notified_at).
    spy(
        monkeypatch,
        "dynamodb_client",
        [],
        fail_on="update_item",
        code="ConditionalCheckFailedException",
        fail_nth=2,
    )

    assert run(WARNING) == {"batchItemFailures": []}
    assert len(aws.sqs.sends) == 1


def test_escalation_queued_at_update_error_fails_the_record(aws, monkeypatch):
    spy(monkeypatch, "dynamodb_client", [], fail_on="update_item", fail_nth=2)

    assert run(WARNING) == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}
    assert len(aws.sqs.sends) == 1


def test_alert_log_line_has_no_check_keys(aws, logs):
    run(WARNING)

    (rec,) = logs()
    assert set(rec) - POWERTOOLS_KEYS <= ALERT_KEYS, rec
    assert rec["nurses"] == "escalation_queued"


# ======== handling the check (criteria 5-8) ========


def test_open_alert_check_emails_nurses_once(aws):
    run(WARNING)
    item = get_item(aws)

    assert run(check_body()) == {"batchItemFailures": []}

    (call,) = aws.nurse_publishes()
    assert call == {
        "TopicArn": aws.nurses_topic_arn,
        "Subject": "SilverCare ESCALATED alert: High heart rate - res-0003",
        "Message": email_text.escalation_body(item, RESIDENT, PORTAL),
    }
    assert "MessageAttributes" not in call
    assert len(aws.alert_publishes()) == 1


def test_escalated_alert_gets_escalated_at_and_stays_open(aws, clock):
    run(WARNING)
    clock["now"] = T0 + timedelta(seconds=DELAY_S, milliseconds=250)

    run(check_body())

    item = get_item(aws)
    assert item["escalated_at"] == "2026-11-03T02:05:00.250Z"
    assert item["status"] == "open"
    assert item["open_pk"] == "OPEN"


def test_escalated_at_update_is_conditional(aws, monkeypatch):
    run(WARNING)
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)

    run(check_body())

    (update,) = [kw for _, op, kw in events if op == "update_item"]
    assert update["UpdateExpression"] == "SET escalated_at = :now"
    assert update["ConditionExpression"] == "attribute_not_exists(escalated_at)"
    assert update["Key"] == key()


def test_escalated_check_log_line(aws, logs):
    run(WARNING)

    run(check_body())

    (rec,) = check_records(logs)
    assert set(rec) - POWERTOOLS_KEYS <= CHECK_KEYS, rec
    assert rec["message"] == "alert"
    assert rec["level"] == "INFO"
    assert rec["alert_id"] == ALERT_ID
    assert rec["alert_type"] == "hr_high"
    assert rec["severity"] == "warning"
    assert rec["device_id"] == DEVICE
    assert rec["resident_id"] == RESIDENT
    assert rec["kind"] == "escalation_check"
    assert rec["outcome"] == "escalated"
    assert "reason" not in rec
    assert isinstance(rec["escalation_delay_ms"], int)


@pytest.mark.parametrize("status", ["acknowledged", "cancelled", "resolved"])
def test_stopped_alert_is_not_escalated(aws, logs, status):
    run(WARNING)
    set_attr(aws, "status", status)

    assert run(check_body()) == {"batchItemFailures": []}

    assert aws.nurse_publishes() == []
    item = get_item(aws)
    assert "escalated_at" not in item
    assert item["status"] == status
    (rec,) = check_records(logs)
    assert rec["outcome"] == "not_escalated"
    assert rec["reason"] == f"status:{status}"
    assert "escalation_delay_ms" not in rec


@pytest.mark.parametrize("status", ["Test Resident A said hr 187", "OPEN", "", 1])
def test_unknown_present_status_is_not_escalated_and_logged_as_other(aws, logs, status):
    run(WARNING)
    set_attr(aws, "status", status)

    run(check_body())

    assert aws.nurse_publishes() == []
    (rec,) = check_records(logs)
    assert rec["outcome"] == "not_escalated"
    assert rec["reason"] == "status:other"
    text = " ".join(
        str(v) for k, v in rec.items() if k not in {"timestamp", "location"}
    )
    assert "Test Resident" not in text
    assert "187" not in text


def test_missing_status_escalates_as_a_fail_safe(aws, logs):
    run(WARNING)
    remove_attr(aws, "status")

    assert run(check_body()) == {"batchItemFailures": []}

    assert len(aws.nurse_publishes()) == 1
    assert "escalated_at" in get_item(aws)
    (rec,) = check_records(logs)
    assert rec["outcome"] == "escalated"


def test_already_escalated_alert_is_a_duplicate(aws, logs):
    run(WARNING)
    set_attr(aws, "escalated_at", "2026-11-03T02:01:00.000Z")

    assert run(check_body()) == {"batchItemFailures": []}

    assert aws.nurse_publishes() == []
    assert get_item(aws)["escalated_at"] == "2026-11-03T02:01:00.000Z"
    (rec,) = check_records(logs)
    assert rec["outcome"] == "duplicate"
    assert "reason" not in rec


def test_second_check_for_the_same_alert_sends_no_second_nurse_email(aws, logs):
    run(WARNING)

    run(check_body())
    run(check_body())

    assert len(aws.nurse_publishes()) == 1
    assert [r["outcome"] for r in check_records(logs)] == ["escalated", "duplicate"]


def test_condition_failure_after_publish_is_a_duplicate_not_a_failure(
    aws, monkeypatch, logs
):
    run(WARNING)
    spy(
        monkeypatch,
        "dynamodb_client",
        [],
        fail_on="update_item",
        code="ConditionalCheckFailedException",
    )

    assert run(check_body()) == {"batchItemFailures": []}

    assert len(aws.nurse_publishes()) == 1
    (rec,) = check_records(logs)
    assert rec["outcome"] == "duplicate"
    assert rec["level"] == "INFO"


def test_check_for_a_missing_item_fails_the_record(aws, logs):
    assert run(check_body()) == {
        "batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]
    }

    assert aws.sns.publishes == []
    (rec,) = logs()
    assert set(rec) - POWERTOOLS_KEYS <= CHECK_KEYS, rec
    assert rec["outcome"] == "error"
    assert rec["error"] == "RuntimeError"
    assert rec["kind"] == "escalation_check"
    assert rec["alert_id"] == ALERT_ID
    assert rec["resident_id"] == RESIDENT


@pytest.mark.parametrize(
    ("client_fn", "operation"),
    [
        ("dynamodb_client", "get_item"),
        ("sns_client", "publish"),
        ("dynamodb_client", "update_item"),
    ],
)
def test_aws_error_while_handling_a_check_fails_the_record(
    aws, monkeypatch, logs, client_fn, operation
):
    run(WARNING)
    spy(monkeypatch, client_fn, [], fail_on=operation)

    assert run(check_body()) == {
        "batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]
    }

    (rec,) = check_records(logs)
    assert set(rec) - POWERTOOLS_KEYS <= CHECK_KEYS, rec
    assert rec["outcome"] == "error"
    assert rec["error"] == "ThrottlingException"
    assert rec["level"] == "ERROR"


def test_retry_after_nurse_publish_failure_escalates_once(aws, monkeypatch):
    run(WARNING)
    with monkeypatch.context() as m:
        spy(m, "sns_client", [], fail_on="publish")
        run(check_body())
    assert "escalated_at" not in get_item(aws)

    assert run(check_body()) == {"batchItemFailures": []}

    assert len(aws.nurse_publishes()) == 1
    assert "escalated_at" in get_item(aws)


def test_check_makes_no_repeat_query_and_reads_the_item_consistently(aws, monkeypatch):
    run(vitals_body("hr_high", "warning"))
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)

    run(check_body())

    assert ops(events) == [
        ("dynamodb_client", "get_item"),
        ("dynamodb_client", "update_item"),
    ]
    get = events[0][2]
    assert get == {"TableName": ALERTS_TABLE, "Key": key(), "ConsistentRead": True}


def test_handling_a_check_queues_no_further_check(aws):
    run(WARNING)
    assert len(aws.sqs.sends) == 1

    run(check_body())

    assert len(aws.sqs.sends) == 1


def test_failing_check_in_a_batch_fails_only_that_record(aws):
    run(WARNING)
    # Another type, so it isn't held back as a repeat.
    run(vitals_body("spo2_low", "warning", alert_id=ALERT_ID_3, event_ts=EVENT_TS_3))

    result = run(
        check_body(),
        check_body(alert_id=ALERT_ID_2, event_ts=EVENT_TS_2),  # no item
        check_body(alert_id=ALERT_ID_3, event_ts=EVENT_TS_3),
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0002"}]}
    assert len(aws.nurse_publishes()) == 2
    assert "escalated_at" in get_item(aws)
    assert "escalated_at" in get_item(aws, event_ts=EVENT_TS_3, alert_id=ALERT_ID_3)


def test_invalid_check_in_a_batch_fails_only_that_record(aws, logs):
    run(WARNING)

    result = run(check_body(kind="reminder"), check_body())

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}
    assert len(aws.nurse_publishes()) == 1
    rejected = [r for r in logs() if r["outcome"] == "invalid"]
    assert [(r["reason"], r["kind"]) for r in rejected] == [("invalid:kind", "invalid")]


@pytest.mark.parametrize(
    "body",
    [
        check_body(kind="reminder"),
        check_body(schema_version=2),
        check_body(alert_id="not-a-uuid"),
        check_body(extra="x"),
    ],
)
def test_invalid_check_is_reported_as_failure_and_sends_nothing(aws, logs, body):
    run(WARNING)

    assert run(body) == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}

    assert aws.nurse_publishes() == []
    assert "escalated_at" not in get_item(aws)
    rec = logs()[-1]
    assert rec["outcome"] == "invalid"
    assert rec["level"] == "WARNING"


# ---- nurse email through the handler (criteria 9-10) ----


def test_warning_check_email_has_escalated_subject_and_5_minute_line(aws):
    run(WARNING)

    run(check_body())

    (call,) = aws.nurse_publishes()
    assert call["Subject"] == "SilverCare ESCALATED alert: High heart rate - res-0003"
    assert len(call["Subject"]) <= 100
    assert "Severity: Warning" in call["Message"]
    assert ESCALATION_LINE in call["Message"]
    assert "60 s" not in call["Message"]


@pytest.mark.parametrize("severity", ["warning", "critical"])
def test_check_of_item_stored_before_routing_escalates_for_both_severities(
    aws, severity
):
    """A critical item without escalated_at (stored before alert-routing) is still
    escalated by a check; check handling is unchanged (criterion 4)."""
    put_item(aws, vitals_body("hr_high", severity))

    run(check_body())

    (call,) = aws.nurse_publishes()
    assert call["Subject"] == "SilverCare ESCALATED alert: High heart rate - res-0003"
    assert f"Severity: {severity.capitalize()}" in call["Message"]
    assert ESCALATION_LINE in call["Message"]
    assert "Sent to the care centre at once" not in call["Message"]


def test_unassigned_check_emails_nurses_with_device_line(aws):
    # Stored before alert-routing: no escalated_at, so a check still escalates.
    put_item(aws, alert_body(resident_id="unassigned"))
    item = get_item(aws, resident_id="unassigned")

    assert run(check_body(resident_id="unassigned")) == {"batchItemFailures": []}

    (call,) = aws.nurse_publishes()
    assert call["Subject"] == "SilverCare ESCALATED alert: Fall - unassigned"
    assert call["Message"] == email_text.escalation_body(item, "unassigned", PORTAL)
    assert "Resident: unassigned\nDevice: wearable-0007\n" in call["Message"]
    assert "escalated_at" in get_item(aws, resident_id="unassigned")


def test_caregiver_email_is_unchanged_by_escalation(aws):
    run(alert_body())

    (call,) = aws.alert_publishes()
    assert "Escalated" not in call["Message"]
    assert "Device:" not in call["Message"]
    assert call["Subject"] == "SilverCare CRITICAL alert: Fall - res-0003"


# ---- escalation_delay_ms (criterion 7) ----


def test_escalation_delay_is_from_notified_at(aws, logs, clock):
    run(WARNING)  # notified_at = T0
    assert get_item(aws)["notified_at"] == "2026-11-03T02:00:00.000Z"
    clock["now"] = T0 + timedelta(seconds=DELAY_S, milliseconds=250)

    run(check_body())

    (rec,) = check_records(logs)
    assert rec["escalation_delay_ms"] == 300250


def test_escalation_delay_for_unassigned_is_from_created_at(aws, logs, clock):
    put_item(aws, UNASSIGNED_WARNING)  # created_at = T0, stored before routing
    clock["now"] = T0 + timedelta(seconds=61, milliseconds=5)

    run(check_body(resident_id="unassigned"))

    (rec,) = check_records(logs)
    assert rec["escalation_delay_ms"] == 61005


def test_escalation_delay_pure():
    published = T0 + timedelta(seconds=60)

    assert (
        handler.escalation_delay_ms(
            {"notified_at": "2026-11-03T02:00:00.500Z", "created_at": "x"}, published
        )
        == 59500
    )
    assert (
        handler.escalation_delay_ms(
            {"created_at": "2026-11-03T01:59:59.000Z"}, published
        )
        == 61000
    )
    assert (
        handler.escalation_delay_ms(
            {"notified_at": "bad", "created_at": "2026-11-03T02:00:00.000Z"},
            published,
        )
        == 60000
    )
    assert handler.escalation_delay_ms({}, published) is None


def test_check_log_has_no_delay_when_item_has_no_valid_timestamps(aws, logs):
    put_item(aws, UNASSIGNED_WARNING, created_at="not a time")

    run(check_body(resident_id="unassigned"))

    (rec,) = check_records(logs)
    assert rec["outcome"] == "escalated"
    assert "escalation_delay_ms" not in rec


# ---- pure decision logic (criterion 5) ----


@pytest.mark.parametrize(
    ("item", "decision"),
    [
        ({"status": "open"}, None),
        ({}, None),
        ({"status": "acknowledged"}, ("not_escalated", "status:acknowledged")),
        ({"status": "cancelled"}, ("not_escalated", "status:cancelled")),
        ({"status": "resolved"}, ("not_escalated", "status:resolved")),
        ({"status": "closed"}, ("not_escalated", "status:other")),
        ({"status": "open", "escalated_at": "x"}, ("duplicate", None)),
        (
            {"status": "resolved", "escalated_at": "x"},
            ("not_escalated", "status:resolved"),
        ),
    ],
)
def test_escalation_decision(item, decision):
    assert handler.escalation_decision(item) == decision


@pytest.mark.parametrize(
    ("item", "needed"),
    [
        ({"status": "open"}, True),
        ({"status": "open", "escalation_queued_at": "x"}, False),
        ({"status": "acknowledged"}, False),
        ({}, False),
    ],
)
def test_needs_escalation_check(item, needed):
    assert handler.needs_escalation_check(item) is needed


# ---- logging (criterion 7) ----


def test_check_log_masks_hand_edited_item_fields(aws, logs):
    run(WARNING)
    set_attr(aws, "device_id", "Test Resident A")
    set_attr(aws, "severity", "very bad hr 187")

    run(check_body())

    (rec,) = check_records(logs)
    assert rec["device_id"] == "invalid"
    assert rec["severity"] == "invalid"


def test_logs_hold_no_values_details_emails_or_body(aws, logs):
    details = {
        "metric": "hr_bpm",
        "value": 187,
        "limit": 163,
        "direction": "above",
        "previous_value": 179,
    }
    run(vitals_body("hr_high", "warning", details=details))
    run(vitals_body("hr_high", "warning", details=details))  # duplicate
    run(check_body())
    run(check_body())  # duplicate
    set_attr(aws, "status", "acknowledged")
    run(check_body())  # not_escalated

    records = logs()
    assert [r["outcome"] for r in records] == [
        "notified",
        "duplicate",
        "escalated",
        "duplicate",
        "not_escalated",
    ]
    for rec in records:
        allowed = CHECK_KEYS if rec.get("kind") else ALERT_KEYS
        assert set(rec) - POWERTOOLS_KEYS <= allowed, rec
        # The real IDs are there before random-looking UUIDs are blanked out.
        assert rec["alert_id"] == ALERT_ID
        assert rec["resident_id"] == RESIDENT
        assert rec["device_id"] == DEVICE

    noisy = {"timestamp", "location", "dispatch_latency_ms", "escalation_delay_ms"}
    text = " ".join(f"{k}={v}" for r in records for k, v in r.items() if k not in noisy)
    text = UUID.sub("<uuid>", text)
    for hidden in (
        "187",
        "163",
        "179",
        "details",
        "previous",
        "limit",
        "SilverCare",
        "Escalated",
        "Reading",
        "heart rate",
        "Subject",
        "@",
        "portal",
        "cloudfront",
    ):
        assert hidden not in text, hidden


# ======== stale checks (decisions table, criterion 5) ========

STALE_AFTER = timedelta(minutes=20)
CREATED = "2026-11-03T02:00:00.000Z"  # T0


@pytest.mark.parametrize(
    ("after", "decision"),
    [
        (STALE_AFTER - timedelta(milliseconds=1), None),
        (STALE_AFTER, None),
        (STALE_AFTER + timedelta(milliseconds=1), ("not_escalated", "stale")),
        (timedelta(days=3), ("not_escalated", "stale")),
    ],
    ids=["just-below", "exactly-20-min", "just-above", "days-later"],
)
def test_stale_boundary(after, decision):
    item = {"status": "open", "created_at": CREATED}

    assert handler.escalation_decision(item, T0 + after) == decision


@pytest.mark.parametrize(
    "created_at", [None, "not a time", "2026-11-03T02:00:00Z", 1793686998, ""]
)
def test_missing_or_invalid_created_at_is_not_stale(created_at):
    item = {"status": "open"}
    if created_at is not None:
        item["created_at"] = created_at

    assert handler.escalation_decision(item, T0 + timedelta(days=30)) is None


@pytest.mark.parametrize(
    ("item", "decision"),
    [
        (
            {"status": "acknowledged", "created_at": CREATED},
            ("not_escalated", "status:acknowledged"),
        ),
        (
            {"status": "cancelled", "created_at": CREATED},
            ("not_escalated", "status:cancelled"),
        ),
        (
            {"status": "open", "escalated_at": "x", "created_at": CREATED},
            ("duplicate", None),
        ),
        ({"created_at": CREATED}, ("not_escalated", "stale")),
    ],
    ids=["acknowledged", "cancelled", "escalated", "missing-status"],
)
def test_status_then_escalated_at_then_stale(item, decision):
    assert handler.escalation_decision(item, T0 + timedelta(hours=1)) == decision


def test_escalation_decision_now_defaults_to_the_current_time(clock):
    item = {"status": "open", "created_at": CREATED}

    clock["now"] = T0 + STALE_AFTER + timedelta(milliseconds=1)
    assert handler.escalation_decision(item) == ("not_escalated", "stale")
    clock["now"] = T0 + STALE_AFTER
    assert handler.escalation_decision(item) is None


@pytest.mark.parametrize("resident_id", [RESIDENT, "unassigned"])
def test_stale_check_emails_nobody(aws, logs, clock, resident_id):
    put_item(aws, vitals_body(resident_id=resident_id))  # created_at = T0
    clock["now"] = T0 + STALE_AFTER + timedelta(milliseconds=1)

    assert run(check_body(resident_id=resident_id)) == {"batchItemFailures": []}

    assert aws.nurse_publishes() == []
    item = get_item(aws, resident_id=resident_id)
    assert "escalated_at" not in item
    assert item["status"] == "open"
    (rec,) = check_records(logs)
    assert set(rec) - POWERTOOLS_KEYS <= CHECK_KEYS, rec
    assert rec["outcome"] == "not_escalated"
    assert rec["reason"] == "stale"
    assert "escalation_delay_ms" not in rec


def test_check_exactly_20_minutes_after_created_at_escalates(aws, logs, clock):
    run(WARNING)
    clock["now"] = T0 + STALE_AFTER

    run(check_body())

    assert len(aws.nurse_publishes()) == 1
    (rec,) = check_records(logs)
    assert rec["outcome"] == "escalated"
    assert rec["escalation_delay_ms"] == 1_200_000


def test_stale_after_is_20_minutes():
    assert handler.STALE_AFTER == timedelta(minutes=20)


def test_check_on_its_5th_try_about_17_minutes_after_created_at_still_emails(
    aws, logs, clock
):
    """A check first arrives 300 s after the alert and is retried every 180 s, so its
    5th try is at about 17 minutes: still within the 20-minute limit."""
    run(WARNING)  # created_at = notified_at = T0
    clock["now"] = T0 + timedelta(seconds=DELAY_S + 4 * 180)

    assert run(check_body()) == {"batchItemFailures": []}

    assert len(aws.nurse_publishes()) == 1
    (rec,) = check_records(logs)
    assert rec["outcome"] == "escalated"
    assert rec["escalation_delay_ms"] == 1_020_000


def test_check_15_minutes_and_1_ms_after_created_at_is_no_longer_stale(
    aws, logs, clock
):
    """The old 15-minute limit no longer applies."""
    run(WARNING)
    clock["now"] = T0 + timedelta(minutes=15, milliseconds=1)

    run(check_body())

    assert len(aws.nurse_publishes()) == 1
    (rec,) = check_records(logs)
    assert rec["outcome"] == "escalated"


@pytest.mark.parametrize("bad", ["not a time", None])
def test_check_with_invalid_or_missing_created_at_escalates(aws, logs, clock, bad):
    run(WARNING)
    if bad is None:
        remove_attr(aws, "created_at")
    else:
        set_attr(aws, "created_at", bad)
    clock["now"] = T0 + timedelta(days=2)

    assert run(check_body()) == {"batchItemFailures": []}

    assert len(aws.nurse_publishes()) == 1
    (rec,) = check_records(logs)
    assert rec["outcome"] == "escalated"


def test_stale_acknowledged_alert_logs_the_status_reason(aws, logs, clock):
    run(WARNING)
    set_attr(aws, "status", "acknowledged")
    clock["now"] = T0 + timedelta(hours=1)

    run(check_body())

    (rec,) = check_records(logs)
    assert rec["reason"] == "status:acknowledged"


def test_stale_escalated_alert_is_a_duplicate(aws, logs, clock):
    run(WARNING)
    set_attr(aws, "escalated_at", "2026-11-03T02:01:00.000Z")
    clock["now"] = T0 + timedelta(hours=1)

    run(check_body())

    (rec,) = check_records(logs)
    assert rec["outcome"] == "duplicate"
    assert "reason" not in rec


# ======== hand-edited items still escalate (decisions table, criterion 10) ========


@pytest.mark.parametrize("alert_type", [None, "hr_weird", "Test Resident A", 7])
def test_missing_or_unknown_alert_type_escalates_with_label_alert(
    aws, logs, alert_type
):
    run(vitals_body("hr_high", "warning"))
    if alert_type is None:
        remove_attr(aws, "alert_type")
    else:
        set_attr(aws, "alert_type", alert_type)

    assert run(check_body()) == {"batchItemFailures": []}

    (call,) = aws.nurse_publishes()
    assert call["Subject"] == "SilverCare ESCALATED alert: Alert - res-0003"
    assert "\nType: Alert\n" in call["Message"]
    assert "Test Resident" not in call["Subject"] + call["Message"]
    assert "escalated_at" in get_item(aws)
    (rec,) = check_records(logs)
    assert rec["outcome"] == "escalated"
    assert rec["alert_type"] == "invalid"


@pytest.mark.parametrize("details", ["free text", ["x"], None, 5])
@pytest.mark.parametrize("make", [alert_body, vitals_body], ids=["fall", "vitals"])
def test_non_dict_details_still_escalates(aws, logs, details, make):
    put_item(aws, make())  # a fall: stored before routing, without escalated_at
    if details is None:
        remove_attr(aws, "details")
    else:
        set_attr(aws, "details", details)

    assert run(check_body()) == {"batchItemFailures": []}

    assert len(aws.nurse_publishes()) == 1
    assert check_records(logs)[0]["outcome"] == "escalated"


@pytest.mark.parametrize("device_id", ["Test Resident A", "wearable-07", 7, None])
def test_unassigned_hand_edited_device_id_prints_device_invalid(aws, device_id):
    put_item(aws, UNASSIGNED_WARNING)  # stored before routing, no escalated_at
    if device_id is None:
        remove_attr(aws, "device_id", resident_id="unassigned")
    else:
        set_attr(aws, "device_id", device_id, resident_id="unassigned")

    assert run(check_body(resident_id="unassigned")) == {"batchItemFailures": []}

    (call,) = aws.nurse_publishes()
    assert "Resident: unassigned\nDevice: invalid\n" in call["Message"]
    assert "Test Resident" not in call["Message"]
