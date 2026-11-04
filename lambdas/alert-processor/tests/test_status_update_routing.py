"""Nurse acknowledges and caregiver cancels end to end against moto
(docs/features/alert-routing.md, criteria 10-15 and 19): what each update may change,
the stored fields (cancelled_by, ack_note), who gets the follow-up, its body, retries,
and the log line."""

from __future__ import annotations

import pytest
from events import (
    ALERT_ID,
    ALERT_ID_2,
    CAREGIVER_MASKED,
    CAREGIVER_NAME,
    CAREGIVER_SUB,
    CHANGED_AT,
    DEVICE,
    NOTE,
    NURSE_SUB,
    RESIDENT,
    alert_body,
    cancel_body,
    caregiver_cancel_body,
    nurse_ack_body,
    vitals_body,
)
from test_status_update import (  # noqa: F401 - clock is an autouse fixture
    EVENT_TS_2,
    EXPIRES_AT,
    POWERTOOLS_KEYS,
    T0_ISO,
    clock,
    get_item,
    key,
    put_alert,
    run,
    set_attr,
    spy,
    status_records,
)

ACK_AT = "2026-10-18T03:14:30.000Z"
ACK_NOTIFIED_AT = "2026-10-18T03:14:31.000Z"
ESCALATED_AT = "2026-10-18T03:14:08.700Z"
OTHER_SUB = "a1b2c3d4-0000-4000-8000-0000000000ff"
ALERTS_FILTER = {"resident_id": {"DataType": "String", "StringValue": RESIDENT}}
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
ALERT_LINES = (
    "Type: Fall",
    "Severity: Critical",
    "Resident: res-0003",
    "When: 2026-10-18 11:14:07 SGT",
)
NOTE_BLOCK = ("", "Message from the care centre:", f"> {NOTE}")


def email(*lines: str) -> str:
    return "\n".join(["SilverCare update", "", *lines, *FOOTER]) + "\n"


ACK_EMAIL = email(
    "The alert below was acknowledged by the care centre nurse at"
    " 2026-10-18 11:15:00 SGT.",
    "",
    *ALERT_LINES,
    *NOTE_BLOCK,
)
ACK_EMAIL_NO_NOTE = email(
    "The alert below was acknowledged by the care centre nurse at"
    " 2026-10-18 11:15:00 SGT.",
    "",
    *ALERT_LINES,
)
CAREGIVER_CANCEL_EMAIL = email(
    f"The alert below was cancelled by {CAREGIVER_MASKED} (caregiver) at"
    " 2026-10-18 11:15:00 SGT.",
    "",
    *ALERT_LINES,
)
ACKED_LINE = (
    "It had been acknowledged by the care centre nurse at 2026-10-18 11:14:30 SGT."
)


def put_acknowledged(aws, **attrs) -> dict:
    """An alert a nurse acknowledged with a note, follow-up sent."""
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


# ======== nurse acknowledge: the update ========


def test_nurse_acknowledge_sets_acknowledged_fields_and_keeps_open_pk(aws):
    put_alert(aws)

    assert run(nurse_ack_body()) == {"batchItemFailures": []}

    item = get_item(aws)
    assert item["status"] == "acknowledged"
    assert item["acknowledged_at"] == CHANGED_AT
    assert item["acknowledged_by"] == NURSE_SUB
    assert item["open_pk"] == "OPEN"
    assert item["status_notified_at"] == T0_ISO
    assert "ack_note" not in item
    assert "expires_at" not in item
    assert "cancelled_at" not in item


def test_nurse_acknowledge_with_note_stores_ack_note_as_sent(aws):
    put_alert(aws)
    note = "Synthetic line one.\n  Synthetic line two."

    run(nurse_ack_body(note=note))

    assert get_item(aws)["ack_note"] == note


def test_nurse_acknowledge_with_null_note_stores_no_ack_note(aws):
    put_alert(aws)

    run(nurse_ack_body(note=None))

    item = get_item(aws)
    assert item["status"] == "acknowledged"
    assert "ack_note" not in item


