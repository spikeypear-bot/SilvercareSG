"""GET /ops/device-problems: groups, request checks, the exact response, actor names,
logs, and the IAM policy kept in step with the code."""

from __future__ import annotations

import json
import re
from pathlib import Path

import handler
import pytest
from conftest import tf_projection
from events import (
    BATTERY,
    BEDROOM,
    CAREGIVER_EMAIL,
    CAREGIVER_NAME,
    DEVICE_DETAILS_EXTRA,
    NOT_WORN,
    OFFLINE,
    RESIDENT_A,
    RESIDENT_B,
    SUB,
    WEARABLE_2,
    api_event,
    put_alert,
    put_device_problems,
    ts,
)

API_TF = Path(__file__).resolve().parents[3] / "infra" / "api.tf"
RESPONSE_KEYS = {
    "resident_id",
    "sk",
    "alert_id",
    "alert_type",
    "severity",
    "device_id",
    "event_ts",
    "status",
    "details",
    "acknowledged_at",
    "acknowledged_by_role",
    "acknowledged_by",
    "resolved_at",
    "resolved_by_role",
}


def _call(event=None):
    result = handler.lambda_handler(event or api_event(), None)
    return result["statusCode"], json.loads(result["body"]), result


# ---- groups ----


@pytest.mark.parametrize(
    "groups",
    [("nurse",), ("doctor",), ("caregiver",), ("family",), (), ("nurse", "doctor")],
)
def test_other_groups_get_403_and_read_nothing(aws, groups):
    put_device_problems(aws.ddb)

    status, body, _ = _call(api_event(groups=groups))

    assert status == 403
    assert body["error"]["code"] == "forbidden"
    assert aws.calls == []


def test_no_groups_claim_is_403(aws):
    status, _, _ = _call(api_event(drop_groups=True))
    assert status == 403
    assert aws.calls == []


@pytest.mark.parametrize(
    "groups", [("ops",), ("ops", "nurse"), "[ops caregiver]", "ops"]
)
def test_ops_in_any_form_is_allowed(aws, groups):
    put_device_problems(aws.ddb)
    status, body, _ = _call(api_event(groups=groups))
    assert status == 200
    assert len(body["alerts"]) == 3


def test_no_claims_is_401(aws):
    event = api_event()
    del event["requestContext"]["authorizer"]
    status, _, _ = _call(event)
    assert status == 401


@pytest.mark.parametrize(
    "top", [{"queryStringParameters": {"type": "fall"}}, {"body": "{}"}]
)
def test_query_string_or_body_is_400(aws, top):
    status, body, _ = _call(api_event(**top))
    assert status == 400
    assert body["error"]["code"] == "bad_request"
    assert aws.calls == []


# ---- response ----


def test_exact_keys_and_values(aws):
    put_device_problems(aws.ddb)

    status, body, result = _call()

    assert status == 200
    assert result["headers"]["Cache-Control"] == "no-store"
    alerts = body["alerts"]
    for alert in alerts:
        assert set(alert) == RESPONSE_KEYS
    # Newest first.
    assert [a["alert_id"] for a in alerts] == [NOT_WORN, BATTERY, OFFLINE]
    battery = alerts[1]
    assert battery == {
        "resident_id": RESIDENT_B,
        "sk": f"ALERT#{ts(20)}#{BATTERY}",
        "alert_id": BATTERY,
        "alert_type": "battery_low",
        "severity": "warning",
        "device_id": WEARABLE_2,
        "event_ts": ts(20),
        "status": "open",
        "details": {"battery_pct": 15},
        "acknowledged_at": None,
        "acknowledged_by_role": None,
        "acknowledged_by": None,
        "resolved_at": None,
        "resolved_by_role": None,
    }
    assert alerts[2]["device_id"] == BEDROOM
    assert alerts[0]["details"] == {"since": ts(5)}


def test_details_keep_only_documented_keys(aws):
    put_device_problems(aws.ddb)

    _, body, result = _call()

    offline = next(a for a in body["alerts"] if a["alert_id"] == OFFLINE)
    assert offline["details"] == {"last_seen": ts(1), "grouped": True}
    assert DEVICE_DETAILS_EXTRA not in result["body"]


def test_internal_fields_are_never_returned(aws):
    put_device_problems(aws.ddb)
    _, _, result = _call()
    for word in ("notified_at", "source_message_id", "open_pk", "ack_note", 'pk"'):
        assert word not in result["body"]


def test_ops_acknowledge_is_shown_as_the_ops_team_never_the_sub(aws):
    put_alert(
        aws.ddb,
        alert_id=OFFLINE,
        alert_type="device_offline",
        status="acknowledged",
        acknowledged_at=ts(12),
        acknowledged_by_role="ops",
        acknowledged_by=SUB,
    )

    _, body, result = _call()

    alert = body["alerts"][0]
    assert alert["acknowledged_by_role"] == "ops"
    assert alert["acknowledged_by"] == "the ops team"
    assert alert["acknowledged_at"] == ts(12)
    assert SUB not in result["body"]
    assert aws.cognito_calls == []


