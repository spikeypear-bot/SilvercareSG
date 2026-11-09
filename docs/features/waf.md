---
status: draft          # draft | ready | built
---

# AWS WAF

## Summary

Two AWS WAF web ACLs, in Terraform: one for the CloudFront website and one for the
Cognito user pool `silvercare-users`, which covers sign-in requests. Both use AWS's
common rule set and a per-IP rate limit, in **Count mode** first: they record matches as
metrics and block nothing until a later change switches them to Block.

**Paused (user, 2026-11-09):** see Open questions. Earlier decisions (user, 2026-11-09): WAF on both targets; rules = AWS common rule set + rate
limit 1000 requests per IP per 5 minutes; Count first.

## Facts this rests on (checked 2026-11-09)

- WAF can protect CloudFront, API Gateway **REST** APIs, and Cognito user pools, among
  others. It **can't** protect API Gateway HTTP APIs, which this project uses
  (AWS WAF developer guide, "AWS WAF" resource list). The API stays protected by the
  Cognito JWT authorizer and Shield Standard only.
- A web ACL for CloudFront must have scope `CLOUDFRONT` and be created in `us-east-1`.
  This needs a second `aws` provider with `alias = "us_east_1"`. The web ACL holds only
  rules, not data. Logging stays off and `sampled_requests_enabled = false`, so no
  request data is stored outside `ap-southeast-1`.
- WAF on a user pool works on every Cognito feature plan, including `LITE`. It inspects
  managed login and the public API calls (`InitiateAuth`, `RespondToAuthChallenge`,
  ...). Usernames, passwords and emails aren't visible to WAF. One web ACL per user pool,
  associated with `aws_wafv2_web_acl_association`. The account-takeover (ATP) rule group
  isn't allowed and isn't used (Cognito developer guide, "Associate an AWS WAF web ACL
  with a user pool").
- Shield Standard is already on for every account at no cost. Shield Advanced is not used.

## Cost

Not free. AWS WAF pricing: US$5/month per web ACL, US$1/month per rule or managed rule
group, US$0.60 per million requests.

| Item | Per month |
|---|---|
| 2 web ACLs | US$10 |
| 4 rules (2 per ACL) | US$4 |
| Requests (well under 1 million) | ~US$0 |
| **Total** | **~US$14/month**, about US$10 for the rest of November (billed hourly) |

With the EC2 simulator (~US$12) this will probably trip the US$40 budget's 50 % (US$20)
email. That's expected.

## Users and access

| Group | Can see | Can do |
|---|---|---|
| All app users | nothing new while in Count mode | nothing new |
| Developers (shared IAM user) | WAF metrics in CloudWatch (`AWS/WAFV2`, already allowed by `cloudwatch:Get*`) | nothing new |

## Acceptance criteria

Tests read the Terraform as text, like `lambdas/results/tests/test_infra.py`, in
`scripts/tests/test_waf_infra.py`.

1. `infra/providers.tf` has a second provider `aws` with `alias = "us_east_1"`,
   `region = "us-east-1"`, and the same `default_tags`.
2. `infra/waf.tf` defines `aws_wafv2_web_acl` `silvercare-frontend` (scope `CLOUDFRONT`,
   provider `aws.us_east_1`) and `silvercare-users` (scope `REGIONAL`, default provider).
3. Each web ACL has `default_action { allow {} }` and exactly two rules:
   - `aws-common`: managed rule group `AWSManagedRulesCommonRuleSet` (vendor `AWS`), with
     `override_action { count {} }`.
   - `rate-limit`: `rate_based_statement` with `limit = 1000`,
     `evaluation_window_sec = 300`, `aggregate_key_type = "IP"`, `action { count {} }`.
4. No rule uses `AWSManagedRulesATPRuleSet`, and no rule has `block` or `captcha` actions.
5. Every `visibility_config` has `cloudwatch_metrics_enabled = true` and
   `sampled_requests_enabled = false`.
6. `aws_cloudfront_distribution.frontend` sets `web_acl_id` to the frontend ACL's `arn`.
7. `aws_wafv2_web_acl_association` links the users ACL to
   `aws_cognito_user_pool.users.arn`.
8. No `aws_wafv2_web_acl_logging_configuration` exists.
9. `make tf-check` passes (checkov's WAF checks may need justified skips, e.g. the
   CKV2_AWS_31 logging check: logging is off on purpose, see Privacy).

### Go-live checks (owner, after merge)

- The site still loads and sign-in still works (Count mode should change nothing).
- In CloudWatch → Metrics → `AWS/WAFV2`, both ACLs show `AllowedRequests`. Check
  `CountedRequests` for a few days before agreeing to switch to Block.

## Data

- Reads and writes: none (no DynamoDB, S3, or Athena).

## IoT

None. IoT Core isn't a WAF target.

## API contract

None (the HTTP API is unchanged and not covered, see above).

## Screens

None.

## Alerts and notifications

None. No alarms: the 10 free alarms are already used.

## Privacy

- Personal data touched: client IP addresses and request headers are inspected in
  memory by WAF. Not stored: logging off, sampled requests off.
- Cognito usernames, passwords, and emails are never visible to WAF (AWS docs).
- Retention: none (nothing stored). CloudWatch metrics are counts only.

## Out of scope

- Switching to Block (a later one-line change per rule, after the counts are reviewed).
- WAF on the HTTP API (not supported), or moving the API to a REST API.
- WAF logging, CAPTCHA, IP reputation list, bot control, Shield Advanced.
- WAF graphs on the CloudWatch dashboard (`docs/features/cloudwatch-dashboard.md`):
  could be added later; the frontend ACL's metrics are in `us-east-1`.

## Open questions

- **Can this account use WAF with CloudFront?** AWS's page for accounts made with
  "Sign up for AWS (new)" says: "If you use CloudFront, certain features that would
  create resources outside of your selected Region aren't available. This includes using
  AWS WAF with CloudFront" (Accounts reference, "AWS Regions for your projects", checked
  2026-11-09). This account runs in `ap-southeast-1`, which that sign-up type doesn't
  assign to Singapore (it assigns `ap-southeast-2`), so the rule may not apply. The owner
  checks https://settings.aws.com → Project for a selected Region. If the rule applies,
  `apply` of the CloudFront ACL fails on `main` and blocks the deploy: drop the
  CloudFront ACL (Cognito ACL alone, ~US$7/month).