def test_acknowledge_update_is_conditional_on_open_and_sets_ack_note(aws, monkeypatch):
    put_alert(aws)
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)

    run(nurse_ack_body(note=NOTE))

    first, second = [kw for op, kw in events if op == "update_item"]
    assert first["ConditionExpression"] == "#status = :open"
    assert first["UpdateExpression"] == (
        "SET #status = :new, acknowledged_at = :at, acknowledged_by = :by, "
        "ack_note = :note"
    )
    assert first["ExpressionAttributeValues"][":note"] == {"S": NOTE}
    assert first["ExpressionAttributeValues"][":by"] == {"S": NURSE_SUB}
    assert second["UpdateExpression"] == "SET status_notified_at = :now"


def test_acknowledge_without_note_does_not_write_ack_note(aws, monkeypatch):
    put_alert(aws)
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)

    run(nurse_ack_body())

    first = next(kw for op, kw in events if op == "update_item")
    assert "ack_note" not in first["UpdateExpression"]
    assert ":note" not in first["ExpressionAttributeValues"]
    assert "REMOVE" not in first["UpdateExpression"]


def test_acknowledge_reads_the_alert_with_a_consistent_get(aws, monkeypatch):
    put_alert(aws)
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)

    run(nurse_ack_body())

    op, kw = events[0]
    assert op == "get_item"
    assert kw["ConsistentRead"] is True
    assert kw["Key"] == key()


def test_acknowledge_changes_only_the_named_alert(aws):
    put_alert(aws)
    put_alert(aws, alert_body(alert_id=ALERT_ID_2, event_ts=EVENT_TS_2))

    run(nurse_ack_body())

    assert get_item(aws)["status"] == "acknowledged"
    assert get_item(aws, alert_id=ALERT_ID_2, event_ts=EVENT_TS_2)["status"] == "open"


# ======== nurse acknowledge: the follow-up (criteria 13-15) ========


def test_nurse_acknowledge_emails_caregivers_with_the_note_block(aws):
    put_alert(aws)

    run(nurse_ack_body(note=NOTE))

    assert aws.alert_publishes() == [
        {
            "TopicArn": aws.topic_arn,
            "Subject": "SilverCare update: Fall - res-0003 acknowledged",
            "Message": ACK_EMAIL,
            "MessageAttributes": ALERTS_FILTER,
        }
    ]


def test_nurse_acknowledge_without_note_email_has_no_note_block(aws):
    put_alert(aws)

    run(nurse_ack_body())

    (pub,) = aws.alert_publishes()
    assert pub["Message"] == ACK_EMAIL_NO_NOTE


def test_nurse_acknowledge_with_multiline_note_keeps_the_lines(aws):
    put_alert(aws)

    run(nurse_ack_body(note="Synthetic line one.\nSynthetic line two."))

    (pub,) = aws.alert_publishes()
    assert (
        "\nMessage from the care centre:\n> Synthetic line one.\n"
        "> Synthetic line two.\n" in pub["Message"]
    )


def test_nurse_acknowledge_of_escalated_alert_emails_caregivers_only(aws):
    put_alert(aws, escalated_at=ESCALATED_AT)

    run(nurse_ack_body(note=NOTE))

    assert aws.nurse_publishes() == []
    assert len(aws.alert_publishes()) == 1


def test_nurse_acknowledge_email_never_has_the_nurse_sub(aws):
    put_alert(aws)

    run(nurse_ack_body(note=NOTE))

    (pub,) = aws.alert_publishes()
    assert NURSE_SUB not in pub["Message"]
    assert NURSE_SUB not in pub["Subject"]


