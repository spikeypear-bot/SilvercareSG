# HTTP API for the frontend (API Gateway v2, the api-route and cognito-auth skills):
# one $default stage with throttling, CORS for the CloudFront origin (and the Vite dev
# server), a Cognito JWT authorizer on every route, and one Lambda per route.
#
# Routes:
# - GET /devices: staff devices view (docs/data-model.md, Devices view; patterns 4, 5,
#   8), Lambda silvercare-api-devices (lambdas/api-devices/).
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
# and the function's log are within the CloudWatch free allowance.

locals {
  api_name                  = "silvercare-api"
  api_devices_function_name = "silvercare-api-devices"

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
  # in the Authorization header (no cookies), so credentials stay off.
  cors_configuration {
    allow_origins     = [local.frontend_origin, "http://localhost:5173"]
    allow_methods     = ["GET"]
    allow_headers     = ["authorization"]
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

  # Per-API limits for a handful of staff and caregivers (30-second polling is far
  # below this). They protect the account's shared Lambda concurrency (ingest and
  # alert-processor too), the DynamoDB capacity (by-type has 2 RCU, shared with the
  # watchdog), and the bill, even from a client stuck in a retry loop.
  default_route_settings {
    throttling_rate_limit    = 5
    throttling_burst_limit   = 5
    detailed_metrics_enabled = false
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
  description   = "GET /devices: every device with its newest status and assigned resident, for staff."
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
