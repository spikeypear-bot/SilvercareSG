---
status: ready          # draft | ready | built
---

# Clean data pipeline

## Summary

A daily job turns the previous UTC day's raw telemetry in S3 into clean, de-duplicated
Parquet files, one table per message type, plus a table of rejected messages with the
reason for each. Tables are in the Glue Data Catalog and access is governed by Lake
Formation. This is the first layer of the data lake (raw → clean → curated); analysts
and later features read from it. Raw files are never changed.

## Approaches considered

| Approach | Trade-off | Outcome |
|---|---|---|
| Daily Lambda writing `.jsonl.gz` in Python | Easiest to test, no new services, but no Parquet, so later searches scan more | Not chosen |
| **Daily Lambda starting Athena SQL that writes Parquet** | Faster, cheaper queries later, no new Python packages; SQL can't run locally, and validation rules exist in both Python and SQL | **Chosen by the user** |
| Glue Spark job | Handles far more data, but is much more than ~42,000 messages a day needs, costs more, and is harder to test locally | Not chosen |

## Decisions

Made by the user (2026-11-04):

- Athena SQL approach, with Lake Formation included.
- All telemetry types; alerts are not part of this feature.
- Invalid messages are kept in a rejected table, with a reason.
- Clean data identifies devices only (`device_id`). Linking to residents happens in the
  later curated layer.
- New bucket, separate from the raw history bucket.
- The shared developer user can query clean tables through Athena, but not `rejected` or
  `raw_telemetry`.
- A failed run emails the existing `silvercare-ops` topic.
- A message whose accelerometer data is malformed stays in the clean table, marked with
  `accel_valid = false`. This differs from ingest on purpose. Accelerometer columns hold
  only known fields rebuilt from valid data, otherwise null.
- `silvercare-cicd-plan` is a Lake Formation read-only admin so PR plans keep working, and
  is denied `lakeformation:GetDataAccess`.
- The history Lambda's message pattern is fixed to allow underscores, and the clean job
  also sorts lines already stored under `unknown` by their `message` field. Raw files
  aren't changed.

Builder-level defaults chosen by Claude, for the user to check:

- Runs at 00:30 UTC (08:30 Singapore time).
- Partitions are added with `ALTER TABLE ADD IF NOT EXISTS PARTITION`, not partition
  projection, because the AWS docs read don't confirm projection works with Lake
  Formation.
- `accel_g` and `accel_window` are stored as JSON strings of known fields.
- The self-check runs after every deploy.
- Only the owner runs re-runs and backfills, because developers can't invoke Lambdas.
- Per-device erasure isn't built here: it must exist before any real resident data.
- Athena results are kept for 7 days.
- A query scan limit of 1 GB per query as a cost guard.
- A failed run retries twice, and the alarm fires once all three attempts have failed.

## Users and access

No Cognito group gets access: this feature has no API or screen.

| Who | Can see | Can do |
|---|---|---|
| doctor, nurse, caregiver, ops | nothing | nothing |
| `silvercare-clean` Lambda role | raw telemetry; clean tables | delete and write objects in the clean bucket under `tables/` only; run queries in its own workgroup |
| Developers (shared IAM user) | Athena: the eight clean tables only. S3: clean, rejected, and raw files, through the existing `ReadProjectBuckets` statement | run queries in the `silvercare-analysis` workgroup; no write or delete on clean or raw data |

How access to a specific resident is decided: not applicable. Clean tables hold device
IDs, not residents.

Lake Formation controls access through Athena and the catalog. It does not limit direct
S3 reads, so the developers' `ReadProjectBuckets` statement still allows reading the
files, including `rejected`. That matches today's rule while all data is synthetic;
review it with the shared user before any real resident data arrives.

## Acceptance criteria

### Schedule and input

1. An EventBridge rule invokes `silvercare-clean` daily at `cron(30 0 * * ? *)` with no
   date. The job then processes the previous UTC date.
2. Invoked with `{"date": "yyyy-mm-dd"}`, the job processes that date. A value that
   isn't a real date in exactly that form fails before any AWS call, so no text reaches
   the SQL.
3. Running the job again for a date replaces that date's output: for every table it
   deletes the objects under the date's folder, then writes again. Output never holds
   two copies of a run. This also handles leftovers from a failed run, which Athena
   doesn't remove.
