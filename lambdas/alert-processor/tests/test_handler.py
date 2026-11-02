"""alert-processor end to end against moto (alerts-core criteria 14-20)."""

from __future__ import annotations

import json
from decimal import Decimal

import handler
import pytest
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError
from conftest import ALERTS_TABLE, BUCKET, LATEST_TABLE
from events import (
    ALERT_ID,
    ALERT_ID_2,
    ALERT_ID_3,
    DEVICE,
    EVENT_TS,
    RECEIVED_AT,
    RESIDENT,
    SOURCE_MESSAGE_ID,
    alert_body,
    record,
    sqs_event,
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
}
ARCHIVE_KEY = f"alerts/date=2026-10-18/{ALERT_ID}.json"
SUBJECT = "SilverCare CRITICAL alert: Fall - res-0003"

_d = TypeDeserializer()
_s = TypeSerializer()


# ---- helpers ----


def run(*bodies_or_records):
    records = [
        b if isinstance(b, dict) else record(b, f"sqs-msg-{i:04d}")
        for i, b in enumerate(bodies_or_records, start=1)
    ]
    return handler.lambda_handler(sqs_event(*records), None)


def alert_items(aws) -> list[dict]:
    items = aws.ddb.scan(TableName=ALERTS_TABLE)["Items"]
    return [{k: _d.deserialize(v) for k, v in i.items()} for i in items]


def only_item(aws) -> dict:
    (item,) = alert_items(aws)
    return item


def archive_keys(aws) -> list[str]:
    resp = aws.s3.list_objects_v2(Bucket=BUCKET)
    return [o["Key"] for o in resp.get("Contents", [])]


def put_vitals(aws, entries: list[dict], device_id: str = DEVICE) -> None:
    """A silvercare-latest vitals item with the given entries (newest first)."""
    item = {
        "pk": f"DEVICE#{device_id}",
        "sk": "MSG#vitals",
        "device_type": "wearable",
        "watch_type": "vitals",
        "entries": entries,
        "latest": entries[0],
        "latest_ts": entries[0]["ts"],
        "version": len(entries),
    }
    aws.ddb.put_item(
        TableName=LATEST_TABLE, Item={k: _s.serialize(v) for k, v in item.items()}
    )


def vitals_entry(ts: str, hr, spo2) -> dict:
    return {
        "message_id": "c2a1d4f0-0000-4000-8000-0000000000aa",
        "ts": ts,
        "received_at": ts,
        "clock_skew": False,
        "hr_bpm": hr,
        "spo2_pct": spo2,
        "skin_temp_c": Decimal("33.4"),
        "activity_pct": 12,
    }


class FailingClient:
    """Wraps a client and raises a ClientError on one operation."""

    def __init__(self, client, fail_on: str, code: str = "ThrottlingException"):
        self._client = client
        self._fail_on = fail_on
        self._code = code
        self.calls: list[str] = []

    def __getattr__(self, name):
        method = getattr(self._client, name)

        def wrapper(**kwargs):
            self.calls.append(name)
            if name == self._fail_on:
                raise ClientError(
                    {"Error": {"Code": self._code, "Message": "synthetic"}}, name
                )
            return method(**kwargs)

        return wrapper


def fail_publish(monkeypatch, aws, code="ThrottlingException"):
    failing = FailingClient(aws.sns, "publish", code)
    monkeypatch.setattr(handler, "sns_client", lambda: failing)
    return failing


def assert_only_allowed_keys(rec):
    assert set(rec) - POWERTOOLS_KEYS <= OUR_KEYS, rec


# ---- store (criterion 16.2) ----


