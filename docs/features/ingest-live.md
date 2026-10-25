---
status: built          # draft | ready | built
---

# Ingest: live path

## Summary

Route every device message from AWS IoT Core to three small Lambdas that validate it and
store it in `silvercare-latest` (the newest 5 messages per device and message type), so
the dashboards built later have live readings to show. This is path 1 of
`docs/decisions/0001-iot-rule-routing.md`. Builds on `iot-core`. Nothing is alerted yet,
and nothing goes to S3 yet: the history path is the next feature, `ingest-history`,
which adds a second action to the rules created here.

## Users and access

No Cognito group uses this feature. There is no API and no screen.

| Group | Can see | Can do |
|---|---|---|
| staff | nothing | nothing |
| caregiver | nothing | nothing |
| family | nothing | nothing |

How access to a specific resident is decided: not applicable. The Lambdas don't read
the registry; `silvercare-latest` is keyed by device, not resident.

Who does what:

| Who | Does |
|---|---|
| Devices (simulated) | Publish to `silvercare/v1/<own id>/<message>` |
| IoT rules | Add `device_id`, `message`, `received_at`, invoke the matching Lambda |
| Lambdas | Validate, add the message to the device's latest 5 |
| CI (`deploy.yml`) | Applies the Terraform |
| Developer | Runs the smoke test and the ingest check against real AWS |
| Claude and its agents | Build the code. No AWS access |

## Acceptance criteria

### Code layout

1. All three functions live in one folder, `lambdas/ingest/`, and share code:
   - `src/handler.py` has three entry points: `device_health_handler`,
     `telemetry_handler`, `fall_alert_handler`
   - shared modules in `src/` for validation and the write rule
   - tests in `lambdas/ingest/tests/`

   Terraform zips `lambdas/ingest/src/` once and creates three separate functions from
   it, so each has its own role, log group, concurrency, and failure handling.

### IoT rules (Terraform)

2. Three topic rules, SQL version `2016-03-23`, exactly as in
   `docs/decisions/0001-iot-rule-routing.md`, each with one more condition in `WHERE`
   that drops a message whose payload already has a `device_id`, `message`, or
   `received_at` field, so a device can't pretend to be another device:

   | Rule name | FROM | WHERE | Action |
   |---|---|---|---|
   | `silvercare_health` | `silvercare/v1/+/status` | reserved fields undefined | Lambda `silvercare-device-health` |
   | `silvercare_telemetry` | `silvercare/v1/+/+` | `topic(4)` is `vitals`, `motion`, or `bed`, and reserved fields undefined | Lambda `silvercare-telemetry` |
   | `silvercare_alert` | `silvercare/v1/+/fall` | reserved fields undefined | Lambda `silvercare-fall-alert` |

   SELECT for all three: `*, topic(3) AS device_id, topic(4) AS message, timestamp() AS
   received_at`. "Reserved fields undefined" is
   `isUndefined(device_id) AND isUndefined(message) AND isUndefined(received_at)`.
3. No rule has an `error_action`, and there is no failed-message queue (user's
   decision, 2026-10-25): failures are visible only in the Lambda logs, and the raw
   message is still kept in S3 history (`ingest-history`).
4. Each Lambda has an `aws_lambda_permission` allowing `iot.amazonaws.com` to invoke it,
   limited by `source_arn` to its own rule and `source_account` to the account.

5. *(Removed: failed-message queue.)*
6. *(Removed: its output.)*

### Lambdas (Terraform)

7. Three functions, `silvercare-device-health`, `silvercare-telemetry`,
   `silvercare-fall-alert`: runtime `python3.13`, architecture `arm64`, 128 MB, timeout
   10 s, the public AWS Lambda Powertools (v3, Python 3.13, arm64) layer at a pinned
   version, X-Ray tracing off. Environment: `LATEST_TABLE` (table name) and
   `POWERTOOLS_SERVICE_NAME`.
8. Least privilege (follow the `iam-policy` skill). Each function has its **own** IAM
   role, trusted only by `lambda.amazonaws.com` (no conditions: AWS documents the
   execution role trust policy without them, and an unsupported one would make creating
   the function fail), with one inline policy allowing exactly:
   - `dynamodb:GetItem` and `dynamodb:PutItem` on the `silvercare-latest` table ARN
     only (not its indexes, not other tables), with condition `ForAllValues:StringLike`
     `dynamodb:LeadingKeys` = `DEVICE#*`
   - `logs:CreateLogStream` and `logs:PutLogEvents` on its own log group
     (`.../log-group:/aws/lambda/<function-name>:*`) only

   Nothing else: no AWS managed policies (in particular not
   `AWSLambdaBasicExecutionRole`, which allows logs on every log group), no
   `logs:CreateLogGroup` (Terraform creates the group), no `UpdateItem`,
   `DeleteItem`, `Query`, `Scan`, or batch actions, no `*` in actions, and no `*`
   resources. No role is shared between functions.
