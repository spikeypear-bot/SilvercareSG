# Watchdog: device problems (docs/features/watchdog.md, Track A4a) and possible fall at
# night (docs/features/possible-fall.md, Track A4b: also reads the newest bed and motion
# entries from latest by-message, and TEST_MODE items from registry by-type); watchdog
# in docs/decisions/0001-iot-rule-routing.md. An EventBridge schedule rule invokes the
# watchdog Lambda every 5 minutes. Each run reads every assigned device (registry
# by-type), every device's newest status (latest by-message), and every open alert
# (alerts open), plus worn_false_since for wearables off the wrist (BatchGetItem on
# MSG#status items), then sends device_offline / battery_low / not_worn alerts and
# system resolves to the silvercare-alerts queue (alerts.tf), where alert-processor
# stores them and emails the caregivers and the ops team. If 5 or more devices go
# offline in one run, the watchdog itself emails one summary to silvercare-ops-devices
# (alerts.tf, encrypted; device-problem emails only).
#
# Failures: any AWS error fails the run. Lambda retries the async invocation at most
# twice (about 1 and 3 minutes later), and events older than 4 minutes are dropped, so a
# retry never runs after the next scheduled run has started. No DLQ or on-failure
# destination: nothing is lost, because every run recomputes everything. Two alarms
# email silvercare-ops (the alarms topic):
# - silvercare-watchdog-errors: Errors >= 1 in 3 consecutive 5-minute periods. The
#   periods follow the clock, not the schedule, so a run and its retries can fall into 2
#   periods: it can fire after 2-3 consecutive failed runs. 1 failed run never alarms.
# - silvercare-watchdog-not-running: no invocation at all in 3 consecutive 5-minute
#   periods (the schedule rule disabled or deleted, or invocations throttled), with
#   missing data counted as breaching.
#
# Data: the function reads device IDs, assignments, heartbeat times, battery level, and
# whether a wearable is worn; it logs only IDs and counts. The summary email carries
# device IDs, opaque resident IDs, and last heartbeat times.
#
# Cost: about US$0. ~8,640 runs a month (Lambda free tier), the EventBridge schedule
# (free), a few RCU per run within the provisioned capacity, 2 more alarms (5 of the 10
# free), rare emails (the new topic uses the free AWS managed key).

locals {
  watchdog_function_name = "silvercare-watchdog"
}

# Kept under .terraform/ (git-ignored); CI builds it during plan and applies the saved
# plan in the same job.
data "archive_file" "watchdog" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/watchdog/src"
  output_path = "${path.module}/.terraform/build/watchdog.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function does with AWS (criterion 17): eventually consistent, paginated
# Queries on the registry by-type index (pattern 4), the latest by-message index
# (pattern 5), and the alerts open index (pattern 9; only pk, sk, open_pk, alert_type,
# device_id, never ack_note); a BatchGetItem of wearables'
# MSG#status items in silvercare-latest (DEVICE# partitions only); SendMessage of alert
# messages and resolves to silvercare-alerts; Publish of the mass-offline summary to
# silvercare-ops-devices. No base-table Query, no writes to any table, no other topic.
# No KMS permissions: the queue uses SSE-SQS, and the topic uses the AWS managed key
# aws/sns, whose key policy already lets SNS use it for principals in this account. The
# module adds the log group permissions.
data "aws_iam_policy_document" "watchdog" {
  statement {
    sid       = "RegistryByTypeIndex"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = ["${module.registry_table.arn}/index/by-type"]
  }

  statement {
    sid       = "LatestByMessageIndex"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = ["${module.latest_table.arn}/index/by-message"]
  }

  # The open index also projects the dashboards' fields, including the nurse's ack_note
  # (dynamodb.tf). The watchdog may read only what read_open_alerts asks for: it must
  # send Select = SPECIFIC_ATTRIBUTES with a ProjectionExpression of these attributes
  # (open_pk is the key condition). AWS's documented pattern for querying only some
  # projected attributes of an index; a Query without a projection is denied.
  statement {
    sid       = "AlertsOpenIndex"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = ["${module.alerts_table.arn}/index/open"]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["pk", "sk", "open_pk", "alert_type", "device_id"]
    }

    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
  }

  statement {
    sid       = "LatestStatusItems"
    effect    = "Allow"
    actions   = ["dynamodb:BatchGetItem"]
    resources = [module.latest_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["DEVICE#*"]
    }
  }

  statement {
    sid       = "AlertsQueue"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.alerts.arn]
  }

  statement {
    sid       = "OpsTopic"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.ops_devices.arn]
  }
}

