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

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:spikeypear-bot/SilvercareSG:pull_request"]
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
    ]
    resources = ["*"]
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
