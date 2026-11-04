# Ingest, live path (docs/features/ingest-live.md; path 1 of
# docs/decisions/0001-iot-rule-routing.md): three IoT topic rules invoke three Lambdas,
# which validate each message and add it to the device's latest 5 in silvercare-latest.
#
# Each rule also sends every message it matches to the silvercare-history queue (second
# action, docs/features/ingest-history.md; see history.tf). Invalid messages are dropped
# by the Lambdas (logged with a reason code). device-health and telemetry have no
# failed-message queue (user's decision): an invocation that still fails after Lambda's
# two automatic retries is dropped and shows only as an error in the function's logs.
# The raw message is still in S3 history.
#
# critical-alert (was fall-alert; docs/features/vitals-alerts.md, criterion 16a) raises
# a critical alert for every device-raised alert, `fall` and `vitals_alert`
# (docs/features/alerts-core.md; docs/features/vitals-alerts.md): it reads the device's
# assignment in silvercare-registry and sends an alert message to the silvercare-alerts
# queue (alerts.tf) before storing the message. A lost message is a lost alert, so its
# failed async invocations and the silvercare_alert rule's failed actions go to
# silvercare-critical-alert-dlq.
#
# critical-alert also handles the wearable "I'm OK" button, `alert_cancel`
# (docs/features/alert-cancel.md): the same registry lookup, then a `status_update` to
# the silvercare-alerts queue (alert-processor cancels the device's open alerts), then
# the store. No new permissions: it already reads the assignment and sends to the queue.
#
# telemetry also runs the cloud vitals check (docs/features/vitals-alerts.md, criteria
# 7-11): after storing a wearable `vitals`, it reads the device's assignment and the
# resident's THRESHOLDS item in silvercare-registry and sends warnings to the alerts
# queue. telemetry has no failed-message queue (user's decision, 2026-11-02; the
# spec's "Known gap"): a warning raised by an invocation that still fails after
# Lambda's two retries is lost; the raw vitals message is still in S3 history.
#
# Cost: pay per use only (IoT rules, Lambda requests, SQS requests), about US$2/month
# with the simulator running 24/7, US$0 when it is stopped. No fixed fees.

locals {
  # Public AWS Lambda Powertools for Python v3 layer (Python 3.13, arm64), pinned.
  # Published by AWS in account 017000801446. Bump the version deliberately.
  powertools_layer_arn = "arn:aws:lambda:ap-southeast-1:017000801446:layer:AWSLambdaPowertoolsPythonV3-python313-arm64:40"

  # function key => settings. Each gets its own role, log group, and failure handling.
  ingest_functions = {
    device_health = {
      name        = "silvercare-device-health"
      handler     = "handler.device_health_handler"
      description = "Validates device status messages and stores the latest 5 per device."
    }
    telemetry = {
      name        = "silvercare-telemetry"
      handler     = "handler.telemetry_handler"
      description = "Validates vitals, motion, and bed messages and stores the latest 5 per device and type."
    }
    critical_alert = {
      name        = "silvercare-critical-alert"
      handler     = "handler.critical_alert_handler"
      description = "Validates fall, vitals_alert, and alert_cancel messages, sends a critical alert or a cancel to the alerts queue, and stores the latest 5 per device."
    }
  }

  # Functions that raise alerts: they read silvercare-registry and send to the alerts
  # queue. device-health does neither.
  alerting_functions = ["telemetry", "critical_alert"]

  # Drops a message whose payload already has a field the rule adds, so a device can't
  # claim another device's ID (criterion 2).
  reserved_fields_undefined = "isUndefined(device_id) AND isUndefined(message) AND isUndefined(received_at)"
  rule_select               = "SELECT *, topic(3) AS device_id, topic(4) AS message, timestamp() AS received_at"

  # rule name => SQL and the function it invokes.
  ingest_rules = {
    silvercare_health = {
      function    = "device_health"
      description = "Device status heartbeats to silvercare-device-health."
      sql         = "${local.rule_select} FROM 'silvercare/v1/+/status' WHERE ${local.reserved_fields_undefined}"
    }
    silvercare_telemetry = {
      function    = "telemetry"
      description = "Vitals, motion, and bed messages to silvercare-telemetry."
      sql         = "${local.rule_select} FROM 'silvercare/v1/+/+' WHERE (topic(4) = 'vitals' OR topic(4) = 'motion' OR topic(4) = 'bed') AND ${local.reserved_fields_undefined}"
    }
    silvercare_alert = {
      function    = "critical_alert"
      description = "Wearable fall, vitals_alert, and alert_cancel events to silvercare-critical-alert."
      sql         = "${local.rule_select} FROM 'silvercare/v1/+/+' WHERE (topic(4) = 'fall' OR topic(4) = 'vitals_alert' OR topic(4) = 'alert_cancel') AND ${local.reserved_fields_undefined}"
    }
  }
}

