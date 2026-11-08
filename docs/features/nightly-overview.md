---
status: ready          # draft | ready | built
---

# Nightly overview

## Summary

A "Last night" section on the doctor dashboard, built from the nightly results
(docs/features/nightly-results.md): the residents whose night had a fall, a vitals alert
episode, or readings outside their own limits, most important first, each linking to the
resident page where the "Nightly summary" section shows the full night. A new
doctor-only API route reads last night's item for every resident from the Results
table.

## Decisions

Made by the user (2026-11-07, during the nightly-results spec):

- Doctors only, through the portal. The Results table (`silvercare-results`) is the
  source; this feature doesn't change how it is loaded.
- The resident page section came first; this list is the shortcut to it.

Made by the user (2026-11-08, this spec):

- **What flags a resident's night:** a fall or a vitals alert episode (`falls` > 0 or
  `vitals_alert_episodes` > 0), or readings outside the resident's own limits that
  night (any of `hr_bpm_above`, `hr_bpm_below`, `spo2_pct_below`, `skin_temp_c_above`,
  `skin_temp_c_below` > 0). Low wear time and a missing summary don't flag a resident.
- **Order:** a fixed priority: falls first, then alert episodes, then out-of-limit
  readings (most readings outside limits first). Ties: the higher count, then resident
  ID (criterion 9).