def test_unassigned_acknowledge_emails_nobody_and_sets_status_notified_at(aws, logs):
    put_alert(aws, alert_body(resident_id="unassigned"), escalated_at=ESCALATED_AT)

    assert run(nurse_ack_body(resident_id="unassigned", note=NOTE)) == {
        "batchItemFailures": []
    }

    assert aws.sns.publishes == []
    item = get_item(aws, resident_id="unassigned")
    assert item["status"] == "acknowledged"
    assert item["ack_note"] == NOTE
    assert item["status_notified_at"] == T0_ISO
    (rec,) = status_records(logs)
    assert rec["outcome"] == "updated"


# ======== nurse acknowledge: nothing to change (criterion 10) ========


@pytest.mark.parametrize("by", [NURSE_SUB, OTHER_SUB])
def test_acknowledge_of_acknowledged_alert_is_not_open(aws, logs, by):
    put_acknowledged(aws, acknowledged_by=by)

    assert run(nurse_ack_body(note="Synthetic second note.")) == {
        "batchItemFailures": []
    }

    item = get_item(aws)
    assert item["ack_note"] == NOTE
    assert item["acknowledged_at"] == ACK_AT
    assert item["status_notified_at"] == ACK_NOTIFIED_AT
    assert aws.sns.publishes == []
    (rec,) = status_records(logs)
    assert rec["outcome"] == "not_open"


@pytest.mark.parametrize("status", ["cancelled", "resolved"])
def test_acknowledge_of_cancelled_or_resolved_alert_is_not_open(aws, logs, status):
    put_alert(aws, status=status, open_pk=None, status_notified_at=T0_ISO)

    assert run(nurse_ack_body()) == {"batchItemFailures": []}

    assert get_item(aws)["status"] == status
    assert aws.sns.publishes == []
    (rec,) = status_records(logs)
    assert rec["outcome"] == "not_open"


def test_acknowledge_of_missing_alert_is_not_open(aws, logs):
    assert run(nurse_ack_body()) == {"batchItemFailures": []}

    (rec,) = status_records(logs)
    assert rec["outcome"] == "not_open"


def test_alert_cancelled_just_before_the_acknowledge_is_skipped(aws, monkeypatch, logs):
    put_alert(aws)
    spy(
        monkeypatch,
        "dynamodb_client",
        [],
        before={("update_item", 1): lambda: set_attr(aws, "status", "cancelled")},
    )

    assert run(nurse_ack_body(note=NOTE)) == {"batchItemFailures": []}

    item = get_item(aws)
    assert item["status"] == "cancelled"
    assert "ack_note" not in item
    assert aws.sns.publishes == []
    (rec,) = status_records(logs)
    assert rec["outcome"] == "skipped"


# ======== nurse acknowledge: retries (criterion 11) ========


def test_acknowledge_retry_by_the_same_nurse_sends_the_missing_follow_up(aws):
    put_acknowledged(aws, status_notified_at=None)

    run(nurse_ack_body(note=NOTE))

    (pub,) = aws.alert_publishes()
    assert pub["Message"] == ACK_EMAIL
    assert get_item(aws)["status_notified_at"] == T0_ISO


def test_acknowledge_by_another_nurse_after_an_unnotified_one_sends_nothing(aws, logs):
    put_acknowledged(aws, acknowledged_by=OTHER_SUB, status_notified_at=None)

    run(nurse_ack_body())

    assert aws.sns.publishes == []
    assert "status_notified_at" not in get_item(aws)
    (rec,) = status_records(logs)
    assert rec["outcome"] == "not_open"


# ======== caregiver cancel ========


def test_caregiver_cancel_sets_cancelled_fields_with_the_caregiver_sub(aws):
    put_alert(aws)

    assert run(caregiver_cancel_body()) == {"batchItemFailures": []}

    item = get_item(aws)
    assert item["status"] == "cancelled"
    assert item["cancelled_at"] == CHANGED_AT
    assert item["cancelled_by_role"] == "caregiver"
    assert item["cancelled_by"] == CAREGIVER_SUB
    assert item["expires_at"] == EXPIRES_AT
    assert "open_pk" not in item
    assert item["status_notified_at"] == T0_ISO


