"""Status updates end to end against moto (docs/features/alert-cancel.md, criteria 8-12
and 19): cancel and acknowledge targets, the conditional updates, follow-up emails,
retries, the later escalation check, and log privacy."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import handler
import pytest
from botocore.exceptions import ClientError
from conftest import ALERTS_TABLE
from events import (
    ALERT_ID,
    ALERT_ID_2,
    ALERT_ID_3,
    CAREGIVER_NAME,
    CHANGED_AT,
    DEVICE,
    EVENT_TS,
    RESIDENT,
    STAFF_NAME,
    STAFF_SUB,
    ack_body,
    alert_body,
    cancel_body,
    check_body,
    record,
    sqs_event,
    vitals_body,
)
from message import parse_alert

T0 = datetime(2026, 11, 3, 2, 0, 0, 0, tzinfo=UTC)
T0_ISO = "2026-11-03T02:00:00.000Z"
OTHER_DEVICE = "wearable-0008"
EVENT_TS_2 = "2026-10-18T03:14:30.000Z"
EVENT_TS_LATER = "2026-10-18T03:15:00.001Z"
EXPIRES_AT = int(
    (datetime(2026, 10, 18, 3, 15, tzinfo=UTC) + timedelta(days=30)).timestamp()
)
POWERTOOLS_KEYS = {"level", "location", "message", "timestamp", "service"}
ITEM_LINE_KEYS = {
    "alert_id",
    "alert_type",
    "severity",
    "device_id",
    "resident_id",
    "kind",
    "new_status",
    "by_role",
    "outcome",
}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

CANCEL_EMAIL = (
    "SilverCare update\n"
    "\n"
    "The alert below was cancelled by the resident at 2026-10-18 11:15:00 SGT.\n"
    "\n"
    "Type: Fall\n"
    "Severity: Critical\n"
    "Resident: res-0003\n"
    "When: 2026-10-18 11:14:07 SGT\n"
    "\n"
    "Open the SilverCare portal: https://d0000example.cloudfront.net/\n"
    "\n"
    "This is an automated message. Do not reply.\n"
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


def put_alert(aws, body: str | None = None, **attrs) -> dict:
    """Store an alert item as alert-processor would have, plus `attrs` (None
    removes an attribute)."""
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


def cancelled_attrs() -> dict:
    """What a cancel by this update leaves on an item (no status_notified_at)."""
    return {
        "status": "cancelled",
        "cancelled_at": CHANGED_AT,
        "cancelled_by_role": "resident",
        "expires_at": EXPIRES_AT,
        "open_pk": None,
    }


class Spy:
    """Wraps a client: records (op, kwargs), can raise a ClientError on the n-th call
    of one operation, and can run a callback before the n-th call of one operation."""

    def __init__(self, client, events, fail_on=None, fail_nth=1, before=None):
        self._client = client
        self._events = events
        self._fail_on = fail_on
        self._fail_nth = fail_nth
        self._before = before or {}
        self._counts: dict[str, int] = {}

    def __getattr__(self, op):
        method = getattr(self._client, op)

        def wrapper(**kwargs):
            self._events.append((op, kwargs))
            n = self._counts.get(op, 0) + 1
            self._counts[op] = n
            hook = self._before.get((op, n))
            if hook:
                hook()
            if op == self._fail_on and n == self._fail_nth:
                raise ClientError(
                    {"Error": {"Code": "ThrottlingException", "Message": "synthetic"}},
                    op,
                )
            return method(**kwargs)

        return wrapper


def spy(monkeypatch, client_fn: str, events: list, **kw) -> Spy:
    real = getattr(handler, client_fn)()
    wrapped = Spy(real, events, **kw)
    monkeypatch.setattr(handler, client_fn, lambda: wrapped)
    return wrapped


def status_records(logs) -> list[dict]:
    return [r for r in logs() if r.get("kind") == "status_update"]


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Fixes handler's datetime.now(UTC) at T0."""
    state = {"now": T0}

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"]

    monkeypatch.setattr(handler, "datetime", FixedDatetime)
    return state


