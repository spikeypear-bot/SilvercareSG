"""Staff resolve end to end against moto (docs/features/staff-resolve.md, criteria
3-8 and 10): the role/type rule, the conditional update, the final record in S3, the
follow-up to caregivers only, retries, the stored note in emails, and log privacy."""

from __future__ import annotations

import json

import handler
import pytest
from conftest import ALERTS_TABLE, BUCKET
from events import (
    ALERT_ID,
    CAREGIVER_NAME,
    DEVICE_PROBLEM_TYPES,
    DOCTOR_SUB,
    DP_EVENT_TS,
    EVENT_TS,
    NOTE,
    NURSE_SUB,
    PF_EVENT_TS,
    RESIDENT,
    RESOLVE_NOTE,
    alert_body,
    back_in_bed_body,
    cancel_body,
    caregiver_cancel_body,
    nurse_ack_body,
    possible_fall_body,
    resolve_body,
    staff_resolve_body,
    vitals_body,
)
from test_device_problem_status import put_dp
from test_status_update import (  # noqa: F401 - clock is an autouse fixture
    EXPIRES_AT,
    POWERTOOLS_KEYS,
    T0_ISO,
    UUID,
    clock,
    get_item,
    put_alert,
    run,
    spy,
    status_records,
)

STAFF = pytest.mark.parametrize("by_role", ["nurse", "doctor"])
SUBS = {"nurse": NURSE_SUB, "doctor": DOCTOR_SUB}
WHO = {"nurse": "the care centre nurse", "doctor": "a SilverCare doctor"}

ACK_AT = "2026-10-18T03:14:30.000Z"
ACK_NOTIFIED_AT = "2026-10-18T03:14:31.000Z"
ESCALATED_AT = "2026-10-18T03:14:08.700Z"
OTHER_SUB = "a1b2c3d4-0000-4000-8000-0000000000ff"
OTHER_NOTE = "Synthetic second note: must never be emailed."
FINAL_KEY = f"alerts/date=2026-10-18/{ALERT_ID}.final.json"
ORIGINAL_KEY = f"alerts/date=2026-10-18/{ALERT_ID}.json"
ALERTS_FILTER = {"resident_id": {"DataType": "String", "StringValue": RESIDENT}}
OK = {"batchItemFailures": []}
FAILED = {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}
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
FOOTER = (
    "",
    "Open the SilverCare portal: https://d0000example.cloudfront.net/",
    "",
    "This is an automated message. Do not reply.",
)
FALL_LINES = (
    "Type: Fall",
    "Severity: Critical",
    "Resident: res-0003",
    "When: 2026-10-18 11:14:07 SGT",
)


def resolve_email(by_role: str, *note_lines: str, alert_lines=FALL_LINES) -> str:
    lines = [
        "SilverCare update",
        "",
        f"The alert below was resolved by {WHO[by_role]} at 2026-10-18 11:15:00 SGT.",
        "",
        *alert_lines,
        "",
        "Closing note:",
        *(f"> {line}" for line in note_lines),
        *FOOTER,
    ]
    return "\n".join(lines) + "\n"


def put_acknowledged(aws, **attrs) -> dict:
    """A health alert a nurse acknowledged with a note, follow-up sent."""
    return put_alert(
        aws,
        **{
            "status": "acknowledged",
            "acknowledged_at": ACK_AT,
            "acknowledged_by": NURSE_SUB,
            "ack_note": NOTE,
            "status_notified_at": ACK_NOTIFIED_AT,
            **attrs,
        },
    )


def resolved_by(by_role: str = "nurse", by: str | None = None, **attrs) -> dict:
    """What a staff resolve leaves on an item before status_notified_at."""
    return {
        "status": "resolved",
        "resolved_at": "2026-10-18T03:15:00.000Z",
        "resolved_by_role": by_role,
        "resolved_by": by or SUBS[by_role],
        "resolve_note": RESOLVE_NOTE,
        "expires_at": EXPIRES_AT,
        "open_pk": None,
        **attrs,
    }


def s3_keys(aws) -> list[str]:
    resp = aws.s3.list_objects_v2(Bucket=BUCKET)
    return sorted(obj["Key"] for obj in resp.get("Contents", []))


