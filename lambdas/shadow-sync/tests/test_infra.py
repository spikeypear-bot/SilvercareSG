"""infra/shadow_sync.tf, infra/iot.tf, and the device policies in
infra/modules/iot-devices/main.tf, read as text (docs/features/device-alert-limits.md,
criteria 5, 6, 10, 12), and how the IAM conditions match what the handler sends."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[3] / "infra"


def read(name: str) -> str:
    return (INFRA / name).read_text(encoding="utf-8")


def block(text: str, header: str) -> str:
    start = text.index(header)
    return text[start : text.index("\n}\n", start)]


def strings(text: str) -> list[str]:
    return re.findall(r'"([^"]*)"', text)


def statements(document: str) -> dict[str, str]:
    parts = re.split(r"\n  statement \{", document)[1:]
    return {re.search(r'sid\s*=\s*"([^"]+)"', p).group(1): p for p in parts}


def actions(statement: str) -> list[str]:
    return strings(re.search(r"actions\s*=\s*\[(.*?)\]", statement, re.DOTALL).group(1))


def resources(statement: str) -> str:
    return re.search(r"resources\s*=\s*\[(.*?)\]", statement, re.DOTALL).group(1)


def condition_values(statement: str, variable: str) -> list[list[str]]:
    out = []
    for cond in re.findall(r"condition \{(.*?)\n    \}", statement, re.DOTALL):
        if f'"{variable}"' in cond and '"Null"' not in cond:
            out.append(
                strings(re.search(r"values\s*=\s*\[(.*?)\]", cond, re.DOTALL).group(1))
            )
    return out


@pytest.fixture(scope="module")
def sync_tf() -> str:
    return read("shadow_sync.tf")


@pytest.fixture(scope="module")
def policy(sync_tf) -> dict[str, str]:
    return statements(block(sync_tf, 'data "aws_iam_policy_document" "shadow_sync" {'))


# ---- the shadow-sync role (criterion 12) ----


def test_wearable_things_arn_is_wearable_things_only():
    text = read("iot.tf")
    (arn,) = re.findall(r'wearable_things_arn\s*=\s*"([^"]+)"', text)
    assert arn.endswith(":thing/wearable-*")
    assert arn.count("*") == 1


def test_role_has_exactly_three_allow_statements(policy):
    assert set(policy) == {"RegistryByTypeIndex", "ThresholdsRead", "WearableShadows"}
    for text in policy.values():
        assert re.search(r'effect\s*=\s*"Allow"', text)


def test_shadow_actions_are_get_and_update_on_wearables_only(policy):
    text = policy["WearableShadows"]
    assert actions(text) == ["iot:GetThingShadow", "iot:UpdateThingShadow"]
    assert resources(text).strip() == "local.wearable_things_arn"


def test_no_delete_or_wildcard_action(sync_tf):
    document = block(sync_tf, 'data "aws_iam_policy_document" "shadow_sync" {')
    for text in statements(document).values():
        for action in actions(text):
            assert "*" not in action
            assert "Delete" not in action
            assert not action.startswith("dynamodb:") or action in {
                "dynamodb:Query",
                "dynamodb:BatchGetItem",
            }


def test_query_is_on_the_by_type_index_only(policy):
    text = policy["RegistryByTypeIndex"]
    assert actions(text) == ["dynamodb:Query"]
    assert resources(text).strip() == '"${module.registry_table.arn}/index/by-type"'


def test_batch_get_is_limited_to_thresholds_partitions(policy):
    text = policy["ThresholdsRead"]
    assert actions(text) == ["dynamodb:BatchGetItem"]
    assert resources(text).strip() == "module.registry_table.arn"
    assert condition_values(text, "dynamodb:LeadingKeys") == [["THRESHOLDS#*"]]
    assert re.search(r'test\s*=\s*"ForAllValues:StringLike"', text)


def test_handler_requests_name_only_attributes_iam_allows(policy, monkeypatch):
    import handler

    sent: dict[str, list] = {"query": [], "batch": []}

    class Client:
        def query(self, **kwargs):
            sent["query"].append(kwargs)
            return {"Items": []}

        def batch_get_item(self, **kwargs):
            sent["batch"].append(kwargs)
            return {"Responses": {}}

    monkeypatch.setattr(handler, "dynamodb_client", Client)
    handler.read_devices()
    handler.read_thresholds(["res-0001"])

    (allowed_query,) = condition_values(
        policy["RegistryByTypeIndex"], "dynamodb:Attributes"
    )
    (query,) = sent["query"]
    assert set(query["ExpressionAttributeNames"].values()) <= set(allowed_query)
    assert condition_values(policy["RegistryByTypeIndex"], "dynamodb:Select") == [
        [query["Select"]]
    ]
    (allowed_batch,) = condition_values(policy["ThresholdsRead"], "dynamodb:Attributes")
    (batch,) = sent["batch"]
    for request in batch["RequestItems"].values():
        assert set(request["ExpressionAttributeNames"].values()) <= set(allowed_batch)
        for key in request["Keys"]:
            assert key["pk"]["S"].startswith("THRESHOLDS#")


def test_endpoint_comes_from_the_iot_data_ats_endpoint(sync_tf):
    module = block(sync_tf, 'module "shadow_sync" {')
    assert re.search(
        r"IOT_DATA_ENDPOINT\s*=\s*data\.aws_iot_endpoint\.data\.endpoint_address",
        module,
    )
    assert re.search(r'endpoint_type\s*=\s*"iot:Data-ATS"', read("iot.tf"))


# ---- schedule and alarm (criteria 6, 10) ----


def test_runs_every_hour(sync_tf):
    rule = block(sync_tf, 'resource "aws_cloudwatch_event_rule" "shadow_sync" {')
    assert re.search(r'schedule_expression\s*=\s*"rate\(1 hour\)"', rule)


def test_errors_alarm_fires_after_two_failed_hourly_runs(sync_tf):
    alarm = block(
        sync_tf, 'resource "aws_cloudwatch_metric_alarm" "shadow_sync_errors" {'
    )
    assert '"silvercare-shadow-sync-errors"' in alarm
    assert re.search(r'metric_name\s*=\s*"Errors"', alarm)
    assert re.search(r"period\s*=\s*3600", alarm)
    assert re.search(r"evaluation_periods\s*=\s*2", alarm)
    assert re.search(r"datapoints_to_alarm\s*=\s*2", alarm)
    assert re.search(r'treat_missing_data\s*=\s*"notBreaching"', alarm)
    assert re.search(r"alarm_actions\s*=\s*\[aws_sns_topic\.ops\.arn\]", alarm)
    assert re.search(r"ok_actions\s*=\s*\[aws_sns_topic\.ops\.arn\]", alarm)


# ---- device policies (criterion 5) ----


@pytest.fixture(scope="module")
def device_policy() -> tuple[str, dict[str, str]]:
    text = read("modules/iot-devices/main.tf")
    return text, statements(block(text, 'data "aws_iam_policy_document" "device" {'))


def _list_local(text: str, name: str) -> list[str]:
    return strings(re.search(rf"{name}\s*=\s*\[(.*?)\]", text, re.DOTALL).group(1))


def test_own_shadow_uses_the_connection_thing_name(device_policy):
    text, _ = device_policy
    (own,) = re.findall(r'own_shadow\s*=\s*"([^"]+)"', text)
    assert own == "$aws/things/$${iot:Connection.Thing.ThingName}/shadow"


def test_shadow_topics_are_exactly_the_classic_ones(device_policy):
    text, _ = device_policy
    assert _list_local(text, "shadow_request_topics") == ["get", "update"]
    # One wildcard per request topic's replies (accepted, rejected, delta, documents):
    # listing them took the policies past AWS IoT's 2048-character limit.
    assert _list_local(text, "shadow_response_topics") == ["get/*", "update/*"]


@pytest.mark.parametrize(
    ("sid", "action", "kind", "topics"),
    [
        ("PublishOwnShadow", "iot:Publish", "topic", "shadow_request_topics"),
        (
            "SubscribeOwnShadow",
            "iot:Subscribe",
            "topicfilter",
            "shadow_response_topics",
        ),
        ("ReceiveOwnShadow", "iot:Receive", "topic", "shadow_response_topics"),
    ],
)
def test_shadow_statement_allows_one_action_on_own_shadow_topics(
    device_policy, sid, action, kind, topics
):
    _, policy = device_policy
    text = policy[sid]
    assert actions(text) == [action]
    assert re.search(r'effect\s*=\s*"Allow"', text)
    assert re.sub(r"\s+", " ", resources(text)).strip() == (
        f'for t in local.{topics} : "${{local.arn_prefix}}:{kind}/'
        '${local.own_shadow}/${t}"'
    )


def test_device_policy_has_no_delete_named_shadow_or_wildcard(device_policy):
    text, policy = device_policy
    document = block(text, 'data "aws_iam_policy_document" "device" {')
    assert "delete" not in document.lower()
    assert "/shadow/name/" not in document
    assert "*" not in document
    topics = _list_local(text, "shadow_request_topics") + _list_local(
        text, "shadow_response_topics"
    )
    for topic in topics:
        assert "#" not in topic and "+" not in topic
    # Requests stay exact (no delete); "*" only as the last level of a reply topic.
    for topic in _list_local(text, "shadow_request_topics"):
        assert "*" not in topic
    for topic in _list_local(text, "shadow_response_topics"):
        assert topic.count("*") == 1 and topic.endswith("/*")
    for statement in policy.values():
        for action in actions(statement):
            assert action in {
                "iot:Connect",
                "iot:Publish",
                "iot:Subscribe",
                "iot:Receive",
            }


def test_both_device_types_get_the_shadow_statements(device_policy):
    text, _ = device_policy
    # One policy document per device type (for_each), so both get every statement.
    document = block(text, 'data "aws_iam_policy_document" "device" {')
    assert "for_each = local.device_types" in document
    assert not re.search(r"dynamic\s+\"statement\"", document)


def _device_messages(text: str) -> dict[str, list[str]]:
    types = re.search(r"device_types\s*=\s*\{(.*?)\n  \}", text, re.DOTALL).group(1)
    return {
        name: strings(body)
        for name, body in re.findall(
            r"(\w+)\s*=\s*\{[^{}]*?messages\s*=\s*\[(.*?)\]", types, re.DOTALL
        )
    }


def test_device_policies_fit_aws_iot_size_limit():
    # AWS IoT rejects a policy over 2048 characters, excluding whitespace (the
    # 2026-11-09 deploy failed on this). Rebuild each policy as the data source renders
    # it, with the real ARN prefix length, and keep a margin.
    text = read("modules/iot-devices/main.tf")
    prefix = "arn:aws:iot:ap-southeast-1:123456789012"
    thing = "${iot:Connection.Thing.ThingName}"
    own = f"$aws/things/{thing}/shadow"
    requests = _list_local(text, "shadow_request_topics")
    replies = _list_local(text, "shadow_response_topics")
    messages = _device_messages(text)
    assert set(messages) == {"wearable", "bedroom"}

    def statement(sid, action, res, condition=None):
        out = {"Sid": sid, "Effect": "Allow", "Action": action}
        out["Resource"] = res if len(res) > 1 else res[0]
        if condition:
            out["Condition"] = condition
        return out

    for device_type, msgs in messages.items():
        document = {
            "Version": "2012-10-17",
            "Statement": [
                statement(
                    "ConnectAsOwnThing",
                    "iot:Connect",
                    [f"{prefix}:client/{thing}"],
                    {"Bool": {"iot:Connection.Thing.IsAttached": "true"}},
                ),
                statement(
                    "PublishOwnMessages",
                    "iot:Publish",
                    [f"{prefix}:topic/silvercare/v1/{thing}/{m}" for m in msgs],
                ),
                statement(
                    "PublishOwnShadow",
                    "iot:Publish",
                    [f"{prefix}:topic/{own}/{t}" for t in requests],
                ),
                statement(
                    "SubscribeOwnShadow",
                    "iot:Subscribe",
                    [f"{prefix}:topicfilter/{own}/{t}" for t in replies],
                ),
                statement(
                    "ReceiveOwnShadow",
                    "iot:Receive",
                    [f"{prefix}:topic/{own}/{t}" for t in replies],
                ),
            ],
        }
        size = len(re.sub(r"\s", "", json.dumps(document)))
        assert size <= 1900, f"{device_type} policy is {size} characters (limit 2048)"
