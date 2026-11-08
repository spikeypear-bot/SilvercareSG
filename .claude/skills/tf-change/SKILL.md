---
name: tf-change
description: Conventions and checks for every Terraform change in this repo. Use when adding, editing, or removing Terraform resources, modules, variables, or IAM policies.
---

# Terraform changes

Merging to `main` runs `terraform apply` in CI. Treat every change as going live.

## Rules
- Root config lives in `infra/` (CI runs from there). Reusable modules go in
  `infra/modules/` (see the `tf-module` skill). `infra/bootstrap/` is applied by hand only.
- Serverless only. No EC2, NAT Gateway, provisioned RDS, or other fixed-cost resources,
  except the simulator instance agreed in `CLAUDE.md` (`infra/simulator.tf`).
- The account is on AWS's free plan, which blocks some services until it is upgraded
  (e.g. Data Firehose, Kinesis, End User Messaging SMS). Before proposing a service not
  already in `infra/`, check it under "Free Tier" in
  https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html.
- Region: ap-southeast-1
- Encrypt every data store at rest (KMS or AWS-managed keys) and enforce TLS in transit.
  CloudWatch alarms can't publish to an SNS topic encrypted with `aws/sns`, but Lambdas
  can: give alarms their own topic and send data-carrying emails through a separate
  `aws/sns` topic (free) rather than leaving one shared topic unencrypted or paying for a
  customer-managed key.
- IAM: follow the `iam-policy` skill (one role per Lambda, no `*` actions or resources).
- Tag every resource: `project = "silvercare"`, `managed_by = "terraform"`
- Pin provider versions in `required_providers`, and set `required_version` for Terraform
  (tflint flags it if missing).
- Outputs: `terraform apply` in `deploy.yml` prints every non-sensitive output to the
  public Actions log. Mark an output `sensitive = true` unless it's fine to publish (it
  can still be read with `terraform output`). Any `terraform` step in a workflow must also
  redact stderr, because discarding stdout alone leaves error text in that log.
- A name Terraform doesn't create (a hard-coded `default`, a `data` lookup) fails only at
  plan or apply: `make tf-check` never contacts AWS. Create it in Terraform where that's
  safe; otherwise have the owner confirm it exists in AWS before the first push. Docs saying
  it exists aren't proof, especially for anything made by hand. For the default VPC,
  existing isn't enough: its network ACL here has no inbound allow rule, so anything in
  it needs `aws_network_acl_rule.simulator_replies` (`infra/simulator.tf`) or its own.
- A new service that stores or returns data: add its read actions to `DenyDataReads` in
  `infra/bootstrap/ci-plan-role.tf` (the owner applies it). The plan role has
  `ReadOnlyAccess`, and any PR from this repo can use it.
- A guard someone must see before merging is a `precondition` (an error), never a
  `check` block: a failed check is only a warning, warnings go to stdout, and
  `plan.yml` and `deploy.yml` discard plan stdout, so it never reaches the PR comment.
  A precondition on an `output` adds no dependency cycle (outputs are leaf nodes).
- A `file()` on a missing path errors before any `precondition` runs. To fail with your
  own message, guard it: `fileexists(p) ? file(p) : "<placeholder>"`. Test such logic
  without AWS on `terraform_data` in a scratch dir.
- `make tf-check` never renders a `templatefile()` (EC2 user data, config files), so a
  broken command in one passes every check and only fails on the instance. Add a pytest
  that renders it with fake values and checks the commands it builds argument by
  argument (`shlex.split`) and `bash -n`, like `simulator/tests/test_ec2_user_data.py`.

## Steps
1. Read existing `.tf` files and match their layout and naming.
2. Make the change.
3. Run `make fmt` and `make tf-check` (fmt check, validate, tflint, checkov, all in the
   dev container); fix any errors. Fix checkov findings or, if one doesn't apply, add a
   `#checkov:skip=<ID>:<reason>` comment on the resource and mention it in the summary.
4. Summarise: resources added / changed / destroyed, and flag anything
   destructive (replacements, deletions) or with ongoing cost. A replaced Lambda with a
   new role is down for up to about a minute (destroy first, then IAM propagation),
   not seconds: say so for anything on an alert path.

## Never
- Run `terraform apply` or `terraform destroy`.
- Change AWS resources outside Terraform.
- Commit or push.
