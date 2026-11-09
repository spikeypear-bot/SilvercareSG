"""infra/dashboard.tf read as text (docs/features/cloudwatch-dashboard.md criteria 1-5)."""

from __future__ import annotations

import re
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2] / "infra"
DASHBOARD_TF = (INFRA / "dashboard.tf").read_text()
# Every metric is a list starting with its namespace; expressions are [{ expression = ... }].
METRIC_RE = re.compile(r'\[\s*"([^"]+)",\s*"[^"]+"')


def code() -> str:
    return "\n".join(line.split("#", 1)[0] for line in DASHBOARD_TF.splitlines())


def metrics() -> list[str]:
    return METRIC_RE.findall(code())


def test_one_dashboard_named_silvercare_overview():
    assert code().count('resource "aws_cloudwatch_dashboard"') == 1
    assert 'dashboard_name = "silvercare-overview"' in code()


def test_metric_count_within_free_tier():
    # The count is only exact because no metric comes from a `for` expression.
    assert not re.search(r"\[\s*for\s", code())
    assert 0 < len(metrics()) <= 50


def test_only_aws_namespaces():
    assert all(ns.startswith("AWS/") for ns in metrics())


def test_no_log_or_custom_widgets():
    types = re.findall(r'type\s*=\s*"([^"]+)"', code())
    assert types and set(types) == {"metric"}


def test_no_hard_coded_ids():
    # Dimension values come from Terraform references, never quoted IDs (criterion 2).
    allowed_literals = {"MQTT"}
    for m in re.finditer(
        r'"(ApiId|FunctionName|QueueName|TableName|InstanceId|Protocol)",\s*("[^"]*")?',
        code(),
    ):
        literal = m.group(2)
        assert literal is None or literal.strip('"') in allowed_literals, m.group(0)


def test_nothing_else_added():
    # Criterion 5: no alarms, topics, metric filters, or IAM in this file.
    for kind in (
        "aws_cloudwatch_metric_alarm",
        "aws_sns_topic",
        "aws_cloudwatch_log_metric_filter",
        "aws_iam_",
    ):
        assert kind not in code()