def test_valid_alert_is_stored_with_data_model_attributes(aws):
    assert run(alert_body()) == {"batchItemFailures": []}

    item = only_item(aws)
    assert set(item) == {
        "pk",
        "sk",
        "alert_id",
        "alert_type",
        "severity",
        "device_id",
        "event_ts",
        "received_at",
        "created_at",
        "status",
        "source_message_id",
        "details",
        "open_pk",
        "notified_at",
    }
    assert item["pk"] == f"RESIDENT#{RESIDENT}"
    assert item["sk"] == f"ALERT#{EVENT_TS}#{ALERT_ID}"
    assert item["alert_id"] == ALERT_ID
    assert item["alert_type"] == "fall"
    assert item["severity"] == "critical"
    assert item["device_id"] == DEVICE
    assert item["event_ts"] == EVENT_TS
    assert item["received_at"] == RECEIVED_AT
    assert item["status"] == "open"
    assert item["open_pk"] == "OPEN"
    assert item["source_message_id"] == SOURCE_MESSAGE_ID
    assert item["details"] == {
        "confidence": Decimal("0.91"),
        "peak_g": Decimal("3.8"),
        "still_s": 20,
    }


@pytest.mark.parametrize("name", ["created_at", "notified_at"])
def test_timestamps_are_iso_utc_with_millis(aws, name):
    run(alert_body())

    value = only_item(aws)[name]
    assert len(value) == len("2026-10-18T03:14:07.410Z")
    assert value.endswith("Z")
    assert value[19] == "."


def test_stored_item_has_no_expires_at_or_notification(aws):
    run(alert_body())

    item = only_item(aws)
    assert "expires_at" not in item
    assert "notification" not in item


def test_decimal_round_trip_keeps_exact_numbers(aws):
    run(alert_body())

    raw = aws.ddb.scan(TableName=ALERTS_TABLE)["Items"][0]
    details = raw["details"]["M"]
    assert details["confidence"] == {"N": "0.91"}
    assert details["peak_g"] == {"N": "3.8"}
    assert details["still_s"] == {"N": "20"}


# ---- archive (criterion 16.3) ----


def test_alert_is_archived_once_under_event_date(aws):
    run(alert_body())

    assert archive_keys(aws) == [ARCHIVE_KEY]
    obj = aws.s3.get_object(Bucket=BUCKET, Key=ARCHIVE_KEY)
    assert obj["ContentType"] == "application/json"


def test_archive_is_the_item_as_one_json_object(aws):
    run(alert_body())

    text = aws.s3.get_object(Bucket=BUCKET, Key=ARCHIVE_KEY)["Body"].read().decode()
    archived = json.loads(text, parse_float=Decimal)
    item = only_item(aws)
    # Archived before notify, so notified_at isn't in the object.
    assert archived == {k: v for k, v in item.items() if k != "notified_at"}
    assert '"confidence":0.91' in text


def test_archive_date_is_the_utc_date_of_event_ts(aws):
    # 23:30 UTC is already the next day in Singapore.
    run(alert_body(event_ts="2026-10-18T23:30:00.000Z"))

    assert archive_keys(aws) == [ARCHIVE_KEY]


# ---- notify (criteria 16.4, 18, 19) ----


def test_assigned_alert_is_published_once(aws):
    run(alert_body())

    (call,) = aws.sns.publishes
    assert call["TopicArn"] == aws.topic_arn
    assert call["Subject"] == SUBJECT
    assert "Resident: res-0003" in call["Message"]
    assert (
        "Fall details: confidence 0.91, peak 3.8 g, still for 20 s" in call["Message"]
    )


def test_publish_carries_resident_id_message_attribute(aws):
    run(alert_body())

    (call,) = aws.sns.publishes
    assert call["MessageAttributes"] == {
        "resident_id": {"DataType": "String", "StringValue": RESIDENT}
    }


def test_published_subject_is_at_most_100_characters(aws):
    run(alert_body())

    assert len(aws.sns.publishes[0]["Subject"]) <= 100


def test_notified_at_is_set_after_publish(aws):
    run(alert_body())

    assert "notified_at" in only_item(aws)


