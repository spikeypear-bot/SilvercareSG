# Ingest, history path (docs/features/ingest-history.md; History path in
# docs/decisions/0001-iot-rule-routing.md): every ingest IoT rule sends each message it
# matches to the silvercare-history queue (second action, see ingest.tf). The history
# Lambda reads the queue in batches of up to 1,000 messages or 5 minutes and writes one
# gzipped JSON Lines file per message type and UTC day to the silvercare-history bucket.
# Replaces Firehose and Kinesis, which need AWS's paid plan.
#
# The queues and the bucket hold every raw message (health readings, falls, raw
# acceleration data), invalid ones too, linked to device IDs. Retention: queue 4 days,
# DLQ 14 days, bucket no expiry until Open decision #4 (retention) is made.
#
# Cost: pay per use only (IoT rule actions, SQS requests, S3 PUTs and storage, Lambda),
# about US$1/month with the simulator running 24/7, US$0 when it is stopped. No fixed fees.

locals {
  history_function_name = "silvercare-history"
}

# ---------------------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------------------

# Messages that fail 5 receives (the Lambda reported them as failed or timed out).
resource "aws_sqs_queue" "history_dlq" {
  name                      = "silvercare-history-dlq"
  sqs_managed_sse_enabled   = true
  message_retention_seconds = 1209600 # 14 days, the SQS maximum
}

resource "aws_sqs_queue" "history" {
  name                       = "silvercare-history"
  sqs_managed_sse_enabled    = true
  visibility_timeout_seconds = 660    # 6 x the Lambda's 60 s timeout + the 300 s batching window (AWS's guidance for event sources)
  message_retention_seconds  = 345600 # 4 days

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.history_dlq.arn
    maxReceiveCount     = 5
  })
}

# Only silvercare-history may use the DLQ. A separate resource, because the source queue
# already depends on the DLQ (its redrive policy names it).
resource "aws_sqs_queue_redrive_allow_policy" "history_dlq" {
  queue_url = aws_sqs_queue.history_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.history.arn]
  })
}

# Deny statements: sqs:* and principal * are intentional here. Denying every action from
# every caller without TLS is narrower access, not broader.
data "aws_iam_policy_document" "history_queue" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [aws_sqs_queue.history.arn]

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

resource "aws_sqs_queue_policy" "history" {
  queue_url = aws_sqs_queue.history.id
  policy    = data.aws_iam_policy_document.history_queue.json
}

data "aws_iam_policy_document" "history_dlq" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [aws_sqs_queue.history_dlq.arn]

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

resource "aws_sqs_queue_policy" "history_dlq" {
  queue_url = aws_sqs_queue.history_dlq.id
  policy    = data.aws_iam_policy_document.history_dlq.json
}

# ---------------------------------------------------------------------------------------
# Bucket
# ---------------------------------------------------------------------------------------

resource "aws_s3_bucket" "history" {
  #checkov:skip=CKV_AWS_21:Versioning off by the user's decision (spec criterion 6): a delete really deletes, e.g. for a PDPA erasure request.
  #checkov:skip=CKV_AWS_18:No access logging (cost: extra requests and storage), and the logs would need their own retention decision first.
  #checkov:skip=CKV_AWS_144:No cross-region replication. The data must stay in ap-southeast-1 (Singapore), and replication would double storage cost.
  #checkov:skip=CKV_AWS_145:SSE-S3 (AWS-managed key) as the spec requires. A customer-managed KMS key costs ~US$1/month plus request charges on every write.
  #checkov:skip=CKV2_AWS_62:No event notifications. Nothing consumes events from this bucket; Lake Formation reads it later on demand.
  bucket        = "silvercare-history-${data.aws_caller_identity.current.account_id}"
  force_destroy = false

  # Holds the only copy of every device message (no versioning, no replication).
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "history" {
  bucket = aws_s3_bucket.history.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "history" {
  bucket = aws_s3_bucket.history.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "history" {
  bucket = aws_s3_bucket.history.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# One rule only. No expiry: how long history is kept is Open decision #4 (retention).
resource "aws_s3_bucket_lifecycle_configuration" "history" {
  bucket = aws_s3_bucket.history.id

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# Deny statement: s3:* and principal * are intentional here. Denying every action from
# every caller without TLS is narrower access, not broader. Access itself is granted by
# IAM policies only (the history Lambda's role, the developer policy).
data "aws_iam_policy_document" "history_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.history.arn,
      "${aws_s3_bucket.history.arn}/*",
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

resource "aws_s3_bucket_policy" "history" {
  bucket = aws_s3_bucket.history.id
  policy = data.aws_iam_policy_document.history_bucket.json

  # Apply after the public access block so the policy is never evaluated without it.
  depends_on = [aws_s3_bucket_public_access_block.history]
}

# ---------------------------------------------------------------------------------------
# Lambda
# ---------------------------------------------------------------------------------------

# Kept under .terraform/ (git-ignored); CI builds it during plan and applies the saved
# plan in the same job.
data "archive_file" "history" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/history/src"
  output_path = "${path.module}/.terraform/build/history.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function does with AWS: the event source mapping polls silvercare-history with
# the function's role (receive, delete, read attributes, change visibility), and the code
# writes new objects under telemetry/ with PutObject. Nothing else: no reads, no deletes,
# no alerts/ prefix, no bucket-level actions, no DynamoDB. The module adds the log group
# permissions.
data "aws_iam_policy_document" "history_lambda" {
  statement {
    sid    = "HistoryQueue"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ChangeMessageVisibility",
    ]
    resources = [aws_sqs_queue.history.arn]
  }

  statement {
    sid       = "HistoryTelemetryObjects"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.history.arn}/telemetry/*"]
  }
}

module "history" {
  source = "./modules/python-lambda"

  function_name = local.history_function_name
  description   = "Writes the silvercare-history queue to S3 as gzipped JSON Lines, one file per message type and UTC day per batch."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.history.output_path
  zip_hash      = data.archive_file.history.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 256
  timeout       = 60

  environment = {
    HISTORY_BUCKET          = aws_s3_bucket.history.bucket
    POWERTOOLS_SERVICE_NAME = local.history_function_name
  }

  policy_json        = data.aws_iam_policy_document.history_lambda.json
  log_retention_days = 30

  # No on_failure_destination: the function is fed by the event source mapping below,
  # not invoked asynchronously. Messages that keep failing go to silvercare-history-dlq.
}

# Batches of up to 1,000 messages or 5 minutes, whichever comes first. The function
# reports failed records by messageId, so only those are retried.
resource "aws_lambda_event_source_mapping" "history" {
  event_source_arn                   = aws_sqs_queue.history.arn
  function_name                      = module.history.function_arn
  batch_size                         = 1000
  maximum_batching_window_in_seconds = 300
  function_response_types            = ["ReportBatchItemFailures"]
  enabled                            = true
}