def test_caregiver_cancel_never_stores_the_name(aws):
    put_alert(aws)

    run(caregiver_cancel_body())

    stored = repr(get_item(aws))
    assert CAREGIVER_NAME not in stored
    assert CAREGIVER_MASKED not in stored


def test_resident_cancel_stores_no_cancelled_by(aws):
    put_alert(aws)

    run(cancel_body())

    item = get_item(aws)
    assert item["cancelled_by_role"] == "resident"
    assert "cancelled_by" not in item


def test_caregiver_cancel_emails_caregivers_with_the_masked_name(aws):
    put_alert(aws)

    run(caregiver_cancel_body())

    assert aws.alert_publishes() == [
        {
            "TopicArn": aws.topic_arn,
            "Subject": "SilverCare update: Fall - res-0003 cancelled",
            "Message": CAREGIVER_CANCEL_EMAIL,
            "MessageAttributes": ALERTS_FILTER,
        }
    ]
    assert CAREGIVER_NAME not in CAREGIVER_CANCEL_EMAIL


def test_caregiver_cancel_of_unescalated_unacknowledged_alert_skips_nurses(aws):
    put_alert(aws, vitals_body("hr_high", "warning"))

    run(caregiver_cancel_body())

    assert aws.nurse_publishes() == []
    assert len(aws.alert_publishes()) == 1


def test_caregiver_cancel_of_escalated_alert_also_emails_nurses(aws):
    put_alert(aws, escalated_at=ESCALATED_AT)

    run(caregiver_cancel_body())

    assert aws.nurse_publishes() == [
        {
            "TopicArn": aws.nurses_topic_arn,
            "Subject": "SilverCare update: Fall - res-0003 cancelled",
            "Message": CAREGIVER_CANCEL_EMAIL,
        }
    ]


@pytest.mark.parametrize("status", ["cancelled", "resolved"])
def test_caregiver_cancel_of_cancelled_or_resolved_alert_is_not_open(aws, logs, status):
    put_alert(aws, status=status, open_pk=None, status_notified_at=T0_ISO)

    assert run(caregiver_cancel_body()) == {"batchItemFailures": []}

    item = get_item(aws)
    assert item["status"] == status
    assert "cancelled_by" not in item
    assert aws.sns.publishes == []
    (rec,) = status_records(logs)
    assert rec["outcome"] == "not_open"


def test_caregiver_cancel_of_missing_alert_is_not_open(aws, logs):
    assert run(caregiver_cancel_body()) == {"batchItemFailures": []}

    assert aws.sns.publishes == []
    (rec,) = status_records(logs)
    assert rec["outcome"] == "not_open"


def test_caregiver_cancel_changes_only_the_named_alert(aws):
    put_alert(aws)
    put_alert(aws, alert_body(alert_id=ALERT_ID_2, event_ts=EVENT_TS_2))

    run(caregiver_cancel_body())

    assert get_item(aws)["status"] == "cancelled"
    assert get_item(aws, alert_id=ALERT_ID_2, event_ts=EVENT_TS_2)["status"] == "open"


# ======== caregiver cancel: retries (criterion 11) ========


def _cancelled_by(role: str, by: str | None = None) -> dict:
    attrs = {
        "status": "cancelled",
        "cancelled_at": CHANGED_AT,
        "cancelled_by_role": role,
        "expires_at": EXPIRES_AT,
        "open_pk": None,
    }
    if by is not None:
        attrs["cancelled_by"] = by
    return attrs


def test_caregiver_cancel_retry_by_the_same_caregiver_sends_the_follow_up(aws, logs):
    put_alert(aws, **_cancelled_by("caregiver", CAREGIVER_SUB))

    run(caregiver_cancel_body())

    (pub,) = aws.alert_publishes()
    assert pub["Message"] == CAREGIVER_CANCEL_EMAIL
    assert get_item(aws)["status_notified_at"] == T0_ISO
    (rec,) = status_records(logs)
    assert rec["outcome"] == "updated"


