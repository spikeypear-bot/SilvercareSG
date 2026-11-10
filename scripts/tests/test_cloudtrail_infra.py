"""infra/cloudtrail.tf and the comments it fixes, read as text (docs/features/cloudtrail.md)."""

from __future__ import annotations

import re
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2] / "infra"
CLOUDTRAIL_TF = (INFRA / "cloudtrail.tf").read_text()


def code(text: str = CLOUDTRAIL_TF) -> str:
    """The file without comments (checkov skips and notes would otherwise match)."""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def block(header: str, text: str | None = None) -> str:
    """The body of the first block whose header starts with `header`, braces matched."""
    text = code() if text is None else text
    start = text.index(header)
    i = text.index("{", start)
    depth = 0
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i + 1 : j]
    raise AssertionError(f"unclosed block {header}")


def blocks(header: str, text: str) -> list[str]:
    out, pos = [], 0
    while (k := text.find(header, pos)) != -1:
        out.append(block(header, text[k:]))
        pos = k + len(header)
    return out


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


# Criterion 1: the bucket and its standard settings.
def test_bucket_name_and_protection():
    b = squash(block('resource "aws_s3_bucket" "cloudtrail"'))
    assert (
        'bucket = "silvercare-cloudtrail-${data.aws_caller_identity.current.account_id}"'
        in b
    )
    assert "force_destroy = false" in b
    assert "prevent_destroy = true" in b


def test_bucket_public_access_blocked():
    b = squash(block('resource "aws_s3_bucket_public_access_block" "cloudtrail"'))
    for setting in (
        "block_public_acls",
        "block_public_policy",
        "ignore_public_acls",
        "restrict_public_buckets",
    ):
        assert f"{setting} = true" in b


def test_bucket_owner_enforced_and_sse_s3():
    assert 'object_ownership = "BucketOwnerEnforced"' in block(
        'resource "aws_s3_bucket_ownership_controls" "cloudtrail"'
    )
    assert 'sse_algorithm = "AES256"' in block(
        'resource "aws_s3_bucket_server_side_encryption_configuration" "cloudtrail"'
    )


# Criterion 2: every object expires at 365 days; no versioning.
def test_lifecycle_expires_everything_after_365_days():
    lc = block('resource "aws_s3_bucket_lifecycle_configuration" "cloudtrail"')
    rules = blocks("rule", lc)
    expiring = [r for r in rules if "expiration" in r]
    assert len(expiring) == 1
    assert re.search(r"filter\s*\{\s*\}", expiring[0]), (
        "expiry must cover the whole bucket"
    )
    assert re.search(r"days\s*=\s*365\b", expiring[0])
    aborts = [r for r in rules if "abort_incomplete_multipart_upload" in r]
    assert aborts and re.search(r"days_after_initiation\s*=\s*1\b", aborts[0])
    assert "aws_s3_bucket_versioning" not in code()


# Criterion 3: CloudTrail may write only for this trail; TLS is required.
def test_bucket_policy_allows_only_this_trail():
    doc = block('data "aws_iam_policy_document" "cloudtrail_bucket"')
    stmts = {
        re.search(r'sid\s*=\s*"([^"]+)"', s).group(1): squash(s)
        for s in blocks("statement", doc)
    }
    assert set(stmts) == {
        "CloudTrailAclCheck",
        "CloudTrailWrite",
        "DenyInsecureTransport",
    }

    acl = stmts["CloudTrailAclCheck"]
    assert 'actions = ["s3:GetBucketAcl"]' in acl
    assert "resources = [aws_s3_bucket.cloudtrail.arn]" in acl

    write = stmts["CloudTrailWrite"]
    assert 'actions = ["s3:PutObject"]' in write
    assert "/AWSLogs/${data.aws_caller_identity.current.account_id}/*" in write
    assert 'values = ["bucket-owner-full-control"]' in write

    for s in (acl, write):
        assert 'identifiers = ["cloudtrail.amazonaws.com"]' in s
        assert 'variable = "aws:SourceArn"' in s
        assert "values = [local.cloudtrail_arn]" in s

    deny = stmts["DenyInsecureTransport"]
    assert 'effect = "Deny"' in deny and 'variable = "aws:SecureTransport"' in deny


