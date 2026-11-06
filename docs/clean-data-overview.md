# Clean data overview

A plain-language summary of the clean-data pipeline. The full spec is
[features/clean-data.md](features/clean-data.md).

**Status (2026-11-06):** reviewed, with confirmed findings fixed and local checks passing,
but not committed or deployed. None of the SQL has run on real Athena, only on local Trino.

## What gets created

| Part | What it is | Purpose |
|---|---|---|
| `silvercare-clean` Lambda | Runs daily at 08:30 Singapore time | Starts the cleaning for the previous UTC day |
| Clean bucket | `silvercare-clean-<account_id>` | Holds the clean Parquet tables; files expire after 365 days (proposed, matching raw) |
| Athena results bucket | Files expire after 7 days | Holds query output files |
| Glue databases | `silvercare_lake`, with `raw_telemetry` and 9 output tables; an empty `default`, which Lake Formation requires for Athena | Lets Athena query the files like tables |
| Lake Formation | Admins, S3 locations and permissions | Controls who can query which table |
| 2 Athena workgroups | `silvercare-clean`, `silvercare-analysis` | One for the job and one for developers, with a 1 GB limit per query |
| Failure alarm | Emails `silvercare-ops` | Fires when all 3 attempts fail |

## The cleaning functions

The Lambda doesn't clean the data itself. It builds SQL, and Athena runs that SQL. The
files are in `lambdas/clean/src/`.

| Function | File | What it does |
|---|---|---|
| `clean_date` | `handler.py` | Runs the steps for one date |
| `delete_output` | `handler.py` | Deletes that date's old output, so a re-run can't create duplicates |
| `_validated`, `_reason` | `sql.py` | Check each line and give the reason if it fails |
| `clean_query` | `sql.py` | Keeps valid lines, sets column types and removes duplicates |
| `_accel_valid`, `_accel_json` | `sql.py` | Check the accelerometer data and rebuild it from known fields |
| `rejected_query` | `sql.py` | Saves failed lines with their reason |
| `run_self_check` | `handler.py` | Runs 167 made-up test lines through the SQL after each deploy |

## What it cleans on

The rules match what ingest accepts, except for bad accelerometer data, and a test fails
if ingest's rules change.

**Every message**

- **Device ID:** must look like `wearable-0001` or `bedroom-0001`.
- **Message type:** the device must be allowed to send it.
- **Other required fields:** `schema_version` must be 1, `message_id` a valid UUID, and
  `ts` and `received_at` valid times.

**Each message type** (`lambdas/clean/src/tables.json`)

| Clean table | Field rules |
|---|---|
| `vitals` | Heart rate 20–250, SpO2 50–100, skin temp 20–45 °C (these three can be null); activity 0–100 |
| `vitals_alert` | Metric must be `hr_bpm` or `spo2_pct`; heart rate 0 (no pulse) is allowed; held for 10–600 s |
| `fall` | Confidence 0–1, peak 0–16 g, still for 0–600 s |
| `alert_cancel` | No extra fields allowed |
| `wearable_status` | Battery 0–100, plus `worn`, `charging` and `fw_version` |
| `bedroom_status` | Battery 0–100 or null, plus `fw_version` |
| `motion`, `bed` | A true/false value |

**Also handled**

- **Duplicates:** keeps the earliest copy of each `message_id` on that date.
- **Clock problems:** if `ts` and `received_at` are over 5 minutes apart, the row stays
  and `clock_skew` is true.
- **Misfiled lines:** lines under `unknown` that name a known type are moved to that
  type's table.

## Raw to clean

**Raw:** one line of JSON text in a gzipped file, with all message types kept in folders.
This example is synthetic.

```json
{"schema_version":1,"message_id":"00000000-0000-4000-8000-000000000001","ts":"2026-10-18T03:12:00.000Z","hr_bpm":72,"spo2_pct":97,"skin_temp_c":33.4,"activity_pct":12,"accel_g":{"x":0.02,"y":-0.11,"z":0.99},"device_id":"wearable-0001","message":"vitals","received_at":1792293121000}
```

**Clean:** one typed row in the `vitals` Parquet table.

