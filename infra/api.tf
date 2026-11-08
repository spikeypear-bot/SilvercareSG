# HTTP API for the frontend (API Gateway v2, the api-route and cognito-auth skills):
# one $default stage with throttling, CORS for the CloudFront origin (and the Vite dev
# server), a Cognito JWT authorizer on every route, and one Lambda per route.
#
# Routes:
# - GET /devices: devices view for staff and ops (docs/data-model.md, Devices view;
#   patterns 4, 5, 8; ops without pattern 8), Lambda silvercare-api-devices
#   (lambdas/api-devices/).
# - GET /dashboard: nurse and doctor dashboards (docs/data-model.md, Dashboard;
#   patterns 9, 8, 5, 4), Lambda silvercare-api-dashboard (lambdas/api-dashboard/).
# - POST /residents/{resident_id}/alerts/{alert_id}/acknowledge, .../cancel, and
#   .../resolve: alert actions (docs/data-model.md, Status updates; patterns 13, 16, 25),
#   one Lambda silvercare-api-alert-actions (lambdas/api-alert-actions/) for all three.
# - GET /residents/{resident_id} (resident page; patterns 10, 11, 12, 18) and
#   GET /me/residents (caregiver view; pattern 14), one Lambda silvercare-api-residents
#   (lambdas/api-residents/).
# - PUT /residents/{resident_id}/thresholds (doctor; pattern 26) and
#   GET /residents/{resident_id}/thresholds/history (staff; pattern 27), one Lambda
#   silvercare-api-thresholds (lambdas/api-thresholds/), so the read-only resident
#   page role never gets write access.
# - GET /residents/{resident_id}/nightly (doctor; pattern 31), Lambda
#   silvercare-api-nightly (lambdas/api-nightly/): stored nights from silvercare-results
#   (docs/features/nightly-results.md).
# - GET /ops/device-problems: the ops view (docs/data-model.md, the ops paragraph;
#   pattern 9 filtered to device problems), Lambda silvercare-api-ops
#   (lambdas/api-ops/).
#
# The authorizer checks the token's signature, expiry, issuer, and audience; each
# handler checks the caller's Cognito group (and, for resident routes, the caregiver
# link). The frontend sends the ID token, whose `aud` is the web client ID.
#
# Data: the access log has request ID, route, status, latency, and error strings only:
# no IP address, user, headers, query string, or body.
#
# Cost: about US$0 at this traffic. HTTP APIs are billed per request (about US$1.30
# per million in ap-southeast-1); the throttle below caps the worst case. The access log
# and the functions' logs are within the CloudWatch free allowance. Cognito AdminGetUser
# is free (it counts toward the pool's API rate limit, not the bill).

locals {
  api_name                        = "silvercare-api"
  api_devices_function_name       = "silvercare-api-devices"
  api_dashboard_function_name     = "silvercare-api-dashboard"
  api_alert_actions_function_name = "silvercare-api-alert-actions"
  api_residents_function_name     = "silvercare-api-residents"
  api_thresholds_function_name    = "silvercare-api-thresholds"
  api_nightly_function_name       = "silvercare-api-nightly"
  api_ops_function_name           = "silvercare-api-ops"
  thresholds_routes = {
    put = {
      route_key = "PUT /residents/{resident_id}/thresholds"
      arn_path  = "PUT/residents/*/thresholds"
      # One save: a strongly consistent GetItem (1 RCU) and a two-item transaction
      # (4 WCU) on the registry's 1 WCU base table, which ingest and the watchdog
      # don't write. Saves are rare; this caps a stuck client at burst capacity.
      rate  = 0.2
      burst = 2
    }
    history = {
      route_key = "GET /residents/{resident_id}/thresholds/history"
      arn_path  = "GET/residents/*/thresholds/history"
      # One Query page (up to 100 small items) per resident page view.
      rate  = 0.5
      burst = 2
    }
  }
  residents_routes = {
    detail = {
      route_key = "GET /residents/{resident_id}"
      arn_path  = "GET/residents/*"
      # One page view reads about 5 RCU, mostly the latest table's 4 RCU base, which
      # ingest's write rule also reads.
      rate  = 0.5
      burst = 2
    }
    mine = {
      route_key = "GET /me/residents"
      arn_path  = "GET/me/residents"
      # The same reads for every linked resident.
      rate  = 0.2
      burst = 2
    }
  }
  alert_action_routes = {
    acknowledge = "POST /residents/{resident_id}/alerts/{alert_id}/acknowledge"
    cancel      = "POST /residents/{resident_id}/alerts/{alert_id}/cancel"
    resolve     = "POST /residents/{resident_id}/alerts/{alert_id}/resolve"
  }

  # The frontend's CloudFront domain, pinned here rather than read from
  # aws_cloudfront_distribution.frontend: the distribution's CSP (frontend_hosting.tf)
  # names this API's URL, so the API can't also depend on the distribution (a cycle).
  # Set it to `terraform output -raw frontend_distribution_domain`. The precondition on
  # the API fails the plan until it is set, and the one on output "api_url"
  # (outputs.tf) fails it if it ever stops matching the distribution. Both are errors,
  # so they show in the PR plan comment (a check block's warning would not).
  frontend_cloudfront_domain = "d27x6wttloadms.cloudfront.net"
  frontend_origin            = "https://${local.frontend_cloudfront_domain}"
}

