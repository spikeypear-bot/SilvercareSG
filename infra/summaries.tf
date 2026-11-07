# Curated layer: daily resident summaries (docs/features/curated-layer.md). After every
# successful asynchronous silvercare-clean run (its daily schedule and retries), Lambda
# invokes silvercare-summaries through clean's on-success destination (clean.tf). It
# reads the device -> resident mapping and thresholds from the registry, runs one Athena
# query over the clean tables for the Singapore date clean just completed, and writes
# one JSON summary per resident to summaries/ in the history bucket, catalogued as
# silvercare_lake.daily_summaries. Lake Formation governs Athena access. Each successful
# asynchronous run then hands off to silvercare-results (on-success destination,
# docs/features/nightly-results.md, criterion 3; results.tf).
#
# Data: per-resident daily health statistics (heart rate, SpO2, skin temperature,
# activity, bed, motion, falls, alert episodes, readings outside thresholds, the
# thresholds), keyed by the opaque resident_id, no names. Retention: summaries/ is kept
# for the resident's lifetime + 6 years (no expiry rule); Athena results 7 days.
# Erasure of one resident: find objects by their resident_id tag (no tool yet).
#
# Cost: under US$0.01/month (one Athena query a day over a few MB, about 20 tagged PUTs
# a day, Lambda and Glue within free allowances) plus 2 alarms (6 of the free 10). No
# fixed fees.

locals {
  summaries_function_name = "silvercare-summaries"
  summaries_prefix        = "summaries"
  summaries_table         = "daily_summaries"

  # The six clean tables the query reads (criterion 20).
  summaries_source_tables = toset(["vitals", "fall", "vitals_alert", "alert_cancel", "bed", "motion"])

  # Columns of daily_summaries, as criterion 11. `date` is the partition key, so it isn't
  # also a column (Glue forbids that); the value in each object matches the partition.
  summaries_columns = [
    { name = "resident_id", type = "string" },
    { name = "hr_bpm_min", type = "int" },
    { name = "hr_bpm_avg", type = "double" },
    { name = "hr_bpm_max", type = "int" },
    { name = "spo2_pct_min", type = "int" },
    { name = "spo2_pct_avg", type = "double" },
    { name = "spo2_pct_max", type = "int" },
    { name = "skin_temp_c_min", type = "double" },
    { name = "skin_temp_c_avg", type = "double" },
    { name = "skin_temp_c_max", type = "double" },
    { name = "activity_pct_avg", type = "double" },
    { name = "vitals_readings", type = "int" },
    { name = "hr_bpm_below", type = "int" },
    { name = "hr_bpm_above", type = "int" },
    { name = "spo2_pct_below", type = "int" },
    { name = "skin_temp_c_below", type = "int" },
    { name = "skin_temp_c_above", type = "int" },
    { name = "thresholds", type = "struct<hr_bpm:struct<min:int,max:int>,spo2_pct:struct<min:int>,skin_temp_c:struct<min:double,max:double>>" },
    { name = "bed_minutes", type = "int" },
    { name = "motion_events", type = "int" },
    { name = "falls", type = "int" },
    { name = "vitals_alert_episodes", type = "int" },
    { name = "cancels", type = "int" },
    { name = "generated_at", type = "string" },
    { name = "schema_version", type = "int" },
  ]
}

# ---------------------------------------------------------------------------------------
# Lake Formation location and Glue table (criteria 19, 20)
# ---------------------------------------------------------------------------------------

# The location role's read on summaries/ is in clean.tf (data.aws_iam_policy_document.lake_location).
resource "aws_lakeformation_resource" "summaries" {
  arn                     = "${aws_s3_bucket.history.arn}/${local.summaries_prefix}"
  role_arn                = aws_iam_role.lake_location.arn
  use_service_linked_role = false

  depends_on = [aws_lakeformation_data_lake_settings.this, aws_iam_role_policy.lake_location]
}

# One JSON object per file (OpenX JSON SerDe), partitioned by the Singapore date.
resource "aws_glue_catalog_table" "daily_summaries" {
  name          = local.summaries_table
  database_name = aws_glue_catalog_database.lake.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL       = "TRUE"
    classification = "json"
  }

  partition_keys {
    name = "date"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.history.bucket}/${local.summaries_prefix}/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
    }

    dynamic "columns" {
      for_each = local.summaries_columns

      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }

  depends_on = [aws_lakeformation_resource.summaries]
}

# ---------------------------------------------------------------------------------------
# Lake Formation grants (criterion 20)
# ---------------------------------------------------------------------------------------

# AWS requires DESCRIBE on the default database for Athena (see clean_default_database).
resource "aws_lakeformation_permissions" "summaries_default_database" {
  principal   = module.summaries.role_arn
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.default.name
  }
}

resource "aws_lakeformation_permissions" "summaries_lake_database" {
  principal   = module.summaries.role_arn
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.lake.name
  }
}

