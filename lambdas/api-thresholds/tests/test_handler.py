"""PUT /residents/{resident_id}/thresholds and GET .../thresholds/history through the
handler, against moto (docs/data-model.md patterns 26 and 27). The device shadow
writes after a save are in test_shadows.py."""

from __future__ import annotations

import json
from decimal import Decimal

import handler
import pytest
from conftest import client_error
from events import (
    HISTORY,
    NAME,
    NOW,
    RESIDENT_A,
    RESIDENT_B,
    SUB,
    UPDATED_AT,
    api_event,
    body,
    get_item,
    log_items,
    put_thresholds,
)


def call(event):
    resp = handler.handle(event, NOW)
    return resp["statusCode"], json.loads(resp["body"])


def stored(aws):
    return get_item(aws.ddb, f"THRESHOLDS#{RESIDENT_A}", "THRESHOLDS")


# ---- 1. valid PUT ----


def test_changed_put_saves_thresholds_and_logs_the_change(aws):
    put_thresholds(aws.ddb)

    status, out = call(api_event(payload=body(hr=(55, 110), spo2=93)))

    assert status == 200
    assert out["changed"] is True
    assert out["thresholds"]["hr_bpm"] == {"min": 55, "max": 110}
    assert out["thresholds"]["spo2_pct"] == {"min": 93}
    assert out["thresholds"]["updated_by"] == NAME
    item = stored(aws)
    assert item["hr_bpm"] == {"min": Decimal(55), "max": Decimal(110)}
    assert item["updated_by"] == SUB
    assert len(log_items(aws.ddb)) == 1


def test_response_never_carries_the_sub(aws):
    put_thresholds(aws.ddb)

    _, out = call(api_event(payload=body(hr=(55, 110))))

    assert SUB not in json.dumps(out)


# ---- 2-4. malformed, missing, unknown ----


@pytest.mark.parametrize(
    "payload",
    [
        "not json {",
        "[]",
        json.dumps({**body(), "hr_bpm": {"min": "55", "max": 110}}),
        json.dumps({**body(), "hr_bpm": {"min": 55.0, "max": 110}}),
        json.dumps({**body(), "hr_bpm": {"min": 110, "max": 55}}),
        json.dumps({**body(), "spo2_pct": {"min": 101}}),
        json.dumps({**body(), "skin_temp_c": {"min": 30.05, "max": 38.0}}),
        '{"hr_bpm": {"min": NaN, "max": 110}}',
    ],
)
def test_malformed_body_is_400_and_nothing_written(aws, payload):
    put_thresholds(aws.ddb)

    status, out = call(api_event(payload=payload))

    assert status == 400
    assert out["error"]["code"] == "bad_request"
    assert stored(aws)["updated_at"] == UPDATED_AT
    assert aws.iot.calls == []


@pytest.mark.parametrize("missing", ["hr_bpm", "spo2_pct", "skin_temp_c"])
def test_missing_metric_is_400(aws, missing):
    put_thresholds(aws.ddb)
    payload = body()
    del payload[missing]

    assert call(api_event(payload=payload))[0] == 400


@pytest.mark.parametrize(
    "extra",
    [{"updated_by": SUB}, {"updated_at": "2026-11-08T00:00:00Z"}, {"limits": {}}],
)
def test_unknown_field_is_400(aws, extra):
    put_thresholds(aws.ddb)

    assert call(api_event(payload={**body(hr=(55, 110)), **extra}))[0] == 400
    assert stored(aws)["updated_at"] == UPDATED_AT


def test_query_parameters_are_400(aws):
    put_thresholds(aws.ddb)

    event = api_event(queryStringParameters={"x": "1"})

    assert call(event)[0] == 400


@pytest.mark.parametrize("resident_id", ["resident-1", "res-1", None, ["res-0001"]])
def test_bad_resident_id_is_400(aws, resident_id):
    assert call(api_event(resident_id=resident_id))[0] == 400


# ---- 5. duplicate delivery ----


def test_repeated_save_changes_nothing_the_second_time(aws):
    put_thresholds(aws.ddb)
    call(api_event(payload=body(hr=(55, 110))))
    after_first = stored(aws)

    status, out = call(api_event(payload=body(hr=(55, 110))))

    assert status == 200
    assert out["changed"] is False
    assert stored(aws) == after_first
    assert len(log_items(aws.ddb)) == 1


# ---- 6. transient errors ----


def test_throttled_transaction_is_a_500(aws):
    put_thresholds(aws.ddb)
    aws.db.fail["transact_write_items"] = client_error(
        "ProvisionedThroughputExceededException", "TransactWriteItems"
    )

    resp = handler.lambda_handler(api_event(payload=body(hr=(55, 110))), None)

    assert resp["statusCode"] == 500
    assert "qz-6631" not in resp["body"]
    assert aws.iot.calls == []


def test_concurrent_change_is_a_409(aws):
    put_thresholds(aws.ddb)
    aws.db.fail["transact_write_items"] = type(
        "TransactionCanceledException",
        (Exception,),
        {},
    )()
    aws.db.fail["transact_write_items"].response = {
        "Error": {"Code": "TransactionCanceledException"},
        "CancellationReasons": [{"Code": "ConditionalCheckFailed"}, {"Code": "None"}],
    }

    status, out = call(api_event(payload=body(hr=(55, 110))))

    assert status == 409
    assert out["error"]["code"] == "conflict"
    assert aws.iot.calls == []


# ---- 7-8. access ----


@pytest.mark.parametrize("groups", [["nurse"], ["caregiver"], ["ops"], [], "family"])
def test_put_without_doctor_is_403_and_writes_nothing(aws, groups):
    put_thresholds(aws.ddb)

    status, _ = call(api_event(groups=groups, payload=body(hr=(55, 110))))

    assert status == 403
    assert stored(aws)["updated_at"] == UPDATED_AT
    assert aws.iot.calls == []


def test_put_for_resident_without_thresholds_is_404(aws):
    put_thresholds(aws.ddb, RESIDENT_A)

    status, _ = call(api_event(resident_id=RESIDENT_B, payload=body(hr=(55, 110))))

    assert status == 404
    assert aws.iot.calls == []


def test_missing_claims_is_401(aws):
    event = api_event()
    event["requestContext"]["authorizer"] = {}

    assert call(event)[0] == 401


def test_unknown_route_is_404(aws):
    assert call(api_event(route="GET /residents/{resident_id}/thresholds"))[0] == 404


# ---- GET history ----


@pytest.mark.parametrize("group", ["doctor", "nurse"])
def test_staff_read_history_without_the_sub(aws, group):
    put_thresholds(aws.ddb)
    call(api_event(payload=body(hr=(55, 110))))

    status, out = call(api_event(route=HISTORY, groups=[group]))

    assert status == 200
    (change,) = out["history"]
    assert change["changed_by_name"] == NAME
    assert {c["field"] for c in change["changes"]} == {"hr_bpm.min", "hr_bpm.max"}
    assert set(change) == {"change_id", "changed_at", "changed_by_name", "changes"}
    assert SUB not in json.dumps(out)


@pytest.mark.parametrize("groups", [["caregiver"], ["ops"], []])
def test_history_without_staff_group_is_403(aws, groups):
    assert call(api_event(route=HISTORY, groups=groups))[0] == 403


def test_history_query_is_projected_without_changed_by(aws):
    call(api_event(route=HISTORY))

    (query,) = aws.db.named("query")
    assert "changed_by" not in query["ExpressionAttributeNames"].values()
