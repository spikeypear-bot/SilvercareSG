"""Repeat suppression and vitals alerts end to end against moto
(docs/features/vitals-alerts.md, criteria 4, 17-22, 29).

All IDs are fixed (no random UUIDs), so log checks can't meet a random digit string.
"""

from __future__ import annotations

import json

import handler
import message
import pytest
from botocore.exceptions import ClientError
from conftest import ALERTS_TABLE, BUCKET
from events import (
    DEVICE,
    NO_PULSE_DETAILS,
    RESIDENT,
    alert_body,
    record,
    sqs_event,
    vitals_body,
)

POWERTOOLS_KEYS = {"level", "location", "message", "timestamp", "service"}
OUR_KEYS = {
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


def aid(n: int) -> str:
    return f"6f1c2e0a-5b3d-5e8f-9a47-{n:012d}"


def at(minutes: int = 0, seconds: int = 0, millis: int = 0) -> str:
    total = minutes * 60 + seconds
    return f"2026-10-18T03:{total // 60:02d}:{total % 60:02d}.{millis:03d}Z"


def vitals(n: int, ts: str, alert_type="hr_high", severity="warning", **kw) -> str:
    return vitals_body(
        alert_type, severity, alert_id=aid(n), event_ts=ts, received_at=ts, **kw
    )


def fall(n: int, ts: str, **kw) -> str:
    return alert_body(alert_id=aid(n), event_ts=ts, received_at=ts, **kw)


def run(*bodies) -> dict:
    records = [record(b, f"sqs-msg-{i:04d}") for i, b in enumerate(bodies, start=1)]
    return handler.lambda_handler(sqs_event(*records), None)


def stored_ids(aws) -> set[str]:
    items = aws.ddb.scan(TableName=ALERTS_TABLE)["Items"]
    return {i["alert_id"]["S"] for i in items}


def archived_ids(aws) -> set[str]:
    resp = aws.s3.list_objects_v2(Bucket=BUCKET)
    return {o["Key"].rsplit("/", 1)[1][:-5] for o in resp.get("Contents", [])}


def outcomes(logs) -> list[str]:
    return [r["outcome"] for r in logs() if r.get("message") == "alert"]


class Recorder:
    def __init__(self, client, fail_on: str | None = None):
        self._client = client
        self._fail_on = fail_on
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, name):
        method = getattr(self._client, name)

        def wrapper(**kwargs):
            self.calls.append((name, kwargs))
            if name == self._fail_on:
                raise ClientError(
                    {"Error": {"Code": "ThrottlingException", "Message": "x"}}, name
                )
            return method(**kwargs)

        return wrapper


# ---- same type and severity within 30 minutes (criterion 17) ----


def test_repeat_within_30_minutes_is_suppressed(aws, logs):
    run(vitals(1, at(0)))

    assert run(vitals(2, at(10))) == {"batchItemFailures": []}

    assert stored_ids(aws) == {aid(1)}
    assert archived_ids(aws) == {aid(1)}
    assert len(aws.sns.publishes) == 1
    assert outcomes(logs) == ["notified", "suppressed"]


def test_suppressed_log_line_has_ids_and_severity(aws, logs):
    run(vitals(1, at(0)))
    run(vitals(2, at(10)))

    rec = logs()[-1]
    assert set(rec) - POWERTOOLS_KEYS <= OUR_KEYS, rec
    assert rec == rec | {
        "alert_id": aid(2),
        "alert_type": "hr_high",
        "severity": "warning",
        "device_id": DEVICE,
        "resident_id": RESIDENT,
        "outcome": "suppressed",
        "nurses": "none",
    }


@pytest.mark.parametrize(
    "later", [at(29, 59, 999), at(0, 0, 1), at(15)], ids=["29m59.999s", "1ms", "15m"]
)
def test_repeat_anywhere_inside_the_window_is_suppressed(aws, later):
    run(vitals(1, at(0)))

    run(vitals(2, later))

    assert stored_ids(aws) == {aid(1)}