def final_record(aws, key: str = FINAL_KEY) -> dict:
    return json.loads(aws.s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())


def as_json(item: dict) -> dict:
    return json.loads(handler.archive_body(item))


def without(item: dict, *names: str) -> dict:
    return {k: v for k, v in item.items() if k not in names}


def log_text(logs) -> str:
    return " ".join(
        f"{k}={v}"
        for rec in logs()
        for k, v in rec.items()
        if k not in {"timestamp", "location"}
    )


# ======== apply (criteria 3 and 4) ========


@STAFF
def test_staff_resolve_of_open_alert_sets_fields_and_removes_open_pk(aws, by_role):
    put_alert(aws, notified_at="2026-10-18T03:14:08.000Z")

    assert run(staff_resolve_body(by_role)) == OK

    item = get_item(aws)
    assert item["status"] == "resolved"
    assert item["resolved_at"] == "2026-10-18T03:15:00.000Z"
    assert item["resolved_by_role"] == by_role
    assert item["resolved_by"] == SUBS[by_role]
    assert item["resolve_note"] == RESOLVE_NOTE
    assert item["expires_at"] == EXPIRES_AT
    assert "open_pk" not in item
    assert item["status_notified_at"] == T0_ISO
    assert "by_name" not in item


@STAFF
def test_staff_resolve_of_acknowledged_alert_keeps_ack_fields_and_ack_note(
    aws, by_role
):
    put_acknowledged(aws)

    assert run(staff_resolve_body(by_role)) == OK

    item = get_item(aws)
    assert item["status"] == "resolved"
    assert item["resolved_by_role"] == by_role
    assert item["resolved_by"] == SUBS[by_role]
    assert item["resolve_note"] == RESOLVE_NOTE
    assert item["acknowledged_at"] == ACK_AT
    assert item["acknowledged_by"] == NURSE_SUB
    assert item["ack_note"] == NOTE
    assert "open_pk" not in item
    assert len(aws.alert_publishes()) == 1


def test_staff_resolve_update_is_conditional_on_open_or_acknowledged(aws, monkeypatch):
    put_alert(aws)
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)

    run(staff_resolve_body())

    (get,) = [kw for op, kw in events if op == "get_item"]
    assert get["ConsistentRead"] is True
    first = next(kw for op, kw in events if op == "update_item")
    assert first["ConditionExpression"] == "#status IN (:open, :acknowledged)"
    assert first["ReturnValues"] == "ALL_NEW"
    assert first["UpdateExpression"].endswith("REMOVE open_pk, status_notified_at")
    assert "resolve_note = :note" in first["UpdateExpression"]
    assert "resolved_by = :by" in first["UpdateExpression"]


@pytest.mark.parametrize(
    "body",
    [
        alert_body,
        lambda: vitals_body("hr_high", "warning"),
        lambda: vitals_body("spo2_low", "critical"),
    ],
    ids=["fall", "hr_high-warning", "spo2_low-critical"],
)
@STAFF
def test_staff_resolve_is_allowed_on_health_alerts(aws, body, by_role):
    put_alert(aws, body())

    run(staff_resolve_body(by_role))

    assert get_item(aws)["status"] == "resolved"
    assert len(aws.alert_publishes()) == 1


@STAFF
def test_staff_resolve_of_possible_fall_emails_caregivers_only(aws, by_role):
    put_alert(aws, possible_fall_body(), escalated_at="2026-11-03T18:30:00.500Z")
    target = {"alert_id": ALERT_ID, "event_ts": PF_EVENT_TS}

    assert run(staff_resolve_body(by_role, target=target)) == OK

    item = get_item(aws, event_ts=PF_EVENT_TS)
    assert item["status"] == "resolved"
    assert item["resolved_by_role"] == by_role
    (pub,) = aws.alert_publishes()
    assert pub["Subject"] == f"SilverCare update: Possible fall - {RESIDENT} resolved"
    assert aws.nurse_publishes() == []
    assert s3_keys(aws) == [f"alerts/date=2026-11-03/{ALERT_ID}.final.json"]


