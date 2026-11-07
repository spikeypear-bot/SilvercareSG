# Clean data pipeline (docs/features/clean-data.md). Every day at 00:30 UTC an
# EventBridge rule invokes the silvercare-clean Lambda, which runs Athena SQL that turns
# the previous UTC day's raw telemetry into Parquet: one table per message type, plus
# rejected lines with a reason. Raw files are never changed. Lake Formation governs
# access through Athena and the catalog. It does not limit direct S3 reads: the
# developers' ReadProjectBuckets statement still reads these files, rejected included
# (spec, Users and access).
#
# Data: pseudonymous readings by device ID, no resident IDs. rejected keeps the raw line.
# Retention: clean tables expire at 365 days (proposed, matching raw telemetry/'s expiry in
# history.tf); Athena results expire after 7 days. Per-device erasure isn't built: it must exist before real data.
#
# Each successful asynchronous run hands off to silvercare-summaries (on-success
# destination, docs/features/curated-layer.md, criterion 1; summaries.tf).
#
# Cost: well under US$1/month. Athena US$5/TB with a 1 GB per query limit, S3 under 1 GB,
# Glue and Lake Formation within free allowances, 1 more alarm (within the free 10). No
# fixed fees.

locals {
  clean_function_name = "silvercare-clean"
  lake_database       = "silvercare_lake"
  raw_table           = "raw_telemetry"
  rejected_table      = "rejected"
  raw_prefix          = "telemetry"
  clean_prefix        = "tables"

  # The Lambda's SQL builder reads the same file, so columns and SQL can't drift apart.
  clean_tables = jsondecode(file("${path.module}/../lambdas/clean/src/tables.json")).tables
  glue_types   = { int = "int", number = "double", bool = "boolean", str = "string" }
  envelope_columns = [
    { name = "message_id", type = "string" },
    { name = "device_id", type = "string" },
    { name = "ts", type = "timestamp" },
    { name = "received_at", type = "timestamp" },
    { name = "schema_version", type = "int" },
    { name = "clock_skew", type = "boolean" },
  ]

  # Column lists of the eight clean tables and rejected.
  clean_table_columns = {
    for name, table in local.clean_tables : name => concat(
      local.envelope_columns,
      [for field in table.fields : { name = field.name, type = local.glue_types[field.kind] }],
      table.accel == null ? [] : [
        { name = table.accel, type = "string" },
        { name = "accel_valid", type = "boolean" },
      ],
    )
  }
  output_table_columns = merge(local.clean_table_columns, {
    (local.rejected_table) = [
      { name = "message", type = "string" },
      { name = "reason", type = "string" },
      { name = "raw_line", type = "string" },
    ]
  })

  lake_buckets = {
    clean          = "silvercare-clean-${data.aws_caller_identity.current.account_id}"
    athena_results = "silvercare-athena-results-${data.aws_caller_identity.current.account_id}"
  }

  # summaries: docs/features/curated-layer.md, criterion 21 (its Lambda is in summaries.tf).
  athena_workgroups = {
    clean     = { name = "silvercare-clean", prefix = "clean" }
    analysis  = { name = "silvercare-analysis", prefix = "analysis" }
    summaries = { name = "silvercare-summaries", prefix = "summaries" }
  }

  # Keys are a hash of the stable user ID, because resource addresses appear in the
  # public plan comment and must not hold IAM user names (criterion 22a).
  developer_arns = {
    for user in data.aws_iam_group.developers.users : substr(sha256(user.user_id), 0, 12) => user.arn
  }
  developer_table_grants = {
    for pair in setproduct(keys(local.developer_arns), keys(local.clean_tables)) :
    "${pair[0]}-${pair[1]}" => { principal = local.developer_arns[pair[0]], table = pair[1] }
  }
}

data "aws_iam_role" "cicd" {
  name = "silvercare-cicd"
}

data "aws_iam_role" "cicd_plan" {
  name = "silvercare-cicd-plan"
}

# AWS's list of Lake Formation principals has IAM users and roles but not IAM groups, so
# each member of the group gets the grants (criterion 22a).
data "aws_iam_group" "developers" {
  group_name = "silvercare-developers"
}

# ---------------------------------------------------------------------------------------
# Buckets (criterion 23)
# ---------------------------------------------------------------------------------------

