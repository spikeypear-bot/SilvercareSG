"""The load (nightly-results.md criteria 2, 5-8, 11): list summaries/date=<D>/
(paginated), read each object, write valid ones with an unconditional PutItem, skip and
log invalid ones by key only and raise afterwards, and treat no objects as a warning.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import handler
import pytest
from botocore.exceptions import ClientError
from events import (
    DATE,
    LOADED_AT,
    LOADED_AT_TEXT,
    LOG_SENTINELS,
    NEXT_DATE,
    RESIDENT_A,
    RESIDENT_B,
    RESIDENT_C,
    empty_summary,
    key,
    on_success,
    summary,
    without,
)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(handler, "now_utc", lambda: LOADED_AT)


def _throttled(op: str) -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "synthetic",
            }
        },
        op,
    )


# ---- valid input: item shape (criterion 2) ----


def test_valid_summary_is_written_as_one_item_with_the_spec_shape(aws):
    aws.put_summary(key(1), summary())
    result = handler.lambda_handler({"date": DATE}, None)
    assert result == {"date": DATE, "loaded": 1}
    (item,) = aws.items()
    assert item["pk"] == "RESIDENT#res-0003"
    assert item["sk"] == "NIGHT#2026-11-18"
    assert item["resident_id"] == RESIDENT_A
    assert item["date"] == DATE
    assert item["schema_version"] == 1
    assert item["loaded_at"] == LOADED_AT_TEXT
    assert set(item) == {
        "pk",
        "sk",
        "resident_id",
        "date",
        "summary",
        "schema_version",
        "loaded_at",
        "expires_at",
    }


def test_summary_map_holds_every_field_except_the_four_not_copied(aws):
    source = summary()
    aws.put_summary(key(1), source)
    handler.lambda_handler({"date": DATE}, None)
    (item,) = aws.items()
    expected = {
        k: v
        for k, v in source.items()
        if k not in {"resident_id", "date", "generated_at", "schema_version"}
    }
    assert set(item["summary"]) == set(expected)
    assert item["summary"]["hr_bpm_avg"] == Decimal("71.4")
    assert item["summary"]["vitals_readings"] == 1312
    assert item["summary"]["thresholds"] == {
        "hr_bpm": {"min": 50, "max": 120},
        "spo2_pct": {"min": 92},
        "skin_temp_c": {"min": 30, "max": 38},
    }


def test_numbers_are_dynamodb_numbers_and_thresholds_a_map(aws):
    aws.put_summary(key(1), summary())
    handler.lambda_handler({"date": DATE}, None)
    raw = aws.raw_item(RESIDENT_A, DATE)
    fields = raw["summary"]["M"]
    assert fields["hr_bpm_avg"] == {"N": "71.4"}
    assert set(fields["vitals_readings"]) == {"N"}
    assert set(fields["thresholds"]) == {"M"}
    assert set(fields["thresholds"]["M"]["spo2_pct"]["M"]["min"]) == {"N"}
    assert set(raw["expires_at"]) == {"N"}
    assert set(raw["schema_version"]) == {"N"}


def test_nulls_are_stored_as_dynamodb_null(aws):
    aws.put_summary(key(1), empty_summary())
    handler.lambda_handler({"date": DATE}, None)
    fields = aws.raw_item(RESIDENT_B, DATE)["summary"]["M"]
    for name in ("hr_bpm_min", "hr_bpm_avg", "skin_temp_c_max", "bed_minutes"):
        assert fields[name] == {"NULL": True}, name
    assert fields["vitals_readings"] == {"N": "0"}


def test_expires_at_is_midnight_sgt_on_the_date_plus_31_days():
    # 00:00 SGT on 2026-12-19 (= 2026-11-18 + 31 days) is 16:00 UTC on 2026-12-18.
    expected = int(datetime(2026, 12, 18, 16, 0, tzinfo=UTC).timestamp())
    assert handler.expires_at("2026-11-18") == expected


def test_expires_at_crosses_a_year_end():
    expected = int(datetime(2027, 1, 30, 16, 0, tzinfo=UTC).timestamp())
    assert handler.expires_at("2026-12-31") == expected


def test_stored_expires_at_matches_the_date(aws):
    aws.put_summary(key(1), summary())
    handler.lambda_handler({"date": DATE}, None)
    (item,) = aws.items()
    assert item["expires_at"] == handler.expires_at(DATE)


def test_loaded_at_has_milliseconds_and_z():
    moment = datetime(2026, 11, 19, 0, 41, 7, 5000, tzinfo=UTC)
    assert handler.iso_ms(moment) == "2026-11-19T00:41:07.005Z"


def test_on_success_record_loads_its_date(aws):
    aws.put_summary(key(1), summary())
    aws.put_summary(key(2), empty_summary())
    result = handler.lambda_handler(on_success(summaries=2), None)
    assert result == {"date": DATE, "loaded": 2}
    assert {i["resident_id"] for i in aws.items()} == {RESIDENT_A, RESIDENT_B}


# ---- what it reads (criterion 5) ----


def test_lists_only_the_dates_prefix(aws):
    aws.put_summary(key(1), summary())
    aws.put_summary(key(2, NEXT_DATE), summary(date=NEXT_DATE))
    aws.put_object("telemetry/date=2026-11-18/x.json", "{}")
    assert handler.lambda_handler({"date": DATE}, None)["loaded"] == 1
    (listing,) = aws.params("s3", "ListObjectsV2")
    assert listing["Prefix"] == "summaries/date=2026-11-18/"
    gets = [p["Key"] for p in aws.params("s3", "GetObject")]
    assert gets == [key(1)]


def test_listing_follows_continuation_tokens(aws, monkeypatch):
    monkeypatch.setattr(handler, "LIST_PAGE", 2)
    for n in range(5):
        aws.put_summary(key(n + 1), summary(resident_id=f"res-{n + 11:04d}"))
    result = handler.lambda_handler({"date": DATE}, None)
    assert result == {"date": DATE, "loaded": 5}
    listings = aws.params("s3", "ListObjectsV2")
    assert len(listings) == 3
    assert "ContinuationToken" not in listings[0]
    assert all("ContinuationToken" in p for p in listings[1:])
    assert len(aws.items()) == 5


def test_handler_makes_no_other_aws_calls(aws):
    aws.put_summary(key(1), summary())
    handler.lambda_handler({"date": DATE}, None)
    assert set(aws.ops("s3")) == {"ListObjectsV2", "GetObject"}
    assert set(aws.ops("dynamodb")) == {"put_item"}


# ---- no objects (criterion 8) ----


def test_no_objects_logs_a_warning_and_returns_zero(aws, logs):
    result = handler.lambda_handler({"date": DATE}, None)
    assert result == {"date": DATE, "loaded": 0}
    assert aws.ops("dynamodb") == []
    warnings = [r for r in logs() if r["level"] == "WARNING"]
    assert warnings and warnings[0]["date"] == DATE


def test_no_objects_for_this_date_ignores_other_dates(aws):
    aws.put_summary(key(1, NEXT_DATE), summary(date=NEXT_DATE))
    assert handler.lambda_handler({"date": DATE}, None) == {"date": DATE, "loaded": 0}
    assert aws.items() == []


# ---- invalid objects are skipped, then the run raises (criteria 5, 7) ----

INVALID_BODIES = {
    "not-json": b"{not json",
    "not-utf8": b"\xff\xfe\x00",
    "array": json.dumps([summary()]).encode(),
    "two-objects": (json.dumps(summary()) + json.dumps(summary())).encode(),
    "schema-2": json.dumps(summary(schema_version=2)).encode(),
    "schema-bool": json.dumps(summary(schema_version=True)).encode(),
    "schema-string": json.dumps(summary(schema_version="1")).encode(),
    "schema-missing": json.dumps(without("schema_version")).encode(),
    "resident-bad": json.dumps(summary(resident_id="res-03")).encode(),
    "resident-name": json.dumps(summary(resident_id="Test Resident A")).encode(),
    "resident-trailing": json.dumps(summary(resident_id="res-0003\n")).encode(),
    "resident-missing": json.dumps(without("resident_id")).encode(),
    "date-other": json.dumps(summary(date=NEXT_DATE)).encode(),
    "date-missing": json.dumps(without("date")).encode(),
    "unknown-field": json.dumps(summary(notes="synthetic")).encode(),
    "field-missing": json.dumps(without("falls")).encode(),
    "count-string": json.dumps(summary(falls="0")).encode(),
    "count-negative": json.dumps(summary(falls=-1)).encode(),
    "count-null": json.dumps(summary(vitals_readings=None)).encode(),
    "count-float": json.dumps(summary(falls=1.5)).encode(),
    "count-bool": json.dumps(summary(falls=False)).encode(),
    "int-stat-float": json.dumps(summary(hr_bpm_min=52.5)).encode(),
    "avg-string": json.dumps(summary(hr_bpm_avg="71.4")).encode(),
    "nan": json.dumps(summary(hr_bpm_avg=float("nan"))).encode(),
    "infinity": json.dumps(summary(skin_temp_c_max=float("inf"))).encode(),
    "thresholds-list": json.dumps(summary(thresholds=[])).encode(),
    "thresholds-extra-metric": json.dumps(
        summary(
            thresholds={
                **summary()["thresholds"],
                "activity_pct": {"min": 1},
            }
        )
    ).encode(),
    "thresholds-spo2-max": json.dumps(
        summary(
            thresholds={
                **summary()["thresholds"],
                "spo2_pct": {"min": 92, "max": 100},
            }
        )
    ).encode(),
    "thresholds-string": json.dumps(
        summary(
            thresholds={
                **summary()["thresholds"],
                "hr_bpm": {"min": "50", "max": 120},
            }
        )
    ).encode(),
    "generated-at-number": json.dumps(summary(generated_at=1)).encode(),
    "too-big": json.dumps(summary()).encode() + b" " * (64 * 1024),
}


@pytest.mark.parametrize("name", sorted(INVALID_BODIES))
def test_invalid_object_is_skipped_and_the_run_raises(aws, name):
    aws.put_summary(key(1), summary())
    aws.put_object(key(2), INVALID_BODIES[name])
    with pytest.raises(handler.SkippedObjectsError):
        handler.lambda_handler({"date": DATE}, None)
    assert [i["resident_id"] for i in aws.items()] == [RESIDENT_A]


def test_valid_objects_are_written_before_the_run_raises(aws):
    aws.put_summary(key(1), summary())
    aws.put_object(key(2), b"{not json")
    aws.put_summary(key(3), summary(resident_id=RESIDENT_C))
    with pytest.raises(handler.SkippedObjectsError):
        handler.lambda_handler({"date": DATE}, None)
    assert {i["resident_id"] for i in aws.items()} == {RESIDENT_A, RESIDENT_C}
    assert len(aws.params("dynamodb", "put_item")) == 2


def test_number_dynamodb_cannot_store_is_skipped(aws):
    # 40 significant digits: more than DynamoDB's 38.
    body = json.dumps(summary()).replace("71.4", "71." + "1" * 39)
    aws.put_object(key(1), body)
    with pytest.raises(handler.SkippedObjectsError):
        handler.lambda_handler({"date": DATE}, None)
    assert aws.items() == []


def test_valid_summary_with_nulls_and_zero_counts_is_loaded(aws):
    aws.put_summary(key(1), empty_summary())
    assert handler.lambda_handler({"date": DATE}, None)["loaded"] == 1


def test_integer_valued_float_statistics_are_accepted(aws):
    aws.put_summary(key(1), summary(skin_temp_c_max=35.0, hr_bpm_avg=71))
    assert handler.lambda_handler({"date": DATE}, None)["loaded"] == 1


def test_skipped_keys_are_logged_and_other_keys_as_invalid(aws, logs):
    bad_key = "summaries/date=2026-11-18/res-0003-synthetic-note.json"
    aws.put_summary(key(1), summary())
    aws.put_object(key(2), b"[]")
    aws.put_object(bad_key, b"[]")
    with pytest.raises(handler.SkippedObjectsError):
        handler.lambda_handler({"date": DATE}, None)
    (record,) = [r for r in logs() if r["level"] == "ERROR"]
    assert sorted(record["skipped_keys"]) == sorted([key(2), "invalid"])
    assert record["listed"] == 3
    assert record["loaded"] == 1
    assert record["skipped"] == 2
    assert record["date"] == DATE
    assert "synthetic-note" not in json.dumps(logs())


def test_error_message_has_counts_only(aws):
    aws.put_object(key(1), json.dumps(summary(resident_id="res-03")))
    with pytest.raises(handler.SkippedObjectsError) as caught:
        handler.lambda_handler({"date": DATE}, None)
    assert "res-03" not in str(caught.value)
    assert "1 of 1" in str(caught.value)


# ---- idempotency (criterion 6) ----


def test_rerun_overwrites_the_same_key(aws, monkeypatch):
    aws.put_summary(key(1), summary())
    handler.lambda_handler({"date": DATE}, None)
    aws.put_summary(key(1), summary(falls=2))
    later = datetime(2026, 11, 19, 3, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(handler, "now_utc", lambda: later)
    handler.lambda_handler({"date": DATE}, None)
    (item,) = aws.items()
    assert item["summary"]["falls"] == 2
    assert item["loaded_at"] == "2026-11-19T03:00:00.000Z"


def test_duplicate_delivery_writes_nothing_new(aws):
    aws.put_summary(key(1), summary())
    handler.lambda_handler(on_success(summaries=1), None)
    first = aws.raw_items()
    handler.lambda_handler(on_success(summaries=1), None)
    assert aws.raw_items() == first


def test_put_item_is_unconditional(aws):
    aws.put_summary(key(1), summary())
    handler.lambda_handler({"date": DATE}, None)
    (put,) = aws.params("dynamodb", "put_item")
    assert set(put) == {"TableName", "Item"}
    assert put["TableName"] == "silvercare-results"


# ---- transient errors (criterion 6) ----


def test_throttled_write_raises_so_lambda_retries(aws):
    aws.put_summary(key(1), summary())
    aws.dynamodb.fail["put_item"] = _throttled("PutItem")
    with pytest.raises(ClientError):
        handler.lambda_handler({"date": DATE}, None)


def test_s3_read_error_raises(aws, monkeypatch):
    aws.put_summary(key(1), summary())

    def fail(_key):
        raise _throttled("GetObject")

    monkeypatch.setattr(handler, "read_object", fail)
    with pytest.raises(ClientError):
        handler.lambda_handler({"date": DATE}, None)
    assert aws.items() == []


def test_dynamodb_client_uses_adaptive_retries(monkeypatch):
    monkeypatch.delenv("DYNAMODB_ENDPOINT", raising=False)
    handler.dynamodb_client.cache_clear()
    try:
        client = handler.dynamodb_client()
        assert client.meta.config.retries["mode"] == "adaptive"
    finally:
        handler.dynamodb_client.cache_clear()


# ---- logs (criterion 11) ----


def _log_text(records: list[dict]) -> str:
    return json.dumps(records)


def test_success_log_has_date_and_counts(aws, logs):
    aws.put_summary(key(1), summary())
    aws.put_summary(key(2), empty_summary())
    handler.lambda_handler({"date": DATE}, None)
    (record,) = [r for r in logs() if r["message"] == "results loaded"]
    assert record["date"] == DATE
    assert (record["listed"], record["loaded"], record["skipped"]) == (2, 2, 0)


def test_logs_never_hold_summary_values_or_bodies(aws, logs):
    aws.put_summary(key(1), summary())
    aws.put_object(key(2), json.dumps(summary(schema_version=2)))
    with pytest.raises(handler.SkippedObjectsError):
        handler.lambda_handler({"date": DATE}, None)
    records = logs()
    assert records
    text = _log_text(records)
    for value in LOG_SENTINELS:
        assert value not in text, value
    assert "hr_bpm" not in text
    assert RESIDENT_A not in text
