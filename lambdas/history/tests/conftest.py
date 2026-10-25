"""Shared setup for the history Lambda tests (docs/features/ingest-history.md).

Fake AWS credentials and env vars are set before anything imports boto3, so no test
can reach real AWS. `history_bucket` starts moto and creates the history bucket.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

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
os.environ.setdefault("HISTORY_BUCKET", "silvercare-history-000000000000")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-history-test")

import boto3
import pytest

BUCKET = os.environ["HISTORY_BUCKET"]


@pytest.fixture
def history_bucket():
    """moto S3 with the history bucket. Yields an S3 client for checks."""
    import handler
    from moto import mock_aws

    with mock_aws():
        handler.s3_client.cache_clear()
        client = boto3.client("s3", region_name="ap-southeast-1")
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "ap-southeast-1"},
        )
        yield client
    handler.s3_client.cache_clear()


@pytest.fixture
def logs():
    """Capture the handler's Powertools log lines. Call it to get parsed records."""
    import handler

    buf = io.StringIO()
    stream_handler = handler.logger.registered_handler
    old = stream_handler.setStream(buf)

    def records() -> list[dict]:
        return [json.loads(line) for line in buf.getvalue().splitlines() if line]

    records.raw = buf  # type: ignore[attr-defined]
    yield records
    stream_handler.setStream(old)
