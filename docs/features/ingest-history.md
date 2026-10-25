---
status: ready          # draft | ready | built
---

# Ingest: history path

## Summary

Keep every device message in S3 for later analysis with Lake Formation and Athena. Each
IoT rule from `ingest-live` gets a second action that sends the message to an SQS queue,
and a `history` Lambda writes the queue to S3 in batches of up to 5 minutes, as gzipped
JSON Lines files split by message type and day. This is the History path in
`docs/decisions/0001-iot-rule-routing.md`. It replaces Firehose and Kinesis, which need
AWS's paid plan. Built on the same branch as `ingest-live` and shipped in the same PR.

## Users and access

No Cognito group uses this feature. There is no API and no screen.

| Group | Can see | Can do |
|---|---|---|
| staff | nothing | nothing |
| caregiver | nothing | nothing |
| family | nothing | nothing |

How access to a specific resident is decided: not applicable. History files hold device
IDs, not residents.

Who does what:

| Who | Does |
|---|---|
| IoT rules | Send every message they match to the history queue (second action) |
| `history` Lambda | Reads the queue in batches, writes files to S3 |
| Developers (shared IAM user) | Read history files (the existing `ReadProjectBuckets` statement covers `silvercare-*`); can't write or delete them |
| Developer | Runs the ingest check with `--history` after a deploy |
| CI (`deploy.yml`) | Applies the Terraform |
| Claude and its agents | Build the code. No AWS access |

## Acceptance criteria

### Queues (Terraform)

1. SQS standard queue `silvercare-history`: SSE-SQS encryption, visibility timeout
   660 s (6 × the 60 s Lambda timeout + the 300 s batching window), message retention 4 days (345,600 s), redrive
   policy to `silvercare-history-dlq` after 5 receives, a queue policy that denies
   requests without TLS.
2. SQS standard queue `silvercare-history-dlq`: SSE-SQS, message retention 14 days
   (1,209,600 s), a redrive allow policy that accepts only `silvercare-history` as a
   source, a queue policy that denies requests without TLS.
3. Outputs `history_queue_url` and `history_dlq_url`.

### IoT rules (change to `ingest-live`)

4. Each of the three rules (`silvercare_health`, `silvercare_telemetry`,
   `silvercare_alert`) gets a second action: `sqs` to `silvercare-history`, using the
   `silvercare-iot-rules` role (created here, trusted only by `iot.amazonaws.com`
   with `aws:SourceAccount` and `aws:SourceArn` on `rule/silvercare_*`),
   `use_base64 = false`. SQL and the Lambda action don't change.
5. The `silvercare-iot-rules` policy allows only `sqs:SendMessage` on
   `silvercare-history`.

### Bucket (Terraform)

6. S3 bucket `silvercare-history-<account_id>` in `ap-southeast-1`:
   - all public access blocked, object ownership `BucketOwnerEnforced`
   - SSE-S3 encryption (AWS-managed key)
   - bucket policy denies any request without TLS (`aws:SecureTransport = false`)
   - versioning **off** (user's decision: a delete really deletes, e.g. for a PDPA
     erasure request)
   - one lifecycle rule only: abort incomplete multipart uploads after 1 day. No
     expiry rule: how long history is kept is Open decision #4.
   - `force_destroy = false` and `lifecycle { prevent_destroy = true }`
7. Every checkov skip on the bucket (e.g. versioning, access logging, cross-region
   replication, event notifications, KMS key) has a comment with the reason (user's
   decision, cost, data stays in Singapore, nothing consumes events).
8. Output `history_bucket_name`.

### Lambda (Terraform)

9. Function `silvercare-history`, built from `lambdas/history/src/` (its own folder:
   it shares no code with `lambdas/ingest/`), handler `handler.lambda_handler`, through
   the existing `infra/modules/python-lambda` module: python3.13, arm64, 256 MB,
   timeout 60 s, the same pinned Powertools layer as `ingest-live`, tracing off, log
   group retention 30 days. Environment: `HISTORY_BUCKET` (bucket name) and
   `POWERTOOLS_SERVICE_NAME`.
10. The module's async invoke config becomes optional: with no on-failure destination
    given, it creates no `aws_lambda_function_event_invoke_config`. The three
    `ingest-live` functions keep theirs unchanged (no change in their plan).
11. Event source mapping from `silvercare-history`: `batch_size = 1000`,
    `maximum_batching_window_in_seconds = 300`,
    `function_response_types = ["ReportBatchItemFailures"]`, enabled.
12. Least privilege (follow the `iam-policy` skill). Its own role, trusted only by
    `lambda.amazonaws.com` with no conditions (as in `ingest-live`), one inline policy
    allowing exactly:
    - `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes`,
      `sqs:ChangeMessageVisibility` on the `silvercare-history` queue ARN only
    - `s3:PutObject` on `arn:aws:s3:::silvercare-history-<account_id>/telemetry/*`
      only (not `alerts/`, not the bucket itself, no read, no delete)
    - `logs:CreateLogStream` and `logs:PutLogEvents` on its own log group only

    No managed policies, no `*` in actions or resources, no DynamoDB access.

### Lambda behaviour

13. For each SQS batch, the Lambda groups records by message type and UTC date and
    writes **one** object per group to
    `telemetry/message=<message>/date=<yyyy-mm-dd>/<yyyymmddTHHMMSSZ>-<uuid4>.jsonl.gz`,
    where the timestamp is the time of writing (UTC) and the UUID is random.
