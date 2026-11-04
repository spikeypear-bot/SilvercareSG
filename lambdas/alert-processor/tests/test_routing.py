"""Routing a new alert to caregivers and nurses end to end against moto
(docs/features/alert-routing.md, criteria 1-3, 5-8 and 19): who is emailed at once,
which alerts get the 5-minute escalation check, redelivery, a failed nurse Publish,
and the `nurses` log field."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import email_text
import handler
import pytest
from botocore.exceptions import ClientError
from conftest import ALERTS_TABLE
from events import (
    ALERT_ID,
    DEVICE,
    EVENT_TS,
    NO_PULSE_DETAILS,
    RESIDENT,
    alert_body,
    record,
    sqs_event,
    vitals_body,
)
from message import parse_alert

T0 = datetime(2026, 11, 3, 2, 0, 0, 0, tzinfo=UTC)
T0_ISO = "2026-11-03T02:00:00.000Z"
PORTAL = "https://d0000example.cloudfront.net"
NOTIFIED_AT = "2026-11-03T01:59:59.000Z"
ESCALATED_AT = "2026-11-03T01:59:59.500Z"
AT_ONCE_CRITICAL = "Sent to the care centre at once: critical alert."
AT_ONCE_UNASSIGNED = (
    "Sent to the care centre at once: device not assigned to a resident."
)
NURSE_ALERTED = "The care centre nurse has also been alerted."

FALL = alert_body()
CRITICAL_VITALS = vitals_body("hr_high", "critical")
NO_PULSE = vitals_body("hr_low", "critical", details=NO_PULSE_DETAILS)
WARNING = vitals_body("hr_high", "warning")
CRITICAL_BODIES = [FALL, CRITICAL_VITALS, NO_PULSE]
CRITICAL_IDS = ["fall", "critical-vitals", "no-pulse"]


# ---- helpers ----


def run(*bodies):
    records = [record(b, f"sqs-msg-{i:04d}") for i, b in enumerate(bodies, start=1)]
    return handler.lambda_handler(sqs_event(*records), None)


def key(resident_id=RESIDENT) -> dict:
    return {
        "pk": {"S": f"RESIDENT#{resident_id}"},
        "sk": {"S": f"ALERT#{EVENT_TS}#{ALERT_ID}"},
    }


def get_item(aws, resident_id=RESIDENT) -> dict:
    resp = aws.ddb.get_item(
        TableName=ALERTS_TABLE, Key=key(resident_id), ConsistentRead=True
    )
    return handler._deserialize(resp["Item"])


def put_item(aws, body: str, **attrs) -> dict:
    """Store the alert's item as alert-processor would have, plus `attrs` (None
    removes an attribute)."""
    alert = parse_alert(body)
    item = handler.build_item(alert, dict(alert.details), T0)
    for name, value in attrs.items():
        if value is None:
            item.pop(name, None)
        else:
            item[name] = value
    aws.ddb.put_item(TableName=ALERTS_TABLE, Item=handler._serialize(item))
    return item


def alert_records(logs) -> list[dict]:
    return [r for r in logs() if "kind" not in r]


class Spy:
    """Records (client, op, kwargs); raises a ClientError on calls `fail` matches."""

    def __init__(self, client, name, events, fail=None, code="ThrottlingException"):
        self._client = client
        self._name = name
        self._events = events
        self._fail = fail
        self._code = code

    def __getattr__(self, op):
        method = getattr(self._client, op)

        def wrapper(**kwargs):
            self._events.append((self._name, op, kwargs))
            if self._fail is not None and self._fail(op, kwargs):
                raise ClientError(
                    {"Error": {"Code": self._code, "Message": "synthetic"}}, op
                )
            return method(**kwargs)

        return wrapper


def spy(monkeypatch, client_fn, events, **kw) -> Spy:
    wrapped = Spy(getattr(handler, client_fn)(), client_fn, events, **kw)
    monkeypatch.setattr(handler, client_fn, lambda: wrapped)
    return wrapped


def nurse_publish_fails(aws):
    return lambda op, kw: op == "publish" and kw["TopicArn"] == aws.nurses_topic_arn


def escalated_at_update_fails(op, kw):
    return op == "update_item" and "escalated_at" in kw["UpdateExpression"]


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Fixes handler's datetime.now(UTC) at T0. Set clock["now"] to move time."""
    state = {"now": T0}

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"]

    monkeypatch.setattr(handler, "datetime", FixedDatetime)
    return state


