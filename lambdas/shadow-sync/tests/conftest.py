"""Shared setup for the shadow-sync tests (docs/features/device-alert-limits.md,
criteria 6-12).

Fake AWS credentials and env vars are set before anything imports boto3, so no test
can reach real AWS. `aws` starts moto with silvercare-registry (keys and index
projections from infra/dynamodb.tf) and the test things in moto's IoT registry, and
replaces the handler's clients with recorders that log every call and can make a call
fail. moto serves iot-data only on its public host names, so IOT_DATA_ENDPOINT is one.
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
os.environ.setdefault("IOT_DATA_ENDPOINT", "data-ats.iot.ap-southeast-1.amazonaws.com")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-shadow-sync-test")

import boto3
import pytest
from botocore.exceptions import ClientError

REGION = "ap-southeast-1"
REGISTRY_TABLE = os.environ["REGISTRY_TABLE"]

DYNAMODB_TF = Path(__file__).resolve().parents[3] / "infra" / "dynamodb.tf"
THINGS = tuple(f"wearable-000{i}" for i in range(1, 6)) + ("bedroom-0001",)


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
                "IndexName": index,
                "KeySchema": [
                    {"AttributeName": hash_key, "KeyType": "HASH"},
                    {"AttributeName": range_key, "KeyType": "RANGE"},
                ],
                "Projection": tf_projection(index),
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 2,
                    "WriteCapacityUnits": 1,
                },
            }
            for index, hash_key, range_key in (
                ("by-type", "entity_type", "pk"),
                ("by-resident", "gsi1_pk", "gsi1_sk"),
            )
        ],
    )


class Recorder:
    """Wraps a moto client. Records (name, kwargs) of every call. `fail[name]` raises
    for that operation; `fail_things[thing]` raises for any call on that thing;
    `fail_updates` raises only on update_thing_shadow; `unprocessed` makes the next N
    batch_get_item calls return every key unprocessed (as when throttled);
    `page_limit` adds Limit to every Query so results come in several pages."""

    def __init__(self, client) -> None:
        self._client = client
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, Exception] = {}
        self.fail_things: dict[str, Exception] = {}
        self.fail_updates: dict[str, Exception] = {}
        self.unprocessed = 0
        self.page_limit: int | None = None

    def __getattr__(self, name):
        method = getattr(self._client, name)
        if not callable(method):
            return method

        def wrapper(**kwargs):
            if name == "query" and self.page_limit is not None:
                kwargs = {**kwargs, "Limit": self.page_limit}
            self.calls.append((name, kwargs))
            thing = kwargs.get("thingName")
            if name in self.fail:
                raise self.fail[name]
            if thing in self.fail_things:
                raise self.fail_things[thing]
            if name == "update_thing_shadow" and thing in self.fail_updates:
                raise self.fail_updates[thing]
            if name == "batch_get_item" and self.unprocessed:
                self.unprocessed -= 1
                return {"Responses": {}, "UnprocessedKeys": kwargs["RequestItems"]}
            return method(**kwargs)

        return wrapper

    def named(self, name: str) -> list[dict]:
        return [kw for n, kw in self.calls if n == name]

    def things(self, name: str) -> list[str]:
        return [kw["thingName"] for kw in self.named(name)]


def iot_data_client():
    return boto3.client(
        "iot-data",
        region_name=REGION,
        endpoint_url=f"https://{os.environ['IOT_DATA_ENDPOINT']}",
    )


def client_error(code: str, operation: str = "UpdateThingShadow") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "Synthetic error text qz-5523"}}, operation
    )


@dataclass
class Aws:
    ddb: Any
    db: Recorder
    iot: Recorder
    sleeps: list = field(default_factory=list)

    def shadow(self, thing: str) -> dict | None:
        """The thing's shadow document straight from moto, or None."""
        try:
            result = iot_data_client().get_thing_shadow(thingName=thing)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                return None
            raise
        return json.loads(result["payload"].read())

    def put_shadow(self, thing: str, state: dict) -> None:
        iot_data_client().update_thing_shadow(
            thingName=thing, payload=json.dumps({"state": state}).encode("utf-8")
        )


@pytest.fixture
def aws(monkeypatch):
    """moto with the registry table and the THINGS; the handler's clients are
    Recorders and its back-off sleeps are recorded, not slept."""
    import handler
    from moto import mock_aws

    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=REGION)
        create_registry_table(ddb)
        iot = boto3.client("iot", region_name=REGION)
        for thing in THINGS:
            iot.create_thing(thingName=thing)
        state = Aws(
            ddb=ddb,
            db=Recorder(boto3.client("dynamodb", region_name=REGION)),
            iot=Recorder(iot_data_client()),
        )
        monkeypatch.setattr(handler, "dynamodb_client", lambda: state.db)
        monkeypatch.setattr(handler, "iot_data_client", lambda: state.iot)
        monkeypatch.setattr(handler.time, "sleep", state.sleeps.append)
        yield state


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
