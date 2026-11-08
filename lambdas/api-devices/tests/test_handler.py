"""GET /devices through the handler, against moto (docs/data-model.md, Devices view;
patterns 4, 5, 8)."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from decimal import Decimal

import handler
import pytest
from botocore.exceptions import ClientError
from conftest import LATEST_TABLE
from events import (
    ASSIGNED_AT,
    BEDROOM,
    EMAIL,
    HOME_A,
    MESSAGE_ID,
    NAME,
    NAME_A,
    NAME_B,
    NOW,
    PHOTO_A,
    PHOTO_B,
    REQUEST_ID,
    RESIDENT_A,
    SUB,
    WEARABLE,
    WEARABLE_2,
    WORN_SENTINEL,
    ago,
    api_event,
    iso,
    put_device,
    put_raw,
    put_resident,
    put_status,
    seed_fleet,
    status_entry,
)

DEVICE_KEYS = {
    "device_id",
    "device_type",
    "online",
    "last_seen",
    "battery_pct",
    "charging",
    "fw_version",
    "assigned_to",
    "alert_limits",
}
ASSIGNED_TO_KEYS = {"resident_id", "display_name", "home_id"}
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
POWERTOOLS_KEYS = {"level", "message", "service", "sampling_rate", "xray_trace_id"}

ASSIGNED_A = {"resident_id": RESIDENT_A, "display_name": NAME_A, "home_id": HOME_A}
# No shadow in moto: the wearables' status is unknown (device-alert-limits.md, 14).
NO_SHADOW_STAFF = {"status": "unknown", "desired": None, "reported": None}


def call(event, now=NOW):
    resp = handler.handle(event, now)
    return resp["statusCode"], json.loads(resp["body"]), resp


def devices_by_id(body):
    return {d["device_id"]: d for d in body["devices"]}


def throttled() -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "Synthetic throttle text qz-7781 for DEVICE#wearable-0001",
            }
        },
        "Query",
    )


# ---- 1. happy path ----


@pytest.mark.parametrize("group", ["doctor", "nurse"])
def test_staff_get_every_device_sorted_by_device_id(aws, group):
    seed_fleet(aws.ddb)

    status, body, _ = call(api_event(groups=[group]))

    assert status == 200
    assert body == {
        "devices": [
            {
                "device_id": BEDROOM,
                "device_type": "bedroom",
                "online": True,
                "last_seen": ago(minutes=1),
                "battery_pct": 87,
                "charging": False,
                "fw_version": "9.8.6-test",
                "assigned_to": ASSIGNED_A,
                "alert_limits": None,
            },
            {
                "device_id": WEARABLE,
                "device_type": "wearable",
                "online": True,
                "last_seen": ago(minutes=1),
                "battery_pct": 63,
                "charging": False,
                "fw_version": "9.8.7-test",
                "assigned_to": ASSIGNED_A,
                "alert_limits": NO_SHADOW_STAFF,
            },
            {
                "device_id": WEARABLE_2,
                "device_type": "wearable",
                "online": False,
                "last_seen": None,
                "battery_pct": None,
                "charging": False,
                "fw_version": None,
                "assigned_to": None,
                "alert_limits": NO_SHADOW_STAFF,
            },
        ]
    }


def test_response_has_exactly_the_data_model_keys(aws):
    seed_fleet(aws.ddb)

    status, body, _ = call(api_event(groups=["nurse"]))

    assert status == 200
    assert set(body) == {"devices"}
    assert len(body["devices"]) == 3
    for device in body["devices"]:
        assert set(device) == DEVICE_KEYS
        if device["assigned_to"] is not None:
            assert set(device["assigned_to"]) == ASSIGNED_TO_KEYS


def test_response_never_carries_projected_fields_the_view_drops(aws):
    seed_fleet(aws.ddb)

    _, _, resp = call(api_event())
    text = resp["body"]

    for name in (
        "photo_key",
        "worn",
        "received_at",
        "clock_skew",
        "message_id",
        "assigned_at",
    ):
        assert name not in text
    received_at = status_entry(WEARABLE)["received_at"]
    for value in (PHOTO_A, PHOTO_B, MESSAGE_ID, ASSIGNED_AT, received_at):
        assert value not in text


def test_worn_value_never_reaches_the_response(aws):
    seed_fleet(aws.ddb)
    put_status(aws.ddb, WEARABLE, worn=WORN_SENTINEL)

    status, _, resp = call(api_event())

    assert status == 200
    assert WORN_SENTINEL not in resp["body"]
    assert "worn" not in resp["body"]


def test_response_headers_are_json_and_not_cached(aws):
    seed_fleet(aws.ddb)

    _, _, resp = call(api_event())

    assert resp["headers"] == {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
    }


def test_lambda_handler_returns_200_with_the_real_clock(aws):
    seed_fleet(aws.ddb)

    resp = handler.lambda_handler(api_event(), None)

    assert resp["statusCode"] == 200
    assert len(json.loads(resp["body"])["devices"]) == 3


def test_empty_registry_returns_an_empty_list(aws):
    status, body, _ = call(api_event())

    assert (status, body) == (200, {"devices": []})


# ---- 2. spare devices and devices without a status ----


def test_spare_device_has_assigned_to_null(aws):
    put_device(aws.ddb, WEARABLE_2, resident_id=None)
    put_status(aws.ddb, WEARABLE_2)

    _, body, _ = call(api_event())

    (device,) = body["devices"]
    assert device["assigned_to"] is None
    assert device["online"] is True


def test_device_without_status_is_offline_with_null_readings(aws):
    put_resident(aws.ddb)
    put_device(aws.ddb, WEARABLE)

    _, body, _ = call(api_event())

    (device,) = body["devices"]
    assert device["online"] is False
    assert device["last_seen"] is None
    assert device["battery_pct"] is None
    assert device["fw_version"] is None
    assert device["charging"] is False
    assert device["assigned_to"] == ASSIGNED_A


# ---- 3. residents without a device ----


def test_resident_without_a_device_is_not_returned(aws):
    seed_fleet(aws.ddb)

    _, body, resp = call(api_event())

    assert NAME_B not in resp["body"]
    assert "res-0002" not in resp["body"]
    assert "home-0002" not in resp["body"]
    names = {
        d["assigned_to"]["display_name"] for d in body["devices"] if d["assigned_to"]
    }
    assert names == {NAME_A}


# ---- 4. online boundary ----


def test_status_exactly_15_minutes_old_is_online(aws):
    put_device(aws.ddb, WEARABLE, resident_id=None)
    put_status(aws.ddb, WEARABLE, ts=iso(NOW - timedelta(minutes=15)))

    _, body, _ = call(api_event())

    assert body["devices"][0]["online"] is True


def test_status_15_minutes_and_1_ms_old_is_offline(aws):
    put_device(aws.ddb, WEARABLE, resident_id=None)
    ts = iso(NOW - timedelta(minutes=15, milliseconds=1))
    put_status(aws.ddb, WEARABLE, ts=ts)

    _, body, _ = call(api_event())

    assert body["devices"][0]["online"] is False
    assert body["devices"][0]["last_seen"] == ts


def test_status_from_the_future_counts_as_online(aws):
    put_device(aws.ddb, WEARABLE, resident_id=None)
    put_status(aws.ddb, WEARABLE, ts=iso(NOW + timedelta(minutes=5)))

    _, body, _ = call(api_event())

    assert body["devices"][0]["online"] is True


# ---- 5. bedroom units and charging ----


def test_bedroom_unit_is_never_charging(aws):
    put_device(aws.ddb, BEDROOM)
    put_status(aws.ddb, BEDROOM, charging=True)

    _, body, _ = call(api_event())

    assert body["devices"][0]["charging"] is False


def test_wearable_charging_is_returned(aws):
    put_device(aws.ddb, WEARABLE)
    put_status(aws.ddb, WEARABLE, charging=True)

    _, body, _ = call(api_event())

    assert body["devices"][0]["charging"] is True


def test_null_battery_passes_through_as_null(aws):
    put_device(aws.ddb, BEDROOM)
    put_status(aws.ddb, BEDROOM, battery_pct=None)

    _, body, _ = call(api_event())

    assert body["devices"][0]["battery_pct"] is None
    assert body["devices"][0]["online"] is True


# ---- 6. malformed data ----


@pytest.mark.parametrize(
    "ts",
    [
        "2026-11-04T03:59:00Z",
        "2026-11-04 03:59:00.000Z",
        "2026-13-04T03:59:00.000Z",
        "2026-11-04T03:59:00.000+08:00",
        Decimal(1),
        None,
    ],
)
def test_invalid_status_ts_is_treated_as_no_status(aws, ts):
    put_device(aws.ddb, WEARABLE, resident_id=None)
    put_status(aws.ddb, WEARABLE, ts=ts)

    _, body, _ = call(api_event())

    device = body["devices"][0]
    assert device["online"] is False
    assert device["last_seen"] is None
    assert device["battery_pct"] is None
    assert device["fw_version"] is None


@pytest.mark.parametrize(
    "battery", [Decimal(101), Decimal(-1), True, Decimal("50.5"), "63", Decimal(1000)]
)
def test_invalid_battery_becomes_null(aws, battery):
    put_device(aws.ddb, WEARABLE, resident_id=None)
    put_status(aws.ddb, WEARABLE, battery_pct=battery)

    _, body, _ = call(api_event())

    assert body["devices"][0]["battery_pct"] is None
    assert body["devices"][0]["online"] is True


@pytest.mark.parametrize(
    "fw", ["v" * 33, "1.0\u0007", "1.0\u202e", "1.0\n", "", Decimal(1)]
)
def test_invalid_fw_version_becomes_null(aws, fw):
    put_device(aws.ddb, WEARABLE, resident_id=None)
    put_status(aws.ddb, WEARABLE, fw_version=fw)

    _, body, _ = call(api_event())

    assert body["devices"][0]["fw_version"] is None


def test_fw_version_of_32_characters_is_kept(aws):
    put_device(aws.ddb, WEARABLE, resident_id=None)
    put_status(aws.ddb, WEARABLE, fw_version="v" * 32)

    _, body, _ = call(api_event())

    assert body["devices"][0]["fw_version"] == "v" * 32


def test_device_with_malformed_key_is_skipped_and_counted(aws, logs):
    put_device(aws.ddb, WEARABLE)
    put_device(aws.ddb, "sensor-0001", resident_id=None)
    put_device(aws.ddb, "wearable-1", resident_id=None)
    put_device(aws.ddb, "wearable-0009", resident_id="resident-9")

    status, body, _ = call(api_event())

    assert status == 200
    assert [d["device_id"] for d in body["devices"]] == [WEARABLE]
    (record,) = logs()
    assert record["outcome"] == "ok"
    assert record["devices"] == 1
    assert record["skipped"] == 3


def test_device_whose_resident_has_no_profile_has_null_display_name(aws):
    put_device(aws.ddb, WEARABLE)

    _, body, _ = call(api_event())

    assert body["devices"][0]["assigned_to"] == {
        "resident_id": RESIDENT_A,
        "display_name": None,
        "home_id": HOME_A,
    }


def test_status_of_a_device_not_in_the_registry_is_ignored(aws):
    put_device(aws.ddb, WEARABLE)
    put_status(aws.ddb, WEARABLE_2)

    _, body, _ = call(api_event())

    assert [d["device_id"] for d in body["devices"]] == [WEARABLE]
    assert body["devices"][0]["online"] is False


def test_status_item_without_latest_map_is_treated_as_no_status(aws):
    put_device(aws.ddb, WEARABLE, resident_id=None)
    put_raw(
        aws.ddb,
        LATEST_TABLE,
        {
            "pk": f"DEVICE#{WEARABLE}",
            "sk": "MSG#status",
            "watch_type": "status",
            "latest": "not-a-map",
        },
    )

    _, body, _ = call(api_event())

    assert body["devices"][0]["online"] is False


def test_other_watch_types_are_not_read_as_status(aws):
    put_device(aws.ddb, WEARABLE, resident_id=None)
    put_raw(
        aws.ddb,
        LATEST_TABLE,
        {
            "pk": f"DEVICE#{WEARABLE}",
            "sk": "MSG#vitals",
            "watch_type": "vitals",
            "latest": {"ts": ago(minutes=1), "hr_bpm": Decimal(72)},
        },
    )

    _, body, _ = call(api_event())

    assert body["devices"][0]["online"] is False


# ---- 7. groups ----


@pytest.mark.parametrize(
    "groups",
    [["caregiver"], ["family"], [], "", "caregiver", "[caregiver family]", None, 7],
)
def test_non_staff_callers_get_403(aws, groups):
    seed_fleet(aws.ddb)

    status, body, _ = call(api_event(groups=groups))

    assert status == 403
    assert body["error"]["code"] == "forbidden"
    assert set(body["error"]) == {"code", "message"}
    assert set(body) == {"error"}
    assert aws.calls == []
    assert aws.client_requests == []


def test_missing_groups_claim_gets_403(aws):
    status, body, _ = call(api_event(drop_groups=True))

    assert status == 403
    assert body["error"]["code"] == "forbidden"
    assert aws.calls == []


@pytest.mark.parametrize(
    "groups",
    [
        ["doctor", "nurse"],
        "[doctor nurse]",
        "nurse",
        "doctor,nurse",
        ["caregiver", "nurse"],
        "[caregiver nurse]",
    ],
)
def test_staff_in_any_group_claim_shape_get_200(aws, groups):
    seed_fleet(aws.ddb)

    status, body, _ = call(api_event(groups=groups))

    assert status == 200
    assert len(body["devices"]) == 3


def test_group_names_are_matched_exactly(aws):
    status, _, _ = call(
        api_event(groups=["Doctor", "nurses", "doctor-x", "Ops", "ops-x"])
    )

    assert status == 403


def test_groups_claim_in_a_header_is_ignored(aws):
    event = api_event(groups=["caregiver"])
    event["headers"]["cognito:groups"] = "doctor"
    event["headers"]["x-groups"] = "nurse"

    status, _, _ = call(event)

    assert status == 403


# ---- 8. missing claims ----


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"requestContext": {}},
        {"requestContext": {"authorizer": {}}},
        {"requestContext": {"authorizer": {"jwt": {}}}},
        {"requestContext": {"authorizer": {"jwt": {"claims": None}}}},
        {"requestContext": {"authorizer": {"jwt": {"claims": "sub=x"}}}},
        {"requestContext": None},
        None,
        [],
    ],
    ids=[
        "empty",
        "no-authorizer",
        "no-jwt",
        "no-claims",
        "claims-null",
        "claims-string",
        "context-null",
        "event-none",
        "event-list",
    ],
)
def test_missing_claims_get_401(aws, event):
    status, body, _ = call(event)

    assert status == 401
    assert body == {
        "error": {"code": "unauthorized", "message": body["error"]["message"]}
    }
    assert aws.calls == []


# ---- 9. unknown input ----


@pytest.mark.parametrize(
    "query", [{"resident_id": "res-0001"}, {"limit": "10"}, {"": ""}]
)
def test_any_query_string_gets_400(aws, query):
    status, body, _ = call(api_event(queryStringParameters=query))

    assert status == 400
    assert body["error"]["code"] == "bad_request"
    assert set(body["error"]) == {"code", "message"}
    assert aws.calls == []
    assert aws.client_requests == []


@pytest.mark.parametrize("body", ['{"device_id":"wearable-0001"}', "{}", "x"])
def test_any_body_gets_400(aws, body):
    status, out, _ = call(api_event(body=body))

    assert status == 400
    assert out["error"]["code"] == "bad_request"
    assert aws.calls == []
    assert aws.client_requests == []


def test_non_staff_with_a_query_string_gets_403_not_400(aws):
    status, _, _ = call(
        api_event(groups=["caregiver"], queryStringParameters={"a": "b"})
    )

    assert status == 403
    assert aws.calls == []


# ---- 10. pagination ----


def test_every_query_follows_last_evaluated_key(aws):
    seed_fleet(aws.ddb)
    _, expected, _ = call(api_event())
    aws.calls.clear()
    aws.client.page_limit = 1

    status, body, _ = call(api_event())

    assert status == 200
    assert body == expected
    by_key: dict[str, list[dict]] = {}
    for request in aws.queries():
        value = next(iter(request["ExpressionAttributeValues"].values()))["S"]
        by_key.setdefault(value, []).append(request)
    assert set(by_key) == {"DEVICE", "status", "RESIDENT"}
    for value, requests in by_key.items():
        assert len(requests) >= 2, value
        assert "ExclusiveStartKey" not in requests[0]
        assert all("ExclusiveStartKey" in r for r in requests[1:]), value


def test_pages_are_joined_from_a_patched_client(aws, monkeypatch):
    """Without moto's Limit: the client returns each query's items one page at a time
    and the handler must join them."""
    pages = {
        "DEVICE": [
            {
                "Items": [{"pk": {"S": f"DEVICE#{WEARABLE}"}}],
                "LastEvaluatedKey": {"k": {"S": "1"}},
            },
            {"Items": [{"pk": {"S": f"DEVICE#{BEDROOM}"}}]},
        ],
        "status": [
            {"Items": [], "LastEvaluatedKey": {"k": {"S": "1"}}},
            {
                "Items": [
                    {
                        "pk": {"S": f"DEVICE#{WEARABLE}"},
                        "latest": {"M": {"ts": {"S": ago(minutes=2)}}},
                    }
                ]
            },
        ],
        "RESIDENT": [{"Items": []}],
    }
    seen: list[dict] = []

    class Client:
        def query(self, **request):
            seen.append(request)
            value = next(iter(request["ExpressionAttributeValues"].values()))["S"]
            return pages[value].pop(0)

    monkeypatch.setattr(handler, "dynamodb_client", lambda: Client())

    status, body, _ = call(api_event())

    assert status == 200
    assert [d["device_id"] for d in body["devices"]] == [BEDROOM, WEARABLE]
    assert body["devices"][1]["online"] is True
    assert len(seen) == 5
    assert all(not p for p in pages.values())


# ---- 11. transient AWS errors ----


def test_throttled_query_returns_clean_500(aws, logs):
    seed_fleet(aws.ddb)
    aws.client.fail["query"] = throttled()

    resp = handler.lambda_handler(api_event(), None)

    assert resp["statusCode"] == 500
    body = json.loads(resp["body"])
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == "internal_error"
    for text in ("qz-7781", "ProvisionedThroughput", "DEVICE#", "Query", "Traceback"):
        assert text not in resp["body"]


def test_throttled_query_logs_only_outcome_and_error_code(aws, logs):
    seed_fleet(aws.ddb)
    aws.client.fail["query"] = throttled()

    handler.lambda_handler(api_event(), None)

    (record,) = logs()
    assert set(record) - POWERTOOLS_KEYS == {"outcome", "error", "request_id"}
    assert record["outcome"] == "error"
    assert record["error"] == "ProvisionedThroughputExceededException"
    assert record["level"] == "ERROR"
    assert "qz-7781" not in json.dumps(record)


def test_unexpected_exception_returns_500_with_its_type_name_logged(
    aws, logs, monkeypatch
):
    def boom(_):
        raise ValueError("synthetic failure text qz-9912")

    monkeypatch.setattr(handler, "read_devices", lambda: boom(None))

    resp = handler.lambda_handler(api_event(), None)

    assert resp["statusCode"] == 500
    assert "qz-9912" not in resp["body"]
    (record,) = logs()
    assert record["error"] == "ValueError"
    assert "qz-9912" not in json.dumps(record)


def test_failure_on_a_later_query_still_returns_500(aws, monkeypatch):
    seed_fleet(aws.ddb)
    real_query = aws.client._client.query
    count = {"n": 0}

    def flaky(**kwargs):
        count["n"] += 1
        if count["n"] == 3:
            raise throttled()
        return real_query(**kwargs)

    monkeypatch.setattr(aws.client._client, "query", flaky)

    resp = handler.lambda_handler(api_event(), None)

    assert resp["statusCode"] == 500
    assert json.loads(resp["body"])["error"]["code"] == "internal_error"


# ---- 12. logs ----


def _log_text(records: list[dict]) -> str:
    """Records as text with UUIDs (the fixed sub) replaced, so short digit strings
    inside them can't match a reading."""
    return UUID_RE.sub("<uuid>", json.dumps(records))