def test_trail_arn_matches_trail_name():
    locals_ = squash(block("locals"))
    assert 'cloudtrail_name = "silvercare-trail"' in locals_
    assert ":trail/${local.cloudtrail_name}" in locals_
    assert "name = local.cloudtrail_name" in squash(
        block('resource "aws_cloudtrail" "audit"')
    )


# Criterion 4: one multi-Region trail with validation, no extras.
def test_one_trail_with_required_settings():
    assert code().count('resource "aws_cloudtrail"') == 1
    t = squash(block('resource "aws_cloudtrail" "audit"'))
    for setting in (
        "is_multi_region_trail = true",
        "include_global_service_events = true",
        "enable_log_file_validation = true",
        "enable_logging = true",
        "s3_bucket_name = aws_s3_bucket.cloudtrail.id",
        "depends_on = [aws_s3_bucket_policy.cloudtrail]",
    ):
        assert setting in t, setting
    for absent in (
        "s3_key_prefix",
        "sns_topic_name",
        "cloud_watch_logs",
        "kms_key_id",
        "insight_selector",
    ):
        assert absent not in t, absent


# Criterion 5: exactly the three advanced selectors, no basic selectors.
def selectors() -> dict[str, list[tuple[str, str, str]]]:
    t = block('resource "aws_cloudtrail" "audit"')
    assert not re.search(r"\bevent_selector\s*\{", t), "advanced selectors only"
    out = {}
    for sel in blocks("advanced_event_selector", t):
        name = re.search(r'name\s*=\s*"([^"]+)"', sel).group(1)
        fields = []
        for f in blocks("field_selector", sel):
            field = re.search(r'field\s*=\s*"([^"]+)"', f).group(1)
            op, value = re.search(r"(\w+)\s*=\s*\[([^\]]*)\]", f).groups()
            fields.append((field, op, value.strip()))
        out[name] = sorted(fields)
    return out


def test_exactly_three_selectors():
    assert set(selectors()) == {
        "Management events",
        "Registry writes",
        "Terraform state",
    }


def test_management_selector_takes_all_management_events():
    assert selectors()["Management events"] == [
        ("eventCategory", "equals", '"Management"')
    ]


def test_registry_selector_is_writes_on_registry_only():
    assert selectors()["Registry writes"] == sorted(
        [
            ("eventCategory", "equals", '"Data"'),
            ("resources.type", "equals", '"AWS::DynamoDB::Table"'),
            ("resources.ARN", "equals", "module.registry_table.arn"),
            ("readOnly", "equals", '"false"'),
        ]
    )


def test_state_selector_is_state_bucket_objects_reads_and_writes():
    assert selectors()["Terraform state"] == sorted(
        [
            ("eventCategory", "equals", '"Data"'),
            ("resources.type", "equals", '"AWS::S3::Object"'),
            (
                "resources.ARN",
                "starts_with",
                '"arn:aws:s3:::silvercare-tfstate-${data.aws_caller_identity.current.account_id}/"',
            ),
        ]
    )


# Criterion 6: tags come from the provider's default_tags.
def test_default_tags_cover_the_new_resources():
    providers = squash((INFRA / "providers.tf").read_text())
    assert (
        'project = "silvercare"' in providers
        and 'managed_by = "terraform"' in providers
    )
    assert "tags" not in code()


# Criterion 8: every checkov skip has a reason.
def test_checkov_skips_have_reasons():
    skips = re.findall(r"#checkov:skip=([A-Z0-9_]+):(.*)", CLOUDTRAIL_TF)
    assert skips
    assert all(reason.strip() for _, reason in skips)


# Criteria 9, 10, 10a: the comments that relied on records that didn't exist.
def test_threshold_log_comment_points_at_the_trail():
    api = (INFRA / "api.tf").read_text()
    assert "tamper-evident only via CloudTrail." not in api
    assert "CloudTrail trail's registry write events (infra/cloudtrail.tf" in api


def test_state_bucket_skip_reason_names_data_events():
    main = (INFRA / "bootstrap" / "main.tf").read_text()
    assert "recorded by CloudTrail management events" not in main
    assert "S3 data events (infra/cloudtrail.tf)" in main


def test_plan_role_cannot_read_event_history():
    plan = code((INFRA / "bootstrap" / "ci-plan-role.tf").read_text())
    deny = next(s for s in blocks("statement", plan) if '"DenyDataReads"' in s)
    assert '"cloudtrail:LookupEvents"' in deny
