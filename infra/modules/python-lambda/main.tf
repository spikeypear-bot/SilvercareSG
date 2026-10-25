# One Python Lambda with its own least-privilege role, its own log group (created here,
# so the role needs no logs:CreateLogGroup), and, when an on-failure destination is
# given, an async invoke config that sends failed async invocations there. See README.md.

# Trusted only by Lambda. No aws:SourceAccount condition: AWS documents the Lambda
# execution role trust policy without one, and if Lambda doesn't send the key, creating
# the function fails. Only someone with iam:PassRole can attach this role to a function.
data "aws_iam_policy_document" "assume" {
  statement {
    sid     = "LambdaAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = var.function_name
  description        = "Execution role of the ${var.function_name} Lambda only."
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_cloudwatch_log_group" "this" {
  #checkov:skip=CKV_AWS_338:30-day retention set by the spec (docs/features/ingest-live.md); logs hold only IDs and outcomes.
  #checkov:skip=CKV_AWS_158:Encrypted with the CloudWatch Logs service key. A customer-managed KMS key costs ~US$1/month; logs hold no field values.
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

# What every function built by this module needs, added to the caller's policy:
# write to its own log group only, and send to its own on-failure destination (only
# when one is given).
data "aws_iam_policy_document" "this" {
  source_policy_documents = [var.policy_json]

  statement {
    sid     = "OwnLogGroup"
    effect  = "Allow"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    # The provider's log group ARN has no ":*" suffix; streams are under "<arn>:*".
    resources = ["${aws_cloudwatch_log_group.this.arn}:*"]
  }

  dynamic "statement" {
    for_each = var.on_failure_destination == null ? [] : [var.on_failure_destination.arn]

    content {
      sid       = "OnFailureDestination"
      effect    = "Allow"
      actions   = ["sqs:SendMessage"]
      resources = [statement.value]
    }
  }
}

# The role's one inline policy. No managed policies are attached.
resource "aws_iam_role_policy" "this" {
  name   = var.function_name
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.this.json
}

resource "aws_lambda_function" "this" {
  #checkov:skip=CKV_AWS_116:No Lambda DLQ. An async-invoked function can be given an on-failure destination (aws_lambda_function_event_invoke_config below); the ingest functions have none by the user's decision (raw messages are kept in S3 history). SQS-triggered functions use the source queue's redrive DLQ.
  #checkov:skip=CKV_AWS_117:No VPC. The function only calls DynamoDB and SQS over TLS; a VPC would need endpoints or a NAT Gateway (fixed cost).
  #checkov:skip=CKV_AWS_115:No reserved concurrency yet; it needs the account's Lambda concurrency limit checked first (spec, Out of scope).
  #checkov:skip=CKV_AWS_173:Environment variables hold only resource names, no secrets; a customer-managed KMS key would add cost.
  #checkov:skip=CKV_AWS_50:X-Ray tracing off on purpose (spec): traces could carry request details and add cost.
  #checkov:skip=CKV_AWS_272:No code signing. Code is deployed only by the CI deploy role from the reviewed main branch.
  function_name    = var.function_name
  description      = var.description
  role             = aws_iam_role.this.arn
  handler          = var.handler
  runtime          = "python3.13"
  architectures    = ["arm64"]
  memory_size      = var.memory_size
  timeout          = var.timeout
  filename         = var.zip_path
  source_code_hash = var.zip_hash
  layers           = var.layers

  environment {
    variables = var.environment
  }

  tracing_config {
    mode = "PassThrough"
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.this.name
  }

  tags = var.tags

  # The log group and the role's permissions exist before the function first runs.
  depends_on = [aws_cloudwatch_log_group.this, aws_iam_role_policy.this]
}

# Only for async-invoked functions (on_failure_destination set). Functions fed by an SQS
# event source mapping get none: the queue's redrive policy handles failures.
resource "aws_lambda_function_event_invoke_config" "this" {
  count = var.on_failure_destination == null ? 0 : 1

  function_name                = aws_lambda_function.this.function_name
  maximum_retry_attempts       = var.maximum_retry_attempts
  maximum_event_age_in_seconds = var.maximum_event_age_in_seconds

  destination_config {
    on_failure {
      destination = var.on_failure_destination.arn
    }
  }

  depends_on = [aws_iam_role_policy.this]
}