def test_ok_log_has_sub_and_counts_and_no_names_or_readings(aws, logs):
    seed_fleet(aws.ddb)

    call(api_event(groups=["doctor"]))

    (record,) = logs()
    assert record["message"] == "get devices"
    assert record["outcome"] == "ok"
    assert record["sub"] == SUB
    assert record["devices"] == 3
    assert record["skipped"] == 0
    assert record["caller"] == "staff"
    assert record["shadow_read_failures"] == 0
    assert set(record) - POWERTOOLS_KEYS == {
        "outcome",
        "caller",
        "sub",
        "devices",
        "skipped",
        "request_id",
        "shadow_read_failures",
    }
    text = _log_text(logs())
    for value in (NAME_A, NAME_B, "63", "87", "9.8.7-test", "9.8.6-test", EMAIL, NAME):
        assert value not in text
    for value in (
        "Test Resident",
        "photo",
        "home-000",
        "res-000",
        "wearable",
        "bedroom",
    ):
        assert value not in text


def test_forbidden_log_has_sub_and_groups(aws, logs):
    call(api_event(groups=["caregiver", "family"]))

    (record,) = logs()
    assert record["outcome"] == "forbidden"
    assert record["sub"] == SUB
    assert record["groups"] == ["caregiver", "family"]
    assert record["level"] == "WARNING"
    text = _log_text(logs())
    assert EMAIL not in text
    assert NAME not in text