# ======== cancel: one open alert (criteria 8-12) ========


def test_cancel_sets_status_times_role_and_expiry_and_removes_open_pk(aws):
    put_alert(aws)

    assert run(cancel_body()) == {"batchItemFailures": []}

    item = get_item(aws)
    assert item["status"] == "cancelled"
    assert item["cancelled_at"] == CHANGED_AT
    assert item["cancelled_by_role"] == "resident"
    assert item["expires_at"] == EXPIRES_AT
    assert "open_pk" not in item
    assert item["status_notified_at"] == T0_ISO
    assert "acknowledged_at" not in item


def test_cancel_emails_caregivers_with_resident_filter_exact_subject_and_body(aws):
    put_alert(aws)

    run(cancel_body())

    assert aws.alert_publishes() == [
        {
            "TopicArn": aws.topic_arn,
            "Subject": "SilverCare update: Fall - res-0003 cancelled",
            "Message": CANCEL_EMAIL,
            "MessageAttributes": {
                "resident_id": {"DataType": "String", "StringValue": RESIDENT}
            },
        }
    ]


def test_cancel_of_unescalated_alert_sends_no_nurse_email(aws):
    put_alert(aws)

    run(cancel_body())

    assert aws.nurse_publishes() == []


def test_cancel_of_escalated_alert_also_emails_nurses(aws):
    put_alert(aws, escalated_at="2026-11-03T01:59:00.000Z")

    run(cancel_body())

    (nurse,) = aws.nurse_publishes()
    assert nurse == {
        "TopicArn": aws.nurses_topic_arn,
        "Subject": "SilverCare update: Fall - res-0003 cancelled",
        "Message": CANCEL_EMAIL,
    }
    assert len(aws.alert_publishes()) == 1


def test_escalated_at_set_just_before_the_cancel_still_emails_nurses(aws, monkeypatch):
    # The Query sees no escalated_at; the check escalates between the Query and the
    # conditional update, which returns ALL_NEW.
    put_alert(aws)
    events: list = []
    spy(
        monkeypatch,
        "dynamodb_client",
        events,
        before={
            ("update_item", 1): lambda: set_attr(
                aws, "escalated_at", "2026-11-03T01:59:59.000Z"
            )
        },
    )

    run(cancel_body())

    assert len(aws.nurse_publishes()) == 1
    status_update = next(kw for op, kw in events if op == "update_item")
    assert status_update["ReturnValues"] == "ALL_NEW"


def test_cancel_update_is_conditional_on_status_open(aws, monkeypatch):
    put_alert(aws)
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)

    run(cancel_body())

    first, second = [kw for op, kw in events if op == "update_item"]
    assert first["ConditionExpression"] == "#status = :open"
    assert "REMOVE open_pk" in first["UpdateExpression"]
    assert second["UpdateExpression"] == "SET status_notified_at = :now"
    assert second["ConditionExpression"] == "attribute_not_exists(status_notified_at)"


def test_order_is_query_update_publish_then_status_notified_at(aws, monkeypatch):
    put_alert(aws)
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)
    spy(monkeypatch, "sns_client", events)

    run(cancel_body())

    assert [op for op, _ in events] == [
        "query",
        "update_item",
        "publish",
        "update_item",
    ]


def test_cancel_skips_the_repeat_check_archive_and_escalation_queue(aws):
    put_alert(aws)

    run(cancel_body())

    assert aws.sqs.sends == []
    assert aws.s3.list_objects_v2(Bucket="silvercare-history-test")["KeyCount"] == 0


# ======== cancel: which alerts (criterion 8) ========


def test_cancel_changes_every_open_alert_of_the_device(aws):
    put_alert(aws)
    put_alert(
        aws,
        vitals_body("hr_high", "critical", alert_id=ALERT_ID_2, event_ts=EVENT_TS_2),
    )

    run(cancel_body())

    assert get_item(aws)["status"] == "cancelled"
    assert get_item(aws, event_ts=EVENT_TS_2, alert_id=ALERT_ID_2)["status"] == (
        "cancelled"
    )
    assert len(aws.alert_publishes()) == 2


