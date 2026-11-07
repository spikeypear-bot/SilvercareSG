"""GET /dashboard against DynamoDB Local: the real engine's handling of the INCLUDE
projections, the descending `open` index, the nested `#latest.#ts` projection, and
BatchGetItem with a ProjectionExpression. Cognito is patched (DynamoDB Local has none).

Run with `make test-integration FN=api-dashboard`, which sets DYNAMODB_ENDPOINT.
"""

from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from conftest import create_alerts_table, create_latest_table, create_registry_table
from events import (
    ALERT_1,
    ALERT_2,
    BEDROOM,
    CAREGIVER_EMAIL,
    CAREGIVER_NAME,
    DETAILS_SENTINEL,
    ESCALATES_AT,
    MESSAGE_ID,
    NAME_A,
    NOTIFIED_AT,
    NOW,
    PHOTO_A,
    RESIDENT_A,
    RESIDENT_B,
    UPDATED_BY_SUB,
    WEARABLE,
    WEARABLE_2,
    ago,
    api_event,
    put_alert,
    put_bed_and_motion,
    put_device,
    put_resident,
    put_status,
    put_thresholds,
    put_vitals,
)

pytestmark = pytest.mark.integration

ENDPOINT = os.environ.get("DYNAMODB_ENDPOINT")
CAREGIVER_SUB = "00000000-0000-4000-8000-000000000002"


class FakeCognito:
    """AdminGetUser for one synthetic caregiver; anyone else is gone."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def admin_get_user(self, **kwargs):
        self.calls.append(("admin_get_user", kwargs))
        if kwargs["Username"] != CAREGIVER_SUB:
            raise ClientError(
                {"Error": {"Code": "UserNotFoundException", "Message": "x"}},
                "AdminGetUser",
            )
        return {
            "Username": CAREGIVER_SUB,
            "UserAttributes": [
                {"Name": "sub", "Value": CAREGIVER_SUB},
                {"Name": "email", "Value": CAREGIVER_EMAIL},
                {"Name": "name", "Value": CAREGIVER_NAME},
            ],
        }


@pytest.fixture
def local(monkeypatch):
    if not ENDPOINT:
        pytest.skip("DYNAMODB_ENDPOINT not set (run make test-integration)")
    import handler

    suffix = uuid.uuid4().hex[:8]
    tables = {
        "REGISTRY_TABLE": f"silvercare-registry-test-{suffix}",
        "LATEST_TABLE": f"silvercare-latest-test-{suffix}",
        "ALERTS_TABLE": f"silvercare-alerts-test-{suffix}",
    }
    for name, value in tables.items():
        monkeypatch.setenv(name, value)
    client = boto3.client(
        "dynamodb",
        endpoint_url=ENDPOINT,
        region_name="ap-southeast-1",
        config=Config(retries={"mode": "standard"}),
    )
    create_registry_table(client, tables["REGISTRY_TABLE"])
    create_latest_table(client, tables["LATEST_TABLE"])
    create_alerts_table(client, tables["ALERTS_TABLE"])
    for table in tables.values():
        client.get_waiter("table_exists").wait(TableName=table)
    cognito = FakeCognito()
    monkeypatch.setattr(handler, "cognito_client", lambda: cognito)
    handler.dynamodb_client.cache_clear()
    yield client, cognito
    handler.dynamodb_client.cache_clear()
    for table in tables.values():
        client.delete_table(TableName=table)


def _seed(client) -> None:
    put_resident(client)
    put_resident(client, RESIDENT_B, "Test Resident B", "home-0002")
    put_device(client, WEARABLE)
    put_device(client, BEDROOM)
    put_device(client, WEARABLE_2, RESIDENT_B, "home-0002")
    put_status(client, WEARABLE)
    put_status(client, BEDROOM)
    put_status(client, WEARABLE_2)
    put_vitals(client, WEARABLE)
    put_vitals(client, WEARABLE_2, spo2_pct=Decimal(89))
    put_thresholds(client, RESIDENT_B, spo2_pct={"min": Decimal(88)})
    put_bed_and_motion(client)
    put_alert(client, alert_id=ALERT_1, event_ts=ago(minutes=10))
    put_alert(
        client,
        alert_id=ALERT_2,
        alert_type="battery_low",
        status="acknowledged",
        event_ts=ago(minutes=2),
        acknowledged_at=ago(minutes=1),
        acknowledged_by=CAREGIVER_SUB,
        acknowledged_by_role="caregiver",
    )


def test_happy_path_against_dynamodb_local(local):
    import handler

    client, cognito = local
    _seed(client)

    resp = handler.handle(api_event(groups=["nurse"]), NOW)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert [a["alert_id"] for a in body["alerts"]] == [ALERT_2, ALERT_1]
    alerts = {a["alert_id"]: a for a in body["alerts"]}
    assert alerts[ALERT_1]["escalates_at"] == ESCALATES_AT
    assert alerts[ALERT_2]["acknowledged_by"] == CAREGIVER_NAME
    residents = {r["resident_id"]: r for r in body["residents"]}
    assert residents[RESIDENT_A]["display_name"] == NAME_A
    assert residents[RESIDENT_A]["status"] == "attention"
    assert residents[RESIDENT_A]["wearable"]["battery_pct"] == 63
    assert residents[RESIDENT_A]["bedroom"]["online"] is True
    # SpO2 89 is normal for B (min 88), so B is ok.
    assert residents[RESIDENT_B]["status"] == "ok"
    assert residents[RESIDENT_B]["vitals"][0]["spo2_pct"] == 89
    assert [op for op, _ in cognito.calls] == ["admin_get_user"]
    for value in (
        PHOTO_A,
        MESSAGE_ID,
        NOTIFIED_AT,
        DETAILS_SENTINEL,
        CAREGIVER_SUB,
        CAREGIVER_EMAIL,
        UPDATED_BY_SUB,
        "activity_pct",
        "photo_key",
        "received_at",
    ):
        assert value not in resp["body"]


def test_nested_latest_projections_on_dynamodb_local(local):
    import handler

    client, _ = local
    _seed(client)

    statuses = handler.read_statuses()
    vitals = handler.read_vitals()

    assert len(statuses) == 3
    assert len(vitals) == 2
    for item in statuses:
        assert set(item) == {"pk", "latest"}
        assert set(item["latest"]) <= {"ts", "battery_pct", "worn", "charging"}
    for item in vitals:
        assert set(item["latest"]) == {"ts", "hr_bpm", "spo2_pct", "skin_temp_c"}


def test_open_index_projection_on_dynamodb_local(local):
    import handler

    client, _ = local
    _seed(client)

    items = handler.read_alerts()

    assert [i["sk"].rsplit("#", 1)[1] for i in items] == [ALERT_2, ALERT_1]
    for item in items:
        assert "details" not in item
        assert "source_message_id" not in item


def test_thresholds_batch_get_on_dynamodb_local(local):
    import handler

    client, _ = local
    _seed(client)

    found = handler.batch_get_thresholds([RESIDENT_A, RESIDENT_B])

    assert set(found) == {RESIDENT_B}
    assert set(found[RESIDENT_B]) == {"pk", "spo2_pct"}