8a. The IoT rule role `silvercare-iot-rules` is created by `ingest-history` for the
    history action. Invoking the Lambdas is granted by the per-function
    `aws_lambda_permission` (criterion 4), not by a role.
9. Each function has a log group `/aws/lambda/<function-name>` with 30-day retention.
10. No async invoke config: Lambda's defaults apply (2 automatic retries, then the
    event is dropped and logged as an error).
11. boto3 clients use the `adaptive` retry mode.

### Validation (shared)

12. Given an event from a rule, the Lambda checks it against `docs/metrics.md`. It
    rejects the message, if any of these is true:
    - `device_id` doesn't match `^(wearable|bedroom)-[0-9]{4}$`
    - `message` isn't allowed for the device type from the `device_id` prefix
      (wearable: `vitals`, `fall`, `status`; bedroom: `motion`, `bed`, `status`)
    - `schema_version` isn't the integer `1`
    - `message_id` isn't a UUID v4 string
    - `ts` doesn't match `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$` or isn't a real
      date and time
    - a required field for the message type is missing, has the wrong type, or is out
      of its valid range (tables in `docs/metrics.md`). Integer fields don't accept
      booleans or decimals; number fields accept integers and decimals but not
      booleans; nullable fields accept `null`.
    - `accel_g` (vitals) or `accel_window` (fall) is missing or malformed: they're
      validated even though they aren't stored
13. Unknown extra fields are ignored and not stored.
14. A rejected message: the Lambda logs one line with `device_id`, `message`,
    `message_id` (if it's a string), and a short reason code (e.g. `out_of_range:hr_bpm`),
    never field values, then returns without raising, so it isn't retried.
15. `received_at` (epoch milliseconds from the rule) is stored as ISO 8601 UTC with
    milliseconds and `Z`. `clock_skew` is `true` when `ts` and `received_at` are more
    than 5 minutes apart in either direction.

### Write rule (shared)

16. Valid messages are added to the item `pk = DEVICE#<device_id>`,
    `sk = MSG#<message>` in `silvercare-latest` exactly as described under
    "Write rule (idempotent)" in `docs/data-model.md`:
    - strongly consistent GetItem
    - same `message_id` already in `entries` → do nothing (duplicate)
    - insert, sort by `ts` newest first, keep 5; older than all 5 when there are
      already 5 → do nothing
    - PutItem with `attribute_not_exists(pk) OR version = :read_version`, `version + 1`
    - on a failed condition, re-read and retry, at most 3 attempts in total, then raise
      (so Lambda's async retry tries again)
17. The item has the attributes in the `silvercare-latest` table in
    `docs/data-model.md`: `pk`, `sk`, `device_type`, `entries`, `latest` (copy of
    `entries[0]`), `latest_ts`, `version`, and `watch_type` (equal to the message type)
    on `vitals`, `status`, `motion`, and `bed` items only, never on `fall`.
18. Each entry holds `message_id`, `ts`, `received_at`, `clock_skew`, plus only the
    allow-listed fields for its type from "Stored fields per type" in
    `docs/data-model.md`. `accel_g` and `accel_window` are never stored. Decimal values
    are written as DynamoDB numbers without float rounding errors (e.g. `33.4` stays
    `33.4`).
19. Each stored, duplicate, or too-old message logs one line with `device_id`,
    `message`, `message_id`, and the outcome (`stored`, `duplicate`, `too_old`). No
    health values, no payloads.
20. Throttling or other transient AWS errors are raised, not swallowed.

### Function behaviour

21. `device-health` stores `status` messages; `telemetry` stores `vitals`, `motion`,
    and `bed`; `fall-alert` stores `fall`. Each rejects a message type it doesn't
    handle (reason `wrong_function`).
22. No function reads `silvercare-registry`, compares thresholds, or raises alerts.

### Contract update

23. `docs/metrics.md` "Envelope" gains: payloads must not contain `device_id`,
    `message`, or `received_at`; the IoT rules add these, and a message that already
    has one is dropped by the rule.

### Tests

24. Unit tests in `lambdas/ingest/tests/` (moto for DynamoDB) cover: every rejection
    reason in criterion 12, including booleans in integer fields; each message type
    stored with only its allow-listed fields; duplicates; out-of-order messages
    (newer and older than the stored 5); the 6th message dropping the oldest; clock
    skew both ways; the `received_at` conversion; `watch_type` present or absent;
    `wrong_function`; a condition failure that succeeds on retry and one that fails 3
    times and raises; log lines containing no health values.
25. An integration test (`@pytest.mark.integration`, DynamoDB Local) runs two writers
    against the same item at once and checks that both messages end up stored once.

### Smoke test and ingest check (real AWS, the developer runs them)

26. `scripts/iot-smoke-test.py` gets `--ingest`. After the existing checks it publishes,
    as the device, one valid synthetic message of each type the device may send, then
    the first of them again (a duplicate), then one invalid message (`hr_bpm: 999` for
    a wearable, `occupied: "yes"` for a bedroom sensor). It prints the message IDs it
    sent and the command for the next step. It needs no AWS credentials, so it still
    runs in the dev container.
27. `make smoke-test DEVICE=<id> ENDPOINT=<host> INGEST=1` passes `--ingest`. (The
    `Makefile` line is added by the main Claude session, not the builders, who can't
    edit the `Makefile`.)
28. `scripts/ingest-check.py --device <id> --message-ids <ids>`, run on the
    developer's machine with their own AWS credentials, region `ap-southeast-1`, reads
    the device's `silvercare-latest` items (GetItem, the developer policy already
    allows it) for up to 30 s. It prints `PASS` when every valid message ID appears
    exactly once and the invalid one doesn't, otherwise `FAIL` with which check failed.
    It never prints field values. It uses `boto3`, pinned in `scripts/requirements.txt`
    (that file is created by the main Claude session, not the builders).
29. Unit tests in `scripts/tests/` (run by `make test`) cover the new smoke test
    payloads and the ingest check's pass/fail logic, with no network (botocore
    `Stubber` or moto).