def test_cancel_includes_an_alert_at_exactly_changed_at(aws):
    put_alert(aws, alert_body(event_ts=CHANGED_AT))

    run(cancel_body())

    assert get_item(aws, event_ts=CHANGED_AT)["status"] == "cancelled"


def test_cancel_leaves_an_alert_after_changed_at_open(aws):
    put_alert(aws, alert_body(alert_id=ALERT_ID_2, event_ts=EVENT_TS_LATER))

    run(cancel_body())

    item = get_item(aws, event_ts=EVENT_TS_LATER, alert_id=ALERT_ID_2)
    assert item["status"] == "open"
    assert item["open_pk"] == "OPEN"
    assert aws.sns.publishes == []


def test_cancel_leaves_another_devices_alert_open(aws):
    put_alert(aws, alert_body(alert_id=ALERT_ID_2, device_id=OTHER_DEVICE))

    run(cancel_body())

    assert get_item(aws, alert_id=ALERT_ID_2)["status"] == "open"
    assert aws.sns.publishes == []


def test_cancel_leaves_another_residents_alert_open(aws):
    put_alert(aws, alert_body(resident_id="res-0004"))

    run(cancel_body())

    assert get_item(aws, resident_id="res-0004")["status"] == "open"


@pytest.mark.parametrize("status", ["acknowledged", "resolved"])
def test_cancel_leaves_a_non_open_alert_unchanged_without_email(aws, logs, status):
    put_alert(aws, status=status, open_pk=None, status_notified_at=T0_ISO)

    assert run(cancel_body()) == {"batchItemFailures": []}

    item = get_item(aws)
    assert item["status"] == status
    assert "cancelled_at" not in item
    assert aws.sns.publishes == []
    (rec,) = status_records(logs)
    assert rec["outcome"] == "no_open_alerts"


def test_acknowledged_alert_without_status_notified_at_is_not_a_cancel_target(
    aws, logs
):
    put_alert(aws, status="acknowledged", acknowledged_by=STAFF_SUB, open_pk=None)

    run(cancel_body())

    assert get_item(aws)["status"] == "acknowledged"
    assert aws.sns.publishes == []
    (rec,) = status_records(logs)
    assert rec["outcome"] == "no_open_alerts"


def test_cancel_query_is_consistent_and_bounded_by_changed_at(aws, monkeypatch):
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)

    run(cancel_body())

    ((op, kw),) = events
    assert op == "query"
    assert kw["ConsistentRead"] is True
    assert kw["KeyConditionExpression"] == "pk = :pk AND sk <= :high"
    values = kw["ExpressionAttributeValues"]
    assert values[":pk"] == {"S": f"RESIDENT#{RESIDENT}"}
    assert values[":high"] == {"S": f"ALERT#{CHANGED_AT}#~"}
    assert values[":device"] == {"S": DEVICE}


def test_cancel_query_follows_every_page(aws, monkeypatch):
    ids = [ALERT_ID, ALERT_ID_2, ALERT_ID_3]
    for i, alert_id in enumerate(ids):
        put_alert(
            aws, alert_body(alert_id=alert_id, event_ts=f"2026-10-18T03:1{i}:00.000Z")
        )
    put_alert(
        aws,
        alert_body(
            alert_id="6f1c2e0a-5b3d-5e8f-9a47-000000000004", device_id=OTHER_DEVICE
        ),
    )
    real = handler.dynamodb_client()
    queries: list = []

    class OnePerPage:
        def __getattr__(self, op):
            return getattr(real, op)

        def query(self, **kw):
            queries.append(kw)
            return real.query(**kw, Limit=1)

    monkeypatch.setattr(handler, "dynamodb_client", lambda: OnePerPage())

    assert run(cancel_body()) == {"batchItemFailures": []}

    for i, alert_id in enumerate(ids):
        item = get_item(aws, alert_id=alert_id, event_ts=f"2026-10-18T03:1{i}:00.000Z")
        assert item["status"] == "cancelled"
    assert len(queries) > len(ids)
    assert "ExclusiveStartKey" in queries[-1]
    assert len(aws.alert_publishes()) == 3