resource "aws_apigatewayv2_api" "api" {
  name          = local.api_name
  description   = "SilverCare HTTP API for the frontend (Cognito JWT on every route)."
  protocol_type = "HTTP"

  # Only the frontend's own origin and the local dev server, never "*". The token goes
  # in the Authorization header (no cookies), so credentials stay off. POST, PUT, and
  # content-type are for the alert actions' and the thresholds save's JSON bodies.
  cors_configuration {
    allow_origins     = [local.frontend_origin, "http://localhost:5173"]
    allow_methods     = ["GET", "POST", "PUT"]
    allow_headers     = ["authorization", "content-type"]
    allow_credentials = false
    max_age           = 3600
  }

  lifecycle {
    precondition {
      condition     = can(regex("^d[a-z0-9]+\\.cloudfront\\.net$", local.frontend_cloudfront_domain))
      error_message = "Set local.frontend_cloudfront_domain in infra/api.tf to the output of `terraform output -raw frontend_distribution_domain` (e.g. d1234abcd5678.cloudfront.net)."
    }
  }
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.api.id
  name             = "cognito"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    # endpoint is cognito-idp.<region>.amazonaws.com/<user_pool_id>.
    issuer   = "https://${aws_cognito_user_pool.users.endpoint}"
    audience = [aws_cognito_user_pool_client.web.id]
  }
}

