---
name: tf-state-bootstrap
description: One-time setup of the S3 remote state backend for this repo's Terraform. Run only when the user asks.
disable-model-invocation: true
---

# Bootstrap Terraform remote state

CI runners are ephemeral. Without a remote backend, every deploy starts with empty
state and tries to create everything again. This sets up an S3 bucket for state
with S3-native locking (`use_lockfile`, Terraform >= 1.11; no DynamoDB lock table).

It's a chicken-and-egg problem: the bucket must exist before the root config can use it.
So the bucket is created by a separate config in `bootstrap/`, which the user applies
**once, by hand**. CI never runs it, because CI only reads `.tf` files at the repo root.

Claude writes the files and gives the user the commands. Claude does not run
`terraform apply`.

## 1. Check first

- If `bootstrap/` or a `backend "s3"` block already exists, stop and show the user
  what's there instead of overwriting it.
- Confirm with the user: AWS account ID (CI uses `753674274065`), region
  (`ap-southeast-1`), and the bucket name. Suggest
  `silvercare-tfstate-<account-id>`, since bucket names are globally unique.

## 2. Write `bootstrap/`

`bootstrap/main.tf` creates:

- `aws_s3_bucket` with `lifecycle { prevent_destroy = true }`
- `aws_s3_bucket_versioning`: `Enabled` (lets you recover a corrupted state)
- `aws_s3_bucket_server_side_encryption_configuration`: SSE-S3 or KMS
- `aws_s3_bucket_public_access_block`: all four settings `true`
- `aws_s3_bucket_ownership_controls`: `BucketOwnerEnforced`
- `aws_s3_bucket_policy` denying any request where `aws:SecureTransport` is false
- Optional `aws_s3_bucket_lifecycle_configuration` that expires noncurrent
  versions after e.g. 90 days

`bootstrap/versions.tf`: `required_version >= 1.11`, pinned AWS provider, and a
`provider "aws"` with the region. No backend block, so it uses local state.

`bootstrap/outputs.tf`: the bucket name and ARN.

## 3. Write the root backend

Root `backend.tf`:

```hcl
terraform {
  backend "s3" {
    bucket       = "<bucket-name>"
    key          = "silvercare/terraform.tfstate"
    region       = "ap-southeast-1"
    encrypt      = true
    use_lockfile = true
  }
}
```

Backend blocks cannot use variables, so write the literal values.

## 4. `.gitignore`

Make sure it contains:

```
.terraform/
*.tfstate
*.tfstate.*
*.tfplan
tfplan
crash.log
```

Keep `.terraform.lock.hcl` committed (it pins provider versions).

## 5. CI role permissions

The `silvercare-cicd` role needs, on the state bucket only:

- `s3:ListBucket` on the bucket ARN
- `s3:GetObject`, `s3:PutObject` on `<bucket-arn>/silvercare/terraform.tfstate`
- `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on
  `<bucket-arn>/silvercare/terraform.tfstate.tflock`

Tell the user to add these. Do not change the CI role yourself (see the
`iam-policy` skill).

Also tell the user to check that `silvercare-cicd`'s trust policy only allows `main`:
the `token.actions.githubusercontent.com:sub` condition must be exactly
`repo:spikeypear-bot/SilvercareSG:ref:refs/heads/main` (`StringEquals`, no wildcards).
Otherwise a pull request could edit a workflow to assume the deploy role.

## 5b. CI plan role (`bootstrap/ci-plan-role.tf`)

`.github/workflows/plan.yml` assumes a separate read-only role on pull requests. Create
it in `bootstrap/`, because it must exist before the root config can be planned:

- `data "aws_iam_openid_connect_provider" "github"` with
  `url = "https://token.actions.githubusercontent.com"`. It already exists, since the
  deploy role uses it. If it doesn't, stop and ask the user.
- `aws_iam_role` named `silvercare-cicd-plan`. Its trust policy allows
  `sts:AssumeRoleWithWebIdentity` from that provider, with `StringEquals` on:
  - `token.actions.githubusercontent.com:aud` = `sts.amazonaws.com`
  - `token.actions.githubusercontent.com:sub` = `repo:spikeypear-bot/SilvercareSG:pull_request`
- Attach the AWS managed `ReadOnlyAccess` policy, so `terraform plan` can read the
  configuration of every resource type. This is an exception to the `iam-policy` rule
  against broad managed policies: it's read-only, and the deny below removes data access.
- An inline policy that:
  - **allows** `s3:ListBucket` on the state bucket and `s3:GetObject` on
    `<bucket-arn>/silvercare/terraform.tfstate` (no lock-file access: the plan job uses
    `-lock=false`)
  - **denies** reading actual data, so PR workflows can never see resident data:
    `dynamodb:GetItem`, `dynamodb:BatchGetItem`, `dynamodb:Query`, `dynamodb:Scan`,
    `dynamodb:PartiQLSelect`, `dynamodb:GetRecords`, `s3:GetObject` and
    `s3:GetObjectVersion` with `NotResource` = the state object, `logs:GetLogEvents`,
    `logs:FilterLogEvents`, `logs:StartQuery`, `cognito-idp:ListUsers`,
    `cognito-idp:ListUsersInGroup`, `cognito-idp:AdminGetUser`, `iot:GetThingShadow`,
    `secretsmanager:GetSecretValue`
- `max_session_duration = 3600`.

## 6. Hand over to the user

Give the user these commands to run themselves, **in their own terminal outside Claude
Code**, with their own AWS credentials. Claude Code's environment hides the credentials,
and the dev container has none. These run the dev image directly with the user's
`~/.aws` mounted read-only. Use plain `docker run`, not `docker compose`, because the
compose service sets fake credentials that would override the real ones:

```sh
tf() { docker run --rm -it -u "$(id -u):$(id -g)" -v "$PWD:/work" \
  -v "$HOME/.aws:/cache/home/.aws:ro" -e AWS_PROFILE="${AWS_PROFILE:-default}" \
  -w "/work/$1" silvercare-dev:local terraform "${@:2}"; }

tf bootstrap init
tf bootstrap plan     # review: one bucket + its settings, nothing else
tf bootstrap apply
tf . init             # root: now uses the S3 backend
```

If they have Terraform installed on the host, plain `terraform` in `bootstrap/` works too.

If the root config already has local state, `terraform init -migrate-state`
moves it into the bucket.

The bootstrap's own local `terraform.tfstate` is small but needed to manage the
bucket later. Tell the user to keep it somewhere safe outside git, or migrate it
into the same bucket under a different key (e.g. `bootstrap/terraform.tfstate`)
after the first apply.

## Never

- Run `terraform apply`, `destroy`, or `init -migrate-state` yourself.
- Remove `prevent_destroy` or disable versioning.
- Commit or push.