def test_no_open_alerts_is_not_a_failure_and_sends_nothing(aws, logs):
    assert run(cancel_body()) == {"batchItemFailures": []}

    assert aws.sns.publishes == []
    (rec,) = status_records(logs)
    assert set(rec) - POWERTOOLS_KEYS == {
        "device_id",
        "resident_id",
        "kind",
        "new_status",
        "by_role",
        "outcome",
    }
    assert rec["outcome"] == "no_open_alerts"


def test_duplicate_cancel_after_success_sends_no_second_email(aws, logs):
    put_alert(aws)
    run(cancel_body())

    run(cancel_body())

    assert len(aws.alert_publishes()) == 1
    assert status_records(logs)[-1]["outcome"] == "no_open_alerts"


# ======== unassigned (criterion 12) ========


def test_unassigned_cancel_emails_no_caregiver_and_sets_status_notified_at(aws):
    put_alert(aws, alert_body(resident_id="unassigned"))

    run(cancel_body(resident_id="unassigned"))

    item = get_item(aws, resident_id="unassigned")
    assert item["status"] == "cancelled"
    assert item["status_notified_at"] == T0_ISO
    assert aws.sns.publishes == []


def test_unassigned_escalated_cancel_emails_nurses_with_device_line(aws):
    put_alert(
        aws,
        alert_body(resident_id="unassigned"),
        escalated_at="2026-11-03T01:59:00.000Z",
    )

    run(cancel_body(resident_id="unassigned"))

    assert aws.alert_publishes() == []
    (nurse,) = aws.nurse_publishes()
    assert nurse["Subject"] == "SilverCare update: Fall - unassigned cancelled"
    assert "\nResident: unassigned\nDevice: wearable-0007\n" in nurse["Message"]


# ======== condition failure (criterion 9) ========


def test_alert_changed_before_the_update_is_skipped_without_email(
    aws, monkeypatch, logs
):
    put_alert(aws)
    spy(
        monkeypatch,
        "dynamodb_client",
        [],
        before={("update_item", 1): lambda: set_attr(aws, "status", "acknowledged")},
    )

    assert run(cancel_body()) == {"batchItemFailures": []}

    item = get_item(aws)
    assert item["status"] == "acknowledged"
    assert "cancelled_at" not in item
    assert "status_notified_at" not in item
    assert aws.sns.publishes == []
    (rec,) = status_records(logs)
    assert rec["outcome"] == "skipped"


# ======== retries (criterion 10) ========


def test_cancelled_alert_without_status_notified_at_gets_its_follow_up(aws, logs):
    put_alert(aws, **cancelled_attrs())

    run(cancel_body())

    assert len(aws.alert_publishes()) == 1
    assert get_item(aws)["status_notified_at"] == T0_ISO
    (rec,) = status_records(logs)
    assert rec["outcome"] == "updated"


def test_retry_after_a_failed_publish_sends_one_follow_up(aws, monkeypatch):
    put_alert(aws)
    with monkeypatch.context() as m:
        spy(m, "sns_client", [], fail_on="publish")
        result = run(cancel_body())
    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}
    item = get_item(aws)
    assert item["status"] == "cancelled"
    assert "status_notified_at" not in item

    assert run(cancel_body()) == {"batchItemFailures": []}
    assert run(cancel_body()) == {"batchItemFailures": []}

    # The failed Publish raised before reaching SNS; exactly one retry emailed.
    assert len(aws.alert_publishes()) == 1
    assert get_item(aws)["status_notified_at"] == T0_ISO


def test_status_notified_at_already_set_by_another_delivery_is_not_a_failure(
    aws, monkeypatch
):
    put_alert(aws)
    spy(
        monkeypatch,
        "dynamodb_client",
        [],
        before={
            ("update_item", 2): lambda: set_attr(
                aws, "status_notified_at", "2026-11-03T01:59:59.999Z"
            )
        },
    )

    assert run(cancel_body()) == {"batchItemFailures": []}

    assert get_item(aws)["status_notified_at"] == "2026-11-03T01:59:59.999Z"


