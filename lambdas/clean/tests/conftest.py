"""Shared setup for the clean Lambda tests (docs/features/clean-data.md).

Fake AWS credentials and env vars are set before anything imports boto3, so no test
can reach real AWS. Athena is a recording fake (moto can't run the SQL); S3 is moto.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "ap-southeast-1"
os.environ["GLUE_DATABASE"] = "silvercare_lake"
os.environ["ATHENA_WORKGROUP"] = "silvercare-clean"
os.environ["CLEAN_BUCKET"] = "silvercare-clean-test"
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-clean-test")

import boto3
import handler
import pytest
from helpers import FakeAthena
from moto import mock_aws


def _no_aws():
    raise AssertionError("no AWS client may be created here")


@pytest.fixture
def no_aws(monkeypatch):
    """Any attempt to reach Athena or S3 fails the test."""
    monkeypatch.setattr(handler, "athena_client", _no_aws)
    monkeypatch.setattr(handler, "s3_client", _no_aws)


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """Replaces the handler's sleep, so polling is instant and can be checked."""
    recorded: list[float] = []
    monkeypatch.setattr(handler, "time", SimpleNamespace(sleep=recorded.append))
    return recorded


@pytest.fixture
def athena(monkeypatch, sleeps) -> FakeAthena:
    fake = FakeAthena()
    monkeypatch.setattr(handler, "athena_client", lambda: fake)
    return fake


@pytest.fixture
def s3(monkeypatch, athena):
    """A moto clean bucket. The fake Athena then behaves like UNLOAD against it."""
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(
            Bucket=os.environ["CLEAN_BUCKET"],
            CreateBucketConfiguration={"LocationConstraint": "ap-southeast-1"},
        )
        monkeypatch.setattr(handler, "s3_client", lambda: client)
        athena.s3 = client
        yield client


@pytest.fixture
def logs():
    """A function returning the handler's log records so far, parsed from JSON."""
    buf = io.StringIO()
    stream_handler = handler.logger.registered_handler
    old = stream_handler.setStream(buf)

    def records() -> list[dict]:
        return [json.loads(line) for line in buf.getvalue().splitlines() if line]

    yield records
    stream_handler.setStream(old)
