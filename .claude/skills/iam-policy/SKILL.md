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
  (`lambda.amazonaws.com`, `iot.amazonaws.com`). Lambda execution roles have no
  conditions (AWS documents none for them). For other services, add `aws:SourceAccount` /
  `aws:SourceArn` conditions where the service supports them. IoT rule roles get both:
  `aws:SourceAccount` and `ArnLike` `aws:SourceArn` on `rule/silvercare_*` (a pattern,
  because the rules depend on the role). A spec that names only one doesn't remove the
  other.
- **Resource policies** (SQS queue, SNS topic, S3 bucket) limiting a resource to our
  own roles: don't add an Allow naming a role ARN as principal. Same-account roles get
  the action from their IAM policy, and SNS/SQS check that named principals exist, so a
  role created in the same apply can fail it (PrincipalNotFound). Use a Deny with an
  `aws:PrincipalArn` condition instead. Service principals (e.g.
  `cloudwatch.amazonaws.com` publishing alarms) still need an Allow. Such a Deny also
  blocks admins and root, so never put one on an action operators need for recovery,
  e.g. `sqs:SendMessage` on a queue a DLQ redrives into.
- **SNS topic policies** reject `sns:*` ("Policy statement action out of service
  scope"), and only fail at apply: `tf-check` doesn't catch it. List the topic actions
  instead (`sns:AddPermission`, `DeleteTopic`, `GetTopicAttributes`,
  `ListSubscriptionsByTopic`, `Publish`, `RemovePermission`, `SetTopicAttributes`,
  `Subscribe`), e.g. for a TLS-only Deny. SQS (`sqs:*`) and S3 (`s3:*`) accept wildcards.
- **Logging:** grant `logs:CreateLogStream` and `logs:PutLogEvents` on the
  function's own log group ARN only. Create the log group in Terraform with a
  retention period so the role does not need `logs:CreateLogGroup`.
- **KMS:** if a resource uses a customer-managed key, grant only `kms:Decrypt`
  / `kms:GenerateDataKey` as needed, on that key's ARN.
- **Use conditions** to narrow further where useful (e.g.
  `dynamodb:LeadingKeys`, `aws:SecureTransport`). Don't copy a peer role's wildcard
  resource either.
- **DynamoDB attribute lists:** `ForAllValues:StringEquals` on `dynamodb:Attributes`
  passes vacuously when a read sends no `ProjectionExpression` (the key is absent), so
  on its own it doesn't stop a GetItem / BatchGetItem / Query reading the whole item.
  Pair it with `Null` `dynamodb:Attributes` = `"false"` (and `dynamodb:Select` =
  `SPECIFIC_ATTRIBUTES`, `IfExists` for the GetItem family) on every read statement
  that is meant to hide fields such as `medical_notes` or a `sub`. This includes a
  `Query` on an index that projects personal data (the registry's `by-type` projects
  `display_name`, `home_id`, `photo_key`; see `api_devices` in `infra/api.tf`): list
  only the attributes read plus keys, and send `Select: SPECIFIC_ATTRIBUTES` in the
  request. For a paginated index Query, "keys" means the index's keys **and** the base
  table's (`pk`, `sk`): a later page's `ExclusiveStartKey` holds them, so leaving `sk`
  out only fails once there's a second page.
- **Lake Formation principals:** an IAM user or role that gets Lake Formation grants has
  no `path`, since its ARN is the principal and AWS documents only plain
  `user/<name>` / `role/<name>` ARNs there.
- **Prefix-protecting Denies** in a bucket policy that rely on object tags (e.g. a
  `resident_id` tag used for erasure) also deny `s3:PutObjectTagging` and
  `s3:DeleteObjectTagging` to everyone but the writer.

## IoT device policies

Devices must only act as themselves:

- `iot:Connect` only with a client ID equal to the thing name:
  `arn:aws:iot:<region>:<account>:client/${iot:Connection.Thing.ThingName}`
- `iot:Publish` only to that device's own topics, e.g.
  `topic/silvercare/*/${iot:Connection.Thing.ThingName}/*`
- `iot:Subscribe` / `iot:Receive` only if the device needs commands, and only on
  its own command topic.
- In Terraform, escape the policy variable as `$${iot:Connection.Thing.ThingName}`.
- **Size:** an IoT policy is at most 2048 characters excluding whitespace, and only
  `apply` finds out (`validate`, checkov, and `plan` pass). Every full ARN costs about
  100 characters, so prefer one `.../<request>/*` wildcard over listing each reply topic,
  and give a new permission set its own policy. Keep
  `test_device_policies_fit_aws_iot_size_limit` (`lambdas/shadow-sync/tests/test_infra.py`)
  passing when adding statements.

## CI role

Two roles are assumed by GitHub Actions through OIDC:

- `silvercare-cicd` (deploy, `main` only) is used by `deploy.yml`. Created by hand, not in
  Terraform, with `AdministratorAccess`, so new services need no extra deploy permissions.
- `silvercare-cicd-plan` (read-only, pull requests) is used by `plan.yml` and defined in
  `infra/bootstrap/` (see `tf-state-bootstrap`).

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
