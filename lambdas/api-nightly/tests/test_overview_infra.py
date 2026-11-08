"""GET /nightly/overview in infra/api.tf, read as text (nightly-overview.md criteria 1,
11, 14-16), and how its IAM conditions match what the handler sends.
"""

from __future__ import annotations

import re
from pathlib import Path

import handler
import pytest

INFRA = Path(__file__).resolve().parents[3] / "infra"
API_TF = INFRA / "api.tf"


def block(text: str, header: str) -> str:
    start = text.index(header)
    return text[start : text.index("\n}\n", start)]


def strings(text: str) -> list[str]:
    return re.findall(r'"([^"]*)"', text)


@pytest.fixture(scope="module")
def api_tf() -> str:
    return API_TF.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def statements(api_tf) -> list[str]:
    document = block(api_tf, 'data "aws_iam_policy_document" "api_nightly_overview" {')
    return re.split(r"\n  statement \{", document)[1:]


def _actions(text: str) -> list[str]:
    return strings(re.search(r"actions\s*=\s*\[(.*?)\]", text, re.DOTALL).group(1))


def _conditions(text: str) -> list[tuple[str, str, list[str]]]:
    out = []
    for cond in re.findall(r"condition \{(.*?)\n    \}", text, re.DOTALL):
        test = re.search(r'test\s*=\s*"([^"]+)"', cond).group(1)
        variable = re.search(r'variable\s*=\s*"([^"]+)"', cond).group(1)
        values = strings(re.search(r"values\s*=\s*\[(.*?)\]", cond, re.DOTALL).group(1))
        out.append((test, variable, values))
    return out


class Client:
    def __init__(self):
        self.queries: list = []
        self.batches: list = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"Items": []}

    def batch_get_item(self, **kwargs):
        self.batches.append(kwargs)
        return {"Responses": {}}


# ---- criterion 16: IAM ----


def test_policy_has_exactly_two_allow_statements(statements):
    assert len(statements) == 2
    for text in statements:
        assert re.search(r'effect\s*=\s*"Allow"', text)
        assert "Deny" not in text


def test_registry_statement_is_query_on_the_by_type_index_only(statements):
    (text,) = [s for s in statements if "registry_table" in s]
    assert _actions(text) == ["dynamodb:Query"]
    resources = re.search(r"resources\s*=\s*\[(.*?)\]", text, re.DOTALL).group(1)
    assert resources.strip() == '"${module.registry_table.arn}/index/by-type"'


def test_registry_statement_limits_attributes_and_select(statements):
    (text,) = [s for s in statements if "registry_table" in s]
    conditions = _conditions(text)
    assert sorted(conditions) == sorted(
        [
            (
                "ForAllValues:StringEquals",
                "dynamodb:Attributes",
                ["pk", "sk", "entity_type"],
            ),
            ("Null", "dynamodb:Attributes", ["false"]),
            ("StringEquals", "dynamodb:Select", ["SPECIFIC_ATTRIBUTES"]),
        ]
    )


def test_results_statement_is_batch_get_only_on_the_table(statements):
    (text,) = [s for s in statements if "results_table" in s]
    assert _actions(text) == ["dynamodb:BatchGetItem"]
    assert re.search(r"resources\s*=\s*\[module\.results_table\.arn\]", text)
    assert "/index/" not in text


def test_no_other_table_or_write_action(statements):
    text = "\n".join(statements)
    for table in ("alerts_table", "latest_table", "users_table"):
        assert table not in text
    actions = [a for s in statements for a in _actions(s)]
    assert sorted(actions) == ["dynamodb:BatchGetItem", "dynamodb:Query"]


def test_handlers_registry_query_fits_the_attribute_conditions(monkeypatch):
    client = Client()
    monkeypatch.setattr(handler, "dynamodb_client", lambda: client)
    handler.query_resident_pks()
    (request,) = client.queries
    assert request["Select"] == "SPECIFIC_ATTRIBUTES"
    assert request["IndexName"] == "by-type"
    # Every attribute the request names, so dynamodb:Attributes is a subset.
    assert set(request["ExpressionAttributeNames"].values()) <= {"pk", "entity_type"}
    assert request["ProjectionExpression"]