- **Residents with nothing flagged** aren't listed; the section shows how many there
  were. Residents with **no summary** for last night are counted too ("2 residents have
  no summary for last night"), not named.
- **Night:** last night only (yesterday's Singapore date), no night picker.
- **Not ready:** the resident page's rule: before 09:30 SGT a neutral note, from 09:30 a
  warning; no older night is shown.
- **Rows:** the resident's name and all their reasons in one line ("1 fall · 1 alert
  episode · 12 HR above 120"); the name links to the resident page.
- **Placement:** after the alert summary, before the residents panel.
- **Unreadable limits** (user's decision, 2026-11-08, after the build): a night whose
  stored `thresholds` can't be read is still flagged on its counts, with
  `thresholds: null`, and its reasons say "limit" instead of the number ("12 HR above
  limit"), so a fall never drops off the list because of a labelling problem. A night
  whose key or counts can't be read still counts as "no summary".
- **Data read:** the Lambda lists resident IDs from the registry and reads last night's
  items with one BatchGetItem (no table, index, or capacity change).

## Users and access

| Group | Can see | Can do |
|---|---|---|
| doctor | every resident's flags for last night, on the doctor dashboard | open a resident's page; read only |
| nurse | nothing in this feature (the section isn't shown; the route returns `403`) | nothing |
| caregiver | nothing (`403`) | nothing |
| ops, family, no group | nothing (`403`) | nothing |

- The route checks the caller's `cognito:groups` claim: it must contain `doctor`. A user
  in both `doctor` and `nurse` passes the API check, but the portal gives that user the
  nurse view (CLAUDE.md), which doesn't show the section.
- How access to a specific resident is decided: doctors see every resident (staff rule,
  `docs/data-model.md`); there is no link check and no path parameter.

## Acceptance criteria

### API: `GET /nightly/overview`

1. A new route on the existing HTTP API, behind the Cognito JWT authorizer, served by a
   new Lambda `silvercare-api-nightly-overview`. Its code is a second entry point in
   `lambdas/api-nightly/src/handler.py` (`overview_handler`), sharing the Singapore-date
   and 09:30 logic with `silvercare-api-nightly`; built like the other API Lambdas
   (payload 2.0, `Cache-Control: no-store`, the same error body shape). It has its own
   IAM role (criterion 16).
2. Check order: caller groups first (`doctor` required, else `403 forbidden`), then the
   reads. The route takes no parameters; a query string is ignored.
3. `expected_date` is yesterday's Singapore date (as `silvercare-api-nightly`).
4. **Residents:** Query the registry's `by-type` index, `entity_type = RESIDENT`
   (pattern 8), eventually consistent, paginated, `Select = SPECIFIC_ATTRIBUTES`,
   projecting `pk` only. The resident ID is `pk` without `RESIDENT#`; a `pk` that doesn't
   give `^res-[0-9]{4}$` is skipped and logged as a count only.
5. **Last night:** BatchGetItem on the Results table of `pk = RESIDENT#<id>`,
   `sk = NIGHT#<expected_date>` for every resident (up to 100 keys per call),
   eventually consistent, projecting `pk`, `summary.falls`,
   `summary.vitals_alert_episodes`, the five out-of-limit counts, and
   `summary.thresholds`. `UnprocessedKeys` are retried with exponential back-off (up to
   5 attempts); if keys are still unprocessed, the route returns `500` (never a partial
   list presented as complete).
6. `available` is true when at least one item was returned. `not_ready` is true only
   when `available` is false **and** the Singapore time is 09:30 or later. When
   `available` is false, `flagged` is empty and both counts are 0.
7. A night is **flagged** when `falls` > 0, or `vitals_alert_episodes` > 0, or any of
   the five out-of-limit counts > 0. A missing or null count counts as 0.
8. Counts: `clear_count` = residents with an item that isn't flagged;
   `no_summary_count` = residents from criterion 4 with no item. `len(flagged) +
   clear_count + no_summary_count` equals the number of residents.
9. Order of `flagged`: tier 1 `falls` > 0, tier 2 `vitals_alert_episodes` > 0, tier 3
   the rest. Within a tier, sort by (descending) `falls`, then `vitals_alert_episodes`,
   then `out_of_limit` (the sum of the five counts), then by `resident_id` ascending.
10. Each flagged entry returns `resident_id`, `falls`, `vitals_alert_episodes`,
    `hr_bpm_above`, `hr_bpm_below`, `spo2_pct_below`, `skin_temp_c_above`,
    `skin_temp_c_below`, `out_of_limit`, and that night's `thresholds` (as stored), or
    `null` when the stored thresholds can't be read (not a map of the three metrics with
    only their allowed limits). No other summary fields. A night whose key or any count
    can't be read (not a whole number of 0 or more) counts under `no_summary_count`.
11. Route throttling: its own `route_settings`, rate 0.1 per second, burst 2 (as the
    nightly route; one call reads about 20 items).
12. Logs: outcome, route, request ID, the caller's `sub`, `expected_date`, and the
    counts (residents, flagged, clear, no summary), and, when above 0, the number of
    unreadable items and of nights with unreadable thresholds. On a `403`, the caller's
    groups.
    Never resident IDs with their flags, summary values, or thresholds.
13. Errors: `403 forbidden` (not a doctor); `500` for a DynamoDB failure or keys left
    unprocessed (no internal detail in the body).
14. Lambda timeout 10 s, memory 128 MB, log retention 30 days; Python 3.13, standard
    library, boto3, Powertools only.
15. No alarm of its own (as the other API Lambdas); failures show as `500` in the portal.
16. IAM for `api-nightly-overview`:
    - Registry `by-type` index: `dynamodb:Query` only, with `ForAllValues:StringEquals`
      `dynamodb:Attributes` = `pk`, `sk`, `entity_type` (`sk` because a later page's
      `ExclusiveStartKey` holds it, as the dashboard's by-type statement), `Null` `dynamodb:Attributes` =
      `false`, and `dynamodb:Select` = `SPECIFIC_ATTRIBUTES` (the iam-policy skill's
      rule for an index that projects personal data).
    - Results table: `dynamodb:BatchGetItem` only.
    - Its own log group. Nothing else.

### Screen: "Last night" section on the doctor dashboard

17. Shown on the doctor dashboard only, after the alert summary and before the residents
    panel. The nurse dashboard neither shows it nor calls the route.
18. Loaded with `getNightlyOverview()` in `frontend/src/lib/api.ts` when the doctor
    dashboard opens, independently of the rest of the dashboard: a failure here doesn't
    affect the other sections, and the dashboard's 30 s polling doesn't reload it. The
    response is kept in memory for the browser tab and reused for 10 minutes if the
    doctor leaves and comes back to the dashboard; after that, or when the Singapore date
    has changed, it loads again. A not-ready response is reused the same way, so a doctor
    who opened the dashboard at 09:25 sees the neutral note until about 09:35, and newly
    loaded results can take up to 10 minutes to appear (a full page reload shows them
    at once).
19. Heading: "Last night · 18 Nov 2026" (`expected_date`, formatted as on the resident
    page's Nightly summary).
20. **Flagged list:** an ordered list in the API's order. Each row: the resident's name
    from the dashboard data already loaded (`GET /dashboard`), or the `resident_id` if
    that resident isn't in it; the name links to `/residents/<resident_id>`. Then the
    reasons, in this order, joined with " · ", each only when its count is above 0:
    - "1 fall" / "2 falls"
    - "1 alert episode" / "2 alert episodes"
    - "12 HR above 120", "3 HR below 50" (the night's `thresholds.hr_bpm` max / min)
    - "3 SpO2 below 92" (`thresholds.spo2_pct.min`)
    - "5 skin temp above 38", "5 skin temp below 30" (`thresholds.skin_temp_c`, shown as
      on the resident page's "Limits used that night")
    When `thresholds` is `null`, or one limit is missing, the reason says "limit"
    instead of the number: "12 HR above limit".
    A warning icon with the text, never colour alone.
21. Under the list: "14 residents had nothing flagged." and "2 residents have no summary
    for last night." (singular "1 resident had" / "1 resident has"), each line only when
    its count is above 0.
22. **Nothing flagged** (available, `flagged` empty): "Nothing flagged last night." then
    the count lines.
23. **Not ready:** `not_ready` false and `available` false (before 09:30 SGT): neutral
    text "Last night's summaries usually appear by 09:30." `not_ready` true: a warning
    (`role="status"`, icon and text) "Last night's summaries aren't ready yet." No list
    and no counts in either case.
24. **Loading:** a skeleton inside the section with an accessible "Loading last night's
    overview" status. **Error** (including `429` throttling): "We couldn't load last
    night's overview." with a "Try again" button that reloads only this section.
25. Matches the dashboard's existing section style (the alert summary's `Card` and
    heading), works at phone width, and links and the button are keyboard-usable with
    visible focus.

### Docs

26. `docs/data-model.md`: access pattern 32 (this route: by-type Query projecting `pk`,
    then BatchGetItem of last night's Results items), pattern 8's "Serves" and the
    `by-type` index row gain this route, and the `silvercare-results` section lists it
    as a reader.

### Tests

27. `api-nightly-overview`: group check before any read (`403` makes no DynamoDB call);
    flag rule (each of the seven counts alone flags; all zero or null doesn't); tier
    order and every tie-break; counts add up; residents with no item; an invalid `pk`
    skipped; `available` / `not_ready` at 09:29:59 and 09:30:00 SGT with and without
    items; `UnprocessedKeys` retried, then `500` when they persist; registry paging;
    logs without resident IDs, values, or thresholds; IAM text checks (as
    `lambdas/api-nightly/tests/test_infra.py`).
28. Frontend (Vitest): shown to doctors only, and the nurse dashboard makes no call; list
    order kept; reason text, plurals, and thresholds from the night's own limits; name
    fallback to `resident_id`; link target; count lines and their singulars; nothing
    flagged; both not-ready states; loading, error with retry; not reloaded by polling;
    10-minute reuse.

## Data

- Reads: registry `by-type` Query `entity_type = RESIDENT` (pattern 8, projecting
  `pk`); Results table BatchGetItem (new pattern 32).
- Writes: none.
- New access pattern:
  - 32: doctor only, doctor dashboard "Last night": every resident's item for yesterday's
    Singapore date: BatchGetItem `pk = RESIDENT#<id>`, `sk = NIGHT#<date>` (up to 100
    keys, `UnprocessedKeys` retried), eventually consistent, projecting the flag counts
    and `thresholds`; per dashboard open (reused for 10 minutes, route throttled to
    0.1/s, burst 2).

## IoT

None.

## API contract

### `GET /nightly/overview`

- Groups: `doctor`
- Path / query parameters: none
- Request body: none
- Response `200`, results loaded:

```json
{
  "expected_date": "2026-11-18",
  "available": true,
  "not_ready": false,
  "flagged": [
    {
      "resident_id": "res-0003",
      "falls": 1,
      "vitals_alert_episodes": 1,
      "hr_bpm_above": 12, "hr_bpm_below": 0,
      "spo2_pct_below": 0,
      "skin_temp_c_above": 0, "skin_temp_c_below": 0,
      "out_of_limit": 12,
      "thresholds": {"hr_bpm": {"min": 50, "max": 120}, "spo2_pct": {"min": 92}, "skin_temp_c": {"min": 30.0, "max": 38.0}}
    }
  ],
  "clear_count": 17,
  "no_summary_count": 2
}
```

- Response `200`, not loaded yet (after 09:30 SGT):

```json
{
  "expected_date": "2026-11-18",
  "available": false,
  "not_ready": true,
  "flagged": [],
  "clear_count": 0,
  "no_summary_count": 0
}
```

- Errors: `403 forbidden` (any caller not in `doctor`), `429` (API Gateway throttling),
  `500`.

## Screens

### "Last night" (section of the doctor dashboard, `/`)

- Shown to: doctors (criterion 17).
- Shows (criteria 19-23):

```
┌ Last night · 18 Nov 2026 ───────────────────────────────────────┐
│ 1 ⚠ Tan Ah Kow     1 fall · 1 alert episode · 12 HR above 120   │
│ 2 ⚠ Lim Siew Ling  2 alert episodes · 3 SpO2 below 92           │
│ 3 ⚠ Goh Bee Hoon   40 HR above 120 · 5 skin temp below 30       │
│ 4 ⚠ Ng Kim Seng    2 HR below 50                                │
│                                                                 │
│ 14 residents had nothing flagged.                               │
│ 2 residents have no summary for last night.                     │
└─────────────────────────────────────────────────────────────────┘
```

(Names above are synthetic examples.)

- Actions: open a resident's page from their name; "Try again" on error.
- Loading / empty / error states: criteria 22-24.

## Alerts and notifications

None.

## Cost

Within free allowances. One call reads about 20 Results items (about 0.5 RCU each,
eventually consistent, so about 10 RCU) and a keys-only registry query (under 1 RCU).
The Results table has 1 RCU; DynamoDB's burst capacity (up to 300 s of unused capacity)
covers occasional calls, and the route throttle (0.1/s) plus the 10-minute reuse keep
the average near or below 1 RCU. No table, index, or capacity change (still 20 RCU / 19
WCU of the free 25 / 25). One more Lambda and API route at a few calls a day. No new
alarms, no fixed fees.

## Privacy

- Personal data touched: per-resident flag counts and thresholds for one night, keyed by
  the opaque `resident_id`; names come from the dashboard data doctors already load.
  Doctors already see every resident and the full nights on the resident page.
- Retention: nothing new is stored; the Results items keep their 30-night TTL. The
  10-minute in-memory reuse ends when the tab closes.
- Erasure: nothing new to erase (see nightly-results.md).
- Must not be logged: resident IDs together with their flags, summary values,
  thresholds.
- Stays in `ap-southeast-1`; the API is HTTPS only.

## Out of scope

- A night picker or earlier nights in the list (the resident page has them).
- Flags for low wear time or missing data, beyond the "no summary" count.
- Nurse, caregiver, and ops access.
- Polling or live updates of the section.
- Changes to the `results` Lambda, the Results table, or its capacity.
- Acknowledging or marking a night as reviewed.

## Open questions

None.