resource "aws_s3_bucket" "lake" {
  #checkov:skip=CKV_AWS_21:Versioning off, as on the history bucket (spec criterion 23): clean is rebuilt from raw and a delete really deletes.
  #checkov:skip=CKV_AWS_18:No access logging (cost: extra requests and storage), and the logs would need their own retention decision first.
  #checkov:skip=CKV_AWS_144:No cross-region replication. The data must stay in ap-southeast-1 (Singapore).
  #checkov:skip=CKV_AWS_145:SSE-S3 (AWS-managed key) as the spec requires. A customer-managed KMS key costs ~US$1/month plus request charges.
  #checkov:skip=CKV2_AWS_62:No event notifications. Nothing consumes events from these buckets.
  #checkov:skip=CKV2_AWS_6:The public access block is aws_s3_bucket_public_access_block.lake below; checkov does not link it through for_each.
  #checkov:skip=CKV2_AWS_61:Lifecycle rules are in aws_s3_bucket_lifecycle_configuration.clean and .athena_results below.
  for_each = local.lake_buckets

  bucket        = each.value
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "lake" {
  for_each = aws_s3_bucket.lake

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "lake" {
  for_each = aws_s3_bucket.lake

  bucket = each.value.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  for_each = aws_s3_bucket.lake

  bucket = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# 365 days (proposed), the figure proposed for raw. Not a hard limit: S3 counts from when
# a file was written, so re-running a date restarts that date's clock.
resource "aws_s3_bucket_lifecycle_configuration" "clean" {
  bucket = aws_s3_bucket.lake["clean"].id

  rule {
    id     = "expire-tables-after-365-days"
    status = "Enabled"

    filter {
      prefix = "tables/"
    }

    expiration {
      days = 365
    }
  }

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "athena_results" {
  bucket = aws_s3_bucket.lake["athena_results"].id

  rule {
    id     = "expire-results-after-7-days"
    status = "Enabled"

    filter {}

    expiration {
      days = 7
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# Deny statements: s3:* and principal * are intentional here. Denying every action from
# every caller without TLS is narrower access, not broader.
data "aws_iam_policy_document" "lake_bucket" {
  for_each = aws_s3_bucket.lake

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [each.value.arn, "${each.value.arn}/*"]

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

resource "aws_s3_bucket_policy" "lake" {
  for_each = aws_s3_bucket.lake

  bucket = each.value.id
  policy = data.aws_iam_policy_document.lake_bucket[each.key].json

  # Apply after the public access block so the policy is never evaluated without it.
  depends_on = [aws_s3_bucket_public_access_block.lake]
}

# ---------------------------------------------------------------------------------------
# Lake Formation settings and locations (criteria 18, 19)
# ---------------------------------------------------------------------------------------

# silvercare-cicd is the only admin, and silvercare-cicd-plan the only read-only admin (view
# only, so PR plans can read these resources; criterion 18). The
# create_*_default_permissions blocks are left out on purpose: the provider documents that
# this clears them, so new databases and tables get no IAMAllowedPrincipals permissions.
# These settings are account-wide.
resource "aws_lakeformation_data_lake_settings" "this" {
  admins           = [data.aws_iam_role.cicd.arn]
  read_only_admins = [data.aws_iam_role.cicd_plan.arn]
}

# Lake Formation assumes this role to hand query engines short-lived access to the
# registered locations. No source conditions: AWS documents this trust without them.
data "aws_iam_policy_document" "lake_location_assume" {
  statement {
    sid     = "LakeFormationAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lakeformation.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lake_location" {
  name               = "silvercare-lake-location"
  description        = "Lake Formation access to the raw and summaries (read) and clean (read, write, delete) locations."
  assume_role_policy = data.aws_iam_policy_document.lake_location_assume.json
}

# Read-only on raw is a decision for the owner: don't widen it, even if a check fails
# (criterion 19). Read-only on summaries/ too (curated-layer criterion 20): the
# summaries Lambda writes them itself. alerts/ isn't reachable.
data "aws_iam_policy_document" "lake_location" {
  statement {
    sid       = "ReadRawObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.history.arn}/${local.raw_prefix}/*"]
  }

  statement {
    sid       = "ListRawPrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.history.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.raw_prefix}/*"]
    }
  }

  statement {
    sid       = "ReadSummaryObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.history.arn}/${local.summaries_prefix}/*"]
  }

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

  statement {
    sid       = "WriteCleanObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.lake["clean"].arn}/${local.clean_prefix}/*"]
  }

  statement {
    sid       = "ListCleanPrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lake["clean"].arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.clean_prefix}/*"]
    }
  }
}

resource "aws_iam_role_policy" "lake_location" {
  name   = "silvercare-lake-location"
  role   = aws_iam_role.lake_location.id
  policy = data.aws_iam_policy_document.lake_location.json
}

resource "aws_lakeformation_resource" "raw" {
  arn                     = "${aws_s3_bucket.history.arn}/${local.raw_prefix}"
  role_arn                = aws_iam_role.lake_location.arn
  use_service_linked_role = false

  depends_on = [aws_lakeformation_data_lake_settings.this, aws_iam_role_policy.lake_location]
}

resource "aws_lakeformation_resource" "clean" {
  arn                     = "${aws_s3_bucket.lake["clean"].arn}/${local.clean_prefix}"
  role_arn                = aws_iam_role.lake_location.arn
  use_service_linked_role = false

  depends_on = [aws_lakeformation_data_lake_settings.this, aws_iam_role_policy.lake_location]
}

# ---------------------------------------------------------------------------------------
# Glue Data Catalog (criteria 6, 8, 13)
# ---------------------------------------------------------------------------------------

# Created after the settings, so it gets no IAMAllowedPrincipals permissions.
resource "aws_glue_catalog_database" "lake" {
  name        = local.lake_database
  description = "SilverCare data lake: raw telemetry and clean tables."

  depends_on = [aws_lakeformation_data_lake_settings.this]
}

# One string column, so a wrong type in one message can't fail the whole query. Text
# input format: Athena picks gzip from the .gz file extension.
resource "aws_glue_catalog_table" "raw_telemetry" {
  name          = local.raw_table
  database_name = aws_glue_catalog_database.lake.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL       = "TRUE"
    classification = "text"
  }

  partition_keys {
    name = "message"
    type = "string"
  }

  partition_keys {
    name = "date"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.history.bucket}/${local.raw_prefix}/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe"
    }

    columns {
      name = "line"
      type = "string"
    }
  }

  depends_on = [aws_lakeformation_resource.raw]
}

