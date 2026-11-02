# Alerts core and vitals alerts (docs/features/alerts-core.md,
# docs/features/vitals-alerts.md; alerts path of docs/decisions/0001-iot-rule-routing.md):
# critical-alert (ingest.tf; falls and device vitals_alerts) and telemetry (ingest.tf;
# the cloud vitals check) look up the device's resident and send an alert message to
# the silvercare-alerts queue. The alert-processor Lambda reads the queue, holds back
# repeats (same resident, type, and severity within 30 minutes; never falls), writes
# each alert to silvercare-alerts and to S3 alerts/ (history bucket), and emails the
# resident's subscribers through the silvercare-alerts SNS topic, filtered by
# resident_id.
#
# No critical alert may be lost on the way: critical-alert's failed async invocations
# and the silvercare_alert rule's failed actions go to silvercare-critical-alert-dlq,
# and alert messages that fail 5 receives go to silvercare-alerts-dlq. A CloudWatch
# alarm on each DLQ emails the team through the silvercare-ops topic. (telemetry has no
# DLQ: see the Known gap in docs/features/vitals-alerts.md.)
#
# The queues and DLQs hold alert messages (falls with the fall details, vitals alerts
# with the reading and limit, linked to a resident ID); critical-alert-dlq can hold raw
# fall and vitals_alert payloads. Retention: alerts queue 4 days, DLQs 14 days, S3
# alerts/ no expiry until Open decision #4 (retention).
#
# Cost: pay per use only, no fixed fees. SNS (1,000 emails/month free) and Lambda stay
# within the always-free tiers at a few alerts a day; 2 of the 10 free alarms; 1 of the
# 10 free custom metrics; the AWS managed SNS key has no monthly fee. SQS: the
# alert-processor trigger long-polls silvercare-alerts all the time, even when idle
# (several hundred thousand ReceiveMessage requests a month). Together with the history
# trigger this can pass the 1M free SQS requests a month: a few US cents a month
# (US$0.40 per extra million requests).

locals {
  alert_processor_function_name = "silvercare-alert-processor"

  # Prefix in the history bucket that only alert-processor may write (history.tf).
  alerts_prefix = "alerts"

  # DLQs watched by an alarm: key => queue name. Alarm name: "<queue name>-not-empty".
  watched_dlqs = {
    alerts_dlq         = aws_sqs_queue.alerts_dlq.name
    critical_alert_dlq = aws_sqs_queue.critical_alert_dlq.name
  }

  # Per-DLQ alarm text. silvercare-critical-alert-dlq holds Lambda on-failure records
  # and IoT rule error payloads, which have no source queue to redrive to.
  dlq_alarm_descriptions = {
    alerts_dlq         = "silvercare-alerts-dlq has at least one message: an alert may not have been stored or emailed. Inspect it, fix the cause, and redrive it to silvercare-alerts."
    critical_alert_dlq = "silvercare-critical-alert-dlq has at least one message: a fall or device vitals_alert may not have become an alert. Inspect it, then replay the original payload manually (it cannot be redriven)."
  }
}

# ---------------------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------------------

# Alert messages that fail 5 receives (invalid, or alert-processor kept failing).
resource "aws_sqs_queue" "alerts_dlq" {
  name                      = "silvercare-alerts-dlq"
  sqs_managed_sse_enabled   = true
  message_retention_seconds = 1209600 # 14 days, the SQS maximum
}

resource "aws_sqs_queue" "alerts" {
  name                       = "silvercare-alerts"
  sqs_managed_sse_enabled    = true
  visibility_timeout_seconds = 180    # 6 x alert-processor's 30 s timeout (no batching window)
  message_retention_seconds  = 345600 # 4 days

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.alerts_dlq.arn
    maxReceiveCount     = 5
  })
}

# Only silvercare-alerts may use the DLQ. A separate resource, because the source queue
# already depends on the DLQ (its redrive policy names it).
resource "aws_sqs_queue_redrive_allow_policy" "alerts_dlq" {
  queue_url = aws_sqs_queue.alerts_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.alerts.arn]
  })
}

# critical-alert's on-failure destination (async invocations that still fail after 2
# retries) and the silvercare_alert rule's error action. Not a redrive target.
# Renamed from fall_alert_dlq (vitals-alerts criterion 16a): the new name replaces the
# queue, so it must be empty before merging.
moved {
  from = aws_sqs_queue.fall_alert_dlq
  to   = aws_sqs_queue.critical_alert_dlq
}

resource "aws_sqs_queue" "critical_alert_dlq" {
  name                      = "silvercare-critical-alert-dlq"
  sqs_managed_sse_enabled   = true
  message_retention_seconds = 1209600 # 14 days, the SQS maximum
}