# The six tables the query reads, nothing else: no raw_telemetry, rejected, or status tables.
resource "aws_lakeformation_permissions" "summaries_source_tables" {
  for_each = local.summaries_source_tables

  principal   = module.summaries.role_arn
  permissions = ["SELECT"]

  table {
    database_name = aws_glue_catalog_database.lake.name
    name          = aws_glue_catalog_table.output[each.key].name
  }
}

# ALTER and INSERT are for ALTER TABLE ADD PARTITION, as clean_output_tables. No SELECT:
# the Lambda never reads the summaries back.
resource "aws_lakeformation_permissions" "summaries_table" {
  principal   = module.summaries.role_arn
  permissions = ["ALTER", "DESCRIBE", "INSERT"]

  table {
    database_name = aws_glue_catalog_database.lake.name
    name          = aws_glue_catalog_table.daily_summaries.name
  }
}

# Per IAM user, as developer_clean_tables: Lake Formation has no IAM-group principals.
resource "aws_lakeformation_permissions" "developer_daily_summaries" {
  for_each = local.developer_arns

  principal   = each.value
  permissions = ["DESCRIBE", "SELECT"]

  table {
    database_name = aws_glue_catalog_database.lake.name
    name          = aws_glue_catalog_table.daily_summaries.name
  }
}

# ---------------------------------------------------------------------------------------
# Lambda (criteria 5, 22, 28)
# ---------------------------------------------------------------------------------------

# Kept under .terraform/ (git-ignored); CI builds it during plan and applies the saved
# plan in the same job.
data "archive_file" "summaries" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/summaries/src"
  output_path = "${path.module}/.terraform/build/summaries.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function does with AWS: Glue GetPartition on the clean tables it reads (the
# missing-partition check); an eventually consistent Query of the registry's by-type
# index and BatchGetItem of DEVICE#<id>/ASSIGNMENT and THRESHOLDS#<id>/THRESHOLDS items;
# Athena queries in its own workgroup (the summary SELECT and ALTER TABLE ADD PARTITION),
# for which Athena uses this role for Glue and its results prefix; then lists, deletes,
# and writes (with tags) objects under summaries/ in the history bucket. No telemetry/,
# alerts/, no other table, no other registry partition, nothing published. Clean data is
# read through Lake Formation's credentials, so there is no S3 access to the clean bucket.
# It invokes silvercare-results only, as its on-success destination (AWS requires the
# source function's role to have lambda:InvokeFunction on it). The module adds the log
# group permissions.
data "aws_iam_policy_document" "summaries" {
  statement {
    sid    = "OwnAthenaWorkgroup"
    effect = "Allow"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
    ]
    resources = [aws_athena_workgroup.lake["summaries"].arn]
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
    # Only the six source tables and daily_summaries, not the whole database.
    resources = concat(
      [
        "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:catalog",
        aws_glue_catalog_database.lake.arn,
        aws_glue_catalog_table.daily_summaries.arn,
      ],
      [for t in local.summaries_source_tables : aws_glue_catalog_table.output[t].arn],
    )
  }

  # Both, as the clean role: AWS doesn't say which one ALTER TABLE ADD PARTITION calls.
  statement {
    sid    = "SummaryPartitions"
    effect = "Allow"
    actions = [
      "glue:CreatePartition",
      "glue:BatchCreatePartition",
    ]
    resources = [
      "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:catalog",
      aws_glue_catalog_database.lake.arn,
      aws_glue_catalog_table.daily_summaries.arn,
    ]
  }

  # Goes with the Lake Formation DESCRIBE on default, which AWS requires for Athena.
  statement {
    sid       = "DefaultDatabase"
    effect    = "Allow"
    actions   = ["glue:GetDatabase"]
    resources = ["arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:database/default"]
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
    resources = ["${aws_s3_bucket.lake["athena_results"].arn}/${local.athena_workgroups.summaries.prefix}/*"]
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

  # As the clean role's ListOwnResults (AWS's documented form for a results location).
  statement {
    sid       = "ListOwnResults"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lake["athena_results"].arn]

    condition {
      test     = "StringLikeIfExists"
      variable = "s3:prefix"
      values = [
        local.athena_workgroups.summaries.prefix,
        "${local.athena_workgroups.summaries.prefix}/*",
      ]
    }
  }

  # Listing summaries/date=<D>/ before replacing it. Nothing else of the bucket is listed.
  statement {
    sid       = "ListSummaryPrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.history.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.summaries_prefix}/*"]
    }
  }

  # Bucket metadata only (its region), which Athena asks for on a table location, as the
  # clean role's HistoryBucketLocation.
  statement {
    sid       = "HistoryBucketLocation"
    effect    = "Allow"
    actions   = ["s3:GetBucketLocation"]
    resources = [aws_s3_bucket.history.arn]
  }

  # No s3:GetObject: the Lambda never reads summaries back. PutObjectTagging is needed
  # because each PutObject carries the resident_id tag.
  statement {
    sid    = "ReplaceSummaryObjects"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:PutObjectTagging",
      "s3:DeleteObject",
    ]
    resources = ["${aws_s3_bucket.history.arn}/${local.summaries_prefix}/*"]
  }

  # Pattern 4: only the listed attributes, with Select = SPECIFIC_ATTRIBUTES, so the role
  # can never read display_name, home_id or photo_key (the index projects them too), as
  # api_devices' RegistryByTypeIndex. entity_type is the key condition; sk is in a later
  # page's ExclusiveStartKey.
  statement {
    sid       = "RegistryByTypeIndex"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = ["${module.registry_table.arn}/index/by-type"]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["pk", "sk", "entity_type", "resident_id"]
    }

    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
  }

  # Device assignments and thresholds only: no RESIDENT# profiles, USER# links, or
  # threshold history.
  statement {
    sid       = "RegistryAssignmentsAndThresholds"
    effect    = "Allow"
    actions   = ["dynamodb:BatchGetItem"]
    resources = [module.registry_table.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["DEVICE#*", "THRESHOLDS#*"]
    }

    # Only the fields the handler projects (assignments: resident_id, device_type,
    # assigned_at; thresholds: the three limits), so the role can't read a threshold
    # item's updated_by (a staff sub) or updated_by_name. The Null condition makes a
    # read with no projection fail: ForAllValues alone passes when the key is absent.
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

  # The on-success destination (nightly-results criterion 3). That function only.
  statement {
    sid       = "InvokeResults"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [module.results.function_arn]
  }
}

