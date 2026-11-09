---
status: built          # draft | ready | built
---

# Vitals trends for doctors

## Summary

A doctor-only page per resident (`/residents/<resident_id>/vitals`) where a doctor
picks a date range and an interval, **1 hour** or **1 day**, and sees one row per
interval: vitals min / average / max, activity, readings, out-of-limit counts, time in
bed, motion events, falls, vitals alert episodes, and cancels. The rows are shown as a
chart above a table and can be downloaded as CSV. Daily rows come from the curated
`daily_summaries` (any date we hold), hourly rows are worked out on request from the
clean tables (the last 365 days). One new API route, read through Athena.

## Decisions

Made by the user (2026-11-09):

- **Doctors only.** Nurses, caregivers, and ops don't get it.
- **Intervals:** 1 hour and 1 day. No per-minute view (see Out of scope).
- **Sources:** 1 day always reads `silvercare_lake.daily_summaries` (any date, and it is
  the stored record with the thresholds that applied). 1 hour always reads the clean
  tables (`vitals`, `fall`, `vitals_alert`, `alert_cancel`, `bed`, `motion`), so only
  the last 365 days. A range never mixes sources.
- **Range:** daily up to 90 days per query, hourly up to 7 days per query. Any start
  date we hold (daily) or within 365 days (hourly).
- **Hourly row columns:** the same as daily: vitals min/avg/max, activity average,
  readings, out-of-limit counts, bed minutes, motion events, falls, vitals alert
  episodes, cancels.
- **Whose readings (hourly):** as daily summaries: the resident's **currently**
  assigned devices, readings before each device's `assigned_at` dropped. The registry
  keeps no assignment history, so readings from a previous device show in daily rows
  but not hourly ones.
- **Hourly out-of-limit counts** use the resident's **current** thresholds, and the
  response says which values were used. An old hour can therefore disagree with its
  day's stored count if a doctor has changed the limits since.
- **Own page**, `/residents/<resident_id>/vitals`, linked from the resident page.
- **Freshness:** up to yesterday (Singapore). Today stays in the resident page's latest
  readings.
- **Display:** a chart above a table of the same rows, drawn with a chart library
  (a new npm dependency).
- **CSV download** of the rows shown.

Builder-level defaults chosen by Claude, for the user to check:

- Chart library: **Recharts 3.10.1** (MIT, released 2026-07-25, React 19 supported,
  needs `react-is` as a peer; checked on npm 2026-11-09), pinned exactly.
- CSV is built in the browser from the response already shown: no second route, no
  file stored anywhere in AWS.
- New Athena workgroup `silvercare-vitals` with the same 1 GB scan limit and results
  prefix expiry (7 days) as the others.
- Lambda timeout 28 s; a query not finished by 25 s is stopped and returns `504`
  (HTTP API's maximum integration timeout is 30 s, not adjustable: AWS docs, "Quotas
  for configuring and running an HTTP API", checked 2026-11-09).
- Route throttle 1 request/s, burst 5.
- The hourly SQL is a copy of the summaries SQL rules (`lambdas/summaries/src/sql.py`)
  with hour buckets, kept in step by a test, as the thresholds module already is.

## Users and access

| Group | Can see | Can do |
|---|---|---|
| doctor (portal view `doctor`) | every resident's vitals trends page | query, view chart and table, download CSV |
| nurse (portal view `nurse`, including users also in `doctor`) | no link on the resident page; the page shows "This page is for doctors." | nothing |
| caregiver | the existing "This page is for care staff." screen (`StaffOnlyNotice`) | nothing |
| ops, `family` only, or no group | the existing no-access screen (`NoAccessPage`), as `StaffOnly` does today | nothing |

- A user in both `nurse` and `doctor` passes the API check, but the portal gives that
  user the nurse view (CLAUDE.md), which shows neither the link nor the page, as the
  Nightly summary section (`docs/features/nightly-results.md`).
- Access to a specific resident: doctors see every resident, so there is no link
  check (as `GET /residents/{resident_id}/nightly`). An unknown resident returns `200`
  with empty rows, so the response never reveals whether a resident exists.
- Check order in the API: group (`doctor`, else `403`) → path and query parameters
  (else `400`) → reads.

## Acceptance criteria

### API: `GET /residents/{resident_id}/vitals`

1. A caller without `doctor` in `cognito:groups` gets `403` before any parameter check
   or read.
2. `resident_id` must match `^res-[0-9]{4}$`, `interval` must be `hour` or `day`,
   `from` and `to` must be real dates in exactly `yyyy-mm-dd` form, with
   `from` ≤ `to`. Otherwise `400` with `error` = `invalid_request`, before any AWS call.
   No parameter text reaches SQL unvalidated.
3. `to` may not be later than yesterday's Singapore date, else `400` `out_of_range`.
   Before that day's pipeline has run (clean at 08:30 SGT, then summaries, about
   09:00), yesterday is allowed but its rows come back `has_data: false`; the page's
   freshness note says "Yesterday's data is ready from about 09:00."
