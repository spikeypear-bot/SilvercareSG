---
name: lambda-python
description: Conventions for writing or changing Python Lambda functions under lambdas/ - layout, handler structure, config, logging, errors, idempotency, and the matching Terraform. Use when creating or editing a Lambda handler.
argument-hint: [function-name]
---

# Python Lambdas

Function: $ARGUMENTS

## Layout

```
lambdas/<name>/
├── src/
│   └── handler.py      # lambda_handler(event, context); more modules allowed in src/
└── tests/              # see lambda-test
```

`<name>` is kebab-case and describes the job: `ingest-vitals`, `api-residents`,
`check-inactivity`.

Functions that share most of their code may share one folder (e.g. `lambdas/ingest/`):
`handler.py` then has one named entry point per function, and Terraform builds every
function from the same zip, each with its own role, log group, and failure handling.

## Handler rules

- Runtime `python3.13`. Dependencies: standard library, `boto3` (in the runtime), and
  AWS Lambda Powertools (public layer). Anything else needs a packaging decision first.
- **Config from environment variables** set by Terraform (table names, bucket names,
  topic ARNs). No hardcoded names, ARNs, or account IDs.
- **Create boto3 clients/resources at module level** so they are reused across
  invocations.
- **Keep logic separate from AWS calls:** parse/validate → pure decision logic → AWS
  calls. That way the logic can be tested without mocks.
- **Validate input at the boundary.** IoT payloads are checked against `docs/metrics.md`,
  API bodies against the route's expected shape. Reject unknown fields and versions.
- **Logging:** Powertools `Logger(service="<name>")`. Log IDs, event types, counts, and
  outcomes. Never log payload bodies, names, contact details, or health values. An ID
  that comes from the payload (e.g. `message_id`) is logged only if it matches its
  expected format, otherwise as `invalid`: a free-text "ID" can carry health data.
- **Idempotency:** IoT Core and async Lambda invocations retry and can deliver
  duplicates. Writes use deterministic keys or condition expressions.
- **Errors:**
  - Invalid input: log the reason (without the data) and return without raising, so
    retries don't loop on a message that can never succeed.
  - Transient failures (throttling, timeouts): raise, so the platform retries.
  - Async-invoked functions get an on-failure destination or DLQ in Terraform, unless
    the spec decides against it (the ingest functions have none). Anything that raises
    alerts should have one: a dropped message there is a lost alert.
- Use type hints. Keep functions small.

## API handlers

For functions behind API Gateway (payload format 2.0), also follow `api-route`. Read the
caller's identity only from the JWT claims in
`event["requestContext"]["authorizer"]["jwt"]["claims"]`, never from the body or headers.

## Terraform per function

Follow `tf-change` and `iam-policy`. Each function gets:

- `data "archive_file"` zipping `lambdas/<name>/src/`
- `aws_lambda_function` with explicit `timeout`, `memory_size`, `runtime`, environment
  variables, and the Powertools layer
- its own IAM role with only the actions the code uses
- `aws_cloudwatch_log_group` named `/aws/lambda/<function-name>` with a retention period
- `aws_lambda_permission` for whatever invokes it (IoT, API Gateway, EventBridge)
- for an SQS trigger: the queue's visibility timeout is at least 6 × the function
  timeout **plus** `maximum_batching_window_in_seconds`, or messages are redelivered
  while still in flight and reach the DLQ without ever failing

Once two functions exist, put this in a `python-lambda` module (see `tf-module`).

## Finish

Run `make fmt` and `make lint`, then hand off to `lambda-tester` or follow `lambda-test`.
To support integration tests, pass `os.environ.get("DYNAMODB_ENDPOINT")` as
`endpoint_url` when creating DynamoDB clients (see `local-dev`).
