"""Shared setup for the watchdog tests (docs/features/watchdog.md, criterion 20).

Fake AWS credentials and env vars are set before anything imports boto3, so no test
can reach real AWS. `aws` starts moto and creates silvercare-registry,
silvercare-latest, and silvercare-alerts with the keys and indexes of
infra/dynamodb.tf, the silvercare-alerts queue, and the silvercare-ops topic. SNS
publishes and SQS sends are recorded in one shared, ordered call log.
"""

from __future__ import annotations

import io
import json
import os
import sys
from dataclasses import dataclass, field
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
os.environ.setdefault("REGISTRY_TABLE", "silvercare-registry")
os.environ.setdefault("LATEST_TABLE", "silvercare-latest")
os.environ.setdefault("ALERTS_TABLE", "silvercare-alerts")
# Replaced by the moto queue's URL and topic's ARN in the aws fixture.
os.environ.setdefault(
    "ALERTS_QUEUE_URL",
    "https://sqs.ap-southeast-1.amazonaws.com/123456789012/silvercare-alerts",
)
os.environ.setdefault(
    "OPS_TOPIC_ARN", "arn:aws:sns:ap-southeast-1:123456789012:silvercare-ops"
)
os.environ.setdefault("PORTAL_URL", "https://d0000example.cloudfront.net")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-watchdog-test")

import boto3
import pytest

REGION = "ap-southeast-1"
REGISTRY_TABLE = os.environ["REGISTRY_TABLE"]
LATEST_TABLE = os.environ["LATEST_TABLE"]
ALERTS_TABLE = os.environ["ALERTS_TABLE"]


def create_registry_table(client) -> None:
    """silvercare-registry as in infra/dynamodb.tf (pk/sk, by-type, by-resident)."""
    client.create_table(
        TableName=REGISTRY_TABLE,
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


def create_latest_table(client) -> None:
    """silvercare-latest as in infra/dynamodb.tf (pk/sk, sparse by-message index)."""
    client.create_table(
        TableName=LATEST_TABLE,
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


def create_alerts_table(client) -> None:
    """silvercare-alerts as in infra/dynamodb.tf (pk/sk, sparse `open` index)."""
    client.create_table(
        TableName=ALERTS_TABLE,
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "open_pk", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 3, "WriteCapacityUnits": 2},
        GlobalSecondaryIndexes=[
            {
                "IndexName": "open",
                "KeySchema": [
                    {"AttributeName": "open_pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 1,
                    "WriteCapacityUnits": 1,
                },
            }
        ],
    )


class Recorder:
    """Wraps a moto client: records calls of the named operations, in order, in a log
    shared by every recorder, and can make one operation raise."""

    def __init__(self, client, ops: tuple[str, ...], calls: list) -> None:
        self._client = client
        self._ops = ops
        self.calls = calls
        self.fail: dict[str, Exception] = {}

    def __getattr__(self, name):
        method = getattr(self._client, name)
        if name not in self._ops:
            return method

        def wrapper(**kwargs):
            self.calls.append((name, kwargs))
            if name in self.fail:
                raise self.fail[name]
            return method(**kwargs)

        return wrapper


@dataclass
class Aws:
    ddb: object
    sns: Recorder
    sqs: Recorder
    queue_url: str
    ops_topic_arn: str
    calls: list = field(default_factory=list)

    def publishes(self) -> list[dict]:
        return [kw for op, kw in self.calls if op == "publish"]

    def sends(self) -> list[dict]:
        return [kw for op, kw in self.calls if op == "send_message"]

    def bodies(self) -> list[dict]:
        return [json.loads(kw["MessageBody"]) for kw in self.sends()]

    def alerts(self) -> list[dict]:
        return [b for b in self.bodies() if "kind" not in b]

    def resolves(self) -> list[dict]:
        return [b for b in self.bodies() if b.get("kind") == "status_update"]


def _clear_clients() -> None:
    import handler

    # At teardown a test's monkeypatch may still replace a client function.
    for fn in (handler.dynamodb_client, handler.sqs_client, handler.sns_client):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()


@pytest.fixture
def aws(monkeypatch):
    """moto with the three tables, the alerts queue, and the ops topic."""
    import handler
    from moto import mock_aws

    with mock_aws():
        _clear_clients()
        ddb = boto3.client("dynamodb", region_name=REGION)
        create_registry_table(ddb)
        create_latest_table(ddb)
        create_alerts_table(ddb)
        calls: list = []
        sns = boto3.client("sns", region_name=REGION)
        topic_arn = sns.create_topic(Name="silvercare-ops")["TopicArn"]
        monkeypatch.setenv("OPS_TOPIC_ARN", topic_arn)
        sns_recorder = Recorder(sns, ("publish",), calls)
        monkeypatch.setattr(handler, "sns_client", lambda: sns_recorder)
        sqs = boto3.client("sqs", region_name=REGION)
        queue_url = sqs.create_queue(QueueName="silvercare-alerts")["QueueUrl"]
        monkeypatch.setenv("ALERTS_QUEUE_URL", queue_url)
        sqs_recorder = Recorder(sqs, ("send_message",), calls)
        monkeypatch.setattr(handler, "sqs_client", lambda: sqs_recorder)
        yield Aws(
            ddb=ddb,
            sns=sns_recorder,
            sqs=sqs_recorder,
            queue_url=queue_url,
            ops_topic_arn=topic_arn,
            calls=calls,
        )
    _clear_clients()


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