resource "aws_glue_catalog_table" "output" {
  for_each = local.output_table_columns

  name          = each.key
  database_name = aws_glue_catalog_database.lake.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL              = "TRUE"
    classification        = "parquet"
    "parquet.compression" = "SNAPPY"
  }

  partition_keys {
    name = "date"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.lake["clean"].bucket}/${local.clean_prefix}/${each.key}/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = each.value

      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }

  depends_on = [aws_lakeformation_resource.clean]
}

# ---------------------------------------------------------------------------------------
# Lake Formation grants (criteria 20, 22a)
# ---------------------------------------------------------------------------------------

# AWS: "The Lake Formation DESCRIBE permission is required on the default database."
# This account has no default database (owner checked 2026-11-06), so it is created here,
# empty. Created after the settings, so it gets no IAMAllowedPrincipals permissions.
resource "aws_glue_catalog_database" "default" {
  name        = "default"
  description = "Empty. Exists because Lake Formation requires DESCRIBE on it for Athena."

  depends_on = [aws_lakeformation_data_lake_settings.this]
}

resource "aws_lakeformation_permissions" "clean_default_database" {
  principal   = module.clean.role_arn
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.default.name
  }
}

resource "aws_lakeformation_permissions" "clean_lake_database" {
  principal   = module.clean.role_arn
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.lake.name
  }
}

# INSERT and ALTER are for ALTER TABLE ADD PARTITION. AWS's permissions reference doesn't
# say which of them creating a partition checks, so both stay. They give no write to raw
# files: the location role and this role's IAM policy are both read-only on raw.
resource "aws_lakeformation_permissions" "clean_raw_table" {
  principal   = module.clean.role_arn
  permissions = ["ALTER", "DESCRIBE", "INSERT", "SELECT"]

  table {
    database_name = aws_glue_catalog_database.lake.name
    name          = aws_glue_catalog_table.raw_telemetry.name
  }
}