def test_forbidden_log_drops_group_values_that_are_not_group_names(aws, logs):
    call(api_event(groups=["caregiver", "Test Resident A", "x" * 80]))

    (record,) = logs()
    assert record["groups"] == ["caregiver"]


def test_bad_request_log_has_no_query_or_body(aws, logs):
    body = '{"note": "Synthetic body text qz-5521"}'

    call(api_event(body=body, queryStringParameters={"q": "synthetic-query-qz-6612"}))

    (record,) = logs()
    assert record["outcome"] == "bad_request"
    assert record["sub"] == SUB
    text = _log_text(logs())
    for value in ("qz-5521", "qz-6612", "Synthetic body", EMAIL, NAME):
        assert value not in text


def test_unauthorized_log_has_only_the_outcome(aws, logs):
    call({"requestContext": {}})

    (record,) = logs()
    assert set(record) - POWERTOOLS_KEYS == {"outcome", "request_id"}
    assert record["outcome"] == "no_claims"
    assert record["request_id"] == "invalid"


def test_sub_that_is_not_a_cognito_uuid_is_logged_as_invalid(aws, logs):
    call(api_event(claims={"sub": EMAIL}))

    (record,) = logs()
    assert record["sub"] == "invalid"
    assert EMAIL not in json.dumps(record)