@pytest.mark.parametrize("alert_type", DEVICE_PROBLEM_TYPES)
@STAFF
def test_staff_resolve_of_device_problem_is_not_allowed(aws, logs, alert_type, by_role):
    put_dp(aws, alert_type)
    before = get_item(aws, event_ts=DP_EVENT_TS)
    target = {"alert_id": ALERT_ID, "event_ts": DP_EVENT_TS}

    assert run(staff_resolve_body(by_role, target=target)) == OK

    assert get_item(aws, event_ts=DP_EVENT_TS) == before
    assert aws.sns.publishes == []
    assert s3_keys(aws) == []
    (line,) = status_records(logs)
    assert line["outcome"] == "not_allowed"


@STAFF
def test_staff_resolve_of_missing_alert_is_not_open(aws, logs, by_role):
    assert run(staff_resolve_body(by_role)) == OK

    assert aws.sns.publishes == []
    assert s3_keys(aws) == []
    (line,) = status_records(logs)
    assert line["outcome"] == "not_open"


@pytest.mark.parametrize(
    "attrs",
    [
        {"status": "cancelled", "open_pk": None, "status_notified_at": T0_ISO},
        {
            "status": "resolved",
            "resolved_by_role": "system",
            "open_pk": None,
            "status_notified_at": T0_ISO,
        },
    ],
    ids=["cancelled", "resolved-and-notified"],
)
@STAFF
def test_staff_resolve_of_closed_alert_is_not_open_and_changes_nothing(
    aws, logs, attrs, by_role
):
    put_alert(aws, **attrs)
    before = get_item(aws)

    assert run(staff_resolve_body(by_role)) == OK

    assert get_item(aws) == before
    assert aws.sns.publishes == []
    assert s3_keys(aws) == []
    (line,) = status_records(logs)
    assert line["outcome"] == "not_open"


def test_alert_cancelled_just_before_the_resolve_is_skipped(aws, monkeypatch, logs):
    put_alert(aws)

    def cancel_meanwhile():
        aws.ddb.update_item(
            TableName=ALERTS_TABLE,
            Key={
                "pk": {"S": f"RESIDENT#{RESIDENT}"},
                "sk": {"S": f"ALERT#{EVENT_TS}#{ALERT_ID}"},
            },
            UpdateExpression="SET #s = :c REMOVE open_pk",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":c": {"S": "cancelled"}},
        )

    spy(
        monkeypatch,
        "dynamodb_client",
        [],
        before={("update_item", 1): cancel_meanwhile},
    )

    assert run(staff_resolve_body()) == OK

    assert get_item(aws)["status"] == "cancelled"
    assert aws.sns.publishes == []
    assert s3_keys(aws) == []
    (line,) = status_records(logs)
    assert line["outcome"] == "skipped"


# ======== order and the final record (criteria 5 and 8) ========


def test_order_is_get_update_final_record_publish_then_status_notified_at(
    aws, monkeypatch
):
    put_alert(aws)
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)
    spy(monkeypatch, "s3_client", events)
    spy(monkeypatch, "sns_client", events)

    run(staff_resolve_body())

    assert [op for op, _ in events] == [
        "get_item",
        "update_item",
        "put_object",
        "publish",
        "update_item",
    ]
    last = events[-1][1]
    assert "status_notified_at" in last["UpdateExpression"]
    assert last["ConditionExpression"] == "attribute_not_exists(status_notified_at)"


def test_final_record_key_and_content_type(aws, monkeypatch):
    put_alert(aws)
    events: list = []
    spy(monkeypatch, "s3_client", events)

    run(staff_resolve_body())

    ((op, put),) = events
    assert op == "put_object"
    assert put["Bucket"] == BUCKET
    assert put["Key"] == FINAL_KEY
    assert put["ContentType"] == "application/json"
    assert s3_keys(aws) == [FINAL_KEY]


def test_final_record_is_the_item_after_the_update(aws):
    put_alert(aws, escalated_at=ESCALATED_AT)

    run(staff_resolve_body())

    record = final_record(aws)
    # Written before status_notified_at is set (criterion 5).
    expected = without(get_item(aws), "status_notified_at")
    assert set(record) == set(expected)
    assert record == as_json(expected)


