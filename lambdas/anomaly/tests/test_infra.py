"""infra/anomaly.tf read as text (docs/features/nightly-anomaly.md criteria 9-11, 22, 23
and the roles table), and how it matches what the handler sends.
"""

from __future__ import annotations

import re
import sys
from fnmatch import fnmatchcase
from pathlib import Path

import handler
import pytest

INFRA = Path(__file__).resolve().parents[3] / "infra"
ANOMALY_TF = INFRA / "anomaly.tf"
SRC = Path(handler.__file__).resolve().parent
ML = "${aws_s3_bucket.ml.arn}"
JOB = "${local.sagemaker_arn_prefix}"


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


def resources(text: str) -> list[str]:
    inner = re.search(r"resources\s*=\s*\[(.*?)\]", text, re.DOTALL).group(1)
    return [r.strip().strip('"') for r in inner.split(",") if r.strip()]


@pytest.fixture(scope="module")
def tf() -> str:
    return ANOMALY_TF.read_text(encoding="utf-8")


def policy(tf: str, name: str) -> str:
    return block(tf, f'data "aws_iam_policy_document" "{name}" {{')


def granted(document: str) -> list[str]:
    return sorted(a for b in statements(document) for a in actions(b))


# ---- criterion 22: the ML bucket ----


def test_bucket_name_and_kept(tf):
    text = block(tf, 'resource "aws_s3_bucket" "ml" {')
    assert '"silvercare-ml-${data.aws_caller_identity.current.account_id}"' in text
    assert re.search(r"force_destroy\s*=\s*false", text)
    assert re.search(r"prevent_destroy\s*=\s*true", text)
    assert "aws_s3_bucket_versioning" not in tf


def test_bucket_is_private_and_sse_s3(tf):
    public = block(tf, 'resource "aws_s3_bucket_public_access_block" "ml" {')
    assert len(re.findall(r"=\s*true", public)) == 4
    sse = block(
        tf, 'resource "aws_s3_bucket_server_side_encryption_configuration" "ml" {'
    )
    assert re.search(r'sse_algorithm\s*=\s*"AES256"', sse)


def test_only_input_and_output_expire_after_30_days(tf):
    text = block(tf, 'resource "aws_s3_bucket_lifecycle_configuration" "ml" {')
    assert re.findall(r'prefix\s*=\s*"([^"]*)"', text) == ["input/", "output/"]
    assert re.findall(r"\bdays\s*=\s*(\d+)", text) == ["30", "30"]


def test_bucket_policy_is_tls_only(tf):
    text = statement(policy(tf, "ml_bucket"), "DenyInsecureTransport")
    assert re.search(r'effect\s*=\s*"Deny"', text)
    assert re.search(r'variable\s*=\s*"aws:SecureTransport"', text)


def test_only_collect_writes_scores(tf):
    text = statement(policy(tf, "ml_bucket"), "DenyScoreWritesFromOthers")
    assert re.search(r'effect\s*=\s*"Deny"', text)
    assert {"s3:PutObject", "s3:DeleteObject"} <= set(actions(text))
    assert resources(text) == [f"{ML}/scores/*"]
    assert re.search(r'test\s*=\s*"StringNotEquals"', text)
    assert re.search(r"values\s*=\s*\[module.anomaly_collect.role_arn\]", text)


# ---- roles table: what each Lambda may do ----


def test_score_policy(tf):
    document = policy(tf, "anomaly_score")
    assert "anomaly_read_summaries.json" in document
    assert granted(document) == [
        "lambda:InvokeFunction",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:PutObject",
        "sagemaker:CreateTransformJob",
    ]
    assert resources(statement(document, "ReadCurrentModel")) == [
        f"{ML}/{handler.CURRENT_KEY}"
    ]
    assert resources(statement(document, "InvokeCollect")) == [
        "module.anomaly_collect.function_arn"
    ]


def test_train_policy(tf):
    document = policy(tf, "anomaly_train")
    assert "anomaly_read_summaries.json" in document
    assert granted(document) == [
        "iam:PassRole",
        "s3:PutObject",
        "sagemaker:CreateTrainingJob",
    ]
    assert resources(statement(document, "WriteTrainInput")) == [f"{ML}/input/train/*"]


def test_collect_policy(tf):
    document = policy(tf, "anomaly_collect")
    assert granted(document) == [
        "dynamodb:UpdateItem",
        "iam:PassRole",
        "s3:DeleteObject",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:PutObject",
        "s3:PutObject",
        "s3:PutObjectTagging",
        "sagemaker:CreateModel",
        "sagemaker:CreateTransformJob",
    ]
    assert resources(statement(document, "UpdateResults")) == [
        "module.results_table.arn"
    ]
    assert resources(statement(document, "WriteScores")) == [f"{ML}/scores/*"]


def test_pass_role_is_the_sagemaker_role_to_sagemaker_only(tf):
    for name in ("anomaly_train", "anomaly_collect"):
        text = statement(policy(tf, name), "PassSageMakerRole")
        assert resources(text) == ["aws_iam_role.sagemaker_anomaly.arn"]
        assert re.search(r'values\s*=\s*\["sagemaker.amazonaws.com"\]', text)


def test_no_wildcard_actions_or_allow_star_resources(tf):
    for name in (
        "anomaly_score",
        "anomaly_train",
        "anomaly_collect",
        "sagemaker_anomaly",
    ):
        for b in statements(policy(tf, name)):
            assert not any(a.endswith("*") for a in actions(b)), b
            if resources(b) == ["*"]:
                # PutMetricData takes no resource: narrowed by namespace instead.
                assert actions(b) == ["cloudwatch:PutMetricData"]
                assert "cloudwatch:namespace" in b


