# Ingest, live path (docs/features/ingest-live.md; path 1 of
# docs/decisions/0001-iot-rule-routing.md): three IoT topic rules invoke three Lambdas,
# which validate each message and add it to the device's latest 5 in silvercare-latest.
#
# Each rule also sends every message it matches to the silvercare-history queue (second
# action, docs/features/ingest-history.md; see history.tf). No alerts yet. Invalid
# messages are dropped by the Lambdas (logged with a reason code). Rule action failures
# and async invocations that still fail after Lambda's retries go to the
# silvercare-ingest-failed queue (kept 14 days, may hold full payloads).
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
    fall_alert = {
      name        = "silvercare-fall-alert"
      handler     = "handler.fall_alert_handler"
      description = "Validates fall messages and stores the latest 5 per device. Sends no alerts yet."
    }
  }

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
      function    = "fall_alert"
      description = "Wearable fall events to silvercare-fall-alert."
      sql         = "${local.rule_select} FROM 'silvercare/v1/+/fall' WHERE ${local.reserved_fields_undefined}"
    }
  }
}

# ---------------------------------------------------------------------------------------
# Failed-message queue
# ---------------------------------------------------------------------------------------

# Receives IoT rule action failures (error_action) and async Lambda invocations that
# still fail after retries (on-failure destination). Messages can include full payloads.
resource "aws_sqs_queue" "ingest_failed" {
  name                      = "silvercare-ingest-failed"
  sqs_managed_sse_enabled   = true
  message_retention_seconds = 1209600 # 14 days, the SQS maximum
}

# Deny statement: sqs:* and principal * are intentional here. Denying every action from
# every caller without TLS is narrower access, not broader.
data "aws_iam_policy_document" "ingest_failed_queue" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [aws_sqs_queue.ingest_failed.arn]

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

resource "aws_sqs_queue_policy" "ingest_failed" {
  queue_url = aws_sqs_queue.ingest_failed.id
  policy    = data.aws_iam_policy_document.ingest_failed_queue.json
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
# tables, no Query/Scan/UpdateItem/DeleteItem/batch). The module adds the log group and
# on-failure destination permissions.
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

  environment = {
    LATEST_TABLE            = module.latest_table.name
    POWERTOOLS_SERVICE_NAME = each.value.name
  }

  policy_json        = data.aws_iam_policy_document.ingest_latest_table.json
  log_retention_days = 30

  on_failure_destination       = { arn = aws_sqs_queue.ingest_failed.arn }
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

# ---------------------------------------------------------------------------------------
# IoT rules
# ---------------------------------------------------------------------------------------

# Used by the rules' SQS actions: the history action and the error_action. Invoking the
# Lambdas is granted by the aws_lambda_permission resources below, not by this role.
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
  description        = "Lets the silvercare ingest IoT rules send messages to silvercare-history and failed messages to silvercare-ingest-failed."
  assume_role_policy = data.aws_iam_policy_document.iot_rules_assume.json
}

data "aws_iam_policy_document" "iot_rules" {
  statement {
    sid       = "IngestFailedQueue"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest_failed.arn]
  }

  # Second action of every rule (docs/features/ingest-history.md, criterion 5).
  statement {
    sid       = "HistoryQueue"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.history.arn]
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

  error_action {
    sqs {
      queue_url  = aws_sqs_queue.ingest_failed.id
      role_arn   = aws_iam_role.iot_rules.arn
      use_base64 = false
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