def test_final_record_holds_both_notes_and_never_by_name(aws):
    put_acknowledged(aws)

    run(staff_resolve_body())

    record = final_record(aws)
    assert record["ack_note"] == NOTE
    assert record["resolve_note"] == RESOLVE_NOTE
    assert record["resolved_by"] == NURSE_SUB
    assert record["resolved_by_role"] == "nurse"
    assert "by_name" not in record
    raw = aws.s3.get_object(Bucket=BUCKET, Key=FINAL_KEY)["Body"].read().decode()
    assert CAREGIVER_NAME not in raw


def test_final_record_date_is_the_utc_date_of_event_ts(aws):
    # 01:30 SGT on 19 October is still 18 October in UTC.
    event_ts = "2026-10-18T17:30:00.000Z"
    put_alert(aws, alert_body(event_ts=event_ts))
    target = {"alert_id": ALERT_ID, "event_ts": event_ts}

    run(staff_resolve_body(target=target, changed_at="2026-10-18T17:31:00.000Z"))

    assert s3_keys(aws) == [f"alerts/date=2026-10-18/{ALERT_ID}.final.json"]


def test_original_alert_copy_is_unchanged_by_the_final_record(aws):
    run(alert_body())
    original = aws.s3.get_object(Bucket=BUCKET, Key=ORIGINAL_KEY)["Body"].read()

    assert run(staff_resolve_body()) == OK

    assert s3_keys(aws) == sorted([ORIGINAL_KEY, FINAL_KEY])
    after = aws.s3.get_object(Bucket=BUCKET, Key=ORIGINAL_KEY)["Body"].read()
    assert after == original
    assert "resolve_note" not in json.loads(original)


def test_failed_final_record_write_fails_the_record_before_any_email(
    aws, monkeypatch, logs
):
    put_alert(aws)
    with monkeypatch.context() as m:
        spy(m, "s3_client", [], fail_on="put_object")
        result = run(staff_resolve_body())

    assert result == FAILED
    assert aws.sns.publishes == []
    item = get_item(aws)
    assert item["status"] == "resolved"
    assert "status_notified_at" not in item
    (line,) = status_records(logs)
    assert (line["outcome"], line["error"]) == ("error", "ThrottlingException")


def test_retry_after_a_failed_final_record_write_completes(aws, monkeypatch):
    put_alert(aws)
    with monkeypatch.context() as m:
        spy(m, "s3_client", [], fail_on="put_object")
        assert run(staff_resolve_body()) == FAILED

    assert run(staff_resolve_body()) == OK

    assert s3_keys(aws) == [FINAL_KEY]
    assert len(aws.alert_publishes()) == 1
    assert get_item(aws)["status_notified_at"] == T0_ISO


def test_final_record_written_for_device_problem_system_resolve(aws):
    put_dp(aws, status="acknowledged", acknowledged_at="2026-11-04T02:03:00.000Z")

    assert run(resolve_body()) == OK

    key = f"alerts/date=2026-11-04/{ALERT_ID}.final.json"
    assert s3_keys(aws) == [key]
    record = final_record(aws, key)
    assert record["status"] == "resolved"
    assert record["resolved_by_role"] == "system"
    assert "resolved_by" not in record
    assert "resolve_note" not in record
    assert record == as_json(get_item(aws, event_ts=DP_EVENT_TS))
    assert aws.sns.publishes == []


def test_final_record_written_for_back_in_bed_resolve_before_the_email(
    aws, monkeypatch
):
    put_alert(aws, possible_fall_body(), escalated_at="2026-11-03T18:30:00.500Z")
    events: list = []
    spy(monkeypatch, "s3_client", events)
    spy(monkeypatch, "sns_client", events)

    assert run(back_in_bed_body()) == OK

    assert [op for op, _ in events] == ["put_object", "publish", "publish"]
    key = f"alerts/date=2026-11-03/{ALERT_ID}.final.json"
    record = final_record(aws, key)
    assert record["resolved_by_role"] == "system"
    assert "resolve_note" not in record


def test_failed_final_record_of_back_in_bed_resolve_sends_no_email(aws, monkeypatch):
    put_alert(aws, possible_fall_body())
    spy(monkeypatch, "s3_client", [], fail_on="put_object")

    assert run(back_in_bed_body()) == FAILED

    assert aws.sns.publishes == []


