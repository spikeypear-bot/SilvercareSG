# shadow-sync: the hourly reconcile of every wearable's device shadow
# (docs/features/device-alert-limits.md, criteria 6-12). An EventBridge schedule invokes
# silvercare-shadow-sync every hour (it can also be invoked by hand with {}). Each run
# reads every device (registry by-type, as pattern 4) and the wearables' residents'
# THRESHOLDS items (BatchGetItem, as pattern 29), computes each wearable's limits, and
# writes `state.desired.limits` to the wearable's classic shadow only where it differs
# (or there is no shadow). api-thresholds writes the shadows at once on a save
# (infra/api.tf); this run catches device moves, seed runs, hand edits, and failed
# writes.
#
# Failures: one wearable failing doesn't stop the others; the run then ends with an
# error. No async retries and no DLQ or on-failure destination: nothing is lost, the
# next run (an hour later) recomputes everything, and without retries one failed run
# can't spill into the next clock hour, so silvercare-shadow-sync-errors fires only
# after 2 failed runs in a row.
#
# Data: the function reads device IDs, assignments, and heart rate / SpO2 thresholds; a
# shadow holds only the derived limits. It logs counts and failed device IDs with error
# codes, never limits, thresholds, or resident IDs.
#
# Cost: about US$0. 720 runs a month (Lambda free tier), the schedule (free), about 20
# GetThingShadow per run (~15,000 a month, inside the 225,000 free IoT Device Shadow
# operations; then US$1.25 per million), burst reads within the provisioned capacity,
# 2 more alarms (10 of the 10 free; an 11th would cost US$0.10/month).

locals {
  shadow_sync_function_name = "silvercare-shadow-sync"
}

data "archive_file" "shadow_sync" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/shadow-sync/src"
  output_path = "${path.module}/.terraform/build/shadow-sync.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function does with AWS (criterion 12): an eventually consistent, paginated
# Query of registry by-type naming pk, device_type, resident_id (never display_name,
# home_id, or photo_key: IAM enforces the attribute list, as for api-devices); a
# BatchGetItem of THRESHOLDS# items naming pk, hr_bpm, spo2_pct; GetThingShadow and
# UpdateThingShadow on wearable things only. No DynamoDB writes, no shadow deletes, no
# bedroom shadows. The module adds the log group permissions.
data "aws_iam_policy_document" "shadow_sync" {
  statement {
    sid       = "RegistryByTypeIndex"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = ["${module.registry_table.arn}/index/by-type"]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["pk", "sk", "entity_type", "device_type", "resident_id"]
    }

    condition {
      test     = "Null"
      variable = "dynamodb:Attributes"
      values   = ["false"]
    }

    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
  }

  statement {
    sid       = "ThresholdsRead"
    effect    = "Allow"
    actions   = ["dynamodb:BatchGetItem"]
    resources = [module.registry_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["THRESHOLDS#*"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["pk", "sk", "hr_bpm", "spo2_pct"]
    }

    condition {
      test     = "Null"
      variable = "dynamodb:Attributes"
      values   = ["false"]
    }

    condition {
      test     = "StringEqualsIfExists"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
  }

  statement {
    sid       = "WearableShadows"
    effect    = "Allow"
    actions   = ["iot:GetThingShadow", "iot:UpdateThingShadow"]
    resources = [local.wearable_things_arn]
  }
}

module "shadow_sync" {
  source = "./modules/python-lambda"

  function_name = local.shadow_sync_function_name
  description   = "Every hour: writes each wearable's device alert limits to its shadow where they differ."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.shadow_sync.output_path
  zip_hash      = data.archive_file.shadow_sync.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  # A run is one Query, one BatchGetItem, and about 20 shadow reads (and rare writes),
  # one after another: a few seconds. 120 s covers slow shadow calls (2 s timeouts).
  timeout = 120

  environment = {
    REGISTRY_TABLE          = module.registry_table.name
    IOT_DATA_ENDPOINT       = data.aws_iot_endpoint.data.endpoint_address
    POWERTOOLS_SERVICE_NAME = local.shadow_sync_function_name
  }

  policy_json        = data.aws_iam_policy_document.shadow_sync.json
  log_retention_days = 30
}

# No async retries: the next hourly run recomputes everything (see the header).
resource "aws_lambda_function_event_invoke_config" "shadow_sync" {
  function_name                = module.shadow_sync.function_name
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 600
}

# ---------------------------------------------------------------------------------------
# Schedule (criterion 6)
# ---------------------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "shadow_sync" {
  name                = local.shadow_sync_function_name
  description         = "Runs the silvercare-shadow-sync Lambda every hour (wearables' device alert limits)."
  schedule_expression = "rate(1 hour)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "shadow_sync" {
  rule      = aws_cloudwatch_event_rule.shadow_sync.name
  target_id = "shadow-sync"
  arn       = module.shadow_sync.function_arn
}

# Only this rule, in this account, may invoke the function.
resource "aws_lambda_permission" "shadow_sync_schedule" {
  statement_id   = "AllowEventBridgeSchedule"
  action         = "lambda:InvokeFunction"
  function_name  = module.shadow_sync.function_name
  principal      = "events.amazonaws.com"
  source_arn     = aws_cloudwatch_event_rule.shadow_sync.arn
  source_account = data.aws_caller_identity.current.account_id
}

# ---------------------------------------------------------------------------------------
# Alarms (criterion 10; not-running from the lambda-python skill for scheduled functions)
# ---------------------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "shadow_sync_errors" {
  alarm_name          = "silvercare-shadow-sync-errors"
  alarm_description   = "silvercare-shadow-sync had errors in 2 consecutive 1-hour periods (2 failed runs in a row): wearables may be using old alert limits after a device move or a failed thresholds save. Check the function's logs (failed device IDs and error codes only) and fix the cause; the next successful run catches up on its own."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = module.shadow_sync.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
  ok_actions          = [aws_sns_topic.ops.arn]
}

# The schedule stopped invoking the function (rule disabled or deleted, throttling): no
# Invocations datapoint is the same as zero, so missing data breaches.
resource "aws_cloudwatch_metric_alarm" "shadow_sync_not_running" {
  alarm_name          = "silvercare-shadow-sync-not-running"
  alarm_description   = "silvercare-shadow-sync was not invoked in 2 consecutive 1-hour periods: wearables' alert limits are not being reconciled (saves still write them at once). Check that the silvercare-shadow-sync EventBridge rule exists and is enabled and that its target and invoke permission are in place."
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  dimensions          = { FunctionName = module.shadow_sync.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
  ok_actions          = [aws_sns_topic.ops.arn]
}
