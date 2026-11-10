---
status: built          # draft | ready | built
---

# CloudTrail audit trail

## Summary

Adds one CloudTrail trail that keeps an audit record of the AWS account for a year:
every management event (who changed what: Terraform applies, IAM, Cognito users,
console sign-ins), plus two narrow sets of data events that today leave no record:
writes to the `silvercare-registry` table (threshold changes and their history log,
and hand edits by the shared developer user) and every read and write of the Terraform
state bucket. Today only CloudTrail's free 90-day Event history exists, which has no
data events, so `infra/api.tf` ("tamper-evident only via CloudTrail") and
`infra/bootstrap/main.tf` ("state access is already recorded by CloudTrail management
events") claim records that don't exist. Decided with the owner on 2026-11-10.

## Users and access

There are no app users or screens. Access is to the log files in S3:

| Who | Can see | Can do |
|---|---|---|
| Account owner | all log files | everything (root / admin) |
| Developers (IAM group `silvercare-developers`, shared user) | all log files, read-only, through the existing `ReadProjectBuckets` statement (`s3:Get*`, `s3:List*` on `silvercare-*`): owner's decision 2026-11-10 | nothing: no statement in their policy allows writing to or deleting from the log bucket, changing its settings, or changing the trail |
| CI plan role `silvercare-cicd-plan` | trail and bucket settings (AWS `ReadOnlyAccess`, already attached), for `terraform plan` | nothing |
| CI deploy role `silvercare-cicd` | everything (`AdministratorAccess`, as for every resource) | creates and changes the trail through Terraform |
| CloudTrail service | | writes log and digest files to the bucket, only for this trail (bucket policy, criterion 3) |

`docs/iam/developer-policy.json` needs **no change**: the bucket name starts with
`silvercare-`, so the existing read statement covers it, and no developer statement
allows writing to it.

## Acceptance criteria

All Terraform goes in a new file `infra/cloudtrail.tf` in the root config (CI applies
it, like every other resource). Tests parse the Terraform the way
`scripts/tests/test_dashboard_infra.py` does, and `make tf-check` (fmt, validate,
tflint, checkov) passes.

### Log bucket

1. An S3 bucket `aws_s3_bucket.cloudtrail` named `silvercare-cloudtrail-<account_id>` in `ap-southeast-1`, with
   `force_destroy = false`, `lifecycle { prevent_destroy = true }`, and the repo's
   standard bucket settings as in `infra/history.tf`: public access fully blocked,
   object ownership `BucketOwnerEnforced`, SSE-S3 (AES256) default encryption.
2. A lifecycle rule expires every object (log files and digest files) **365 days**
   after creation (owner's decision 2026-11-10, matching raw telemetry), and aborts
   incomplete multipart uploads after 1 day. Versioning stays off, so an expired file
   is really gone.
3. The bucket policy:
   - allows `cloudtrail.amazonaws.com` `s3:GetBucketAcl` on the bucket and
     `s3:PutObject` on `AWSLogs/<account_id>/*`, both with condition
     `aws:SourceArn` = this trail's ARN (built from the name, to avoid a cycle), and
     the put also requiring `s3:x-amz-acl` = `bucket-owner-full-control`
   - denies every request without TLS (`aws:SecureTransport` = false), as the other
     buckets do.

### Trail

4. One `aws_cloudtrail.audit` named `silvercare-trail`, created in `ap-southeast-1`, with
   `is_multi_region_trail = true` and `include_global_service_events = true` (since
   2021, IAM, STS, and console sign-in events reach a trail outside `us-east-1` only if
   it's multi-Region), `enable_log_file_validation = true`, `enable_logging = true`,
   writing to the bucket from criterion 1 with no prefix, no SNS topic, no CloudWatch
   Logs group, no KMS key, and no Insights. It `depends_on` the bucket policy (the
   trail can't be created before CloudTrail is allowed to write).
5. The trail uses `advanced_event_selector` blocks only (no basic `event_selector`),
   exactly three:
   - **Management events:** `eventCategory` = `Management`; all of them, read and write.
   - **Registry writes:** `eventCategory` = `Data`, `resources.type` =
     `AWS::DynamoDB::Table`, `resources.ARN` equals the registry table's ARN (from
     `module.registry_table.arn`), `readOnly` = `false`.
   - **Terraform state:** `eventCategory` = `Data`, `resources.type` =
     `AWS::S3::Object`, `resources.ARN` starts with
     `arn:aws:s3:::silvercare-tfstate-<account_id>/` (built from the account ID data
     source; the bucket is in `infra/bootstrap/`, so it's referenced by name), reads
     and writes.
   No other table, bucket, or data event type is selected.
6. Tags: none set on the resources; the provider's `default_tags`
   (`infra/providers.tf`: `project = "silvercare"`, `managed_by = "terraform"`) apply.
7. A header comment in `infra/cloudtrail.tf` states: purpose, the three selectors and
   why each exists, retention (365 days), who can read the logs, that non-key DynamoDB
   attribute values are not logged (keys are opaque IDs), the cost (criterion 11), and
   that removing the trail needs two merges (first drop `prevent_destroy`).
8. Every checkov rule the trail or bucket fails is either fixed or skipped with a
   one-line reason in the file's style (`#checkov:skip=<id>:<reason>`), e.g. no KMS
   key (~US$1/month), no CloudWatch Logs (cost; all 10 free alarms are used), no SNS
   topic, no access logging or replication on the log bucket (as for the history
   bucket).

### Fix the two comments

9. `infra/api.tf` (around line 1159): the threshold log is tamper-evident through the
   trail's registry data events (`docs/features/cloudtrail.md`), kept 365 days.
10. `infra/bootstrap/main.tf` line 7, the `CKV_AWS_18` skip reason: state object
   access is recorded by the CloudTrail trail's S3 data events
   (`infra/cloudtrail.tf`), not by management events. Comment only: no bootstrap
   apply is needed for it.
10a. `infra/bootstrap/ci-plan-role.tf`: `cloudtrail:LookupEvents` joins
   `DenyDataReads` (`tf-change` rule: a service that returns data is denied to the
   plan role, which any PR from the repo can use). Event history holds app users'
   sign-in IPs; plans read trails with `GetTrail` / `GetEventSelectors`, never
   `LookupEvents`. The owner applies `infra/bootstrap/` by hand (added during the
   build, 2026-11-10).

### Cost and docs

11. Cost, stated in the header comment (prices from the CloudTrail pricing page,
    checked 2026-11-10; volumes are estimates):
    - management events: first copy to S3 is **free**
    - data events: US$0.10 per 100,000. Registry writes: threshold saves and hand
      edits, well under 10,000 a month. State bucket: about 5 object calls per CI
      plan or apply × up to 40 runs a day × 30 ≈ 6,000 a month. Together < 20,000 a
      month ≈ **US$0.02/month**
    - S3 storage: an unchecked estimate of under 1 GB a year of compressed logs ≈
      < US$0.03/month by the end of the year
    - total ≈ **US$0.05/month**, no fixed fee.
12. `docs/data-model.md`: the new bucket is listed with its purpose and 365-day
    retention under Retention, and "Before any real resident data arrives" gains: add
    read data events on the history and clean buckets, and alarms on the trail, once
    developers no longer share one IAM user.
13. `docs/architecture.drawio` (and its PNG): CloudTrail and its log bucket are added
    to the Monitoring group (`aws-architecture-diagram` skill).

## Data

- Reads: none by the system. People read log files from S3 by hand.
- Writes: CloudTrail writes gzip JSON log files under
  `AWSLogs/<account_id>/CloudTrail/<region>/yyyy/mm/dd/` and digest files under
  `AWSLogs/<account_id>/CloudTrail-Digest/`. Duplicates are not a concern (append
  only).
- New access patterns or tables: none. One new bucket (criterion 1).

## IoT

None. (IoT data-plane calls such as MQTT publishes are data events and are not
selected.)

## API contract

None.

## Screens

None.

## Alerts and notifications

None. No alarm on the trail itself (stopped logging, root sign-in): all 10 free
CloudWatch alarms are used and CloudWatch Logs isn't set up; listed for later in
`docs/data-model.md` (criterion 12).

## Privacy

- Personal data touched (checked against the AWS docs, 2026-11-10):
  - **Cognito user-pool calls** are management events and include end users' sign-ins:
    each event has the user's Cognito `sub` and **source IP address**, but not their
    username or email (Cognito records `UserSub`, not `UserName`, and hides passwords
    and tokens). So the trail holds caregiver, nurse, doctor, and ops users' sign-in IP
    addresses for a year, readable by developers (accepted by the owner, 2026-11-10).
  - **Registry data events** record the item **key** and attribute **names**, never
    non-key values (classic API; PartiQL values are redacted). Registry keys are
    opaque IDs (`RESIDENT#<resident_id>`, `USER#<sub>`, `DEVICE#<id>`,
    `THRESHOLDS#…`, `THRESHOLD_LOG#…`): no names, addresses, or health values.
  - IAM identities (the shared developer user, CI roles, Lambda roles) and their IPs.
- Retention: 365 days, then deleted by the lifecycle rule (criterion 2).
- Erasure: single events can't be removed from a log file without breaking log file
  validation, so one person's events (their `sub` and IPs) are erased by the 365-day
  expiry, not on request. To note in the PDPA retention notes.
- Must not be logged or shown: nothing new is logged by the app; no log content is
  printed in CI (the plan summary lists only resource addresses).

## Go-live checks (owner, real AWS)

Not tests: they need the real account.

1. The PR's plan comment shows only creates for `aws_cloudtrail.audit` and the log
   bucket's resources (bucket, public access block, ownership, encryption, lifecycle,
   policy): no update, replace, or destroy rows.
2. After the merge, CloudTrail console → Trails shows `silvercare-trail` as
   multi-Region and logging. If the apply fails on the multi-Region setting (this
   account's free plan limits Regions), stop and tell Claude; don't switch it to
   single-Region, which would lose IAM and sign-in events.
3. Within about 15 minutes, `AWSLogs/<account_id>/CloudTrail/ap-southeast-1/` and
   `AWSLogs/<account_id>/CloudTrail/us-east-1/` both have files (the second proves
   global events arrive).
4. Save one threshold change in the doctor dashboard; within about 15 minutes a log
   file has a `TransactWriteItems` (or `UpdateItem`) event on `silvercare-registry`.
5. After the next CI run, a log file has `GetObject` events on the state bucket.
6. A week later, check the bucket size (S3 console → Metrics) against the storage
   estimate in criterion 11.

## Out of scope

- Read data events on any table or bucket, Lambda invoke events, IoT data events.
- CloudWatch Logs delivery, metric filters, alarms, SNS notifications, Insights,
  CloudTrail Lake.
- A KMS key for the logs, Object Lock, a separate audit account.
- Telling developers apart (they share one IAM user: see "Before any real resident
  data arrives").
- Athena tables over the logs.

## Open questions

-