module "watchdog" {
  source = "./modules/python-lambda"

  function_name = local.watchdog_function_name
  description   = "Every 5 minutes: raises device offline, low battery, not-worn, and possible-fall-at-night alerts and resolves the ones that cleared."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.watchdog.output_path
  zip_hash      = data.archive_file.watchdog.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  # A run takes seconds (six paginated Queries, one BatchGetItem, a few sends); well
  # under the 5-minute schedule.
  timeout = 60

  environment = {
    REGISTRY_TABLE          = module.registry_table.name
    LATEST_TABLE            = module.latest_table.name
    ALERTS_TABLE            = module.alerts_table.name
    ALERTS_QUEUE_URL        = aws_sqs_queue.alerts.url
    OPS_TOPIC_ARN           = aws_sns_topic.ops_devices.arn
    PORTAL_URL              = "https://${aws_cloudfront_distribution.frontend.domain_name}"
    POWERTOOLS_SERVICE_NAME = local.watchdog_function_name
  }

  policy_json        = data.aws_iam_policy_document.watchdog.json
  log_retention_days = 30

  # No on_failure_destination (criterion 15): the next run recomputes everything. The
  # retry settings are in aws_lambda_function_event_invoke_config.watchdog below.
}

# Async retries (criterion 15): Lambda's 2 retries, but an event older than 4 minutes is
# dropped, so a retry never overlaps the next scheduled run (5 minutes). No destination.
resource "aws_lambda_function_event_invoke_config" "watchdog" {
  function_name                = module.watchdog.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 240
}

# ---------------------------------------------------------------------------------------
# Schedule (criterion 18)
# ---------------------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "watchdog" {
  name                = local.watchdog_function_name
  description         = "Runs the silvercare-watchdog Lambda every 5 minutes (device problems)."
  schedule_expression = "rate(5 minutes)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "watchdog" {
  rule      = aws_cloudwatch_event_rule.watchdog.name
  target_id = "watchdog"
  arn       = module.watchdog.function_arn
}

# Only this rule, in this account, may invoke the function.
resource "aws_lambda_permission" "watchdog_schedule" {
  statement_id   = "AllowEventBridgeSchedule"
  action         = "lambda:InvokeFunction"
  function_name  = module.watchdog.function_name
  principal      = "events.amazonaws.com"
  source_arn     = aws_cloudwatch_event_rule.watchdog.arn
  source_account = data.aws_caller_identity.current.account_id
}

# ---------------------------------------------------------------------------------------
# Alarms (criterion 15; not-running added by the user, 2026-11-04)
# ---------------------------------------------------------------------------------------

# The schedule stopped invoking the function (rule disabled or deleted, throttling): no
# Invocations datapoint is the same as zero, so missing data breaches.
resource "aws_cloudwatch_metric_alarm" "watchdog_not_running" {
  alarm_name          = "silvercare-watchdog-not-running"
  alarm_description   = "silvercare-watchdog was not invoked in 3 consecutive 5-minute periods: device offline, low battery, and not-worn alerts are not being raised or resolved. Check that the silvercare-watchdog EventBridge rule exists and is enabled and that its target and invoke permission are in place."
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  dimensions          = { FunctionName = module.watchdog.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
  ok_actions          = [aws_sns_topic.ops.arn]
}

resource "aws_cloudwatch_metric_alarm" "watchdog_errors" {
  alarm_name          = "silvercare-watchdog-errors"
  alarm_description   = "silvercare-watchdog had errors in 3 consecutive 5-minute periods (2-3 failed runs in a row): device offline, low battery, and not-worn alerts are not being raised or resolved. Check the function's logs (error codes only) and fix the cause; the next successful run catches up on its own."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = module.watchdog.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
  ok_actions          = [aws_sns_topic.ops.arn]
}
