# Nightly anomaly score (docs/features/nightly-anomaly.md). After every successful
# asynchronous silvercare-results run, Lambda invokes silvercare-anomaly-score through
# results' on-success destination (results.tf). It standardises each resident's night
# against their own nights 8-37 days earlier and starts a SageMaker batch transform on
# the current Random Cut Forest model. A weekly schedule runs silvercare-anomaly-train,
# which starts a training job. silvercare-anomaly-collect handles SageMaker's job state
# changes (EventBridge): it creates the model and its calibration transform, writes the
# cut-off, and writes each night's scores to scores/ and the Results table.
#
# Data: per-resident z-scores, anomaly scores, and explanations (health-derived), keyed
# by the opaque resident_id, no names. Retention: input/ and output/ 30 days; scores/
# and models/ lifetime + 6 years (no expiry), as derived records that informed care.
#
# Cost (rough, to check): ~US$0.50 a month of ml.m5.large job time (about 34 short jobs
# a month), no endpoint, nothing billed while idle. No alarms (user decision).

locals {
  anomaly_score_function_name   = "silvercare-anomaly-score"
  anomaly_train_function_name   = "silvercare-anomaly-train"
  anomaly_collect_function_name = "silvercare-anomaly-collect"
  anomaly_sagemaker_role_name   = "silvercare-sagemaker-anomaly"
  sagemaker_arn_prefix          = "arn:aws:sagemaker:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}"
}

# AWS's built-in Random Cut Forest image for this region.
data "aws_sagemaker_prebuilt_ecr_image" "rcf" {
  repository_name = "randomcutforest"
}

# ---------------------------------------------------------------------------------------
# ML bucket (criterion 22)
# ---------------------------------------------------------------------------------------

