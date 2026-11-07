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
# Nurses (docs/features/escalation.md, docs/features/alert-routing.md): critical alerts
# and every alert from an unassigned device are emailed to every nurse subscriber at
# once through the silvercare-nurses SNS topic (no filter: staff see every resident).
# For a warning from an assigned device, alert-processor sends an escalation check back
# to silvercare-alerts with a per-message DelaySeconds of 300 (the queue's own delay
# stays 0). 5 minutes later it reads the alert again and, if it is still open and not
# yet escalated, emails the nurses. No extra queue, function, mapping, or alarm: failed
# checks reach silvercare-alerts-dlq and its alarm like failed alerts.
#
# Cancel, acknowledge, and follow-ups (docs/features/alert-cancel.md, alert-routing.md):
# critical-alert sends a `status_update` for the wearable "I'm OK" button; Track B's
# API will send one for a caregiver cancel or a nurse acknowledge. alert-processor
# changes the matching alerts and emails a short follow-up through the same two topics:
# caregivers (resident_id filter), and for a cancel the nurses if they were emailed or
# had acknowledged. No new resources: failed updates reach silvercare-alerts-dlq like
# failed alerts.
#
# Device problems (docs/features/watchdog.md): the watchdog (watchdog.tf) sends
# device_offline, battery_low, and not_worn alerts and system resolves to the same
# queue. alert-processor emails device problems to the resident's caregivers (alerts
# topic) and to the ops team (silvercare-ops-devices, encrypted), never to the nurses, and resolves them
# without email.
#
# No critical alert may be lost on the way: critical-alert's failed async invocations
# and the silvercare_alert rule's failed actions go to silvercare-critical-alert-dlq,
# and alert messages that fail 5 receives go to silvercare-alerts-dlq. A CloudWatch
# alarm on each DLQ emails the team through the silvercare-ops topic. (telemetry has no
# DLQ: see the Known gap in docs/features/vitals-alerts.md.)
#
# The queues and DLQs hold alert messages (falls with the fall details, vitals alerts
# with the reading and limit, linked to a resident ID; status updates with the changed
# alert's target and, from Track B, the user's sub, a caregiver's full name, and a
# nurse's acknowledge note or a nurse's or doctor's closing note, which every staff
# resolve carries); critical-alert-dlq can hold raw fall, vitals_alert, and
# alert_cancel payloads. Retention: alerts queue 4 days, DLQs 14 days, S3 alerts/
# (the original copy and the <alert_id>.final.json record with the notes,
# docs/features/staff-resolve.md) the resident's lifetime + 6 years (Open decision #4,
# decided 2026-11-01; see Retention in docs/data-model.md).
#
# Cost: pay per use only, no fixed fees. SNS (1,000 emails/month free, shared by the
# alert and nurse emails) and Lambda stay
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
    alerts_dlq         = "silvercare-alerts-dlq has at least one message: an alert may not have been stored or emailed, an escalation_check may not have emailed the nurses, or a status_update (cancel or acknowledge) may not have been applied or followed up. Inspect it, fix the cause, and redrive it to silvercare-alerts. escalation_check messages are skipped as stale once 20 minutes have passed since the alert was stored (all automatic retries finish before that), so redriving one later sends no nurse email. A redriven status_update still changes only alerts that are open (a cancel also acknowledged ones; for the wearable, those up to when the press was received)."
    critical_alert_dlq = "silvercare-critical-alert-dlq has at least one message: a fall or device vitals_alert may not have become an alert, or an alert_cancel may not have cancelled the open alerts. Inspect it, then replay the original payload manually (it cannot be redriven)."
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
  # telemetry role policies (ingest.tf) and the alert-processor role policy (below, for
  # its delayed escalation checks), the watchdog's, and the alert-actions API's
  # (api.tf) grant it, and the shared developer user has no SQS permissions.
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

  # The only principal allowed to subscribe to the alerts and nurses topics
  # (escalation criterion 14a): subscriptions come from Terraform only. The deploy
  # role silvercare-cicd was made by hand and is not in Terraform (CLAUDE.md,
  # Deployment; iam-policy skill), so its ARN is built from the account ID.
  deploy_role_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/silvercare-cicd"
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

  # Subscriptions only through Terraform: the deploy role may subscribe, nobody else
  # (escalation criterion 14a). Existing subscriptions and "Confirm subscription"
  # links are not affected.
  statement {
    sid       = "DenySubscribeFromOthers"
    effect    = "Deny"
    actions   = ["sns:Subscribe"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "aws:PrincipalArn"
      values   = [local.deploy_role_arn]
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
# SNS: nurse escalation emails (docs/features/escalation.md, criteria 11-12)
# ---------------------------------------------------------------------------------------

# Critical and unassigned alerts at once, warnings still open 5 minutes after the
# first notification (docs/features/alert-routing.md), and cancel follow-ups. Carries
# the same health data as silvercare-alerts.
resource "aws_sns_topic" "nurses" {
  name = "silvercare-nurses"
  # AWS managed key: free, no key policy to maintain.
  kms_master_key_id = "alias/aws/sns"
}

# Only alert-processor may publish, only the deploy role may subscribe, and nothing
# without TLS. Same pattern and reasons as
# the alerts topic policy above (Deny on aws:PrincipalArn, no Allow naming the role).
data "aws_iam_policy_document" "nurses_topic" {
  statement {
    sid       = "DenyPublishFromOthers"
    effect    = "Deny"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.nurses.arn]

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

  # Subscriptions only through Terraform, as for the alerts topic (criterion 14a).
  statement {
    sid       = "DenySubscribeFromOthers"
    effect    = "Deny"
    actions   = ["sns:Subscribe"]
    resources = [aws_sns_topic.nurses.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "aws:PrincipalArn"
      values   = [local.deploy_role_arn]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = local.sns_topic_actions
    resources = [aws_sns_topic.nurses.arn]

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

resource "aws_sns_topic_policy" "nurses" {
  arn    = aws_sns_topic.nurses.arn
  policy = data.aws_iam_policy_document.nurses_topic.json
}

# One email subscription per label, no filter policy: nurses are staff and see every
# resident. The subscriber confirms by clicking the link in AWS's confirmation email.
resource "aws_sns_topic_subscription" "nurse_email" {
  for_each = toset(var.nurse_subscribers)

  topic_arn = aws_sns_topic.nurses.arn
  protocol  = "email"
  endpoint  = var.subscriber_emails[each.key]
}

# ---------------------------------------------------------------------------------------
# SNS: ops (alarm emails only; device problems go to silvercare-ops-devices below)
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
# SNS: ops device problems (docs/features/watchdog.md; user's decision 2026-11-04)
# ---------------------------------------------------------------------------------------

# Device-problem emails and their acknowledge follow-ups (alert-processor) and the
# mass-offline summary (watchdog). A separate topic from silvercare-ops so it can be
# encrypted: it carries device IDs, opaque resident IDs, heartbeat times, battery level,
# and a caregiver's masked name (no names or health readings). No alarm publishes here,
# so the AWS managed key works (free).
resource "aws_sns_topic" "ops_devices" {
  name = "silvercare-ops-devices"
  # AWS managed key: free, no key policy to maintain.
  kms_master_key_id = "alias/aws/sns"
}

# Only alert-processor and the watchdog may publish, only the deploy role may subscribe,
# and nothing without TLS. Same pattern and reasons as the alerts and nurses topic
# policies (Deny on aws:PrincipalArn, no Allow naming the roles).
data "aws_iam_policy_document" "ops_devices_topic" {
  statement {
    sid       = "DenyPublishFromOthers"
    effect    = "Deny"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.ops_devices.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "aws:PrincipalArn"
      values   = [module.alert_processor.role_arn, module.watchdog.role_arn]
    }
  }

  # Subscriptions only through Terraform, as for the alerts and nurses topics.
  statement {
    sid       = "DenySubscribeFromOthers"
    effect    = "Deny"
    actions   = ["sns:Subscribe"]
    resources = [aws_sns_topic.ops_devices.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "aws:PrincipalArn"
      values   = [local.deploy_role_arn]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = local.sns_topic_actions
    resources = [aws_sns_topic.ops_devices.arn]

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

resource "aws_sns_topic_policy" "ops_devices" {
  arn    = aws_sns_topic.ops_devices.arn
  policy = data.aws_iam_policy_document.ops_devices_topic.json
}

# One email subscription per ops_subscribers label, no filter policy: ops see device
# problems for every resident. Each subscriber confirms from AWS's confirmation email.
resource "aws_sns_topic_subscription" "ops_devices_email" {
  for_each = toset(var.ops_subscribers)

  topic_arn = aws_sns_topic.ops_devices.arn
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
# the function's role (receive, delete, read attributes); the code sends the delayed
# escalation check back to silvercare-alerts (SendMessage, escalation criterion 13);
# it does Queries of the resident's alerts (repeat suppression, pattern 7;
# vitals-alerts criterion 24; a cancel's device-and-status Query, pattern 24,
# alert-cancel criterion 15), a conditional PutItem, a GetItem on a retry, for an
# escalation check (pattern 23), or for an acknowledge (pattern 25), and conditional
# UpdateItems (notified_at, escalation_queued_at, escalated_at, the status change,
# status_notified_at) on its alert in silvercare-alerts (base table, RESIDENT#
# partitions only); a GetItem on the device's vitals item in silvercare-latest; a
# PutObject under alerts/; and an SNS Publish to the alerts and nurses topics (alerts,
# escalations, and status follow-ups) and to silvercare-ops-devices (device problems and their
# acknowledge follow-ups, docs/features/watchdog.md). The status updates need no new
# permission. Nothing else: no indexes, no Scan/DeleteItem, no other prefixes or
# reads in S3. No KMS permissions: the alerts, nurses, and ops-devices topics use the AWS managed key aws/sns, whose
# key policy already lets SNS use it for principals in this account (the iam-policy
# skill asks for KMS grants only for customer-managed keys). The module adds the log
# group permissions.
data "aws_iam_policy_document" "alert_processor" {
  statement {
    sid       = "AlertsQueue"
    effect    = "Allow"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.alerts.arn]
  }

  statement {
    sid       = "AlertsQueueEscalationChecks"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
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

  statement {
    sid       = "NursesTopic"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.nurses.arn]
  }

  # Device-problem emails and their acknowledge follow-ups (watchdog criteria 13, 14a).
  statement {
    sid       = "OpsTopic"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.ops_devices.arn]
  }
}

module "alert_processor" {
  source = "./modules/python-lambda"

  function_name = local.alert_processor_function_name
  description   = "Stores alerts from the silvercare-alerts queue in DynamoDB and S3, emails subscribers through SNS, escalates unanswered alerts to nurses, emails device problems to ops, and applies cancels, acknowledges, and resolves with follow-up emails."
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
    ALERTS_QUEUE_URL             = aws_sqs_queue.alerts.url
    NURSES_TOPIC_ARN             = aws_sns_topic.nurses.arn
    OPS_TOPIC_ARN                = aws_sns_topic.ops_devices.arn
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
