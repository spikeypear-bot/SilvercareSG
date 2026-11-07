"""Shared setup for the api-dashboard tests (GET /dashboard, docs/data-model.md,
Dashboard; patterns 9, 8, 5, 4).

Fake AWS credentials and env vars are set before anything imports boto3, so no test
can reach real AWS. `aws` starts moto and creates silvercare-registry,
silvercare-latest, and silvercare-alerts with the keys and index projections of
infra/dynamodb.tf, plus a moto Cognito user pool. The handler's DynamoDB and Cognito
clients are replaced by recorders that log every call, can make one operation fail,
and (DynamoDB) can force small Query pages.
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
os.environ.setdefault("REGISTRY_TABLE", "silvercare-registry")
os.environ.setdefault("LATEST_TABLE", "silvercare-latest")
os.environ.setdefault("USER_POOL_ID", "ap-southeast-1_testpool0")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-api-dashboard-test")

import boto3
import pytest

REGION = "ap-southeast-1"
ALERTS_TABLE = os.environ["ALERTS_TABLE"]
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


def _gsi(name: str, hash_key: str, range_key: str) -> dict:
    return {
        "IndexName": name,
        "KeySchema": [
            {"AttributeName": hash_key, "KeyType": "HASH"},
            {"AttributeName": range_key, "KeyType": "RANGE"},
        ],
        "Projection": tf_projection(name),
        "ProvisionedThroughput": {"ReadCapacityUnits": 2, "WriteCapacityUnits": 1},
    }


def _create(client, table_name: str, attributes: tuple[str, ...], gsis: list) -> None:
    client.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": name, "AttributeType": "S"} for name in attributes
        ],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 2, "WriteCapacityUnits": 1},
        GlobalSecondaryIndexes=gsis,
    )


def create_registry_table(client, table_name: str = REGISTRY_TABLE) -> None:
    """silvercare-registry as in infra/dynamodb.tf (pk/sk, by-type, by-resident)."""
    _create(
        client,
        table_name,
        ("pk", "sk", "entity_type", "gsi1_pk", "gsi1_sk"),
        [
            _gsi("by-type", "entity_type", "pk"),
            _gsi("by-resident", "gsi1_pk", "gsi1_sk"),
        ],
    )


def create_latest_table(client, table_name: str = LATEST_TABLE) -> None:
    """silvercare-latest as in infra/dynamodb.tf (pk/sk, sparse by-message index)."""
    _create(
        client,
        table_name,
        ("pk", "sk", "watch_type"),
        [_gsi("by-message", "watch_type", "pk")],
    )


def create_alerts_table(client, table_name: str = ALERTS_TABLE) -> None:
    """silvercare-alerts as in infra/dynamodb.tf (pk/sk, sparse `open` index)."""
    _create(
        client, table_name, ("pk", "sk", "open_pk"), [_gsi("open", "open_pk", "sk")]
    )


class Recorder:
    """Wraps a moto client. Records every method call (name, kwargs) in order, can make
    one operation raise, and with `page_limit` adds `Limit` to every Query so results
    come back in several pages."""

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
    cognito: Any
    cognito_client: Recorder
    user_pool_id: str
    calls: list = field(default_factory=list)
    cognito_calls: list = field(default_factory=list)
    cognito_requests: list = field(default_factory=list)

    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]

    def queries(self) -> list[dict[str, Any]]:
        return [kw for op, kw in self.calls if op == "query"]

    def batch_gets(self) -> list[dict[str, Any]]:
        return [kw for op, kw in self.calls if op == "batch_get_item"]

    def cognito_ops(self) -> list[str]:
        return [op for op, _ in self.cognito_calls]

    def lookups(self) -> list[dict[str, Any]]:
        """Every AdminGetUser the handler made."""
        return [kw for op, kw in self.cognito_calls if op == "admin_get_user"]

    def add_user(self, email: str, name: str | None) -> str:
        """A synthetic Cognito user (optionally with `name`); returns moto's `sub`,
        which is also the username (the pool signs in with email)."""
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

    def delete_user(self, sub: str) -> None:
        self.cognito.admin_delete_user(UserPoolId=self.user_pool_id, Username=sub)


def _clear_clients() -> None:
    import handler

    # At teardown a test's monkeypatch may still replace the client functions.
    for factory in (handler.dynamodb_client, handler.cognito_client):
        if hasattr(factory, "cache_clear"):
            factory.cache_clear()


@pytest.fixture
def aws(monkeypatch):
    """moto with the three tables and a user pool; the handler's clients are
    Recorders. `cognito_requests` counts how often the handler asked for a Cognito
    client at all."""
    import handler
    from moto import mock_aws

    with mock_aws():
        _clear_clients()
        ddb = boto3.client("dynamodb", region_name=REGION)
        create_registry_table(ddb)
        create_latest_table(ddb)
        create_alerts_table(ddb)
        cognito = boto3.client("cognito-idp", region_name=REGION)
        # As silvercare-users: email is the username attribute, so the username is
        # the sub.
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
            cognito_client=cognito_recorder,
            user_pool_id=pool_id,
            calls=calls,
            cognito_calls=cognito_calls,
        )

        def cognito_factory():
            state.cognito_requests.append(1)
            return cognito_recorder

        monkeypatch.setattr(handler, "dynamodb_client", lambda: recorder)
        monkeypatch.setattr(handler, "cognito_client", cognito_factory)
        yield state
    _clear_clients()


@pytest.fixture(autouse=True)
def _reset_state():
    """handle() appends request_id to the module-level logger, and thresholds and
    names are cached per container: reset both around each test."""
    import handler

    handler.clear_caches()
    yield
    handler.logger.remove_keys(["request_id"])
    handler.clear_caches()


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