resource "aws_s3_bucket" "ml" {
  #checkov:skip=CKV_AWS_21:Versioning off, as the history bucket: a delete really deletes, e.g. for a PDPA erasure request.
  #checkov:skip=CKV_AWS_18:No access logging (cost), as the history bucket.
  #checkov:skip=CKV_AWS_144:No cross-region replication. The data must stay in ap-southeast-1 (Singapore).
  #checkov:skip=CKV_AWS_145:SSE-S3 (AWS-managed key), as the history bucket. A customer-managed KMS key costs ~US$1/month plus requests.
  #checkov:skip=CKV2_AWS_62:No event notifications. Job completion comes from SageMaker's EventBridge events.
  bucket        = "silvercare-ml-${data.aws_caller_identity.current.account_id}"
  force_destroy = false

  # Holds the kept scores/ records and the models that produced them.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "ml" {
  bucket = aws_s3_bucket.ml.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "ml" {
  bucket = aws_s3_bucket.ml.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ml" {
  bucket = aws_s3_bucket.ml.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# input/ and output/ hold per-resident z-scores and raw job output: 30 days. scores/
# and models/ are kept (lifetime + 6 years): no expiry.
resource "aws_s3_bucket_lifecycle_configuration" "ml" {
  bucket = aws_s3_bucket.ml.id

  rule {
    id     = "expire-input-after-30-days"
    status = "Enabled"

    filter {
      prefix = "input/"
    }

    expiration {
      days = 30
    }
  }

  rule {
    id     = "expire-output-after-30-days"
    status = "Enabled"

    filter {
      prefix = "output/"
    }

    expiration {
      days = 30
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

# Deny statements: s3:* and principal * are intentional (narrower, not broader). Access
# itself comes from IAM policies only.
data "aws_iam_policy_document" "ml_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.ml.arn,
      "${aws_s3_bucket.ml.arn}/*",
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

  # scores/ is written and replaced by anomaly-collect only. Tagging too: the
  # resident_id tag is how one resident's scores are found for erasure. Like summaries/,
  # this blocks admins: erasure needs this statement changed first.
  statement {
    sid    = "DenyScoreWritesFromOthers"
    effect = "Deny"
    actions = [
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:PutObjectTagging",
      "s3:DeleteObjectTagging",
    ]
    resources = ["${aws_s3_bucket.ml.arn}/scores/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "aws:PrincipalArn"
      values   = [module.anomaly_collect.role_arn]
    }
  }
}

resource "aws_s3_bucket_policy" "ml" {
  bucket = aws_s3_bucket.ml.id
  policy = data.aws_iam_policy_document.ml_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.ml]
}

# ---------------------------------------------------------------------------------------
# SageMaker execution role (training and transform jobs)
# ---------------------------------------------------------------------------------------

# aws:SourceAccount only: the role is passed to training jobs, models, and transform
# jobs, whose aws:SourceArn values differ by job type.
data "aws_iam_policy_document" "sagemaker_anomaly_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

  }
}

resource "aws_iam_role" "sagemaker_anomaly" {
  name               = local.anomaly_sagemaker_role_name
  assume_role_policy = data.aws_iam_policy_document.sagemaker_anomaly_assume.json
}

# Training reads input/train/, writes models/. Transform reads input/score/ and
# input/train/ (calibration) and the model, writes output/. Logs go to SageMaker's own
# log groups (created below with a retention).
data "aws_iam_policy_document" "sagemaker_anomaly" {
  statement {
    sid       = "ListJobPrefixes"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.ml.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["input/score/*", "input/train/*", "models/*"]
    }
  }

  statement {
    sid    = "ReadJobInput"
    effect = "Allow"
    actions = [
      "s3:GetObject",
    ]
    resources = [
      "${aws_s3_bucket.ml.arn}/input/score/*",
      "${aws_s3_bucket.ml.arn}/input/train/*",
      "${aws_s3_bucket.ml.arn}/models/*",
    ]
  }

  statement {
    sid     = "WriteJobOutput"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.ml.arn}/models/*",
      "${aws_s3_bucket.ml.arn}/output/*",
    ]
  }

  statement {
    sid    = "JobLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = [
      "${aws_cloudwatch_log_group.sagemaker_training.arn}:*",
      "${aws_cloudwatch_log_group.sagemaker_transform.arn}:*",
    ]
  }

  # PutMetricData supports no resource (AWS: only "*"); narrowed to SageMaker's namespace.
  statement {
    sid       = "JobMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringLike"
      variable = "cloudwatch:namespace"
      values   = ["/aws/sagemaker/*"]
    }
  }
}

resource "aws_iam_role_policy" "sagemaker_anomaly" {
  name   = local.anomaly_sagemaker_role_name
  role   = aws_iam_role.sagemaker_anomaly.id
  policy = data.aws_iam_policy_document.sagemaker_anomaly.json
}

# SageMaker writes job logs here; without these, it creates them with no expiry.
resource "aws_cloudwatch_log_group" "sagemaker_training" {
  #checkov:skip=CKV_AWS_158:CloudWatch Logs' default encryption, as every log group here (no customer-managed key, cost).
  #checkov:skip=CKV_AWS_338:30 days, as the Lambda log groups: the logs hold no health data.
  name              = "/aws/sagemaker/TrainingJobs"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "sagemaker_transform" {
  #checkov:skip=CKV_AWS_158:CloudWatch Logs' default encryption, as every log group here (no customer-managed key, cost).
  #checkov:skip=CKV_AWS_338:30 days, as the Lambda log groups: the logs hold no health data.
  name              = "/aws/sagemaker/TransformJobs"
  retention_in_days = 30
}

# ---------------------------------------------------------------------------------------
# Lambdas (one zip, three functions, three roles)
# ---------------------------------------------------------------------------------------

data "archive_file" "anomaly" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/anomaly/src"
  output_path = "${path.module}/.terraform/build/anomaly.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# Reading summaries for D and its baseline dates from the history bucket (score, train).
data "aws_iam_policy_document" "anomaly_read_summaries" {
  statement {
    sid       = "ListSummaryPrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.history.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.summaries_prefix}/*"]
    }
  }

  statement {
    sid       = "ReadSummaryObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.history.arn}/${local.summaries_prefix}/*"]
  }
}

# score: reads summaries and models/current.json, writes input/score/ and
# input/score-meta/, starts anomaly-score-* transform jobs, invokes collect directly when
# nothing needs a job.
data "aws_iam_policy_document" "anomaly_score" {
  source_policy_documents = [data.aws_iam_policy_document.anomaly_read_summaries.json]

  statement {
    sid       = "ReadCurrentModel"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.ml.arn}/models/current.json"]
  }

  # Without ListBucket a missing current.json returns 403 instead of NoSuchKey.
  statement {
    sid       = "ListCurrentModel"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.ml.arn]

    condition {
      test     = "StringEquals"
      variable = "s3:prefix"
      values   = ["models/current.json"]
    }
  }

  statement {
    sid     = "WriteScoreInput"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.ml.arn}/input/score/*",
      "${aws_s3_bucket.ml.arn}/input/score-meta/*",
    ]
  }

  statement {
    sid       = "StartScoring"
    effect    = "Allow"
    actions   = ["sagemaker:CreateTransformJob"]
    resources = ["${local.sagemaker_arn_prefix}:transform-job/anomaly-score-*"]
  }

  statement {
    sid       = "InvokeCollect"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [module.anomaly_collect.function_arn]
  }
}

# train: reads summaries, writes input/train/, starts anomaly-train-* training jobs as
# the SageMaker role.
data "aws_iam_policy_document" "anomaly_train" {
  source_policy_documents = [data.aws_iam_policy_document.anomaly_read_summaries.json]

  statement {
    sid       = "WriteTrainInput"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.ml.arn}/input/train/*"]
  }

  statement {
    sid       = "StartTraining"
    effect    = "Allow"
    actions   = ["sagemaker:CreateTrainingJob"]
    resources = ["${local.sagemaker_arn_prefix}:training-job/anomaly-train-*"]
  }

  statement {
    sid       = "PassSageMakerRole"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.sagemaker_anomaly.arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["sagemaker.amazonaws.com"]
    }
  }
}

# collect: creates models and calibration jobs, reads job output and the sidecar,
# writes cutoff.json / current.json, replaces scores/<date>/, updates Results items.
data "aws_iam_policy_document" "anomaly_collect" {
  statement {
    sid       = "CreateModel"
    effect    = "Allow"
    actions   = ["sagemaker:CreateModel"]
    resources = ["${local.sagemaker_arn_prefix}:model/anomaly-train-*"]
  }

  statement {
    sid       = "StartCalibration"
    effect    = "Allow"
    actions   = ["sagemaker:CreateTransformJob"]
    resources = ["${local.sagemaker_arn_prefix}:transform-job/anomaly-cal-*"]
  }

  statement {
    sid       = "PassSageMakerRole"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.sagemaker_anomaly.arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["sagemaker.amazonaws.com"]
    }
  }

  statement {
    sid     = "ReadJobFiles"
    effect  = "Allow"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.ml.arn}/input/score-meta/*",
      "${aws_s3_bucket.ml.arn}/output/*",
    ]
  }

  statement {
    sid     = "WriteModelState"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.ml.arn}/models/*/cutoff.json",
      "${aws_s3_bucket.ml.arn}/models/current.json",
    ]
  }

  statement {
    sid       = "ListScores"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.ml.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["scores/*"]
    }
  }

  # PutObjectTagging: PutObject with the resident_id tag needs it.
  statement {
    sid    = "WriteScores"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:PutObjectTagging",
      "s3:DeleteObject",
    ]
    resources = ["${aws_s3_bucket.ml.arn}/scores/*"]
  }

  statement {
    sid       = "UpdateResults"
    effect    = "Allow"
    actions   = ["dynamodb:UpdateItem"]
    resources = [module.results_table.arn]
  }
}

