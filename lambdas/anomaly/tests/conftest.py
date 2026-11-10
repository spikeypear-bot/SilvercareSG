"""Shared setup for the anomaly Lambda tests (docs/features/nightly-anomaly.md).

Fake AWS credentials and env vars are set before boto3 is imported. `aws` starts moto
with the history bucket, the ML bucket, and silvercare-results (pk/sk, as
infra/dynamodb.tf). SageMaker and Lambda (no moto extras in the dev image) are small
recording fakes of just the calls the handler makes. Data is synthetic.
"""

from __future__ import annotations

import io
import json
import os
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
os.environ["HISTORY_BUCKET"] = "silvercare-history-test"
os.environ["ML_BUCKET"] = "silvercare-ml-test"
os.environ["RESULTS_TABLE"] = "silvercare-results"
os.environ["COLLECT_FUNCTION"] = "silvercare-anomaly-collect"
os.environ["RCF_IMAGE"] = "000000000000.dkr.ecr.ap-southeast-1.amazonaws.com/rcf:1"
os.environ["SAGEMAKER_ROLE_ARN"] = "arn:aws:iam::000000000000:role/sagemaker-test"
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-anomaly-test")

import boto3
import handler
import pytest
from boto3.dynamodb.types import TypeDeserializer
from moto import mock_aws

REGION = "ap-southeast-1"
HISTORY = os.environ["HISTORY_BUCKET"]
ML = os.environ["ML_BUCKET"]
TABLE = os.environ["RESULTS_TABLE"]

_deserializer = TypeDeserializer()


class Recording:
    """Records (operation, params); `fail[op]` makes that operation raise."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, Exception] = {}

    def __getattr__(self, name: str):
        def call(**kwargs: Any) -> dict:
            self.calls.append((name, kwargs))
            if name in self.fail:
                raise self.fail[name]
            return {}

        return call

    def params(self, op: str) -> list[dict]:
        return [p for o, p in self.calls if o == op]


class Aws:
    def __init__(self, s3, ddb, sagemaker: Recording, lam: Recording) -> None:
        self.s3 = s3
        self.ddb = ddb
        self.sagemaker = sagemaker
        self.lam = lam

    def put(self, bucket: str, key: str, body: bytes | str) -> None:
        self.s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body if isinstance(body, bytes) else body.encode(),
        )

    def get(self, bucket: str, key: str) -> bytes:
        return self.s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    def keys(self, bucket: str, prefix: str = "") -> list[str]:
        out = self.s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        return sorted(o["Key"] for o in out.get("Contents", []))

    def put_results_item(self, resident_id: str, date: str) -> None:
        self.ddb.put_item(
            TableName=TABLE,
            Item={
                "pk": {"S": f"RESIDENT#{resident_id}"},
                "sk": {"S": f"NIGHT#{date}"},
                "date": {"S": date},
            },
        )

    def results_item(self, resident_id: str, date: str) -> dict | None:
        item = self.ddb.get_item(
            TableName=TABLE,
            Key={"pk": {"S": f"RESIDENT#{resident_id}"}, "sk": {"S": f"NIGHT#{date}"}},
        ).get("Item")
        if item is None:
            return None
        return {k: _deserializer.deserialize(v) for k, v in item.items()}


@pytest.fixture
def aws(monkeypatch):
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        for bucket in (HISTORY, ML):
            s3.create_bucket(
                Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": REGION}
            )
        ddb = boto3.client("dynamodb", region_name=REGION)
        ddb.create_table(
            TableName=TABLE,
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            ProvisionedThroughput={"ReadCapacityUnits": 1, "WriteCapacityUnits": 1},
        )
        sagemaker, lam = Recording(), Recording()
        handler_s3 = boto3.client("s3", region_name=REGION)
        handler_ddb = boto3.client("dynamodb", region_name=REGION)
        monkeypatch.setattr(handler, "s3_client", lambda: handler_s3)
        monkeypatch.setattr(handler, "dynamodb_client", lambda: handler_ddb)
        monkeypatch.setattr(handler, "sagemaker_client", lambda: sagemaker)
        monkeypatch.setattr(handler, "lambda_client", lambda: lam)
        monkeypatch.setattr(handler, "new_run", lambda: "0a1b2c3d")
        yield Aws(s3, ddb, sagemaker, lam)


@pytest.fixture
def logs():
    """Parsed Powertools log records without `timestamp` and `location`."""
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