def test_no_log_record_carries_the_email_or_name_claim(aws, logs):
    seed_fleet(aws.ddb)

    call(api_event(groups=["nurse"]))
    call(api_event(groups=["caregiver"]))
    call(api_event(body="{}"))
    aws.client.fail["query"] = throttled()
    handler.lambda_handler(api_event(), None)

    records = logs()
    assert len(records) == 4
    text = _log_text(records)
    for value in (EMAIL, NAME, "example.invalid", "Test Staff"):
        assert value not in text


def _with_request_id(value):
    event = api_event()
    event["requestContext"]["requestId"] = value
    return event


def test_every_outcome_log_carries_the_request_id(aws, logs):
    seed_fleet(aws.ddb)

    handler.lambda_handler(api_event(groups=["nurse"]), None)
    handler.lambda_handler(api_event(groups=["caregiver"]), None)
    handler.lambda_handler(api_event(body="{}"), None)
    aws.client.fail["query"] = throttled()
    handler.lambda_handler(api_event(), None)

    records = logs()
    assert [r["outcome"] for r in records] == [
        "ok",
        "forbidden",
        "bad_request",
        "error",
    ]
    assert all(r["request_id"] == REQUEST_ID for r in records)


@pytest.mark.parametrize(
    "value",
    [
        "Test Resident A",
        "id;drop",
        "x" * 65,
        "",
        "JpvNMgAbSQ0EJcQ=\n",
        "Jpv\u202eNMg",
        7,
        None,
    ],
)
def test_malformed_request_id_is_logged_as_invalid(aws, logs, value):
    handler.lambda_handler(_with_request_id(value), None)

    (record,) = logs()
    assert record["request_id"] == "invalid"