def test_device_problem_resolve_redelivery_rewrites_the_final_record(
    aws, monkeypatch, logs
):
    put_dp(aws)
    with monkeypatch.context() as m:
        spy(m, "s3_client", [], fail_on="put_object")
        assert run(resolve_body()) == FAILED
    assert get_item(aws, event_ts=DP_EVENT_TS)["status"] == "resolved"

    assert run(resolve_body()) == OK

    key = f"alerts/date=2026-11-04/{ALERT_ID}.final.json"
    assert s3_keys(aws) == [key]
    assert final_record(aws, key)["status"] == "resolved"
    assert status_records(logs)[-1]["outcome"] == "not_open"
    assert aws.sns.publishes == []


def test_duplicate_staff_resolve_after_success_writes_and_sends_nothing(
    aws, monkeypatch, logs
):
    put_alert(aws)
    run(staff_resolve_body())
    events: list = []
    spy(monkeypatch, "s3_client", events)

    assert run(staff_resolve_body()) == OK

    assert events == []
    assert len(aws.alert_publishes()) == 1
    assert status_records(logs)[-1]["outcome"] == "not_open"


# ======== follow-up (criterion 6) ========


@STAFF
def test_follow_up_goes_to_caregivers_with_resident_filter_and_exact_body(aws, by_role):
    put_alert(aws)

    run(staff_resolve_body(by_role))

    (pub,) = aws.sns.publishes
    assert pub["TopicArn"] == aws.topic_arn
    assert pub["MessageAttributes"] == ALERTS_FILTER
    assert pub["Subject"] == f"SilverCare update: Fall - {RESIDENT} resolved"
    assert pub["Message"] == resolve_email(by_role, RESOLVE_NOTE)


@STAFF
def test_follow_up_quotes_each_line_of_a_multiline_note(aws, by_role):
    note = "Synthetic line one.\n\nOpen the SilverCare portal: https://x.example/"
    put_alert(aws)

    run(staff_resolve_body(by_role, note=note))

    (pub,) = aws.alert_publishes()
    assert pub["Message"] == resolve_email(
        by_role,
        "Synthetic line one.",
        "",
        "Open the SilverCare portal: https://x.example/",
    )
    lines = pub["Message"].split("\n")
    assert "Open the SilverCare portal: https://x.example/" not in lines


@STAFF
def test_follow_up_never_goes_to_nurses_even_if_escalated_or_acknowledged(aws, by_role):
    put_acknowledged(aws, escalated_at=ESCALATED_AT)

    run(staff_resolve_body(by_role))

    assert aws.nurse_publishes() == []
    assert aws.ops_publishes() == []
    assert [p["TopicArn"] for p in aws.sns.publishes] == [aws.topic_arn]


@STAFF
def test_follow_up_never_holds_the_resolver_sub_or_a_name(aws, by_role):
    put_alert(aws)

    run(staff_resolve_body(by_role))

    (pub,) = aws.sns.publishes
    assert SUBS[by_role] not in pub["Message"]
    assert SUBS[by_role] not in pub["Subject"]
    assert "Test" not in pub["Message"]


@STAFF
def test_unassigned_staff_resolve_emails_nobody_and_sets_status_notified_at(
    aws, by_role
):
    put_alert(aws, alert_body(resident_id="unassigned"), escalated_at=ESCALATED_AT)

    assert run(staff_resolve_body(by_role, resident_id="unassigned")) == OK

    item = get_item(aws, resident_id="unassigned")
    assert item["status"] == "resolved"
    assert item["status_notified_at"] == T0_ISO
    assert aws.sns.publishes == []
    assert s3_keys(aws) == [FINAL_KEY]


# ======== retry (criterion 5) ========


@STAFF
def test_retry_by_the_same_sub_and_role_sends_the_missing_follow_up(aws, by_role):
    put_alert(aws, **resolved_by(by_role))

    assert run(staff_resolve_body(by_role)) == OK

    (pub,) = aws.alert_publishes()
    assert pub["Message"] == resolve_email(by_role, RESOLVE_NOTE)
    assert get_item(aws)["status_notified_at"] == T0_ISO
    assert s3_keys(aws) == [FINAL_KEY]