# ALTER and INSERT: see clean_raw_table. INSERT also covers UNLOAD's writes here.
resource "aws_lakeformation_permissions" "clean_output_tables" {
  for_each = aws_glue_catalog_table.output

  principal   = module.clean.role_arn
  permissions = ["ALTER", "DESCRIBE", "INSERT"]

  table {
    database_name = aws_glue_catalog_database.lake.name
    name          = each.value.name
  }
}

# See clean_default_database (criterion 22a).
resource "aws_lakeformation_permissions" "developer_default_database" {
  for_each = local.developer_arns

  principal   = each.value
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.default.name
  }
}

resource "aws_lakeformation_permissions" "developer_lake_database" {
  for_each = local.developer_arns

  principal   = each.value
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.lake.name
  }
}

# The eight clean tables only: nothing on raw_telemetry or rejected.
resource "aws_lakeformation_permissions" "developer_clean_tables" {
  for_each = local.developer_table_grants

  principal   = each.value.principal
  permissions = ["DESCRIBE", "SELECT"]

  table {
    database_name = aws_glue_catalog_database.lake.name
    name          = aws_glue_catalog_table.output[each.value.table].name
  }
}

# ---------------------------------------------------------------------------------------
# Athena workgroups (criterion 21)
# ---------------------------------------------------------------------------------------

resource "aws_athena_workgroup" "lake" {
  for_each = local.athena_workgroups

  name          = each.value.name
  state         = "ENABLED"
  force_destroy = false

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = 1073741824 # 1 GB, the cost guard

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }

    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake["athena_results"].bucket}/${each.value.prefix}/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

# ---------------------------------------------------------------------------------------
# Lambda (criteria 22, 24)
# ---------------------------------------------------------------------------------------

# Kept under .terraform/ (git-ignored); CI builds it during plan and applies the saved
# plan in the same job.
data "archive_file" "clean" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/clean/src"
  output_path = "${path.module}/.terraform/build/clean.zip"
  excludes    = ["__pycache__", "**/__pycache__/**", "**/*.pyc"]
}