def test_missing_request_id_is_logged_as_invalid(aws, logs):
    event = api_event()
    del event["requestContext"]["requestId"]

    handler.lambda_handler(event, None)

    (record,) = logs()
    assert record["request_id"] == "invalid"


def test_request_id_of_64_characters_is_kept(aws, logs):
    handler.lambda_handler(_with_request_id("A" * 64), None)

    (record,) = logs()
    assert record["request_id"] == "A" * 64


def test_request_id_does_not_carry_over_to_the_next_invocation(aws, logs):
    handler.lambda_handler(_with_request_id("FirstReqIdAAA="), None)
    handler.lambda_handler({}, None)

    first, second = logs()
    assert first["request_id"] == "FirstReqIdAAA="
    assert second["request_id"] == "invalid"


# ---- 13. read-only and repeatable ----


def test_two_calls_return_the_same_body(aws):
    seed_fleet(aws.ddb)

    first = handler.handle(api_event(), NOW)
    second = handler.handle(api_event(), NOW)

    assert first == second


def test_handler_only_queries_and_never_writes(aws):
    seed_fleet(aws.ddb)

    call(api_event())
    call(api_event(groups=["nurse"]))

    assert set(aws.ops()) == {"query"}
    assert len(aws.queries()) == 6
    for request in aws.queries():
        assert "ConsistentRead" not in request or request["ConsistentRead"] is False
        assert request["Select"] == "SPECIFIC_ATTRIBUTES"
        assert request["IndexName"] in {"by-type", "by-message"}


