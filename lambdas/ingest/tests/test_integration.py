"""Concurrent writers against DynamoDB Local (criterion 25).

Run with `make test-integration FN=ingest`, which sets DYNAMODB_ENDPOINT.
"""

from __future__ import annotations

import os
import threading
import uuid

import boto3
import pytest
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config
from conftest import create_latest_table
from events import BEDROOM, rule_event, ts_at

pytestmark = pytest.mark.integration

ENDPOINT = os.environ.get("DYNAMODB_ENDPOINT")


@pytest.fixture
def local_table(monkeypatch):
    if not ENDPOINT:
        pytest.skip("DYNAMODB_ENDPOINT not set (run make test-integration)")
    import handler

    table = f"silvercare-latest-test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("LATEST_TABLE", table)
    client = boto3.client(
        "dynamodb",
        endpoint_url=ENDPOINT,
        region_name="ap-southeast-1",
        config=Config(retries={"mode": "standard"}),
    )
    create_latest_table(client, table)
    client.get_waiter("table_exists").wait(TableName=table)
    cached_client = handler.dynamodb_client  # tests may monkeypatch it
    cached_client.cache_clear()
    yield client, table
    cached_client.cache_clear()
    client.delete_table(TableName=table)


def stored_entries(client, table):
    resp = client.get_item(
        TableName=table,
        Key={"pk": {"S": f"DEVICE#{BEDROOM}"}, "sk": {"S": "MSG#motion"}},
        ConsistentRead=True,
    )
    d = TypeDeserializer()
    item = {k: d.deserialize(v) for k, v in resp["Item"].items()}
    return item


class BarrierClient:
    """Makes both writers finish GetItem before either does PutItem (forces a race)."""

    def __init__(self, client, barrier):
        self.client = client
        self.barrier = barrier
        self.first_get = threading.local()

    def get_item(self, **kwargs):
        resp = self.client.get_item(**kwargs)
        if not getattr(self.first_get, "done", False):
            self.first_get.done = True
            self.barrier.wait(timeout=10)
        return resp

    def put_item(self, **kwargs):
        return self.client.put_item(**kwargs)


def test_two_concurrent_writers_both_store_once(local_table, monkeypatch):
    import handler

    client, table = local_table
    real = handler.dynamodb_client()
    barrier = threading.Barrier(2)
    racing = BarrierClient(real, barrier)
    monkeypatch.setattr(handler, "dynamodb_client", lambda: racing)

    events = [
        rule_event("motion", ts=ts_at(10)),
        rule_event("motion", ts=ts_at(11), motion=False),
    ]
    results: list = [None, None]
    errors: list = []

    def run(i):
        try:
            results[i] = handler.telemetry_handler(events[i], None)
        except Exception as exc:  # noqa: BLE001 - any error fails the assert below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors
    assert results == [{"outcome": "stored"}, {"outcome": "stored"}]
    item = stored_entries(client, table)
    ids = [e["message_id"] for e in item["entries"]]
    assert sorted(ids) == sorted(e["message_id"] for e in events)
    assert len(ids) == len(set(ids)) == 2
    assert item["version"] == 2
    assert item["latest_ts"] == ts_at(11)


def test_concurrent_duplicate_deliveries_store_once(local_table, monkeypatch):
    import handler

    client, table = local_table
    real = handler.dynamodb_client()
    racing = BarrierClient(real, threading.Barrier(2))
    monkeypatch.setattr(handler, "dynamodb_client", lambda: racing)
    event = rule_event("motion", ts=ts_at(10))
    results: list = []

    def run():
        results.append(handler.telemetry_handler(dict(event), None))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(r["outcome"] for r in results) == ["duplicate", "stored"]
    item = stored_entries(client, table)
    assert [e["message_id"] for e in item["entries"]] == [event["message_id"]]
    assert item["version"] == 1