def test_caregiver_cancel_after_another_caregivers_unnotified_one_sends_nothing(
    aws, logs
):
    put_alert(aws, **_cancelled_by("caregiver", OTHER_SUB))

    run(caregiver_cancel_body())

    assert aws.sns.publishes == []
    assert "status_notified_at" not in get_item(aws)
    (rec,) = status_records(logs)
    assert rec["outcome"] == "not_open"


def test_caregiver_cancel_after_an_unnotified_resident_cancel_sends_nothing(aws, logs):
    put_alert(aws, **_cancelled_by("resident"))

    run(caregiver_cancel_body())

    assert aws.sns.publishes == []
    item = get_item(aws)
    assert item["cancelled_by_role"] == "resident"
    assert "status_notified_at" not in item
    (rec,) = status_records(logs)
    assert rec["outcome"] == "not_open"


# ======== cancel of an acknowledged alert (criteria 10-11, 13, 15) ========


@pytest.mark.parametrize(
    "body", [cancel_body(), caregiver_cancel_body()], ids=["device", "alert"]
)
def test_cancel_of_acknowledged_alert_keeps_the_acknowledge_fields(aws, body):
    put_acknowledged(aws)

    assert run(body) == {"batchItemFailures": []}

    item = get_item(aws)
    assert item["status"] == "cancelled"
    assert item["acknowledged_at"] == ACK_AT
    assert item["acknowledged_by"] == NURSE_SUB
    assert item["ack_note"] == NOTE
    assert item["cancelled_at"] == CHANGED_AT
    assert item["expires_at"] == EXPIRES_AT
    assert "open_pk" not in item
    # The cancel's own follow-up time, not the acknowledge's.
    assert item["status_notified_at"] == T0_ISO


@pytest.mark.parametrize(
    "body", [cancel_body(), caregiver_cancel_body()], ids=["device", "alert"]
)
def test_cancel_of_acknowledged_alert_emails_nurses_without_escalated_at(aws, body):
    put_acknowledged(aws, escalated_at=None)

    run(body)

    (caregivers,) = aws.alert_publishes()
    (nurses,) = aws.nurse_publishes()
    assert nurses["Message"] == caregivers["Message"]
    assert nurses["Subject"] == "SilverCare update: Fall - res-0003 cancelled"
    assert "MessageAttributes" not in nurses


def test_caregiver_cancel_of_acknowledged_alert_body_is_exact(aws):
    put_acknowledged(aws)

    run(caregiver_cancel_body())

    (pub,) = aws.alert_publishes()
    assert pub["Message"] == email(
        f"The alert below was cancelled by {CAREGIVER_MASKED} (caregiver) at"
        " 2026-10-18 11:15:00 SGT.",
        ACKED_LINE,
        "",
        *ALERT_LINES,
    )


def test_resident_cancel_of_acknowledged_alert_body_is_exact(aws):
    put_acknowledged(aws)

    run(cancel_body())

    (pub,) = aws.nurse_publishes()
    assert pub["Message"] == email(
        "The alert below was cancelled by the resident at 2026-10-18 11:15:00 SGT.",
        ACKED_LINE,
        "",
        *ALERT_LINES,
    )


@pytest.mark.parametrize(
    "body", [cancel_body(), caregiver_cancel_body()], ids=["device", "alert"]
)
def test_cancel_of_acknowledged_alert_never_emails_the_note(aws, body):
    put_acknowledged(aws)

    run(body)

    for pub in aws.sns.publishes:
        assert NOTE not in pub["Message"]
        assert "Message from the care centre" not in pub["Message"]