def test_retry_after_a_failed_publish_sends_one_follow_up(aws, monkeypatch):
    put_alert(aws)
    with monkeypatch.context() as m:
        spy(m, "sns_client", [], fail_on="publish")
        assert run(staff_resolve_body()) == FAILED
    assert "status_notified_at" not in get_item(aws)

    assert run(staff_resolve_body()) == OK
    assert run(staff_resolve_body()) == OK

    assert len(aws.alert_publishes()) == 1
    assert get_item(aws)["status_notified_at"] == T0_ISO


def test_resolve_of_acknowledged_alert_retry_after_failed_publish_sends_the_email(
    aws, monkeypatch
):
    """Criterion 5: a missing email isn't accepted. The acknowledge's
    status_notified_at must not make the resolve's retry look already done."""
    put_acknowledged(aws)
    with monkeypatch.context() as m:
        spy(m, "sns_client", [], fail_on="publish")
        assert run(staff_resolve_body()) == FAILED

    assert run(staff_resolve_body()) == OK

    (pub,) = aws.alert_publishes()
    assert pub["Message"] == resolve_email("nurse", RESOLVE_NOTE)


def test_resolve_of_acknowledged_alert_retry_after_failed_final_record_writes_it(
    aws, monkeypatch
):
    """Criterion 8: a failed write fails the record and the retry writes it."""
    put_acknowledged(aws)
    with monkeypatch.context() as m:
        spy(m, "s3_client", [], fail_on="put_object")
        assert run(staff_resolve_body()) == FAILED

    assert run(staff_resolve_body()) == OK

    assert s3_keys(aws) == [FINAL_KEY]
    assert len(aws.alert_publishes()) == 1


def test_resolve_by_another_sub_after_an_unnotified_one_sends_nothing(aws, logs):
    put_alert(aws, **resolved_by("nurse", by=OTHER_SUB))
    before = get_item(aws)

    assert run(staff_resolve_body()) == OK

    assert get_item(aws) == before
    assert aws.sns.publishes == []
    assert s3_keys(aws) == []
    assert status_records(logs)[-1]["outcome"] == "not_open"


def test_resolve_by_the_same_sub_in_another_role_is_not_a_retry(aws, logs):
    put_alert(aws, **resolved_by("nurse"))

    assert run(staff_resolve_body("doctor", by=NURSE_SUB)) == OK

    assert aws.sns.publishes == []
    assert s3_keys(aws) == []
    assert status_records(logs)[-1]["outcome"] == "not_open"


def test_resolve_after_an_unnotified_system_resolve_is_not_a_retry(aws, logs):
    put_alert(aws, status="resolved", resolved_by_role="system", open_pk=None)

    assert run(staff_resolve_body()) == OK

    assert aws.sns.publishes == []
    assert s3_keys(aws) == []
    assert status_records(logs)[-1]["outcome"] == "not_open"


# ======== the stored note is emailed, never the update's own ========


def test_second_resolve_with_another_note_emails_and_keeps_the_first_note(
    aws, monkeypatch
):
    """The shared nurse account resolves twice before status_notified_at is set: the
    second counts as a retry, and the email and final record hold the FIRST note."""
    put_alert(aws)
    with monkeypatch.context() as m:
        spy(m, "sns_client", [], fail_on="publish")
        assert run(staff_resolve_body(note=RESOLVE_NOTE)) == FAILED

    assert run(staff_resolve_body(note=OTHER_NOTE)) == OK

    (pub,) = aws.alert_publishes()
    assert pub["Message"] == resolve_email("nurse", RESOLVE_NOTE)
    assert OTHER_NOTE not in pub["Message"]
    item = get_item(aws)
    assert item["resolve_note"] == RESOLVE_NOTE
    record = final_record(aws)
    assert record["resolve_note"] == RESOLVE_NOTE
    assert OTHER_NOTE not in json.dumps(record)


def test_acknowledge_retry_with_another_note_emails_the_stored_note(aws):
    put_acknowledged(aws, status_notified_at=None)

    run(nurse_ack_body(note=OTHER_NOTE))

    (pub,) = aws.alert_publishes()
    assert f"> {NOTE}" in pub["Message"]
    assert OTHER_NOTE not in pub["Message"]
    assert get_item(aws)["ack_note"] == NOTE