# Deny statements in the queue policies below: sqs:* and principal * are intentional.
# Denying every action from every caller without TLS is narrower access, not broader.
data "aws_iam_policy_document" "alerts_queue" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [aws_sqs_queue.alerts.arn]

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

  # No "only the detectors may send" Deny (user's decision, 2026-11-02): a DLQ redrive
  # sends with the operator's own credentials, and such a Deny would block it even for
  # admins and root. Sending is limited by IAM instead: the critical-alert and
  # telemetry role policies (ingest.tf) grant it, and the shared developer user has no
  # SQS permissions.
}

resource "aws_sqs_queue_policy" "alerts" {
  queue_url = aws_sqs_queue.alerts.id
  policy    = data.aws_iam_policy_document.alerts_queue.json
}

data "aws_iam_policy_document" "alerts_dlq" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [aws_sqs_queue.alerts_dlq.arn]

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

resource "aws_sqs_queue_policy" "alerts_dlq" {
  queue_url = aws_sqs_queue.alerts_dlq.id
  policy    = data.aws_iam_policy_document.alerts_dlq.json
}

# Only the critical-alert role (on-failure destination; Lambda sends with the
# function's role) and the silvercare-iot-rules role (rule error action) may send. The Deny makes
# that hold even for principals whose IAM policy would allow it. No Allow naming the
# roles: their own IAM policies (ingest.tf, python-lambda module) grant the send, and a
# role principal in a queue policy can fail the first apply (PrincipalNotFound while
# the new role propagates) and drifts if a role is recreated.
data "aws_iam_policy_document" "critical_alert_dlq" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [aws_sqs_queue.critical_alert_dlq.arn]

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

  statement {
    sid       = "DenySendFromOthers"
    effect    = "Deny"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.critical_alert_dlq.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "aws:PrincipalArn"
      values = [
        module.ingest_lambda["critical_alert"].role_arn,
        aws_iam_role.iot_rules.arn,
      ]
    }
  }
}

moved {
  from = aws_sqs_queue_policy.fall_alert_dlq
  to   = aws_sqs_queue_policy.critical_alert_dlq
}

resource "aws_sqs_queue_policy" "critical_alert_dlq" {
  queue_url = aws_sqs_queue.critical_alert_dlq.id
  policy    = data.aws_iam_policy_document.critical_alert_dlq.json
}

# ---------------------------------------------------------------------------------------
# SNS: alert emails
# ---------------------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name = "silvercare-alerts"
  # AWS managed key: free, no key policy to maintain.
  kms_master_key_id = "alias/aws/sns"
}

# Only alert-processor may publish; nothing without TLS. All topic actions and principal
# * in the Deny statements are intentional (narrower access, not broader). No Allow naming the
# alert-processor role: its own IAM policy grants sns:Publish, and a role principal in
# the topic policy can fail the first apply (PrincipalNotFound) and drifts if the role
# is recreated.
locals {
  # Every action a topic policy may name. SNS rejects "sns:*" in a topic policy
  # ("Policy statement action out of service scope"), so the TLS-only Deny lists them.
  # Same list as the default policy SNS gives a new topic.
  sns_topic_actions = [
    "sns:AddPermission",
    "sns:DeleteTopic",
    "sns:GetTopicAttributes",
    "sns:ListSubscriptionsByTopic",
    "sns:Publish",
    "sns:RemovePermission",
    "sns:SetTopicAttributes",
    "sns:Subscribe",
  ]
}