def test_queries_use_the_configured_table_names(aws):
    seed_fleet(aws.ddb)

    call(api_event())

    tables = {(r["TableName"], r["IndexName"]) for r in aws.queries()}
    assert tables == {
        ("silvercare-registry", "by-type"),
        ("silvercare-latest", "by-message"),
    }


# ---- 7b. ops (user's decision 2026-11-08: ops get the devices tab, without pattern 8) ----

ASSIGNED_A_FOR_OPS = {"resident_id": RESIDENT_A, "display_name": None, "home_id": None}


@pytest.mark.parametrize(
    "groups", [["ops"], "[ops]", "ops", ["caregiver", "ops"], "[ops family]"]
)
def test_ops_get_every_device_without_names_or_home(aws, groups):
    seed_fleet(aws.ddb)

    status, body, _ = call(api_event(groups=groups))

    assert status == 200
    assert [d["device_id"] for d in body["devices"]] == [BEDROOM, WEARABLE, WEARABLE_2]
    assigned = [d["assigned_to"] for d in body["devices"]]
    assert assigned == [ASSIGNED_A_FOR_OPS, ASSIGNED_A_FOR_OPS, None]
    for device in body["devices"]:
        assert set(device) == DEVICE_KEYS
    assert NAME_A not in json.dumps(body)
    assert HOME_A not in json.dumps(body)


def test_ops_never_query_residents_or_read_home_id(aws):
    seed_fleet(aws.ddb)

    call(api_event(groups=["ops"]))

    queries = aws.queries()
    assert len(queries) == 2
    for request in queries:
        values = json.dumps(request["ExpressionAttributeValues"])
        assert "RESIDENT" not in values
        assert "home_id" not in request["ExpressionAttributeNames"].values()
        assert "display_name" not in request["ExpressionAttributeNames"].values()


@pytest.mark.parametrize(
    "groups", [["ops", "nurse"], ["ops", "doctor"], "[ops doctor]"]
)
def test_staff_who_are_also_ops_get_the_staff_response(aws, groups):
    seed_fleet(aws.ddb)

    status, body, _ = call(api_event(groups=groups))

    assert status == 200
    assert body["devices"][0]["assigned_to"] == ASSIGNED_A


def test_ops_ok_log_says_ops_and_has_no_names(aws, logs):
    seed_fleet(aws.ddb)

    call(api_event(groups=["ops"]))

    (record,) = logs()
    assert record["outcome"] == "ok"
    assert record["caller"] == "ops"
    assert NAME_A not in json.dumps(record)