@pytest.mark.parametrize(
    "body", [cancel_body, caregiver_cancel_body], ids=["resident", "caregiver"]
)
def test_cancel_after_an_acknowledge_with_a_note_never_emails_the_note(aws, body):
    put_acknowledged(aws)

    run(body())

    assert aws.sns.publishes
    for pub in aws.sns.publishes:
        assert NOTE not in pub["Message"]
        assert "Message from the care centre:" not in pub["Message"]
        assert "Closing note:" not in pub["Message"]


def test_nurse_acknowledge_with_a_note_emails_the_note_block(aws):
    put_alert(aws)

    run(nurse_ack_body(note=NOTE))

    (pub,) = aws.alert_publishes()
    lines = pub["Message"].split("\n")
    assert lines[lines.index("Message from the care centre:") + 1] == f"> {NOTE}"
    assert "Closing note:" not in lines


def test_nurse_acknowledge_without_a_note_emails_no_note_block(aws):
    put_alert(aws)

    run(nurse_ack_body())

    (pub,) = aws.alert_publishes()
    assert "Message from the care centre:" not in pub["Message"]
    assert "Closing note:" not in pub["Message"]
    assert "> " not in pub["Message"]


# ======== logs (criterion 7) ========


@STAFF
def test_staff_resolve_log_line_has_has_note_and_no_other_new_field(aws, logs, by_role):
    put_alert(aws)

    run(staff_resolve_body(by_role))

    (line,) = status_records(logs)
    assert set(line) - POWERTOOLS_KEYS == ITEM_LINE_KEYS | {"has_note"}
    assert line["has_note"] is True
    assert (line["new_status"], line["by_role"], line["outcome"]) == (
        "resolved",
        by_role,
        "updated",
    )


def test_system_resolve_log_line_still_has_no_has_note(aws, logs):
    put_dp(aws)

    run(resolve_body())

    (line,) = status_records(logs)
    assert "has_note" not in line


def _fail_get(monkeypatch):
    spy(monkeypatch, "dynamodb_client", [], fail_on="get_item")


def _fail_put(monkeypatch):
    spy(monkeypatch, "s3_client", [], fail_on="put_object")


LOG_CASES = {
    "valid": (lambda aws: put_alert(aws), None, lambda: staff_resolve_body("nurse")),
    "valid-doctor": (
        lambda aws: put_acknowledged(aws),
        None,
        lambda: staff_resolve_body("doctor"),
    ),
    "invalid-note": (
        lambda aws: put_alert(aws),
        None,
        lambda: staff_resolve_body(note=RESOLVE_NOTE + "\t"),
    ),
    "invalid-by_name": (
        lambda aws: put_alert(aws),
        None,
        lambda: staff_resolve_body(by_name="Test Doctor Delta"),
    ),
    "not_allowed": (
        lambda aws: put_dp(aws),
        None,
        lambda: staff_resolve_body(
            "doctor", target={"alert_id": ALERT_ID, "event_ts": DP_EVENT_TS}
        ),
    ),
    "not_open": (
        lambda aws: put_alert(aws, status="cancelled", open_pk=None),
        None,
        lambda: staff_resolve_body(),
    ),
    "aws-error": (lambda aws: put_alert(aws), _fail_get, lambda: staff_resolve_body()),
    "put-object-failure": (
        lambda aws: put_alert(aws),
        _fail_put,
        lambda: staff_resolve_body("doctor"),
    ),
}


@pytest.mark.parametrize("case", list(LOG_CASES))
def test_staff_resolve_logs_never_hold_the_note_or_the_sub(
    aws, logs, monkeypatch, case
):
    setup, patch, body = LOG_CASES[case]
    setup(aws)
    if patch:
        patch(monkeypatch)

    run(body())

    assert status_records(logs) or logs()
    text = log_text(logs)
    # Real IDs first, before UUIDs are replaced.
    assert NURSE_SUB not in text
    assert DOCTOR_SUB not in text
    text = UUID.sub("<uuid>", text)
    for hidden in (
        RESOLVE_NOTE,
        NOTE,
        "Synthetic",
        "closing note",
        "all fine",
        "Closing note:",
        "SilverCare update",
        "Test Doctor",
        "by_name=",
        "by=",
        "resolve_note",
        "ack_note",
    ):
        assert hidden not in text, hidden
    for rec in logs():
        assert not {"note", "by", "by_name", "resolve_note", "ack_note"} & set(rec)