14. Per record:
    - the body is parsed as JSON (the rule's output: payload plus `device_id`,
      `message`, `received_at`)
    - `<message>` is the body's `message` if it matches `^[a-z]{1,16}$`, otherwise
      `unknown`
    - the date comes from `received_at` (epoch milliseconds) if it is a non-negative
      integer, otherwise from the record's SQS `SentTimestamp` attribute
    - a body that isn't a JSON object goes to `message=unparseable` as
      `{"raw_body": "<body as a string>"}`
15. Each file is gzip-compressed JSON Lines: one compact JSON object per line (the
    parsed body re-serialized with no extra whitespace), in the order received, UTF-8,
    ending with a newline. Messages are stored as received: invalid messages are kept
    too (cleaning happens later), and nothing is added or removed.
16. `PutObject` uses `ContentType = "application/gzip"` and no `ContentEncoding`.
17. If writing a group's object fails, the Lambda returns that group's records as
    `batchItemFailures` (by `messageId`) and the other groups count as done. If every
    group fails, all records are returned. It never raises for a record it can't
    parse (criterion 14 handles those).
18. Logs: one line per batch with the record count, and one line per object with the
    message type, date, record count, and object key. No bodies, field values, device
    IDs, or message IDs.
19. A redelivered batch may store a message twice. That is accepted: the Lake Formation
    cleaning step removes duplicates by `message_id` (recorded in the decision).

### Ingest check

20. `scripts/ingest-check.py` gets `--history`. After the existing `silvercare-latest`
    check, it looks for the smoke test's messages in S3 for up to 7 minutes: it lists
    objects under `telemetry/message=<type>/date=<today and yesterday UTC>/` for each
    message type the device sends, modified since the check started minus 15 minutes,
    downloads and decompresses them, and looks for the message IDs. `PASS` when every
    valid **and** invalid message ID appears at least once (history keeps invalid
    messages and duplicates); otherwise `FAIL` naming the missing IDs. It never prints
    file contents or field values.
21. The bucket name defaults to `silvercare-history-<account_id>`, with the account ID
    from `sts:GetCallerIdentity`, and can be overridden with `--bucket`. The developer
    policy already allows the reads (`ReadProjectBuckets`); no IAM change.
22. `scripts/iot-smoke-test.py --ingest` also prints the `--history` variant of the
    check command.

### Docs

23. `docs/data-model.md` gets a short "S3 history" section: bucket, key layout, file
    format, who writes and reads it, and a pointer to the decision's History path.
24. After the build, the main Claude session adds `--history` to README "Checking
    ingest" (builders can't edit `README.md`).

### Tests

25. Unit tests in `lambdas/history/tests/` (moto for S3) cover: grouping by type and
    date, including a batch across UTC midnight; the key format; gzip and JSON Lines
    content round-trip; `unknown` and `unparseable` handling; the `SentTimestamp`
    fallback; partial failure returning only the failed group's message IDs; all
    groups failing; an empty batch; log lines with no bodies or IDs.
26. Unit tests in `scripts/tests/` cover `--history`: found, missing, invalid IDs
    required, the bucket name default and override, with no network (moto or botocore
    `Stubber`).

## Data

- Reads: none from DynamoDB.
- Writes: S3 objects in `silvercare-history-<account_id>/telemetry/` (new). Duplicates
  are possible on redelivery and removed later by `message_id`.
- New access patterns or tables: none. New bucket: documented in `docs/data-model.md`
  (criterion 23).

## IoT

- Topics: all six, through the three existing rules.
- Change: a second (SQS) action on each rule. No SQL or device policy changes.

## API contract

None.

## Screens

None.

## Alerts and notifications

None. Messages that keep failing land in `silvercare-history-dlq`; there is no alarm
(out of scope).

## Privacy

- Personal data touched: **every** raw message, including health readings, falls, bed
  and motion events, and the raw acceleration data (`accel_g`, `accel_window`) that
  `silvercare-latest` leaves out, linked to device IDs. Invalid messages are kept too.
- Retention: S3 history has no expiry until Open decision #4 is decided; the history
  queue keeps messages up to 4 days (normally under 5 minutes); the DLQ 14 days; Lambda
  logs 30 days (counts and keys only).
- Access: the `history` Lambda can only add files under `telemetry/`. Developers can
  read but not write or delete. Versioning is off, so a deletion (by the account owner)
  is final.
- Must not be logged or shown: message bodies, field values, device IDs, message IDs.

## Cost

US prices from memory; check `ap-southeast-1` under Billing → Free Tier. At ~1.3M
messages/month with the simulator running 24/7:

- IoT rule actions: 1.3M more actions, ~US$0.20
- SQS: ~1.3M sends plus Lambda polling, a little over the 1M free requests, ~US$0.50
- S3: ~43,000 PUTs (one file per type per 5 min), ~US$0.25; storage well under
  1 GB/month, cents
- Lambda: ~8,600 invocations, inside the free tier

About **US$1/month**, US$0 when the simulator is stopped. No fixed fees.

## Out of scope

- Writing alerts to `alerts/` (comes with `alert-processor`).
- Lake Formation, the Glue catalog, Athena tables, and deduplication.
- A retention or expiry rule (Open decision #4).
- CloudWatch alarms on the DLQ.
- Replaying DLQ messages (can be done by hand in the SQS console with "redrive").

## Open questions

None.
