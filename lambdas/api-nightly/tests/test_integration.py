"""GET /residents/{resident_id}/nightly against DynamoDB Local: the real engine's
`begins_with` key condition, descending sort, the `#a0..#a2` ProjectionExpression, and
LastEvaluatedKey paging.

Run with `make test-integration FN=api-nightly`, which sets DYNAMODB_ENDPOINT.
"""

from __future__ import annotations

import json
import os
import uuid

import boto3
import handler
import pytest
from conftest import Recorder, create_results_table
from events import (
    EXPECTED_NIGHT,
    NOW,
    RESIDENT_A,
    RESIDENT_B,
    YESTERDAY,
    api_event,
    night_item,
    put_raw,
)

pytestmark = pytest.mark.integration

ENDPOINT = os.environ.get("DYNAMODB_ENDPOINT")


@pytest.fixture
def local(monkeypatch):
    if not ENDPOINT:
        pytest.skip("DYNAMODB_ENDPOINT is not set (run make test-integration)")
    table = f"silvercare-results-it-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("RESULTS_TABLE", table)
    ddb = boto3.client("dynamodb", endpoint_url=ENDPOINT, region_name="ap-southeast-1")
    create_results_table(ddb, table)
    real_client = handler.dynamodb_client
    real_client.cache_clear()
    calls: list = []
    recorder = Recorder(real_client(), calls)
    monkeypatch.setattr(handler, "dynamodb_client", lambda: recorder)
    try:
        yield ddb, table, recorder
    finally:
        ddb.delete_table(TableName=table)
        real_client.cache_clear()


def test_real_query_returns_the_spec_night(local):
    ddb, table, _ = local
    put_raw(ddb, night_item(), table)
    resp = handler.handle(api_event(), NOW)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["nights"] == [EXPECTED_NIGHT]


def test_real_query_is_newest_first_paged_and_partition_only(local):
    ddb, table, recorder = local
    dates = [f"2026-11-{d:02d}" for d in range(12, 19)]
    for date in dates:
        put_raw(ddb, night_item(date=date), table)
    put_raw(ddb, night_item(RESIDENT_B, YESTERDAY), table)
    other = night_item()
    other["sk"] = "PROFILE"
    put_raw(ddb, other, table)
    recorder.page_limit = 3
    nights = json.loads(handler.handle(api_event(RESIDENT_A), NOW)["body"])["nights"]
    assert [n["date"] for n in nights] == sorted(dates, reverse=True)
    assert len([c for c in recorder.calls if c[0] == "query"]) >= 3