def test_repeat_at_exactly_30_minutes_is_not_suppressed(aws, logs):
    run(vitals(1, at(0)))

    run(vitals(2, at(30)))

    assert stored_ids(aws) == {aid(1), aid(2)}
    assert outcomes(logs) == ["notified", "notified"]


@pytest.mark.parametrize("later", [at(30, 0, 1), at(31), at(59)])
def test_repeat_after_30_minutes_is_not_suppressed(aws, later):
    run(vitals(1, at(0)))

    run(vitals(2, later))

    assert stored_ids(aws) == {aid(1), aid(2)}
    assert len(aws.sns.publishes) == 2


def test_earlier_alert_arriving_later_is_not_suppressed_by_a_newer_one(aws):
    # Times come from event_ts: an alert from before the stored one isn't a repeat.
    run(vitals(2, at(10)))

    run(vitals(1, at(0)))

    assert stored_ids(aws) == {aid(1), aid(2)}


def test_chain_of_repeats_is_held_back_by_the_first(aws):
    run(vitals(1, at(0)), vitals(2, at(10)), vitals(3, at(20)))

    assert stored_ids(aws) == {aid(1)}


def test_suppressed_alert_does_not_extend_the_window(aws):
    # Suppressed alerts aren't stored, so the window is counted from the first one.
    run(vitals(1, at(0)), vitals(2, at(20)), vitals(3, at(35)))

    assert stored_ids(aws) == {aid(1), aid(3)}


# ---- different type or severity (criteria 17, 19) ----


def test_warning_does_not_hold_back_a_critical_alert(aws):
    run(vitals(1, at(0), severity="warning"))

    run(vitals(2, at(5), severity="critical"))

    assert stored_ids(aws) == {aid(1), aid(2)}
    assert len(aws.alert_publishes()) == 2
    # The critical one also went to the nurses at once (alert-routing).
    assert len(aws.nurse_publishes()) == 1


def test_critical_does_not_hold_back_a_warning(aws):
    run(vitals(1, at(0), severity="critical"))

    run(vitals(2, at(5), severity="warning"))

    assert stored_ids(aws) == {aid(1), aid(2)}


def test_critical_repeat_is_suppressed(aws):
    run(vitals(1, at(0), severity="critical"))

    run(vitals(2, at(5), severity="critical"))

    assert stored_ids(aws) == {aid(1)}


def test_no_pulse_after_a_low_heart_rate_critical_is_suppressed(aws):
    # Same type (hr_low) and severity (critical): the decided rule holds it back.
    run(vitals(1, at(0), "hr_low", "critical"))

    run(vitals(2, at(5), "hr_low", "critical", details=NO_PULSE_DETAILS))

    assert stored_ids(aws) == {aid(1)}


def test_different_alert_type_is_not_suppressed(aws):
    run(vitals(1, at(0), "hr_high"))

    run(vitals(2, at(5), "spo2_low"))

    assert stored_ids(aws) == {aid(1), aid(2)}


def test_other_residents_alerts_do_not_suppress(aws):
    run(vitals(1, at(0), resident_id="res-0001"))

    run(vitals(2, at(5), resident_id="res-0002"))

    assert stored_ids(aws) == {aid(1), aid(2)}


def test_assigned_resident_repeat_from_another_device_is_suppressed(aws):
    run(vitals(1, at(0), device_id="wearable-0001"))

    run(vitals(2, at(5), device_id="wearable-0002"))

    assert stored_ids(aws) == {aid(1)}


def test_fall_does_not_suppress_vitals_alerts(aws):
    run(fall(1, at(0)))

    run(vitals(2, at(5), severity="critical"))

    assert stored_ids(aws) == {aid(1), aid(2)}


# ---- falls are never suppressed (criterion 19) ----


def test_falls_are_never_suppressed(aws, logs):
    run(fall(1, at(0)))

    run(fall(2, at(5)))

    assert stored_ids(aws) == {aid(1), aid(2)}
    assert outcomes(logs) == ["notified", "notified"]


def test_fall_makes_no_suppression_query(aws, monkeypatch):
    recorder = Recorder(handler.dynamodb_client())
    monkeypatch.setattr(handler, "dynamodb_client", lambda: recorder)

    run(fall(1, at(0)))

    assert "query" not in [name for name, _ in recorder.calls]


