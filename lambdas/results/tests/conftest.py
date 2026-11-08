"""Shared setup for the results Lambda tests (docs/features/nightly-results.md).

Fake AWS credentials and env vars are set before anything imports boto3, so no test
can reach real AWS. `aws` starts moto with the history bucket and silvercare-results
(pk/sk, no indexes, as infra/dynamodb.tf). Every call the handler makes is appended to
one shared `calls` list as (service, operation, params): S3 through botocore's
`provide-client-params` event (paginators emit it too), DynamoDB through a recording
wrapper that can also make one operation raise. Setup and checks use separate,
unrecorded clients.
"""

from __future__ import annotations

import io
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
os.environ["HISTORY_BUCKET"] = "silvercare-history-test"
os.environ.setdefault("RESULTS_TABLE", "silvercare-results")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-results-test")

import boto3
import handler
import pytest
from boto3.dynamodb.types import TypeDeserializer
from moto import mock_aws

REGION = "ap-southeast-1"
BUCKET = os.environ["HISTORY_BUCKET"]
RESULTS_TABLE = os.environ["RESULTS_TABLE"]

_deserializer = TypeDeserializer()


def create_results_table(client, table_name: str = RESULTS_TABLE) -> None:
    """silvercare-results as in infra/dynamodb.tf: pk/sk, 1 RCU / 1 WCU, no indexes."""
    client.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 1, "WriteCapacityUnits": 1},
    )


class Recorder:
    """Wraps a moto DynamoDB client: records each call, can make one operation raise."""

    def __init__(self, client, calls: list) -> None:
        self._client = client
        self.calls = calls
        self.fail: dict[str, Exception] = {}

    def __getattr__(self, name):
        method = getattr(self._client, name)
        if not callable(method):
            return method

        def wrapper(*args, **kwargs):
            self.calls.append(("dynamodb", name, kwargs))
            if name in self.fail:
                raise self.fail[name]
            return method(*args, **kwargs)

        return wrapper


def _recording(client, service: str, calls: list):
    def hook(params, event_name, **kwargs):
        calls.append((service, event_name.rsplit(".", 1)[-1], dict(params)))

    client.meta.events.register(f"provide-client-params.{service}", hook)
    return client


@dataclass
class Aws:
    ddb: Any  # unrecorded
    s3: Any  # unrecorded
    dynamodb: Recorder  # what the handler uses
    calls: list = field(default_factory=list)

    def ops(self, service: str | None = None) -> list[str]:
        return [op for svc, op, _ in self.calls if service in (None, svc)]

    def params(self, service: str, op: str) -> list[dict]:
        return [p for svc, o, p in self.calls if svc == service and o == op]

    def put_object(self, key: str, body: bytes | str) -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.s3.put_object(Bucket=BUCKET, Key=key, Body=body)

    def put_summary(self, key: str, summary: dict) -> None:
        self.put_object(key, json.dumps(summary))

    def raw_items(self) -> list[dict]:
        return self.ddb.scan(TableName=RESULTS_TABLE)["Items"]

    def items(self) -> list[dict]:
        return [
            {k: _deserializer.deserialize(v) for k, v in item.items()}
            for item in self.raw_items()
        ]

    def raw_item(self, resident_id: str, date: str) -> dict | None:
        return self.ddb.get_item(
            TableName=RESULTS_TABLE,
            Key={"pk": {"S": f"RESIDENT#{resident_id}"}, "sk": {"S": f"NIGHT#{date}"}},
        ).get("Item")


@pytest.fixture
def aws(monkeypatch):
    calls: list = []
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=REGION)
        create_results_table(ddb)
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION}
        )
        recorder = Recorder(boto3.client("dynamodb", region_name=REGION), calls)
        handler_s3 = _recording(boto3.client("s3", region_name=REGION), "s3", calls)
        monkeypatch.setattr(handler, "dynamodb_client", lambda: recorder)
        monkeypatch.setattr(handler, "s3_client", lambda: handler_s3)
        yield Aws(ddb=ddb, s3=s3, dynamodb=recorder, calls=calls)


def _no_aws():
    raise AssertionError("no AWS client may be created here")


@pytest.fixture
def no_aws(monkeypatch):
    """Any attempt to reach AWS fails the test."""
    for name in ("s3_client", "dynamodb_client"):
        monkeypatch.setattr(handler, name, _no_aws)


@pytest.fixture
def logs():
    """Capture the handler's Powertools log lines. Call it to get parsed records
    without `timestamp` and `location` (a time like 13:33:05,187 holds digits)."""
    buf = io.StringIO()
    stream_handler = handler.logger.registered_handler
    old = stream_handler.setStream(buf)

    def records() -> list[dict]:
        out = []
        for line in buf.getvalue().splitlines():
            if not line:
                continue
            record = json.loads(line)
            record.pop("timestamp", None)
            record.pop("location", None)
            out.append(record)
        return out

    yield records
    stream_handler.setStream(old)
