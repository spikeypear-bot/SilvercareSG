"""results' PutItem against DynamoDB Local: the real engine accepts the item (nested
map with NULLs and numbers, `expires_at` a number) and a re-run overwrites it.

S3 is replaced by monkeypatching list_keys/read_object (moto would also intercept the
DynamoDB Local calls). Run with `make test-integration FN=results`.
"""

from __future__ import annotations

import json
import os
import uuid

import boto3
import handler
import pytest
from boto3.dynamodb.types import TypeDeserializer
from conftest import create_results_table
from events import DATE, LOADED_AT, RESIDENT_A, RESIDENT_B, empty_summary, key, summary

pytestmark = pytest.mark.integration

ENDPOINT = os.environ.get("DYNAMODB_ENDPOINT")


@pytest.fixture
def local(monkeypatch):
    if not ENDPOINT:
        pytest.skip("DYNAMODB_ENDPOINT is not set (run make test-integration)")
    table = f"silvercare-results-it-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("RESULTS_TABLE", table)
    monkeypatch.setattr(handler, "now_utc", lambda: LOADED_AT)
    ddb = boto3.client("dynamodb", endpoint_url=ENDPOINT, region_name="ap-southeast-1")
    create_results_table(ddb, table)
    handler.dynamodb_client.cache_clear()
    objects: dict[str, bytes] = {}
    monkeypatch.setattr(handler, "list_keys", lambda date: sorted(objects))
    monkeypatch.setattr(handler, "read_object", lambda k: objects[k])
    try:
        yield ddb, table, objects
    finally:
        ddb.delete_table(TableName=table)
        handler.dynamodb_client.cache_clear()


def _items(ddb, table) -> list[dict]:
    des = TypeDeserializer()
    return [
        {k: des.deserialize(v) for k, v in item.items()}
        for item in ddb.scan(TableName=table)["Items"]
    ]


def test_real_put_item_stores_both_summaries(local):
    ddb, table, objects = local
    objects[key(1)] = json.dumps(summary()).encode()
    objects[key(2)] = json.dumps(empty_summary()).encode()
    assert handler.lambda_handler({"date": DATE}, None) == {"date": DATE, "loaded": 2}
    by_resident = {i["resident_id"]: i for i in _items(ddb, table)}
    assert set(by_resident) == {RESIDENT_A, RESIDENT_B}
    assert by_resident[RESIDENT_B]["summary"]["hr_bpm_avg"] is None
    assert by_resident[RESIDENT_A]["summary"]["vitals_readings"] == 1312
    assert by_resident[RESIDENT_A]["expires_at"] == handler.expires_at(DATE)


def test_real_rerun_overwrites(local):
    ddb, table, objects = local
    objects[key(1)] = json.dumps(summary()).encode()
    handler.lambda_handler({"date": DATE}, None)
    objects[key(1)] = json.dumps(summary(falls=3)).encode()
    handler.lambda_handler({"date": DATE}, None)
    (item,) = _items(ddb, table)
    assert item["summary"]["falls"] == 3
