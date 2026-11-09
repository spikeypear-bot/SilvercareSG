# Live alerts (docs/features/live-alerts.md): one AppSync Event API. Browsers connect
# and subscribe with their Cognito token; only silvercare-alert-processor publishes,
# with IAM. Each subscribe is checked by silvercare-realtime-subscribe (staff: any
# resident; caregivers: linked residents only). Pings carry only a resident_id.
#
# Data: nothing stored. No AppSync logging (no log_config), so no event data in logs.
# Cost: US$1 per million operations, US$0.08 per million connection-minutes, no fixed
# fee: ~US$0-0.15/month at this scale (spec, Cost).

locals {
  realtime_subscribe_function_name = "silvercare-realtime-subscribe"
  realtime_namespaces              = toset(["health", "device"])
  events_http_dns                  = aws_appsync_api.events.dns["HTTP"]
  events_realtime_dns              = aws_appsync_api.events.dns["REALTIME"]
}

resource "aws_appsync_api" "events" {
  name = "silvercare-events"

  event_config {
    auth_provider {
      auth_type = "AMAZON_COGNITO_USER_POOLS"
      cognito_config {
        user_pool_id        = aws_cognito_user_pool.users.id
        aws_region          = data.aws_region.current.region
        app_id_client_regex = "^${aws_cognito_user_pool_client.web.id}$"
      }
    }

    auth_provider {
      auth_type = "AWS_IAM"
    }

    connection_auth_mode {
      auth_type = "AMAZON_COGNITO_USER_POOLS"
    }

    default_subscribe_auth_mode {
      auth_type = "AMAZON_COGNITO_USER_POOLS"
    }

    default_publish_auth_mode {
      auth_type = "AWS_IAM"
    }
  }
}

resource "aws_appsync_channel_namespace" "alerts" {
  for_each = local.realtime_namespaces

  api_id = aws_appsync_api.events.api_id
  name   = each.key

  publish_auth_mode {
    auth_type = "AWS_IAM"
  }

  subscribe_auth_mode {
    auth_type = "AMAZON_COGNITO_USER_POOLS"
  }

  # No on_publish handler: published pings pass through unchanged.
  handler_configs {
    on_subscribe {
      behavior = "DIRECT"
      integration {
        data_source_name = aws_appsync_datasource.realtime_subscribe.name
        lambda_config {
          invoke_type = "REQUEST_RESPONSE"
        }
      }
    }
  }
}

# ---------------------------------------------------------------------------------------
# Subscribe check (criteria 3, 7-12)
# ---------------------------------------------------------------------------------------

data "archive_file" "realtime_subscribe" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/realtime-subscribe/src"
  output_path = "${path.module}/.terraform/build/realtime-subscribe.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

data "aws_iam_policy_document" "realtime_subscribe" {
  # Pattern 13, keys only: does USER#<sub> / RESIDENT#<id> exist? No profiles or other
  # items.
  statement {
    sid       = "RegistryCaregiverLink"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [module.registry_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["USER#*"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["pk", "sk"]
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
}

module "realtime_subscribe" {
  source = "./modules/python-lambda"

  function_name = local.realtime_subscribe_function_name
  description   = "Checks AppSync Events subscriptions: staff any resident, caregivers linked residents only."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.realtime_subscribe.output_path
  zip_hash      = data.archive_file.realtime_subscribe.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  timeout       = 5

  environment = {
    REGISTRY_TABLE          = module.registry_table.name
    POWERTOOLS_SERVICE_NAME = local.realtime_subscribe_function_name
  }

  policy_json        = data.aws_iam_policy_document.realtime_subscribe.json
  log_retention_days = 30

  # Invoked synchronously by AppSync: no async retries, so no on-failure destination.
}

# AppSync calls the function through this data source and its own role.
data "aws_iam_policy_document" "realtime_datasource_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["appsync.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["${aws_appsync_api.events.api_arn}*"]
    }
  }
}

resource "aws_iam_role" "realtime_datasource" {
  name               = "silvercare-realtime-datasource"
  assume_role_policy = data.aws_iam_policy_document.realtime_datasource_trust.json
}

data "aws_iam_policy_document" "realtime_datasource" {
  statement {
    sid       = "InvokeSubscribeCheck"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [module.realtime_subscribe.function_arn]
  }
}

resource "aws_iam_role_policy" "realtime_datasource" {
  name   = "invoke-subscribe-check"
  role   = aws_iam_role.realtime_datasource.id
  policy = data.aws_iam_policy_document.realtime_datasource.json
}

resource "aws_appsync_datasource" "realtime_subscribe" {
  api_id           = aws_appsync_api.events.api_id
  name             = "realtime_subscribe"
  type             = "AWS_LAMBDA"
  service_role_arn = aws_iam_role.realtime_datasource.arn

  lambda_config {
    function_arn = module.realtime_subscribe.function_arn
  }
}