module "anomaly_score" {
  source = "./modules/python-lambda"

  function_name = local.anomaly_score_function_name
  description   = "Daily, after results: builds last night's z-scores and starts the SageMaker anomaly scoring job."
  handler       = "handler.score_handler"
  zip_path      = data.archive_file.anomaly.output_path
  zip_hash      = data.archive_file.anomaly.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  timeout       = 120

  environment = {
    HISTORY_BUCKET          = aws_s3_bucket.history.bucket
    ML_BUCKET               = aws_s3_bucket.ml.bucket
    COLLECT_FUNCTION        = module.anomaly_collect.function_name
    POWERTOOLS_SERVICE_NAME = local.anomaly_score_function_name
  }

  policy_json        = data.aws_iam_policy_document.anomaly_score.json
  log_retention_days = 30
}

module "anomaly_train" {
  source = "./modules/python-lambda"

  function_name = local.anomaly_train_function_name
  description   = "Weekly: builds 90 days of z-scores and starts the SageMaker Random Cut Forest training job."
  handler       = "handler.train_handler"
  zip_path      = data.archive_file.anomaly.output_path
  zip_hash      = data.archive_file.anomaly.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  timeout       = 300

  environment = {
    HISTORY_BUCKET          = aws_s3_bucket.history.bucket
    ML_BUCKET               = aws_s3_bucket.ml.bucket
    RCF_IMAGE               = data.aws_sagemaker_prebuilt_ecr_image.rcf.registry_path
    SAGEMAKER_ROLE_ARN      = aws_iam_role.sagemaker_anomaly.arn
    POWERTOOLS_SERVICE_NAME = local.anomaly_train_function_name
  }

