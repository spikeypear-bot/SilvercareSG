# Keep-warm pings (docs/features/api-caching.md, criteria 1-2): every 5 minutes two
# rules (an EventBridge rule takes at most 5 targets) call 7 API Lambdas with
# {"source": "silvercare.keep-warm"}, which each handler answers at once (no AWS calls,
# no logs), so a warm container is usually ready.
# ~US$0: ~17,280 events and ~60,000 short invocations a month, inside the free tiers.
# No alarm (the 10 free alarms are used): a missed ping only means a cold start.

locals {
  keep_warm_functions = {
    dashboard = module.api_dashboard
    residents = module.api_residents
    ops       = module.api_ops
    devices   = module.api_devices
  }

  # Added 2026-11-10 (user): the resident page waits on threshold history (and, for
  # doctors, last night), and the vitals cache only survives in a warm container.
  keep_warm_functions_2 = {
    thresholds = module.api_thresholds
    nightly    = module.api_nightly
    vitals     = module.api_vitals
  }
}

resource "aws_cloudwatch_event_rule" "api_keep_warm" {
  name                = "silvercare-api-keep-warm"
  description         = "Pings the busy API Lambdas every 5 minutes to keep a container warm."
  schedule_expression = "rate(5 minutes)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "api_keep_warm" {
  for_each = local.keep_warm_functions

  rule      = aws_cloudwatch_event_rule.api_keep_warm.name
  target_id = "keep-warm-${each.key}"
  arn       = each.value.function_arn
  input     = jsonencode({ source = "silvercare.keep-warm" })
}

# Only this rule, in this account, may invoke the functions this way.
resource "aws_lambda_permission" "api_keep_warm" {
  for_each = local.keep_warm_functions

  statement_id   = "AllowEventBridgeKeepWarm"
  action         = "lambda:InvokeFunction"
  function_name  = each.value.function_name
  principal      = "events.amazonaws.com"
  source_arn     = aws_cloudwatch_event_rule.api_keep_warm.arn
  source_account = data.aws_caller_identity.current.account_id
}

resource "aws_cloudwatch_event_rule" "api_keep_warm_2" {
  name                = "silvercare-api-keep-warm-2"
  description         = "Pings more API Lambdas every 5 minutes to keep a container warm."
  schedule_expression = "rate(5 minutes)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "api_keep_warm_2" {
  for_each = local.keep_warm_functions_2

  rule      = aws_cloudwatch_event_rule.api_keep_warm_2.name
  target_id = "keep-warm-${each.key}"
  arn       = each.value.function_arn
  input     = jsonencode({ source = "silvercare.keep-warm" })
}

resource "aws_lambda_permission" "api_keep_warm_2" {
  for_each = local.keep_warm_functions_2

  statement_id   = "AllowEventBridgeKeepWarm"
  action         = "lambda:InvokeFunction"
  function_name  = each.value.function_name
  principal      = "events.amazonaws.com"
  source_arn     = aws_cloudwatch_event_rule.api_keep_warm_2.arn
  source_account = data.aws_caller_identity.current.account_id
}