resource "aws_cloudwatch_log_group" "api_access" {
  #checkov:skip=CKV_AWS_338:30-day retention, as the Lambda logs; access logs hold only request IDs, routes, statuses, and latencies.
  #checkov:skip=CKV_AWS_158:Encrypted with the CloudWatch Logs service key. A customer-managed KMS key costs ~US$1/month; the log holds no personal data.
  name              = "/aws/apigateway/${local.api_name}"
  retention_in_days = 30
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true

  # Default per-route limits (API Gateway applies them to each route separately, not
  # to the API as a whole). They cap the bill and the account's shared Lambda
  # concurrency (ingest and alert-processor too) even for a client stuck in a retry
  # loop.
  default_route_settings {
    throttling_rate_limit    = 5
    throttling_burst_limit   = 5
    detailed_metrics_enabled = false
  }

  # The two staff routes read the by-type and by-message indexes the watchdog also
  # reads (by-type 2 RCU, by-message 3 RCU, infra/dynamodb.tf). One load is about
  # 1.5 RCU on by-type and 2 RCU on by-message, so at the default 5/s a stuck client
  # would drain their burst capacity in minutes and throttle the watchdog's runs (an
  # alert path). 0.5/s per route still serves about 15 open dashboards polling every
  # 30 s.
  route_settings {
    route_key              = aws_apigatewayv2_route.get_dashboard.route_key
    throttling_rate_limit  = 0.5
    throttling_burst_limit = 2
  }

  route_settings {
    route_key              = aws_apigatewayv2_route.get_devices.route_key
    throttling_rate_limit  = 0.5
    throttling_burst_limit = 2
  }

  # The ops view: one `open` index Query plus a BatchGetItem of every open device
  # problem's base item (0.5 RCU each) on the alerts base table's 3 RCU, which
  # alert-processor reads strongly consistent on the alert path. 40 offline devices
  # would be 20 RCU per load, so a stuck client is held to one load per 20 s
  # (about 1 RCU/s). The ops dashboard polls every 30 s while its tab is visible, so a
  # second ops user's polls can be throttled (429): the page keeps its last data.
  route_settings {
    route_key              = aws_apigatewayv2_route.get_ops_device_problems.route_key
    throttling_rate_limit  = 0.05
    throttling_burst_limit = 2
  }

  dynamic "route_settings" {
    for_each = local.residents_routes
    content {
      route_key              = aws_apigatewayv2_route.residents[route_settings.key].route_key
      throttling_rate_limit  = route_settings.value.rate
      throttling_burst_limit = route_settings.value.burst
    }
  }

  dynamic "route_settings" {
    for_each = local.thresholds_routes
    content {
      route_key              = aws_apigatewayv2_route.thresholds[route_settings.key].route_key
      throttling_rate_limit  = route_settings.value.rate
      throttling_burst_limit = route_settings.value.burst
    }
  }

  # Nightly summaries (user's decision 2026-11-07): one page view Queries up to about 30
  # items (about 6 RCU) on the Results table's 1 RCU, so at the default 5/s a stuck
  # client would drain its burst capacity and every doctor's section would fail.
  route_settings {
    route_key              = aws_apigatewayv2_route.get_nightly.route_key
    throttling_rate_limit  = 0.1
    throttling_burst_limit = 2
  }

  # Alert actions (acknowledge, cancel, resolve), per call: a strongly consistent alerts
  # GetItem (1 RCU on the alerts base table's 3 RCU, shared with alert-processor), for a
  # caregiver also an eventually consistent registry link GetItem (0.5 RCU), and one SQS
  # send. Each route has its own allowance. A nurse may acknowledge or resolve a few
  # alerts in a row, so a burst of 5.
  dynamic "route_settings" {
    for_each = aws_apigatewayv2_route.alert_action
    content {
      route_key              = route_settings.value.route_key
      throttling_rate_limit  = 1
      throttling_burst_limit = 5
    }
  }

  # No $context.identity.sourceIp, user, or claims: nothing personal.
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId        = "$context.requestId"
      requestTime      = "$context.requestTime"
      routeKey         = "$context.routeKey"
      status           = "$context.status"
      responseLatency  = "$context.responseLatency"
      integrationError = "$context.integrationErrorMessage"
      authorizerError  = "$context.authorizer.error"
    })
  }
}

# ---------------------------------------------------------------------------------------
# GET /devices
# ---------------------------------------------------------------------------------------

data "archive_file" "api_devices" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/api-devices/src"
  output_path = "${path.module}/.terraform/build/api-devices.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function does with AWS: three eventually consistent, paginated Queries, no
# writes and no base-table reads.
# - registry by-type (patterns 4 and 8): only the listed attributes, with Select =
#   SPECIFIC_ATTRIBUTES, so it can never read photo_key (the index also projects it).
#   entity_type is the key condition; sk is in a later page's ExclusiveStartKey.
# - latest by-message (pattern 5): the handler queries watch_type = status only and
#   projects pk and four fields of latest. IAM can't narrow this further: the role may
#   query every watch_type (vitals, bed, motion too) and read the whole `latest` map,
#   because dynamodb:Attributes checks top-level names only. The watchdog's role has the
#   same access (watchdog.tf); the handler and its tests keep the reads to status.
# The module adds the log group permissions.
data "aws_iam_policy_document" "api_devices" {
  statement {
    sid       = "RegistryByTypeIndex"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = ["${module.registry_table.arn}/index/by-type"]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["pk", "sk", "entity_type", "device_type", "resident_id", "home_id", "display_name"]
    }

    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
  }

  statement {
    sid       = "LatestByMessageIndex"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = ["${module.latest_table.arn}/index/by-message"]
  }
}

module "api_devices" {
  source = "./modules/python-lambda"

  function_name = local.api_devices_function_name
  description   = "GET /devices: every device with its newest status and assigned resident, for staff and ops."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.api_devices.output_path
  zip_hash      = data.archive_file.api_devices.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  # Three small paginated Queries (40 devices, 20 residents): well under a second.
  timeout = 10

  environment = {
    REGISTRY_TABLE          = module.registry_table.name
    LATEST_TABLE            = module.latest_table.name
    POWERTOOLS_SERVICE_NAME = local.api_devices_function_name
  }

  policy_json        = data.aws_iam_policy_document.api_devices.json
  log_retention_days = 30

  # Invoked synchronously by API Gateway: no async retries, so no on-failure destination.
}

