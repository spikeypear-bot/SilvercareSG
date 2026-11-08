# Nightly results (docs/features/nightly-results.md, the team slide's loadResults). After
# every successful asynchronous silvercare-summaries run (the chained daily run and its
# retries), Lambda invokes silvercare-results through summaries' on-success destination
# (summaries.tf). It reads that Singapore date's summaries from summaries/date=<D>/ in
# the history bucket and writes one item per resident to silvercare-results
# (dynamodb.tf; docs/data-model.md, pattern 30), which GET /residents/{resident_id}/nightly
# serves to doctors (api.tf; pattern 31).
#
# Data: per-resident nightly health statistics and the thresholds used, keyed by the
# opaque resident_id, no names. Retention: items expire 30 nights after their own (TTL
# expires_at), recoverable through point-in-time recovery for 35 days; the S3 summaries
# stay the record (lifetime + 6 years).
#
# Cost: within free allowances (about 20 S3 GETs and 20 PutItems a day, one short Lambda
# run) plus 2 alarms (8 of the free 10). No fixed fees.

locals {
  results_function_name = "silvercare-results"
}

# Kept under .terraform/ (git-ignored); CI builds it during plan and applies the saved
# plan in the same job.
data "archive_file" "results" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/results/src"
  output_path = "${path.module}/.terraform/build/results.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function does with AWS (criterion 14): lists summaries/date=<D>/ in the history
# bucket, reads each object there, and writes each valid summary with an unconditional
# PutItem on the Results table. No S3 writes or deletes, no telemetry/ or alerts/, no
# other table, no reads of the table, nothing published. The module adds the log group
# permissions.
data "aws_iam_policy_document" "results" {
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

  statement {
    sid       = "WriteResults"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem"]
    resources = [module.results_table.arn]
  }
}

module "results" {
  source = "./modules/python-lambda"

  function_name = local.results_function_name
  description   = "Daily, after summaries: copies a Singapore date's resident summaries from S3 into the silvercare-results table."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.results.output_path
  zip_hash      = data.archive_file.results.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  timeout       = 120

  environment = {
    HISTORY_BUCKET          = aws_s3_bucket.history.bucket
    RESULTS_TABLE           = module.results_table.name
    POWERTOOLS_SERVICE_NAME = local.results_function_name
  }

  policy_json        = data.aws_iam_policy_document.results.json
  log_retention_days = 30

  # No on_failure_destination: nothing is lost, because the S3 summaries stay and any
  # date can be re-run. The retry settings are in aws_lambda_function_event_invoke_config.results.
}

# 2 retries and an hour's event age, as clean and summaries (criterion 9). No invoke
# permission is needed for the on-success destination: Lambda uses summaries' execution
# role, which may invoke this function (summaries.tf).
resource "aws_lambda_function_event_invoke_config" "results" {
  function_name                = module.results.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

# ---------------------------------------------------------------------------------------
# Alarms (criterion 10)
# ---------------------------------------------------------------------------------------

# The handed-off run and its 2 retries all failed (or a failing manual run), including a
# run that skipped invalid objects (it raises after loading the valid ones).
resource "aws_cloudwatch_metric_alarm" "results_errors" {
  alarm_name          = "silvercare-results-errors"
  alarm_description   = "silvercare-results had at least 3 errors in one hour (the run after summaries and its 2 retries, or a failing manual run): that Singapore night may be missing from doctors' nightly summaries. Check the function's logs (skipped objects are logged by key only), fix the cause (re-run summaries with {\"date\": \"yyyy-mm-dd\"} if its objects are wrong), then re-run results with {\"date\": \"yyyy-mm-dd\"}."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = module.results.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
  ok_actions          = [aws_sns_topic.ops.arn]
}

# Summaries succeeded but Lambda couldn't hand off to results: without this, a night
# would be missing from the portal with no email.
resource "aws_cloudwatch_metric_alarm" "summaries_destination_failures" {
  alarm_name          = "silvercare-summaries-destination-failures"
  alarm_description   = "silvercare-summaries finished but Lambda could not invoke its on-success destination silvercare-results: that Singapore night is missing from doctors' nightly summaries. Check the summaries role's lambda:InvokeFunction permission and that silvercare-results exists, then run results with {\"date\": \"yyyy-mm-dd\"} (the Singapore date summaries processed)."
  namespace           = "AWS/Lambda"
  metric_name         = "DestinationDeliveryFailures"
  dimensions          = { FunctionName = module.summaries.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
  ok_actions          = [aws_sns_topic.ops.arn]
}
