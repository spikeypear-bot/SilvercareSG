---
name: iam-policy
description: Write or review IAM roles and policies (and AWS IoT device policies) in Terraform with least privilege. Use when a Lambda, IoT rule, device, or CI needs AWS permissions, or when adding or changing any aws_iam_* or aws_iot_policy resource.
---

# IAM policies

This system handles residents' health data (PDPA). Every permission must be
justified by something the code actually does.

## Rules

- **One role per Lambda / IoT rule.** Never share a role between functions.
- **Write policies with `data "aws_iam_policy_document"`**, not inline JSON strings
  or `jsonencode`, so Terraform validates the structure.
- **No `*` in actions or resources.** List exact actions
  (`dynamodb:PutItem`, not `dynamodb:*`) and reference resource ARNs from Terraform
  (`aws_dynamodb_table.x.arn`), never hand-typed ARNs.
  - Exception: actions AWS only supports on `*` (e.g. `xray:PutTraceSegments`).
    Add a comment saying so.
- **No AWS managed policies** with broad scope (`AdministratorAccess`,
  `PowerUserAccess`, `*FullAccess`). Small, specific managed policies are fine
  only if they grant nothing extra; prefer writing it out.
- **Trust policies** name exactly one service principal
  (`lambda.amazonaws.com`, `iot.amazonaws.com`). Add `aws:SourceAccount` /
  `aws:SourceArn` conditions where the service supports them.
- **Logging:** grant `logs:CreateLogStream` and `logs:PutLogEvents` on the
  function's own log group ARN only. Create the log group in Terraform with a
  retention period so the role does not need `logs:CreateLogGroup`.
- **KMS:** if a resource uses a customer-managed key, grant only `kms:Decrypt`
  / `kms:GenerateDataKey` as needed, on that key's ARN.
- **Use conditions** to narrow further where useful (e.g.
  `dynamodb:LeadingKeys`, `aws:SecureTransport`).

## IoT device policies

Devices must only act as themselves:

- `iot:Connect` only with a client ID equal to the thing name:
  `arn:aws:iot:<region>:<account>:client/${iot:Connection.Thing.ThingName}`
- `iot:Publish` only to that device's own topics, e.g.
  `topic/silvercare/*/${iot:Connection.Thing.ThingName}/*`
- `iot:Subscribe` / `iot:Receive` only if the device needs commands, and only on
  its own command topic.
- In Terraform, escape the policy variable as `$${iot:Connection.Thing.ThingName}`.

## CI role

Two roles are assumed by GitHub Actions through OIDC:

- `silvercare-cicd` (deploy, `main` only) is used by `deploy.yml`.
- `silvercare-cicd-plan` (read-only, pull requests) is used by `plan.yml` and defined in
  `bootstrap/` (see `tf-state-bootstrap`).

Do not widen either role's permissions or trust policy as a side effect of another
change. Never let pull-request workflows assume the deploy role. If a change needs a CI
role to have more access, stop and tell the user what to add and why.

## Steps

1. List what the code does with AWS (each API call and the resource it touches).
2. Write one statement per resource group with exactly those actions.
3. Run `make fmt` and `make tf-check`.
4. Summarise the permissions granted, in plain words, per role. Flag any `*`, any
   cross-account access, and any change to a trust policy.

## Never

- Grant permissions "for later" or to make an error go away without knowing why.
- Run `terraform apply`, change IAM in the console, or commit/push.