resource "aws_apigatewayv2_integration" "api_devices" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.api_devices.function_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 12000
}

resource "aws_apigatewayv2_route" "get_devices" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "GET /devices"
  target             = "integrations/${aws_apigatewayv2_integration.api_devices.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

# Only this API's GET /devices route (any stage) may invoke the function.
resource "aws_lambda_permission" "api_devices" {
  statement_id  = "AllowApiGatewayGetDevices"
  action        = "lambda:InvokeFunction"
  function_name = module.api_devices.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/GET/devices"
}

# ---------------------------------------------------------------------------------------
# GET /dashboard
# ---------------------------------------------------------------------------------------

data "archive_file" "api_dashboard" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/api-dashboard/src"
  output_path = "${path.module}/.terraform/build/api-dashboard.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function does with AWS: five eventually consistent, paginated Queries, one
# BatchGetItem (cached 5 minutes), and Cognito name lookups. No writes, and no reads of
# resident profiles, links, alert details, or full latest entries.
# - alerts open (pattern 9): only the alert-row attributes the index projects, with
#   Select = SPECIFIC_ATTRIBUTES, so never `details` (not projected either).
# - registry by-type (patterns 4 and 8): GET /devices' attribute list minus
#   device_type, so it can never read photo_key.
# - latest by-message (pattern 5): the handler queries `status` and `vitals` and
#   projects only the fields the cards show. As for GET /devices and the watchdog, IAM
#   can't narrow this to one watch_type or to fields inside `latest`.
# - registry THRESHOLDS# items (for the resident status's abnormal-reading rule): only
#   those partitions (dynamodb:LeadingKeys, checked for every key), so never a
#   RESIDENT# profile or USER# link, and only the three limits (never updated_by, a
#   sub, or updated_by_name). The Select condition is AWS's documented companion
#   (IfExists: BatchGetItem has no Select parameter, so plain StringEquals could deny
#   the handler's own call).
# Every statement with an attribute list also has Null dynamodb:Attributes = false, as
# api-residents: a request without a projection would otherwise pass ForAllValues
# vacuously (dynamodb:Attributes absent) and read whole items. The handler projects on
# every read. LatestByMessageIndex has no attribute list (see above), so no Null either.
# - Cognito AdminGetUser with the sub as username: it can't list or search users, so
#   the role can't enumerate the pool, but each call returns that user's attributes
#   (email included); the handler keeps only `name`. Accepted while all users are
#   synthetic test accounts (docs/data-model.md, Before any real resident data arrives).
# The module adds the log group permissions.
data "aws_iam_policy_document" "api_dashboard" {
  statement {
    sid       = "AlertsOpenIndex"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = ["${module.alerts_table.arn}/index/open"]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values = [
        "pk", "sk", "open_pk", "alert_type", "device_id", "status", "event_ts", "severity",
        "acknowledged_at", "ack_note", "acknowledged_by_role", "acknowledged_by",
        "escalated_at", "notified_at",
      ]
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
    sid       = "RegistryByTypeIndex"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = ["${module.registry_table.arn}/index/by-type"]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["pk", "sk", "entity_type", "resident_id", "home_id", "display_name"]
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
    sid       = "LatestByMessageIndex"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = ["${module.latest_table.arn}/index/by-message"]
  }

  statement {
    sid       = "RegistryThresholds"
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
      values   = ["pk", "sk", "hr_bpm", "spo2_pct", "skin_temp_c"]
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
    sid       = "CognitoNames"
    effect    = "Allow"
    actions   = ["cognito-idp:AdminGetUser"]
    resources = [aws_cognito_user_pool.users.arn]
  }
}

module "api_dashboard" {
  source = "./modules/python-lambda"

  function_name = local.api_dashboard_function_name
  description   = "GET /dashboard: open alerts, residents, status, and device state, for staff."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.api_dashboard.output_path
  zip_hash      = data.archive_file.api_dashboard.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  # Five small paginated Queries, one BatchGetItem, and a few Cognito lookups.
  timeout = 10

  environment = {
    ALERTS_TABLE            = module.alerts_table.name
    REGISTRY_TABLE          = module.registry_table.name
    LATEST_TABLE            = module.latest_table.name
    USER_POOL_ID            = aws_cognito_user_pool.users.id
    POWERTOOLS_SERVICE_NAME = local.api_dashboard_function_name
  }

