"""Tests for the history Lambda (docs/features/ingest-history.md, criteria 13-19, 25).

Pure logic is tested directly; the handler runs against moto S3. All data is synthetic.
"""

from __future__ import annotations

import gzip
import json
import re
import uuid
from datetime import UTC, datetime

import handler
import pytest
from botocore.exceptions import ClientError
from conftest import BUCKET
from events import (
    BASE_MS,
    BEDROOM,
    WEARABLE,
    body,
    epoch_ms,
    record,
    sqs_event,
)

KEY_RE = re.compile(
    r"^telemetry/message=(?P<message>[a-z]{1,16})/date=(?P<date>\d{4}-\d{2}-\d{2})/"
    r"(?P<stamp>\d{8}T\d{6}Z)-(?P<uuid>[0-9a-f-]{36})\.jsonl\.gz$"
)
NOW = datetime(2026, 10, 18, 3, 20, 5, tzinfo=UTC)


# ---- helpers ----


def all_keys(s3) -> list[str]:
    resp = s3.list_objects_v2(Bucket=BUCKET)
    return sorted(obj["Key"] for obj in resp.get("Contents", []))


def read_lines(s3, key: str) -> list[dict]:
    data = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    text = gzip.decompress(data).decode("utf-8")
    assert text.endswith("\n")
    return [json.loads(line) for line in text[:-1].split("\n")]


def key_for(s3, message: str, date: str | None = None) -> str:
    matches = [
        k
        for k in all_keys(s3)
        if KEY_RE.match(k)["message"] == message
        and (date is None or KEY_RE.match(k)["date"] == date)
    ]
    assert len(matches) == 1, matches
    return matches[0]


class FailingS3:
    """Wraps a real (moto) client; put_object fails for keys containing `fail_on`."""

    def __init__(self, real, fail_on: tuple[str, ...], exc: Exception | None = None):
        self.real = real
        self.fail_on = fail_on
        self.exc = exc or ClientError(
            {"Error": {"Code": "SlowDown", "Message": "synthetic throttle"}},
            "PutObject",
        )
        self.attempted: list[str] = []

    def put_object(self, **kwargs):
        self.attempted.append(kwargs["Key"])
        if any(part in kwargs["Key"] for part in self.fail_on):
            raise self.exc
        return self.real.put_object(**kwargs)