# ---- the handler's names fit the IAM resources ----


def test_job_names_fit_the_iam_resources(tf):
    date, run = "2026-11-16", "a1b2c3d4"
    version = f"anomaly-train-20261116-{run}"
    assert handler.VERSION_RE.fullmatch(version)
    assert handler.SCORE_JOB_RE.fullmatch(f"anomaly-score-{date}-{run}")
    assert handler.CAL_JOB_RE.fullmatch(f"anomaly-cal-{version}")
    cases = [
        ("anomaly_score", "StartScoring", f"transform-job/anomaly-score-{date}-{run}"),
        ("anomaly_train", "StartTraining", f"training-job/{version}"),
        ("anomaly_collect", "CreateModel", f"model/{version}"),
        ("anomaly_collect", "StartCalibration", f"transform-job/anomaly-cal-{version}"),
    ]
    for name, sid, arn in cases:
        (pattern,) = resources(statement(policy(tf, name), sid))
        assert pattern.startswith(f"{JOB}:"), pattern
        assert fnmatchcase(arn, pattern.removeprefix(f"{JOB}:")), (arn, pattern)


def test_score_keys_fit_the_iam_resources():
    prefix = handler.score_prefix("2026-11-16", "a1b2c3d4")
    assert fnmatchcase(prefix + "rows.csv", "input/score/*")
    # The sidecar must sit outside the transform's input prefix (criterion 7).
    assert not "input/score-meta/".startswith(prefix)


# ---- criterion 23: functions ----


@pytest.mark.parametrize(
    ("module", "entry", "timeout", "env"),
    [
        (
            "anomaly_score",
            "score_handler",
            120,
            ["HISTORY_BUCKET", "ML_BUCKET", "COLLECT_FUNCTION"],
        ),
        (
            "anomaly_train",
            "train_handler",
            300,
            ["HISTORY_BUCKET", "ML_BUCKET", "RCF_IMAGE", "SAGEMAKER_ROLE_ARN"],
        ),
        (
            "anomaly_collect",
            "collect_handler",
            60,
            ["ML_BUCKET", "RESULTS_TABLE", "RCF_IMAGE", "SAGEMAKER_ROLE_ARN"],
        ),
    ],
)
def test_function_settings(tf, module, entry, timeout, env):
    text = block(tf, f'module "{module}" {{')
    assert re.search(rf'handler\s*=\s*"handler.{entry}"', text)
    assert callable(getattr(handler, entry))
    assert re.search(rf"timeout\s*=\s*{timeout}\b", text)
    assert re.search(r"memory_size\s*=\s*128\b", text)
    assert re.search(r"log_retention_days\s*=\s*30\b", text)
    assert "local.powertools_layer_arn" in text
    for name in env:
        assert re.search(rf"\b{name}\s*=", text), name


def test_every_env_var_the_handler_reads_is_set(tf):
    source = (SRC / "handler.py").read_text(encoding="utf-8")
    read = set(re.findall(r'os\.environ\["([A-Z_]+)"\]', source))
    lambdas = "".join(
        block(tf, f'module "{m}" {{')
        for m in ("anomaly_score", "anomaly_train", "anomaly_collect")
    )
    assert read and all(re.search(rf"\b{n}\s*=", lambdas) for n in read), read


@pytest.mark.parametrize("name", ["anomaly_score", "anomaly_train", "anomaly_collect"])
def test_async_invocations_get_two_retries_and_an_hour(tf, name):
    text = block(tf, f'resource "aws_lambda_function_event_invoke_config" "{name}"')
    assert re.search(r"maximum_retry_attempts\s*=\s*2\b", text)
    assert re.search(r"maximum_event_age_in_seconds\s*=\s*3600\b", text)


def test_zip_is_built_from_the_src_folder(tf):
    assert "/../lambdas/anomaly/src" in block(tf, 'data "archive_file" "anomaly" {')


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


# ---- criteria 9-11, 24: triggers, instance, no alarms ----


def test_weekly_schedule_is_monday_10_sgt(tf):
    text = block(tf, 'resource "aws_cloudwatch_event_rule" "anomaly_train" {')
    assert re.search(r'schedule_expression\s*=\s*"cron\(0 2 \? \* MON \*\)"', text)
    target = block(tf, 'resource "aws_cloudwatch_event_target" "anomaly_train" {')
    assert "module.anomaly_train.function_arn" in target


def test_job_events_reach_collect(tf):
    text = block(tf, 'resource "aws_cloudwatch_event_rule" "anomaly_jobs" {')
    assert '"SageMaker Training Job State Change"' in text
    assert '"SageMaker Transform Job State Change"' in text
    assert '{ prefix = "anomaly-train-" }' in text
    assert '{ prefix = "anomaly-" }' in text
    target = block(tf, 'resource "aws_cloudwatch_event_target" "anomaly_jobs" {')
    assert "module.anomaly_collect.function_arn" in target


def test_jobs_use_one_ml_m5_large_and_the_rcf_image(tf):
    assert handler.INSTANCE_TYPE == "ml.m5.large"
    assert 'repository_name = "randomcutforest"' in tf


def test_no_alarms_or_endpoints(tf):
    assert "aws_cloudwatch_metric_alarm" not in tf
    assert "aws_sagemaker_endpoint" not in tf
