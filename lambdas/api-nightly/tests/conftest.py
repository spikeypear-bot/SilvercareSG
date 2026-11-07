"""Shared setup for the api-nightly tests (GET /residents/{resident_id}/nightly,
docs/features/nightly-results.md; pattern 31).

Fake AWS credentials and env vars are set before anything imports boto3, so no test
can reach real AWS. `aws` starts moto and creates silvercare-results (pk/sk, no
indexes, as infra/dynamodb.tf). The handler's DynamoDB client is a recorder that logs
every call, can make one operation fail, and can force small Query pages.
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
os.environ.setdefault("RESULTS_TABLE", "silvercare-results")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-api-nightly-test")

import boto3
import handler
import pytest
from moto import mock_aws

REGION = "ap-southeast-1"
RESULTS_TABLE = os.environ["RESULTS_TABLE"]


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
    """Wraps a DynamoDB client. Records every call, can make one operation raise, and
    with `page_limit` adds `Limit` to every Query so results come in several pages."""

    def __init__(self, client, calls: list) -> None:
        self._client = client
        self.calls = calls
        self.fail: dict[str, Exception] = {}
        self.page_limit: int | None = None

    def __getattr__(self, name):
        method = getattr(self._client, name)
        if not callable(method):
            return method

        def wrapper(*args, **kwargs):
            if name == "query" and self.page_limit is not None:
                kwargs = {**kwargs, "Limit": self.page_limit}
            self.calls.append((name, kwargs))
            if name in self.fail:
                raise self.fail[name]
            return method(*args, **kwargs)

        return wrapper


@dataclass
class Aws:
    ddb: Any
    client: Recorder
    calls: list = field(default_factory=list)

    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]

    def queries(self) -> list[dict[str, Any]]:
        return [kw for op, kw in self.calls if op == "query"]


@pytest.fixture
def aws(monkeypatch):
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=REGION)
        create_results_table(ddb)
        calls: list = []
        recorder = Recorder(boto3.client("dynamodb", region_name=REGION), calls)
        monkeypatch.setattr(handler, "dynamodb_client", lambda: recorder)
        yield Aws(ddb=ddb, client=recorder, calls=calls)


def _no_aws():
    raise AssertionError("no AWS client may be created here")


@pytest.fixture
def no_aws(monkeypatch):
    monkeypatch.setattr(handler, "dynamodb_client", _no_aws)


@pytest.fixture(autouse=True)
def _reset_keys():
    yield
    handler.logger.remove_keys(handler.LOG_KEYS)


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