def test_dispatch_latency_metric_is_emitted(aws, capsys):
    run(alert_body())

    metrics = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("{") and '"_aws"' in line
    ]
    (metric,) = metrics
    (directive,) = metric["_aws"]["CloudWatchMetrics"]
    assert directive["Namespace"] == "SilverCare"
    assert directive["Dimensions"] == [["alert_type"]]
    assert directive["Metrics"] == [
        {"Name": "AlertDispatchLatency", "Unit": "Milliseconds"}
    ]
    assert metric["alert_type"] == "fall"
    assert isinstance(metric["AlertDispatchLatency"], (int, float, list))


def test_notified_log_line_has_dispatch_latency(aws, logs):
    run(alert_body())

    (rec,) = logs()
    assert rec["outcome"] == "notified"
    assert isinstance(rec["dispatch_latency_ms"], int)
    assert rec["dispatch_latency_ms"] > 0


def test_latency_is_publish_time_minus_received_at():
    from datetime import UTC, datetime

    received = datetime(2026, 10, 18, 3, 14, 7, 410000, tzinfo=UTC)
    published = datetime(2026, 10, 18, 3, 14, 8, 650000, tzinfo=UTC)

    assert handler.latency_ms(received, published) == 1240


def test_notified_at_already_set_by_another_delivery_is_not_an_error(aws, monkeypatch):
    failing = FailingClient(
        handler.dynamodb_client(), "update_item", "ConditionalCheckFailedException"
    )
    monkeypatch.setattr(handler, "dynamodb_client", lambda: failing)

    assert run(alert_body()) == {"batchItemFailures": []}
    assert len(aws.sns.publishes) == 1


# ---- duplicates (criterion 16.2, 16.4) ----


def test_redelivered_message_creates_no_second_item_and_no_second_publish(aws, logs):
    run(alert_body())
    first = only_item(aws)

    assert run(alert_body()) == {"batchItemFailures": []}

    assert only_item(aws) == first
    assert len(aws.sns.publishes) == 1
    assert archive_keys(aws) == [ARCHIVE_KEY]
    assert [r["outcome"] for r in logs()] == ["notified", "duplicate"]


def test_redelivery_archives_the_existing_item(aws):
    run(alert_body())
    created_at = only_item(aws)["created_at"]

    run(alert_body())

    text = aws.s3.get_object(Bucket=BUCKET, Key=ARCHIVE_KEY)["Body"].read()
    archived = json.loads(text)
    assert archived["created_at"] == created_at
    assert archived["notified_at"] == only_item(aws)["notified_at"]


def test_publish_failure_fails_the_record_and_leaves_item_unnotified(
    aws, monkeypatch, logs
):
    fail_publish(monkeypatch, aws)

    assert run(alert_body()) == {
        "batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]
    }

    item = only_item(aws)
    assert "notified_at" not in item
    (rec,) = logs()
    assert rec["outcome"] == "error"
    assert rec["error"] == "ThrottlingException"


def test_stored_but_unnotified_alert_is_notified_on_retry(aws, monkeypatch, logs):
    with monkeypatch.context() as m:
        fail_publish(m, aws)
        run(alert_body())

    assert run(alert_body()) == {"batchItemFailures": []}

    assert len(aws.sns.publishes) == 1
    assert "notified_at" in only_item(aws)
    assert logs()[-1]["outcome"] == "notified"


# ---- unassigned (criterion 16.5) ----


def test_unassigned_alert_is_stored_with_skipped_notification(aws, logs):
    assert run(alert_body(resident_id="unassigned")) == {"batchItemFailures": []}

    item = only_item(aws)
    assert item["pk"] == "RESIDENT#unassigned"
    assert item["notification"] == "skipped_unassigned"
    assert "notified_at" not in item
    assert aws.sns.publishes == []
    assert archive_keys(aws) == [ARCHIVE_KEY]
    assert logs()[-1]["outcome"] == "skipped_unassigned"


