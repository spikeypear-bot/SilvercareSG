"""Device-problem alerts end to end against moto (docs/features/watchdog.md, criteria
12-14, 14a routing, and 20): stored and archived, emailed to the caregivers and the
ops team (never the nurses), grouped alerts not emailed to ops, redelivery per
timestamp, failed Publishes, no repeat check, no fall enrichment, no escalation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import BUCKET, LATEST_TABLE
from events import (
    ALERT_ID,
    ALERT_ID_2,
    DEVICE,
    DEVICE_PROBLEM_DETAILS,
    DEVICE_PROBLEM_TYPES,
    DP_EVENT_TS,
    RESIDENT,
    device_problem_body,
)
from test_status_update import (  # noqa: F401 - clock is an autouse fixture
    POWERTOOLS_KEYS,
    clock,
    get_item,
    put_alert,
    run,
    spy,
)

NOW = datetime(2026, 11, 4, 2, 0, 1, 200_000, tzinfo=UTC)
NOW_ISO = "2026-11-04T02:00:01.200Z"
EARLIER_TS = "2026-11-04T01:30:00.000Z"
ALERTS_FILTER = {"resident_id": {"DataType": "String", "StringValue": RESIDENT}}
SUBJECTS = {
    "device_offline": f"SilverCare device problem: Device offline - {DEVICE} ({RESIDENT})",
    "battery_low": f"SilverCare device problem: Low battery - {DEVICE} ({RESIDENT})",
    "not_worn": f"SilverCare device problem: Wearable not worn - {DEVICE} ({RESIDENT})",
}
DETAIL_LINES = {
    "device_offline": "Last heartbeat: 2026-11-04 09:40:00 SGT",
    "battery_low": "Battery: 15 %",
    "not_worn": "Not worn since: 2026-11-04 09:40:00 SGT",
}
LABELS = {
    "device_offline": "Device offline",
    "battery_low": "Low battery",
    "not_worn": "Wearable not worn",
}


@pytest.fixture(autouse=True)
def now(clock):  # noqa: F811 - the imported fixture
    clock["now"] = NOW
    return clock


def body_text(alert_type: str, resident_id: str = RESIDENT) -> str:
    return (
        "SilverCare device problem\n"
        "\n"
        f"Type: {LABELS[alert_type]}\n"
        f"Device: {DEVICE}\n"
        f"Resident: {resident_id}\n"
        f"{DETAIL_LINES[alert_type]}\n"
        "\n"
        "Open the SilverCare portal: https://d0000example.cloudfront.net/\n"
        "\n"
        "This is an automated message. Do not reply.\n"
    )


def item(aws, resident_id=RESIDENT, alert_id=ALERT_ID, event_ts=DP_EVENT_TS):
    return get_item(aws, resident_id=resident_id, event_ts=event_ts, alert_id=alert_id)


def alert_lines(logs) -> list[dict]:
    return [r for r in logs() if r.get("message") == "alert" and "kind" not in r]


def grouped_body() -> str:
    return device_problem_body(
        "device_offline", details={"last_seen": DP_EVENT_TS, "grouped": True}
    )


# ======== store, archive, email (criteria 12-14) ========


@pytest.mark.parametrize("alert_type", DEVICE_PROBLEM_TYPES)
def test_device_problem_is_stored_open_with_both_email_timestamps(aws, alert_type):
    assert run(device_problem_body(alert_type)) == {"batchItemFailures": []}

    stored = item(aws)
    assert stored["alert_type"] == alert_type
    assert stored["severity"] == "warning"
    assert stored["status"] == "open"
    assert stored["open_pk"] == "OPEN"
    assert stored["details"] == DEVICE_PROBLEM_DETAILS[alert_type]
    assert stored["notified_at"] == NOW_ISO
    assert stored["ops_notified_at"] == NOW_ISO
    assert "escalated_at" not in stored
    assert "escalation_queued_at" not in stored


@pytest.mark.parametrize("alert_type", DEVICE_PROBLEM_TYPES)
def test_device_problem_is_archived(aws, alert_type):
    run(device_problem_body(alert_type))

    obj = aws.s3.get_object(
        Bucket=BUCKET, Key=f"alerts/date=2026-11-04/{ALERT_ID}.json"
    )
    assert alert_type.encode() in obj["Body"].read()


@pytest.mark.parametrize("alert_type", DEVICE_PROBLEM_TYPES)
def test_caregivers_get_the_email_with_the_resident_filter(aws, alert_type):
    run(device_problem_body(alert_type))

    (publish,) = aws.alert_publishes()
    assert publish["Subject"] == SUBJECTS[alert_type]
    assert publish["Message"] == body_text(alert_type)
    assert publish["MessageAttributes"] == ALERTS_FILTER


@pytest.mark.parametrize("alert_type", DEVICE_PROBLEM_TYPES)
def test_ops_get_the_same_subject_and_body(aws, alert_type):
    run(device_problem_body(alert_type))

    (ops,) = aws.ops_publishes()
    (caregivers,) = aws.alert_publishes()
    assert ops["Subject"] == caregivers["Subject"]
    assert ops["Message"] == caregivers["Message"]
    assert "MessageAttributes" not in ops


@pytest.mark.parametrize("alert_type", DEVICE_PROBLEM_TYPES)
def test_nurses_get_no_email_and_no_escalation_check_is_queued(aws, alert_type):
    run(device_problem_body(alert_type))

    assert aws.nurse_publishes() == []
    assert aws.sqs.sends == []


def test_caregiver_email_is_published_before_the_ops_email(aws):
    run(device_problem_body())

    assert [p["TopicArn"] for p in aws.sns.publishes] == [
        aws.topic_arn,
        aws.ops_topic_arn,
    ]


def test_log_line_has_nurses_none_and_ops_emailed(aws, logs):
    run(device_problem_body())

    (line,) = alert_lines(logs)
    assert line["outcome"] == "notified"
    assert line["nurses"] == "none"
    assert line["ops"] == "emailed"
    assert line["alert_type"] == "battery_low"
    assert set(line) - POWERTOOLS_KEYS == {
        "alert_id",
        "alert_type",
        "severity",
        "device_id",
        "resident_id",
        "outcome",
        "nurses",
        "ops",
        "dispatch_latency_ms",
    }


def test_log_line_never_has_details(aws, logs):
    run(device_problem_body(details={"battery_pct": 17}))

    text = str(
        [
            {k: v for k, v in r.items() if k not in ("timestamp", "location")}
            for r in logs()
        ]
    )
    text = text.replace(ALERT_ID, "<id>")
    assert "17" not in text
    assert "battery_pct" not in text


# ======== grouped (criterion 8a) ========


def test_grouped_alert_is_emailed_to_caregivers_but_not_to_ops(aws):
    run(grouped_body())

    assert len(aws.alert_publishes()) == 1
    assert aws.ops_publishes() == []
    stored = item(aws)
    assert stored["details"]["grouped"] is True
    assert "notified_at" in stored
    assert "ops_notified_at" not in stored


def test_grouped_alert_log_line_has_ops_grouped(aws, logs):
    run(grouped_body())

    (line,) = alert_lines(logs)
    assert line["ops"] == "grouped"
    assert line["nurses"] == "none"


def test_grouped_alert_redelivered_never_emails_ops(aws):
    run(grouped_body())
    run(grouped_body())

    assert aws.ops_publishes() == []
    assert len(aws.alert_publishes()) == 1


# ======== redelivery (criterion 13) ========


def test_redelivered_alert_sends_no_second_email(aws, logs):
    run(device_problem_body())
    run(device_problem_body())

    assert len(aws.alert_publishes()) == 1
    assert len(aws.ops_publishes()) == 1
    assert alert_lines(logs)[-1]["outcome"] == "duplicate"


def test_redelivery_with_notified_at_set_emails_only_ops(aws):
    put_alert(aws, device_problem_body(), notified_at="2026-11-04T02:00:00.900Z")

    assert run(device_problem_body()) == {"batchItemFailures": []}

    assert aws.alert_publishes() == []
    (ops,) = aws.ops_publishes()
    # The item read back from DynamoDB has battery_pct as a Decimal.
    assert ops["Message"] == body_text("battery_low")
    assert item(aws)["ops_notified_at"] == NOW_ISO


def test_redelivery_with_both_timestamps_set_emails_nobody(aws):
    put_alert(
        aws,
        device_problem_body(),
        notified_at="2026-11-04T02:00:00.900Z",
        ops_notified_at="2026-11-04T02:00:01.000Z",
    )

    run(device_problem_body())

    assert aws.sns.publishes == []
    assert item(aws)["ops_notified_at"] == "2026-11-04T02:00:01.000Z"


def test_failed_ops_publish_fails_the_record_after_the_caregiver_email(
    aws, monkeypatch
):
    events: list = []
    spy(monkeypatch, "sns_client", events, fail_on="publish", fail_nth=2)

    result = run(device_problem_body())

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}
    assert len(aws.alert_publishes()) == 1
    stored = item(aws)
    assert "notified_at" in stored
    assert "ops_notified_at" not in stored


def test_retry_after_a_failed_ops_publish_emails_only_ops(aws, monkeypatch):
    events: list = []
    spy(monkeypatch, "sns_client", events, fail_on="publish", fail_nth=2)
    run(device_problem_body())

    assert run(device_problem_body()) == {"batchItemFailures": []}

    assert len(aws.alert_publishes()) == 1
    assert len(aws.ops_publishes()) == 1
    assert "ops_notified_at" in item(aws)


def test_failed_caregiver_publish_fails_the_record_without_an_ops_email(
    aws, monkeypatch
):
    events: list = []
    spy(monkeypatch, "sns_client", events, fail_on="publish", fail_nth=1)

    result = run(device_problem_body())

    assert result["batchItemFailures"] == [{"itemIdentifier": "sqs-msg-0001"}]
    assert aws.sns.publishes == []
    stored = item(aws)
    assert "notified_at" not in stored
    assert "ops_notified_at" not in stored


def test_failed_ops_timestamp_update_fails_the_record(aws, monkeypatch, logs):
    events: list = []
    spy(monkeypatch, "dynamodb_client", events, fail_on="update_item", fail_nth=2)

    result = run(device_problem_body())

    assert result["batchItemFailures"] == [{"itemIdentifier": "sqs-msg-0001"}]
    assert alert_lines(logs)[-1]["error"] == "ThrottlingException"


# ======== what device problems skip (criterion 13) ========


def test_no_repeat_check_a_second_alert_of_the_same_type_is_stored_and_emailed(aws):
    run(device_problem_body(event_ts=EARLIER_TS))
    run(device_problem_body(alert_id=ALERT_ID_2))

    assert item(aws, alert_id=ALERT_ID_2) is not None
    assert len(aws.alert_publishes()) == 2
    assert len(aws.ops_publishes()) == 2


def test_no_query_of_recent_alerts_and_no_latest_vitals_read(aws, monkeypatch):
    events: list = []
    spy(monkeypatch, "dynamodb_client", events)

    run(device_problem_body("not_worn"))

    ops = [op for op, _ in events]
    assert "query" not in ops
    assert all(kw.get("TableName") != LATEST_TABLE for _, kw in events)
    assert "latest_vitals" not in item(aws)["details"]


# ======== unassigned (criterion 13) ========


def test_unassigned_device_problem_is_stored_and_emailed_to_ops_only(aws, logs):
    run(device_problem_body("device_offline", resident_id="unassigned"))

    stored = item(aws, resident_id="unassigned")
    assert stored["notification"] == "skipped_unassigned"
    assert "notified_at" not in stored
    assert stored["ops_notified_at"] == NOW_ISO
    assert aws.alert_publishes() == []
    assert aws.nurse_publishes() == []
    (ops,) = aws.ops_publishes()
    assert ops["Subject"] == (
        f"SilverCare device problem: Device offline - {DEVICE} (unassigned)"
    )
    (line,) = alert_lines(logs)
    assert (line["outcome"], line["nurses"], line["ops"]) == (
        "skipped_unassigned",
        "none",
        "emailed",
    )
