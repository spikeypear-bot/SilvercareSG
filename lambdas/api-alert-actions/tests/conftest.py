"""Shared setup for the api-alert-actions tests (POST .../acknowledge, .../cancel,
.../resolve; docs/data-model.md, Status updates and Track B route: resolve).

Fake AWS credentials and env vars are set before anything imports boto3, so no test
can reach real AWS. `aws` starts moto with silvercare-registry and silvercare-alerts
(keys and index projections from infra/dynamodb.tf) and the silvercare-alerts queue,
and replaces the handler's clients with recorders that log every call in one ordered
list and can make one operation fail. `ap_message` loads alert-processor's own
validator (lambdas/alert-processor/src/message.py) under another module name, so it
never clashes with this function's `handler` and `rules`.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
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
os.environ.setdefault("ALERTS_TABLE", "silvercare-alerts")
# Replaced by the moto queue's URL in the aws fixture.
os.environ.setdefault(
    "ALERTS_QUEUE_URL",
    "https://sqs.ap-southeast-1.amazonaws.com/123456789012/silvercare-alerts",
)
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-api-alert-actions-test")

import boto3
import pytest

REGION = "ap-southeast-1"
REGISTRY_TABLE = os.environ["REGISTRY_TABLE"]
ALERTS_TABLE = os.environ["ALERTS_TABLE"]

DYNAMODB_TF = Path(__file__).resolve().parents[3] / "infra" / "dynamodb.tf"
MESSAGE_PY = (
    Path(__file__).resolve().parents[2] / "alert-processor" / "src" / "message.py"
)


def tf_projection(index_name: str) -> dict:
    """The `Projection` of `index_name` exactly as infra/dynamodb.tf declares it (the
    dynamodb-table module: INCLUDE with its non_key_attributes, else KEYS_ONLY)."""
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
                "Projection": tf_projection("by-type"),
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
                "Projection": tf_projection("by-resident"),
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 1,
                    "WriteCapacityUnits": 1,
                },
            },
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
                "Projection": tf_projection("open"),
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 2,
                    "WriteCapacityUnits": 1,
                },
            }
        ],
    )


class Recorder:
    """Wraps a moto client: records every method call (name, kwargs) in a shared,
    ordered log and can make one operation raise."""

    def __init__(self, client, calls: list) -> None:
        self._client = client
        self.calls = calls
        self.fail: dict[str, Exception] = {}

    def __getattr__(self, name):
        method = getattr(self._client, name)
        if not callable(method):
            return method

        def wrapper(*args, **kwargs):
            self.calls.append((name, kwargs))
            if name in self.fail:
                raise self.fail[name]
            return method(*args, **kwargs)

        return wrapper


@dataclass
class Aws:
    ddb: Any
    sqs_raw: Any
    dynamodb: Recorder
    sqs: Recorder
    queue_url: str
    calls: list = field(default_factory=list)

    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]

    def get_items(self) -> list[dict[str, Any]]:
        return [kw for op, kw in self.calls if op == "get_item"]

    def sends(self) -> list[dict[str, Any]]:
        return [kw for op, kw in self.calls if op == "send_message"]

    def queued(self) -> list[str]:
        """Every body actually on the moto queue (drains it)."""
        out: list[str] = []
        while True:
            got = self.sqs_raw.receive_message(
                QueueUrl=self.queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=0
            )
            messages = got.get("Messages", [])
            if not messages:
                return out
            for m in messages:
                out.append(m["Body"])
                self.sqs_raw.delete_message(
                    QueueUrl=self.queue_url, ReceiptHandle=m["ReceiptHandle"]
                )


def _clear_clients() -> None:
    import handler

    # At teardown a test's monkeypatch may still replace a client function.
    for fn in (handler.dynamodb_client, handler.sqs_client):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()


@pytest.fixture
def aws(monkeypatch):
    """moto with the registry and alerts tables and the alerts queue; the handler's
    clients are Recorders sharing one call log."""
    import handler
    from moto import mock_aws

    with mock_aws():
        _clear_clients()
        ddb = boto3.client("dynamodb", region_name=REGION)
        create_registry_table(ddb)
        create_alerts_table(ddb)
        sqs = boto3.client("sqs", region_name=REGION)
        queue_url = sqs.create_queue(QueueName="silvercare-alerts")["QueueUrl"]
        monkeypatch.setenv("ALERTS_QUEUE_URL", queue_url)
        calls: list = []
        ddb_recorder = Recorder(boto3.client("dynamodb", region_name=REGION), calls)
        sqs_recorder = Recorder(boto3.client("sqs", region_name=REGION), calls)
        monkeypatch.setattr(handler, "dynamodb_client", lambda: ddb_recorder)
        monkeypatch.setattr(handler, "sqs_client", lambda: sqs_recorder)
        yield Aws(
            ddb=ddb,
            sqs_raw=sqs,
            dynamodb=ddb_recorder,
            sqs=sqs_recorder,
            queue_url=queue_url,
            calls=calls,
        )
    _clear_clients()


@pytest.fixture
def frozen_now(monkeypatch):
    """Make the handler's `datetime.now(UTC)` return events.NOW."""
    import handler
    from events import NOW

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(handler, "datetime", Frozen)
    return NOW


@pytest.fixture(autouse=True)
def _reset_log_keys():
    """handle() appends keys to the module-level logger; drop them after each test."""
    import handler

    yield
    handler.logger.remove_keys(
        ["request_id", "sub", "action", "resident_id", "alert_id"]
    )


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


@pytest.fixture(scope="session")
def ap_message():
    """alert-processor's message.py (pure), loaded by path under a distinct name."""
    name = "alert_processor_message_for_api_alert_actions"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, MESSAGE_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