# ---- unassigned devices (criterion 18) ----


def test_unassigned_repeat_from_the_same_device_is_suppressed(aws, logs):
    run(vitals(1, at(0), resident_id="unassigned"))

    run(vitals(2, at(5), resident_id="unassigned"))

    assert stored_ids(aws) == {aid(1)}
    assert outcomes(logs) == ["skipped_unassigned", "suppressed"]


def test_unassigned_alerts_from_different_devices_are_not_suppressed(aws):
    run(vitals(1, at(0), resident_id="unassigned", device_id="wearable-0001"))

    run(vitals(2, at(5), resident_id="unassigned", device_id="wearable-0002"))

    assert stored_ids(aws) == {aid(1), aid(2)}
    assert aws.alert_publishes() == []
    # Unassigned alerts go to the nurses at once, one email each (alert-routing).
    assert len(aws.nurse_publishes()) == 2


# ---- retries (criteria 17, 20) ----


def test_redelivered_alert_is_a_duplicate_not_suppressed(aws, logs):
    body = vitals(1, at(0))
    run(body)

    run(body)

    assert stored_ids(aws) == {aid(1)}
    assert outcomes(logs) == ["notified", "duplicate"]
    assert len(aws.sns.publishes) == 1


def test_retry_after_publish_failure_is_notified_not_suppressed(aws, monkeypatch):
    failing = Recorder(aws.sns, fail_on="publish")
    monkeypatch.setattr(handler, "sns_client", lambda: failing)
    run(vitals(1, at(0)))
    monkeypatch.setattr(handler, "sns_client", lambda: aws.sns)

    run(vitals(1, at(0)))

    assert len(aws.sns.publishes) == 1


def test_stored_alert_is_not_suppressed_on_retry_by_an_earlier_one(
    aws, logs, monkeypatch
):
    """Alert 2 (03:10) is stored but its email fails. Alert 1 (03:00, same type and
    severity) is then processed: nothing earlier, so it is stored and emailed. SQS
    redelivers alert 2. handler.py's docstring says "an alert that is already stored
    is never suppressed afterwards", so the retry must not be suppressed: alert 2
    exists in silvercare-alerts and must not be left without a notification outcome.
    """
    failing = Recorder(aws.sns, fail_on="publish")
    monkeypatch.setattr(handler, "sns_client", lambda: failing)
    run(vitals(2, at(10)))
    monkeypatch.setattr(handler, "sns_client", lambda: aws.sns)
    run(vitals(1, at(0)))

    run(vitals(2, at(10)))

    assert outcomes(logs)[-1] != "suppressed"


def test_is_repeat_ignores_items_when_its_own_alert_id_is_there():
    alert = message.parse_alert(vitals(2, at(10)))
    items = [
        {
            "alert_id": aid(1),
            "alert_type": "hr_high",
            "severity": "warning",
            "device_id": DEVICE,
            "event_ts": at(0),
        },
        {
            "alert_id": aid(2),
            "alert_type": "hr_high",
            "severity": "warning",
            "device_id": DEVICE,
            "event_ts": at(10),
        },
    ]

    assert handler.is_repeat(alert, items) is False
    assert handler.is_repeat(alert, items[:1]) is True


# ---- the query (pattern 7) ----


def test_query_is_the_residents_30_minute_window(aws, monkeypatch):
    recorder = Recorder(handler.dynamodb_client())
    monkeypatch.setattr(handler, "dynamodb_client", lambda: recorder)

    run(vitals(1, at(40)))

    ((_, kwargs),) = [c for c in recorder.calls if c[0] == "query"]
    assert kwargs["TableName"] == ALERTS_TABLE
    assert "IndexName" not in kwargs
    assert kwargs["ConsistentRead"] is True
    assert kwargs["KeyConditionExpression"] == "pk = :pk AND sk BETWEEN :low AND :high"
    assert kwargs["ExpressionAttributeValues"] == {
        ":pk": {"S": f"RESIDENT#{RESIDENT}"},
        ":low": {"S": f"ALERT#{at(10)}"},
        ":high": {"S": f"ALERT#{at(40)}#~"},
    }


