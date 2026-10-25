# python-lambda

One Python Lambda with the SilverCare defaults built in:

- runtime `python3.13`, architecture `arm64`, explicit memory and timeout
- its **own** IAM role, trusted only by `lambda.amazonaws.com` (no conditions: AWS
  documents none for Lambda execution roles)
- one inline policy: the caller's `policy_json` (what the code does), plus
  `logs:CreateLogStream` / `logs:PutLogEvents` on its own log group and
  `sqs:SendMessage` on its own on-failure destination (if one is given). No managed policies, no
  `logs:CreateLogGroup`.
- its log group `/aws/lambda/<function_name>`, created here with a retention period
- an async invoke config (retries, maximum event age, SQS on-failure destination), only
  when `on_failure_destination` is set. Functions fed by an SQS event source mapping
  leave it null; failed messages then go to the source queue's redrive DLQ
- X-Ray tracing off (pass-through only), no VPC

The zip is built by the caller (`data "archive_file"`), so several functions can share
one zip with different `handler`s. Invoke permissions (`aws_lambda_permission`) are
also the caller's job, because they depend on what invokes the function.

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `function_name` | `string` | required | `silvercare-<name>`; also the role and log group name |
| `description` | `string` | required | What the function does |
| `handler` | `string` | required | `<module>.<function>` |
| `zip_path` | `string` | required | `archive_file` `output_path` |
| `zip_hash` | `string` | required | `archive_file` `output_base64sha256` |
| `layers` | `list(string)` | `[]` | Layer version ARNs |
| `environment` | `map(string)` | `{}` | Environment variables (no secrets) |
| `memory_size` | `number` | `128` | MB |
| `timeout` | `number` | `10` | Seconds |
| `log_retention_days` | `number` | `30` | Log group retention |
| `policy_json` | `string` | required | `aws_iam_policy_document` JSON with the code's own permissions |
| `on_failure_destination` | `object({ arn = string })` | `null` | SQS queue for failed async invocations; null = no async invoke config |
| `maximum_retry_attempts` | `number` | `2` | Async retries (0-2) |
| `maximum_event_age_in_seconds` | `number` | `3600` | Async event age limit (60-21600) |
| `tags` | `map(string)` | `{}` | Extra tags (provider `default_tags` are added anyway) |

## Outputs

| Name | Description |
|---|---|
| `function_name` | Function name |
| `function_arn` | Function ARN |
| `role_arn` | The function's role ARN |
| `log_group_name` | The function's log group |

## Example

```hcl
data "archive_file" "ingest" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/ingest/src"
  output_path = "${path.module}/.terraform/build/ingest.zip"
}

module "telemetry_lambda" {
  source = "./modules/python-lambda"

  function_name = "silvercare-telemetry"
  description   = "Validates telemetry and stores the latest 5 per device and type."
  handler       = "handler.telemetry_handler"
  zip_path      = data.archive_file.ingest.output_path
  zip_hash      = data.archive_file.ingest.output_base64sha256
  layers        = [local.powertools_layer_arn]

  environment = {
    LATEST_TABLE            = module.latest_table.name
    POWERTOOLS_SERVICE_NAME = "silvercare-telemetry"
  }

  policy_json                = data.aws_iam_policy_document.ingest_latest_table.json
  on_failure_destination = { arn = aws_sqs_queue.ingest_failed.arn }
}
```