4. `interval=day`: at most 90 days (`to − from` ≤ 89 days), else `400` `range_too_long`.
5. `interval=hour`: at most 7 days (`to − from` ≤ 6 days), else `400` `range_too_long`;
   and `from` no earlier than 365 days before today's Singapore date, else `400`
   `out_of_range`.
6. All dates and hours are Singapore time (UTC+8). A date `D` covers
   D 00:00 – 24:00 SGT.

### Daily rows (`interval=day`)

7. Reads `daily_summaries` for the resident and dates `from`..`to` through Athena,
   with the partition filter on `date`, and returns one row per date in the range,
   oldest first. A date with no summary has `has_data: false` and every value null
   (so the chart shows a gap, not a zero).
8. Each row has the summary's columns (`docs/features/curated-layer.md`) and the
   `thresholds` stored with that summary. `resident_id`, `generated_at`, and
   `schema_version` are not returned.

### Hourly rows (`interval=hour`)

9. Reads the resident's devices (registry `by-resident`, pattern 10) and each device's
   `assigned_at` (BatchGetItem `DEVICE#<id>` / `ASSIGNMENT`, as pattern 29), and the
   resident's thresholds (GetItem `THRESHOLDS#<id>` / `THRESHOLDS`), resolved with
   the defaults as `lambdas/ingest/src/thresholds.py`.
10. Returns one row per Singapore hour in the range (24 per day, oldest first), each
    with `hour` (ISO 8601 with `+08:00`, e.g. `2026-11-08T14:00:00+08:00`) and the same
    columns as daily rows, computed with the summaries rules applied to the hour instead
    of the day:
    - reading time is `ts`, or `received_at` when `clock_skew` is true;
    - only readings at or after the device's `assigned_at`, earliest-received copy of
      each `message_id` kept;
    - vitals, falls, vitals alerts, and cancels from the resident's wearables; bed and
      motion from the bedroom sensors;
    - averages ignore nulls; below / above counts are strict, against the current
      resolved thresholds;
    - `bed_minutes`: the state at the hour's start is the last `bed` event before it in
      the read (look-back as the summaries query); with none, minutes count from the
      first event in the range; an hour that no known bed state reaches (no bedroom
      sensor, or before the first `bed` event in the read) has null, not 0 (built
      2026-11-09: a per-hour unknown is shown as unknown);
    - `vitals_alert_episodes`: an alert starts an episode when the previous one for the
      same wearable and metric came more than 10 minutes earlier, or there was none;
      only starts inside the hour count.
11. Every hour gets a row, even with no readings (counts 0, statistics null). An hour
    has `has_data: true` when it has a vitals reading, a known bed state, or a motion,
    fall, vitals alert, or cancel event; otherwise false.
12. The response's `thresholds` gives the current resolved values used. With no
    assigned device, every hour has `has_data: false`.
13. The clean partitions read are the UTC dates covering the range plus the summaries'
    look-back (2 days before for `bed` and `vitals_alert`). Missing partitions are not
    an error.

### Running the query

14. Queries run in workgroup `silvercare-vitals`, polled every 1 s. A query that
    hasn't succeeded by 25 s is stopped (`StopQueryExecution`) and the API returns
    `504` `query_timeout`. A failed query returns `500` and logs its query ID and state.
