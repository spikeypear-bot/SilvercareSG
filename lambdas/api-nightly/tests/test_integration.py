"""GET /residents/{resident_id}/nightly against DynamoDB Local: the real engine's
`begins_with` key condition, descending sort, the `#a0..#a2` ProjectionExpression, and
LastEvaluatedKey paging; for GET /nightly/overview, the by-type Query with
Select = SPECIFIC_ATTRIBUTES on an INCLUDE index and BatchGetItem's nested projection.

Run with `make test-integration FN=api-nightly`, which sets DYNAMODB_ENDPOINT.
"""

from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal

import boto3
import handler
import pytest
from conftest import Recorder, create_registry_table, create_results_table
from events import (
    CLEAR_COUNTS,
    EXPECTED_NIGHT,
    EXPECTED_THRESHOLDS,
    NOW,
    RESIDENT_A,
    RESIDENT_B,
    YESTERDAY,
    api_event,
    night_item,
    overview_event,
    put_raw,
    resident_profile,
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


# ---- GET /nightly/overview (nightly-overview.md criteria 4, 5) ----


@pytest.fixture
def local_overview(monkeypatch):
    if not ENDPOINT:
        pytest.skip("DYNAMODB_ENDPOINT is not set (run make test-integration)")
    suffix = uuid.uuid4().hex[:8]
    results = f"silvercare-results-it-{suffix}"
    registry = f"silvercare-registry-it-{suffix}"
    monkeypatch.setenv("RESULTS_TABLE", results)
    monkeypatch.setenv("REGISTRY_TABLE", registry)
    ddb = boto3.client("dynamodb", endpoint_url=ENDPOINT, region_name="ap-southeast-1")
    create_results_table(ddb, results)
    create_registry_table(ddb, registry)
    real_client = handler.dynamodb_client
    real_client.cache_clear()
    calls: list = []
    recorder = Recorder(real_client(), calls)
    monkeypatch.setattr(handler, "dynamodb_client", lambda: recorder)
    try:
        yield ddb, results, registry, recorder
    finally:
        ddb.delete_table(TableName=results)
        ddb.delete_table(TableName=registry)
        real_client.cache_clear()


def test_real_overview_reads_pk_only_pages_and_projects_the_counts(local_overview):
    ddb, results, registry, recorder = local_overview
    ids = [f"res-{n:04d}" for n in range(1, 6)]
    for resident_id in ids:
        put_raw(ddb, resident_profile(resident_id), registry)
    put_raw(ddb, night_item(ids[0], falls=Decimal(1)), results)
    put_raw(ddb, night_item(ids[1], **CLEAR_COUNTS), results)
    put_raw(ddb, night_item(ids[2], date="2026-11-17"), results)
    recorder.page_limit = 2
    resp = handler.handle_overview(overview_event(), NOW)
    assert resp["statusCode"] == 200
    payload = json.loads(resp["body"])
    assert payload["available"] is True
    # night_item's default summary has hr_bpm_above 12, spo2_pct_below 3, 1 episode.
    assert [e["resident_id"] for e in payload["flagged"]] == [ids[0]]
    assert payload["flagged"][0] == {
        "resident_id": ids[0],
        "falls": 1,
        "vitals_alert_episodes": 1,
        "hr_bpm_above": 12,
        "hr_bpm_below": 0,
        "spo2_pct_below": 3,
        "skin_temp_c_above": 0,
        "skin_temp_c_below": 0,
        "out_of_limit": 15,
        "thresholds": EXPECTED_THRESHOLDS,
    }
    assert payload["clear_count"] == 1
    assert payload["no_summary_count"] == 3
    assert len([c for c in recorder.calls if c[0] == "query"]) >= 3