def test_redelivered_unassigned_alert_is_never_published(aws, logs):
    run(alert_body(resident_id="unassigned"))
    run(alert_body(resident_id="unassigned"))

    assert aws.sns.publishes == []
    assert len(alert_items(aws)) == 1
    assert [r["outcome"] for r in logs()] == ["skipped_unassigned", "duplicate"]


# ---- latest vitals (criterion 16.1) ----


def test_latest_vitals_from_newest_entry_are_added(aws):
    put_vitals(
        aws,
        [
            vitals_entry("2026-10-18T03:13:00.000Z", 72, 97),
            vitals_entry("2026-10-18T03:12:00.000Z", 70, 96),
        ],
    )

    run(alert_body())

    assert only_item(aws)["details"]["latest_vitals"] == {
        "hr_bpm": 72,
        "spo2_pct": 97,
        "ts": "2026-10-18T03:13:00.000Z",
    }
    message = aws.sns.publishes[0]["Message"]
    assert "heart rate 72 bpm, SpO2 97 %" in message
    assert "11:13:00 SGT" in message


def test_null_latest_vitals_are_stored_as_null_and_print_no_reading(aws):
    put_vitals(aws, [vitals_entry("2026-10-18T03:13:00.000Z", None, None)])

    run(alert_body())

    vitals = only_item(aws)["details"]["latest_vitals"]
    assert vitals["hr_bpm"] is None
    assert vitals["spo2_pct"] is None
    message = aws.sns.publishes[0]["Message"]
    assert "heart rate no reading, SpO2 no reading" in message


def test_no_vitals_item_means_no_latest_vitals(aws):
    run(alert_body())

    assert "latest_vitals" not in only_item(aws)["details"]
    assert "Latest vitals: none received" in aws.sns.publishes[0]["Message"]


def test_other_devices_vitals_are_not_used(aws):
    put_vitals(aws, [vitals_entry("2026-10-18T03:13:00.000Z", 72, 97)], "wearable-0001")

    run(alert_body())

    assert "latest_vitals" not in only_item(aws)["details"]


def test_only_the_three_vital_fields_are_copied(aws):
    put_vitals(aws, [vitals_entry("2026-10-18T03:13:00.000Z", 72, 97)])

    run(alert_body())

    assert set(only_item(aws)["details"]["latest_vitals"]) == {
        "hr_bpm",
        "spo2_pct",
        "ts",
    }


# ---- invalid bodies and partial batches (criteria 14, 15) ----


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        None,
        "{}",
        alert_body(alert_type="vitals_alert"),
        alert_body(alert_id="not-a-uuid"),
        alert_body(schema_version=2),
        alert_body(extra="x"),
    ],
)
def test_invalid_body_is_reported_as_failure_and_nothing_is_written(aws, logs, body):
    assert run(body) == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]}

    assert alert_items(aws) == []
    assert archive_keys(aws) == []
    assert aws.sns.publishes == []
    (rec,) = logs()
    assert rec["outcome"] == "invalid"
    assert rec["reason"]
    assert rec["level"] == "WARNING"


