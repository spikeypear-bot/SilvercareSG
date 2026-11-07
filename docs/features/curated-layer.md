---
status: ready          # draft | ready | built
---

# Curated layer: daily resident summaries

## Summary

Every morning a `summaries` Lambda (the slide's `runAthena`) turns one Singapore day of
clean telemetry into one summary per resident: heart rate, SpO2, and skin temperature
(min, average, max), average activity, how many readings those cover, time in bed,
motion events, counts of falls, vitals alert episodes, and "I'm OK" cancels, and how
many readings were outside the resident's thresholds. It links devices to residents from
the registry, writes each summary to S3 `summaries/` (the record kept for the
resident's lifetime + 6 years), and catalogs them for Athena. This is the curated layer
of the data lake (raw → clean → curated). It also adds the 365-day expiry on raw
`telemetry/`, which the repo allows only once daily summaries exist.

Later features read these summaries: `nightly-results` (`loadResults`, the Results
table, the doctors' portal view) and `tableau-access` (Tableau through Athena).

## Decisions

Made by the user (2026-11-07):

- Metrics: heart rate, SpO2, and skin temperature min / average / max; average
  activity; `vitals_readings` (how many readings the day's statistics cover); time in
  bed; motion events; counts of falls, vitals alerts, and cancels.
- Vitals alerts are counted as **episodes**: a new episode starts when more than 10
  minutes have passed since the previous `vitals_alert` for the same metric.
- A summary covers one **Singapore day** (00:00–24:00 SGT), ready at about 09:00 SGT
  the next morning.
- A device's readings belong to its **current** resident; readings from before the
  device's `assigned_at` are dropped, as the API already does.
- The device → resident mapping comes from a daily read of the registry: Query
  `by-type` for devices, then BatchGetItem each `DEVICE#<id>` / `ASSIGNMENT` for
  `assigned_at`, which the index doesn't project. It is passed to Athena **inline in
  the query**, so no mapping file is stored.
- Time in bed: when no bed event precedes the day, minutes count from the day's first
  event.
- Stored alerts (`alerts/`) are not read. Fall, vitals alert, and cancel counts come
  from the clean `fall`, `vitals_alert`, and `alert_cancel` tables (device messages).
- "Usual range" means the resident's vitals thresholds. The comparison is **stored in
  the summary**: counts of readings outside the thresholds, plus the thresholds used,
  so the record keeps the limits that applied.
- The job **chains from the clean job's success** (an on-success destination on
  clean), instead of a fixed schedule.
- Results reach the portal through a DynamoDB Results table filled by `loadResults`,
  kept 30 nights (the slide's design). That is the `nightly-results` feature.
- One S3 object per resident per day, with a random key and a `resident_id` tag, like
  photos.
- The shared developer user may query `daily_summaries` through Athena.
- Summaries are derived records that informed care: kept for the resident's lifetime +
  6 years, with no expiry rule.
- The raw `telemetry/` 365-day expiry is added with this feature, as the repo says.

## Users and access

| Who | Can see | Can do |
|---|---|---|
| nurse, doctor, caregiver, ops (portal) | nothing in this feature (doctors get the portal view in `nightly-results`) | nothing |
| `silvercare-summaries` Lambda role | clean tables `vitals`, `fall`, `vitals_alert`, `alert_cancel`, `bed`, `motion` through Athena; registry `DEVICE#` and `THRESHOLDS#` items | write and delete `summaries/` objects in the history bucket; add `daily_summaries` partitions |
| `silvercare-clean` Lambda role | unchanged | additionally invoke `silvercare-summaries` (its on-success destination) |
| Developers (shared IAM user) | `daily_summaries` through Athena in `silvercare-analysis`, as the clean tables; direct S3 reads of `summaries/` through the existing `ReadProjectBuckets` statement | none |

How access to a specific resident is decided: not applicable. No portal user reads
summaries in this feature. Summaries hold the opaque `resident_id`, no names.

## Acceptance criteria

### Trigger and input

1. `aws_lambda_function_event_invoke_config.clean` gains an on-success destination:
   the `silvercare-summaries` function. The clean role's policy gains
   `lambda:InvokeFunction` on that function only (AWS requires the source function's
   execution role to have it). Lambda then invokes summaries asynchronously after every
   successful **asynchronous** clean run (the daily schedule and its retries).
   Synchronous runs, such as a manual `aws lambda invoke`, don't trigger it: after a
   manual clean re-run the owner runs summaries for the same date too.
2. When clean has finished UTC date D, Singapore date D (16:00 UTC on D−1 to 16:00 UTC
   on D) is fully cleaned, so the Lambda summarises **Singapore date D**. Expected
   ready by about 08:40 SGT. A clean run that fails all its retries starts no summary
   (clean's alarm already emails ops).
3. Input forms: Lambda's on-success invocation record (the date is read from its
   `responsePayload.date`, clean's return value), or `{"date": "yyyy-mm-dd"}` for a
   manual re-run or backfill. Anything else, or a date that isn't a real date, raises
   before any AWS call, and the error never repeats the value (as
   `lambdas/clean/src/handler.py` `parse_date`).
4. Before summarising, the Lambda checks that the clean partitions it reads (criterion
   10) exist in the Glue catalog. If any is missing it fails, naming the table and date
   (no values), and writes and deletes nothing.
5. Asynchronous invocations get 2 retries and a maximum event age of 1 hour (as clean).

### Device → resident mapping and thresholds

6. The Lambda Queries `by-type` with `entity_type = DEVICE` (eventually consistent,
   paginated), then BatchGetItem `pk = DEVICE#<device_id>`, `sk = ASSIGNMENT` for each
   device with a `resident_id` (projecting `pk`, `resident_id`, `device_type`,
   `assigned_at`; retrying `UnprocessedKeys` with backoff). Devices without
   `resident_id` are skipped.
7. Every `device_id` must match `^(wearable|bedroom)-[0-9]{4}$`, every `resident_id`
   `^res-[0-9]{4}$` (as `scripts/seed.py`), and every `assigned_at` must be an ISO 8601
   UTC timestamp. An item that fails is skipped and logged with its `device_id` only.
8. For each resident in the mapping the Lambda BatchGetItems
   `pk = THRESHOLDS#<resident_id>`, `sk = THRESHOLDS` and resolves the limits with the
   same rules as `lambdas/ingest/src/thresholds.py` `limits_from_item`: defaults heart
   rate 50–120 bpm, SpO2 at least 92 %, skin temperature 30.0–38.0 °C; each present and
   valid limit replaces its default; a missing or invalid one keeps it; if a metric's
   resolved min is not below its max, both of that metric's limits keep the defaults.
   An invalid limit is logged with `resident_id` and the field name, never the value.
   The rules are copied into `lambdas/summaries/src/` (each function is packaged
   separately) and tested with the same cases as ingest's tests.
9. The validated mapping and resolved limits are passed to Athena as `VALUES` lists in
   the query text; only validated IDs, timestamps, and numbers reach the SQL. A
   reading is used only if its time (criterion 10) is at or after the device's
   `assigned_at`. Readings from unassigned devices are not used. Athena's query text
   limit (262,144 bytes, not adjustable) caps this at roughly 2,000–3,000 devices; the
   Lambda fails with a clear error before starting a query that would exceed it.

### Which readings count

10. A reading's time is `ts`, or `received_at` when `clock_skew` is true. It belongs to
    Singapore date D when that time is in [D 00:00 SGT, D+1 00:00 SGT), i.e.
    [D−1 16:00 UTC, D 16:00 UTC). The query reads clean partitions D−1 and D, plus D−2
    for `bed` and `vitals_alert` (the look-back in criteria 13 and 14).

### Summary fields

11. One summary per resident with at least one assigned device at run time, **even
    with no readings** that day (counts 0, statistics null). Fields:

    | Field | Type | From |
    |---|---|---|
    | `resident_id` | string | registry |
    | `date` | string `yyyy-mm-dd` | the Singapore date |
    | `hr_bpm_min`, `hr_bpm_avg`, `hr_bpm_max` | int, double (1 decimal), int; null if no non-null reading | `vitals.hr_bpm` |
    | `spo2_pct_min`, `spo2_pct_avg`, `spo2_pct_max` | int, double (1 decimal), int; null if none | `vitals.spo2_pct` |
    | `skin_temp_c_min`, `skin_temp_c_avg`, `skin_temp_c_max` | double (1 decimal); null if none | `vitals.skin_temp_c` |
    | `activity_pct_avg` | double (1 decimal); null if none | `vitals.activity_pct` |
    | `vitals_readings` | int | number of `vitals` messages used (at most 1,440 a day: one a minute while worn) |
    | `hr_bpm_below`, `hr_bpm_above` | int | readings strictly below the heart-rate min / above the max |
    | `spo2_pct_below` | int | readings strictly below the SpO2 min (SpO2 has no max) |
    | `skin_temp_c_below`, `skin_temp_c_above` | int | readings strictly below / above the skin-temperature limits |
    | `thresholds` | object | the resolved limits used: `{"hr_bpm": {"min": 50, "max": 120}, "spo2_pct": {"min": 92}, "skin_temp_c": {"min": 30.0, "max": 38.0}}` |
    | `bed_minutes` | int or null | `bed` (criterion 13) |
    | `motion_events` | int | `motion` messages with `motion = true` |
    | `falls` | int | `fall` messages |
    | `vitals_alert_episodes` | int | `vitals_alert` messages (criterion 14) |
    | `cancels` | int | `alert_cancel` messages |
    | `generated_at` | string, ISO 8601 UTC | when the Lambda wrote it |
    | `schema_version` | int | `1` |

12. Averages ignore null readings; a null reading is never counted as below or above.
    Vitals, falls, alert episodes, and cancels use only the resident's wearable;
    `bed_minutes` and `motion_events` only the resident's bedroom sensor. The thresholds
    are those in the registry when the Lambda runs (a change made during the day is
    not split by time; threshold change history isn't built).
13. `bed_minutes`: minutes in the Singapore day while the latest known `occupied` was
    `true`. The state at 00:00 SGT is the last `bed` event before it in the read
    window; with no earlier event, minutes count only from the day's first event. Null
    when the resident has no bedroom sensor or no `bed` event in the read window.
14. `vitals_alert_episodes`: for each metric (`hr_bpm`, `spo2_pct`) separately, a
    `vitals_alert` starts a new episode when no `vitals_alert` for that metric from the
    same wearable came in the previous 10 minutes (exactly 10 minutes is the same
    episode). Only episodes that **start** in the Singapore day count; an episode
    continuing past midnight counts on the day it started. The count is the sum over
    both metrics.

### Output

15. Each summary is one JSON object (UTF-8, `Content-Type: application/json`) at
    `summaries/date=<yyyy-mm-dd>/<uuid4>.json` in `silvercare-history-<account_id>`,
    where `<date>` is the **Singapore** date (unlike `telemetry/` and `alerts/`, whose
    dates are UTC). It is written with the object tag `resident_id=<id>` in the same
    PutObject call.
16. Re-running a date replaces it: the Lambda first completes the query and builds every
    summary, then lists `summaries/date=<D>/`, deletes every object there, then writes
    the new summaries. A failure before the delete leaves the old summaries in place.
17. The Lambda is the only writer of `summaries/`: the history bucket policy denies
    `s3:PutObject` and `s3:DeleteObject` on `summaries/*` to every other principal (as
    `alerts/`).
18. After writing, the Lambda adds the `date=<D>` partition to `daily_summaries`
    (`ALTER TABLE ... ADD IF NOT EXISTS PARTITION`).

### Catalog, Lake Formation, Athena

19. Glue table `silvercare_lake.daily_summaries`: JSON (OpenX JSON SerDe), columns as
    criterion 11 (`thresholds` as a struct), partitioned by `date` (string), location
    `s3://silvercare-history-<account_id>/summaries/`.
20. `summaries/` in the history bucket is registered as a Lake Formation location with
    the existing `silvercare-lake-location` role, which gains read on `summaries/`.
    The Lambda's role gets `DESCRIBE` on the databases, `SELECT` on the six clean
    tables it reads, and `ALTER`, `DESCRIBE`, `INSERT` on `daily_summaries`. Each member
    of the `silvercare-developers` group gets `DESCRIBE` and `SELECT` on
    `daily_summaries` (per IAM user, as the clean tables' developer grants).
21. A new Athena workgroup `silvercare-summaries` (engine 3, enforced settings, 1 GB
    scan limit, results under `summaries/` in `silvercare-athena-results-<account_id>`,
    which expire after 7 days as that bucket's other results). The Lambda runs queries
    only there.
22. IAM for the Lambda (least privilege): Athena on its workgroup; Glue read on
    `silvercare_lake` and its tables, partition create on `daily_summaries`;
    `lakeformation:GetDataAccess`; read and write on its own results prefix;
    `s3:ListBucket` on the history bucket limited to prefix `summaries/`;
    `s3:PutObject`, `s3:PutObjectTagging`, `s3:DeleteObject` on `summaries/*`;
    DynamoDB `Query` on the registry's `by-type` index and `BatchGetItem` on the
    registry limited by `dynamodb:LeadingKeys` to `DEVICE#*` and `THRESHOLDS#*` and by
    `dynamodb:Attributes` to the fields it projects (`pk`, `sk`, `resident_id`,
    `device_type`, `assigned_at`, `hr_bpm`, `spo2_pct`, `skin_temp_c`), with `Null`
    `dynamodb:Attributes` = `false` so a read without a projection is refused (never
    `updated_by` or `updated_by_name` on threshold items). No
    access to `telemetry/`, `alerts/`, other tables, or other registry partitions.

### Raw expiry

23. The history bucket's lifecycle gains a rule expiring objects under `telemetry/` 365
    days after creation. `alerts/` and `summaries/` get no expiry.
24. Backfill is manual (owner, after merge): for each date since the first raw file,
    oldest first, re-run `clean` with `{"date": ...}` if its tables are missing, then
    `summaries` with the same date. The docs list the commands. No raw file reaches
    365 days before the project ends (Nov 2026), so nothing is deleted unsummarised.

### Alarms, logs, docs

25. Alarm `silvercare-summaries-errors`: at least 3 errors in one hour emails the
    existing `silvercare-ops` topic, with re-run instructions in the description (as
    `silvercare-clean-errors`).
26. Alarm `silvercare-clean-destination-failures`: clean's `DestinationDeliveryFailures`
    of at least 1 in an hour emails `silvercare-ops` (otherwise a failed hand-off would
    leave a day unsummarised with no email).
27. Logs: date, Athena query IDs and states, bytes scanned, and counts (devices,
    residents, summaries written, objects deleted). Never summary values, readings,
    thresholds, or the query text.
28. Lambda: `lambdas/summaries/src/handler.py`, Python 3.13, standard library, boto3,
    and Powertools only; timeout 300 s, memory 128 MB, log retention 30 days.
29. Docs updated: `docs/data-model.md` (S3 history: `summaries/` no longer proposed,
    with its layout and writer; S3 clean layer: `daily_summaries` and its access;
    Retention rows; Capacity; the new access pattern), `docs/features/clean-data.md`
    (the on-success destination), and `docs/decisions/0001-iot-rule-routing.md` if it
    describes the raw expiry as not built.

### Tests

30. Unit tests (moto + pytest) cover: input parsing (invocation record, `{"date"}`,
    bad input); the registry read, pagination, and `UnprocessedKeys`; skipping
    unassigned and invalid items; threshold resolution (ingest's cases); the
    missing-partition check; the query-size guard; the replace order (query, then list,
    delete, write; nothing deleted on a query failure); key format and tag; logs with
    no values. The SQL is tested as strings (as `lambdas/clean/tests/test_sql.py`):
    the Singapore day window, `clock_skew`, `assigned_at`, strict threshold
    comparisons, `bed_minutes` carry-over, and vitals alert episodes (a 10-minute gap
    is one episode, 10 min 1 s is two, an episode crossing midnight counts once on its
    start day).

## Data

- Reads: registry pattern 4 (`by-type`, `entity_type = DEVICE`) and the new pattern
  below; clean tables through Athena.
- Writes: `summaries/` objects (replaced per date); `daily_summaries` partitions.
- New access pattern 29: backend `summaries`: "every assigned device's `assigned_at`, and
  each assigned resident's thresholds": BatchGetItem `pk = DEVICE#<device_id>`,
  `sk = ASSIGNMENT` and `pk = THRESHOLDS#<resident_id>`, `sk = THRESHOLDS`, eventually
  consistent, once a day. About 60 items × 0.5 RCU = 30 RCU at once on a 3-RCU table:
  covered by burst capacity, and throttled keys come back as `UnprocessedKeys` and are
  retried.
- No new DynamoDB table or capacity in this feature.

## IoT

None. Reads clean data derived from `vitals`, `fall`, `vitals_alert`, `alert_cancel`,
`bed`, `motion` (`docs/metrics.md`).

## API contract

None.

## Screens

None.

## Alerts and notifications

No resident alerts. Job failures and failed hand-offs from clean email `silvercare-ops`
(criteria 25, 26).

## Cost

Under US$0.01 a month: one Athena query a day scanning a few MB (US$5/TB, 10 MB minimum),
about 20 S3 PUTs with tags a day, Lambda and Glue within free allowances. Two new alarms
(4 exist today, so 6 of the free 10). No fixed fees. At 20,000 residents: 20,000 small
files a day (about US$3/month in PUTs), slower Athena scans over many small files, and
the inline mapping must become a file (criterion 9).

## Privacy

- Personal data touched: per-resident daily health statistics (heart rate, SpO2, skin
  temperature, activity, bed, motion, falls, alert episodes, readings outside
  thresholds, the thresholds themselves), keyed by the opaque `resident_id`, with no
  names or contact details. The device → resident mapping and thresholds exist only in
  the Lambda's memory and the query text; Athena keeps query text 45 days and result
  files 7 days.
- Retention: `summaries/` lifetime + 6 years (no expiry rule); Athena results 7 days.
  Raw `telemetry/` now expires at 365 days.
- Erasure of one resident: find objects by the `resident_id` tag and delete them.
  Deleting at date of death + 6 years still needs a date-of-death field (an existing
  gap in `docs/data-model.md`).
- Must not be logged: summary values, readings, thresholds, the query text.
- Stays in `ap-southeast-1`, encrypted at rest (SSE-S3) and in transit (TLS-only bucket
  policy).

## Out of scope

- The Results table, `loadResults`, API route, and doctors' portal view
  (`nightly-results`).
- Tableau, its IAM user, and its grants (`tableau-access`).
- Caregiver and nurse access to summaries.
- Stored alerts in summaries; possible falls raised by the watchdog.
- Assignment history: readings from before a reassignment that weren't summarised yet
  are dropped.
- Threshold history within a day: the thresholds at run time are used.
- A late-data trigger: a late raw file is picked up only by re-running clean and then
  summaries for that date.
- A tool to erase one resident's summaries, and recording a date of death.
- Backfill automation.

## Open questions

None.