def test_failed_status_notified_at_update_fails_the_record(aws, monkeypatch, logs):
    put_alert(aws)
    spy(monkeypatch, "dynamodb_client", [], fail_on="update_item", fail_nth=2)

    result = run(cancel_body())

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}
    (rec,) = status_records(logs)
    assert rec["outcome"] == "error"
    assert rec["error"] == "ThrottlingException"


def test_query_failure_fails_the_record(aws, monkeypatch, logs):
    put_alert(aws)
    spy(monkeypatch, "dynamodb_client", [], fail_on="query")

    result = run(cancel_body())

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}
    assert get_item(aws)["status"] == "open"
    (rec,) = status_records(logs)
    assert rec["outcome"] == "error"


# ======== acknowledge (alert target) ========


def test_staff_acknowledge_sets_acknowledged_fields_and_keeps_open_pk(aws):
    put_alert(aws)

    assert run(ack_body("staff")) == {"batchItemFailures": []}

    item = get_item(aws)
    assert item["status"] == "acknowledged"
    assert item["acknowledged_at"] == CHANGED_AT
    assert item["acknowledged_by"] == STAFF_SUB
    assert item["open_pk"] == "OPEN"
    assert "expires_at" not in item
    assert "cancelled_at" not in item
    assert item["status_notified_at"] == T0_ISO


def test_staff_acknowledge_emails_caregivers_with_masked_name(aws):
    put_alert(aws)

    run(ack_body("staff"))

    (pub,) = aws.alert_publishes()
    assert pub["Subject"] == "SilverCare update: Fall - res-0003 acknowledged"
    assert (
        "The alert below was acknowledged by Te** Nu*** Al*** (staff) at"
        " 2026-10-18 11:15:00 SGT." in pub["Message"]
    )
    assert STAFF_NAME not in pub["Message"]
    assert STAFF_SUB not in pub["Message"]


def test_caregiver_acknowledge_email_says_caregiver(aws):
    put_alert(aws)

    run(ack_body("caregiver"))

    (pub,) = aws.alert_publishes()
    assert "by Te** Ca******* Be** (caregiver) at" in pub["Message"]
    assert CAREGIVER_NAME not in pub["Message"]


def test_acknowledge_changes_only_the_named_alert(aws):
    put_alert(aws)
    put_alert(aws, alert_body(alert_id=ALERT_ID_2, event_ts=EVENT_TS_2))

    run(ack_body())

    assert get_item(aws)["status"] == "acknowledged"
    assert get_item(aws, alert_id=ALERT_ID_2, event_ts=EVENT_TS_2)["status"] == "open"


def test_acknowledge_reads_the_alert_with_a_consistent_get(aws, monkeypatch):
    put_alert(aws)
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)

    run(ack_body())

    op, kw = events[0]
    assert op == "get_item"
    assert kw["ConsistentRead"] is True
    assert kw["Key"] == key()


@pytest.mark.parametrize("status", ["cancelled", "resolved"])
def test_acknowledge_of_non_open_alert_is_not_open_without_email(aws, logs, status):
    put_alert(aws, status=status, open_pk=None, status_notified_at=T0_ISO)

    assert run(ack_body()) == {"batchItemFailures": []}

    assert aws.sns.publishes == []
    (rec,) = status_records(logs)
    assert rec["outcome"] == "not_open"


def test_acknowledge_of_missing_alert_is_not_open(aws, logs):
    assert run(ack_body()) == {"batchItemFailures": []}

    (rec,) = status_records(logs)
    assert rec["outcome"] == "not_open"


def test_acknowledge_retry_by_the_same_user_sends_the_missing_follow_up(aws):
    put_alert(
        aws,
        status="acknowledged",
        acknowledged_at=CHANGED_AT,
        acknowledged_by=STAFF_SUB,
    )

    run(ack_body())

    assert len(aws.alert_publishes()) == 1
    assert get_item(aws)["status_notified_at"] == T0_ISO