def test_query_failure_fails_the_record_and_writes_nothing(aws, monkeypatch, logs):
    failing = Recorder(handler.dynamodb_client(), fail_on="query")
    monkeypatch.setattr(handler, "dynamodb_client", lambda: failing)

    assert run(vitals(1, at(0))) == {
        "batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]
    }

    assert stored_ids(aws) == set()
    assert aws.sns.publishes == []
    assert logs()[-1]["outcome"] == "error"


# ---- vitals alerts are stored, archived, and emailed (criteria 2-3, 21-22) ----


def test_vitals_alert_is_stored_with_severity_and_details(aws):
    run(vitals(1, at(0)))

    (raw,) = aws.ddb.scan(TableName=ALERTS_TABLE)["Items"]
    assert raw["severity"] == {"S": "warning"}
    assert raw["alert_type"] == {"S": "hr_high"}
    assert raw["details"]["M"] == {
        "metric": {"S": "hr_bpm"},
        "value": {"N": "128"},
        "limit": {"N": "120"},
        "direction": {"S": "above"},
        "previous_value": {"N": "125"},
    }
    assert "latest_vitals" not in raw["details"]["M"]


def test_vitals_alert_reads_no_latest_vitals(aws, monkeypatch):
    recorder = Recorder(handler.dynamodb_client())
    monkeypatch.setattr(handler, "dynamodb_client", lambda: recorder)

    run(vitals(1, at(0)))

    assert "get_item" not in [name for name, _ in recorder.calls]


def test_archive_has_severity(aws):
    run(vitals(1, at(0), severity="critical"))

    key = f"alerts/date=2026-10-18/{aid(1)}.json"
    archived = json.loads(aws.s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    assert archived["severity"] == "critical"
    assert archived["details"]["held_s"] == 10


@pytest.mark.parametrize(
    ("severity", "subject"),
    [
        ("warning", "SilverCare alert: High heart rate - res-0003"),
        ("critical", "SilverCare CRITICAL alert: High heart rate - res-0003"),
    ],
)
def test_published_subject_per_severity(aws, severity, subject):
    run(vitals(1, at(0), severity=severity))

    (call,) = aws.alert_publishes()
    assert call["Subject"] == subject
    assert f"Severity: {severity.capitalize()}" in call["Message"]


def test_no_pulse_email_has_the_no_pulse_line(aws):
    run(vitals(1, at(0), "hr_low", "critical", details=NO_PULSE_DETAILS))

    (call,) = aws.alert_publishes()
    assert call["Subject"] == "SilverCare CRITICAL alert: Low heart rate - res-0003"
    assert "Reading: no pulse detected for 10 s (heart rate 0 bpm)" in call["Message"]


def test_missing_severity_is_reported_as_failure(aws, logs):
    body = json.loads(vitals(1, at(0)))
    del body["severity"]

    assert run(json.dumps(body)) == {
        "batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]
    }

    assert stored_ids(aws) == set()
    assert logs()[-1]["reason"] == "missing:severity"


# ---- no values in logs (privacy) ----


def test_logs_hold_no_values_limits_or_details(aws, logs):
    details = {
        "metric": "hr_bpm",
        "value": 187,
        "limit": 113,
        "direction": "above",
        "previous_value": 186,
    }
    run(vitals(1, at(0), details=details))
    run(vitals(2, at(5), details=details))  # suppressed
    run(vitals(1, at(0), details=details))  # duplicate

    records = logs()
    assert outcomes(logs) == ["notified", "suppressed", "duplicate"]
    noisy = {"timestamp", "location", "dispatch_latency_ms"}
    text = " ".join(f"{k}={v}" for r in records for k, v in r.items() if k not in noisy)
    for hidden in (
        "187",
        "186",
        "113",
        "details",
        "limit",
        "previous_value",
        "hr_bpm",
        "above",
        "Reading",
        "SilverCare",
    ):
        assert hidden not in text, hidden
    for rec in records:
        assert set(rec) - POWERTOOLS_KEYS <= OUR_KEYS, rec
