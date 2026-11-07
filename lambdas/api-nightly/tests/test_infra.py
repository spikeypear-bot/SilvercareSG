"""The route, function, and IAM in infra/api.tf, read as text (nightly-results.md
criteria 15, 19, 22, 23), and how the policy matches what the handler sends.
"""

from __future__ import annotations

import re
import sys
from fnmatch import fnmatchcase
from pathlib import Path

import handler
import pytest

INFRA = Path(__file__).resolve().parents[3] / "infra"
API_TF = INFRA / "api.tf"
SRC = Path(handler.__file__).resolve().parent


def block(text: str, header: str) -> str:
    start = text.index(header)
    return text[start : text.index("\n}\n", start)]


def strings(text: str) -> list[str]:
    return re.findall(r'"([^"]*)"', text)


@pytest.fixture(scope="module")
def api_tf() -> str:
    return API_TF.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def document(api_tf) -> str:
    return block(api_tf, 'data "aws_iam_policy_document" "api_nightly" {')


# ---- criterion 23: IAM ----


def test_policy_is_one_query_statement_on_the_results_table(document):
    statements = re.split(r"\n  statement \{", document)[1:]
    assert len(statements) == 1
    (text,) = statements
    assert re.search(r'effect\s*=\s*"Allow"', text)
    granted = strings(re.search(r"actions\s*=\s*\[(.*?)\]", text, re.DOTALL).group(1))
    assert granted == ["dynamodb:Query"]
    assert re.search(r"resources\s*=\s*\[module\.results_table\.arn\]", text)
    assert "/index/" not in text


def test_leading_keys_condition_fits_the_handlers_query(document, monkeypatch):
    values = strings(re.search(r"values\s*=\s*\[(.*?)\]", document, re.DOTALL).group(1))
    assert re.search(r'variable\s*=\s*"dynamodb:LeadingKeys"', document)

    class Client:
        def __init__(self):
            self.requests = []

        def query(self, **kwargs):
            self.requests.append(kwargs)
            return {"Items": []}

    client = Client()
    monkeypatch.setattr(handler, "dynamodb_client", lambda: client)
    handler.query_nights("res-0003")
    (request,) = client.requests
    pk = request["ExpressionAttributeValues"][":pk"]["S"]
    assert any(fnmatchcase(pk, v) for v in values)


# ---- criteria 15, 22: function ----


def test_function_settings(api_tf):
    text = block(api_tf, 'module "api_nightly" {')
    assert re.search(r'handler\s*=\s*"handler.lambda_handler"', text)
    assert re.search(r"memory_size\s*=\s*128\b", text)
    assert re.search(r"log_retention_days\s*=\s*30\b", text)
    assert "local.powertools_layer_arn" in text
    assert re.search(r"RESULTS_TABLE\s*=\s*module.results_table.name", text)
    assert 'api_nightly_function_name       = "silvercare-api-nightly"' in api_tf


def test_zip_is_built_from_the_src_folder(api_tf):
    text = block(api_tf, 'data "archive_file" "api_nightly" {')
    assert "/../lambdas/api-nightly/src" in text


def test_src_imports_only_stdlib_boto3_and_powertools():
    allowed = {"boto3", "botocore", "aws_lambda_powertools", "__future__"}
    for path in SRC.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for top in re.findall(
            r"^(?:from|import)\s+([A-Za-z_]\w*)", source, re.MULTILINE
        ):
            assert (
                top in sys.stdlib_module_names
                or top in allowed
                or (SRC / f"{top}.py").exists()
            ), top


# ---- criterion 15: route ----


def test_route_is_behind_the_cognito_jwt_authorizer(api_tf):
    text = block(api_tf, 'resource "aws_apigatewayv2_route" "get_nightly" {')
    assert re.search(r'route_key\s*=\s*"GET /residents/\{resident_id\}/nightly"', text)
    assert re.search(r'authorization_type\s*=\s*"JWT"', text)
    assert re.search(
        r"authorizer_id\s*=\s*aws_apigatewayv2_authorizer.cognito.id", text
    )
    assert "aws_apigatewayv2_integration.api_nightly.id" in text


def test_integration_uses_payload_2(api_tf):
    text = block(api_tf, 'resource "aws_apigatewayv2_integration" "api_nightly" {')
    assert re.search(r'payload_format_version\s*=\s*"2.0"', text)
    assert re.search(r'integration_type\s*=\s*"AWS_PROXY"', text)
    assert "module.api_nightly.function_arn" in text


def test_only_this_route_may_invoke_the_function(api_tf):
    text = block(api_tf, 'resource "aws_lambda_permission" "api_nightly" {')
    assert re.search(r'principal\s*=\s*"apigateway.amazonaws.com"', text)
    assert "/*/GET/residents/*/nightly" in text


# ---- criterion 19: throttling ----


def test_route_has_its_own_throttling(api_tf):
    stage = block(api_tf, 'resource "aws_apigatewayv2_stage" "default" {')
    settings = [
        m
        for m in re.findall(r"route_settings \{(.*?)\n  \}", stage, re.DOTALL)
        if "get_nightly" in m
    ]
    assert len(settings) == 1
    (text,) = settings
    assert re.search(
        r"route_key\s*=\s*aws_apigatewayv2_route.get_nightly.route_key", text
    )
    assert re.search(r"throttling_rate_limit\s*=\s*0\.1\b", text)
    assert re.search(r"throttling_burst_limit\s*=\s*2\b", text)
