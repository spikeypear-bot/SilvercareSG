"""Shared setup for the api-devices tests (GET /devices, docs/data-model.md, Devices
view).

Fake AWS credentials and env vars are set before anything imports boto3, so no test
can reach real AWS. `aws` starts moto and creates silvercare-registry and
silvercare-latest with the keys and index projections of infra/dynamodb.tf, and
replaces the handler's DynamoDB client with a recorder that logs every call, can make
one operation fail, and can force small Query pages.
"""

from __future__ import annotations

import io
import json
import os
import re
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
os.environ.setdefault("REGISTRY_TABLE", "silvercare-registry")
os.environ.setdefault("LATEST_TABLE", "silvercare-latest")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-api-devices-test")

import boto3
import pytest

REGION = "ap-southeast-1"
REGISTRY_TABLE = os.environ["REGISTRY_TABLE"]
LATEST_TABLE = os.environ["LATEST_TABLE"]

DYNAMODB_TF = Path(__file__).resolve().parents[3] / "infra" / "dynamodb.tf"


def tf_projection(index_name: str) -> dict:
    """The `Projection` of `index_name` exactly as infra/dynamodb.tf declares it (the
    dynamodb-table module: INCLUDE with its non_key_attributes, else KEYS_ONLY), so the
    fixture can't drift from what is deployed and reading an attribute the index
    doesn't project fails here too."""
    text = DYNAMODB_TF.read_text(encoding="utf-8")
    block = re.search(
        r'name\s*=\s*"' + re.escape(index_name) + r'"(.*?)read_capacity',
        text,
        re.DOTALL,
    )
    if block is None:
        raise LookupError(f"index {index_name} not found in {DYNAMODB_TF}")
    listed = re.search(r"non_key_attributes\s*=\s*\[(.*?)\]", block.group(1), re.DOTALL)
    attributes = re.findall(r'"([^"]+)"', listed.group(1)) if listed else []
    if not attributes:
        return {"ProjectionType": "KEYS_ONLY"}
    return {"ProjectionType": "INCLUDE", "NonKeyAttributes": attributes}


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
                "Projection": tf_projection("by-type"),
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 2,
                    "WriteCapacityUnits": 1,
                },
            },
            {
                "IndexName": "by-resident",
                "KeySchema": [
                    {"AttributeName": "gsi1_pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi1_sk", "KeyType": "RANGE"},
                ],
                "Projection": tf_projection("by-resident"),
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 2,
                    "WriteCapacityUnits": 1,
                },
            },
        ],
    )


def create_latest_table(client, table_name: str = LATEST_TABLE) -> None:
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
                "Projection": tf_projection("by-message"),
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 3,
                    "WriteCapacityUnits": 4,
                },
            }
        ],
    )


class Recorder:
    """Wraps a moto DynamoDB client. Records every method call (name, kwargs) in
    order, can make one operation raise, and with `page_limit` adds `Limit` to every
    Query so results come back in several pages."""

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
    client_requests: list = field(default_factory=list)

    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]

    def queries(self) -> list[dict[str, Any]]:
        return [kw for op, kw in self.calls if op == "query"]


def _clear_client() -> None:
    import handler

    # At teardown a test's monkeypatch may still replace the client function.
    if hasattr(handler.dynamodb_client, "cache_clear"):
        handler.dynamodb_client.cache_clear()


@pytest.fixture
def aws(monkeypatch):
    """moto with the registry and latest tables; the handler's client is a Recorder.
    `client_requests` counts how often the handler asked for a client at all."""
    import handler
    from moto import mock_aws

    with mock_aws():
        _clear_client()
        ddb = boto3.client("dynamodb", region_name=REGION)
        create_registry_table(ddb)
        create_latest_table(ddb)
        calls: list = []
        recorder = Recorder(boto3.client("dynamodb", region_name=REGION), calls)
        state = Aws(ddb=ddb, client=recorder, calls=calls)

        def client():
            state.client_requests.append(1)
            return recorder

        monkeypatch.setattr(handler, "dynamodb_client", client)
        yield state
    _clear_client()


@pytest.fixture(autouse=True)
def _reset_log_keys():
    """handle() appends request_id to the module-level logger; drop it after each
    test so no test sees another's key."""
    import handler

    yield
    handler.logger.remove_keys(["request_id"])


@pytest.fixture
def logs():
    """Capture the handler's Powertools log lines. Call it to get parsed records
    without `timestamp` and `location` (a time like 13:33:05,187 holds digits)."""
    import handler

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
