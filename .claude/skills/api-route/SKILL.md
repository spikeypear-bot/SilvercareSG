---
name: api-route
description: Add or change an HTTP API route (API Gateway HTTP API + Lambda + Cognito JWT authorizer) and the access checks for it. Use when the frontend needs a new endpoint or an endpoint's request, response, or permissions change.
argument-hint: [METHOD /path]
---

# API routes

Route: $ARGUMENTS

## Design first

Before writing code, state:

- method and path (`GET /residents/{resident_id}/status`); plural nouns, `snake_case` JSON
- which Cognito groups may call it
- how access to the **specific resident** is checked (e.g. a caregiver/family
  assignment item in DynamoDB)
- request shape, response shape per role, and error responses

## Terraform

Follow `tf-change` and `iam-policy`.

- `aws_apigatewayv2_route` with `authorization_type = "JWT"` and the shared Cognito
  authorizer. No unauthenticated routes unless the user explicitly agrees.
- `aws_apigatewayv2_integration` of type `AWS_PROXY` to the Lambda, with
  `payload_format_version = "2.0"`.
- `aws_lambda_permission` for `apigateway.amazonaws.com`, with `source_arn` scoped to the
  API's execution ARN and this route.
- CORS on the API allows only the CloudFront domain (and `http://localhost:5173` for
  local dev), never `*`.
- Stage throttling limits set.

## Handler

Follow `lambda-python`, plus:

1. Read `sub` and `cognito:groups` from
   `event["requestContext"]["authorizer"]["jwt"]["claims"]`. The groups claim may arrive
   as a string such as `"[staff caregiver]"` rather than a list, so parse it defensively.
2. Check the group. Return 403 if it's missing.
3. Check the caller's link to the requested resident. Return 404 (not 403) if there's no
   link, so callers can't probe which resident IDs exist.
4. Validate the path, query, and body.
5. Return only the fields this role may see. Build the response explicitly; never return
   raw DynamoDB items.
6. Errors are `{"error": {"code": "...", "message": "..."}}` with the right status code.
   No stack traces or internal details.

## Frontend

Add the typed request/response and the call to the API client in `frontend/` (see the
`frontend` skill).

## Tests

Follow `lambda-test`, including the group and resident-link cases.
