"""The api-thresholds role's additions in infra/api.tf, read as text
(docs/features/device-alert-limits.md, criterion 5c), and how the by-resident
conditions match what the handler sends."""

from __future__ import annotations

import re
from pathlib import Path

import handler
import pytest

INFRA = Path(__file__).resolve().parents[3] / "infra"
API_TF = (INFRA / "api.tf").read_text(encoding="utf-8")


def block(text: str, header: str) -> str:
    start = text.index(header)
    return text[start : text.index("\n}\n", start)]


@pytest.fixture(scope="module")
def policy() -> dict[str, str]:
    document = block(API_TF, 'data "aws_iam_policy_document" "api_thresholds" {')
    parts = re.split(r"\n  statement \{", document)[1:]
    return {re.search(r'sid\s*=\s*"([^"]+)"', p).group(1): p for p in parts}


def values(statement: str, test: str, variable: str) -> list[str]:
    for cond in re.findall(r"condition \{(.*?)\n    \}", statement, re.DOTALL):
        if f'"{test}"' in cond and f'"{variable}"' in cond:
            listed = re.search(r"values\s*=\s*\[(.*?)\]", cond, re.DOTALL).group(1)
            return re.findall(r'"([^"]*)"', listed)
    raise LookupError(variable)


def test_only_wearable_shadow_update_among_iot_actions(policy):
    iot = [s for s in policy.values() if '"iot:' in s]
    (statement,) = iot
    assert re.search(r'actions\s*=\s*\["iot:UpdateThingShadow"\]', statement)
    assert re.search(r"resources\s*=\s*\[local\.wearable_things_arn\]", statement)


def test_by_resident_query_is_on_that_index_only(policy):
    statement = policy["RegistryByResidentIndex"]
    assert re.search(r'actions\s*=\s*\["dynamodb:Query"\]', statement)
    assert re.search(
        r'resources\s*=\s*\["\$\{module\.registry_table\.arn\}/index/by-resident"\]',
        statement,
    )


def test_handler_query_names_only_attributes_iam_allows(policy, monkeypatch):
    sent = []

    class Client:
        def query(self, **kwargs):
            sent.append(kwargs)
            return {"Items": []}

    monkeypatch.setattr(handler, "dynamodb_client", Client)
    handler.query_wearables("res-0001")

    (query,) = sent
    statement = policy["RegistryByResidentIndex"]
    allowed = values(statement, "ForAllValues:StringEquals", "dynamodb:Attributes")
    assert set(query["ExpressionAttributeNames"].values()) <= set(allowed)
    assert "relationship" not in allowed
    assert values(statement, "StringEquals", "dynamodb:Select") == [query["Select"]]


def test_function_gets_the_iot_data_endpoint():
    module = block(API_TF, 'module "api_thresholds" {')
    assert re.search(
        r"IOT_DATA_ENDPOINT\s*=\s*data\.aws_iot_endpoint\.data\.endpoint_address",
        module,
    )
