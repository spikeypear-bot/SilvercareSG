---
name: tf-change
description: Conventions and checks for every Terraform change in this repo. Use when adding, editing, or removing Terraform resources, modules, variables, or IAM policies.
---

# Terraform changes

Merging to `main` runs `terraform apply` in CI. Treat every change as going live.

## Rules
- Root config lives at the repo root (CI runs from there). Reusable modules go in
  `modules/` (see the `tf-module` skill).
- Serverless only. No EC2, NAT Gateway, provisioned RDS, or other fixed-cost resources.
- Region: ap-southeast-1
- Encrypt every data store at rest (KMS or AWS-managed keys) and enforce TLS in transit.
- IAM: follow the `iam-policy` skill (one role per Lambda, no `*` actions or resources).
- Tag every resource: `project = "silvercare"`, `managed_by = "terraform"`
- Pin provider versions in `required_providers`.

## Steps
1. Read existing `.tf` files and match their layout and naming.
2. Make the change.
3. Run `terraform fmt -recursive` and `terraform validate`; fix any errors.
4. Summarise: resources added / changed / destroyed, and flag anything
   destructive (replacements, deletions) or with ongoing cost.

## Never
- Run `terraform apply` or `terraform destroy`.
- Change AWS resources outside Terraform.
- Commit or push.