def test_partial_batch_failure_reports_only_the_failed_id(aws):
    result = run(
        alert_body(),
        alert_body(alert_type="unknown"),
        alert_body(alert_id=ALERT_ID_2, event_ts="2026-10-18T03:20:00.000Z"),
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0002"}]}
    assert {i["alert_id"] for i in alert_items(aws)} == {ALERT_ID, ALERT_ID_2}
    assert len(aws.sns.publishes) == 2


def test_aws_failure_on_one_record_reports_only_that_id(aws, monkeypatch):
    real = aws.sns
    calls = {"n": 0}

    class FailSecond:
        def publish(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ClientError(
                    {"Error": {"Code": "ThrottlingException", "Message": "x"}},
                    "Publish",
                )
            return real.publish(**kwargs)

    monkeypatch.setattr(handler, "sns_client", FailSecond)

    result = run(
        alert_body(),
        alert_body(alert_id=ALERT_ID_2, event_ts="2026-10-18T03:20:00.000Z"),
        alert_body(alert_id=ALERT_ID_3, event_ts="2026-10-18T03:25:00.000Z"),
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-msg-0002"}]}


def test_empty_event_reports_no_failures(aws):
    assert handler.lambda_handler({"Records": []}, None) == {"batchItemFailures": []}


# ---- AWS errors (criterion 17) ----


@pytest.mark.parametrize(
    ("client_fn", "operation"),
    [
        ("dynamodb_client", "get_item"),
        ("dynamodb_client", "put_item"),
        ("dynamodb_client", "update_item"),
        ("s3_client", "put_object"),
    ],
)
def test_aws_error_in_a_step_fails_the_record(
    aws, monkeypatch, logs, client_fn, operation
):
    real = getattr(handler, client_fn)()
    failing = FailingClient(real, operation, "ProvisionedThroughputExceededException")
    monkeypatch.setattr(handler, client_fn, lambda: failing)

    assert run(alert_body()) == {
        "batchItemFailures": [{"itemIdentifier": "sqs-msg-0001"}]
    }

    (rec,) = logs()
    assert_only_allowed_keys(rec)
    assert rec["outcome"] == "error"
    assert rec["error"] == "ProvisionedThroughputExceededException"


def test_store_failure_publishes_nothing(aws, monkeypatch):
    failing = FailingClient(handler.dynamodb_client(), "put_item")
    monkeypatch.setattr(handler, "dynamodb_client", lambda: failing)

    run(alert_body())

    assert aws.sns.publishes == []
    assert archive_keys(aws) == []


def test_archive_failure_publishes_nothing(aws, monkeypatch):
    failing = FailingClient(handler.s3_client(), "put_object")
    monkeypatch.setattr(handler, "s3_client", lambda: failing)

    run(alert_body())

    assert aws.sns.publishes == []


# ---- logging (criterion 20) ----


def test_one_log_line_per_message_with_ids_and_outcome(aws, logs):
    run(alert_body())

    (rec,) = logs()
    assert_only_allowed_keys(rec)
    assert rec["message"] == "alert"
    assert rec["alert_id"] == ALERT_ID
    assert rec["alert_type"] == "fall"
    assert rec["severity"] == "critical"
    assert rec["device_id"] == DEVICE
    assert rec["resident_id"] == RESIDENT
    assert rec["outcome"] == "notified"


def test_logs_contain_no_health_values_details_or_email_text(aws, logs):
    put_vitals(aws, [vitals_entry("2026-10-18T03:13:00.000Z", 187, 83)])
    body = alert_body(details={"confidence": 0.87, "peak_g": 4.6, "still_s": 33})

    run(body)
    run(body)  # duplicate

    records = logs()
    assert [r["outcome"] for r in records] == ["notified", "duplicate"]
    # timestamp, location (line numbers), and the latency are numbers unrelated to
    # the data; the IDs are fixed, so no random UUID can match.
    noisy = {"timestamp", "location", "dispatch_latency_ms"}
    text = " ".join(f"{k}={v}" for r in records for k, v in r.items() if k not in noisy)
    for hidden in (
        "187",
        "83",
        "0.87",
        "4.6",
        "33",
        "details",
        "vitals",
        "confidence",
        "SilverCare alert",
        "Subject",
        "@",
        "portal",
    ):
        assert hidden not in text, hidden
    for rec in records:
        assert_only_allowed_keys(rec)


def test_invalid_body_logs_no_free_text(aws, logs):
    body = alert_body(resident_id="Test Resident A hr 187", device_id="hr=187")

    run(body)

    (rec,) = logs()
    assert_only_allowed_keys(rec)
    assert rec["resident_id"] == "invalid"
    assert rec["device_id"] == "invalid"
    text = " ".join(
        str(v) for k, v in rec.items() if k not in {"timestamp", "location"}
    )
    assert "Test Resident" not in text
    assert "187" not in text
