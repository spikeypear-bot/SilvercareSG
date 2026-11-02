"""Shared setup for the ingest Lambda tests (docs/features/ingest-live.md).

Fake AWS credentials and env vars are set before anything imports boto3, so no test
can reach real AWS. `latest_table` starts moto and creates silvercare-latest and
silvercare-registry with the same keys and indexes as infra/dynamodb.tf, plus the
silvercare-alerts queue that critical-alert sends to (docs/features/alerts-core.md).
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "ap-southeast-1"
os.environ["AWS_SHARED_CREDENTIALS_FILE"] = "/dev/null"
os.environ["AWS_CONFIG_FILE"] = "/dev/null"
os.environ.setdefault("LATEST_TABLE", "silvercare-latest")
os.environ.setdefault("REGISTRY_TABLE", "silvercare-registry")
os.environ.setdefault("ALERTS_QUEUE_NAME", "silvercare-alerts")
# Replaced by the moto queue's real URL in the latest_table fixture.
os.environ.setdefault(
    "ALERTS_QUEUE_URL",
    "https://sqs.ap-southeast-1.amazonaws.com/123456789012/silvercare-alerts",
)
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-ingest-test")

import boto3
import pytest

TABLE_NAME = os.environ["LATEST_TABLE"]
REGISTRY_TABLE = os.environ["REGISTRY_TABLE"]
QUEUE_NAME = os.environ["ALERTS_QUEUE_NAME"]


def create_latest_table(client, table_name: str = TABLE_NAME) -> None:
    """silvercare-latest as in infra/dynamodb.tf (pk/sk, sparse by-message index)."""
    client.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "watch_type", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 4, "WriteCapacityUnits": 8},
        GlobalSecondaryIndexes=[
            {
                "IndexName": "by-message",
                "KeySchema": [
                    {"AttributeName": "watch_type", "KeyType": "HASH"},
                    {"AttributeName": "pk", "KeyType": "RANGE"},
                ],
                "Projection": {
                    "ProjectionType": "INCLUDE",
                    "NonKeyAttributes": ["device_type", "latest", "latest_ts"],
                },
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 3,
                    "WriteCapacityUnits": 4,
                },
            }
        ],
    )


def create_registry_table(client, table_name: str = REGISTRY_TABLE) -> None:
    """silvercare-registry as in infra/dynamodb.tf (pk/sk, by-type, by-resident)."""
    client.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": name, "AttributeType": "S"}
            for name in ("pk", "sk", "entity_type", "gsi1_pk", "gsi1_sk")
        ],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 2, "WriteCapacityUnits": 1},
        GlobalSecondaryIndexes=[
            {
                "IndexName": "by-type",
                "KeySchema": [
                    {"AttributeName": "entity_type", "KeyType": "HASH"},
                    {"AttributeName": "pk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 1,
                    "WriteCapacityUnits": 1,
                },
            },
            {
                "IndexName": "by-resident",
                "KeySchema": [
                    {"AttributeName": "gsi1_pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi1_sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 1,
                    "WriteCapacityUnits": 1,
                },
            },
        ],
    )


def assign_device(client, device_id: str, resident_id: str) -> None:
    """A registry ASSIGNMENT item linking a device to a synthetic resident."""
    client.put_item(
        TableName=REGISTRY_TABLE,
        Item={
            "pk": {"S": f"DEVICE#{device_id}"},
            "sk": {"S": "ASSIGNMENT"},
            "entity_type": {"S": "DEVICE"},
            "device_type": {"S": device_id.split("-", 1)[0]},
            "resident_id": {"S": resident_id},
            "home_id": {"S": "home-0001"},
            "gsi1_pk": {"S": f"RESIDENT#{resident_id}"},
            "gsi1_sk": {"S": f"DEVICE#{device_id}"},
        },
    )


def _clear_handler_state() -> None:
    import handler
    import registry
    import thresholds

    # At teardown a test's monkeypatch may still replace a client function (the
    # monkeypatch fixture is undone after this one), so skip what isn't cached.
    for client_fn in (handler.dynamodb_client, handler.sqs_client):
        if hasattr(client_fn, "cache_clear"):
            client_fn.cache_clear()
    registry.clear_cache()
    thresholds.clear_cache()


@pytest.fixture
def latest_table(monkeypatch):
    """moto with silvercare-latest, silvercare-registry, and the alerts queue.

    Yields a low-level DynamoDB client for checks; use `alerts_queue` for the queue.
    """
    from moto import mock_aws

    with mock_aws():
        _clear_handler_state()
        client = boto3.client("dynamodb", region_name="ap-southeast-1")
        create_latest_table(client)
        create_registry_table(client)
        sqs = boto3.client("sqs", region_name="ap-southeast-1")
        url = sqs.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]
        monkeypatch.setenv("ALERTS_QUEUE_URL", url)
        yield client
    _clear_handler_state()


class AlertsQueue:
    """The moto alerts queue: read back what critical-alert sent."""

    def __init__(self, client, url: str) -> None:
        self.client = client
        self.url = url

    def receive_all(self) -> list[dict]:
        messages = []
        while True:
            resp = self.client.receive_message(
                QueueUrl=self.url,
                MaxNumberOfMessages=10,
                MessageAttributeNames=["All"],
                AttributeNames=["All"],
                VisibilityTimeout=0,
                WaitTimeSeconds=0,
            )
            batch = resp.get("Messages", [])
            if not batch:
                return messages
            for m in batch:
                self.client.delete_message(
                    QueueUrl=self.url, ReceiptHandle=m["ReceiptHandle"]
                )
            messages.extend(batch)

    def bodies(self) -> list[dict]:
        return [json.loads(m["Body"]) for m in self.receive_all()]


@pytest.fixture
def alerts_queue(latest_table) -> AlertsQueue:
    client = boto3.client("sqs", region_name="ap-southeast-1")
    return AlertsQueue(client, os.environ["ALERTS_QUEUE_URL"])


@pytest.fixture
def logs():
    """Capture the handler's Powertools log lines. Call it to get parsed records."""
    import handler

    buf = io.StringIO()
    stream_handler = handler.logger.registered_handler
    old = stream_handler.setStream(buf)

    def records() -> list[dict]:
        return [json.loads(line) for line in buf.getvalue().splitlines() if line]

    records.raw = buf  # type: ignore[attr-defined]
    yield records
    stream_handler.setStream(old)