| Column | Value | Change |
|---|---|---|
| `message_id` | `00000000-0000-4000-8000-000000000001` | Same |
| `device_id` | `wearable-0001` | Same |
| `ts` | `2026-10-18 03:12:00.000` | Text → timestamp |
| `received_at` | `2026-10-18 03:12:01.000` | Epoch milliseconds → timestamp |
| `schema_version` | `1` | Same |
| `clock_skew` | `false` | **New** |
| `hr_bpm`, `spo2_pct`, `activity_pct` | `72`, `97`, `12` | JSON → integer |
| `skin_temp_c` | `33.4` | JSON → decimal |
| `accel_g` | `{"x":0.02,"y":-0.11,"z":0.99}` | Rebuilt as text, with known fields only |
| `accel_valid` | `true` | **New** |
| `date` | `2026-10-18` | **New**, from the raw folder |

`message` is dropped because the table name already says it, and so are unknown fields.

**Bad data**

| Raw line has | Result |
|---|---|
| `hr_bpm: 300` | Goes to `rejected` with reason `out_of_range:hr_bpm` and the original line |
| Accelerometer axis of 99 | Stays in `vitals`, with `accel_g` null and `accel_valid` false |
| Same `message_id` twice | One row is kept |

The raw files are never changed, so any date can be re-run.

## Why critical alerts aren't cleaned

There are two kinds of "critical alert", and only one is left out:

| Data | Where it comes from | Cleaned? |
|---|---|---|
| `fall`, `vitals_alert`, `alert_cancel` | Sent by the device | **Yes**, each has its own table |
| Alert records in `alerts/date=<d>/<alert_id>.json` | Created by our system when it raises an alert | **No** |

The owner's "all telemetry" choice left the alert records out, for these reasons:

- **Already structured:** our own code writes them as one JSON file per alert, and a
  redelivered alert overwrites the same file, so they don't have the same problems as raw
  lines.
- **Resident-linked:** they are stored per resident, while the clean layer holds device
  IDs only.
- **Longer retention:** they must be kept for the resident's lifetime + 6 years, unlike
  telemetry.
- **Separate from emergencies:** alerts fire live, while this job runs once a day, so it
  never delays or blocks one.

**Limits for the doctor dashboard**

- **Alert status:** the S3 copies are never updated, so they still show `open` even after
  someone acknowledges or resolves the alert.
- **Missing alerts:** until the fall alert bug below is fixed, a fall with bad
  accelerometer data can appear in clean even though no alert was raised.

## When picking this back up

**Owner, before merging**

1. Apply the plan role change: `cd infra/bootstrap && terraform apply`, after reading what
   it will change.
2. Commit and open the PR, then compare its plan comment with the expected plan: about 62
   creates with one developer IAM user, 1 update (the `history` Lambda), and no replace or
   destroy rows. Stop and ask about any other row.

The account had no `default` Glue database and no Lake Formation admins on 2026-11-06, so
Terraform creates `default` and removes no admin. If the apply says `default` already
exists, something created it since then: stop and ask.

**Owner, after merging**

1. Confirm the deploy's self-check passed.
2. Apply `docs/iam/developer-policy.json` to the `silvercare-developers` group by hand.
3. Run one day with data, replacing the date:
   ```sh
   aws lambda invoke --function-name silvercare-clean --cli-binary-format raw-in-base64-out --cli-read-timeout 310 --payload '{"date":"yyyy-mm-dd"}' /tmp/clean-run.json
   ```
4. As the developer user in `silvercare-analysis`, confirm a `SELECT` on `vitals` works,
   and that `rejected` and `raw_telemetry` are denied. This covers Athena only: developers
   can still read those files directly in S3, which must be reviewed before real data (see
   "Before any real resident data arrives" in `data-model.md`).
5. Check that the next PR's plan still succeeds. Use the PR comment: a local plan as the
   developer user will probably fail now, because it has no access to `rejected` or
   `raw_telemetry`.
6. Run `aws lakeformation get-data-lake-settings --region ap-southeast-1` and confirm
   `CreateDatabaseDefaultPermissions` and `CreateTableDefaultPermissions` are empty. Today
   they give `IAM_ALLOWED_PRINCIPALS` full access, which would bypass Lake Formation.

If the first dated run gets `AccessDenied`, check first the `s3:GetObject` on `tables/`
that was removed from the job's role.

**Next with Claude**

1. Fix the live fall alert bug on its own branch: ingest's `validate_event` runs before
   alerts are raised (`lambdas/ingest/src/handler.py:196`), so a fall with a malformed
   `accel_window` is rejected and gets no alert.
2. Build the curated layer and the doctor queries. Dashboards, analytics and Bedrock stay
   parked until then.
