---
name: cognito-auth
description: Set up or change Cognito authentication and authorisation - user pool, groups, app client, API JWT authorizer, and how the frontend signs in. Use when touching login, users, groups, roles, or tokens.
---

# Cognito auth

Groups: `doctor`, `nurse`, `caregiver`, `family` (see `CLAUDE.md`); `doctor` and `nurse`
are "staff". Groups decide what kind of
user someone is; links stored in DynamoDB decide **which residents** they can see. Both
checks happen in the API. The frontend only uses groups to choose which screens to show.

## User pool (Terraform)

- `aws_cognito_user_pool`:
  - sign-in with email (`username_attributes = ["email"]`), email auto-verified
  - `admin_create_user_config { allow_admin_create_user_only = true }` until the
    onboarding decision in `CLAUDE.md` says otherwise
  - strong password policy; MFA optional (TOTP), as decided by the user
  - `deletion_protection = "ACTIVE"`
  - only the attributes the app needs. No NRIC, no health data in user attributes.
  - **Required attributes are writable by every app client**, whatever
    `write_attributes` says. Only mark an attribute required if users may change it
    themselves. `name` and `email` are deliberately not required (no `schema` blocks), so
    only admins can change them.
  - Required-ness, `username_attributes`, `username_configuration`, and `schema` blocks
    can't change after creation: changing them replaces the pool and deletes every user.
    Keep `lifecycle { prevent_destroy = true }` on the pool.
- One `aws_cognito_user_group` per group.
- `aws_cognito_user_pool_client` for the React app:
  - `generate_secret = false` (it runs in a browser and can't keep a secret)
  - `explicit_auth_flows = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]`
  - `prevent_user_existence_errors = "ENABLED"`
  - set token validity explicitly (short access/ID token lifetime, longer refresh token)
  - `write_attributes` must list only what users may change about themselves, and must
    never be empty or unset: Cognito treats that as "every attribute writable". If users
    may change nothing, list one attribute the app never reads (this repo uses
    `["zoneinfo"]`). This applies to every app client in the pool.
  - `auto_verified_attributes` does not verify admin-created users. Admins must mark the
    email verified (`email_verified=true`), or password reset by verified email fails
    silently.

## API authorizer

- `aws_apigatewayv2_authorizer` of type `JWT`:
  - `issuer = "https://cognito-idp.ap-southeast-1.amazonaws.com/<user_pool_id>"`
  - `audience = [<app_client_id>]`
- The frontend sends the **access token** as `Authorization: Bearer <token>`.
- Group checks happen in the handler (see `api-route`).

## Frontend

- Use `aws-amplify` v6, **Auth only** (`aws-amplify/auth`), configured from Vite env vars
  (`VITE_USER_POOL_ID`, `VITE_USER_POOL_CLIENT_ID`). These IDs are not secrets.
- Custom sign-in screens built with Tailwind. Handle new-password-required (for
  admin-created users) and MFA challenges.
- Read groups from the current session's token to pick routes and views.
- Never store tokens yourself (Amplify handles storage). Never put tokens in URLs.

## Never

- Trust a group or user ID sent by the client in a body, header, or query string.
- Add self sign-up, or a group with broader access, without the user deciding it.
