---
name: tf-change
description: Conventions and checks for every Terraform change in this repo. Use when adding, editing, or removing Terraform resources, modules, variables, or IAM policies.
---

# Terraform changes

Merging to `main` runs `terraform apply` in CI. Treat every change as going live.

## Rules
- Root config lives in `infra/` (CI runs from there). Reusable modules go in
  `infra/modules/` (see the `tf-module` skill). `infra/bootstrap/` is applied by hand only.
- Serverless only. No EC2, NAT Gateway, provisioned RDS, or other fixed-cost resources.
- The account is on AWS's free plan, which blocks some services until it is upgraded
  (e.g. Data Firehose, Kinesis, End User Messaging SMS). Before proposing a service not
  already in `infra/`, check it under "Free Tier" in
  https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html.
- Region: ap-southeast-1
- Encrypt every data store at rest (KMS or AWS-managed keys) and enforce TLS in transit.
- IAM: follow the `iam-policy` skill (one role per Lambda, no `*` actions or resources).
- Tag every resource: `project = "silvercare"`, `managed_by = "terraform"`
- Pin provider versions in `required_providers`, and set `required_version` for Terraform
  (tflint flags it if missing).
- Outputs: `terraform apply` in `deploy.yml` prints every non-sensitive output to the
  public Actions log. Mark an output `sensitive = true` unless it's fine to publish (it
  can still be read with `terraform output`).
- A `file()` on a missing path errors before any `precondition` runs. To fail with your
  own message, guard it: `fileexists(p) ? file(p) : "<placeholder>"`. Test such logic
  without AWS on `terraform_data` in a scratch dir.

## Steps
1. Read existing `.tf` files and match their layout and naming.
2. Make the change.
3. Run `make fmt` and `make tf-check` (fmt check, validate, tflint, checkov, all in the
   dev container); fix any errors. Fix checkov findings or, if one doesn't apply, add a
   `#checkov:skip=<ID>:<reason>` comment on the resource and mention it in the summary.
4. Summarise: resources added / changed / destroyed, and flag anything
   destructive (replacements, deletions) or with ongoing cost.

## Never
- Run `terraform apply` or `terraform destroy`.
- Change AWS resources outside Terraform.
- Commit or push.
