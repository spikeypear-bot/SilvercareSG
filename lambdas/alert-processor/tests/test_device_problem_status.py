"""Status updates of device problems end to end against moto
(docs/features/watchdog.md, criteria 10, 11, 14a and 20): ops and caregiver
acknowledges with their follow-up, the system resolve, the role/type rule
(not_allowed), and "I'm OK" leaving device problems open."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from events import (
    ALERT_ID,
    ALERT_ID_2,
    ALERT_ID_3,
    CAREGIVER_MASKED,
    CAREGIVER_NAME,
    CAREGIVER_SUB,
    DEVICE,
    DEVICE_PROBLEM_TYPES,
    DP_CHANGED_AT,
    DP_EVENT_TS,
    EVENT_TS,
    OPS_SUB,
    RESIDENT,
    alert_body,
    cancel_body,
    caregiver_ack_body,
    caregiver_cancel_body,
    device_problem_body,
    nurse_ack_body,
    ops_ack_body,
    resolve_body,
    vitals_body,
)
from test_status_update import (  # noqa: F401 - clock is an autouse fixture
    POWERTOOLS_KEYS,
    T0_ISO,
    clock,
    get_item,
    put_alert,
    run,
    spy,
    status_records,
)

DP_TARGET = {"alert_id": ALERT_ID, "event_ts": DP_EVENT_TS}
HEALTH_TARGET = {"alert_id": ALERT_ID, "event_ts": EVENT_TS}
ACK_AT = "2026-11-04T02:03:00.000Z"
RESOLVE_EXPIRES = int(
    (datetime(2026, 11, 4, 2, 5, tzinfo=UTC) + timedelta(days=30)).timestamp()
)
ALERTS_FILTER = {"resident_id": {"DataType": "String", "StringValue": RESIDENT}}
LOW_BATTERY_LINES = (
    "Type: Low battery\n"
    f"Device: {DEVICE}\n"
    f"Resident: {RESIDENT}\n"
    "Battery: 15 %\n"
    "\n"
    "Open the SilverCare portal: https://d0000example.cloudfront.net/\n"
    "\n"
    "This is an automated message. Do not reply.\n"
)
ACK_SUBJECT = f"SilverCare update: Low battery - {DEVICE} ({RESIDENT}) acknowledged"


def ack_email(who: str) -> str:
    return (
        "SilverCare update\n"
        "\n"
        f"The device problem below was acknowledged by {who} at"
        " 2026-11-04 10:05:00 SGT.\n"
        "\n" + LOW_BATTERY_LINES
    )


def put_dp(aws, alert_type="battery_low", **attrs) -> dict:
    """A stored device problem (open, both emails sent), plus `attrs`."""
    return put_alert(
        aws,
        device_problem_body(alert_type),
        notified_at="2026-11-04T02:00:01.000Z",
        ops_notified_at="2026-11-04T02:00:01.100Z",
        **attrs,
    )


def dp_item(aws) -> dict:
    return get_item(aws, event_ts=DP_EVENT_TS)


def put_acked(aws, by=OPS_SUB, role="ops", **attrs) -> dict:
    return put_dp(
        aws,
        status="acknowledged",
        acknowledged_at=ACK_AT,
        acknowledged_by=by,
        acknowledged_by_role=role,
        **attrs,
    )


# ======== ops and caregiver acknowledge (criterion 14a) ========


def test_ops_acknowledge_sets_fields_and_keeps_open_pk(aws):
    put_dp(aws)

    assert run(ops_ack_body()) == {"batchItemFailures": []}

    item = dp_item(aws)
    assert item["status"] == "acknowledged"
    assert item["acknowledged_at"] == DP_CHANGED_AT
    assert item["acknowledged_by"] == OPS_SUB
    assert item["acknowledged_by_role"] == "ops"
    assert item["open_pk"] == "OPEN"
    assert item["status_notified_at"] == T0_ISO
    assert "ack_note" not in item
    assert "expires_at" not in item


def test_caregiver_acknowledge_sets_fields_with_role_caregiver(aws):
    put_dp(aws)

    run(caregiver_ack_body())

    item = dp_item(aws)
    assert item["status"] == "acknowledged"
    assert item["acknowledged_by"] == CAREGIVER_SUB
    assert item["acknowledged_by_role"] == "caregiver"
    assert item["open_pk"] == "OPEN"
    assert CAREGIVER_NAME not in str(item)


def test_ops_acknowledge_follow_up_goes_to_caregivers_and_ops(aws):
    put_dp(aws)

    run(ops_ack_body())

    (caregivers,) = aws.alert_publishes()
    (ops,) = aws.ops_publishes()
    assert caregivers["Subject"] == ops["Subject"] == ACK_SUBJECT
    assert caregivers["Message"] == ops["Message"] == ack_email("the ops team")
    assert caregivers["MessageAttributes"] == ALERTS_FILTER
    assert "MessageAttributes" not in ops


def test_caregiver_acknowledge_follow_up_names_the_masked_caregiver(aws):
    put_dp(aws)

    run(caregiver_ack_body())

    (caregivers,) = aws.alert_publishes()
    (ops,) = aws.ops_publishes()
    expected = ack_email(f"{CAREGIVER_MASKED} (caregiver)")
    assert caregivers["Message"] == ops["Message"] == expected
    assert CAREGIVER_NAME not in expected


@pytest.mark.parametrize("build", [ops_ack_body, caregiver_ack_body])
def test_device_problem_acknowledge_never_emails_the_nurses(aws, build):
    put_dp(aws, escalated_at="2026-11-04T02:00:02.000Z")

    run(build())

    assert aws.nurse_publishes() == []


@pytest.mark.parametrize("alert_type", DEVICE_PROBLEM_TYPES)
def test_each_device_problem_type_can_be_acknowledged_by_ops(aws, alert_type):
    put_dp(aws, alert_type)

    run(ops_ack_body())

    assert dp_item(aws)["status"] == "acknowledged"


def test_acknowledge_log_line_has_ids_role_and_no_sub_or_name(aws, logs):
    put_dp(aws)

    run(caregiver_ack_body())

    (line,) = status_records(logs)
    assert line["outcome"] == "updated"
    assert line["by_role"] == "caregiver"
    assert line["new_status"] == "acknowledged"
    assert line["has_note"] is False
    assert line["alert_type"] == "battery_low"
    text = str(logs())
    assert CAREGIVER_SUB not in text
    assert CAREGIVER_NAME not in text
    assert CAREGIVER_MASKED not in text


def test_acknowledge_of_an_acknowledged_device_problem_is_not_open(aws, logs):
    put_acked(aws, status_notified_at="2026-11-04T02:03:01.000Z")

    assert run(caregiver_ack_body()) == {"batchItemFailures": []}

    assert aws.sns.publishes == []
    assert dp_item(aws)["acknowledged_by"] == OPS_SUB
    assert status_records(logs)[-1]["outcome"] == "not_open"


def test_retry_of_the_same_acknowledge_sends_the_missing_follow_up(aws):
    put_acked(aws)

    run(ops_ack_body())

    item = dp_item(aws)
    assert item["acknowledged_at"] == ACK_AT
    assert item["status_notified_at"] == T0_ISO
    assert len(aws.alert_publishes()) == 1
    assert len(aws.ops_publishes()) == 1


def test_acknowledge_by_another_sub_is_not_a_retry(aws, logs):
    put_acked(aws, by="a1b2c3d4-0000-4000-8000-0000000000ff")

    run(ops_ack_body())

    assert aws.sns.publishes == []
    assert "status_notified_at" not in dp_item(aws)
    assert status_records(logs)[-1]["outcome"] == "not_open"


def test_acknowledge_by_the_same_sub_in_another_role_is_not_a_retry(aws, logs):
    put_acked(aws, by=OPS_SUB, role="ops")

    run(caregiver_ack_body(by=OPS_SUB))

    assert aws.sns.publishes == []
    assert status_records(logs)[-1]["outcome"] == "not_open"


def test_failed_ops_follow_up_fails_the_record_and_the_retry_completes(
    aws, monkeypatch
):
    put_dp(aws)
    events: list = []
    spy(monkeypatch, "sns_client", events, fail_on="publish", fail_nth=2)

    first = run(ops_ack_body())

    assert first == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}
    item = dp_item(aws)
    assert item["status"] == "acknowledged"
    assert "status_notified_at" not in item

    assert run(ops_ack_body()) == {"batchItemFailures": []}
    assert len(aws.ops_publishes()) == 1
    assert dp_item(aws)["status_notified_at"] == T0_ISO


def test_unassigned_ops_acknowledge_emails_only_ops(aws):
    put_alert(aws, device_problem_body(resident_id="unassigned"))

    run(ops_ack_body(resident_id="unassigned"))

    assert aws.alert_publishes() == []
    (ops,) = aws.ops_publishes()
    assert ops["Subject"] == (
        f"SilverCare update: Low battery - {DEVICE} (unassigned) acknowledged"
    )
    item = get_item(aws, resident_id="unassigned", event_ts=DP_EVENT_TS)
    assert item["status_notified_at"] == T0_ISO


# ======== system resolve (criteria 10-11) ========


def test_system_resolve_of_an_open_alert_sets_fields_and_removes_open_pk(aws):
    put_dp(aws)

    assert run(resolve_body()) == {"batchItemFailures": []}

    item = dp_item(aws)
    assert item["status"] == "resolved"
    assert item["resolved_at"] == DP_CHANGED_AT
    assert item["resolved_by_role"] == "system"
    assert item["expires_at"] == RESOLVE_EXPIRES
    assert "open_pk" not in item
    assert "status_notified_at" not in item


def test_system_resolve_sends_no_email(aws):
    put_dp(aws, escalated_at="2026-11-04T02:00:02.000Z")

    run(resolve_body())

    assert aws.sns.publishes == []
    assert aws.sqs.sends == []


def test_system_resolve_of_an_acknowledged_alert_keeps_the_acknowledge(aws):
    put_acked(aws, status_notified_at="2026-11-04T02:03:01.000Z")

    run(resolve_body())

    item = dp_item(aws)
    assert item["status"] == "resolved"
    assert item["acknowledged_at"] == ACK_AT
    assert item["acknowledged_by"] == OPS_SUB
    assert item["acknowledged_by_role"] == "ops"
    assert "open_pk" not in item
    assert aws.sns.publishes == []


@pytest.mark.parametrize("alert_type", DEVICE_PROBLEM_TYPES)
def test_each_device_problem_type_can_be_resolved(aws, alert_type):
    put_dp(aws, alert_type)

    run(resolve_body())

    assert dp_item(aws)["status"] == "resolved"


def test_second_resolve_is_not_open_and_changes_nothing(aws, logs):
    put_dp(aws)
    run(resolve_body())
    before = dp_item(aws)

    assert run(resolve_body(changed_at="2026-11-04T02:10:00.000Z")) == {
        "batchItemFailures": []
    }

    assert dp_item(aws) == before
    assert status_records(logs)[-1]["outcome"] == "not_open"


def test_resolve_of_a_missing_alert_is_not_open(aws, logs):
    assert run(resolve_body()) == {"batchItemFailures": []}

    assert status_records(logs)[-1]["outcome"] == "not_open"


def test_resolve_log_line_has_outcome_updated_and_no_has_note(aws, logs):
    put_dp(aws)

    run(resolve_body())

    (line,) = status_records(logs)
    assert line["outcome"] == "updated"
    assert (line["new_status"], line["by_role"]) == ("resolved", "system")
    assert "has_note" not in line


# ======== role/type rule: not_allowed (criterion 14a) ========

DP_DISALLOWED = {
    "nurse-acknowledge": lambda: nurse_ack_body(target=DP_TARGET),
    "caregiver-cancel": lambda: caregiver_cancel_body(target=DP_TARGET),
}
HEALTH_DISALLOWED = {
    "ops-acknowledge": lambda: ops_ack_body(target=HEALTH_TARGET),
    "caregiver-acknowledge": lambda: caregiver_ack_body(target=HEALTH_TARGET),
    "system-resolve": lambda: resolve_body(target=HEALTH_TARGET),
}
HEALTH_ALERTS = {
    "fall": alert_body,
    "hr_high-warning": lambda: vitals_body("hr_high", "warning"),
    "spo2_low-critical": lambda: vitals_body("spo2_low", "critical"),
}


def assert_not_allowed(aws, logs, before, read):
    assert read() == before
    assert aws.sns.publishes == []
    assert aws.sqs.sends == []
    (line,) = status_records(logs)
    assert line["outcome"] == "not_allowed"


@pytest.mark.parametrize("update", list(DP_DISALLOWED))
@pytest.mark.parametrize("alert_type", DEVICE_PROBLEM_TYPES)
def test_disallowed_update_of_a_device_problem_is_not_allowed(
    aws, logs, alert_type, update
):
    put_dp(aws, alert_type)
    before = dp_item(aws)

    assert run(DP_DISALLOWED[update]()) == {"batchItemFailures": []}

    assert_not_allowed(aws, logs, before, lambda: dp_item(aws))


@pytest.mark.parametrize("update", list(HEALTH_DISALLOWED))
@pytest.mark.parametrize("alert", list(HEALTH_ALERTS))
def test_disallowed_update_of_a_health_alert_is_not_allowed(aws, logs, alert, update):
    put_alert(aws, HEALTH_ALERTS[alert]())
    before = get_item(aws)

    assert run(HEALTH_DISALLOWED[update]()) == {"batchItemFailures": []}

    assert_not_allowed(aws, logs, before, lambda: get_item(aws))


def test_not_allowed_is_checked_before_not_open_for_a_device_problem(aws, logs):
    put_dp(aws, status="resolved", open_pk=None)
    before = dp_item(aws)

    run(nurse_ack_body(target=DP_TARGET))

    assert_not_allowed(aws, logs, before, lambda: dp_item(aws))


def test_not_allowed_is_checked_before_not_open_for_a_health_alert(aws, logs):
    put_alert(aws, status="cancelled", open_pk=None)
    before = get_item(aws)

    run(resolve_body(target=HEALTH_TARGET))

    assert_not_allowed(aws, logs, before, lambda: get_item(aws))


def test_not_allowed_log_line_has_item_ids_and_no_sub_or_name(aws, logs):
    put_dp(aws)

    run(caregiver_cancel_body(target=DP_TARGET))

    (line,) = status_records(logs)
    assert line["alert_id"] == ALERT_ID
    assert line["alert_type"] == "battery_low"
    assert line["device_id"] == DEVICE
    assert line["by_role"] == "caregiver"
    text = str(logs())
    assert CAREGIVER_SUB not in text
    assert CAREGIVER_NAME not in text


def test_allowed_health_updates_still_work_next_to_the_rule(aws):
    put_alert(aws)

    run(nurse_ack_body())

    assert get_item(aws)["status"] == "acknowledged"
    assert "acknowledged_by_role" not in get_item(aws)


# ======== "I'm OK" (criterion 14a) ========

# Before the default cancel's changed_at (2026-10-18T03:15:00.000Z), so the device
# target Query's sort-key bound includes them.
DP_TS = {
    "device_offline": ("2026-10-18T03:00:00.000Z", ALERT_ID_2),
    "battery_low": ("2026-10-18T03:05:00.000Z", ALERT_ID_3),
    "not_worn": ("2026-10-18T03:10:00.000Z", "6f1c2e0a-5b3d-5e8f-9a47-000000000004"),
}


def put_old_device_problems(aws) -> None:
    for alert_type, (ts, alert_id) in DP_TS.items():
        details = {
            "device_offline": {"last_seen": ts},
            "battery_low": {"battery_pct": 15},
            "not_worn": {"since": ts},
        }[alert_type]
        put_alert(
            aws,
            device_problem_body(
                alert_type, event_ts=ts, alert_id=alert_id, details=details
            ),
        )


def test_im_ok_cancels_the_health_alert_and_leaves_device_problems_open(aws):
    put_alert(aws)
    put_old_device_problems(aws)

    assert run(cancel_body()) == {"batchItemFailures": []}

    assert get_item(aws)["status"] == "cancelled"
    for ts, alert_id in DP_TS.values():
        item = get_item(aws, event_ts=ts, alert_id=alert_id)
        assert item["status"] == "open"
        assert item["open_pk"] == "OPEN"
        assert "cancelled_at" not in item
    (follow_up,) = aws.alert_publishes()
    assert "Type: Fall" in follow_up["Message"]


def test_im_ok_with_only_device_problems_open_finds_nothing(aws, logs):
    put_old_device_problems(aws)

    assert run(cancel_body()) == {"batchItemFailures": []}

    assert aws.sns.publishes == []
    assert status_records(logs)[-1]["outcome"] == "no_open_alerts"


def test_im_ok_leaves_an_acknowledged_device_problem_acknowledged(aws):
    ts, alert_id = DP_TS["battery_low"]
    put_alert(
        aws,
        device_problem_body(event_ts=ts, alert_id=alert_id),
        status="acknowledged",
        acknowledged_by=OPS_SUB,
        acknowledged_by_role="ops",
    )

    run(cancel_body())

    assert get_item(aws, event_ts=ts, alert_id=alert_id)["status"] == "acknowledged"
