# CloudTrail audit trail (docs/features/cloudtrail.md). One multi-Region trail keeps an
# audit record of the account in S3 for 365 days (owner's decision 2026-11-10, matching
# raw telemetry). Three advanced event selectors, and nothing else:
# - Management events, read and write: who changed what (Terraform applies, IAM,
#   Cognito users, console sign-ins). Multi-Region because, since 2021, IAM, STS, and
#   sign-in events reach a trail outside us-east-1 only if it is multi-Region.
# - silvercare-registry writes (data events): threshold changes and their history log
#   (THRESHOLD_LOG#, which api-thresholds' role could overwrite: infra/api.tf), and hand
#   edits by the shared developer user. DynamoDB data events record the item key and
#   attribute names, never non-key values; registry keys are opaque IDs.
# - Terraform state object reads and writes (data events): who read or changed state.
#   The bucket is in infra/bootstrap/, so it is referenced by name.
#
# Who reads the logs: the account owner, and developers read-only through their existing
# ReadProjectBuckets statement (silvercare-*). Nobody but CloudTrail writes here. The logs
# hold app users' Cognito sub and sign-in IP address (no username or email), so one
# person's entries are erased by the 365-day expiry, not on request (log file
# validation would flag an edited file).
#
# Cost (CloudTrail pricing page, checked 2026-11-10; volumes are estimates): the first
# copy of management events is free; data events are US$0.10 per 100,000 (registry
# writes well under 10,000 a month, state ~5 object calls x 40 CI runs a day x 30 =
# ~6,000 a month): ~US$0.02/month; S3 storage under 1 GB a year: < US$0.03/month.
# No fixed fee.
#
# Removing the trail needs two merges: first drop prevent_destroy, then the resources.

locals {
  cloudtrail_name = "silvercare-trail"
  # Built from the name: the bucket policy needs it before the trail exists.
  cloudtrail_arn = "arn:aws:cloudtrail:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:trail/${local.cloudtrail_name}"
}

# ---------------------------------------------------------------------------------------
# Bucket
# ---------------------------------------------------------------------------------------

resource "aws_s3_bucket" "cloudtrail" {
  #checkov:skip=CKV_AWS_21:Versioning off: the 365-day expiry must really delete, and log file validation already shows any edited or deleted file.
  #checkov:skip=CKV_AWS_18:No access logging: it would need a second bucket and its own retention decision; reads are by the owner and developers only.
  #checkov:skip=CKV_AWS_144:No cross-region replication. The logs stay in ap-southeast-1 (Singapore), and replication would double storage cost.
  #checkov:skip=CKV_AWS_145:SSE-S3 (AWS-managed key). A customer-managed KMS key costs ~US$1/month plus request charges.
  #checkov:skip=CKV2_AWS_62:No event notifications. Nothing consumes the log files automatically.
  bucket        = "silvercare-cloudtrail-${data.aws_caller_identity.current.account_id}"
  force_destroy = false

  # The only copy of the audit record.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Log and digest files expire 365 days after they were written.
resource "aws_s3_bucket_lifecycle_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  rule {
    id     = "expire-logs-after-365-days"
    status = "Enabled"

    filter {}

    expiration {
      days = 365
    }
  }

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# CloudTrail (a service principal) needs an Allow; both statements are limited to this
# trail by aws:SourceArn. The TLS Deny uses s3:* and principal *: denying every action
# from every caller without TLS is narrower access, not broader.
data "aws_iam_policy_document" "cloudtrail_bucket" {
  statement {
    sid       = "CloudTrailAclCheck"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.cloudtrail.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.cloudtrail_arn]
    }
  }

  statement {
    sid       = "CloudTrailWrite"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.cloudtrail.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.cloudtrail_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
  }

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.cloudtrail.arn,
      "${aws_s3_bucket.cloudtrail.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  policy = data.aws_iam_policy_document.cloudtrail_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.cloudtrail]
}

# ---------------------------------------------------------------------------------------
# Trail
# ---------------------------------------------------------------------------------------

resource "aws_cloudtrail" "audit" {
  #checkov:skip=CKV_AWS_35:SSE-S3 instead of a customer-managed KMS key (~US$1/month plus request charges).
  #checkov:skip=CKV_AWS_252:No SNS topic: nothing consumes new-file notifications.
  #checkov:skip=CKV2_AWS_10:No CloudWatch Logs delivery: ingestion costs, and all 10 free alarms are used, so nothing would alarm on it yet.
  name                          = local.cloudtrail_name
  s3_bucket_name                = aws_s3_bucket.cloudtrail.id
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true
  enable_logging                = true

  advanced_event_selector {
    name = "Management events"

    field_selector {
      field  = "eventCategory"
      equals = ["Management"]
    }
  }

  advanced_event_selector {
    name = "Registry writes"

    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }

    field_selector {
      field  = "resources.type"
      equals = ["AWS::DynamoDB::Table"]
    }

    field_selector {
      field  = "resources.ARN"
      equals = [module.registry_table.arn]
    }

    field_selector {
      field  = "readOnly"
      equals = ["false"]
    }
  }

  advanced_event_selector {
    name = "Terraform state"

    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }

    field_selector {
      field  = "resources.type"
      equals = ["AWS::S3::Object"]
    }

    field_selector {
      field       = "resources.ARN"
      starts_with = ["arn:aws:s3:::silvercare-tfstate-${data.aws_caller_identity.current.account_id}/"]
    }
  }

  # CloudTrail checks it may write to the bucket when the trail is created.
  depends_on = [aws_s3_bucket_policy.cloudtrail]
}
