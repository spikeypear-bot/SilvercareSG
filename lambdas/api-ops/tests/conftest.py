"""Shared setup for the api-ops tests (GET /ops/device-problems, docs/data-model.md,
the **ops** paragraph of Access patterns).

Fake AWS credentials and env vars are set before anything imports boto3, so no test
can reach real AWS. `aws` starts moto and creates silvercare-alerts with the keys and
`open` index projection of infra/dynamodb.tf, plus a moto Cognito user pool. The
handler's DynamoDB and Cognito clients are replaced by recorders that log every call,
can replace one operation's result, and (DynamoDB) can force small Query pages.
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
os.environ.setdefault("ALERTS_TABLE", "silvercare-alerts")
os.environ.setdefault("USER_POOL_ID", "ap-southeast-1_testpool0")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-api-ops-test")

import boto3
import pytest

REGION = "ap-southeast-1"
ALERTS_TABLE = os.environ["ALERTS_TABLE"]
DYNAMODB_TF = Path(__file__).resolve().parents[3] / "infra" / "dynamodb.tf"


def tf_projection(index_name: str) -> dict:
    """The `Projection` of `index_name` exactly as infra/dynamodb.tf declares it, so
    reading an attribute the index doesn't project fails here too."""
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


def create_alerts_table(client, table_name: str = ALERTS_TABLE) -> None:
    """silvercare-alerts as in infra/dynamodb.tf (pk/sk, sparse `open` index)."""
    client.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": name, "AttributeType": "S"}
            for name in ("pk", "sk", "open_pk")
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
                "Projection": tf_projection("open"),
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 2,
                    "WriteCapacityUnits": 1,
                },
            }
        ],
    )


class Recorder:
    """Wraps a moto client. Records every call (name, kwargs) in order. `page_limit`
    adds `Limit` to every Query; `override[name]` replaces a call's result with
    `override[name](kwargs, real_result)`."""

    def __init__(self, client, calls: list) -> None:
        self._client = client
        self.calls = calls
        self.page_limit: int | None = None
        self.override: dict[str, Any] = {}

    def __getattr__(self, name):
        method = getattr(self._client, name)
        if not callable(method):
            return method

        def wrapper(*args, **kwargs):
            if name == "query" and self.page_limit is not None:
                kwargs = {**kwargs, "Limit": self.page_limit}
            self.calls.append((name, kwargs))
            result = method(*args, **kwargs)
            if name in self.override:
                return self.override[name](kwargs, result)
            return result

        return wrapper


@dataclass
class Aws:
    ddb: Any
    client: Recorder
    cognito: Any
    user_pool_id: str
    calls: list = field(default_factory=list)
    cognito_calls: list = field(default_factory=list)

    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]

    def queries(self) -> list[dict[str, Any]]:
        return [kw for op, kw in self.calls if op == "query"]

    def batch_gets(self) -> list[dict[str, Any]]:
        return [kw for op, kw in self.calls if op == "batch_get_item"]

    def add_user(self, email: str, name: str | None) -> str:
        attributes = [{"Name": "email", "Value": email}]
        if name is not None:
            attributes.append({"Name": "name", "Value": name})
        user = self.cognito.admin_create_user(
            UserPoolId=self.user_pool_id,
            Username=email,
            UserAttributes=attributes,
            MessageAction="SUPPRESS",
        )["User"]
        return next(a["Value"] for a in user["Attributes"] if a["Name"] == "sub")


def _clear_clients() -> None:
    import handler

    for factory in (handler.dynamodb_client, handler.cognito_client):
        if hasattr(factory, "cache_clear"):
            factory.cache_clear()


@pytest.fixture
def aws(monkeypatch):
    import handler
    from moto import mock_aws

    with mock_aws():
        _clear_clients()
        ddb = boto3.client("dynamodb", region_name=REGION)
        create_alerts_table(ddb)
        cognito = boto3.client("cognito-idp", region_name=REGION)
        pool_id = cognito.create_user_pool(
            PoolName="silvercare-users-test", UsernameAttributes=["email"]
        )["UserPool"]["Id"]
        monkeypatch.setenv("USER_POOL_ID", pool_id)

        calls: list = []
        cognito_calls: list = []
        recorder = Recorder(boto3.client("dynamodb", region_name=REGION), calls)
        cognito_recorder = Recorder(
            boto3.client("cognito-idp", region_name=REGION), cognito_calls
        )
        state = Aws(
            ddb=ddb,
            client=recorder,
            cognito=cognito,
            user_pool_id=pool_id,
            calls=calls,
            cognito_calls=cognito_calls,
        )
        monkeypatch.setattr(handler, "dynamodb_client", lambda: recorder)
        monkeypatch.setattr(handler, "cognito_client", lambda: cognito_recorder)
        yield state
    _clear_clients()


@pytest.fixture(autouse=True)
def _reset_state():
    import handler

    handler.clear_caches()
    yield
    handler.logger.remove_keys(["request_id"])
    handler.clear_caches()


@pytest.fixture
def logs():
    """The handler's Powertools log lines, parsed, without timestamp and location."""
    import handler

    buf = io.StringIO()
    stream_handler = handler.logger.registered_handler
    old = stream_handler.setStream(buf)

    def records() -> list[dict]:
        out = []
        for line in buf.getvalue().splitlines():
            if line:
                record = json.loads(line)
                record.pop("timestamp", None)
                record.pop("location", None)
                out.append(record)
        return out

    yield records
    stream_handler.setStream(old)
