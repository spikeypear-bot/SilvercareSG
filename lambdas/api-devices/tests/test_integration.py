"""GET /devices against DynamoDB Local: the real engine's handling of the INCLUDE
projections and the nested `#latest.#ts` ProjectionExpression.

Run with `make test-integration FN=api-devices`, which sets DYNAMODB_ENDPOINT.
"""

from __future__ import annotations

import json
import os
import uuid

import boto3
import pytest
from botocore.config import Config
from conftest import create_latest_table, create_registry_table
from events import (
    BEDROOM,
    MESSAGE_ID,
    NAME_A,
    NAME_B,
    NOW,
    PHOTO_A,
    WEARABLE,
    WEARABLE_2,
    api_event,
    seed_fleet,
)

pytestmark = pytest.mark.integration

ENDPOINT = os.environ.get("DYNAMODB_ENDPOINT")


@pytest.fixture
def local_tables(monkeypatch):
    if not ENDPOINT:
        pytest.skip("DYNAMODB_ENDPOINT not set (run make test-integration)")
    import handler

    suffix = uuid.uuid4().hex[:8]
    registry = f"silvercare-registry-test-{suffix}"
    latest = f"silvercare-latest-test-{suffix}"
    monkeypatch.setenv("REGISTRY_TABLE", registry)
    monkeypatch.setenv("LATEST_TABLE", latest)
    client = boto3.client(
        "dynamodb",
        endpoint_url=ENDPOINT,
        region_name="ap-southeast-1",
        config=Config(retries={"mode": "standard"}),
    )
    create_registry_table(client, registry)
    create_latest_table(client, latest)
    for table in (registry, latest):
        client.get_waiter("table_exists").wait(TableName=table)
    handler.dynamodb_client.cache_clear()
    yield client
    handler.dynamodb_client.cache_clear()
    for table in (registry, latest):
        client.delete_table(TableName=table)


def test_happy_path_against_dynamodb_local(local_tables):
    import handler

    seed_fleet(local_tables)

    resp = handler.handle(api_event(groups=["nurse"]), NOW)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert [d["device_id"] for d in body["devices"]] == [BEDROOM, WEARABLE, WEARABLE_2]
    wearable = body["devices"][1]
    assert wearable["online"] is True
    assert wearable["battery_pct"] == 63
    assert wearable["fw_version"] == "9.8.7-test"
    assert wearable["assigned_to"]["display_name"] == NAME_A
    for value in (NAME_B, PHOTO_A, MESSAGE_ID, "worn", "received_at", "photo_key"):
        assert value not in resp["body"]


def test_by_message_nested_projection_on_dynamodb_local(local_tables):
    import handler

    seed_fleet(local_tables)

    items = handler.read_statuses()

    assert len(items) == 2
    for item in items:
        assert set(item) == {"pk", "latest"}
        assert set(item["latest"]) <= {"ts", "battery_pct", "charging", "fw_version"}
