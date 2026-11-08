"""Shared setup for the api-thresholds tests (PUT /residents/{resident_id}/thresholds,
GET .../thresholds/history; docs/data-model.md patterns 10, 26, 27; device shadows from
docs/features/device-alert-limits.md, criteria 5a-5c).

Fake AWS credentials and env vars are set before anything imports boto3, so no test
can reach real AWS. `aws` starts moto with silvercare-registry (keys and index
projections from infra/dynamodb.tf), a Cognito user pool holding the caller, and the
test things in moto's IoT registry, and replaces the handler's DynamoDB and IoT data
clients with recorders that log every call and can make a call fail. moto serves
iot-data only on its public host names, so IOT_DATA_ENDPOINT is one.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
from dataclasses import dataclass
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
os.environ.setdefault("USER_POOL_ID", "ap-southeast-1_placeholder")
os.environ.setdefault("IOT_DATA_ENDPOINT", "data-ats.iot.ap-southeast-1.amazonaws.com")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-api-thresholds-test")

import boto3
import pytest
from botocore.exceptions import ClientError

REGION = "ap-southeast-1"
REGISTRY_TABLE = os.environ["REGISTRY_TABLE"]

DYNAMODB_TF = Path(__file__).resolve().parents[3] / "infra" / "dynamodb.tf"
THINGS = ("wearable-0001", "wearable-0002", "wearable-0003", "bedroom-0001")


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
    for that operation; `fail_things[thing]` raises for any call on that thing."""

    def __init__(self, client) -> None:
        self._client = client
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, Exception] = {}
        self.fail_things: dict[str, Exception] = {}

    def __getattr__(self, name):
        method = getattr(self._client, name)
        if not callable(method):
            return method

        def wrapper(**kwargs):
            self.calls.append((name, kwargs))
            if name in self.fail:
                raise self.fail[name]
            if kwargs.get("thingName") in self.fail_things:
                raise self.fail_things[kwargs["thingName"]]
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


def client_error(code: str, operation: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "Synthetic error text qz-6631"}}, operation
    )


@dataclass
class Aws:
    ddb: Any
    db: Recorder
    iot: Recorder

    def shadow(self, thing: str) -> dict | None:
        """The thing's shadow document straight from moto, or None."""
        try:
            result = iot_data_client().get_thing_shadow(thingName=thing)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                return None
            raise
        return json.loads(result["payload"].read())


@pytest.fixture
def aws(monkeypatch):
    """moto with the registry, a user pool holding the caller (events.SUB, named
    events.NAME), and the THINGS; the handler's DynamoDB and IoT data clients are
    Recorders."""
    import handler
    from events import NAME, SUB
    from moto import mock_aws

    with mock_aws():
        handler.cognito_client.cache_clear()
        ddb = boto3.client("dynamodb", region_name=REGION)
        create_registry_table(ddb)
        cognito = boto3.client("cognito-idp", region_name=REGION)
        pool = cognito.create_user_pool(PoolName="silvercare-users-test")["UserPool"]
        monkeypatch.setenv("USER_POOL_ID", pool["Id"])
        cognito.admin_create_user(
            UserPoolId=pool["Id"],
            Username=SUB,
            UserAttributes=[{"Name": "name", "Value": NAME}],
        )
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
        yield state
        handler.cognito_client.cache_clear()


@pytest.fixture(autouse=True)
def _reset_log_keys():
    """handle() appends keys to the module-level logger; drop them after each test."""
    import handler

    yield
    handler.logger.remove_keys(["request_id", "sub", "resident_id", "route"])


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