# ======== routing table (criterion 1) ========


@pytest.mark.parametrize("body", CRITICAL_BODIES, ids=CRITICAL_IDS)
def test_assigned_critical_alert_emails_caregivers_and_nurses_at_once(aws, body):
    assert run(body) == {"batchItemFailures": []}

    assert len(aws.alert_publishes()) == 1
    assert len(aws.nurse_publishes()) == 1
    assert aws.sqs.sends == []


@pytest.mark.parametrize("body", CRITICAL_BODIES, ids=CRITICAL_IDS)
def test_assigned_critical_alert_sets_both_timestamps_and_no_check(aws, body):
    run(body)

    item = get_item(aws)
    assert item["notified_at"] == T0_ISO
    assert item["escalated_at"] == T0_ISO
    assert "escalation_queued_at" not in item
    assert item["status"] == "open"
    assert item["open_pk"] == "OPEN"


def test_assigned_critical_nurse_publish_is_exact(aws):
    run(FALL)
    item = get_item(aws)

    (call,) = aws.nurse_publishes()
    assert call == {
        "TopicArn": aws.nurses_topic_arn,
        "Subject": "SilverCare CRITICAL alert: Fall - res-0003",
        "Message": email_text.nurse_body(item, RESIDENT, PORTAL),
    }
    assert AT_ONCE_CRITICAL in call["Message"]


def test_nurse_subject_is_the_caregiver_subject(aws):
    run(CRITICAL_VITALS)

    (caregiver,) = aws.alert_publishes()
    (nurse,) = aws.nurse_publishes()
    assert nurse["Subject"] == caregiver["Subject"]
    assert nurse["Subject"] == "SilverCare CRITICAL alert: High heart rate - res-0003"


def test_assigned_critical_order_is_caregiver_then_nurse(aws, monkeypatch):
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)
    spy(monkeypatch, "sns_client", events)
    spy(monkeypatch, "sqs_client", events)

    run(FALL)

    assert [(n, op) for n, op, _ in events] == [
        ("dynamodb_client", "get_item"),  # latest vitals (fall)
        ("dynamodb_client", "put_item"),
        ("sns_client", "publish"),  # caregivers
        ("dynamodb_client", "update_item"),  # notified_at
        ("sns_client", "publish"),  # nurses
        ("dynamodb_client", "update_item"),  # escalated_at
    ]
    assert events[2][2]["TopicArn"] == aws.topic_arn
    assert events[4][2]["TopicArn"] == aws.nurses_topic_arn
    update = events[5][2]
    assert update["UpdateExpression"] == "SET escalated_at = :now"
    assert update["ConditionExpression"] == "attribute_not_exists(escalated_at)"
    assert update["Key"] == key()


def test_assigned_warning_emails_caregivers_and_queues_a_300_s_check(aws):
    assert run(WARNING) == {"batchItemFailures": []}

    assert len(aws.alert_publishes()) == 1
    assert aws.nurse_publishes() == []
    (send,) = aws.sqs.sends
    assert send["DelaySeconds"] == 300
    item = get_item(aws)
    assert "escalation_queued_at" in item
    assert "escalated_at" not in item


@pytest.mark.parametrize(
    "body",
    [
        vitals_body("hr_high", "warning", resident_id="unassigned"),
        vitals_body("hr_high", "critical", resident_id="unassigned"),
        alert_body(resident_id="unassigned"),
    ],
    ids=["warning", "critical-vitals", "fall"],
)
def test_unassigned_alert_emails_only_nurses_at_once_without_check(aws, logs, body):
    assert run(body) == {"batchItemFailures": []}

    assert aws.alert_publishes() == []
    (nurse,) = aws.nurse_publishes()
    assert "MessageAttributes" not in nurse
    assert aws.sqs.sends == []
    item = get_item(aws, "unassigned")
    assert item["escalated_at"] == T0_ISO
    assert item["notification"] == "skipped_unassigned"
    assert "notified_at" not in item
    assert "escalation_queued_at" not in item
    (rec,) = alert_records(logs)
    assert rec["outcome"] == "skipped_unassigned"
    assert rec["nurses"] == "immediate"


