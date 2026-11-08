"""The api-devices role's shadow access in infra/api.tf, read as text
(docs/features/device-alert-limits.md, criterion 15)."""

from __future__ import annotations

import re
from pathlib import Path

INFRA = Path(__file__).resolve().parents[3] / "infra"
API_TF = (INFRA / "api.tf").read_text(encoding="utf-8")


def block(text: str, header: str) -> str:
    start = text.index(header)
    return text[start : text.index("\n}\n", start)]


def iot_statements(document: str) -> list[str]:
    parts = re.split(r"\n  statement \{", document)[1:]
    return [p for p in parts if '"iot:' in p]


def test_role_reads_wearable_shadows_only():
    document = block(API_TF, 'data "aws_iam_policy_document" "api_devices" {')
    (statement,) = iot_statements(document)
    assert re.search(r'actions\s*=\s*\["iot:GetThingShadow"\]', statement)
    assert re.search(r"resources\s*=\s*\[local\.wearable_things_arn\]", statement)
    assert re.search(r'effect\s*=\s*"Allow"', statement)


def test_function_gets_the_iot_data_endpoint():
    module = block(API_TF, 'module "api_devices" {')
    assert re.search(
        r"IOT_DATA_ENDPOINT\s*=\s*data\.aws_iot_endpoint\.data\.endpoint_address",
        module,
    )
