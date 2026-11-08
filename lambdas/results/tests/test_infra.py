"""infra/results.tf, the summaries hand-off in infra/summaries.tf, and the Results table
in infra/dynamodb.tf, read as text (nightly-results.md criteria 1, 3, 9, 10, 12, 14), and
how they match what the handler sends.
"""

from __future__ import annotations

import re
import sys
from fnmatch import fnmatchcase
from pathlib import Path

import handler
import pytest

INFRA = Path(__file__).resolve().parents[3] / "infra"
RESULTS_TF = INFRA / "results.tf"
SUMMARIES_TF = INFRA / "summaries.tf"
DYNAMODB_TF = INFRA / "dynamodb.tf"
SRC = Path(handler.__file__).resolve().parent


def block(text: str, header: str) -> str:
    start = text.index(header)
    return text[start : text.index("\n}\n", start)]


def statements(document: str) -> list[str]:
    return re.split(r"\n  statement \{", document)[1:]


def statement(document: str, sid: str) -> str:
    found = [b for b in statements(document) if re.search(rf'sid\s*=\s*"{sid}"', b)]
    assert len(found) == 1, f"statement {sid} not found exactly once"
    return found[0]


def strings(text: str) -> list[str]:
    return re.findall(r'"([^"]*)"', text)


def actions(text: str) -> list[str]:
    return strings(re.search(r"actions\s*=\s*\[(.*?)\]", text, re.DOTALL).group(1))


def resources(text: str) -> str:
    return re.search(r"resources\s*=\s*\[(.*?)\]", text, re.DOTALL).group(1).strip()


@pytest.fixture(scope="module")
def results_tf() -> str:
    return RESULTS_TF.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def document(results_tf) -> str:
    return block(results_tf, 'data "aws_iam_policy_document" "results" {')


@pytest.fixture(scope="module")
def summaries_tf() -> str:
    return SUMMARIES_TF.read_text(encoding="utf-8")


# ---- criterion 14: IAM ----


def test_policy_has_exactly_the_three_statements(document):
    granted = sorted(a for b in statements(document) for a in actions(b))
    assert granted == ["dynamodb:PutItem", "s3:GetObject", "s3:ListBucket"]
    assert "Deny" not in document
    assert len(statements(document)) == 3


def test_listing_is_limited_to_the_summaries_prefix(document):
    text = statement(document, "ListSummaryPrefix")
    assert actions(text) == ["s3:ListBucket"]
    assert resources(text) == "aws_s3_bucket.history.arn"
    assert re.search(r'variable\s*=\s*"s3:prefix"', text)
    assert re.search(r'test\s*=\s*"StringLike"', text)
    assert '"${local.summaries_prefix}/*"' in text


def test_handler_prefix_fits_the_list_condition():
    assert fnmatchcase(handler.summary_prefix("2026-11-18"), "summaries/*")


def test_reads_are_limited_to_summaries_objects(document):
    text = statement(document, "ReadSummaryObjects")
    assert actions(text) == ["s3:GetObject"]
    assert (
        resources(text) == '"${aws_s3_bucket.history.arn}/${local.summaries_prefix}/*"'
    )


def test_writes_are_put_item_on_the_results_table_only(document):
    text = statement(document, "WriteResults")
    assert actions(text) == ["dynamodb:PutItem"]
    assert resources(text) == "module.results_table.arn"


def test_summaries_prefix_is_summaries(summaries_tf):
    assert re.search(r'summaries_prefix\s*=\s*"summaries"', summaries_tf)


# ---- criterion 12: function settings ----


def test_function_settings(results_tf):
    text = block(results_tf, 'module "results" {')
    assert re.search(r"timeout\s*=\s*120\b", text)
    assert re.search(r"memory_size\s*=\s*128\b", text)
    assert re.search(r"log_retention_days\s*=\s*30\b", text)
    assert "local.powertools_layer_arn" in text
    assert re.search(r'handler\s*=\s*"handler.lambda_handler"', text)
    assert re.search(r"HISTORY_BUCKET\s*=\s*aws_s3_bucket.history.bucket", text)
    assert re.search(r"RESULTS_TABLE\s*=\s*module.results_table.name", text)
    assert 'results_function_name = "silvercare-results"' in results_tf


def test_zip_is_built_from_the_src_folder(results_tf):
    text = block(results_tf, 'data "archive_file" "results" {')
    assert "/../lambdas/results/src" in text


def test_src_imports_only_stdlib_boto3_and_powertools():
    allowed_third_party = {"boto3", "botocore", "aws_lambda_powertools"}
    for path in SRC.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for top in re.findall(
            r"^(?:from|import)\s+([A-Za-z_][\w]*)", source, re.MULTILINE
        ):
            assert (
                top in sys.stdlib_module_names
                or top in allowed_third_party
                or top == "__future__"
                or (SRC / f"{top}.py").exists()
            ), top


