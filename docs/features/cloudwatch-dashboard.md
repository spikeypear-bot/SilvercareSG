---
status: built          # draft | ready | built
---

# CloudWatch dashboard

## Summary

One CloudWatch dashboard, `silvercare-overview`, in Terraform, with graphs of the
system's health on one page: API Gateway traffic and errors, IoT messages in, Lambda
errors, DynamoDB capacity use and throttling, queue backlogs, and the simulator
instance. It adds no alarms (all 10 free alarms are already used) and no new metrics,
so nothing extra is billed.

## Cost

- CloudWatch always-free tier: 3 custom dashboards referencing up to 50 metrics each per
  month. This is the first custom dashboard. It must reference **at most 50 metrics**.
- Viewing your own dashboard in the console doesn't charge for GetMetricData. Sharing a
  dashboard does, and so do Logs Insights widgets and custom Lambda widgets, so none of
  those are used (AWS CloudWatch pricing page, checked 2026-11-09).
- Only AWS's free default metrics are used. API Gateway detailed (per-route) metrics are
  billed as custom metrics, so they stay off.

## Security note (Shield)

AWS Shield Standard is on automatically for every account at no cost and covers
CloudFront and API Gateway. Shield Advanced (US$3,000/month) is out of budget. Nothing
to build. AWS WAF is a separate feature (`docs/features/waf.md`, paused): WAF can't attach to
an HTTP API, only to CloudFront and the Cognito user pool here (AWS WAF docs, checked
2026-11-09).

## Users and access

No Cognito group uses this feature. The dashboard is in the AWS console.

| Who | Can see | Can do |
|---|---|---|
| Developers (shared IAM user) | the dashboard: already allowed by `cloudwatch:Get*` / `List*` in `docs/iam/developer-policy.json` | nothing new |
| doctor / nurse / caregiver / ops | nothing (no app screen) | nothing |

## Widgets (agreed 2026-11-09)

All graphs use 5-minute periods and the last 3 hours by default. Metric count in brackets.

1. **API Gateway (HTTP API, whole API):** `Count`, `4xx`, `5xx`, `Latency` p95, by `ApiId` (4)
2. **IoT Core** (`AWS/IoT`): `PublishIn.Success` and `Connect.Success` with dimension
   `Protocol=MQTT`, and `RulesExecuted` (no dimension) (3). Names checked in AWS IoT's
   metrics docs 2026-11-09.
3. **Lambda, account totals:** `Invocations`, `Errors`, `Throttles`, `ConcurrentExecutions` (4)
4. **Lambda errors, alert path:** `Errors` for the ingest functions,
   `silvercare-alert-processor`, `silvercare-history`, `silvercare-watchdog` (6)
5. **DynamoDB, per table** (`silvercare-registry`, `-latest`, `-alerts`, `-results`):
   `ConsumedReadCapacityUnits`, `ConsumedWriteCapacityUnits`, `ReadThrottleEvents`,
   `WriteThrottleEvents`, with a horizontal line at each table's provisioned capacity (16)
6. **SQS backlog:** `ApproximateNumberOfMessagesVisible` for `silvercare-alerts`,
   `silvercare-history` and the 3 DLQs (5)
7. **Simulator EC2:** `CPUUtilization`, `StatusCheckFailed` (2)

Total 40, under 50.

## Acceptance criteria

1. `infra/` defines one `aws_cloudwatch_dashboard` named `silvercare-overview` in
   `ap-southeast-1`.
2. Every metric in it comes from Terraform references (API ID, function names, table
   names, queue names, instance ID), not hard-coded IDs.
3. A test (`scripts/tests/test_dashboard_infra.py`, reading the Terraform as text like
   `lambdas/results/tests/test_infra.py`) counts the metrics in the dashboard body and
   fails above 50.
4. The body has no `log` widgets, no `custom` (Lambda) widgets, and no metrics in a
   custom namespace (only `AWS/*`).
5. No new alarms, SNS topics, metric filters, or IAM changes.
6. `make tf-check` passes.

## Data

- Reads: CloudWatch metrics only (counts, durations, capacity). No DynamoDB or S3 reads.
- Writes: none.

## IoT

None (reads IoT Core's built-in metrics only).

## API contract

None.

## Screens

None in the app. The dashboard is in the CloudWatch console.

## Alerts and notifications

None. (Alarms stay as they are.)

## Privacy

- Personal data touched: none. The metrics are aggregate counts with resource names as
  dimensions, no resident or device IDs.
- Retention: CloudWatch's default metric retention (AWS-managed).
- Must not be shown: no log widgets, because Lambda logs could contain IDs.

## Out of scope

- New alarms or alarm changes.
- New simulator scenarios: the user confirmed the 5 existing ones are enough
  (2026-11-09).
- Per-route API Gateway metrics, custom metrics, Logs Insights widgets, sharing the
  dashboard publicly.
- Shield Advanced.
- AWS WAF (its own spec, `docs/features/waf.md`).

## Open questions

None.
