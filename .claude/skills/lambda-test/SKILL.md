---
name: lambda-test
description: How to write and run pytest tests for Python Lambdas in this repo - fake IoT and API Gateway events, moto for AWS, synthetic data, and required cases. Use when writing, fixing, or running Lambda tests.
argument-hint: [function-name]
---

# Lambda tests

Function: $ARGUMENTS

## Layout

```
lambdas/<name>/tests/
├── conftest.py         # path setup, fake AWS env, fixtures
├── events.py           # builders for fake events
└── test_handler.py
```

`conftest.py` must:

- add `../src` to `sys.path` so `import handler` works
- set fake credentials and region **before** any boto3 import:
  `AWS_ACCESS_KEY_ID=testing`, `AWS_SECRET_ACCESS_KEY=testing`,
  `AWS_DEFAULT_REGION=ap-southeast-1`, and the env vars the handler reads
  (table names etc.)
- provide fixtures that start moto (`from moto import mock_aws`) and create the tables
  and buckets the handler needs, matching the key schema in Terraform

Run tests in the dev container: `make test FN=<name>` (unit) and
`make test-integration FN=<name>` (against DynamoDB Local; see `local-dev`). `make test`
alone runs every function, each in its own pytest run, because they all have a module
named `handler` and a single run would import the wrong one.

The container already sets fake AWS credentials and the region, but keep the
`conftest.py` defaults too, so the tests don't depend on where they run.

## Events

Build fake events in `events.py` with small functions that take overrides:

- **IoT rule events:** the payload from the example in `docs/metrics.md`, plus any fields
  the topic rule SQL adds (e.g. `device_id` from `topic(n)`).
- **API Gateway HTTP API (payload 2.0):** `requestContext.authorizer.jwt.claims` with
  `sub` and `cognito:groups`, plus `pathParameters`, `queryStringParameters`, and a
  JSON `body` string.

## Data

Synthetic only and obviously fake: `resident-test-001`, "Test Resident A",
`device-test-01`, phone `+65 0000 0000`. Never use real names, NRIC-like strings, or
anything copied from production.

## Required cases

For every handler:

1. Valid input → expected result and expected DynamoDB/S3 state.
2. Malformed input (bad JSON, wrong types) → handled without raising, nothing written.
3. Missing required field.
4. Unknown `schema_version` (IoT) or unknown fields (API).
5. Duplicate delivery → second call writes nothing new (idempotency).
6. Transient AWS error (e.g. throttling, via `botocore.stub.Stubber` or patching) →
   raises so it gets retried.

Also, for API handlers:

7. A caller without the required group → 403.
8. A caller with the right group but no link to the requested resident → 403/404.
9. Responses contain only the fields that role should see.

For alert logic: thresholds at, just below, and just above each boundary.

## Rules

- Tests never call real AWS. If a test needs credentials it isn't mocked properly; fix
  the test.
- Test pure logic directly, without moto, wherever possible.
- One behaviour per test. Name tests after the behaviour:
  `test_duplicate_reading_is_not_written_twice`.