def test_handler_calls_only_batch_get_on_results(monkeypatch):
    client = Client()
    monkeypatch.setattr(handler, "dynamodb_client", lambda: client)
    handler.batch_get_nights(["res-0003"], "2026-11-18")
    assert client.queries == []
    (request,) = client.batches
    assert list(request["RequestItems"]) == [handler.os.environ["RESULTS_TABLE"]]


# ---- criteria 1, 14, 15: function ----


@pytest.fixture(scope="module")
def module_text(api_tf) -> str:
    return block(api_tf, 'module "api_nightly_overview" {')


def test_function_name(api_tf, module_text):
    assert re.search(
        r'api_overview_function_name\s*=\s*"silvercare-api-nightly-overview"', api_tf
    )
    assert re.search(
        r"function_name\s*=\s*local.api_overview_function_name", module_text
    )


def test_function_is_the_overview_entry_point_of_the_shared_zip(module_text):
    assert re.search(r'handler\s*=\s*"handler.overview_handler"', module_text)
    assert "data.archive_file.api_nightly.output_path" in module_text
    assert "data.archive_file.api_nightly.output_base64sha256" in module_text
    assert callable(handler.overview_handler)


def test_function_settings(module_text):
    assert re.search(r"memory_size\s*=\s*128\b", module_text)
    assert re.search(r"timeout\s*=\s*10\b", module_text)
    assert re.search(r"log_retention_days\s*=\s*30\b", module_text)
    assert "local.powertools_layer_arn" in module_text
    assert re.search(r"REGISTRY_TABLE\s*=\s*module.registry_table.name", module_text)
    assert re.search(r"RESULTS_TABLE\s*=\s*module.results_table.name", module_text)
    assert re.search(
        r"policy_json\s*=\s*data.aws_iam_policy_document.api_nightly_overview.json",
        module_text,
    )


def test_function_has_its_own_role_not_the_nightly_ones(api_tf, module_text):
    nightly = block(api_tf, 'module "api_nightly" {')
    assert "api_nightly_overview" not in nightly
    assert "aws_iam_policy_document.api_nightly.json" not in module_text


def test_no_alarm_or_failure_destination(api_tf, module_text):
    assert "on_failure_destination" not in module_text
    alarms = re.findall(
        r'resource "aws_cloudwatch_metric_alarm" "[^"]+" \{.*?\n\}', api_tf, re.DOTALL
    )
    assert not [a for a in alarms if "api_nightly_overview" in a]


# ---- criterion 1: route ----


def test_route_is_behind_the_cognito_jwt_authorizer(api_tf):
    text = block(api_tf, 'resource "aws_apigatewayv2_route" "nightly_overview" {')
    assert re.search(r'route_key\s*=\s*"GET /nightly/overview"', text)
    assert re.search(r'authorization_type\s*=\s*"JWT"', text)
    assert re.search(
        r"authorizer_id\s*=\s*aws_apigatewayv2_authorizer.cognito.id", text
    )
    assert "aws_apigatewayv2_integration.api_nightly_overview.id" in text


def test_integration_uses_payload_2(api_tf):
    text = block(
        api_tf, 'resource "aws_apigatewayv2_integration" "api_nightly_overview" {'
    )
    assert re.search(r'payload_format_version\s*=\s*"2.0"', text)
    assert re.search(r'integration_type\s*=\s*"AWS_PROXY"', text)
    assert "module.api_nightly_overview.function_arn" in text


def test_only_this_route_may_invoke_the_function(api_tf):
    text = block(api_tf, 'resource "aws_lambda_permission" "api_nightly_overview" {')
    assert re.search(r'principal\s*=\s*"apigateway.amazonaws.com"', text)
    assert "module.api_nightly_overview.function_name" in text
    assert '/*/GET/nightly/overview"' in text


# ---- criterion 11: throttling ----


def test_route_has_its_own_throttling(api_tf):
    stage = block(api_tf, 'resource "aws_apigatewayv2_stage" "default" {')
    settings = [
        m
        for m in re.findall(r"route_settings \{(.*?)\n  \}", stage, re.DOTALL)
        if "nightly_overview" in m
    ]
    assert len(settings) == 1
    (text,) = settings
    assert re.search(
        r"route_key\s*=\s*aws_apigatewayv2_route.nightly_overview.route_key", text
    )
    assert re.search(r"throttling_rate_limit\s*=\s*0\.1\b", text)
    assert re.search(r"throttling_burst_limit\s*=\s*2\b", text)