def test_caregiver_acknowledge_shows_the_name_never_the_sub(aws):
    caregiver_sub = aws.add_user(CAREGIVER_EMAIL, CAREGIVER_NAME)
    put_alert(
        aws.ddb,
        alert_id=BATTERY,
        alert_type="battery_low",
        status="acknowledged",
        acknowledged_at=ts(12),
        acknowledged_by_role="caregiver",
        acknowledged_by=caregiver_sub,
    )

    _, body, result = _call()

    assert body["alerts"][0]["acknowledged_by"] == CAREGIVER_NAME
    assert caregiver_sub not in result["body"]
    assert CAREGIVER_EMAIL not in result["body"]


def test_resolved_fields_come_from_the_base_item(aws):
    put_alert(
        aws.ddb,
        alert_id=OFFLINE,
        alert_type="device_offline",
        resolved_at=ts(40),
        resolved_by_role="system",
    )
    _, body, _ = _call()
    assert body["alerts"][0]["resolved_at"] == ts(40)
    assert body["alerts"][0]["resolved_by_role"] == "system"


def test_unassigned_device_problem_is_shown(aws):
    put_alert(
        aws.ddb, alert_id=OFFLINE, alert_type="device_offline", resident_id="unassigned"
    )
    _, body, _ = _call()
    assert body["alerts"][0]["resident_id"] == "unassigned"


def test_aws_error_is_a_clean_500(aws, monkeypatch):
    def boom():
        raise RuntimeError("synthetic failure with res-0001 inside")

    monkeypatch.setattr(handler, "read_device_problems", boom)
    status, body, result = _call()
    assert status == 500
    assert body["error"]["code"] == "internal_error"
    assert "res-0001" not in result["body"]


# ---- logs ----


def test_logs_hold_counts_and_ids_only(aws, logs):
    caregiver_sub = aws.add_user(CAREGIVER_EMAIL, CAREGIVER_NAME)
    put_device_problems(aws.ddb)
    put_alert(
        aws.ddb,
        alert_id="6f1c2e0a-5b3d-5e8f-9a47-0000000000d9",
        alert_type="not_worn",
        status="acknowledged",
        acknowledged_by_role="caregiver",
        acknowledged_by=caregiver_sub,
    )

    _call()

    text = json.dumps(logs())
    ok = [r for r in logs() if r.get("outcome") == "ok"]
    assert ok and ok[0]["alerts"] == 4 and ok[0]["sub"] == SUB
    assert ok[0]["request_id"] == "JpvNMgAbSQ0EJcQ="
    for secret in (
        CAREGIVER_NAME,
        CAREGIVER_EMAIL,
        caregiver_sub,
        DEVICE_DETAILS_EXTRA,
        "Test Ops Name Zq",
        "synthetic-token",
        RESIDENT_A,
    ):
        assert secret not in text


# ---- reads match the projection and the IAM policy ----


def _policy() -> str:
    text = API_TF.read_text(encoding="utf-8")
    match = re.search(
        r'data "aws_iam_policy_document" "api_ops" \{(.*?)\n\}\n', text, re.DOTALL
    )
    assert match is not None, "api_ops policy document not found in api.tf"
    return match.group(1)


def _statement(sid: str) -> str:
    match = re.search(r'sid\s*=\s*"' + sid + r'"(.*?)\n  \}', _policy(), re.DOTALL)
    assert match is not None, f"statement {sid} not found"
    return match.group(1)


def _attribute_list(statement: str) -> set[str]:
    match = re.search(
        r'variable = "dynamodb:Attributes"\s*values\s*=\s*\[(.*?)\]',
        statement,
        re.DOTALL,
    )
    assert match is not None
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _projected(request: dict) -> set[str]:
    names = request["ExpressionAttributeNames"]
    return {names[part.strip()] for part in request["ProjectionExpression"].split(",")}


def test_index_query_reads_only_allowed_and_projected_attributes(aws):
    put_device_problems(aws.ddb)
    _call()
    (query,) = aws.queries()
    projected = _projected(query)
    assert query["Select"] == "SPECIFIC_ATTRIBUTES"
    assert "ack_note" not in projected and "details" not in projected
    index = set(tf_projection("open")["NonKeyAttributes"]) | {"pk", "sk", "open_pk"}
    assert projected <= index
    assert projected <= _attribute_list(_statement("AlertsOpenIndex"))
    assert "ack_note" not in _attribute_list(_statement("AlertsOpenIndex"))


def test_base_read_is_only_type_details_and_resolved(aws):
    put_device_problems(aws.ddb)
    _call()
    (batch,) = aws.batch_gets()
    (request,) = batch["RequestItems"].values()
    projected = _projected(request)
    assert projected == {
        "pk",
        "sk",
        "alert_type",
        "details",
        "resolved_at",
        "resolved_by_role",
    }
    assert projected == _attribute_list(_statement("AlertItems"))