4. A day with no raw data for a message type succeeds and leaves that date's folder
   empty.
5. If any query doesn't succeed, the job logs its query ID and final state and raises,
   so the invocation fails. EventBridge's async retry (2 retries) then applies.

### Raw table

6. The Glue database `silvercare_lake` has `raw_telemetry` with a single `line string`
   column (text input format, gzip read automatically), partitioned by `message` and
   `date`, located at `s3://silvercare-history-<account_id>/telemetry/`. One string
   column means a wrong type in one message can't fail the whole query.
6a. `lambdas/history/src/handler.py` and its documented key layout accept message names
   matching `[a-z_]{1,16}`, so new `vitals_alert` and `alert_cancel` files use their own
   folders. Existing raw files aren't moved or edited.
6b. A line under `unknown` whose `message` field names a type in the contract is processed
   as that type, for that date. Any other line under `unknown` is rejected as
   `unknown_message`.
7. Before querying, the job adds the partitions for the date for `vitals`,
   `vitals_alert`, `fall`, `alert_cancel`, `status`, `motion`, `bed`, `unknown`, and
   `unparseable`. The IoT rules only pass the seven named types, and the history Lambda
   produces the other two.

### Clean tables

8. There is one Parquet (Snappy) table per message type: `vitals`, `vitals_alert`,
   `fall`, `alert_cancel`, `wearable_status`, `bedroom_status`, `motion`, `bed`,
   partitioned by `date` (the raw file's `date`, from `received_at`), at
   `s3://silvercare-clean-<account_id>/tables/<table>/date=<yyyy-mm-dd>/`. `status`
   splits into two tables by device type, because their fields differ.
9. Every clean table has `message_id` (string), `device_id` (string), `ts` (timestamp),
   `received_at` (timestamp), `schema_version` (int), `clock_skew` (boolean), and its
   message's fields from `docs/metrics.md`, using the types there. Unknown fields are
   dropped. Exceptions: `accel_g` in `vitals` and `accel_window` in `fall` are stored as
   JSON strings rebuilt from known fields, each with an `accel_valid` boolean. When
   `accel_valid` is false, the JSON column is null. Extra nested fields are never stored.
   - `vitals`: `accel_valid` is true when `accel_g` is an object whose `x`, `y`, and `z`
     are numbers from −16 to 16. Stored as `{"x":…,"y":…,"z":…}`.
   - `fall`: `accel_valid` is true when `accel_window` is an object with `hz` 25,
     `start_offset_ms` −1000, and a `samples` array of 50 items, each an array of 3 numbers
     from −16 to 16. Stored with only those three fields.
10. `clock_skew` is true when `ts` is more than 5 minutes from `received_at`
    (`docs/metrics.md`). Those messages stay in the clean table.
11. A message appears in a clean table only if `validate_event` in
    `lambdas/ingest/src/validation.py` would accept it, ignoring its accelerometer checks
    (`_check_accel_g`, `_check_accel_window`). This includes `null` readings and `hr_bpm`
    0 in `vitals_alert` (no pulse). An emergency reading must never be rejected. A message
    failing only an accelerometer check stays, with `accel_valid` false.
12. Duplicates: within a table and date, the row with the earliest `received_at` is kept
    for each `message_id`. Ties may keep either row. A duplicate that lands on another
    date isn't removed (a known limit: only QoS 1 resends across midnight can cause it).

### Rejected table

13. Every raw line that fails validation, every line under `unparseable`, and every line
    under `unknown` that isn't recovered by criterion 6b is in `rejected`, located at
    `s3://silvercare-clean-<account_id>/tables/rejected/date=<yyyy-mm-dd>/`, with
    `message` (the raw partition), `reason` (string), `raw_line` (string), and `date`.
    Exact duplicate lines are kept once. Duplicates of valid messages aren't rejected.
14. `reason` uses the same strings as `Rejection` in `validation.py` (for example
    `invalid:ts`, `missing:message_id`), plus `unknown_message` and `unparseable`. When a
    message has several problems, the reason may differ from the Python one, but it must
    name a real problem with that line.

### Validation stays in step with ingest

15. `lambdas/clean/src/cases.json` holds synthetic lines with `expected_ingest` (valid or
    rejected), `expected_clean` (valid or rejected), and, where valid, expected values.
    They differ only for accelerometer-only failures. It covers every message
    type, each rule family, `null` readings, `hr_bpm` 0, wrong JSON types (including a
    number as a string, and booleans), an out-of-range value, malformed `accel_g` and
    `accel_window`, extra fields on `alert_cancel`, and duplicates.
16. A unit test runs every case through ingest's `validate_event` and checks
    `expected_ingest`, so a rule change in ingest fails that test until `cases.json` and
    the SQL are updated.
17. Invoked with `{"self_check": true}`, the job runs the real SQL in Athena on the cases
    as inline `VALUES`, without reading or writing bucket data, and raises unless every
    `expected_clean` outcome matches. Synthetic data only. `deploy.yml` invokes this after
    `terraform apply` and fails the workflow if it fails, printing only pass or fail. A
    failure doesn't undo the apply.

### Lake Formation and Athena

18. Lake Formation data lake settings: `silvercare-cicd` (looked up by name) is the admin,
    `silvercare-cicd-plan` (looked up by name) is the only read-only admin, and new databases and tables get no `IAMAllowedPrincipals` permissions.
19. `s3://silvercare-history-<account_id>/telemetry/` and
    `s3://silvercare-clean-<account_id>/tables/` are registered with Lake Formation using
    a user-defined role, `silvercare-lake-location`, trusted by
    `lakeformation.amazonaws.com`. It has read on the raw location and read, write, and
    delete on the clean location. `alerts/` isn't registered. Read-only on raw must not be
    widened, even if a later check fails: that is a decision for the owner.
20. The `silvercare-clean` role gets the Lake Formation permissions it needs on
    `silvercare_lake` and its tables and nothing wider, including `DESCRIBE` on the
    `default` database, which AWS requires for Athena. The account has no `default`
    database (checked 2026-11-06), so Terraform creates it, empty.
21. Athena workgroups `silvercare-clean` (Lambda) and `silvercare-analysis` (developers)
    enforce their settings, write results to separate prefixes of
    `s3://silvercare-athena-results-<account_id>/` with SSE-S3, and cancel any query
    scanning over 1 GB. The Lambda runs queries only in its workgroup.
22. The Lambda's IAM role allows: Athena on this workgroup; Glue on `silvercare_lake`;
    `lakeformation:GetDataAccess`; read on the raw `telemetry/` prefix; write and delete
    on the clean bucket's `tables/` prefix; read and write on the results bucket. It has
    no write or delete on the history bucket, and no access to `alerts/` or DynamoDB.
    It may invoke nothing and publish nothing.
22a. Members of the IAM group `silvercare-developers` (looked up by name) get Lake
    Formation `DESCRIBE` on `silvercare_lake` and on the empty `default` database, and
    `SELECT` and `DESCRIBE` on the eight clean tables, and nothing on `raw_telemetry` or `rejected`. Resource addresses must
    not contain IAM user names, because plan comments are public.
22b. `docs/iam/developer-policy.json` adds the least access needed for Athena on
    `silvercare-analysis`, Glue read on `silvercare_lake`, `glue:GetDatabase` on `default`,
    `lakeformation:GetDataAccess`,
    and write to that workgroup's results prefix. The owner applies this by hand.

### Buckets

23. `silvercare-clean-<account_id>` and `silvercare-athena-results-<account_id>` are in
    `ap-southeast-1` and match the history bucket's protections: all public access
    blocked, `BucketOwnerEnforced`, SSE-S3, TLS-only policy, versioning off. Objects under
    `tables/` in clean, `rejected` included, expire after 365 days *(proposed)*. Results
    objects expire after 7 days.

### Checks after the first deploy

23b. The owner runs and records, as the developer user in `silvercare-analysis`: a `SELECT`
    on `vitals` succeeds, and the same on `rejected` and `raw_telemetry` is denied. They also
    confirm the first dated run writes readable Parquet, and that a PR plan after the deploy
    still succeeds.

### Failure alarm

23a. CloudWatch alarm `silvercare-clean-errors` emails the existing `silvercare-ops` topic,
    and sends OK when cleared, when the Lambda has at least 3 errors in one hour (the
    scheduled run and its 2 retries all failed). It doesn't detect a run that never
    started.

### Lambda and docs

24. `lambdas/clean/src/handler.py` follows `lambda-python`, uses the `python-lambda`
    module, and uses only the standard library, boto3, and Powertools. Timeout is 5
    minutes. It runs the queries one after another, polling every 2 seconds.
25. Logs hold only the date, table names, query IDs, states, bytes scanned, and the IDs
    of failed self-check cases (synthetic): never
    lines, field values, device IDs, or message IDs.
26. Unit tests with mocked Athena cover: date input (default, given, invalid), rerun
    order (delete before write), every table queried, a failed query raising, and logs
    holding no data. SQL correctness is checked by the self-check.
27. `docs/data-model.md` gets the clean layer's tables, buckets, and access. `CLAUDE.md`
    says the Athena job does the cleaning and Lake Formation governs access, and its
    Deployment table lists the self-check.

## Data

- Reads: raw files from `docs/data-model.md`, S3 history (`telemetry/` only).
- Writes: clean bucket, replacing a date's output on each run.
- DynamoDB: none, so no RCU or WCU use.

## IoT

No changes. All message types in `docs/metrics.md` are read.

## API contract

None.

## Screens

None.

## Alerts and notifications

No resident alerts. Job failures email `silvercare-ops` (criterion 23a).

## Cost

Rough, based on US or third-party prices, to be checked after the first runs:

| Service | Basis | Monthly |
|---|---|---|
| Athena | $5/TB; 19 queries a run, of which 9 scan data at the 10 MB minimum | ~US$0.02 |
| S3 | under 1 GB of Parquet and a little request cost | ~US$0.05 |
| Lambda, EventBridge, Lake Formation, Glue Data Catalog | within free allowances | $0 |
| CloudWatch alarm | free for the first 10 alarms, then about $0.10 | up to ~US$0.10 |

Total: well under US$1 a month. A fixed-cost service isn't used, and idle cost is close
to zero.

**Free plan check:** the owner ran `SELECT 1` in the Athena console successfully on
2026-11-04. That doesn't test writing through a Lake Formation location, so the first
deploy's self-check and first dated run prove that. If either is blocked by the plan,
stop and ask the owner; the fallback is the Python `.jsonl.gz` approach.

## Privacy

- Personal data touched: pseudonymous health and location-related readings by device
  ID, with no resident ID, names, or contact details. `raw_line` in `rejected` holds the
  message as received.
- Retention: clean tables, `rejected` included, expire 365 days after a file is written
  *(proposed, decided by the owner 2026-11-06 to match raw's limit; Open decision #4)*. A
  re-run of a date restarts that date's clock. Athena result files last 7 days; Athena keeps query text in its history for 45 days.
- Erasure: **not solved by this feature.** Raw files batch many devices together, so
  erasing one device means rewriting those files without it, then re-running the affected
  dates. Those rewrites restart the expiry clock for every other device on those dates,
  so the tool must account for that. `unparseable` lines have no parsed `device_id`, but
  `raw_body` may still contain one as text, so the tool must search that text or rely on
  expiry. Athena results and query history age out rather than being erased. That tool
  must exist before any real resident data arrives.
- Must not be logged: lines, values, device IDs, message IDs.
- Stays in `ap-southeast-1`, encrypted at rest and in transit.

## Out of scope

- Alerts in the clean layer.
- The curated layer and linking devices to residents.
- Dashboards, doctor queries, analytics, thresholds, and Bedrock.
- Non-standard JSON in a raw line (`NaN`, `1e400`) or duplicate top-level keys: the whole
  line goes to `rejected`, even if the bad value is only in accelerometer data. This is a
  known limit of the SQL's JSON parsing; the original stays in raw, and live alerts are
  unaffected by the clean job.
- Two ingest differences that real devices and the IoT rule don't produce: non-ASCII
  digits in `ts` and a `received_at` too large for a bigint pass ingest but are rejected
  here. The original stays in raw.
- A late-data trigger: late raw files are picked up only by re-running that date.
- A per-device erasure tool.
- Backfill automation: older dates are run one at a time with `{"date": ...}`.
- Running two jobs for one date at once isn't guarded against. Reserved concurrency isn't
  used, since Free plan accounts may have little to reserve.

## Open questions

None.