def test_cancel_of_acknowledged_alert_removes_the_acknowledges_status_notified_at(
    aws, monkeypatch
):
    """If the cancel's follow-up fails, the item has no status_notified_at, so the
    retry sends it (the acknowledge's value would have hidden it)."""
    put_acknowledged(aws)
    with monkeypatch.context() as m:
        spy(m, "sns_client", [], fail_on="publish")
        result = run(caregiver_cancel_body())
    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}
    item = get_item(aws)
    assert item["status"] == "cancelled"
    assert "status_notified_at" not in item

    assert run(caregiver_cancel_body()) == {"batchItemFailures": []}

    assert len(aws.alert_publishes()) == 1
    assert len(aws.nurse_publishes()) == 1
    assert get_item(aws)["status_notified_at"] == T0_ISO


def test_cancel_of_acknowledged_alert_order_is_get_update_two_publishes_update(
    aws, monkeypatch
):
    put_acknowledged(aws)
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)
    spy(monkeypatch, "sns_client", events)

    run(caregiver_cancel_body())

    assert [op for op, _ in events] == [
        "get_item",
        "update_item",
        "publish",
        "publish",
        "update_item",
    ]


def test_unassigned_cancel_of_acknowledged_alert_emails_only_nurses(aws):
    put_alert(
        aws,
        alert_body(resident_id="unassigned"),
        status="acknowledged",
        acknowledged_at=ACK_AT,
        acknowledged_by=NURSE_SUB,
        status_notified_at=ACK_NOTIFIED_AT,
    )

    run(cancel_body(resident_id="unassigned"))

    assert aws.alert_publishes() == []
    (nurse,) = aws.nurse_publishes()
    assert "\nResident: unassigned\nDevice: wearable-0007\n" in nurse["Message"]
    assert ACKED_LINE in nurse["Message"]


# ======== logs (criterion 12) ========


@pytest.mark.parametrize(("note", "has_note"), [(NOTE, True), (None, False)])
def test_acknowledge_log_line_has_has_note_and_no_other_new_field(
    aws, logs, note, has_note
):
    put_alert(aws)

    run(nurse_ack_body(note=note))

    (rec,) = status_records(logs)
    assert set(rec) - POWERTOOLS_KEYS == ITEM_LINE_KEYS | {"has_note"}
    assert rec["has_note"] is has_note
    assert rec["by_role"] == "nurse"
    assert rec["new_status"] == "acknowledged"
    assert rec["alert_id"] == ALERT_ID
    assert rec["device_id"] == DEVICE


def test_acknowledge_not_open_log_line_has_has_note(aws, logs):
    run(nurse_ack_body(note=NOTE))

    (rec,) = status_records(logs)
    assert set(rec) - POWERTOOLS_KEYS == {
        "alert_id",
        "resident_id",
        "kind",
        "new_status",
        "by_role",
        "has_note",
        "outcome",
    }
    assert rec["has_note"] is True
    assert rec["outcome"] == "not_open"


@pytest.mark.parametrize(
    "body", [cancel_body(), caregiver_cancel_body()], ids=["resident", "caregiver"]
)
def test_cancel_log_line_has_no_has_note(aws, logs, body):
    put_alert(aws)

    run(body)

    (rec,) = status_records(logs)
    assert set(rec) - POWERTOOLS_KEYS == ITEM_LINE_KEYS
    assert rec["by_role"] in {"resident", "caregiver"}


def test_caregiver_cancel_log_line_says_caregiver(aws, logs):
    put_alert(aws)

    run(caregiver_cancel_body())

    (rec,) = status_records(logs)
    assert rec["by_role"] == "caregiver"
    assert rec["outcome"] == "updated"


@pytest.mark.parametrize(
    "body",
    [nurse_ack_body(note=NOTE), caregiver_cancel_body(), cancel_body()],
    ids=["ack", "caregiver-cancel", "resident-cancel"],
)
def test_status_update_log_records_never_have_note_by_or_by_name_keys(aws, logs, body):
    if "cancelled" in body:
        put_acknowledged(aws)
    else:
        put_alert(aws)

    run(body)

    for rec in logs():
        assert not {"note", "ack_note", "by", "by_name"} & set(rec)
        assert NOTE not in rec.values()
        assert CAREGIVER_NAME not in rec.values()