# ---- criterion 9: async retries ----


def test_async_invocations_get_two_retries_and_an_hour(results_tf):
    text = block(
        results_tf, 'resource "aws_lambda_function_event_invoke_config" "results"'
    )
    assert re.search(r"maximum_retry_attempts\s*=\s*2\b", text)
    assert re.search(r"maximum_event_age_in_seconds\s*=\s*3600\b", text)


# ---- criterion 3: the hand-off from summaries ----


def test_summaries_on_success_destination_is_results(summaries_tf):
    text = block(
        summaries_tf, 'resource "aws_lambda_function_event_invoke_config" "summaries"'
    )
    on_success = re.search(r"on_success\s*\{(.*?)\}", text, re.DOTALL).group(1)
    assert re.search(r"destination\s*=\s*module.results.function_arn", on_success)
    assert "on_failure" not in text


def test_summaries_may_invoke_results_only(summaries_tf):
    document = block(summaries_tf, 'data "aws_iam_policy_document" "summaries" {')
    text = statement(document, "InvokeResults")
    assert actions(text) == ["lambda:InvokeFunction"]
    assert resources(text) == "module.results.function_arn"
    invokes = [b for b in statements(document) if "lambda:" in b]
    assert len(invokes) == 1


# ---- criterion 10: alarms ----


def _alarm(results_tf: str, name: str) -> str:
    return block(results_tf, f'resource "aws_cloudwatch_metric_alarm" "{name}"')


def test_errors_alarm(results_tf):
    text = _alarm(results_tf, "results_errors")
    assert '"silvercare-results-errors"' in text
    assert re.search(r'metric_name\s*=\s*"Errors"', text)
    assert re.search(r"FunctionName\s*=\s*module.results.function_name", text)
    assert re.search(r"period\s*=\s*3600\b", text)
    assert re.search(r"threshold\s*=\s*3\b", text)
    assert re.search(r'"GreaterThanOrEqualToThreshold"', text)
    assert re.search(r"alarm_actions\s*=\s*\[aws_sns_topic.ops.arn\]", text)
    description = re.search(r'alarm_description\s*=\s*"(.*)"\n', text).group(1)
    assert "re-run results" in description
    assert '{\\"date\\": \\"yyyy-mm-dd\\"}' in description


def test_destination_failures_alarm(results_tf):
    text = _alarm(results_tf, "summaries_destination_failures")
    assert '"silvercare-summaries-destination-failures"' in text
    assert re.search(r'metric_name\s*=\s*"DestinationDeliveryFailures"', text)
    assert re.search(r"FunctionName\s*=\s*module.summaries.function_name", text)
    assert re.search(r"period\s*=\s*3600\b", text)
    assert re.search(r"threshold\s*=\s*1\b", text)
    assert re.search(r'"GreaterThanOrEqualToThreshold"', text)
    assert re.search(r"alarm_actions\s*=\s*\[aws_sns_topic.ops.arn\]", text)


# ---- criterion 1: the table ----


def test_results_table_definition():
    text = block(DYNAMODB_TF.read_text(encoding="utf-8"), 'module "results_table" {')
    assert re.search(r'source\s*=\s*"./modules/dynamodb-table"', text)
    assert re.search(r'name\s*=\s*"silvercare-results"', text)
    assert re.search(r'hash_key\s*=\s*"pk"', text)
    assert re.search(r'range_key\s*=\s*"sk"', text)
    assert re.search(r"read_capacity\s*=\s*1\b", text)
    assert re.search(r"write_capacity\s*=\s*1\b", text)
    assert re.search(r'ttl_attribute\s*=\s*"expires_at"', text)
    assert "global_secondary_indexes" not in text


def test_capacity_comment_is_20_rcu_19_wcu():
    text = DYNAMODB_TF.read_text(encoding="utf-8")
    assert "Current total: 20 RCU / 19 WCU." in text


def test_capacity_total_matches_the_comment():
    """Sum every read/write capacity in dynamodb.tf (tables and indexes)."""
    text = DYNAMODB_TF.read_text(encoding="utf-8")
    reads = sum(int(n) for n in re.findall(r"read_capacity\s*=\s*(\d+)", text))
    writes = sum(int(n) for n in re.findall(r"write_capacity\s*=\s*(\d+)", text))
    assert (reads, writes) == (20, 19)


def test_table_module_turns_on_deletion_protection_and_pitr():
    main = (INFRA / "modules" / "dynamodb-table" / "main.tf").read_text(
        encoding="utf-8"
    )
    assert re.search(r"deletion_protection_enabled\s*=\s*true", main)
    pitr = re.search(r"point_in_time_recovery\s*\{(.*?)\}", main, re.DOTALL).group(1)
    assert re.search(r"enabled\s*=\s*true", pitr)