@pytest.mark.parametrize("severity", ["warning", "critical"])
def test_unassigned_nurse_email_has_the_unassigned_line_and_device(aws, severity):
    run(vitals_body("hr_high", severity, resident_id="unassigned"))

    (nurse,) = aws.nurse_publishes()
    lines = nurse["Message"].splitlines()
    i = lines.index(f"Severity: {severity.capitalize()}")
    assert lines[i + 1 : i + 4] == [
        AT_ONCE_UNASSIGNED,
        "Resident: unassigned",
        f"Device: {DEVICE}",
    ]
    assert AT_ONCE_CRITICAL not in nurse["Message"]
    assert NURSE_ALERTED not in nurse["Message"]
    assert nurse["Subject"].endswith(" - unassigned")


def test_unassigned_order_is_store_then_nurse_publish_then_escalated_at(
    aws, monkeypatch
):
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)
    spy(monkeypatch, "sns_client", events)

    run(vitals_body("hr_high", "warning", resident_id="unassigned"))

    assert [(n, op) for n, op, _ in events] == [
        ("dynamodb_client", "query"),  # repeat check
        ("dynamodb_client", "put_item"),
        ("sns_client", "publish"),
        ("dynamodb_client", "update_item"),
    ]
    assert events[2][2]["TopicArn"] == aws.nurses_topic_arn
    assert events[3][2]["UpdateExpression"] == "SET escalated_at = :now"


# ======== missing severity counts as critical (criterion 1) ========


def test_stored_item_without_severity_is_routed_as_critical(aws):
    # A fall stored before vitals alerts: no severity, not yet emailed.
    put_item(aws, FALL, severity=None)

    assert run(FALL) == {"batchItemFailures": []}

    assert len(aws.alert_publishes()) == 1
    (nurse,) = aws.nurse_publishes()
    assert nurse["Subject"] == "SilverCare CRITICAL alert: Fall - res-0003"
    assert AT_ONCE_CRITICAL in nurse["Message"]
    assert "Severity: Critical" in nurse["Message"]
    assert aws.sqs.sends == []
    assert "escalated_at" in get_item(aws)


@pytest.mark.parametrize(
    ("resident_id", "item", "at_once"),
    [
        (RESIDENT, {"severity": "critical"}, True),
        (RESIDENT, {"severity": "warning"}, False),
        (RESIDENT, {}, True),
        (RESIDENT, {"severity": None}, True),
        (RESIDENT, {"severity": ""}, True),
        ("unassigned", {"severity": "warning"}, True),
        ("unassigned", {"severity": "critical"}, True),
        ("unassigned", {}, True),
    ],
)
def test_nurses_at_once(resident_id, item, at_once):
    assert handler.nurses_at_once(resident_id, item) is at_once


# ======== redelivery (criterion 2) ========


@pytest.mark.parametrize("body", CRITICAL_BODIES, ids=CRITICAL_IDS)
def test_redelivery_with_notified_at_only_sends_only_the_nurse_email(aws, logs, body):
    put_item(aws, body, notified_at=NOTIFIED_AT)

    assert run(body) == {"batchItemFailures": []}

    assert aws.alert_publishes() == []
    assert len(aws.nurse_publishes()) == 1
    item = get_item(aws)
    assert item["notified_at"] == NOTIFIED_AT
    assert item["escalated_at"] == T0_ISO
    (rec,) = alert_records(logs)
    assert rec["outcome"] == "duplicate"
    assert rec["nurses"] == "immediate"


@pytest.mark.parametrize("body", CRITICAL_BODIES, ids=CRITICAL_IDS)
def test_redelivery_with_both_timestamps_sends_nothing(aws, logs, body):
    put_item(aws, body, notified_at=NOTIFIED_AT, escalated_at=ESCALATED_AT)

    assert run(body) == {"batchItemFailures": []}

    assert aws.sns.publishes == []
    assert aws.sqs.sends == []
    assert get_item(aws)["escalated_at"] == ESCALATED_AT
    (rec,) = alert_records(logs)
    assert rec["outcome"] == "duplicate"