@pytest.fixture
def fixed_now(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(handler, "datetime", FixedDatetime)
    return NOW


# ---- parse_body (criterion 14) ----


def test_parse_body_returns_message_and_object():
    b = body("vitals")

    assert handler.parse_body(json.dumps(b)) == ("vitals", b)


@pytest.mark.parametrize("message", ["a", "a" * 16, "status", "somethingnew"])
def test_parse_body_keeps_any_lowercase_message_up_to_16_letters(message):
    assert handler.parse_body(json.dumps(body(message)))[0] == message


@pytest.mark.parametrize(
    "message",
    ["", "a" * 17, "Vitals", "vitals1", "vi-tals", "vitals\n", "../x", 5, None, True],
)
def test_parse_body_bad_message_becomes_unknown(message):
    b = body("vitals", message=message)

    assert handler.parse_body(json.dumps(b)) == ("unknown", b)


def test_parse_body_missing_message_becomes_unknown():
    b = body("vitals", message="_DELETE")

    assert handler.parse_body(json.dumps(b)) == ("unknown", b)


@pytest.mark.parametrize(
    "raw",
    ["not json", "[1, 2]", "42", '"vitals"', "null", "", "{", '{"a": NaN}', "Infinity"],
)
def test_parse_body_non_object_is_unparseable_raw_body(raw):
    assert handler.parse_body(raw) == ("unparseable", {"raw_body": raw})


def test_parse_body_none_is_unparseable_empty_raw_body():
    assert handler.parse_body(None) == ("unparseable", {"raw_body": ""})


def test_parse_body_deeply_nested_json_does_not_raise():
    raw = "[" * 100_000 + "]" * 100_000

    assert handler.parse_body(raw) == ("unparseable", {"raw_body": raw})


# ---- record_date (criterion 14) ----


def test_date_comes_from_received_at():
    stored = body(received_at=epoch_ms(2026, 10, 17, 23, 59, 59))
    rec = record(stored, sent_timestamp=str(epoch_ms(2026, 10, 18, 0, 0, 1)))

    assert handler.record_date(stored, rec, NOW) == "2026-10-17"


def test_date_at_utc_midnight_boundary():
    before = body(received_at=epoch_ms(2026, 10, 17, 23, 59, 59) + 999)
    at = body(received_at=epoch_ms(2026, 10, 18))

    assert handler.record_date(before, record(before), NOW) == "2026-10-17"
    assert handler.record_date(at, record(at), NOW) == "2026-10-18"


def test_date_is_utc_not_singapore_time():
    # 2026-10-17T20:00Z is already 2026-10-18 in Singapore (UTC+8).
    stored = body(received_at=epoch_ms(2026, 10, 17, 20, 0, 0))

    assert handler.record_date(stored, record(stored), NOW) == "2026-10-17"


def test_received_at_zero_is_valid():
    stored = body(received_at=0)

    assert handler.record_date(stored, record(stored), NOW) == "1970-01-01"


@pytest.mark.parametrize(
    "bad",
    [-1, "1792293300120", 1792293300120.0, True, None, [1], 10**30, "_DELETE"],
)
def test_bad_received_at_falls_back_to_sent_timestamp(bad):
    stored = body(received_at=bad)
    rec = record(stored, sent_timestamp=str(epoch_ms(2026, 9, 1, 12, 0, 0)))

    assert handler.record_date(stored, rec, NOW) == "2026-09-01"


@pytest.mark.parametrize("sent", [None, "", "abc", "-5", "1.5e12"])
def test_no_usable_timestamp_falls_back_to_time_of_writing(sent):
    stored = body(received_at="bad")
    rec = record(stored)
    if sent is None:
        del rec["attributes"]["SentTimestamp"]
    else:
        rec["attributes"]["SentTimestamp"] = sent

    assert handler.record_date(stored, rec, NOW) == "2026-10-18"


# ---- serialize / key / encode (criteria 13, 15) ----


def test_serialize_is_compact_json():
    line = handler.serialize({"a": 1, "b": [1, 2], "c": {"d": "e f"}})

    assert line == '{"a":1,"b":[1,2],"c":{"d":"e f"}}'


def test_object_key_format():
    now = datetime(2026, 10, 18, 3, 4, 5, tzinfo=UTC)
    unique = "11111111-2222-4333-8444-555555555555"

    key = handler.object_key("vitals", "2026-10-17", now, unique)

    assert key == (
        "telemetry/message=vitals/date=2026-10-17/"
        "20261018T030405Z-11111111-2222-4333-8444-555555555555.jsonl.gz"
    )


def test_encode_lines_round_trips_and_ends_with_newline():
    lines = ['{"a":1}', '{"b":"\\u00e9"}']

    text = gzip.decompress(handler.encode_lines(lines)).decode("utf-8")

    assert text == '{"a":1}\n{"b":"\\u00e9"}\n'


# ---- group_records (criteria 13, 14) ----


def test_groups_by_message_type_keeping_order():
    v1, s1, v2 = body("vitals"), body("status"), body("vitals")
    recs = [record(v1), record(s1), record(v2)]

    groups = handler.group_records(recs, NOW)

    assert [(g.message, g.date) for g in groups] == [
        ("vitals", "2026-10-18"),
        ("status", "2026-10-18"),
    ]
    assert groups[0].message_ids == [recs[0]["messageId"], recs[2]["messageId"]]
    assert [json.loads(line) for line in groups[0].lines] == [v1, v2]


def test_batch_across_utc_midnight_splits_same_type_into_two_groups():
    late = body("vitals", received_at=epoch_ms(2026, 10, 17, 23, 59, 59))
    early = body("vitals", received_at=epoch_ms(2026, 10, 18, 0, 0, 0))

    groups = handler.group_records([record(late), record(early)], NOW)

    assert [(g.message, g.date, len(g.lines)) for g in groups] == [
        ("vitals", "2026-10-17", 1),
        ("vitals", "2026-10-18", 1),
    ]


def test_unparseable_record_dated_by_sent_timestamp():
    rec = record("garbage{", sent_timestamp=str(epoch_ms(2026, 8, 31, 23, 0, 0)))

    [group] = handler.group_records([rec], NOW)

    assert (group.message, group.date) == ("unparseable", "2026-08-31")
    assert group.lines == ['{"raw_body":"garbage{"}']


def test_group_records_skips_non_dict_records_without_raising():
    good = record(body("bed"))

    groups = handler.group_records(["junk", None, 5, good], NOW)

    assert [g.message for g in groups] == ["bed"]


# ---- handler with moto S3 ----


def test_valid_batch_writes_one_object_per_group(history_bucket, fixed_now):
    bodies = [body("vitals"), body("fall"), body("vitals"), body("status")]

    result = handler.lambda_handler(sqs_event(*map(record, bodies)), None)

    assert result == {"batchItemFailures": []}
    keys = all_keys(history_bucket)
    assert len(keys) == 3
    assert {KEY_RE.match(k)["message"] for k in keys} == {"vitals", "fall", "status"}
    assert read_lines(history_bucket, key_for(history_bucket, "vitals")) == [
        bodies[0],
        bodies[2],
    ]


def test_key_uses_time_of_writing_and_random_uuid4(history_bucket, fixed_now):
    handler.lambda_handler(
        sqs_event(record(body("vitals")), record(body("status"))), None
    )

    keys = all_keys(history_bucket)
    for key in keys:
        m = KEY_RE.match(key)
        assert m, key
        assert m["date"] == "2026-10-18"
        assert m["stamp"] == "20261018T032005Z"
        assert uuid.UUID(m["uuid"]).version == 4
    assert len({KEY_RE.match(k)["uuid"] for k in keys}) == 2


def test_key_date_is_message_date_not_write_date(history_bucket, fixed_now):
    old = body("bed", received_at=epoch_ms(2026, 10, 1, 9, 0, 0))

    handler.lambda_handler(sqs_event(record(old)), None)

    [key] = all_keys(history_bucket)
    assert key.startswith("telemetry/message=bed/date=2026-10-01/20261018T032005Z-")


def test_batch_across_midnight_writes_two_objects(history_bucket):
    late = body("motion", received_at=epoch_ms(2026, 10, 17, 23, 59, 59) + 999)
    early = body("motion", received_at=epoch_ms(2026, 10, 18, 0, 0, 0))

    handler.lambda_handler(sqs_event(record(late), record(early)), None)

    assert read_lines(
        history_bucket, key_for(history_bucket, "motion", "2026-10-17")
    ) == [late]
    assert read_lines(
        history_bucket, key_for(history_bucket, "motion", "2026-10-18")
    ) == [early]


def test_object_is_gzip_jsonl_compact_utf8_with_trailing_newline(history_bucket):
    bodies = [body("vitals", note="caf\u00e9 synthetic"), body("vitals")]

    handler.lambda_handler(sqs_event(*map(record, bodies)), None)

    key = key_for(history_bucket, "vitals")
    raw = history_bucket.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    text = gzip.decompress(raw).decode("utf-8")
    lines = text.split("\n")
    assert lines[-1] == ""
    assert len(lines) == 3
    for line, original in zip(lines[:2], bodies, strict=True):
        assert json.loads(line) == original
        assert line == json.dumps(json.loads(line), separators=(",", ":"))


def test_put_object_content_type_gzip_and_no_content_encoding(history_bucket):
    handler.lambda_handler(sqs_event(record(body("fall"))), None)

    [key] = all_keys(history_bucket)
    head = history_bucket.head_object(Bucket=BUCKET, Key=key)
    assert head["ContentType"] == "application/gzip"
    assert "ContentEncoding" not in head


def test_invalid_messages_are_stored_exactly_as_received(history_bucket):
    weird = [
        body("vitals", hr_bpm=999, unexpected_field={"x": [1, 2]}),
        body("vitals", schema_version=99),
        body("vitals", message_id="_DELETE", ts="_DELETE"),
        body("vitals", device_id="not-a-device"),
    ]

    handler.lambda_handler(sqs_event(*map(record, weird)), None)

    assert read_lines(history_bucket, key_for(history_bucket, "vitals")) == weird


def test_unknown_message_type_goes_to_message_unknown(history_bucket):
    b = body("vitals", message="NOT_VALID")

    handler.lambda_handler(sqs_event(record(b)), None)

    [key] = all_keys(history_bucket)
    assert key.startswith("telemetry/message=unknown/date=2026-10-18/")
    assert read_lines(history_bucket, key) == [b]


def test_unparseable_body_goes_to_message_unparseable_as_raw_body(history_bucket):
    rec = record('["not", "an object"]', sent_timestamp=str(epoch_ms(2026, 7, 4)))

    result = handler.lambda_handler(sqs_event(rec), None)

    assert result == {"batchItemFailures": []}
    [key] = all_keys(history_bucket)
    assert key.startswith("telemetry/message=unparseable/date=2026-07-04/")
    assert read_lines(history_bucket, key) == [{"raw_body": '["not", "an object"]'}]


def test_sent_timestamp_fallback_used_by_handler(history_bucket):
    b = body("status", received_at="_DELETE")
    rec = record(b, sent_timestamp=str(epoch_ms(2026, 10, 16, 6, 0, 0)))

    handler.lambda_handler(sqs_event(rec), None)

    [key] = all_keys(history_bucket)
    assert "/date=2026-10-16/" in key


def test_partial_failure_returns_only_failed_group_ids(history_bucket, monkeypatch):
    recs = [
        record(body("vitals")),
        record(body("fall")),
        record(body("vitals")),
        record(body("fall")),
        record(body("status")),
    ]
    failing = FailingS3(history_bucket, ("message=fall/",))
    monkeypatch.setattr(handler, "s3_client", lambda: failing)

    result = handler.lambda_handler(sqs_event(*recs), None)

    assert result == {
        "batchItemFailures": [
            {"itemIdentifier": recs[1]["messageId"]},
            {"itemIdentifier": recs[3]["messageId"]},
        ]
    }
    assert {KEY_RE.match(k)["message"] for k in all_keys(history_bucket)} == {
        "vitals",
        "status",
    }


def test_all_groups_failing_returns_every_record(history_bucket, monkeypatch):
    recs = [record(body("vitals")), record("junk"), record(body("bed"))]
    failing = FailingS3(history_bucket, ("telemetry/",))
    monkeypatch.setattr(handler, "s3_client", lambda: failing)

    result = handler.lambda_handler(sqs_event(*recs), None)

    assert sorted(f["itemIdentifier"] for f in result["batchItemFailures"]) == sorted(
        r["messageId"] for r in recs
    )
    assert all_keys(history_bucket) == []


def test_non_aws_exception_in_write_is_reported_not_raised(history_bucket, monkeypatch):
    recs = [record(body("vitals")), record(body("status"))]
    failing = FailingS3(history_bucket, ("message=vitals/",), RuntimeError("boom"))
    monkeypatch.setattr(handler, "s3_client", lambda: failing)

    result = handler.lambda_handler(sqs_event(*recs), None)

    assert result == {"batchItemFailures": [{"itemIdentifier": recs[0]["messageId"]}]}


def test_bucket_missing_reports_all_records(history_bucket, monkeypatch):
    monkeypatch.setenv("HISTORY_BUCKET", "silvercare-history-999999999999")
    recs = [record(body("vitals"))]

    result = handler.lambda_handler(sqs_event(*recs), None)

    assert result == {"batchItemFailures": [{"itemIdentifier": recs[0]["messageId"]}]}


@pytest.mark.parametrize("event", [{"Records": []}, {}, {"Records": None}])
def test_empty_batch_writes_nothing(history_bucket, event):
    assert handler.lambda_handler(event, None) == {"batchItemFailures": []}
    assert all_keys(history_bucket) == []


def test_garbage_records_never_raise(history_bucket):
    event = {"Records": [{}, {"messageId": "sqs-test-x"}, {"body": 12345}]}

    result = handler.lambda_handler(event, None)

    assert result == {"batchItemFailures": []}
    [key] = all_keys(history_bucket)
    assert "message=unparseable" in key
    assert read_lines(history_bucket, key) == [
        {"raw_body": ""},
        {"raw_body": ""},
        {"raw_body": "12345"},
    ]


def test_redelivered_batch_stores_message_twice(history_bucket):
    # Criterion 19: duplicates are accepted and removed later by message_id.
    event = sqs_event(record(body("vitals")))

    handler.lambda_handler(event, None)
    handler.lambda_handler(event, None)

    keys = all_keys(history_bucket)
    assert len(keys) == 2
    assert read_lines(history_bucket, keys[0]) == read_lines(history_bucket, keys[1])


# ---- logs (criterion 18) ----


def test_logs_one_batch_line_and_one_line_per_object(history_bucket, logs):
    recs = [record(body("vitals")), record(body("vitals")), record(body("bed"))]

    handler.lambda_handler(sqs_event(*recs), None)

    lines = logs()
    batch = [r for r in lines if r["message"] == "history batch"]
    written = [r for r in lines if r["message"] == "history object written"]
    assert len(batch) == 1
    assert batch[0]["record_count"] == 3
    assert sorted(
        (r["message_type"], r["date"], r["record_count"]) for r in written
    ) == [
        ("bed", "2026-10-18", 1),
        ("vitals", "2026-10-18", 2),
    ]
    assert {r["key"] for r in written} == set(all_keys(history_bucket))


def test_logs_contain_no_bodies_ids_or_values(history_bucket, logs, monkeypatch):
    bodies = [
        body("vitals", hr_bpm=187, note="synthetic-secret-note"),
        body("bed", occupied=True),
    ]
    recs = [record(b) for b in bodies] + [record("raw-synthetic-garbage")]
    failing = FailingS3(
        history_bucket,
        ("message=bed/",),
        RuntimeError("leak synthetic-secret-note " + bodies[1]["message_id"]),
    )
    monkeypatch.setattr(handler, "s3_client", lambda: failing)

    handler.lambda_handler(sqs_event(*recs), None)

    text = logs.raw.getvalue()
    assert "history object write failed" in text
    forbidden = [
        "synthetic-secret-note",
        "raw-synthetic-garbage",
        "187",
        "hr_bpm",
        "occupied",
        WEARABLE,
        BEDROOM,
        str(BASE_MS),
    ]
    forbidden += [b["message_id"] for b in bodies]
    forbidden += [r["messageId"] for r in recs]
    for value in forbidden:
        assert value not in text, value


def test_failure_log_has_error_code_and_group_details(
    history_bucket, logs, monkeypatch
):
    failing = FailingS3(history_bucket, ("telemetry/",))
    monkeypatch.setattr(handler, "s3_client", lambda: failing)

    handler.lambda_handler(sqs_event(record(body("fall"))), None)

    [failed] = [r for r in logs() if r["message"] == "history object write failed"]
    assert failed["error"] == "SlowDown"
    assert (failed["message_type"], failed["record_count"]) == ("fall", 1)
    assert "synthetic throttle" not in logs.raw.getvalue()