def test_acknowledge_by_another_user_after_an_unnotified_one_sends_nothing(aws, logs):
    put_alert(
        aws,
        status="acknowledged",
        acknowledged_at=CHANGED_AT,
        acknowledged_by="a1b2c3d4-0000-4000-8000-0000000000ff",
    )

    run(ack_body())

    assert aws.sns.publishes == []
    assert "status_notified_at" not in get_item(aws)
    (rec,) = status_records(logs)
    assert rec["outcome"] == "not_open"


def test_acknowledge_of_escalated_alert_emails_nurses_too(aws):
    put_alert(aws, escalated_at="2026-11-03T01:59:00.000Z")

    run(ack_body())

    assert len(aws.nurse_publishes()) == 1
    assert len(aws.alert_publishes()) == 1


# ======== later escalation check (criterion 19) ========


def test_cancelled_alert_later_escalation_check_is_not_escalated(aws, logs):
    run(alert_body())
    run(cancel_body())

    assert run(check_body()) == {"batchItemFailures": []}

    assert aws.nurse_publishes() == []
    (check,) = [r for r in logs() if r.get("kind") == "escalation_check"]
    assert check["outcome"] == "not_escalated"
    assert check["reason"] == "status:cancelled"


# ======== logs (criterion 11) ========


def test_per_alert_log_line_has_exactly_the_criterion_11_fields(aws, logs):
    put_alert(aws)

    run(cancel_body())

    (rec,) = status_records(logs)
    assert set(rec) - POWERTOOLS_KEYS == ITEM_LINE_KEYS
    assert rec == {
        **{k: rec[k] for k in POWERTOOLS_KEYS & set(rec)},
        "alert_id": ALERT_ID,
        "alert_type": "fall",
        "severity": "critical",
        "device_id": DEVICE,
        "resident_id": RESIDENT,
        "kind": "status_update",
        "new_status": "cancelled",
        "by_role": "resident",
        "outcome": "updated",
    }


def test_one_log_line_per_cancelled_alert(aws, logs):
    put_alert(aws)
    put_alert(aws, alert_body(alert_id=ALERT_ID_2, event_ts=EVENT_TS_2))

    run(cancel_body())

    recs = status_records(logs)
    assert sorted(r["alert_id"] for r in recs) == [ALERT_ID, ALERT_ID_2]
    assert all(r["outcome"] == "updated" for r in recs)


def _log_text(logs) -> str:
    return " ".join(
        f"{k}={v}"
        for rec in logs()
        for k, v in rec.items()
        if k not in {"timestamp", "location"}
    )


def test_acknowledge_logs_never_hold_by_name_details_or_email_text(aws, logs):
    put_alert(aws, escalated_at="2026-11-03T01:59:00.000Z")

    run(ack_body("staff"))

    text = _log_text(logs)
    # Real IDs first, before UUIDs are replaced.
    assert STAFF_SUB not in text
    text = UUID.sub("<uuid>", text)
    for hidden in (
        STAFF_NAME,
        "Test",
        "Nurse",
        "Alpha",
        "Te**",
        "Nu***",
        "Al***",
        "0.91",
        "3.8",
        "confidence",
        "details",
        "SilverCare update",
        "was acknowledged",
        "(staff)",
        "Open the SilverCare portal",
        "by_name",
        "'by'",
        "by=",
    ):
        assert hidden not in text, hidden


def test_rejected_status_update_logs_never_hold_by_or_by_name(aws, logs):
    body = ack_body("staff", by_name="Test Nurse Alpha\n")

    result = run(body)

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}
    text = _log_text(logs)
    assert STAFF_SUB not in text
    text = UUID.sub("<uuid>", text)
    for hidden in ("Test", "Nurse", "Alpha", "by_name="):
        assert hidden not in text, hidden
    (rec,) = logs()
    assert rec["reason"] == "invalid:by_name"


def test_cancel_logs_never_hold_email_text_or_details(aws, logs):
    put_alert(aws, escalated_at="2026-11-03T01:59:00.000Z")

    run(cancel_body())

    text = UUID.sub("<uuid>", _log_text(logs))
    for hidden in ("0.91", "3.8", "SilverCare update", "the resident at", "SGT"):
        assert hidden not in text, hidden
