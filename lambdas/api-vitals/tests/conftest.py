"""Shared setup for the api-vitals tests (GET /residents/{resident_id}/vitals,
docs/features/vitals-trends.md).

Fake AWS credentials and env vars are set before anything imports boto3. `aws` starts
moto with silvercare-registry (pk/sk, by-type, by-resident, each index projected as in
infra/dynamodb.tf). Athena isn't in moto's installed extras, so `athena` is a small
recording fake of the calls the handler makes.
"""

from __future__ import annotations

import os
import re
import sys
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
os.environ.setdefault("GLUE_DATABASE", "silvercare_lake")
os.environ.setdefault("ATHENA_WORKGROUP", "silvercare-vitals")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-api-vitals-test")

import boto3
import handler
import pytest
from moto import mock_aws

REGISTRY_TABLE = os.environ["REGISTRY_TABLE"]
DYNAMODB_TF = Path(__file__).resolve().parents[3] / "infra" / "dynamodb.tf"


def tf_projection(index_name: str) -> dict:
    """The `Projection` of `index_name` exactly as infra/dynamodb.tf declares it."""
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
    def gsi(name: str, hash_key: str, range_key: str) -> dict:
        return {
            "IndexName": name,
            "KeySchema": [
                {"AttributeName": hash_key, "KeyType": "HASH"},
                {"AttributeName": range_key, "KeyType": "RANGE"},
            ],
            "Projection": tf_projection(name),
            "ProvisionedThroughput": {"ReadCapacityUnits": 2, "WriteCapacityUnits": 1},
        }

    client.create_table(
        TableName=REGISTRY_TABLE,
        AttributeDefinitions=[
            {"AttributeName": n, "AttributeType": "S"}
            for n in ("pk", "sk", "entity_type", "gsi1_pk", "gsi1_sk")
        ],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 2, "WriteCapacityUnits": 1},
        GlobalSecondaryIndexes=[
            gsi("by-type", "entity_type", "pk"),
            gsi("by-resident", "gsi1_pk", "gsi1_sk"),
        ],
    )


class FakeAthena:
    """Records queries and serves one result set per query (header row first)."""

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.requests: list[dict] = []
        self.stopped: list[str] = []
        self.state = "SUCCEEDED"
        self.running_polls = 0
        self.columns: list[str] = []
        self.rows: list[dict[str, str | None]] = []
        self._polls = 0

    def start_query_execution(self, **kwargs: Any) -> dict:
        self.requests.append(kwargs)
        self.queries.append(kwargs["QueryString"])
        return {"QueryExecutionId": f"query-test-{len(self.queries):04d}"}

    def get_query_execution(self, QueryExecutionId: str) -> dict:
        self._polls += 1
        state = "RUNNING" if self._polls <= self.running_polls else self.state
        return {
            "QueryExecution": {
                "Status": {"State": state, "StateChangeReason": "hr_bpm 77 leaked"}
            }
        }

    def stop_query_execution(self, QueryExecutionId: str) -> dict:
        self.stopped.append(QueryExecutionId)
        return {}

    def get_paginator(self, name: str) -> Any:
        assert name == "get_query_results"
        fake = self

        class Paginator:
            def paginate(self, **kwargs: Any):
                header = [{"VarCharValue": c} for c in fake.columns]
                data = [
                    [
                        {} if r.get(c) is None else {"VarCharValue": r[c]}
                        for c in fake.columns
                    ]
                    for r in fake.rows
                ]
                rows = [{"Data": header}, *({"Data": d} for d in data)]
                yield {"ResultSet": {"Rows": rows}}

        return Paginator()


@pytest.fixture
def aws(monkeypatch):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="ap-southeast-1")
        create_registry_table(client)
        handler.dynamodb_client.cache_clear()
        fake = FakeAthena()
        monkeypatch.setattr(handler, "athena_client", lambda: fake)
        monkeypatch.setattr(handler.time, "sleep", lambda s: None)
        yield client, fake
        handler.dynamodb_client.cache_clear()


@pytest.fixture
def logs():
    """Capture the handler's Powertools log lines, parsed, without `timestamp` and
    `location` (a time like 13:33:05,187 holds digits)."""
    import io
    import json

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


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    from events import NOW

    monkeypatch.setattr(handler, "now_utc", lambda: NOW)


@pytest.fixture(autouse=True)
def _empty_cache():
    handler.clear_cache()
    yield
    handler.clear_cache()
