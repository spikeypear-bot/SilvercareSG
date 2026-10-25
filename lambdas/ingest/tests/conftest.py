"""Shared setup for the ingest Lambda tests (docs/features/ingest-live.md).

Fake AWS credentials and env vars are set before anything imports boto3, so no test
can reach real AWS. `latest_table` starts moto and creates silvercare-latest with the
same keys and index as infra/dynamodb.tf.
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
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-ingest-test")

import boto3
import pytest

TABLE_NAME = os.environ["LATEST_TABLE"]


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


@pytest.fixture
def latest_table():
    """moto DynamoDB with silvercare-latest. Yields a low-level client for checks."""
    import handler
    from moto import mock_aws

    with mock_aws():
        handler.dynamodb_client.cache_clear()
        client = boto3.client("dynamodb", region_name="ap-southeast-1")
        create_latest_table(client)
        yield client
    handler.dynamodb_client.cache_clear()


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