# ---------------------------------------------------------------------------------------
# Lambdas
# ---------------------------------------------------------------------------------------

# One zip for all three functions. Kept under .terraform/ (git-ignored); CI builds it
# during plan and applies the saved plan in the same job.
data "archive_file" "ingest" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/ingest/src"
  output_path = "${path.module}/.terraform/build/ingest.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function code does with AWS: a strongly consistent GetItem and a conditional
# PutItem on its device's item in silvercare-latest. Nothing else (no indexes, no other
# tables, no Query/Scan/UpdateItem/DeleteItem/batch). The module adds the log group
# permissions.
data "aws_iam_policy_document" "ingest_latest_table" {
  statement {
    sid       = "LatestTableDeviceItems"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [module.latest_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["DEVICE#*"]
    }
  }
}

# critical-alert only (alerts-core criterion 26): today's statement plus the registry
# lookup (GetItem on the device's assignment item, DEVICE# partitions only) and sending
# alert messages and cancel status_updates (alert-cancel criterion 15) to the alerts
# queue. device-health keeps ingest_latest_table alone.
# The module adds sending to the on-failure destination.
data "aws_iam_policy_document" "critical_alert" {
  source_policy_documents = [data.aws_iam_policy_document.ingest_latest_table.json]

  statement {
    sid       = "RegistryDeviceAssignment"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [module.registry_table.arn]

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
}

# telemetry only (vitals-alerts criterion 23): today's statement plus GetItem on the
# device's assignment item and the resident's THRESHOLDS item (DEVICE# and THRESHOLDS#
# partitions only, so it can never read RESIDENT# profiles or USER# links) and sending
# warnings to the alerts queue. No on-failure destination (Known gap in the spec).
data "aws_iam_policy_document" "telemetry" {
  source_policy_documents = [data.aws_iam_policy_document.ingest_latest_table.json]

  statement {
    sid       = "RegistryAssignmentAndThresholds"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [module.registry_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["DEVICE#*", "THRESHOLDS#*"]
    }
  }

  statement {
    sid       = "AlertsQueue"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.alerts.arn]
  }
}

locals {
  # function key => its policy (the module adds its log group and destination).
  ingest_policies = {
    device_health  = data.aws_iam_policy_document.ingest_latest_table.json
    telemetry      = data.aws_iam_policy_document.telemetry.json
    critical_alert = data.aws_iam_policy_document.critical_alert.json
  }
}

# Renamed from fall_alert (vitals-alerts criterion 16a). The names change too, so the
# plan shows the function, its role, role policy, log group, and invoke config as
# replaced, not as unrelated destroys and creates.
moved {
  from = module.ingest_lambda["fall_alert"]
  to   = module.ingest_lambda["critical_alert"]
}

module "ingest_lambda" {
  source   = "./modules/python-lambda"
  for_each = local.ingest_functions

  function_name = each.value.name
  description   = each.value.description
  handler       = each.value.handler
  zip_path      = data.archive_file.ingest.output_path
  zip_hash      = data.archive_file.ingest.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  timeout       = 10

  environment = merge(
    {
      LATEST_TABLE            = module.latest_table.name
      POWERTOOLS_SERVICE_NAME = each.value.name
    },
    contains(local.alerting_functions, each.key) ? {
      ALERTS_QUEUE_URL = aws_sqs_queue.alerts.url
      REGISTRY_TABLE   = module.registry_table.name
    } : {},
  )

