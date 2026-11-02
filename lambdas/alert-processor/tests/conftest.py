"""Shared setup for the alert-processor tests (docs/features/alerts-core.md, 13-20).

Fake AWS credentials and env vars are set before anything imports boto3, so no test
can reach real AWS. `aws` starts moto and creates silvercare-alerts and
silvercare-latest (keys and indexes as infra/dynamodb.tf), the history bucket, and the
alerts SNS topic.
"""

from __future__ import annotations

import io
import json
import os
import sys
from dataclasses import dataclass
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
os.environ.setdefault("ALERTS_TABLE", "silvercare-alerts")
os.environ.setdefault("LATEST_TABLE", "silvercare-latest")
os.environ.setdefault("HISTORY_BUCKET", "silvercare-history-test")
# Replaced by the moto topic's ARN in the aws fixture.
os.environ.setdefault(
    "ALERTS_TOPIC_ARN", "arn:aws:sns:ap-southeast-1:123456789012:silvercare-alerts"
)
os.environ.setdefault("PORTAL_URL", "https://d0000example.cloudfront.net")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-alert-processor-test")
os.environ.setdefault("POWERTOOLS_METRICS_NAMESPACE", "SilverCare")

import boto3
import pytest

ALERTS_TABLE = os.environ["ALERTS_TABLE"]
LATEST_TABLE = os.environ["LATEST_TABLE"]
BUCKET = os.environ["HISTORY_BUCKET"]
REGION = "ap-southeast-1"


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


class SnsRecorder:
    """Wraps the moto SNS client and keeps every publish call's arguments."""

    def __init__(self, client) -> None:
        self._client = client
        self.publishes: list[dict] = []

    def publish(self, **kwargs):
        self.publishes.append(kwargs)
        return self._client.publish(**kwargs)

    def __getattr__(self, name):
        return getattr(self._client, name)


@dataclass
class Aws:
    ddb: object
    s3: object
    sns: SnsRecorder
    topic_arn: str


def _clear_clients() -> None:
    import handler

    # At teardown a test's monkeypatch may still replace a client function.
    for fn in (handler.dynamodb_client, handler.s3_client, handler.sns_client):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()


@pytest.fixture
def aws(monkeypatch):
    """moto with both tables, the bucket, and the topic. SNS publishes are recorded."""
    import handler
    from moto import mock_aws

    with mock_aws():
        _clear_clients()
        ddb = boto3.client("dynamodb", region_name=REGION)
        create_alerts_table(ddb)
        create_latest_table(ddb)
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION}
        )
        sns = boto3.client("sns", region_name=REGION)
        topic_arn = sns.create_topic(Name="silvercare-alerts")["TopicArn"]
        monkeypatch.setenv("ALERTS_TOPIC_ARN", topic_arn)
        recorder = SnsRecorder(sns)
        monkeypatch.setattr(handler, "sns_client", lambda: recorder)
        yield Aws(ddb=ddb, s3=s3, sns=recorder, topic_arn=topic_arn)
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