### Docs

30. `README.md` "IoT devices" section gets "Checking ingest": install
    `scripts/requirements.txt` on the host, run the smoke test with `INGEST=1`, then
    the ingest check with the printed command. (Written by the main Claude session
    after the build; builders can't edit `README.md`.)

## Data

- Reads: `silvercare-latest` item for the device and message type (write rule step 1,
  pattern 3).
- Writes: the same item, PutItem with the version condition (pattern 3). Duplicates are
  dropped by `message_id`; concurrent writes are resolved by the version check.
- New access patterns or tables: none. The registry and alerts tables aren't touched.
- Capacity: within the existing `silvercare-latest` and `by-message` capacity
  (`docs/data-model.md`, Capacity). No change.

## IoT

- Topics: all six `silvercare/v1/<device_id>/<message>` topics in `docs/metrics.md`.
- New: three topic rules. No device policy changes.
- Contract change: reserved payload fields (criterion 23).

## API contract

None.

## Screens

None.

## Alerts and notifications

None. A `fall` is stored in `silvercare-latest` but **nobody is notified**. Fall alerts
come with the alerting feature (Open decision #2). All data is synthetic until then.

## Privacy

- Personal data touched: health readings (heart rate, SpO2, skin temperature,
  activity), falls, bed occupancy, and motion, linked to a device ID. The device ID is
  linked to a resident only in the registry, which this feature doesn't read.
- Retention: the newest 5 per device and message type (write rule); Lambda logs 30
  days (IDs and outcomes only). No failed-message queue, so no payloads are kept
  outside DynamoDB and S3 history.
- Must not be logged or shown: payloads and any field values. Logs hold only
  `device_id`, `message`, `message_id`, outcome, and reason codes.

## Cost

US prices from memory; check `ap-southeast-1` under Billing → Free Tier. At ~1.3M
messages/month with the simulator running 24/7:

- IoT Core messaging and connectivity: ~US$1.45 (paid whatever happens to messages)
- IoT rules: 1.3M triggers + 1.3M actions, ~US$0.40
- Lambda: 1.3M requests (1M free) ~US$0.06; compute inside the free tier
- DynamoDB: inside the provisioned free tier, US$0
- CloudWatch Logs: ~1 GB/month, inside the 5 GB free tier

About **US$2/month** in total, US$0 when the simulator is stopped. No fixed fees.

## Out of scope

- The history path to S3 (next feature: `ingest-history`).
- The alerts queue, `alert-processor`, SNS, the watchdog, threshold checks, and
  registry lookups.
- Reserved concurrency for `fall-alert` (needs the account's Lambda concurrency limit
  checked first).
- CloudWatch alarms on the failed-message queue.
- The device simulator.

## Open questions

None.