@pytest.mark.parametrize("status", ["acknowledged", "cancelled", "resolved"])
@pytest.mark.parametrize("body", CRITICAL_BODIES, ids=CRITICAL_IDS)
def test_redelivery_of_a_non_open_item_without_escalated_at_emails_no_nurse(
    aws, logs, body, status
):
    put_item(aws, body, notified_at=NOTIFIED_AT, status=status, open_pk=None)

    assert run(body) == {"batchItemFailures": []}

    assert aws.sns.publishes == []
    item = get_item(aws)
    assert "escalated_at" not in item
    assert item["status"] == status
    assert aws.sqs.sends == []
    (rec,) = alert_records(logs)
    assert rec["outcome"] == "duplicate"


@pytest.mark.parametrize("status", ["acknowledged", "cancelled", "resolved"])
def test_redelivery_of_a_non_open_unassigned_item_emails_no_nurse(aws, status):
    body = alert_body(resident_id="unassigned")
    put_item(aws, body, status=status, open_pk=None)

    assert run(body) == {"batchItemFailures": []}

    assert aws.sns.publishes == []
    assert "escalated_at" not in get_item(aws, "unassigned")


def test_redelivery_of_an_item_without_status_still_emails_the_nurses(aws):
    """A missing status counts as open (fail-safe, as for escalation checks)."""
    put_item(aws, FALL, notified_at=NOTIFIED_AT, status=None)

    assert run(FALL) == {"batchItemFailures": []}

    assert aws.alert_publishes() == []
    assert len(aws.nurse_publishes()) == 1
    assert get_item(aws)["escalated_at"] == T0_ISO


@pytest.mark.parametrize(
    ("item", "sends"),
    [
        ({"status": "open"}, True),
        ({}, True),
        ({"status": "acknowledged"}, False),
        ({"status": "cancelled"}, False),
        ({"status": "resolved"}, False),
        ({"status": "open", "escalated_at": ESCALATED_AT}, False),
    ],
)
def test_notify_nurses_sends_only_for_an_open_unescalated_item(aws, item, sends):
    alert = parse_alert(FALL)
    stored = {**handler.build_item(alert, dict(alert.details), T0), **item}
    if "status" not in item:
        del stored["status"]
    aws.ddb.put_item(TableName=ALERTS_TABLE, Item=handler._serialize(stored))

    handler.notify_nurses(alert, stored)

    assert len(aws.nurse_publishes()) == (1 if sends else 0)


def test_redelivered_critical_alert_sends_one_email_each_in_total(aws):
    run(FALL)
    run(FALL)
    run(FALL)

    assert len(aws.alert_publishes()) == 1
    assert len(aws.nurse_publishes()) == 1


def test_redelivered_unassigned_alert_without_escalated_at_emails_nurses(aws):
    body = alert_body(resident_id="unassigned")
    put_item(aws, body)

    run(body)

    assert aws.alert_publishes() == []
    assert len(aws.nurse_publishes()) == 1


def test_redelivered_unassigned_alert_with_escalated_at_sends_nothing(aws):
    body = alert_body(resident_id="unassigned")
    put_item(aws, body, escalated_at=ESCALATED_AT)

    run(body)

    assert aws.sns.publishes == []


# ======== failed nurse Publish (criterion 3) ========


def test_failed_nurse_publish_fails_the_record_after_the_caregiver_email(
    aws, monkeypatch, logs
):
    with monkeypatch.context() as m:
        spy(m, "sns_client", [], fail=nurse_publish_fails(aws))
        result = run(FALL)

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}
    assert len(aws.alert_publishes()) == 1
    item = get_item(aws)
    assert "notified_at" in item
    assert "escalated_at" not in item
    assert "escalation_queued_at" not in item
    assert aws.sqs.sends == []
    (rec,) = alert_records(logs)
    assert rec["outcome"] == "error"
    assert rec["error"] == "ThrottlingException"
    assert rec["level"] == "ERROR"


def test_retry_after_failed_nurse_publish_sends_no_second_caregiver_email(
    aws, monkeypatch
):
    with monkeypatch.context() as m:
        spy(m, "sns_client", [], fail=nurse_publish_fails(aws))
        run(FALL)

    assert run(FALL) == {"batchItemFailures": []}

    # The failed Publish raised before reaching SNS: one email each in total.
    assert len(aws.alert_publishes()) == 1
    assert len(aws.nurse_publishes()) == 1
    assert "escalated_at" in get_item(aws)
    assert aws.sqs.sends == []


