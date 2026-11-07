"""The summaries role's registry access in infra/summaries.tf, read as text, and how it
matches what the handler sends (docs/features/curated-layer.md, criteria 5, 17, 22).

These guard the review fix that limits by-type reads to `pk` and `resident_id`: if the
handler stopped sending `Select = SPECIFIC_ATTRIBUTES` or projected another attribute,
or the policy lost a condition, a test here fails.
"""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from pathlib import Path

import handler
import pytest

INFRA = Path(__file__).resolve().parents[3] / "infra"
SUMMARIES_TF = INFRA / "summaries.tf"
HISTORY_TF = INFRA / "history.tf"


def policy_document(text: str, name: str) -> str:
    start = text.index(f'data "aws_iam_policy_document" "{name}" {{')
    end = text.index("\n}\n", start)
    return text[start:end]


def statement(document: str, sid: str) -> str:
    """The text of one statement block, found by its sid."""
    blocks = re.split(r"\n  statement \{", document)[1:]
    matches = [b for b in blocks if re.search(rf'sid\s*=\s*"{re.escape(sid)}"', b)]
    assert len(matches) == 1, f"statement {sid} not found exactly once"
    return matches[0]


def strings(text: str) -> list[str]:
    return re.findall(r'"([^"]*)"', text)


def conditions(block: str) -> list[dict]:
    """Each condition block as {test, variable, values, raw}: `values` are the quoted
    strings, `raw` the list's text (for references like module.x.role_arn)."""
    out = []
    for body in re.findall(r"condition \{(.*?)\n\s*\}", block, re.DOTALL):
        test = re.search(r'test\s*=\s*"([^"]+)"', body).group(1)
        variable = re.search(r'variable\s*=\s*"([^"]+)"', body).group(1)
        raw = re.search(r"values\s*=\s*\[(.*?)\]", body, re.DOTALL).group(1)
        out.append(
            {"test": test, "variable": variable, "values": strings(raw), "raw": raw}
        )
    return out


def condition(block: str, variable: str) -> dict:
    (found,) = [c for c in conditions(block) if c["variable"] == variable]
    return found


def actions(block: str) -> list[str]:
    match = re.search(r"actions\s*=\s*\[(.*?)\]", block, re.DOTALL)
    return strings(match.group(1))


@pytest.fixture(scope="module")
def document() -> str:
    return policy_document(SUMMARIES_TF.read_text(encoding="utf-8"), "summaries")


# ---- by-type Query (criteria 6, 22) ----


def test_by_type_statement_allows_only_query_on_the_index(document):
    block = statement(document, "RegistryByTypeIndex")
    assert re.search(r'effect\s*=\s*"Allow"', block)
    assert actions(block) == ["dynamodb:Query"]
    assert '"${module.registry_table.arn}/index/by-type"' in block


def test_by_type_statement_limits_the_attributes(document):
    block = statement(document, "RegistryByTypeIndex")
    attributes = condition(block, "dynamodb:Attributes")
    assert attributes["test"] == "ForAllValues:StringEquals"
    assert set(attributes["values"]) == {"pk", "sk", "entity_type", "resident_id"}


def test_by_type_statement_requires_specific_attributes(document):
    block = statement(document, "RegistryByTypeIndex")
    select = condition(block, "dynamodb:Select")
    assert select["test"] == "StringEquals"
    assert select["values"] == ["SPECIFIC_ATTRIBUTES"]


class _QueryRecorder:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def query(self, **kwargs):
        self.requests.append(kwargs)
        return {"Items": []}


def test_handler_query_fits_the_by_type_conditions(document, monkeypatch):
    client = _QueryRecorder()
    monkeypatch.setattr(handler, "dynamodb_client", lambda: client)
    handler.read_index()
    (request,) = client.requests
    block = statement(document, "RegistryByTypeIndex")
    allowed = {c["variable"]: set(c["values"]) for c in conditions(block)}
    names = request["ExpressionAttributeNames"]
    projected = {names[a.strip()] for a in request["ProjectionExpression"].split(",")}
    assert projected <= allowed["dynamodb:Attributes"]
    assert set(names.values()) <= allowed["dynamodb:Attributes"]
    assert request["Select"] in allowed["dynamodb:Select"]
    assert request["IndexName"] == "by-type"


# ---- BatchGetItem (criteria 6, 8, 22) ----


def test_batch_get_statement_is_limited_to_device_and_threshold_partitions(document):
    block = statement(document, "RegistryAssignmentsAndThresholds")
    assert actions(block) == ["dynamodb:BatchGetItem"]
    assert re.search(r"resources\s*=\s*\[module\.registry_table\.arn\]", block)
    assert "/index/" not in block
    leading = condition(block, "dynamodb:LeadingKeys")
    assert leading["test"] == "ForAllValues:StringLike"
    assert sorted(leading["values"]) == ["DEVICE#*", "THRESHOLDS#*"]


class _BatchRecorder:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def batch_get_item(self, **kwargs):
        self.requests.append(kwargs)
        return {"Responses": {}}