data "aws_iam_policy_document" "alerts_topic" {
  statement {
    sid       = "DenyPublishFromOthers"
    effect    = "Deny"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "aws:PrincipalArn"
      values   = [module.alert_processor.role_arn]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = local.sns_topic_actions
    resources = [aws_sns_topic.alerts.arn]

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

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}

# One email subscription per label (never per email: emails are sensitive and would
# show in resource addresses). Each gets only the alerts of its listed residents.
# The subscriber confirms by clicking the link in AWS's confirmation email.
resource "aws_sns_topic_subscription" "alert_email" {
  for_each = var.alert_subscribers

  topic_arn           = aws_sns_topic.alerts.arn
  protocol            = "email"
  endpoint            = var.subscriber_emails[each.key]
  filter_policy_scope = "MessageAttributes"
  filter_policy = jsonencode({
    resident_id = each.value.resident_ids
  })
}

# ---------------------------------------------------------------------------------------
# SNS: ops (DLQ alarm emails)
# ---------------------------------------------------------------------------------------

resource "aws_sns_topic" "ops" {
  #checkov:skip=CKV_AWS_26:Not encrypted on purpose (spec criterion 22). CloudWatch alarms can't publish to a topic using the AWS managed SNS key, and a customer-managed key costs ~US$1/month. The topic carries only alarm names and states, no personal data.
  name = "silvercare-ops"
}

# Only CloudWatch alarms named silvercare-* in this account may publish; nothing
# without TLS. All topic actions and principal * in the Deny statements are intentional.
data "aws_iam_policy_document" "ops_topic" {
  statement {
    sid       = "AllowPublishFromAccountAlarms"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.ops.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:cloudwatch:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:alarm:silvercare-*"]
    }
  }

  # Anyone else (no alarm source ARN) is denied, whatever their IAM policy allows.
  statement {
    sid       = "DenyPublishFromOthers"
    effect    = "Deny"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.ops.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "ArnNotLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:cloudwatch:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:alarm:silvercare-*"]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = local.sns_topic_actions
    resources = [aws_sns_topic.ops.arn]

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

resource "aws_sns_topic_policy" "ops" {
  arn    = aws_sns_topic.ops.arn
  policy = data.aws_iam_policy_document.ops_topic.json
}

resource "aws_sns_topic_subscription" "ops_email" {
  for_each = toset(var.ops_subscribers)

  topic_arn = aws_sns_topic.ops.arn
  protocol  = "email"
  endpoint  = var.subscriber_emails[each.key]
}

# ---------------------------------------------------------------------------------------
# Alarms
# ---------------------------------------------------------------------------------------

# Any message in a DLQ is an alert that didn't make it: alarm at the first one.
moved {
  from = aws_cloudwatch_metric_alarm.dlq_not_empty["fall_alert_dlq"]
  to   = aws_cloudwatch_metric_alarm.dlq_not_empty["critical_alert_dlq"]
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  for_each = local.watched_dlqs

  alarm_name          = "${each.value}-not-empty"
  alarm_description   = local.dlq_alarm_descriptions[each.key]
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = each.value }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
  ok_actions          = [aws_sns_topic.ops.arn]
}

# ---------------------------------------------------------------------------------------
# alert-processor Lambda
# ---------------------------------------------------------------------------------------

# Kept under .terraform/ (git-ignored); CI builds it during plan and applies the saved
# plan in the same job.
data "archive_file" "alert_processor" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/alert-processor/src"
  output_path = "${path.module}/.terraform/build/alert-processor.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function does with AWS: the event source mapping polls silvercare-alerts with
# the function's role (receive, delete, read attributes); the code does a Query of the
# resident's recent alerts (repeat suppression, pattern 7; vitals-alerts criterion 24),
# a conditional PutItem, a GetItem on a retry, and one UpdateItem (notified_at) on its
# alert in silvercare-alerts (base table, RESIDENT# partitions only); a GetItem on the
# device's vitals item in silvercare-latest; a PutObject under alerts/; and an SNS
# Publish to the alerts topic. Nothing else: no indexes, no Scan/DeleteItem, no other
# prefixes or reads in S3. No KMS
# permissions: the topic uses the AWS managed key aws/sns, whose key policy already
# lets SNS use it for principals in this account (the iam-policy skill asks for KMS
# grants only for customer-managed keys). The module adds the log group permissions.
data "aws_iam_policy_document" "alert_processor" {
  statement {
    sid       = "AlertsQueue"
    effect    = "Allow"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.alerts.arn]
  }

  statement {
    sid       = "AlertsTableResidentItems"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [module.alerts_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["RESIDENT#*"]
    }
  }

  statement {
    sid       = "LatestTableDeviceItems"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [module.latest_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["DEVICE#*"]
    }
  }

  statement {
    sid       = "HistoryAlertObjects"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.history.arn}/${local.alerts_prefix}/*"]
  }

  statement {
    sid       = "AlertsTopic"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }
}

module "alert_processor" {
  source = "./modules/python-lambda"

  function_name = local.alert_processor_function_name
  description   = "Stores alerts from the silvercare-alerts queue in DynamoDB and S3 and emails subscribers through SNS."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.alert_processor.output_path
  zip_hash      = data.archive_file.alert_processor.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  timeout       = 30

  environment = {
    ALERTS_TABLE                 = module.alerts_table.name
    LATEST_TABLE                 = module.latest_table.name
    HISTORY_BUCKET               = aws_s3_bucket.history.bucket
    ALERTS_TOPIC_ARN             = aws_sns_topic.alerts.arn
    PORTAL_URL                   = "https://${aws_cloudfront_distribution.frontend.domain_name}"
    POWERTOOLS_SERVICE_NAME      = local.alert_processor_function_name
    POWERTOOLS_METRICS_NAMESPACE = "SilverCare"
  }

  policy_json        = data.aws_iam_policy_document.alert_processor.json
  log_retention_days = 30

  # No on_failure_destination: fed by the event source mapping below, not invoked
  # asynchronously. Messages that keep failing go to silvercare-alerts-dlq.
}

# Each message is handled on its own; the function reports failed ones by messageId.
resource "aws_lambda_event_source_mapping" "alert_processor" {
  event_source_arn                   = aws_sqs_queue.alerts.arn
  function_name                      = module.alert_processor.function_arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 0
  function_response_types            = ["ReportBatchItemFailures"]
  enabled                            = true
}
