---
status: ready          # draft | ready | built
---

# Nightly results

## Summary

Each morning, right after the `summaries` job (docs/features/curated-layer.md) writes
the previous Singapore day's summaries to S3, a `results` Lambda (the team slide's
`loadResults`) copies them into a small DynamoDB Results table. A new doctor-only API
route returns a resident's stored nights, and a new "Nightly summary" section on the
resident page shows one night at a time, chosen with a night picker, compared with the
resident's thresholds. The S3 summaries stay the record (lifetime + 6 years); the
Results table is a 30-night copy for the portal.

## Decisions

Made by the user (2026-11-07, during the curated-layer spec):

- Results reach the portal through a DynamoDB Results table filled by `loadResults`
  (the team slide's design), 1 RCU and 1 WCU, inside the always-free 25 / 25 (19 RCU /
  18 WCU used before this feature, 20 / 19 after).
- The table keeps about **30 nights** per resident, then items expire (DynamoDB TTL).
  This is an exception to CLAUDE.md's "DynamoDB is for live state only", recorded in
  CLAUDE.md and `docs/data-model.md`.
- **Doctors only** see the nightly results, through the portal (not Tableau). Nurses
  and caregivers don't.
- The "usual range" is the resident's vitals thresholds; the comparison is already in
  each summary (`*_below` / `*_above` counts and the `thresholds` used).

Made by the user (2026-11-07, this spec):

- **This feature builds the API route and the doctor's screen section**, next to
  Track B's routes and pages; Track B's owner agreed.
- **`loadResults` chains from `summaries`' success** (an on-success destination, as
  clean → summaries).
- **Night picker:** the doctor can choose any stored night (up to 30) to view.
- **Not ready:** after **09:30 SGT**, if there is no summary for yesterday's Singapore
  date, doctors see "Last night's summary isn't ready yet (latest: <date>)" with a
  button to view the latest night available. Before 09:30 SGT the latest night shows
  without a warning.