  policy_json        = data.aws_iam_policy_document.anomaly_train.json
  log_retention_days = 30
}

module "anomaly_collect" {
  source = "./modules/python-lambda"

  function_name = local.anomaly_collect_function_name
  description   = "On SageMaker job state changes: creates anomaly models, writes the cut-off, and writes nightly scores to S3 and silvercare-results."
  handler       = "handler.collect_handler"
  zip_path      = data.archive_file.anomaly.output_path
  zip_hash      = data.archive_file.anomaly.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  timeout       = 60

  environment = {
    ML_BUCKET               = aws_s3_bucket.ml.bucket
    RESULTS_TABLE           = module.results_table.name
    RCF_IMAGE               = data.aws_sagemaker_prebuilt_ecr_image.rcf.registry_path
    SAGEMAKER_ROLE_ARN      = aws_iam_role.sagemaker_anomaly.arn
    POWERTOOLS_SERVICE_NAME = local.anomaly_collect_function_name
  }

  policy_json        = data.aws_iam_policy_document.anomaly_collect.json
  log_retention_days = 30
}

# 2 retries and an hour's event age, as clean, summaries, and results (criterion 23).
# No on-failure destinations or alarms (user decision): every step can be re-run.
resource "aws_lambda_function_event_invoke_config" "anomaly_score" {
  function_name                = module.anomaly_score.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

resource "aws_lambda_function_event_invoke_config" "anomaly_train" {
  function_name                = module.anomaly_train.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

resource "aws_lambda_function_event_invoke_config" "anomaly_collect" {
  function_name                = module.anomaly_collect.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

# ---------------------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------------------

# Mondays 10:00 SGT = 02:00 UTC, after the nightly chain (criterion 9).
resource "aws_cloudwatch_event_rule" "anomaly_train" {
  name                = local.anomaly_train_function_name
  description         = "Retrains the anomaly model weekly, Mondays 10:00 SGT."
  schedule_expression = "cron(0 2 ? * MON *)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "anomaly_train" {
  rule      = aws_cloudwatch_event_rule.anomaly_train.name
  target_id = "anomaly-train"
  arn       = module.anomaly_train.function_arn
}

resource "aws_lambda_permission" "anomaly_train_schedule" {
  statement_id   = "AllowEventBridgeSchedule"
  action         = "lambda:InvokeFunction"
  function_name  = module.anomaly_train.function_name
  principal      = "events.amazonaws.com"
  source_arn     = aws_cloudwatch_event_rule.anomaly_train.arn
  source_account = data.aws_caller_identity.current.account_id
}

# Only our own jobs' end states reach collect.
resource "aws_cloudwatch_event_rule" "anomaly_jobs" {
  name        = local.anomaly_collect_function_name
  description = "SageMaker anomaly training and transform jobs that ended."
  event_pattern = jsonencode({
    source = ["aws.sagemaker"]
    "detail-type" = [
      "SageMaker Training Job State Change",
      "SageMaker Transform Job State Change",
    ]
    "$or" = [
      {
        detail = {
          TrainingJobName   = [{ prefix = "anomaly-train-" }]
          TrainingJobStatus = ["Completed", "Failed", "Stopped"]
        }
      },
      {
        detail = {
          TransformJobName   = [{ prefix = "anomaly-" }]
          TransformJobStatus = ["Completed", "Failed", "Stopped"]
        }
      },
    ]
  })
}

resource "aws_cloudwatch_event_target" "anomaly_jobs" {
  rule      = aws_cloudwatch_event_rule.anomaly_jobs.name
  target_id = "anomaly-collect"
  arn       = module.anomaly_collect.function_arn
}

resource "aws_lambda_permission" "anomaly_jobs" {
  statement_id   = "AllowSageMakerJobEvents"
  action         = "lambda:InvokeFunction"
  function_name  = module.anomaly_collect.function_name
  principal      = "events.amazonaws.com"
  source_arn     = aws_cloudwatch_event_rule.anomaly_jobs.arn
  source_account = data.aws_caller_identity.current.account_id
}
