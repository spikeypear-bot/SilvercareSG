"""Possible falls in alert-processor (docs/features/possible-fall.md, criteria 7-13
and 17): validation, routing to caregivers and nurses at once without a repeat check
or fall enrichment, the emails, the back-in-bed resolve and its follow-up, the
role/type rule, "I'm OK" cancelling the resident's possible falls, and logs.

The bed was vacated at 2026-11-04 02:10:00 SGT; the watchdog raised it at 02:30.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from conftest import LATEST_TABLE
from events import (
    ALERT_ID,
    ALERT_ID_2,
    ALERT_ID_3,
    CAREGIVER_MASKED,
    CAREGIVER_NAME,
    DEVICE,
    OPS_SUB,
    PF_BED_ID,
    PF_CHANGED_AT,
    PF_DEVICE,
    PF_DEVICE_2,
    PF_EVENT_TS,
    PF_RECEIVED_AT,
    RESIDENT,
    alert_body,
    back_in_bed_body,
    back_in_bed_message,
    cancel_body,
    cancel_message,
    caregiver_ack_body,
    caregiver_cancel_body,
    caregiver_cancel_message,
    device_problem_body,
    nurse_ack_body,
    nurse_ack_message,
    ops_ack_body,
    possible_fall_body,
    resolve_body,
)
from message import Alert, Rejection, StatusUpdate, parse_message
from test_status_update import (  # noqa: F401 - clock is an autouse fixture
    clock,
    get_item,
    put_alert,
    run,
    spy,
    status_records,
)

NOW = datetime(2026, 11, 3, 18, 30, 1, 0, tzinfo=UTC)
NOW_ISO = "2026-11-03T18:30:01.000Z"
RESIDENT_B = "res-0004"
OTHER_WEARABLE = "wearable-0008"
PF_TARGET = {"alert_id": ALERT_ID, "event_ts": PF_EVENT_TS}
ESCALATED_AT = "2026-11-03T18:30:00.500Z"
NOTIFIED_AT = "2026-11-03T18:30:00.400Z"
ACK_AT = "2026-11-03T18:35:00.000Z"
NURSE_SUB = "a1b2c3d4-0000-4000-8000-00000000abcd"
RESOLVE_EXPIRES = int(
    (datetime(2026, 11, 3, 18, 45, tzinfo=UTC) + timedelta(days=30)).timestamp()
)
ALERTS_FILTER = {"resident_id": {"DataType": "String", "StringValue": RESIDENT}}
SUBJECT = f"SilverCare CRITICAL alert: Possible fall - {RESIDENT}"
RESOLVED_SUBJECT = f"SilverCare update: Possible fall - {RESIDENT} resolved"
CANCELLED_SUBJECT = f"SilverCare update: Possible fall - {RESIDENT} cancelled"
FOOTER = (
    "Open the SilverCare portal: https://d0000example.cloudfront.net/\n"
    "\n"
    "This is an automated message. Do not reply.\n"
)
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@pytest.fixture(autouse=True)
def now(clock):  # noqa: F811 - the imported fixture
    clock["now"] = NOW
    return clock


def caregiver_email(wearable_line: str = "Wearable: not worn", extra: str = "") -> str:
    return (
        "SilverCare alert\n"
        "\n"
        "Type: Possible fall\n"
        "Severity: Critical\n"
        f"Resident: {RESIDENT}\n"
        f"Device: {PF_DEVICE}\n"
        "When: 2026-11-04 02:10:00 SGT\n"
        "Out of bed since: 2026-11-04 02:10:00 SGT\n"
        f"{wearable_line}\n"
        f"{extra}"
        "\n"
        "The care centre nurse has also been alerted.\n"
        "\n" + FOOTER
    )


NURSE_EMAIL = (
    "SilverCare alert\n"
    "\n"
    "Type: Possible fall\n"
    "Severity: Critical\n"
    "Sent to the care centre at once: critical alert.\n"
    f"Resident: {RESIDENT}\n"
    f"Device: {PF_DEVICE}\n"
    "When: 2026-11-04 02:10:00 SGT\n"
    "Out of bed since: 2026-11-04 02:10:00 SGT\n"
    "Wearable: not worn\n"
    "\n" + FOOTER
)

RESOLVED_EMAIL = (
    "SilverCare update\n"
    "\n"
    "The alert below was resolved at 2026-11-04 02:45:00 SGT: the resident is back"
    " in bed.\n"
    "\n"
    "Type: Possible fall\n"
    "Severity: Critical\n"
    f"Resident: {RESIDENT}\n"
    "When: 2026-11-04 02:10:00 SGT\n"
    "\n" + FOOTER
)


def pf_item(aws, resident_id=RESIDENT, alert_id=ALERT_ID, event_ts=PF_EVENT_TS):
    return get_item(aws, resident_id=resident_id, event_ts=event_ts, alert_id=alert_id)


def put_pf(aws, body: str | None = None, **attrs) -> dict:
    """A stored possible fall (open, both emails sent), plus `attrs`."""
    attrs = {"notified_at": NOTIFIED_AT, "escalated_at": ESCALATED_AT, **attrs}
    return put_alert(aws, body or possible_fall_body(), **attrs)


def alert_lines(logs) -> list[dict]:
    return [r for r in logs() if r.get("message") == "alert" and "kind" not in r]


# ======== validation (criterion 9) ========


@pytest.mark.parametrize("wearable", ["not_worn", "no_recent_status", "no_wearable"])
def test_possible_fall_with_each_wearable_state_is_valid(wearable):
    body = possible_fall_body(
        details={"out_of_bed_since": PF_EVENT_TS, "wearable": wearable}
    )

    result = parse_message(body)

    assert isinstance(result, Alert), result
    assert result.details["wearable"] == wearable


def test_possible_fall_with_test_mode_true_is_valid():
    details = {"out_of_bed_since": PF_EVENT_TS, "wearable": "no_wearable"}
    details["test_mode"] = True

    result = parse_message(possible_fall_body(details=details))

    assert isinstance(result, Alert), result
    assert result.details["test_mode"] is True


def test_possible_fall_warning_is_rejected():
    assert parse_message(possible_fall_body(severity="warning")) == Rejection(
        "invalid:severity"
    )


def pf_details(**changes) -> dict:
    details = {"out_of_bed_since": PF_EVENT_TS, "wearable": "not_worn"}
    for name, value in changes.items():
        if value is None:
            details.pop(name)
        else:
            details[name] = value
    return details


@pytest.mark.parametrize(
    ("details", "reason"),
    [
        (pf_details(out_of_bed_since=None), "missing:details.out_of_bed_since"),
        (pf_details(wearable=None), "missing:details.wearable"),
        (
            pf_details(out_of_bed_since=1793722200000),
            "wrong_type:details.out_of_bed_since",
        ),
        (
            pf_details(out_of_bed_since="2026-11-03T18:10:00Z"),
            "invalid:details.out_of_bed_since",
        ),
        (
            pf_details(out_of_bed_since="yesterday night"),
            "invalid:details.out_of_bed_since",
        ),
        (pf_details(wearable=False), "wrong_type:details.wearable"),
        (pf_details(wearable="worn"), "invalid:details.wearable"),
        (pf_details(wearable="Not_Worn"), "invalid:details.wearable"),
        (pf_details(test_mode=False), "invalid:details.test_mode"),
        (pf_details(test_mode="true"), "wrong_type:details.test_mode"),
        (pf_details(test_mode=1), "wrong_type:details.test_mode"),
        (pf_details(confidence=0.9), "unknown_field:details"),
        (pf_details(grouped=True), "unknown_field:details"),
        (pf_details(latest_vitals={"hr_bpm": 70}), "unknown_field:details"),
        ([], "wrong_type:details"),
    ],
)
def test_invalid_possible_fall_details_are_rejected(details, reason):
    assert parse_message(possible_fall_body(details=details)) == Rejection(reason)


def test_invalid_possible_fall_is_not_stored_or_emailed(aws):
    # Invalid bodies are reported as failures, like every other invalid body.
    assert run(possible_fall_body(severity="warning")) == {
        "batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]
    }

    assert pf_item(aws) is None
    assert aws.sns.publishes == []


# ======== reason (criterion 7) ========


def test_system_resolve_with_reason_back_in_bed_is_valid():
    result = parse_message(back_in_bed_body())

    assert isinstance(result, StatusUpdate), result
    assert result.reason == "back_in_bed"


def test_system_resolve_without_reason_is_valid_with_no_reason():
    result = parse_message(resolve_body())

    assert isinstance(result, StatusUpdate), result
    assert result.reason is None


@pytest.mark.parametrize(
    "reason",
    ["back-in-bed", "BACK_IN_BED", "", "other", None, 1, True, ["back_in_bed"]],
)
def test_system_resolve_with_another_reason_is_rejected(reason):
    body = json.dumps(back_in_bed_message(reason=reason))

    assert parse_message(body) == Rejection("invalid:reason")


@pytest.mark.parametrize(
    "build",
    [
        lambda: cancel_message(),
        lambda: caregiver_cancel_message(),
        lambda: nurse_ack_message(),
        lambda: nurse_ack_message(note="Synthetic note"),
    ],
    ids=["resident-cancel", "caregiver-cancel", "nurse-ack", "nurse-ack-note"],
)
def test_reason_on_a_non_system_update_is_rejected(build):
    body = build()
    body["reason"] = "back_in_bed"

    assert parse_message(json.dumps(body)) == Rejection("invalid:reason")


def test_reason_on_an_ops_acknowledge_is_rejected():
    body = json.loads(ops_ack_body())
    body["reason"] = "back_in_bed"

    assert parse_message(json.dumps(body)) == Rejection("invalid:reason")


# ======== routing (criterion 10) ========


def test_possible_fall_is_stored_open_and_critical(aws):
    assert run(possible_fall_body()) == {"batchItemFailures": []}

    item = pf_item(aws)
    assert item["alert_type"] == "possible_fall"
    assert item["severity"] == "critical"
    assert item["status"] == "open"
    assert item["open_pk"] == "OPEN"
    assert item["device_id"] == PF_DEVICE
    assert item["source_message_id"] == PF_BED_ID
    assert item["details"] == {"out_of_bed_since": PF_EVENT_TS, "wearable": "not_worn"}
    assert item["notified_at"] == NOW_ISO
    assert item["escalated_at"] == NOW_ISO


def test_possible_fall_emails_caregivers_and_nurses_at_once(aws):
    run(possible_fall_body())

    assert aws.alert_publishes() == [
        {
            "TopicArn": aws.topic_arn,
            "Subject": SUBJECT,
            "Message": caregiver_email(),
            "MessageAttributes": ALERTS_FILTER,
        }
    ]
    assert aws.nurse_publishes() == [
        {"TopicArn": aws.nurses_topic_arn, "Subject": SUBJECT, "Message": NURSE_EMAIL}
    ]


def test_possible_fall_queues_no_escalation_check(aws):
    run(possible_fall_body())

    assert aws.sqs.sends == []
    assert "escalation_queued_at" not in pf_item(aws)


def test_possible_fall_is_never_emailed_to_ops(aws):
    run(possible_fall_body())

    assert aws.ops_publishes() == []


def test_second_possible_fall_10_minutes_later_is_not_suppressed(aws):
    later = "2026-11-03T18:20:00.000Z"
    run(possible_fall_body())
    run(
        possible_fall_body(
            alert_id=ALERT_ID_2,
            event_ts=later,
            details={"out_of_bed_since": later, "wearable": "not_worn"},
        )
    )

    assert pf_item(aws, alert_id=ALERT_ID_2, event_ts=later)["status"] == "open"
    assert len(aws.alert_publishes()) == 2
    assert len(aws.nurse_publishes()) == 2


def test_possible_fall_has_no_repeat_query_and_no_latest_vitals_read(aws, monkeypatch):
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)

    run(possible_fall_body())

    assert "query" not in [op for op, _ in events]
    assert all(kw.get("TableName") != LATEST_TABLE for _, kw in events)
    assert "latest_vitals" not in pf_item(aws)["details"]


def test_possible_fall_log_line_has_nurses_immediate_and_no_details(aws, logs):
    run(possible_fall_body())

    (line,) = alert_lines(logs)
    assert line["alert_type"] == "possible_fall"
    assert line["severity"] == "critical"
    assert line["device_id"] == PF_DEVICE
    assert line["nurses"] == "immediate"
    assert line["outcome"] == "notified"


# ======== emails (criterion 11) ========


@pytest.mark.parametrize(
    ("wearable", "line"),
    [
        ("not_worn", "Wearable: not worn"),
        ("no_recent_status", "Wearable: no recent status"),
        ("no_wearable", "Wearable: none assigned"),
    ],
)
def test_email_wearable_line_per_state(aws, wearable, line):
    details = {"out_of_bed_since": PF_EVENT_TS, "wearable": wearable}

    run(possible_fall_body(details=details))

    (caregivers,) = aws.alert_publishes()
    assert caregivers["Message"] == caregiver_email(line)
    (nurses,) = aws.nurse_publishes()
    assert f"\n{line}\n" in nurses["Message"]


def test_test_mode_email_has_the_test_mode_line(aws):
    details = {"out_of_bed_since": PF_EVENT_TS, "wearable": "not_worn"}
    details["test_mode"] = True

    run(possible_fall_body(details=details))

    (caregivers,) = aws.alert_publishes()
    assert caregivers["Message"] == caregiver_email(extra="Test mode: yes\n")
    (nurses,) = aws.nurse_publishes()
    assert "\nWearable: not worn\nTest mode: yes\n" in nurses["Message"]


def test_email_without_test_mode_has_no_test_mode_line(aws):
    run(possible_fall_body())

    for publish in aws.sns.publishes:
        assert "Test mode" not in publish["Message"]


def test_out_of_bed_since_is_shown_from_details_not_event_ts(aws):
    details = {"out_of_bed_since": "2026-11-03T18:05:00.000Z", "wearable": "not_worn"}

    run(possible_fall_body(details=details))

    (caregivers,) = aws.alert_publishes()
    assert "Out of bed since: 2026-11-04 02:05:00 SGT\n" in caregivers["Message"]
    assert "When: 2026-11-04 02:10:00 SGT\n" in caregivers["Message"]


def test_email_has_no_readings(aws):
    run(possible_fall_body())

    for publish in aws.sns.publishes:
        for text in ("Reading", "Latest vitals", "Fall details", "bpm"):
            assert text not in publish["Message"], text


# ======== back in bed (criteria 7-8) ========


def test_back_in_bed_resolves_an_open_possible_fall(aws):
    put_pf(aws)

    assert run(back_in_bed_body()) == {"batchItemFailures": []}

    item = pf_item(aws)
    assert item["status"] == "resolved"
    assert item["resolved_at"] == PF_CHANGED_AT
    assert item["resolved_by_role"] == "system"
    assert item["expires_at"] == RESOLVE_EXPIRES
    assert item["status_notified_at"] == NOW_ISO
    assert "open_pk" not in item


def test_back_in_bed_follow_up_goes_to_caregivers_and_escalated_nurses(aws):
    put_pf(aws)

    run(back_in_bed_body())

    assert aws.alert_publishes() == [
        {
            "TopicArn": aws.topic_arn,
            "Subject": RESOLVED_SUBJECT,
            "Message": RESOLVED_EMAIL,
            "MessageAttributes": ALERTS_FILTER,
        }
    ]
    assert aws.nurse_publishes() == [
        {
            "TopicArn": aws.nurses_topic_arn,
            "Subject": RESOLVED_SUBJECT,
            "Message": RESOLVED_EMAIL,
        }
    ]
    assert aws.ops_publishes() == []


def test_back_in_bed_without_escalated_at_emails_only_caregivers(aws):
    put_pf(aws, escalated_at=None)

    run(back_in_bed_body())

    assert len(aws.alert_publishes()) == 1
    assert aws.nurse_publishes() == []


def test_back_in_bed_resolves_an_acknowledged_possible_fall(aws):
    put_pf(
        aws,
        status="acknowledged",
        acknowledged_at=ACK_AT,
        acknowledged_by=NURSE_SUB,
        status_notified_at="2026-11-03T18:35:01.000Z",
    )

    run(back_in_bed_body())

    item = pf_item(aws)
    assert item["status"] == "resolved"
    assert item["resolved_by_role"] == "system"
    assert item["acknowledged_at"] == ACK_AT
    assert "open_pk" not in item
    assert len(aws.alert_publishes()) == 1
    assert len(aws.nurse_publishes()) == 1


def test_back_in_bed_retry_sends_the_missing_follow_up(aws):
    put_pf(
        aws,
        status="resolved",
        resolved_at=PF_CHANGED_AT,
        resolved_by_role="system",
        expires_at=RESOLVE_EXPIRES,
        open_pk=None,
    )

    run(back_in_bed_body())

    assert len(aws.alert_publishes()) == 1
    assert len(aws.nurse_publishes()) == 1
    assert pf_item(aws)["status_notified_at"] == NOW_ISO


def test_back_in_bed_after_the_follow_up_was_sent_is_not_open(aws, logs):
    put_pf(
        aws,
        status="resolved",
        resolved_by_role="system",
        status_notified_at="2026-11-03T18:45:01.000Z",
        open_pk=None,
    )

    run(back_in_bed_body())

    assert aws.sns.publishes == []
    assert status_records(logs)[-1]["outcome"] == "not_open"


def test_resolved_item_not_resolved_by_the_system_is_not_a_retry(aws, logs):
    put_pf(aws, status="resolved", resolved_by_role="nurse", open_pk=None)

    run(back_in_bed_body())

    assert aws.sns.publishes == []
    assert "status_notified_at" not in pf_item(aws)
    assert status_records(logs)[-1]["outcome"] == "not_open"


def test_back_in_bed_of_a_cancelled_possible_fall_changes_nothing(aws, logs):
    put_pf(aws, status="cancelled", status_notified_at=ACK_AT, open_pk=None)
    before = pf_item(aws)

    run(back_in_bed_body())

    assert pf_item(aws) == before
    assert aws.sns.publishes == []
    assert status_records(logs)[-1]["outcome"] == "not_open"


def test_second_back_in_bed_resolve_sends_nothing_more(aws):
    put_pf(aws)
    run(back_in_bed_body())
    run(back_in_bed_body(changed_at="2026-11-03T18:50:00.000Z"))

    assert len(aws.alert_publishes()) == 1
    assert pf_item(aws)["resolved_at"] == PF_CHANGED_AT


def test_failed_follow_up_fails_the_record_and_the_retry_sends_it(aws, monkeypatch):
    put_pf(aws)
    events: list = []
    spy(monkeypatch, "sns_client", events, fail_on="publish", fail_nth=1)

    first = run(back_in_bed_body())

    assert first == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}
    assert pf_item(aws)["status"] == "resolved"
    assert "status_notified_at" not in pf_item(aws)

    assert run(back_in_bed_body()) == {"batchItemFailures": []}
    assert len(aws.alert_publishes()) == 1
    assert pf_item(aws)["status_notified_at"] == NOW_ISO


# ======== role/type rule (criterion 12) ========


def assert_not_allowed(aws, logs, before, read) -> None:
    assert read() == before
    assert aws.sns.publishes == []
    assert aws.sqs.sends == []
    (line,) = status_records(logs)
    assert line["outcome"] == "not_allowed"


@pytest.mark.parametrize(
    "build",
    [
        lambda: resolve_body(target=PF_TARGET),
        lambda: ops_ack_body(target=PF_TARGET),
        lambda: caregiver_ack_body(target=PF_TARGET),
    ],
    ids=["system-resolve-without-reason", "ops-acknowledge", "caregiver-acknowledge"],
)
def test_disallowed_update_of_a_possible_fall_is_not_allowed(aws, logs, build):
    put_pf(aws)
    before = pf_item(aws)

    assert run(build()) == {"batchItemFailures": []}

    assert_not_allowed(aws, logs, before, lambda: pf_item(aws))


@pytest.mark.parametrize("alert_type", ["device_offline", "battery_low", "not_worn"])
def test_back_in_bed_resolve_of_a_device_problem_is_not_allowed(aws, logs, alert_type):
    from events import DEVICE_PROBLEM_DETAILS, DP_EVENT_TS

    put_alert(
        aws,
        device_problem_body(
            alert_type, details=dict(DEVICE_PROBLEM_DETAILS[alert_type])
        ),
    )
    target = {"alert_id": ALERT_ID, "event_ts": DP_EVENT_TS}
    before = get_item(aws, event_ts=DP_EVENT_TS)

    run(back_in_bed_body(target=target))

    assert_not_allowed(aws, logs, before, lambda: get_item(aws, event_ts=DP_EVENT_TS))


def test_back_in_bed_resolve_of_a_fall_is_not_allowed(aws, logs):
    from events import EVENT_TS

    put_alert(aws, alert_body())
    before = get_item(aws)

    run(back_in_bed_body(target={"alert_id": ALERT_ID, "event_ts": EVENT_TS}))

    assert_not_allowed(aws, logs, before, lambda: get_item(aws))


def test_nurse_acknowledge_of_a_possible_fall_is_allowed(aws):
    put_pf(aws)

    run(nurse_ack_body(target=PF_TARGET, changed_at=ACK_AT))

    item = pf_item(aws)
    assert item["status"] == "acknowledged"
    assert item["open_pk"] == "OPEN"
    (caregivers,) = aws.alert_publishes()
    assert caregivers["Subject"] == (
        f"SilverCare update: Possible fall - {RESIDENT} acknowledged"
    )


def test_caregiver_cancel_of_a_possible_fall_is_allowed(aws):
    put_pf(aws)

    run(caregiver_cancel_body(target=PF_TARGET, changed_at=ACK_AT))

    item = pf_item(aws)
    assert item["status"] == "cancelled"
    assert item["cancelled_by_role"] == "caregiver"
    (caregivers,) = aws.alert_publishes()
    (nurses,) = aws.nurse_publishes()
    assert caregivers["Subject"] == nurses["Subject"] == CANCELLED_SUBJECT
    assert f"{CAREGIVER_MASKED} (caregiver)" in caregivers["Message"]
    assert CAREGIVER_NAME not in caregivers["Message"]


# ======== "I'm OK" (criterion 13) ========


def im_ok(device_id: str = DEVICE, resident_id: str = RESIDENT) -> str:
    return cancel_body(
        changed_at=PF_CHANGED_AT,
        resident_id=resident_id,
        target={"device_id": device_id},
    )


def test_im_ok_cancels_the_resident_possible_fall_from_the_bedroom_unit(aws):
    put_pf(aws)

    assert run(im_ok()) == {"batchItemFailures": []}

    item = pf_item(aws)
    assert item["status"] == "cancelled"
    assert item["cancelled_by_role"] == "resident"
    assert "open_pk" not in item
    (caregivers,) = aws.alert_publishes()
    (nurses,) = aws.nurse_publishes()
    assert caregivers["Subject"] == nurses["Subject"] == CANCELLED_SUBJECT
    assert caregivers["Message"].startswith(
        "SilverCare update\n\nThe alert below was cancelled by the resident at"
        " 2026-11-04 02:45:00 SGT.\n"
    )


def test_im_ok_cancels_an_acknowledged_possible_fall(aws):
    put_pf(
        aws,
        status="acknowledged",
        acknowledged_at=ACK_AT,
        acknowledged_by=NURSE_SUB,
        status_notified_at="2026-11-03T18:35:01.000Z",
    )

    run(im_ok())

    item = pf_item(aws)
    assert item["status"] == "cancelled"
    assert item["acknowledged_at"] == ACK_AT


def test_im_ok_cancels_the_wearable_alert_and_the_possible_fall_together(aws):
    put_pf(aws)
    fall_ts = "2026-11-03T18:40:00.000Z"
    put_alert(aws, alert_body(alert_id=ALERT_ID_2, event_ts=fall_ts))

    run(im_ok())

    assert pf_item(aws)["status"] == "cancelled"
    assert get_item(aws, event_ts=fall_ts, alert_id=ALERT_ID_2)["status"] == "cancelled"
    assert len(aws.alert_publishes()) == 2


def test_im_ok_leaves_another_resident_possible_fall_untouched(aws):
    put_pf(aws, possible_fall_body(resident_id=RESIDENT_B, device_id=PF_DEVICE_2))
    before = pf_item(aws, resident_id=RESIDENT_B)

    run(im_ok())

    assert pf_item(aws, resident_id=RESIDENT_B) == before
    assert before["status"] == "open"
    assert aws.sns.publishes == []


def test_im_ok_of_resident_a_leaves_resident_b_open_and_cancels_only_a(aws):
    put_pf(aws)
    put_pf(aws, possible_fall_body(resident_id=RESIDENT_B, device_id=PF_DEVICE_2))

    run(im_ok())

    assert pf_item(aws)["status"] == "cancelled"
    other = pf_item(aws, resident_id=RESIDENT_B)
    assert other["status"] == "open"
    assert other["open_pk"] == "OPEN"
    assert "cancelled_at" not in other
    (caregivers,) = aws.alert_publishes()
    assert caregivers["MessageAttributes"] == ALERTS_FILTER


def test_im_ok_leaves_a_bedroom_device_problem_open(aws):
    put_pf(aws)
    dp_ts = "2026-11-03T18:00:00.000Z"
    put_alert(
        aws,
        device_problem_body(
            "device_offline",
            alert_id=ALERT_ID_3,
            device_id=PF_DEVICE,
            event_ts=dp_ts,
            details={"last_seen": dp_ts},
        ),
    )

    run(im_ok())

    assert get_item(aws, event_ts=dp_ts, alert_id=ALERT_ID_3)["status"] == "open"


def test_im_ok_does_not_cancel_another_wearable_fall(aws):
    fall_ts = "2026-11-03T18:40:00.000Z"
    put_alert(
        aws, alert_body(alert_id=ALERT_ID_2, event_ts=fall_ts, device_id=OTHER_WEARABLE)
    )

    run(im_ok())

    assert get_item(aws, event_ts=fall_ts, alert_id=ALERT_ID_2)["status"] == "open"


def test_possible_fall_after_the_im_ok_press_is_not_cancelled(aws):
    later = "2026-11-03T18:50:00.000Z"
    put_pf(
        aws,
        possible_fall_body(
            event_ts=later, details={"out_of_bed_since": later, "wearable": "not_worn"}
        ),
    )

    run(im_ok())

    assert pf_item(aws, event_ts=later)["status"] == "open"


def test_bedroom_target_cancel_does_not_cancel_another_unit_possible_fall(aws):
    put_pf(aws)

    run(im_ok(PF_DEVICE_2))

    assert pf_item(aws)["status"] == "open"
    assert aws.sns.publishes == []


def query_filter(monkeypatch, body: str) -> dict:
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)
    run(body)
    (query,) = [kw for op, kw in events if op == "query"]
    return query


def test_wearable_im_ok_query_also_matches_possible_falls(aws, monkeypatch):
    query = query_filter(monkeypatch, im_ok())

    assert "alert_type = :possible_fall" in query["FilterExpression"]
    assert query["ExpressionAttributeValues"][":possible_fall"] == {
        "S": "possible_fall"
    }
    assert query["ConsistentRead"] is True


def test_bedroom_target_query_is_not_extended_to_possible_falls(aws, monkeypatch):
    query = query_filter(monkeypatch, im_ok(PF_DEVICE))

    assert ":possible_fall" not in query["FilterExpression"]
    assert ":possible_fall" not in query["ExpressionAttributeValues"]


# ======== logs (privacy) ========


def test_logs_never_contain_times_wearable_state_details_or_reason(aws, logs):
    details = {"out_of_bed_since": PF_EVENT_TS, "wearable": "no_recent_status"}
    details["test_mode"] = True
    run(possible_fall_body(details=details))
    run(back_in_bed_body())
    put_pf(
        aws,
        possible_fall_body(
            alert_id=ALERT_ID_2,
            event_ts="2026-11-03T18:12:00.000Z",
            details={
                "out_of_bed_since": "2026-11-03T18:12:00.000Z",
                "wearable": "no_wearable",
            },
        ),
    )
    run(im_ok())
    run(back_in_bed_body(reason="other"))

    records = [
        {k: v for k, v in r.items() if k not in ("timestamp", "location")}
        for r in logs()
    ]
    assert len(records) >= 4
    text = json.dumps(records)
    for value in (ALERT_ID, ALERT_ID_2, PF_BED_ID, NURSE_SUB, OPS_SUB):
        text = text.replace(value, "<id>")
    assert not UUID.search(text)
    for value in (
        "out_of_bed_since",
        PF_EVENT_TS,
        PF_RECEIVED_AT,
        PF_CHANGED_AT,
        "2026-11-03T18:12",
        "2026",
        "details",
        'wearable"',
        "not_worn",
        "no_recent_status",
        "no_wearable",
        "worn",
        "test_mode",
        "back_in_bed",
        '"reason": "back',
        "Out of bed",
    ):
        assert value not in text, value