15. The API role can read through Lake Formation only `daily_summaries` and the six clean
    tables above (SELECT, plus DESCRIBE on `silvercare_lake` and `default`), write only
    under its own Athena results prefix, and read the registry only for
    `DEVICE#*`, `THRESHOLDS#*`, and the `by-resident` index (`dynamodb:LeadingKeys`).
    No access to `raw_telemetry`, `rejected`, `alerts/`, or any write on lake data.

### Page: `/residents/<resident_id>/vitals`

16. Doctors see a back link to the resident page, the resident's name, an interval
    switch (Hourly / Daily), a date range picker, and presets: for Daily 7 / 30 / 90
    days ending yesterday (default 30), for Hourly 1 / 7 days ending yesterday. Dates
    the API would refuse (after yesterday, too wide, hourly older than 365 days) can't
    be picked, and a note says "Data up to yesterday. Today's readings are on the
    resident page."
17. The chart shows heart rate, SpO2, and skin temperature, one at a time (a metric
    switch), as a min–max band with an average line, plus the threshold lines. Gaps
    (`has_data: false`) are gaps, not zeros. A second chart shows counts (falls,
    alert episodes, cancels, out-of-limit readings) per row. Values are in the table
    for screen readers; the chart has a text summary.
18. The table below has one row per interval with every returned column, oldest first.
19. "Download CSV" saves the rows shown, with a header row, named
    `vitals-<resident_id>-<interval>-<from>-<to>.csv`. It contains the opaque
    `resident_id` only, no name. It is built in the browser; nothing is sent or stored.
20. States: loading (skeleton, with "This can take a few seconds"), empty (every row
    `has_data: false`: "No readings for this resident in this range."), `504`
    ("This took too long. Try a shorter range."), other errors (the existing error
    notice with retry). A non-doctor gets the screens in Users and access.
21. The resident page shows a "Vitals trends" link when the portal view is `doctor`
    (as the Nightly summary section).

### Docs and tests

22. `docs/data-model.md` gains the new access patterns (Data, below), and CLAUDE.md's
    frontend line mentions the page.
23. Tests: each criterion above; the copied `sql.py`, `registry.py`, and `thresholds.py`
    are byte-identical to the summaries Lambda's; the SQL text keeps the summaries'
    rules (as the summaries tests, SQL can't run locally); the response for an unknown
    resident; no values or thresholds in logs.

### Go-live checks (owner, after merge)

24. As a doctor in the portal, open a resident with data: Daily, last 7 days, loads
    and matches the resident page's Nightly summary for those nights.
25. Hourly for one day that has a daily summary: in Athena (`silvercare-analysis`
    workgroup) or from the CSV, the 24 hours' `vitals_readings`, `falls`,
    `motion_events`, and `cancels` add up to that day's summary. `bed_minutes` and
    `vitals_alert_episodes` may differ slightly at the day's edges (an episode or bed
    span crossing midnight). A query error on real Athena (the engine differs from the
    SQL the tests read) shows as a 500: check the function's log for the query ID.
26. As a nurse, the resident page has no "Vitals trends" link and the URL shows "This
    page is for doctors."; the API returns 403 for a nurse token.

## Data

- Reads:
  - `daily_summaries` (Athena, Lake Formation): new pattern **35**, one resident, a
    date range, doctor only.
  - clean `vitals`, `fall`, `vitals_alert`, `alert_cancel`, `bed`, `motion` (Athena):
    new pattern **36**, one resident's current devices, up to 7 days plus look-back.
  - registry: pattern 10 (`by-resident`, devices), the `ASSIGNMENT` read of pattern 29
    for one resident's devices, and the `THRESHOLDS#` GetItem of pattern 18.
    About 3 RCU per hourly request.
- Writes: none. Athena result files under the workgroup's prefix (expire after 7 days).
- New tables: none.

## IoT

None.

## API contract

### `GET /residents/{resident_id}/vitals`

- Groups: `doctor`
- Query parameters: `interval` (`hour` | `day`), `from`, `to` (`yyyy-mm-dd`, Singapore
  dates, inclusive)
- Request body: none

Example: `GET /residents/res-0002/vitals?interval=day&from=2026-11-01&to=2026-11-02`

- Response `200` (synthetic values):

