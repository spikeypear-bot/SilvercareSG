# Tableau access (docs/features/tableau-access.md). Read-only IAM users for Tableau
# Desktop's Amazon Athena connector, so the Track D analytics dashboards can be built on
# silvercare_lake.daily_summaries (the curated layer) and nothing else. Each user runs
# queries in its own workgroup, silvercare-tableau; Lake Formation decides which tables
# it can see. Setup guide: docs/tableau.md.
#
# Access keys: created by hand by the account owner (aws iam create-access-key) and handed
# to the user privately. They are never stored in the repo, chat, CI, or Terraform state,
# which is why there is no aws_iam_access_key here.
#
# Live connection only: Tableau queries Athena each time a dashboard opens or refreshes.
# No extracts or packaged workbooks, which keeps data on the laptop to a minimum; Tableau
# still caches query results on disk, and a .twb can hold some values (e.g. filter lists).
#
# User names must stay neutral (no personal names): the repo and its plan comments are
# public. Who holds which user is kept off the repo.
#
# Cost: none fixed. IAM, Lake Formation, and the workgroup are free; queries are Athena's
# US$5/TB scanned (1 GB limit per query), results expire after 7 days.

locals {
  # Adding a name adds that user's IAM user, group membership, and Lake Formation grants.
  tableau_users = toset(["silvercare-tableau-1"])
}

# ---------------------------------------------------------------------------------------
# Users and group (criteria 1-3)
# ---------------------------------------------------------------------------------------

# No access key, login profile, or inline policy: the key is made by hand, there is no
# console password, and the permissions are on the group. force_destroy = false, so
# Terraform can't delete a user whose hand-made key still exists (no orphaned live key).
resource "aws_iam_user" "tableau" {
  #checkov:skip=CKV_AWS_273:Tableau Desktop's Athena connector signs in with an IAM access key; this account has no IAM Identity Center (spec decision, 2026-11-08).
  for_each = local.tableau_users

  # No path: the ARN is the Lake Formation principal, and AWS only documents plain
  # user/<name> ARNs there, so a path could fail the grants mid-apply on main.
  name          = each.key
  force_destroy = false
}

resource "aws_iam_group" "tableau" {
  name = "silvercare-tableau"
}

resource "aws_iam_user_group_membership" "tableau" {
  for_each = local.tableau_users

  user   = aws_iam_user.tableau[each.key].name
  groups = [aws_iam_group.tableau.name]
}

# ---------------------------------------------------------------------------------------
# Group policy (criteria 5, 6)
# ---------------------------------------------------------------------------------------

# What Tableau does with AWS: runs and reads Athena queries in its own workgroup, lists
# workgroups, browses the catalog, reads the Glue entries Athena needs, gets data access
# from Lake Formation (which vends the S3 read of summaries/), and writes and reads its
# results under tableau/. No direct S3 access to the history or clean buckets, no other
# workgroup, no DynamoDB, IAM, or Lambda, and no write to the Glue catalog.
data "aws_iam_policy_document" "tableau" {
  statement {
    sid    = "OwnAthenaWorkgroup"
    effect = "Allow"
    actions = [
      "athena:StartQueryExecution",
      "athena:StopQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetQueryResultsStream",
      "athena:BatchGetQueryExecution",
      "athena:ListQueryExecutions",
      "athena:GetWorkGroup",
    ]
    resources = [aws_athena_workgroup.lake["tableau"].arn]
  }

  # AWS only supports * for this action. It returns workgroup names, not data.
  statement {
    sid       = "ListWorkGroups"
    effect    = "Allow"
    actions   = ["athena:ListWorkGroups"]
    resources = ["*"]
  }

  statement {
    sid    = "AthenaCatalogBrowse"
    effect = "Allow"
    actions = [
      "athena:GetDataCatalog",
      "athena:ListDatabases",
      "athena:GetDatabase",
      "athena:ListTableMetadata",
      "athena:GetTableMetadata",
    ]
    resources = ["arn:aws:athena:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:datacatalog/AwsDataCatalog"]
  }

  # The table wildcard is needed for glue:GetTables (listing the database). Lake Formation
  # filters what Glue returns to the tables the caller has permissions on (only
  # daily_summaries, see the grants below), so no other table is listed or readable.
  statement {
    sid    = "LakeCatalogRead"
    effect = "Allow"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartition",
      "glue:GetPartitions",
      "glue:BatchGetPartition",
    ]
    resources = [
      "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:catalog",
      aws_glue_catalog_database.lake.arn,
      "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:table/${aws_glue_catalog_database.lake.name}/*",
    ]
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
      "s3:PutObject",
      "s3:GetObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.lake["athena_results"].arn}/${local.athena_workgroups.tableau.prefix}/*"]
  }

  # As the summaries role's ListOwnResults (AWS's documented form for a results location).
  statement {
    sid       = "ListOwnResults"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lake["athena_results"].arn]

    condition {
      test     = "StringLikeIfExists"
      variable = "s3:prefix"
      values = [
        local.athena_workgroups.tableau.prefix,
        "${local.athena_workgroups.tableau.prefix}/*",
      ]
    }
  }

  statement {
    sid       = "ResultsBucketLocation"
    effect    = "Allow"
    actions   = ["s3:GetBucketLocation"]
    resources = [aws_s3_bucket.lake["athena_results"].arn]
  }
}

# One inline policy on the group, written out here, so every permission is in this file.
resource "aws_iam_group_policy" "tableau" {
  name   = "silvercare-tableau"
  group  = aws_iam_group.tableau.name
  policy = data.aws_iam_policy_document.tableau.json
}

# ---------------------------------------------------------------------------------------
# Lake Formation grants (criterion 7)
# ---------------------------------------------------------------------------------------

# Per IAM user, as developer_daily_summaries: Lake Formation has no IAM-group principals.
# The for_each key is the (neutral) user name. No grants on any other table.

# AWS requires DESCRIBE on the default database for Athena (see clean_default_database).
resource "aws_lakeformation_permissions" "tableau_default_database" {
  for_each = local.tableau_users

  principal   = aws_iam_user.tableau[each.key].arn
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.default.name
  }
}

resource "aws_lakeformation_permissions" "tableau_lake_database" {
  for_each = local.tableau_users

  principal   = aws_iam_user.tableau[each.key].arn
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.lake.name
  }
}

resource "aws_lakeformation_permissions" "tableau_daily_summaries" {
  for_each = local.tableau_users

  principal   = aws_iam_user.tableau[each.key].arn
  permissions = ["DESCRIBE", "SELECT"]

  table {
    database_name = aws_glue_catalog_database.lake.name
    name          = aws_glue_catalog_table.daily_summaries.name
  }
}