  policy_json        = local.ingest_policies[each.key]
  log_retention_days = 30

  # critical-alert only: 2 retries within an hour, then the event goes to
  # silvercare-critical-alert-dlq (alerts-core criterion 9). device-health and telemetry
  # get none (no failed-message queue, user's decision), so the module creates no async
  # invoke config for them: Lambda's defaults apply (2 retries, then dropped).
  on_failure_destination       = each.key == "critical_alert" ? { arn = aws_sqs_queue.critical_alert_dlq.arn } : null
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

# ---------------------------------------------------------------------------------------
# IoT rules
# ---------------------------------------------------------------------------------------

# Used by the rules' SQS action to the history queue and by the silvercare_alert rule's
# error action. Invoking the Lambdas is granted by the aws_lambda_permission resources
# below, not by this role.
data "aws_iam_policy_document" "iot_rules_assume" {
  statement {
    sid     = "IotAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["iot.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    # Only the silvercare rules may use the role. A pattern, not the rule ARNs: the
    # rules depend on this role, so listing them would create a cycle.
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:iot:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:rule/silvercare_*"]
    }
  }
}

data "aws_region" "current" {}

resource "aws_iam_role" "iot_rules" {
  name               = "silvercare-iot-rules"
  description        = "Lets the silvercare ingest IoT rules send messages to silvercare-history and silvercare-critical-alert-dlq."
  assume_role_policy = data.aws_iam_policy_document.iot_rules_assume.json
}

data "aws_iam_policy_document" "iot_rules" {
  # Second action of every rule (docs/features/ingest-history.md, criterion 5).
  statement {
    sid       = "HistoryQueue"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.history.arn]
  }

  # Error action of the silvercare_alert rule (alerts-core criteria 10, 28).
  statement {
    sid       = "CriticalAlertDlq"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.critical_alert_dlq.arn]
  }
}

resource "aws_iam_role_policy" "iot_rules" {
  name   = "silvercare-iot-rules"
  role   = aws_iam_role.iot_rules.id
  policy = data.aws_iam_policy_document.iot_rules.json
}

resource "aws_iot_topic_rule" "ingest" {
  for_each = local.ingest_rules

  name        = each.key
  description = each.value.description
  enabled     = true
  sql         = each.value.sql
  sql_version = "2016-03-23"

  lambda {
    function_arn = module.ingest_lambda[each.value.function].function_arn
  }

  # History path: every matched message also goes to S3 through the history queue.
  sqs {
    queue_url  = aws_sqs_queue.history.id
    role_arn   = aws_iam_role.iot_rules.arn
    use_base64 = false
  }

  # silvercare_alert only (alerts-core criterion 10): a fall, vitals_alert, or
  # alert_cancel the rule
  # can't hand to critical-alert (or to the history queue) goes to
  # silvercare-critical-alert-dlq, whose alarm emails the team. The other rules have no error_action (no failed-message queue,
  # user's decision): if their Lambda action fails, the history action still runs, so
  # the message is still kept in S3.
  dynamic "error_action" {
    for_each = each.key == "silvercare_alert" ? [aws_sqs_queue.critical_alert_dlq.id] : []

    content {
      sqs {
        queue_url  = error_action.value
        role_arn   = aws_iam_role.iot_rules.arn
        use_base64 = false
      }
    }
  }

  depends_on = [aws_iam_role_policy.iot_rules]
}

# Each function may be invoked by IoT only from its own rule, in this account.
resource "aws_lambda_permission" "ingest_iot" {
  for_each = local.ingest_rules

  statement_id   = "AllowIotRule-${each.key}"
  action         = "lambda:InvokeFunction"
  function_name  = module.ingest_lambda[each.value.function].function_name
  principal      = "iot.amazonaws.com"
  source_arn     = aws_iot_topic_rule.ingest[each.key].arn
  source_account = data.aws_caller_identity.current.account_id
}