module "summaries" {
  source = "./modules/python-lambda"

  function_name = local.summaries_function_name
  description   = "Daily, after clean: writes one JSON summary per resident for a Singapore date to summaries/ and catalogs it as daily_summaries."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.summaries.output_path
  zip_hash      = data.archive_file.summaries.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  timeout       = 300

  environment = {
    ATHENA_WORKGROUP        = aws_athena_workgroup.lake["summaries"].name
    GLUE_DATABASE           = aws_glue_catalog_database.lake.name
    HISTORY_BUCKET          = aws_s3_bucket.history.bucket
    REGISTRY_TABLE          = module.registry_table.name
    POWERTOOLS_SERVICE_NAME = local.summaries_function_name
  }

  policy_json        = data.aws_iam_policy_document.summaries.json
  log_retention_days = 30

  # No on_failure_destination: nothing is lost, because the clean tables stay and any
  # date can be re-run. The retry settings are in aws_lambda_function_event_invoke_config.summaries.
}

# 2 retries and an hour's event age, as clean (criterion 5). No invoke permission is
# needed for the on-success destination: Lambda uses clean's execution role, which may
# invoke this function (clean.tf).
#
# After a successful asynchronous run, Lambda invokes silvercare-results with the
# invocation record, whose responsePayload is summaries' {"date": ..., "summaries": n}
# (nightly-results criterion 3). Synchronous runs (a manual `aws lambda invoke`) don't:
# run results for that date by hand too. A failed hand-off alarms as
# silvercare-summaries-destination-failures (results.tf).
resource "aws_lambda_function_event_invoke_config" "summaries" {
  function_name                = module.summaries.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600

  destination_config {
    on_success {
      destination = module.results.function_arn
    }
  }

  # AWS checks the role may invoke the destination when the config is saved.
  depends_on = [module.summaries]
}

# ---------------------------------------------------------------------------------------
# Alarms (criteria 25, 26)
# ---------------------------------------------------------------------------------------

# The handed-off run and its 2 retries all failed (or a failing manual run).
resource "aws_cloudwatch_metric_alarm" "summaries_errors" {
  alarm_name          = "silvercare-summaries-errors"
  alarm_description   = "silvercare-summaries had at least 3 errors in one hour (the run after clean and its 2 retries, or a failing manual run): that Singapore date's summaries may be missing or old. Check the function's logs (a missing clean partition names its table and date; failed queries log their ID and state), fix the cause (re-run clean with {\"date\": \"yyyy-mm-dd\"} if a partition is missing), then re-run summaries with {\"date\": \"yyyy-mm-dd\"}."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = module.summaries.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
  ok_actions          = [aws_sns_topic.ops.arn]
}

# Clean succeeded but Lambda couldn't hand off to summaries: without this, a day would be
# left unsummarised with no email.
resource "aws_cloudwatch_metric_alarm" "clean_destination_failures" {
  alarm_name          = "silvercare-clean-destination-failures"
  alarm_description   = "silvercare-clean finished but Lambda could not invoke its on-success destination silvercare-summaries: that Singapore date has no new summaries. Check the clean role's lambda:InvokeFunction permission and that silvercare-summaries exists, then run summaries with {\"date\": \"yyyy-mm-dd\"} (the UTC date clean processed)."
  namespace           = "AWS/Lambda"
  metric_name         = "DestinationDeliveryFailures"
  dimensions          = { FunctionName = module.clean.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
  ok_actions          = [aws_sns_topic.ops.arn]
}