def test_handler_batch_keys_fit_the_leading_keys_condition(document, monkeypatch):
    client = _BatchRecorder()
    monkeypatch.setattr(handler, "dynamodb_client", lambda: client)
    handler.read_devices(["wearable-0001", "bedroom-0001"])
    handler.read_limits(["res-0001"])
    block = statement(document, "RegistryAssignmentsAndThresholds")
    leading = condition(block, "dynamodb:LeadingKeys")
    pks = [
        key["pk"]["S"]
        for request in client.requests
        for table in request["RequestItems"].values()
        for key in table["Keys"]
    ]
    assert len(pks) == 3
    for pk in pks:
        assert any(fnmatchcase(pk, pattern) for pattern in leading["values"]), pk


BATCH_GET_ATTRIBUTES = {
    "pk",
    "sk",
    "resident_id",
    "device_type",
    "assigned_at",
    "hr_bpm",
    "spo2_pct",
    "skin_temp_c",
}


def _attribute_conditions(block: str) -> dict[str, dict]:
    """The statement's dynamodb:Attributes conditions, by test (there are two)."""
    return {
        c["test"]: c
        for c in conditions(block)
        if c["variable"] == "dynamodb:Attributes"
    }


def test_batch_get_statement_limits_the_attributes(document):
    block = statement(document, "RegistryAssignmentsAndThresholds")
    by_test = _attribute_conditions(block)
    assert set(by_test["ForAllValues:StringEquals"]["values"]) == BATCH_GET_ATTRIBUTES
    # Threshold items also hold updated_by (a staff sub) and updated_by_name.
    assert not {"updated_by", "updated_by_name", "updated_at"} & BATCH_GET_ATTRIBUTES


def test_batch_get_statement_refuses_a_read_without_a_projection(document):
    block = statement(document, "RegistryAssignmentsAndThresholds")
    by_test = _attribute_conditions(block)
    assert by_test["Null"]["values"] == ["false"]
    select = condition(block, "dynamodb:Select")
    assert select["test"] == "StringEqualsIfExists"
    assert select["values"] == ["SPECIFIC_ATTRIBUTES"]


def test_handler_batch_projections_fit_the_attributes_condition(document, monkeypatch):
    client = _BatchRecorder()
    monkeypatch.setattr(handler, "dynamodb_client", lambda: client)
    handler.read_devices(["wearable-0001", "bedroom-0001"])
    handler.read_limits(["res-0001"])
    block = statement(document, "RegistryAssignmentsAndThresholds")
    allowed = set(_attribute_conditions(block)["ForAllValues:StringEquals"]["values"])
    assert len(client.requests) == 2
    for request in client.requests:
        for table in request["RequestItems"].values():
            names = table["ExpressionAttributeNames"]
            projected = {
                names[a.strip()] for a in table["ProjectionExpression"].split(",")
            }
            assert projected
            assert projected <= allowed


def test_no_other_dynamodb_access(document):
    granted = set()
    for block in re.split(r"\n  statement \{", document)[1:]:
        granted |= {a for a in actions(block) if a.startswith("dynamodb:")}
    assert granted == {"dynamodb:Query", "dynamodb:BatchGetItem"}


# ---- S3 (criteria 16, 22) ----


def test_history_listing_is_limited_to_summaries(document):
    block = statement(document, "ListSummaryPrefix")
    assert actions(block) == ["s3:ListBucket"]
    (prefix,) = conditions(block)
    assert prefix["variable"] == "s3:prefix"
    assert prefix["values"] == ["${local.summaries_prefix}/*"]
    assert fnmatchcase(handler.sql.summary_prefix("2026-10-18"), "summaries/*")


def test_object_writes_are_limited_to_summaries(document):
    block = statement(document, "ReplaceSummaryObjects")
    assert sorted(actions(block)) == [
        "s3:DeleteObject",
        "s3:PutObject",
        "s3:PutObjectTagging",
    ]
    assert '"${aws_s3_bucket.history.arn}/${local.summaries_prefix}/*"' in block


def test_summaries_prefix_is_summaries():
    text = SUMMARIES_TF.read_text(encoding="utf-8")
    assert re.search(r'summaries_prefix\s*=\s*"summaries"', text)


# ---- criterion 17: only this role writes summaries/ ----


def test_bucket_policy_denies_summary_writes_to_everyone_else():
    document = HISTORY_TF.read_text(encoding="utf-8")
    block = statement(document, "DenySummaryWritesFromOthers")
    assert re.search(r'effect\s*=\s*"Deny"', block)
    assert set(actions(block)) == {
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:PutObjectTagging",
        "s3:DeleteObjectTagging",
    }
    assert "${local.summaries_prefix}/*" in block
    (principal,) = conditions(block)
    assert principal["test"] == "StringNotEquals"
    assert principal["variable"] == "aws:PrincipalArn"
    assert principal["raw"].strip() == "module.summaries.role_arn"


# ---- criterion 5: retries ----


def test_async_invocations_get_two_retries_and_an_hour():
    text = SUMMARIES_TF.read_text(encoding="utf-8")
    start = text.index('resource "aws_lambda_function_event_invoke_config" "summaries"')
    block = text[start : text.index("\n}\n", start)]
    assert re.search(r"maximum_retry_attempts\s*=\s*2\b", block)
    assert re.search(r"maximum_event_age_in_seconds\s*=\s*3600\b", block)
