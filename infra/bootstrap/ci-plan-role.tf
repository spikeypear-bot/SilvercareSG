# Read-only role that .github/workflows/plan.yml assumes on pull requests to run
# `terraform plan`. It can read resource configuration and the state file, but never
# resident data. Lives here because it must exist before the root config can be planned.

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "ci_plan_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # The repo uses GitHub's immutable OIDC subject (owner and repo IDs after the names),
    # so a renamed or re-created repo with the same name can't assume this role.
    # Check with: gh api repos/{owner}/{repo}/actions/oidc/customization/sub
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:spikeypear-bot@232546008/SilvercareSG@1416327993:pull_request"]
    }
  }
}

resource "aws_iam_role" "ci_plan" {
  name                 = "silvercare-cicd-plan"
  description          = "Read-only terraform plan for pull requests (GitHub Actions OIDC)"
  assume_role_policy   = data.aws_iam_policy_document.ci_plan_trust.json
  max_session_duration = 3600
}

# Broad on purpose so plan can read every resource type's configuration. Read-only, and
# the deny statements below remove access to actual data.
resource "aws_iam_role_policy_attachment" "ci_plan_read_only" {
  role       = aws_iam_role.ci_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

locals {
  tfstate_object_arn = "${aws_s3_bucket.tfstate.arn}/silvercare/terraform.tfstate"
}

data "aws_iam_policy_document" "ci_plan" {
  statement {
    sid       = "ListStateBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.tfstate.arn]
  }

  # No lock-file access: the plan job runs with -lock=false.
  statement {
    sid       = "ReadState"
    actions   = ["s3:GetObject"]
    resources = [local.tfstate_object_arn]
  }

  statement {
    sid    = "DenyDataReads"
    effect = "Deny"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:BatchGetItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:PartiQLSelect",
      "dynamodb:GetRecords",
      "logs:GetLogEvents",
      "logs:FilterLogEvents",
      "logs:StartQuery",
      "cognito-idp:ListUsers",
      "cognito-idp:ListUsersInGroup",
      "cognito-idp:AdminGetUser",
      "iot:GetThingShadow",
      "secretsmanager:GetSecretValue",
      # Lake Formation read-only admin may view the catalog, never fetch data credentials.
      "lakeformation:GetDataAccess",
      # Query text and failure reasons can quote data. Plans only need athena:GetWorkGroup.
      "athena:GetQueryExecution",
      "athena:BatchGetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetQueryResultsStream",
    ]
    resources = ["*"]
  }

  # Device private keys for the EC2 simulator (docs/features/simulator-ec2.md, 8a).
  # The aws/ssm key decrypts for every principal in the account, so ssm:GetParameter*
  # is the only lock. Scoped to our path: plans still read AWS's public AMI parameter.
  statement {
    sid    = "DenyProjectParameterReads"
    effect = "Deny"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath",
      "ssm:GetParameterHistory",
    ]
    # The bare path too: GetParametersByPath is authorised on the path itself.
    resources = [
      "arn:aws:ssm:ap-southeast-1:753674274065:parameter/silvercare",
      "arn:aws:ssm:ap-southeast-1:753674274065:parameter/silvercare/*",
    ]
  }

  statement {
    sid           = "DenyObjectReadsExceptState"
    effect        = "Deny"
    actions       = ["s3:GetObject", "s3:GetObjectVersion"]
    not_resources = [local.tfstate_object_arn]
  }
}

resource "aws_iam_role_policy" "ci_plan" {
  name   = "silvercare-cicd-plan"
  role   = aws_iam_role.ci_plan.id
  policy = data.aws_iam_policy_document.ci_plan.json
}