# What the function does with AWS: starts queries in its own workgroup and reads their
# state and results; Athena then uses this role for Glue and S3. Reads raw telemetry,
# adds partitions, writes and replaces objects under tables/, and writes results under
# its own prefix. No write or delete on history, no alerts/, no DynamoDB, nothing
# published. It invokes silvercare-summaries only, as its on-success destination (AWS
# requires the source function's role to have lambda:InvokeFunction on it). The module
# adds the log group permissions.
data "aws_iam_policy_document" "clean" {
  statement {
    sid    = "OwnAthenaWorkgroup"
    effect = "Allow"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
    ]
    resources = [aws_athena_workgroup.lake["clean"].arn]
  }

  # AWS names glue:CreatePartition for ALTER TABLE ADD PARTITION and glue:BatchCreatePartition
  # for adding partitions; AmazonAthenaFullAccess lists both, and all three partition reads,
  # without saying which call each statement uses. So all five stay.
  statement {
    sid    = "LakeCatalog"
    effect = "Allow"
    actions = [
      "glue:GetDatabase",
      "glue:GetTable",
      "glue:GetPartition",
      "glue:GetPartitions",
      "glue:BatchGetPartition",
      "glue:CreatePartition",
      "glue:BatchCreatePartition",
    ]
    resources = [
      "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:catalog",
      aws_glue_catalog_database.lake.arn,
      "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:table/${local.lake_database}/*",
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
    sid       = "ReadRawObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.history.arn}/${local.raw_prefix}/*"]
  }

  statement {
    sid       = "ListRawPrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.history.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.raw_prefix}/*"]
    }
  }

  statement {
    sid       = "HistoryBucketLocation"
    effect    = "Allow"
    actions   = ["s3:GetBucketLocation"]
    resources = [aws_s3_bucket.history.arn]
  }

  # No s3:GetObject: nothing here reads the clean files back. UNLOAD writes them (its
  # manifest goes to the results prefix), and the job only lists and deletes old output.
  statement {
    sid    = "ReplaceCleanObjects"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.lake["clean"].arn}/${local.clean_prefix}/*"]
  }

  statement {
    sid       = "ListCleanPrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lake["clean"].arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.clean_prefix}/*"]
    }
  }

  statement {
    sid    = "CleanBucketInfo"
    effect = "Allow"
    actions = [
      "s3:GetBucketLocation",
      "s3:ListBucketMultipartUploads",
    ]
    resources = [aws_s3_bucket.lake["clean"].arn]
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
    resources = ["${aws_s3_bucket.lake["athena_results"].arn}/${local.athena_workgroups.clean.prefix}/*"]
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

  # The prefix form AWS documents for an Athena results location (workgroups-identity-center):
  # IfExists lets through a request with no prefix, which only ever shows query IDs.
  statement {
    sid       = "ListOwnResults"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lake["athena_results"].arn]

    condition {
      test     = "StringLikeIfExists"
      variable = "s3:prefix"
      values = [
        local.athena_workgroups.clean.prefix,
        "${local.athena_workgroups.clean.prefix}/*",
      ]
    }
  }

  # The on-success destination (curated-layer criterion 1). That function only.
  statement {
    sid       = "InvokeSummaries"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [module.summaries.function_arn]
  }
}

module "clean" {
  source = "./modules/python-lambda"

  function_name = local.clean_function_name
  description   = "Daily: runs Athena SQL that writes a UTC day of raw telemetry as Parquet clean tables and a rejected table."
  handler       = "handler.lambda_handler"
  zip_path      = data.archive_file.clean.output_path
  zip_hash      = data.archive_file.clean.output_base64sha256
  layers        = [local.powertools_layer_arn]
  memory_size   = 128
  timeout       = 300

  environment = {
    ATHENA_WORKGROUP        = aws_athena_workgroup.lake["clean"].name
    CLEAN_BUCKET            = aws_s3_bucket.lake["clean"].bucket
    GLUE_DATABASE           = aws_glue_catalog_database.lake.name
    POWERTOOLS_SERVICE_NAME = local.clean_function_name
  }

  policy_json        = data.aws_iam_policy_document.clean.json
  log_retention_days = 30

  # No on_failure_destination: nothing is lost, because raw files stay and any date can
  # be re-run. The retry settings are in aws_lambda_function_event_invoke_config.clean.
}

# 2 retries (criterion 5). An hour's event age keeps all three attempts inside the alarm's
# window and well before the next day's run. After a successful asynchronous run, Lambda
# invokes silvercare-summaries with the invocation record, whose responsePayload is
# clean's {"date": ...} (curated-layer criterion 1). Synchronous runs (a manual
# `aws lambda invoke`) don't: run summaries for that date by hand too. A failed hand-off
# alarms as silvercare-clean-destination-failures (summaries.tf).
resource "aws_lambda_function_event_invoke_config" "clean" {
  function_name                = module.clean.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600

  destination_config {
    on_success {
      destination = module.summaries.function_arn
    }
  }

  # AWS checks the role may invoke the destination when the config is saved.
  depends_on = [module.clean]
}

# ---------------------------------------------------------------------------------------
# Schedule (criterion 1)
# ---------------------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "clean" {
  name                = local.clean_function_name
  description         = "Runs the silvercare-clean Lambda daily for the previous UTC date."
  schedule_expression = "cron(30 0 * * ? *)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "clean" {
  rule      = aws_cloudwatch_event_rule.clean.name
  target_id = "clean"
  arn       = module.clean.function_arn
}

# Only this rule, in this account, may invoke the function.
resource "aws_lambda_permission" "clean_schedule" {
  statement_id   = "AllowEventBridgeSchedule"
  action         = "lambda:InvokeFunction"
  function_name  = module.clean.function_name
  principal      = "events.amazonaws.com"
  source_arn     = aws_cloudwatch_event_rule.clean.arn
  source_account = data.aws_caller_identity.current.account_id
}

# ---------------------------------------------------------------------------------------
# Failure alarm (criterion 23a)
# ---------------------------------------------------------------------------------------

# The scheduled run and its 2 retries all failed. A run that never started doesn't alarm
# (spec decision: no Invocations alarm).
resource "aws_cloudwatch_metric_alarm" "clean_errors" {
  alarm_name          = "silvercare-clean-errors"
  alarm_description   = "silvercare-clean had at least 3 errors in one hour (the scheduled run and its 2 retries, or a failing manual run): that date's clean tables may be missing. Check the function's logs for the query ID and state, fix the cause, then re-run with {\"date\": \"yyyy-mm-dd\"}."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = module.clean.function_name }
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
