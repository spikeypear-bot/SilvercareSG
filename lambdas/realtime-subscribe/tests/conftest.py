"""Shared setup for the realtime-subscribe tests: fake AWS settings before boto3 is
imported, so no test can reach real AWS; `aws` starts moto with a registry table and
counts the handler's DynamoDB calls."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "ap-southeast-1"
os.environ["AWS_SHARED_CREDENTIALS_FILE"] = "/dev/null"
os.environ["AWS_CONFIG_FILE"] = "/dev/null"
os.environ.setdefault("REGISTRY_TABLE", "silvercare-registry")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "silvercare-realtime-subscribe-test")

import boto3
import handler
import pytest
from moto import mock_aws

REGION = "ap-southeast-1"
CAREGIVER_SUB = "00000000-0000-4000-8000-00000000c001"
STAFF_SUB = "00000000-0000-4000-8000-00000000d001"


class Counting:
    def __init__(self, client: Any) -> None:
        self._client = client
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._client, name)

        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            return method(*args, **kwargs)

        return call


@pytest.fixture
def aws(monkeypatch):
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=REGION)
        ddb.create_table(
            TableName=os.environ["REGISTRY_TABLE"],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.put_item(
            TableName=os.environ["REGISTRY_TABLE"],
            Item={
                "pk": {"S": f"USER#{CAREGIVER_SUB}"},
                "sk": {"S": "RESIDENT#res-0001"},
                "linked_by": {"S": "seed"},
            },
        )
        counting = Counting(ddb)
        monkeypatch.setattr(handler, "dynamodb_client", lambda: counting)
        yield counting


def subscribe_event(path: Any, groups: Any = ("caregiver",), sub: Any = CAREGIVER_SUB):
    """A synthetic AppSync Events onSubscribe event (Cognito identity)."""
    return {
        "identity": {
            "sub": sub,
            "groups": list(groups) if isinstance(groups, tuple) else groups,
            "username": sub,
            "issuer": "https://cognito-idp.ap-southeast-1.amazonaws.com/test",
            "claims": {"token_use": "id"},
            "sourceIp": ["192.0.2.1"],
            "defaultAuthStrategy": "ALLOW",
        },
        "info": {
            "channel": {"path": path, "segments": str(path).strip("/").split("/")},
            "channelNamespace": {"name": str(path).strip("/").split("/")[0]},
            "operation": "SUBSCRIBE",
        },
        "request": {"headers": {"authorization": "synthetic-token"}},
        "events": [],
    }