```json
{
  "resident_id": "res-0002",
  "interval": "day",
  "from": "2026-11-01",
  "to": "2026-11-02",
  "thresholds": null,
  "rows": [
    {
      "date": "2026-11-01",
      "has_data": true,
      "hr_bpm_min": 58, "hr_bpm_avg": 71.4, "hr_bpm_max": 104,
      "spo2_pct_min": 90, "spo2_pct_avg": 95.8, "spo2_pct_max": 99,
      "skin_temp_c_min": 32.1, "skin_temp_c_avg": 33.6, "skin_temp_c_max": 34.9,
      "activity_pct_avg": 14.2, "vitals_readings": 1302,
      "hr_bpm_below": 0, "hr_bpm_above": 0, "spo2_pct_below": 0,
      "skin_temp_c_below": 0, "skin_temp_c_above": 0,
      "bed_minutes": 512, "motion_events": 37,
      "falls": 0, "vitals_alert_episodes": 0, "cancels": 0,
      "thresholds": {"hr_bpm": {"min": 50, "max": 120}, "spo2_pct": {"min": 88},
                     "skin_temp_c": {"min": 30.0, "max": 38.0}}
    },
    {"date": "2026-11-02", "has_data": false, "hr_bpm_min": null, "...": null}
  ]
}
```

For `interval=hour`, each row has `hour` instead of `date` and no `thresholds`, and
the top-level `thresholds` holds the current resolved values used.

- Errors: `400` `invalid_request` | `out_of_range` | `range_too_long`, `403`
  (not a doctor), `500` (query failed), `504` `query_timeout`. Error bodies are
  `{"error": {"code": "<code>", "message": "<text>"}}`, as the other routes.

## Screens

### Vitals trends (`/residents/<resident_id>/vitals`)

- Shown to: doctors (others: Users and access).
- Shows: back link, resident name, interval switch, date range with presets, the
  freshness note, the vitals chart with metric switch and threshold lines, the counts
  chart, the table, "Download CSV".
- Actions: change interval, dates, metric; download CSV.
- Loading / empty / error states: criterion 20.

## Alerts and notifications

None.

## Cost

Rough, US prices, to be checked after first use:

| Service | Basis | Monthly |
|---|---|---|
| Athena | $5/TB, 10 MB minimum per query ≈ US$0.00005 per query; 1,000 queries a month | ~US$0.05 |
| Lambda, API Gateway, DynamoDB reads | within free allowances | $0 |
| Recharts | open source | $0 |

No fixed-cost service.

## Privacy

- Personal data touched: a resident's health readings (vitals, activity, bed and
  motion, falls, alerts), aggregated per hour or day, and the resident's thresholds.
  Shown with the resident's name on the page; the CSV holds `resident_id` only.
- Retention: no new storage. Athena result files under the workgroup prefix expire after
  7 days (they hold aggregated rows for one resident). A downloaded CSV is on the
  doctor's device and outside the system's retention and erasure: the page warns
  "This file contains health data. Store it securely and delete it when you're done."
- Erasure: no new copy in AWS beyond the 7-day result files, so erasing a resident's
  summaries and clean rows also covers this feature after 7 days.
- Must not be logged: values, thresholds, Athena result rows, or SQL text. Logs hold
  the outcome, `resident_id`, `interval`, the number of days, the caller's `sub`, and
  the query ID, so each access can be traced without the data.
- Frontend: the page title is "Vitals trends" (no resident name); nothing from the
  response goes to browser storage or the console; the URL holds only the opaque
  `resident_id`. The resident's name comes from the existing `GET /residents/{id}`
  (pattern 18), not the new route.
- PDPA check (2026-11-09): passed; what the PDPA requires for exports is listed under
  "Before any real resident data arrives" in `docs/data-model.md` for the user to check.

## Out of scope

- Per-minute view of a single day (could be a later feature on the same route).
- Today's data (only up to yesterday).
- Nurses, caregivers, and ops.
- Readings from a resident's previous devices in hourly rows (needs assignment history).
- Hourly out-of-limit counts against the thresholds that applied at the time.
- Comparing residents, or ranges across more than one resident.
- Server-side export, scheduled reports, Tableau changes.

## Open questions

-
