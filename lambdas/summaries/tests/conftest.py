"""Shared setup for the summaries Lambda tests (docs/features/curated-layer.md).

Fake AWS credentials and env vars are set before anything imports boto3, so no test
can reach real AWS. Athena is a recording fake (moto can't run the SQL), and so is Glue
(the handler only calls GetPartition, and moto's Glue needs pyparsing, which the dev
image doesn't install). DynamoDB and S3 are moto. The registry table has the keys and
index projections of infra/dynamodb.tf.

Every call the handler makes is appended to one shared `calls` list, as
(service, operation, params), so tests can check the order of steps across services:
DynamoDB through a recording wrapper, S3 through botocore's `provide-client-params`
event (which paginators emit too), Athena and Glue by their fakes, and
`summary.build_all` by a wrapper. Setup and assertions use separate, unrecorded clients.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
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
os.environ["GLUE_DATABASE"] = "silvercare_lake"
os.environ["ATHENA_WORKGROUP"] = "silvercare-summaries"
os.environ["HISTORY_BUCKET"] = "silvercare-history-test"
os.environ["REGISTRY_TABLE"] = "silvercare-registry"
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-summaries-test")

import boto3
import handler
import pytest
import summary
from helpers import DATE, FakeAthena, FakeGlue, add_clean_partitions
from moto import mock_aws

REGION = "ap-southeast-1"
REGISTRY_TABLE = os.environ["REGISTRY_TABLE"]
BUCKET = os.environ["HISTORY_BUCKET"]
DATABASE = os.environ["GLUE_DATABASE"]
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


def create_registry_table(client) -> None:
    """silvercare-registry as in infra/dynamodb.tf (pk/sk, by-type, by-resident)."""
    throughput = {"ReadCapacityUnits": 2, "WriteCapacityUnits": 1}
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
        ProvisionedThroughput={"ReadCapacityUnits": 3, "WriteCapacityUnits": 1},
        GlobalSecondaryIndexes=[
            {
                "IndexName": "by-type",
                "KeySchema": [
                    {"AttributeName": "entity_type", "KeyType": "HASH"},
                    {"AttributeName": "pk", "KeyType": "RANGE"},
                ],
                "Projection": tf_projection("by-type"),
                "ProvisionedThroughput": throughput,
            },
            {
                "IndexName": "by-resident",
                "KeySchema": [
                    {"AttributeName": "gsi1_pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi1_sk", "KeyType": "RANGE"},
                ],
                "Projection": tf_projection("by-resident"),
                "ProvisionedThroughput": throughput,
            },
        ],
    )


class Recorder:
    """Wraps a moto DynamoDB client. Records every method call in `calls`, can make
    one operation raise, adds `Limit` to every Query with `page_limit` (several pages),
    and with `unprocessed_rounds = n` answers the first n BatchGetItem calls with every
    requested key unprocessed (as throttling does)."""

    def __init__(self, client, calls: list) -> None:
        self._client = client
        self.calls = calls
        self.fail: dict[str, Exception] = {}
        self.page_limit: int | None = None
        self.unprocessed_rounds = 0

    def __getattr__(self, name):
        method = getattr(self._client, name)
        if not callable(method):
            return method

        def wrapper(*args, **kwargs):
            if name == "query" and self.page_limit is not None:
                kwargs = {**kwargs, "Limit": self.page_limit}
            self.calls.append(("dynamodb", name, kwargs))
            if name in self.fail:
                raise self.fail[name]
            if name == "batch_get_item" and self.unprocessed_rounds > 0:
                self.unprocessed_rounds -= 1
                return {"Responses": {}, "UnprocessedKeys": kwargs["RequestItems"]}
            return method(*args, **kwargs)

        return wrapper


def _recording(client, service: str, calls: list):
    """Record every operation of a real (moto) client, paginated ones included."""

    def hook(params, event_name, **kwargs):
        calls.append((service, event_name.rsplit(".", 1)[-1], dict(params)))

    client.meta.events.register(f"provide-client-params.{service}", hook)
    return client


@dataclass
class Aws:
    ddb: Any  # unrecorded, for setup and checks
    s3: Any  # unrecorded
    glue: FakeGlue  # what the handler uses; edit `partitions` to remove one
    dynamodb: Recorder  # what the handler uses
    calls: list = field(default_factory=list)

    def ops(self, service: str | None = None) -> list[str]:
        return [op for svc, op, _ in self.calls if service in (None, svc)]

    def params(self, service: str, op: str) -> list[dict]:
        return [p for svc, o, p in self.calls if svc == service and o == op]

    def object_keys(self, prefix: str = "") -> list[str]:
        pages = self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=BUCKET, Prefix=prefix
        )
        return [item["Key"] for page in pages for item in page.get("Contents", [])]


@pytest.fixture
def calls() -> list:
    return []


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """Replaces the handler's sleep, so polling and back-off are instant and checked."""
    recorded: list[float] = []
    monkeypatch.setattr(handler, "time", SimpleNamespace(sleep=recorded.append))
    return recorded


@pytest.fixture
def athena(monkeypatch, sleeps, calls) -> FakeAthena:
    fake = FakeAthena(calls)
    monkeypatch.setattr(handler, "athena_client", lambda: fake)
    return fake


@pytest.fixture
def built(monkeypatch, calls):
    """Records when the summaries are built, between the query and the S3 steps."""
    real = summary.build_all

    def build_all(*args, **kwargs):
        calls.append(("build", "build_all", {}))
        return real(*args, **kwargs)

    monkeypatch.setattr(summary, "build_all", build_all)


@pytest.fixture
def aws(monkeypatch, athena, built, calls):
    """moto with the registry, the six clean tables (every partition DATE needs), and
    the history bucket. The handler's clients are recorded; Athena is the fake."""
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=REGION)
        create_registry_table(ddb)

        glue = FakeGlue(calls)
        add_clean_partitions(glue, DATABASE, DATE)

        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )

        recorder = Recorder(boto3.client("dynamodb", region_name=REGION), calls)
        handler_s3 = _recording(boto3.client("s3", region_name=REGION), "s3", calls)
        monkeypatch.setattr(handler, "dynamodb_client", lambda: recorder)
        monkeypatch.setattr(handler, "s3_client", lambda: handler_s3)
        monkeypatch.setattr(handler, "glue_client", lambda: glue)
        yield Aws(ddb=ddb, s3=s3, glue=glue, dynamodb=recorder, calls=calls)


def _no_aws():
    raise AssertionError("no AWS client may be created here")


@pytest.fixture
def no_aws(monkeypatch):
    """Any attempt to reach AWS fails the test."""
    for name in ("athena_client", "s3_client", "glue_client", "dynamodb_client"):
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
