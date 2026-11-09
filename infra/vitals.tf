# Vitals trends for doctors (docs/features/vitals-trends.md): GET
# /residents/{resident_id}/vitals, Lambda silvercare-api-vitals (lambdas/api-vitals/).
# Daily rows: one Athena query of daily_summaries for one resident. Hourly rows: the
# resident's devices and thresholds from the registry, then one Athena query of six
# clean tables. Runs in its own workgroup (clean.tf, local.athena_workgroups.vitals).
#
# Data: one resident's aggregated health readings per hour or day, read only. Nothing is
# stored except Athena result files (7-day expiry, bucket-wide rule in clean.tf).
#
# Cost: Athena about US$0.00005 per query (10 MB minimum), Lambda and API Gateway within
# free allowances. No fixed fees.

locals {
  api_vitals_function_name = "silvercare-api-vitals"

  # What the hourly query reads (criterion 15): the summaries' six source tables.
  vitals_clean_tables = local.summaries_source_tables
}

# ---------------------------------------------------------------------------------------
# Lake Formation grants (criterion 15): SELECT on daily_summaries and the six clean
# tables only. No raw_telemetry, rejected, status tables, or any write.
# ---------------------------------------------------------------------------------------

resource "aws_lakeformation_permissions" "vitals_default_database" {
  principal   = module.api_vitals.role_arn
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.default.name
  }
}

resource "aws_lakeformation_permissions" "vitals_lake_database" {
  principal   = module.api_vitals.role_arn
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.lake.name
  }
}

resource "aws_lakeformation_permissions" "vitals_clean_tables" {
  for_each = local.vitals_clean_tables

  principal   = module.api_vitals.role_arn
  permissions = ["SELECT"]

  table {
    database_name = aws_glue_catalog_database.lake.name
    name          = aws_glue_catalog_table.output[each.key].name
  }
}

resource "aws_lakeformation_permissions" "vitals_daily_summaries" {
  principal   = module.api_vitals.role_arn
  permissions = ["SELECT"]

  table {
    database_name = aws_glue_catalog_database.lake.name
    name          = aws_glue_catalog_table.daily_summaries.name
  }
}

# ---------------------------------------------------------------------------------------
# Lambda
# ---------------------------------------------------------------------------------------

data "archive_file" "api_vitals" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/api-vitals/src"
  output_path = "${path.module}/.terraform/build/api-vitals.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function does with AWS: Athena queries in its own workgroup (start, poll,
# results, stop on timeout), for which Athena uses this role for Glue, Lake Formation,
# and its results prefix; for hourly rows a by-resident Query (pattern 10) and a
# BatchGetItem of DEVICE#<id>/ASSIGNMENT and THRESHOLDS#<id>/THRESHOLDS (patterns 29,
# 18). No writes to DynamoDB or lake data, no other table or partition. Clean data and
# summaries are read through Lake Formation's credentials, so there is no S3 access to
# the clean or history bucket. The module adds the log group permissions.
data "aws_iam_policy_document" "api_vitals" {
  statement {
    sid    = "OwnAthenaWorkgroup"
    effect = "Allow"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
    ]
    resources = [aws_athena_workgroup.lake["vitals"].arn]
  }

  statement {
    sid    = "LakeCatalogRead"
    effect = "Allow"
    actions = [
      "glue:GetDatabase",
      "glue:GetTable",
      "glue:GetPartition",
      "glue:GetPartitions",
      "glue:BatchGetPartition",
    ]
    # Only the tables the queries read, not the whole database.
    resources = concat(
      [
        "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:catalog",
        aws_glue_catalog_database.lake.arn,
        aws_glue_catalog_table.daily_summaries.arn,
      ],
      [for t in local.vitals_clean_tables : aws_glue_catalog_table.output[t].arn],
    )
  }

  # Goes with the Lake Formation DESCRIBE on default, which AWS requires for Athena.
  statement {
    sid       = "DefaultDatabase"
    effect    = "Allow"
    actions   = ["glue:GetDatabase"]
    resources = ["arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:database/${aws_glue_catalog_database.default.name}"]
  }

  # AWS only supports * for this action; Lake Formation decides what it returns.
  statement {
    sid       = "LakeFormationDataAccess"
    effect    = "Allow"
    actions   = ["lakeformation:GetDataAccess"]
    resources = ["*"]
  }

  statement {
    sid    = "OwnResultObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.lake["athena_results"].arn}/${local.athena_workgroups.vitals.prefix}/*"]
  }

  statement {
    sid    = "ResultsBucketInfo"
    effect = "Allow"
    actions = [
      "s3:GetBucketLocation",
      "s3:ListBucketMultipartUploads",
    ]
    resources = [aws_s3_bucket.lake["athena_results"].arn]
  }

  statement {
    sid       = "ListOwnResults"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lake["athena_results"].arn]

    condition {
      test     = "StringLikeIfExists"
      variable = "s3:prefix"
      values = [
        local.athena_workgroups.vitals.prefix,
        "${local.athena_workgroups.vitals.prefix}/*",
      ]
    }
  }

  # Pattern 10, keys only (the index projects `relationship` too), as api_residents.
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

  # Device assignments and thresholds only, named attributes only: no RESIDENT#
  # profiles, USER# links, or threshold history.
  statement {
    sid       = "RegistryDevicesAndThresholds"
    effect    = "Allow"
    actions   = ["dynamodb:BatchGetItem"]
    resources = [module.registry_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["DEVICE#*", "THRESHOLDS#*"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values = [
        "pk", "sk", "resident_id", "device_type", "assigned_at",
        "hr_bpm", "spo2_pct", "skin_temp_c",
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
}

module "api_vitals" {
  source = "./modules/python-lambda"

  function_name = local.api_vitals_function_name
  description   = "GET /residents/{id}/vitals: a resident's vitals trends per hour or day, for doctors."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.api_vitals.output_path
  zip_hash      = data.archive_file.api_vitals.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 256
  # Under the integration's 29 s; the handler stops a query still running at 25 s.
  timeout = 28

  environment = {
    REGISTRY_TABLE          = module.registry_table.name
    GLUE_DATABASE           = aws_glue_catalog_database.lake.name
    ATHENA_WORKGROUP        = aws_athena_workgroup.lake["vitals"].name
    POWERTOOLS_SERVICE_NAME = local.api_vitals_function_name
  }

  policy_json        = data.aws_iam_policy_document.api_vitals.json
  log_retention_days = 30

  # Invoked synchronously by API Gateway: no async retries, so no on-failure destination.
}

resource "aws_apigatewayv2_integration" "api_vitals" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.api_vitals.function_arn
  payload_format_version = "2.0"
  # HTTP API's maximum is 30 s.
  timeout_milliseconds = 29000
}

resource "aws_apigatewayv2_route" "get_vitals" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "GET /residents/{resident_id}/vitals"
  target             = "integrations/${aws_apigatewayv2_integration.api_vitals.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

# Only this API's GET /residents/{resident_id}/vitals route (any stage) may invoke it.
resource "aws_lambda_permission" "api_vitals" {
  statement_id  = "AllowApiGatewayGetVitals"
  action        = "lambda:InvokeFunction"
  function_name = module.api_vitals.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/GET/residents/*/vitals"
}