  policy_json        = data.aws_iam_policy_document.api_dashboard.json
  log_retention_days = 30

  # Invoked synchronously by API Gateway: no async retries, so no on-failure destination.
}

resource "aws_apigatewayv2_integration" "api_dashboard" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.api_dashboard.function_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 12000
}

resource "aws_apigatewayv2_route" "get_dashboard" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "GET /dashboard"
  target             = "integrations/${aws_apigatewayv2_integration.api_dashboard.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

# Only this API's GET /dashboard route (any stage) may invoke the function.
resource "aws_lambda_permission" "api_dashboard" {
  statement_id  = "AllowApiGatewayGetDashboard"
  action        = "lambda:InvokeFunction"
  function_name = module.api_dashboard.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/GET/dashboard"
}

# ---------------------------------------------------------------------------------------
# GET /ops/device-problems
# ---------------------------------------------------------------------------------------

data "archive_file" "api_ops" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/api-ops/src"
  output_path = "${path.module}/.terraform/build/api-ops.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function does with AWS: one eventually consistent, paginated Query of the
# alerts `open` index with a FilterExpression to the three device-problem types
# (pattern 9), one BatchGetItem of those alerts' base items for `details` (the index
# doesn't project it), and Cognito name lookups. No writes; no registry or latest
# access at all (ops get no resident profiles or readings).
# - AlertsOpenIndex: the alert-row attributes minus ack_note, escalated_at, and
#   notified_at, with Select = SPECIFIC_ATTRIBUTES.
# - AlertItems: RESIDENT# partitions, only pk, sk, alert_type, details, resolved_at,
#   resolved_by_role. IAM can't restrict a read by alert_type, so the role could read
#   a health alert's details if the handler asked for its key: the handler only asks
#   for keys of items it has already checked are device problems, and
#   lambdas/api-ops/tests/test_filtering.py checks that.
# Both have Null dynamodb:Attributes = false, as api-residents: a request without a
# projection would otherwise pass ForAllValues vacuously and read whole items.
# - Cognito AdminGetUser: as api-dashboard (the handler keeps `name`).
data "aws_iam_policy_document" "api_ops" {
  statement {
    sid       = "AlertsOpenIndex"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = ["${module.alerts_table.arn}/index/open"]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values = [
        "pk", "sk", "open_pk", "alert_type", "device_id", "status", "event_ts", "severity",
        "acknowledged_at", "acknowledged_by_role", "acknowledged_by",
      ]
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
    sid       = "AlertItems"
    effect    = "Allow"
    actions   = ["dynamodb:BatchGetItem"]
    resources = [module.alerts_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["RESIDENT#*"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["pk", "sk", "alert_type", "details", "resolved_at", "resolved_by_role"]
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
    sid       = "CognitoNames"
    effect    = "Allow"
    actions   = ["cognito-idp:AdminGetUser"]
    resources = [aws_cognito_user_pool.users.arn]
  }
}

module "api_ops" {
  source = "./modules/python-lambda"

  function_name = local.api_ops_function_name
  description   = "GET /ops/device-problems: open device problems for every resident, for ops."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.api_ops.output_path
  zip_hash      = data.archive_file.api_ops.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  timeout       = 10

  environment = {
    ALERTS_TABLE            = module.alerts_table.name
    USER_POOL_ID            = aws_cognito_user_pool.users.id
    POWERTOOLS_SERVICE_NAME = local.api_ops_function_name
  }

  policy_json        = data.aws_iam_policy_document.api_ops.json
  log_retention_days = 30

  # Invoked synchronously by API Gateway: no async retries, so no on-failure destination.
}

resource "aws_apigatewayv2_integration" "api_ops" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.api_ops.function_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 12000
}

resource "aws_apigatewayv2_route" "get_ops_device_problems" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "GET /ops/device-problems"
  target             = "integrations/${aws_apigatewayv2_integration.api_ops.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

# Only this API's GET /ops/device-problems route (any stage) may invoke the function.
resource "aws_lambda_permission" "api_ops" {
  statement_id  = "AllowApiGatewayGetOpsDeviceProblems"
  action        = "lambda:InvokeFunction"
  function_name = module.api_ops.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/GET/ops/device-problems"
}

# ---------------------------------------------------------------------------------------
# POST /residents/{resident_id}/alerts/{alert_id}/acknowledge, .../cancel, .../resolve
# ---------------------------------------------------------------------------------------

data "archive_file" "api_alert_actions" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/api-alert-actions/src"
  output_path = "${path.module}/.terraform/build/api-alert-actions.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function does with AWS: per call, at most one registry GetItem (the
# caregiver's link, pattern 13), one alerts GetItem (pattern 25), and one SQS send of a
# status_update. It never writes an alert: alert-processor applies the update.
# - registry: GetItem on USER# partitions only, so never a RESIDENT# profile or
#   THRESHOLDS#. IAM can't pin the partition to the caller's own sub (the Lambda, not
#   the user, is the principal); the handler builds the key from the token's sub.
# - alerts: GetItem on RESIDENT# partitions, only pk, sk, alert_type, and status (never
#   details, ack_note, resolve_note, or subs).
# Both GetItem statements also have Null dynamodb:Attributes = false, as api-residents:
# a GetItem without a projection would otherwise pass ForAllValues vacuously
# (dynamodb:Attributes absent) and read the whole item. The handler projects on both.
# - sqs:SendMessage on silvercare-alerts (SQS-managed encryption: no KMS permission).
data "aws_iam_policy_document" "api_alert_actions" {
  statement {
    sid       = "RegistryUserLinks"
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

  statement {
    sid       = "AlertItems"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [module.alerts_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["RESIDENT#*"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["pk", "sk", "alert_type", "status"]
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
    sid       = "AlertsQueue"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.alerts.arn]
  }
}

module "api_alert_actions" {
  source = "./modules/python-lambda"

  function_name = local.api_alert_actions_function_name
  description   = "POST acknowledge / cancel / resolve: checks the caller and alert, then queues a status_update."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.api_alert_actions.output_path
  zip_hash      = data.archive_file.api_alert_actions.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  timeout       = 10

  environment = {
    ALERTS_TABLE            = module.alerts_table.name
    REGISTRY_TABLE          = module.registry_table.name
    ALERTS_QUEUE_URL        = aws_sqs_queue.alerts.id
    POWERTOOLS_SERVICE_NAME = local.api_alert_actions_function_name
  }

  policy_json        = data.aws_iam_policy_document.api_alert_actions.json
  log_retention_days = 30

  # Invoked synchronously by API Gateway: no async retries, so no on-failure destination.
}

resource "aws_apigatewayv2_integration" "api_alert_actions" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.api_alert_actions.function_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 12000
}

resource "aws_apigatewayv2_route" "alert_action" {
  for_each = local.alert_action_routes

  api_id             = aws_apigatewayv2_api.api.id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.api_alert_actions.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

# Only this API's three alert-action routes (any stage) may invoke the function.
resource "aws_lambda_permission" "api_alert_actions" {
  for_each = local.alert_action_routes

  statement_id  = "AllowApiGatewayAlert${title(each.key)}"
  action        = "lambda:InvokeFunction"
  function_name = module.api_alert_actions.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/POST/residents/*/alerts/*/${each.key}"
}

# ---------------------------------------------------------------------------------------
# GET /residents/{resident_id} and GET /me/residents
# ---------------------------------------------------------------------------------------

data "archive_file" "api_residents" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/api-residents/src"
  output_path = "${path.module}/.terraform/build/api-residents.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function does with AWS, per resident: a by-resident Query (pattern 10), a
# registry BatchGetItem (profile, thresholds, the devices' ASSIGNMENT items), a latest
# BatchGetItem (pattern 11), an alerts Query (pattern 12); per caller a link GetItem
# (pattern 13) or links Query (pattern 14); Cognito AdminGetUser for actor names. No
# writes. Every read must name its attributes: a request without a projection would
# pass ForAllValues vacuously (dynamodb:Attributes absent), so each statement also has
# Null dynamodb:Attributes = false, which denies it. With that, IAM itself (not only
# the handler) keeps RESIDENT# profiles to display_name and home_id, never
# medical_notes, emergency_contacts, year_of_birth, or photo_key; alerts without
# details, resolve_note, or source_message_id; latest entries whole (received_at and
# clock_skew are dropped by the handler).
data "aws_iam_policy_document" "api_residents" {
  statement {
    sid       = "RegistryByResidentIndex"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = ["${module.registry_table.arn}/index/by-resident"]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["gsi1_pk", "gsi1_sk", "pk", "sk"]
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
    sid       = "RegistryUserLinks"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:Query"]
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

  statement {
    sid       = "RegistryResidentItems"
    effect    = "Allow"
    actions   = ["dynamodb:BatchGetItem"]
    resources = [module.registry_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["RESIDENT#*", "THRESHOLDS#*", "DEVICE#*"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values = [
        "pk", "sk", "display_name", "home_id", "hr_bpm", "spo2_pct", "skin_temp_c",
        "updated_at", "updated_by_name", "resident_id", "assigned_at",
      ]
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
    sid       = "LatestEntries"
    effect    = "Allow"
    actions   = ["dynamodb:BatchGetItem"]
    resources = [module.latest_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["DEVICE#*"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["pk", "sk", "entries"]
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
    sid       = "AlertsByResident"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = [module.alerts_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["RESIDENT#*"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values = [
        "pk", "sk", "alert_type", "device_id", "status", "event_ts", "severity",
        "acknowledged_at", "acknowledged_by_role", "acknowledged_by", "ack_note",
        "escalated_at", "notified_at", "resolved_at", "resolved_by", "resolved_by_role",
        "cancelled_at", "cancelled_by_role", "cancelled_by",
      ]
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
    sid       = "CognitoNames"
    effect    = "Allow"
    actions   = ["cognito-idp:AdminGetUser"]
    resources = [aws_cognito_user_pool.users.arn]
  }
}

module "api_residents" {
  source = "./modules/python-lambda"

  function_name = local.api_residents_function_name
  description   = "GET /residents/{id} and GET /me/residents: resident page and caregiver view."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.api_residents.output_path
  zip_hash      = data.archive_file.api_residents.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  timeout       = 10

  environment = {
    ALERTS_TABLE            = module.alerts_table.name
    REGISTRY_TABLE          = module.registry_table.name
    LATEST_TABLE            = module.latest_table.name
    USER_POOL_ID            = aws_cognito_user_pool.users.id
    POWERTOOLS_SERVICE_NAME = local.api_residents_function_name
  }

  policy_json        = data.aws_iam_policy_document.api_residents.json
  log_retention_days = 30

  # Invoked synchronously by API Gateway: no async retries, so no on-failure destination.
}

resource "aws_apigatewayv2_integration" "api_residents" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.api_residents.function_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 12000
}

resource "aws_apigatewayv2_route" "residents" {
  for_each = local.residents_routes

  api_id             = aws_apigatewayv2_api.api.id
  route_key          = each.value.route_key
  target             = "integrations/${aws_apigatewayv2_integration.api_residents.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

# Only this API's two resident routes (any stage) may invoke the function.
resource "aws_lambda_permission" "api_residents" {
  for_each = local.residents_routes

  statement_id  = "AllowApiGatewayResidents${title(each.key)}"
  action        = "lambda:InvokeFunction"
  function_name = module.api_residents.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/${each.value.arn_path}"
}

# ---------------------------------------------------------------------------------------
# PUT /residents/{resident_id}/thresholds and GET .../thresholds/history
# ---------------------------------------------------------------------------------------

data "archive_file" "api_thresholds" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/api-thresholds/src"
  output_path = "${path.module}/.terraform/build/api-thresholds.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function does with AWS: per save, a strongly consistent GetItem of
# THRESHOLDS#<id> (pattern 26's read), AdminGetUser for the doctor's name, and one
# TransactWriteItems (an UpdateItem of THRESHOLDS#<id>, a PutItem of a THRESHOLD_LOG#
# item; IAM authorises a transaction as those two actions); per history view, one Query
# of THRESHOLD_LOG#<id> (pattern 27).
# - Registry partitions only: THRESHOLDS# for the read and update, THRESHOLD_LOG# for
#   the log write and Query, so never a RESIDENT# profile, USER# link, or DEVICE# item.
# - Reads must name their attributes (Null dynamodb:Attributes = false, as for
#   api-residents): the thresholds read can't see updated_by (a sub), and the history
#   Query can never return changed_by.
# - The writes have no attribute list: those partitions hold only thresholds and log
#   items. The role can overwrite a log item (IAM can't require the handler's
#   attribute_not_exists condition), so the log is tamper-evident only via CloudTrail.
# - Cognito AdminGetUser: as api-residents and api-dashboard (the handler keeps `name`).
data "aws_iam_policy_document" "api_thresholds" {
  statement {
    sid       = "ThresholdsRead"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [module.registry_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["THRESHOLDS#*"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["pk", "sk", "hr_bpm", "spo2_pct", "skin_temp_c", "updated_at", "updated_by_name"]
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
    sid       = "ThresholdsUpdate"
    effect    = "Allow"
    actions   = ["dynamodb:UpdateItem"]
    resources = [module.registry_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["THRESHOLDS#*"]
    }
  }

  statement {
    sid       = "ChangeLogWrite"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem"]
    resources = [module.registry_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["THRESHOLD_LOG#*"]
    }
  }

  statement {
    sid       = "ChangeLogRead"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = [module.registry_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["THRESHOLD_LOG#*"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["pk", "sk", "change_id", "changed_at", "changed_by_name", "changes"]
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
    sid       = "CognitoNames"
    effect    = "Allow"
    actions   = ["cognito-idp:AdminGetUser"]
    resources = [aws_cognito_user_pool.users.arn]
  }
}

module "api_thresholds" {
  source = "./modules/python-lambda"

  function_name = local.api_thresholds_function_name
  description   = "PUT thresholds (doctor) and GET thresholds/history (staff) for one resident."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.api_thresholds.output_path
  zip_hash      = data.archive_file.api_thresholds.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  timeout       = 10

  environment = {
    REGISTRY_TABLE          = module.registry_table.name
    USER_POOL_ID            = aws_cognito_user_pool.users.id
    POWERTOOLS_SERVICE_NAME = local.api_thresholds_function_name
  }

  policy_json        = data.aws_iam_policy_document.api_thresholds.json
  log_retention_days = 30

  # Invoked synchronously by API Gateway: no async retries, so no on-failure destination.
}

resource "aws_apigatewayv2_integration" "api_thresholds" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.api_thresholds.function_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 12000
}

resource "aws_apigatewayv2_route" "thresholds" {
  for_each = local.thresholds_routes

  api_id             = aws_apigatewayv2_api.api.id
  route_key          = each.value.route_key
  target             = "integrations/${aws_apigatewayv2_integration.api_thresholds.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

# Only this API's two threshold routes (any stage) may invoke the function.
resource "aws_lambda_permission" "api_thresholds" {
  for_each = local.thresholds_routes

  statement_id  = "AllowApiGatewayThresholds${title(each.key)}"
  action        = "lambda:InvokeFunction"
  function_name = module.api_thresholds.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/${each.value.arn_path}"
}

# ---------------------------------------------------------------------------------------
# GET /residents/{resident_id}/nightly
# ---------------------------------------------------------------------------------------

data "archive_file" "api_nightly" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/api-nightly/src"
  output_path = "${path.module}/.terraform/build/api-nightly.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function does with AWS (criterion 23): one eventually consistent, paginated
# Query of RESIDENT#<id> on the Results table (pattern 31). No writes, no other table, no
# registry read (no profile check, by the user's decision). RESIDENT# partitions only;
# the table holds nothing else.
data "aws_iam_policy_document" "api_nightly" {
  statement {
    sid       = "ResultsByResident"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = [module.results_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["RESIDENT#*"]
    }
  }
}

module "api_nightly" {
  source = "./modules/python-lambda"

  function_name = local.api_nightly_function_name
  description   = "GET /residents/{id}/nightly: a resident's stored nightly summaries, for doctors."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.api_nightly.output_path
  zip_hash      = data.archive_file.api_nightly.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  # One small paginated Query (about 30 items).
  timeout = 10

  environment = {
    RESULTS_TABLE           = module.results_table.name
    POWERTOOLS_SERVICE_NAME = local.api_nightly_function_name
  }

  policy_json        = data.aws_iam_policy_document.api_nightly.json
  log_retention_days = 30

  # Invoked synchronously by API Gateway: no async retries, so no on-failure destination.
}

resource "aws_apigatewayv2_integration" "api_nightly" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.api_nightly.function_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 12000
}

resource "aws_apigatewayv2_route" "get_nightly" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "GET /residents/{resident_id}/nightly"
  target             = "integrations/${aws_apigatewayv2_integration.api_nightly.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

# Only this API's GET /residents/{resident_id}/nightly route (any stage) may invoke the
# function.
resource "aws_lambda_permission" "api_nightly" {
  statement_id  = "AllowApiGatewayGetNightly"
  action        = "lambda:InvokeFunction"
  function_name = module.api_nightly.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/GET/residents/*/nightly"
}