def test_failed_unassigned_nurse_publish_fails_the_record_and_retry_sends_it(
    aws, monkeypatch
):
    body = vitals_body("hr_high", "warning", resident_id="unassigned")
    with monkeypatch.context() as m:
        spy(m, "sns_client", [], fail=nurse_publish_fails(aws))
        assert run(body) == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}
    assert "escalated_at" not in get_item(aws, "unassigned")

    assert run(body) == {"batchItemFailures": []}

    assert len(aws.nurse_publishes()) == 1
    assert aws.alert_publishes() == []
    assert aws.sqs.sends == []


def test_escalated_at_already_set_by_another_delivery_is_not_an_error(aws, monkeypatch):
    spy(
        monkeypatch,
        "dynamodb_client",
        [],
        fail=escalated_at_update_fails,
        code="ConditionalCheckFailedException",
    )

    assert run(FALL) == {"batchItemFailures": []}
    assert len(aws.nurse_publishes()) == 1


def test_escalated_at_update_error_fails_the_record(aws, monkeypatch, logs):
    spy(monkeypatch, "dynamodb_client", [], fail=escalated_at_update_fails)

    assert run(FALL) == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}

    assert len(aws.nurse_publishes()) == 1
    (rec,) = alert_records(logs)
    assert rec["outcome"] == "error"


def test_escalated_at_is_the_time_after_the_nurse_publish(aws, clock):
    clock["now"] = T0 + timedelta(milliseconds=1234)

    run(FALL)

    assert get_item(aws)["escalated_at"] == "2026-11-03T02:00:01.234Z"


# ======== caregiver email lines (criterion 8) ========


@pytest.mark.parametrize("body", CRITICAL_BODIES, ids=CRITICAL_IDS)
def test_critical_caregiver_email_says_the_nurse_was_alerted(aws, body):
    run(body)

    (call,) = aws.alert_publishes()
    lines = call["Message"].splitlines()
    i = lines.index(NURSE_ALERTED)
    assert lines[i - 1] == ""
    assert lines[i + 1 : i + 3] == ["", f"Open the SilverCare portal: {PORTAL}/"]
    assert AT_ONCE_CRITICAL not in call["Message"]


def test_warning_caregiver_email_has_no_nurse_line(aws):
    run(WARNING)

    (call,) = aws.alert_publishes()
    assert NURSE_ALERTED not in call["Message"]
    assert "care centre" not in call["Message"]


def test_nurse_email_never_has_the_caregiver_nurse_alerted_line(aws):
    run(FALL)

    (call,) = aws.nurse_publishes()
    assert NURSE_ALERTED not in call["Message"]


# ======== log line (criterion 5) ========


@pytest.mark.parametrize(
    ("body", "nurses"),
    [
        (FALL, "immediate"),
        (CRITICAL_VITALS, "immediate"),
        (WARNING, "escalation_queued"),
        (alert_body(resident_id="unassigned"), "immediate"),
    ],
    ids=["fall", "critical-vitals", "warning", "unassigned"],
)
def test_alert_log_line_has_the_nurses_route(aws, logs, body, nurses):
    run(body)

    (rec,) = alert_records(logs)
    assert rec["nurses"] == nurses


def test_suppressed_alert_log_line_says_nurses_none(aws, logs):
    run(
        WARNING,
        vitals_body(
            "hr_high",
            "warning",
            alert_id="6f1c2e0a-5b3d-5e8f-9a47-000000000002",
            event_ts="2026-10-18T03:20:00.000Z",
        ),
    )

    assert [(r["outcome"], r["nurses"]) for r in alert_records(logs)] == [
        ("notified", "escalation_queued"),
        ("suppressed", "none"),
    ]


def test_alert_log_line_gains_only_the_nurses_field(aws, logs):
    run(FALL)

    (rec,) = alert_records(logs)
    assert set(rec) - {"level", "location", "message", "timestamp", "service"} == {
        "alert_id",
        "alert_type",
        "severity",
        "device_id",
        "resident_id",
        "outcome",
        "dispatch_latency_ms",
        "nurses",
    }


def test_logs_hold_no_nurse_email_text(aws, logs):
    run(FALL, alert_body(resident_id="unassigned"))

    text = " ".join(
        f"{k}={v}"
        for r in logs()
        for k, v in r.items()
        if k not in {"timestamp", "location"}
    )
    for hidden in ("care centre", "Sent to", "SilverCare", "Device:", "0.91", "3.8"):
        assert hidden not in text, hidden