- **Scope:** the resident page section only. The dashboard list ("who needs a look
  first") is the next feature, `nightly-overview`.
- **Position and layout:** a "Nightly summary" section between "Latest readings" and
  "Alert history" on the resident page, laid out as in Screens below.

## Users and access

| Group | Can see | Can do |
|---|---|---|
| doctor | every resident's stored nights (up to 30) on the resident page | choose a night; read only |
| nurse | nothing in this feature (the section isn't shown; the route returns `403`) | nothing |
| caregiver | nothing in this feature (`403`) | nothing |
| ops, family, no group | nothing (`403`) | nothing |

- The route checks the caller's `cognito:groups` claim: it must contain `doctor`. A
  user in both `doctor` and `nurse` passes the API check, but the portal gives that
  user the nurse view (CLAUDE.md), which doesn't show the section.
- How access to a specific resident is decided: doctors see every resident (staff
  rule, `docs/data-model.md`); there is no link check.
- `results` and `api-nightly` Lambda roles: see criteria 14 and 23.

## Acceptance criteria

### Results table

1. New table `silvercare-results` through `infra/modules/dynamodb-table` (deletion
   protection and point-in-time recovery on, as every table), `hash_key = "pk"`,
   `range_key = "sk"`, read capacity 1, write capacity 1, no indexes, TTL attribute
   `expires_at`. The capacity comment in `infra/dynamodb.tf` says 20 RCU / 19 WCU.
2. One item per resident per Singapore night:

   | Attribute | Example | Notes |
   |---|---|---|
   | `pk` | `RESIDENT#res-0003` | |
   | `sk` | `NIGHT#2026-11-18` | the summary's Singapore `date` |
   | `resident_id` | `res-0003` | |
   | `date` | `2026-11-18` | |
   | `summary` | map | every field of the curated-layer summary (curated-layer.md criterion 11) except `resident_id`, `date`, `generated_at`, `schema_version`; numbers as DynamoDB numbers, nulls as NULL, `thresholds` as a map |
   | `schema_version` | `1` | the summary's |
   | `loaded_at` | `2026-11-19T00:41:07.512Z` | when `results` wrote it |
   | `expires_at` | epoch seconds | 00:00 SGT on `date` + 31 days, so the item lives for 30 nights after its own |

### Load (`results` Lambda)

3. `aws_lambda_function_event_invoke_config.summaries` gains an on-success destination,
   the `silvercare-results` function, and the summaries role's policy gains
   `lambda:InvokeFunction` on that function only. Asynchronous summaries runs (the
   chained daily run) then invoke `results`; manual synchronous runs don't, so after a
   manual summaries run the owner also runs `results` with the same date.
4. Input forms: Lambda's on-success invocation record (`responsePayload.date` and
   `responsePayload.summaries`, summaries' return value), or `{"date": "yyyy-mm-dd"}`
   for a manual run. Anything else, or a date that isn't a real date, raises before any
   AWS call without repeating the value (as clean's and summaries' `parse_date`). When
   the record says `summaries: 0`, the Lambda logs it and returns without reading S3.
5. The Lambda lists `summaries/date=<D>/` in the history bucket (paginated) and reads
   each object. An object is loaded only if it parses as one JSON object with
   `schema_version` 1, `resident_id` matching `^res-[0-9]{4}$`, and `date` equal to D;
   otherwise it's skipped and logged with its key only.
6. Each valid summary is written with an unconditional PutItem (a re-run overwrites
   the same key, so it's idempotent). Writes retry throttling (adaptive retry mode).
7. If any object was skipped, the Lambda raises after loading the valid ones, so the
   errors alarm fires. It returns `{"date": D, "loaded": n}`.
8. No objects for D: logs a warning and returns `{"date": D, "loaded": 0}` (nothing to
   load is not an error; summaries already alarms on its own failures).
9. Asynchronous invocations: 2 retries, maximum event age 1 hour (as clean and
   summaries).
10. Alarms to `silvercare-ops`: `silvercare-results-errors` (at least 3 errors in one
    hour, re-run instructions in the description) and
    `silvercare-summaries-destination-failures` (summaries' `DestinationDeliveryFailures`
    at least 1 in an hour).
11. Logs: date, counts (listed, loaded, skipped), skipped keys. Never summary values,
    thresholds, or object bodies.
12. Lambda `lambdas/results/src/handler.py`, Python 3.13, standard library, boto3,
    Powertools only; timeout 120 s, memory 128 MB, log retention 30 days.
13. Backfill is manual (owner, after merge): after the summaries backfill, run
    `results` with `{"date": ...}` for each of the last 30 nights, oldest first. The
    docs list the command.
14. IAM for `results`: `s3:ListBucket` on the history bucket with `s3:prefix`
    `summaries/*`; `s3:GetObject` on `summaries/*`; `dynamodb:PutItem` on the Results
    table only. Nothing else (no S3 writes or deletes, no other tables).

### API: `GET /residents/{resident_id}/nightly`

15. A new route on the existing HTTP API, behind the Cognito JWT authorizer, served by
    a new Lambda `silvercare-api-nightly` (`lambdas/api-nightly/`), built like the
    other API Lambdas (`lambdas/api-residents/`: payload 2.0, `Cache-Control:
    no-store`, the same error body shape).
16. Check order: caller groups first (`doctor` required, else `403 forbidden`), then
    `resident_id` (must match `^res-[0-9]{4}$`, else `400 invalid_resident_id`), then
    the read. No profile read (user's decision 2026-11-07): an unknown resident returns
    `200` with no nights. Doctors may see every resident, so this reveals nothing, and
    the portal only calls the route inside a resident page whose own
    `GET /residents/{resident_id}` already returns `404` for an unknown resident.
17. The read: Query `pk = RESIDENT#<resident_id>`, `begins_with(sk, "NIGHT#")`, newest
    first, eventually consistent, paginated. Nights whose date is older than 30 days
    before today's Singapore date are dropped (TTL deletes items late, up to days).
18. `expected_date` is yesterday's Singapore date. `not_ready` is true only when the
    Singapore time is 09:30 or later **and** the newest returned night isn't
    `expected_date` (or there are no nights). Before 09:30 SGT it's always false.
19. Route throttling (user's decision 2026-11-07): its own `route_settings`, rate 0.1 per second,
    burst 2 (one page view reads up to 30 items, about 6 RCU, on the 1 RCU table).
20. Logs: outcome, route, request ID, the caller's `sub`, the opaque `resident_id`, the
    number of nights, and on a `403` the caller's groups. Never summary values or
    thresholds.
21. Errors: `400 invalid_resident_id`; `403 forbidden` (not a doctor); `500` for a
    DynamoDB failure (no internal detail in the body).
22. Lambda `lambdas/api-nightly/src/handler.py`, the same runtime and packaging rules as
    criterion 12.
23. IAM for `api-nightly`: `dynamodb:Query` on the Results table only. Nothing else.

### Screen: "Nightly summary" section

24. Shown on the resident page (`/residents/<resident_id>`) only in the **doctor** view,
    between "Latest readings" and "Alert history". The nurse view neither shows it nor
    calls the route.
25. It loads once when the page opens (no polling), with `getNightly(residentId)` in
    `frontend/src/lib/api.ts`, independently of the rest of the page: a failure here
    doesn't affect the other sections.
26. **Night picker:** a `<select>` labelled "Night", listing every returned night newest
    first, formatted like "18 Nov 2026"; the newest is selected by default.
27. **Not ready:** when `not_ready` is true, the section shows (as `role="status"`)
    "Last night's summary isn't ready yet (latest: 18 Nov 2026)" and a "View 18 Nov
    2026" button; the night's figures stay hidden until the doctor presses it or picks a
    night. With no nights at all, it shows "Last night's summary isn't ready yet." with
    no button.
28. **The night's figures**, using the selected night's own `thresholds`:
    - Heart rate, SpO2, Skin temperature: "min 52 avg 71 max 128", then a flag: "12
      above 120" and/or "3 below 50" (each with a warning icon and text, never colour
      alone), "In range" when both counts are 0 and there were readings, "No readings"
      when the statistics are null. SpO2 has only "below".
    - "Activity avg 14%" ("No readings" when null).
    - "Readings 1,312 of 1,440 (worn about 22 h)", hours = `vitals_readings` / 60,
      rounded to the nearest hour.
    - "Time in bed 7 h 40 m" ("No bed data" when null), "Motion events 23".
    - "Falls 0", "Alert episodes 1", "\"I'm OK\" cancels 0".
    - "Limits used that night: HR 50–120 · SpO2 ≥92 · 30–38 °C".
29. **Loading:** a skeleton inside the section with an accessible "Loading nightly
    summary" status. **Error:** "We couldn't load the nightly summaries." with a "Try
    again" button that reloads only this section. **Empty** (no nights, before 09:30
    SGT): "No nightly summaries yet. They appear each morning after a night of
    readings."
30. Matches the page's existing section style (`DetailSection`), works at phone width,
    and the picker and button are keyboard-usable with visible focus.

### Docs

31. `docs/data-model.md`: a `silvercare-results` section (keys, item, TTL, PITR, who
    reads and writes it), access patterns 30 (`results` PutItem) and 31 (API Query),
    Capacity (20 RCU / 19 WCU), and a Retention row. `docs/features/curated-layer.md`:
    summaries' on-success destination. CLAUDE.md's Database line names the Results
    table as the one exception to "live state only" (updated outside the workflow).

### Tests

32. `results`: input parsing, `summaries: 0`, paging, skipping invalid objects then
    raising, idempotent re-run, item shape and `expires_at`, logs without values, IAM
    text checks (as `lambdas/summaries/tests/test_infra.py`).
33. `api-nightly`: group check before validation before the read; `400` / `403`; the
    30-day filter; `not_ready` at 09:29:59 and 09:30:00 SGT, with and without
    yesterday's night, and with no nights; paging; logs without values.
34. Frontend (Vitest): shown to doctors only; picker default and change; not-ready
    banner and its button; the flags ("above", "below", "In range", "No readings");
    worn hours rounding; loading, error with retry, and empty states.

## Data

- Reads: S3 `summaries/date=<D>/` (results); Results table Query (api-nightly).
- Writes: Results table PutItem (results).
- New table: `silvercare-results` (criteria 1-2). New access patterns:
  - 30: backend `results`: write one resident's night: PutItem `pk = RESIDENT#<id>`,
    `sk = NIGHT#<date>`; about 20 a day (about 2 WCU each, within burst capacity).
  - 31: doctor only, resident page: this resident's stored nights: Query
    `pk = RESIDENT#<id>`, `begins_with(sk, "NIGHT#")`, descending; per page view.

## IoT

None.

## API contract

### `GET /residents/{resident_id}/nightly`

- Groups: `doctor`
- Path parameters: `resident_id` (`res-` + 4 digits)
- Request body: none
- Response `200`:

```json
{
  "resident_id": "res-0003",
  "expected_date": "2026-11-18",
  "not_ready": false,
  "nights": [
    {
      "date": "2026-11-18",
      "hr_bpm_min": 52, "hr_bpm_avg": 71.4, "hr_bpm_max": 128,
      "spo2_pct_min": 90, "spo2_pct_avg": 96.1, "spo2_pct_max": 99,
      "skin_temp_c_min": 31.2, "skin_temp_c_avg": 33.8, "skin_temp_c_max": 35.0,
      "activity_pct_avg": 14.0,
      "vitals_readings": 1312,
      "hr_bpm_below": 0, "hr_bpm_above": 12,
      "spo2_pct_below": 3,
      "skin_temp_c_below": 0, "skin_temp_c_above": 0,
      "thresholds": {"hr_bpm": {"min": 50, "max": 120}, "spo2_pct": {"min": 92}, "skin_temp_c": {"min": 30.0, "max": 38.0}},
      "bed_minutes": 460,
      "motion_events": 23,
      "falls": 0,
      "vitals_alert_episodes": 1,
      "cancels": 0
    }
  ]
}
```

- Errors: `400 invalid_resident_id`, `403 forbidden` (any caller not in `doctor`), `500`.

## Screens

### Nightly summary (section of `/residents/<resident_id>`)

- Shown to: doctors (criterion 24).
- Shows (criteria 26-28):

```
┌ Nightly summary ─────────────── Night: [ 18 Nov 2026 ▾ ] ┐
│ ⚠ Last night's summary isn't ready yet (latest: 18 Nov)   │  (only when not_ready)
│   [View 18 Nov 2026]                                      │
│ Heart rate     min 52  avg 71  max 128   12 above 120 ⚠   │
│ SpO2           min 90  avg 96  max 99     3 below 92  ⚠   │
│ Skin temp      min 31.2 avg 33.8 max 35.0  In range       │
│ Activity avg 14%   Readings 1,312 of 1,440 (worn about 22 h) │
│ Time in bed 7 h 40 m   Motion events 23                   │
│ Falls 0   Alert episodes 1   "I'm OK" cancels 0           │
│ Limits used that night: HR 50–120 · SpO2 ≥92 · 30–38 °C   │
└───────────────────────────────────────────────────────────┘
```

- Actions: choose a night; "View <date>" when not ready.
- Loading / empty / error states: criterion 29.

## Alerts and notifications

None for residents. A failed load or a failed hand-off from summaries emails
`silvercare-ops` (criterion 10).

## Cost

Within free allowances: the table's 1 RCU / 1 WCU (20 / 19 of the free 25 / 25),
storage of a few hundred KB, about 20 S3 GETs and 20 PutItems a day, two Lambdas, and
API requests at a few page views a day. Point-in-time recovery is billed per GB of
table size (about US$0.20/GB-month, from memory), so well under US$0.01. Two new alarms
(6 → 8 of the free 10). No fixed fees.

## Privacy

- Personal data touched: per-resident nightly health summaries, keyed by the opaque
  `resident_id`, with no names; shown to doctors only, who already see every resident.
- Retention: Results items expire 30 nights after their date (TTL deletes within days
  of `expires_at`, typically within two days, so the API also hides older nights).
  Point-in-time recovery can restore the whole table to any moment in the last 35
  days, so a deleted or expired item stays recoverable that long. The S3 summaries are
  the record (curated-layer.md).
- Erasure of one resident: Query `pk = RESIDENT#<id>` and delete those items; they
  stay recoverable through point-in-time recovery for 35 days.
- Must not be logged: summary values, thresholds, object bodies.
- Stays in `ap-southeast-1`; DynamoDB encrypts at rest, the API is HTTPS only.

## Out of scope

- The dashboard "who needs a look first" list (`nightly-overview`, next).
- Nurse and caregiver access.
- Deleting a night's item for a resident who no longer has a summary after a re-run
  (it expires with TTL).
- Editing, exporting, or annotating nights.
- Backfill automation (backfill is manual, criterion 13).

## Open questions

None.
