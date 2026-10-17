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

## 6. Hand over to the user

Give the user these commands to run themselves with their own AWS credentials:

```sh
cd bootstrap
terraform init
terraform plan        # review: one bucket + its settings, nothing else
terraform apply
cd ..
terraform init        # root: now uses the S3 backend
```

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
