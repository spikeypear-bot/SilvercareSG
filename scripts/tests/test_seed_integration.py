"""scripts/seed.py --local against DynamoDB Local, twice (criterion 24).

Run with `make test-integration FN=scripts`, which sets DYNAMODB_ENDPOINT. Skips
without it. Only the DynamoDB Local endpoint may be reached.
"""

from __future__ import annotations

import io
import os
import socket
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.integration

ENDPOINT = os.environ.get("DYNAMODB_ENDPOINT")
FAMILY_SUB = "00000000-0000-4000-8000-000000000004"
CAREGIVER_SUB = "00000000-0000-4000-8000-000000000003"
NOW = datetime(2026, 11, 1, 3, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 2, 3, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Overrides conftest's no_network: allow only the DynamoDB Local host."""
    if not ENDPOINT:
        pytest.skip("DYNAMODB_ENDPOINT not set (run make test-integration FN=scripts)")
    allowed = urlparse(ENDPOINT).hostname
    real_getaddrinfo = socket.getaddrinfo

    def only_local(host, *args, **kwargs):
        if host != allowed:
            raise RuntimeError(f"tests may only reach DynamoDB Local, not {host}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", only_local)


@pytest.fixture
def local(seed, aws_env, monkeypatch):
    monkeypatch.setenv("DYNAMODB_ENDPOINT", ENDPOINT)
    return seed.make_client("dynamodb", local=True)


@pytest.fixture
def scratch_table(local):
    name = f"silvercare-registry-test-{uuid.uuid4().hex[:8]}"
    yield name
    try:
        local.delete_table(TableName=name)
    except local.exceptions.ResourceNotFoundException:
        pass


def query(client, table, index, name, value, prefix=None, sk_name=None):
    expr, names, values = "#k = :v", {"#k": name}, {":v": {"S": value}}
    if prefix:
        expr += " AND begins_with(#s, :p)"
        names["#s"] = sk_name
        values[":p"] = {"S": prefix}
    kwargs = {
        "TableName": table,
        "KeyConditionExpression": expr,
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
    }
    if index:
        kwargs["IndexName"] = index
    items = []
    while True:
        response = client.query(**kwargs)
        items.extend(response["Items"])
        if "LastEvaluatedKey" not in response:
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def check_criterion_8(client, table):
    assert len(query(client, table, "by-type", "entity_type", "DEVICE")) >= 40
    seeded_residents = [
        i
        for i in query(client, table, "by-type", "entity_type", "RESIDENT")
        if i["pk"]["S"] in {f"RESIDENT#res-{n:04d}" for n in range(1, 21)}
    ]
    assert len(seeded_residents) == 20
    devices = query(
        client,
        table,
        "by-resident",
        "gsi1_pk",
        "RESIDENT#res-0002",
        "DEVICE#",
        "gsi1_sk",
    )
    assert {i["gsi1_sk"]["S"] for i in devices} >= {
        "DEVICE#wearable-0002",
        "DEVICE#bedroom-0002",
    }
    family = query(client, table, None, "pk", f"USER#{FAMILY_SUB}", "RESIDENT#", "sk")
    assert sorted(i["sk"]["S"] for i in family) == [
        "RESIDENT#res-0002",
        "RESIDENT#res-0005",
    ]
    users = query(
        client, table, "by-resident", "gsi1_pk", "RESIDENT#res-0002", "USER#", "gsi1_sk"
    )
    assert {i["gsi1_sk"]["S"] for i in users} >= {
        f"USER#{FAMILY_SUB}",
        f"USER#{CAREGIVER_SUB}",
    }


def test_seed_local_twice_on_a_fresh_table(seed, local, scratch_table):
    # Exact counts: this table holds only the seed.
    assert seed.seed_local(local, table=scratch_table, now=NOW) == 85
    assert seed.seed_local(local, table=scratch_table, now=LATER) == 85
    check_criterion_8(local, scratch_table)
    assert len(query(local, scratch_table, "by-type", "entity_type", "DEVICE")) == 40
    assert len(query(local, scratch_table, "by-type", "entity_type", "RESIDENT")) == 20
    users = query(
        local,
        scratch_table,
        "by-resident",
        "gsi1_pk",
        "RESIDENT#res-0002",
        "USER#",
        "gsi1_sk",
    )
    assert len(users) == 2
    count = local.scan(TableName=scratch_table, Select="COUNT")["Count"]
    assert count == 85


def test_main_local_twice(seed, local):
    # The real table name, as `make seed-local` uses it. Other local data may exist,
    # so seeded IDs are checked rather than exact totals.
    for _ in range(2):
        out, err = io.StringIO(), io.StringIO()
        code = seed.main(["--local"], out=out, err=err)
        assert code == 0, err.getvalue()
        assert "registry: 85 items written" in out.getvalue()
    check_criterion_8(local, "silvercare-registry")
