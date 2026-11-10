"""GET /residents/{resident_id}/nightly (nightly-results.md criteria 15-21, 33): group
check before validation before the read, 400/403/500, the 30-day filter, `not_ready`
around 09:30 SGT, paging, and logs without values.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import handler
import pytest
from botocore.exceptions import ClientError
from events import (
    BEFORE_READY,
    EXPECTED_NIGHT,
    LOG_SENTINELS,
    NOW,
    REQUEST_ID,
    RESIDENT_A,
    RESIDENT_B,
    RESIDENT_UNKNOWN,
    SUB,
    YESTERDAY,
    api_event,
    night_item,
    put_night,
    put_raw,
    sgt,
)


def body(resp: dict) -> dict:
    return json.loads(resp["body"])


def call(event: dict, now: datetime = NOW) -> dict:
    return handler.handle(event, now)


def _throttled() -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "synthetic detail qx-5521",
            }
        },
        "Query",
    )


# ---- valid request ----


def test_doctor_gets_the_spec_example(aws):
    put_night(aws.ddb)
    resp = call(api_event())
    assert resp["statusCode"] == 200
    assert body(resp) == {
        "resident_id": RESIDENT_A,
        "expected_date": YESTERDAY,
        "not_ready": False,
        "nights": [EXPECTED_NIGHT],
    }


def test_numbers_have_json_types(aws):
    put_night(aws.ddb)
    (night,) = body(call(api_event()))["nights"]
    assert type(night["vitals_readings"]) is int
    assert type(night["hr_bpm_min"]) is int
    assert type(night["thresholds"]["hr_bpm"]["max"]) is int
    assert type(night["hr_bpm_avg"]) is float
    assert type(night["skin_temp_c_max"]) is float
    assert type(night["thresholds"]["skin_temp_c"]["min"]) is float


def test_null_statistics_stay_null(aws):
    put_night(aws.ddb, hr_bpm_min=None, hr_bpm_avg=None, bed_minutes=None)
    (night,) = body(call(api_event()))["nights"]
    assert night["hr_bpm_min"] is None
    assert night["hr_bpm_avg"] is None
    assert night["bed_minutes"] is None


def test_response_holds_only_the_nights_fields(aws):
    put_night(aws.ddb)
    payload = body(call(api_event()))
    assert set(payload) == {"resident_id", "expected_date", "not_ready", "nights"}
    (night,) = payload["nights"]
    assert set(night) == set(EXPECTED_NIGHT)
    for hidden in ("loaded_at", "expires_at", "schema_version", "pk", "sk"):
        assert hidden not in night


def test_headers_are_json_and_no_store(aws):
    resp = call(api_event())
    assert resp["headers"] == {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
    }


def test_nights_are_newest_first(aws):
    for date in ("2026-11-10", YESTERDAY, "2026-11-15"):
        put_night(aws.ddb, date=date)
    dates = [n["date"] for n in body(call(api_event()))["nights"]]
    assert dates == [YESTERDAY, "2026-11-15", "2026-11-10"]


def test_only_the_residents_nights_are_returned(aws):
    put_night(aws.ddb, RESIDENT_A)
    put_night(aws.ddb, RESIDENT_B, date="2026-11-17")
    nights = body(call(api_event(RESIDENT_A)))["nights"]
    assert [n["date"] for n in nights] == [YESTERDAY]


def test_unknown_resident_returns_200_with_no_nights(aws):
    resp = call(api_event(RESIDENT_UNKNOWN), BEFORE_READY)
    assert resp["statusCode"] == 200
    assert body(resp)["nights"] == []
    assert body(resp)["resident_id"] == RESIDENT_UNKNOWN


def test_doctor_and_nurse_is_allowed(aws):
    put_night(aws.ddb)
    resp = call(api_event(groups=("nurse", "doctor")))
    assert resp["statusCode"] == 200


@pytest.mark.parametrize("groups", ["[doctor]", "[nurse doctor]", "doctor"])
def test_groups_as_the_authorizers_string_form(aws, groups):
    assert call(api_event(groups=groups))["statusCode"] == 200


def test_other_items_in_the_partition_are_ignored(aws):
    put_night(aws.ddb)
    item = night_item()
    item["sk"] = "PROFILE"
    put_raw(aws.ddb, item)
    nights = body(call(api_event()))["nights"]
    assert [n["date"] for n in nights] == [YESTERDAY]


def test_invalid_item_is_dropped_and_the_rest_returned(aws, logs):
    put_night(aws.ddb)
    put_night(aws.ddb, date="2026-11-17", falls="synthetic")
    put_night(aws.ddb, date="2026-11-16", hr_bpm_min=Decimal("52.5"))
    resp = call(api_event())
    assert resp["statusCode"] == 200
    assert [n["date"] for n in body(resp)["nights"]] == [YESTERDAY]
    (warning,) = [r for r in logs() if r.get("outcome") == "invalid_items"]
    assert warning["items"] == 2


def test_item_with_a_bad_night_date_is_dropped(aws):
    put_night(aws.ddb)
    item = night_item()
    item["sk"] = "NIGHT#2026-02-30"
    put_raw(aws.ddb, item)
    assert [n["date"] for n in body(call(api_event()))["nights"]] == [YESTERDAY]


# ---- criterion 16: check order ----


@pytest.mark.parametrize(
    "groups",
    [("nurse",), ("caregiver",), ("ops",), ("family",), (), ("Doctor",), ("doctors",)],
)
def test_non_doctors_get_403(no_aws, groups):
    resp = call(api_event(groups=groups))
    assert resp["statusCode"] == 403
    assert body(resp) == {
        "error": {
            "code": "forbidden",
            "message": "Only doctors can see nightly summaries.",
        }
    }
    assert resp["headers"]["Cache-Control"] == "no-store"


def test_missing_groups_claim_gets_403(no_aws):
    assert call(api_event(drop_groups=True))["statusCode"] == 403


def test_missing_authorizer_gets_403(no_aws):
    event = api_event()
    del event["requestContext"]["authorizer"]
    assert call(event)["statusCode"] == 403


def test_group_check_comes_before_resident_validation(no_aws):
    resp = call(api_event("not-a-resident", groups=("nurse",)))
    assert resp["statusCode"] == 403


@pytest.mark.parametrize(
    "resident_id",
    [
        "res-03",
        "res-00003",
        "RES-0003",
        "res-0003\n",
        " res-0003",
        "res-000a",
        "Test Resident A",
        "",
        None,
        3,
        ["res-0003"],
    ],
)
def test_invalid_resident_id_gets_400_without_a_read(no_aws, resident_id):
    resp = call(api_event(resident_id))
    assert resp["statusCode"] == 400
    assert body(resp) == {
        "error": {
            "code": "invalid_resident_id",
            "message": "The resident ID is not valid.",
        }
    }


def test_missing_path_parameters_gets_400(no_aws):
    event = api_event()
    event["pathParameters"] = None
    assert call(event)["statusCode"] == 400


def test_the_read_is_one_query_with_no_profile_read(aws):
    call(api_event())
    assert aws.ops() == ["query"]


# ---- criterion 17: the read ----


def test_query_shape(aws):
    call(api_event())
    (query,) = aws.queries()
    names = query["ExpressionAttributeNames"]
    assert query["TableName"] == "silvercare-results"
    assert query["ConsistentRead"] is False
    assert query["ScanIndexForward"] is False
    assert "IndexName" not in query
    key = query["KeyConditionExpression"]
    pk_name = key.split(" = ")[0]
    assert names[pk_name] == "pk"
    assert "begins_with(" in key
    assert query["ExpressionAttributeValues"] == {
        ":pk": {"S": "RESIDENT#res-0003"},
        ":night": {"S": "NIGHT#"},
    }
    projected = {names[a.strip()] for a in query["ProjectionExpression"].split(",")}
    assert projected == {"pk", "sk", "summary", "anomaly"}


def test_query_follows_last_evaluated_key(aws):
    dates = [f"2026-11-{d:02d}" for d in range(10, 19)]
    for date in dates:
        put_night(aws.ddb, date=date)
    aws.client.page_limit = 2
    nights = body(call(api_event()))["nights"]
    assert [n["date"] for n in nights] == sorted(dates, reverse=True)
    queries = aws.queries()
    assert len(queries) == 5
    assert "ExclusiveStartKey" not in queries[0]
    assert all("ExclusiveStartKey" in q for q in queries[1:])


def test_night_exactly_30_days_old_is_kept(aws):
    # Today (SGT) is 2026-11-19; 30 days before is 2026-10-20.
    put_night(aws.ddb, date="2026-10-20")
    nights = body(call(api_event()))["nights"]
    assert [n["date"] for n in nights] == ["2026-10-20"]


def test_night_31_days_old_is_dropped(aws):
    put_night(aws.ddb, date="2026-10-19")
    put_night(aws.ddb, date="2026-09-01")
    assert body(call(api_event()))["nights"] == []


def test_cutoff_uses_the_singapore_date():
    # The cutoff moves at 00:00 SGT, not 00:00 UTC.
    assert handler.cutoff_date(sgt(2026, 11, 19, 7, 59)) == "2026-10-20"
    assert handler.cutoff_date(sgt(2026, 11, 18, 23, 59)) == "2026-10-19"


# ---- criterion 18: expected_date and not_ready ----


def test_expected_date_is_yesterday_in_singapore():
    assert handler.expected_date(sgt(2026, 11, 19, 0, 0, 0)) == "2026-11-18"
    assert handler.expected_date(sgt(2026, 11, 18, 23, 59, 59)) == "2026-11-17"
    assert handler.expected_date(sgt(2027, 1, 1, 9, 30)) == "2026-12-31"


def test_not_ready_false_at_092959_without_yesterday(aws):
    put_night(aws.ddb, date="2026-11-17")
    payload = body(call(api_event(), BEFORE_READY))
    assert payload["not_ready"] is False
    assert payload["expected_date"] == YESTERDAY


def test_not_ready_true_at_093000_without_yesterday(aws):
    put_night(aws.ddb, date="2026-11-17")
    assert body(call(api_event(), NOW))["not_ready"] is True


def test_not_ready_false_at_092959_with_yesterday(aws):
    put_night(aws.ddb)
    assert body(call(api_event(), BEFORE_READY))["not_ready"] is False


def test_not_ready_false_at_093000_with_yesterday(aws):
    put_night(aws.ddb)
    put_night(aws.ddb, date="2026-11-17")
    assert body(call(api_event(), NOW))["not_ready"] is False


def test_not_ready_false_at_092959_with_no_nights(aws):
    payload = body(call(api_event(), BEFORE_READY))
    assert payload["nights"] == []
    assert payload["not_ready"] is False


def test_not_ready_true_at_093000_with_no_nights(aws):
    payload = body(call(api_event(), NOW))
    assert payload["nights"] == []
    assert payload["not_ready"] is True


def test_not_ready_true_late_in_the_day_without_yesterday(aws):
    put_night(aws.ddb, date="2026-11-17")
    assert body(call(api_event(), sgt(2026, 11, 19, 23, 59, 59)))["not_ready"] is True


def test_not_ready_false_just_after_midnight_sgt(aws):
    # 00:00 SGT on 11-20: yesterday is 11-19 (missing), but it's before 09:30.
    put_night(aws.ddb)
    payload = body(call(api_event(), sgt(2026, 11, 20, 0, 0, 0)))
    assert payload["expected_date"] == "2026-11-19"
    assert payload["not_ready"] is False


# ---- criterion 21: 500 ----


def test_dynamodb_failure_returns_500_without_detail(aws, logs):
    aws.client.fail["query"] = _throttled()
    resp = handler.lambda_handler(api_event(), None)
    assert resp["statusCode"] == 500
    assert body(resp) == {
        "error": {
            "code": "internal_error",
            "message": "Could not load the nightly summaries.",
        }
    }
    assert resp["headers"]["Cache-Control"] == "no-store"
    assert "qx-5521" not in resp["body"]
    (record,) = [r for r in logs() if r.get("outcome") == "error"]
    assert record["error"] == "ProvisionedThroughputExceededException"
    assert "qx-5521" not in json.dumps(logs())


def test_lambda_handler_uses_the_current_singapore_time(aws, monkeypatch):
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return BEFORE_READY.astimezone(tz)

    monkeypatch.setattr(handler, "datetime", Fixed)
    payload = body(handler.lambda_handler(api_event(), None))
    assert payload["expected_date"] == YESTERDAY
    assert payload["not_ready"] is False


# ---- criterion 20: logs ----


def test_ok_log_has_outcome_route_ids_and_count(aws, logs):
    put_night(aws.ddb)
    put_night(aws.ddb, date="2026-11-17")
    call(api_event())
    (record,) = [r for r in logs() if r.get("outcome") == "ok"]
    assert record["route"] == "GET /residents/{resident_id}/nightly"
    assert record["request_id"] == REQUEST_ID
    assert record["sub"] == SUB
    assert record["resident_id"] == RESIDENT_A
    assert record["nights"] == 2


def test_forbidden_log_has_the_groups(no_aws, logs):
    call(api_event(groups=("nurse", "caregiver")))
    (record,) = [r for r in logs() if r.get("outcome") == "forbidden"]
    assert record["groups"] == ["caregiver", "nurse"]
    assert record["sub"] == SUB
    assert "resident_id" not in record


def test_free_text_sub_and_request_id_are_logged_as_invalid(no_aws, logs):
    event = api_event(groups=("nurse",), claims={"sub": "Test Doctor Zq"})
    event["requestContext"]["requestId"] = "synthetic request with spaces"
    call(event)
    (record,) = logs()
    assert record["sub"] == "invalid"
    assert record["request_id"] == "invalid"


def test_invalid_resident_id_is_not_logged(no_aws, logs):
    call(api_event("res-0003-synthetic-note"))
    assert "synthetic-note" not in json.dumps(logs())


def test_warm_container_drops_the_previous_resident_id(aws, logs):
    call(api_event(RESIDENT_A))
    call(api_event(RESIDENT_A, groups=("nurse",)))
    forbidden = [r for r in logs() if r.get("outcome") == "forbidden"]
    assert forbidden and "resident_id" not in forbidden[0]


def test_logs_never_hold_summary_values(aws, logs):
    put_night(aws.ddb)
    put_night(aws.ddb, date="2026-11-17", falls="synthetic")
    call(api_event())
    text = json.dumps(logs())
    for value in LOG_SENTINELS:
        assert value not in text, value
    assert "hr_bpm" not in text
