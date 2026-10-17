---
name: local-dev
description: How to run and test SilverCare locally - unit tests with moto, DynamoDB Local in Docker for integration tests, the Vite dev server, and what can't be emulated. Use when setting up local development, Docker, or integration tests.
---

# Local development

## Levels of testing

1. **Unit tests (default):** pytest + moto. No Docker and no AWS. See `lambda-test`.
2. **Integration tests:** handlers against DynamoDB Local in Docker, to catch key-schema,
   condition-expression, and query mistakes that mocks can hide.
3. **Frontend:** `npm run dev` in `frontend/` (Vite on `http://localhost:5173`) against
   the deployed API and Cognito. The API's CORS allows localhost for this.

## DynamoDB Local

When the first integration test is needed, create `docker-compose.yml` at the repo root
with an `amazon/dynamodb-local` service on port 8000. Pin a specific image tag; check
Docker Hub for the current one and don't use `latest`.

```sh
docker compose up -d dynamodb-local
DYNAMODB_ENDPOINT=http://localhost:8000 pytest lambdas/<name> -m integration
docker compose down
```

- Mark integration tests with `@pytest.mark.integration` and register the marker. Plain
  `pytest` runs should still work without Docker.
- Handlers read an optional `DYNAMODB_ENDPOINT` env var and pass it as `endpoint_url`
  when it's set. It's never set in AWS.
- Integration fixtures create tables with the same key schema and GSIs as Terraform, and
  delete them afterwards.

## What can't run locally

- **IoT Core / MQTT:** no free local emulator. Test handlers with fake rule events (see
  `lambda-test`). End-to-end MQTT tests need a test thing in the real account, and the
  user does those.
- **Cognito and the API Gateway JWT authorizer:** test handlers with fake claims. Test
  real sign-in against the deployed user pool.

## Rules

- Local runs use fake credentials (`AWS_ACCESS_KEY_ID=testing`) unless the user says
  otherwise. Never point local tests at real AWS tables.
- Only synthetic data, locally too.
