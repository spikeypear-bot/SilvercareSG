---
status: ready          # draft | ready | built
---

# Device alert limits (device shadows)

## Summary

Each wearable gets its on-device `vitals_alert` limits from its AWS IoT device shadow:
the **stricter** of the resident's thresholds and the fixed 45 / 130 bpm and 88 %. The
thresholds API writes the resident's wearables' shadows when a doctor saves, and an
hourly `shadow-sync` Lambda catches everything else (device moves, seed runs, hand
edits, failed writes).
The simulator's devices read their shadow, and its wearables send `vitals_alert` when a
reading stays past those limits for 10 s, instead of on a script. The Devices tab shows
whether each wearable has its latest limits, and the thresholds section tells staff the
wearable's critical limits. User's decisions 2026-11-08. A follow-up feature
(always-on simulator on EC2, device keys in Secrets Manager) builds on this one.

## What exists today (checked in code, 2026-11-08)

- Device limits are fixed: `docs/metrics.md` (`vitals_alert`); `lambdas/ingest/src/validation.py:237`
  rejects a `vitals_alert` whose `limit` isn't 45 / 130 (`hr_bpm`) or 88 (`spo2_pct`),
  and `lambdas/clean/src/sql.py` (`_vitals_alert_checks`) mirrors that check.
  `lambdas/alert-processor/src/message.py` checks `limit` by range only.
- `critical-alert` raises a **critical** alert at once for every `vitals_alert`. The
  cloud's per-resident check on `vitals` raises warnings against the resolved
  thresholds (`lambdas/ingest/src/thresholds.py`: a missing or invalid limit uses the
  default 50 / 120 bpm, SpO2 92, skin 30.0-38.0 °C).
- Device policies allow connect and publish to their own topics only
  (`infra/modules/iot-devices/main.tf:107`). Thing name = device ID.
- The simulator's `vitals_alert` is scripted: a scenario schedules one 10 s after it
  starts (`simulator/src/engine.py:487`). Normal readings are clamped inside the seed
  file's thresholds narrowed by 8 bpm / 3 % / 1.0 °C (`simulator/src/residents.py:20`).
  `simulator/src/live.py` has one MQTT connection per device and only publishes.
- None of the 20 seeded residents' thresholds is stricter than 45 / 130 / 88, so with
  the seed data every wearable's limits stay 45 / 130 / 88 until a doctor tightens one.

## Decisions (user, 2026-11-08)

| Question | Decision |
|---|---|
| Where limits come from | The resident's thresholds (the ones doctors edit), automatically |
| Allowed range | Only stricter than fixed: limit = stricter of threshold and 45 / 130 / 88 |
| Severity | Every device `vitals_alert` stays critical (nurse emailed at once), as today |
| Bedroom sensors | Get shadow access too; nothing writes limits for them yet |
| Sync status | Shown on the Devices tab |
| Ops on the Devices tab | Sync status only, never the numbers |
| Thresholds section | Shows a note with the wearable's critical limits |
| How shadows are kept in step | Written on save by the thresholds API (instant), plus an hourly reconcile (`shadow-sync`) for everything else |
| Contract | Topics and fields only, no firmware rules |
| Size | One feature (user agreed to the size) |

## Users and access

| Group | Can see | Can do |
|---|---|---|
| doctor, nurse (staff) | Devices tab: each wearable's limits (wanted and in use) and sync status. Resident page thresholds section: the critical-limits note | nothing new (doctors already edit thresholds) |
| ops | Devices tab: each wearable's sync status only; never the numbers | nothing new |
| caregiver | nothing new (no Devices tab, no resident page) | nothing |

Every staff and ops user sees every device, as today (`GET /devices` has no resident
link check). No new group, route, or access pattern on the API side.

## Device limits

`limits` for a wearable, the same shape everywhere (shadow, API):

```json
{"hr_bpm": {"min": 45, "max": 130}, "spo2_pct": {"min": 88}}
```

**Computing a wearable's limits** (`shadow-sync`, dry run, backfill, frontend note):

- Assigned wearable: take the resident's thresholds resolved with the same rules as
  `lambdas/ingest/src/thresholds.py` (missing item, missing or invalid limit → that
  default), then `hr_bpm.min = max(threshold, 45)`, `hr_bpm.max = min(threshold, 130)`,
  `spo2_pct.min = max(threshold, 88)`. Skin temperature is never a device limit.
- Unassigned wearable (no `resident_id`): 45 / 130 / 88.
- If the result has `hr_bpm.min >= hr_bpm.max` (only possible with an extreme
  threshold), the heart rate limits are 45 / 130 and SpO2 keeps its computed value.

**Valid limits** (checked by every component that receives them): integers,
`hr_bpm.min` 45-250, `hr_bpm.max` 20-130, `hr_bpm.min < hr_bpm.max`, `spo2_pct.min`
88-100, no other keys.

## Acceptance criteria

### Contract and ingest

1. `docs/metrics.md` documents, for both device types, the classic shadow topics a
   device uses (`$aws/things/<device_id>/shadow/get`, `get/accepted`, `get/rejected`,
   `update`, `update/accepted`, `update/rejected`, `update/delta`), the shadow document
   (`state.desired.limits` written by the cloud, `state.reported.limits` written by the
   wearable, the `limits` shape and valid limits above), and that bedroom sensors have
   access but no fields yet. `vitals_alert.limit`'s valid range becomes "an integer in
   the metric's value range (`hr_bpm` 20-250, `spo2_pct` 50-100): the device limit
   crossed". The examples stay valid.
2. Given a `vitals_alert` with any integer `limit` in the metric's value range (e.g.
   `hr_bpm` 110 above, `spo2_pct` 92 below), when ingest validates it, then it is
   accepted and raises a critical alert as today. A `limit` outside the range, or not
   an integer, is still rejected with `invalid:limit`. The `value` and `direction`
   rules are unchanged (no-pulse `0` below stays valid).
3. `lambdas/clean/src/sql.py` mirrors criterion 2 exactly, so a row accepted by ingest
   is never marked invalid by the clean job (its tests cover 110 and 92).
4. `docs/features/vitals-alerts.md` gets a one-line note that the fixed limits are now
   the loosest a device may use (this spec).

### IoT policy

5. Both device policies (`wearable`, `bedroom`) also allow, using
   `${iot:Connection.Thing.ThingName}` so a device can reach only its own shadow:
   `iot:Publish` on `topic/$aws/things/<thing>/shadow/get` and `.../shadow/update`;
   `iot:Subscribe` on `topicfilter/` and `iot:Receive` on `topic/` for
   `.../shadow/get/*` and `.../shadow/update/*` (the accepted, rejected, delta, and
   documents replies). Nothing else is added (no `delete`, no named shadows, no other
   wildcards). Listing each reply topic took both policies past AWS IoT's 2048-character
   limit (excluding whitespace) and failed the 2026-11-09 deploy; a test now checks the
   size.

### On save (`api-thresholds`)

5a. After a `PUT /residents/{resident_id}/thresholds` that writes a change
    (`"changed": true`), `api-thresholds` Queries the resident's devices (pattern 10:
    registry `by-resident`, `gsi1_pk = RESIDENT#<resident_id>`,
    `begins_with(gsi1_sk, "DEVICE#")`, projecting `gsi1_sk`, `device_type`), computes
    the limits from the thresholds just written (Device limits above), and calls
    `UpdateThingShadow` with `{"state": {"desired": {"limits": <computed>}}}` for each
    wearable (2 s timeout each). A save that changes nothing writes no shadow.
5b. The shadow write never changes the API's answer: the thresholds are already saved,
    so a failed Query or shadow write (throttle, timeout, error) still returns the
    normal `200`, logs the device IDs with an error code, and is left to the next
    reconcile run (criterion 6). `by-resident` can lag a device move, so a just-moved
    wearable may be missed or written once with the wrong resident's limits; the
    reconcile run corrects both.
5c. IAM: the `api-thresholds` role adds `dynamodb:Query` on the registry `by-resident`
    index and `iot:UpdateThingShadow` on `thing/wearable-*` only, and gets the IoT data
    endpoint as in criterion 12. Logs add the count of shadows written and failed, never
    limit values.

### `shadow-sync` Lambda (hourly reconcile)

6. A new Lambda `silvercare-shadow-sync` (`lambdas/shadow-sync/`) runs every hour
   (EventBridge `rate(1 hour)`) and can also be invoked by hand with `{}`.
7. Each run: Query registry `by-type` `entity_type = DEVICE` (pattern 4: eventually
   consistent, paginated, projecting `pk`, `device_type`, `resident_id`); keep
   wearables; BatchGetItem `THRESHOLDS#<resident_id>` / `THRESHOLDS` for their
   residents (as pattern 29: up to 100 keys, `UnprocessedKeys` retried with back-off,
   projecting `pk`, `hr_bpm`, `spo2_pct`); compute each wearable's limits (Device
   limits above).
8. For each wearable: `GetThingShadow` (classic shadow). If there is no shadow (404),
   or `state.desired.limits` differs from the computed limits, `UpdateThingShadow` with
   `{"state": {"desired": {"limits": <computed>}}}` only. It never writes `reported`,
   never touches bedroom shadows, and never deletes a shadow. When they are equal it
   writes nothing.
9. Given a doctor tightens a resident's heart rate max to 110, then right after the
   save that resident's wearable's desired `hr_bpm.max` is 110 (criterion 5a), or, if
   that write failed, after the next run (at most 1 hour). Given a wearable is moved to
   another resident or unassigned, then within one run (at most 1 hour) its desired
   limits are the new resident's (or 45 / 130 / 88). Until then the device may
   alert at the previous limits; such an alert is routed by `critical-alert` with the
   assignment at receipt, as today, so it never goes to the previous resident.
10. A failure on one wearable (throttled, timeout, error) doesn't stop the others; the
    run then ends with an error so the Lambda's error metric counts it. An alarm
    `silvercare-shadow-sync-errors` emails `aws_sns_topic.ops` when the function has
    errors in 2 consecutive 1-hour periods (2 failed runs in a row), built like
    `watchdog_errors` in `infra/watchdog.tf` (with `ok_actions`, missing data not
    breaching).
11. Logs: counts (wearables, updated, unchanged, failed) and failed device IDs with an
    error code. Never limit values, thresholds, or resident IDs.
12. IAM: `dynamodb:Query` on the registry `by-type` index and `dynamodb:BatchGetItem`
    on the registry limited to `THRESHOLDS#*` partitions (`dynamodb:LeadingKeys`);
    `iot:GetThingShadow` and `iot:UpdateThingShadow` on `thing/wearable-*` only. The
    IoT data endpoint (`aws_iot_endpoint`, `iot:Data-ATS`) comes in through an
    environment variable.

### `GET /devices`

13. Each device gains `alert_limits`. For a bedroom sensor it is `null`. For a wearable,
    `api-devices` calls `GetThingShadow` for every wearable in the response (in
    parallel, 2 s timeout each) and returns:
    - staff: `{"status": ..., "desired": <limits or null>, "reported": <limits or null>}`
    - ops: `{"status": ...}` only.
14. `status`: `in_sync` when desired and reported are both valid limits and equal;
    `pending` when desired is valid and reported is missing, invalid, or different;
    `unknown` when there is no shadow, desired is missing or invalid, or the read
    failed. A shadow read failure never fails the request: the device gets `unknown`
    and the response is still `200`. Invalid limits are returned as `null`.
15. IAM: the `api-devices` role adds `iot:GetThingShadow` on `thing/wearable-*` only.
    Logs add the count of shadow reads that failed; never limit values.

### Screens

16. Devices tab, staff: each wearable shows its limits in use, e.g. "HR 45-130 bpm ·
    SpO₂ ≥ 88 %" (from `reported`, or "No limits reported" when null), and a status:
    "Limits up to date" (`in_sync`), "Waiting for device" (`pending`, plus "New: HR
    45-110 bpm · SpO₂ ≥ 88 %" from `desired`), "Status unknown" (`unknown`). Bedroom
    sensors show "—". Status is text, not colour alone.
17. Devices tab, ops: the same status text, no numbers, no "New:" line.
18. Resident page thresholds section (staff): under the thresholds, the line "The
    wearable sends a critical alert below <min> or above <max> bpm, or SpO₂ below
    <spo2> %.", computed in the frontend from the thresholds shown with Device limits
    above (saved values; while a doctor edits, from the form's values when they are
    valid). When editing, it adds "The wearable gets the change when you save, or
    when it next connects if it's offline." Loading and error states are the
    section's existing ones.

### Simulator

19. Live run: after a device's connection is accepted, it subscribes (QoS 1) to its
    `shadow/get/accepted`, `get/rejected`, and `update/delta`, then publishes an empty
    message to `shadow/get`. Bedroom sensors do the same and ignore the content.
20. A wearable starts with its limits computed from the seed file (Device limits
    above). On `get/accepted` with valid `state.desired.limits`, or `update/delta` with
    valid `state.limits`, it applies them and publishes
    `{"state": {"reported": {"limits": <applied>}}}` to `shadow/update`. On
    `get/rejected` (404, no shadow) it keeps the seed limits and reports them. Invalid
    limits are ignored (nothing applied or reported) and printed as
    `<device_id>: limits rejected`. Output names device IDs only, never limit values.
21. Normal readings stay inside the wearable's current limits narrowed by the margins
    (heart rate `[min + 8, max − 8]`, SpO2 at least `min + 3`). If the narrowed heart
    rate range is empty, readings are `(min + max) // 2`. Skin temperature keeps today's
    seed-based range. Because device limits are never looser than the resident's
    thresholds, normal readings never cross either.
22. A wearable sends `vitals_alert` from its readings, not from a schedule: while worn,
    when its current heart rate is below `min` or above `max` (no pulse counts as 0,
    below), or SpO2 below `min`, continuously for 10 s, it sends one with that metric,
    value, the limit crossed, direction, and `held_s` as today; then every 5 minutes
    while it lasts. Each metric has its own timer; if both fire at the same moment,
    `hr_bpm` is sent first. When the reading comes back inside, the episode ends; a new
    crossing starts a new 10 s timer. A limit change during an episode is applied at
    once, and the next message uses the new limit.
23. The existing scenarios (`hr-spike`, `spo2-drop`, `no-pulse`, `fall`, `im-ok`) give
    the same messages at the same times as today for a resident with 45 / 130 / 88
    limits (their values cross any valid limit); only `limit` changes with the device's
    limits. A timer still under 10 s when the run ends sends nothing and is reported
    with the existing "cut off: run ended" line.
24. `--dry-run` and `simulator/src/backfill.py` open no connection: limits come from the
    seed file (criterion 20's start values) and the alert logic is criterion 22.
    Backfill stays byte-identical for the same arguments and input files.

## Data

- Reads: registry `by-type` DEVICE (pattern 4) and `THRESHOLDS#` items (as pattern 29)
  by `shadow-sync`, every hour; pattern 10 (`by-resident`) by `api-thresholds` once
  per changed save; existing `GET /devices` reads unchanged.
- Writes: none to DynamoDB. Shadows only (desired by `api-thresholds` and
  `shadow-sync`, reported by the wearable). `shadow-sync` never rewrites equal limits;
  a repeated save writes the same desired limits again, which is harmless.
- `docs/data-model.md`: a new access pattern "backend `shadow-sync`: every wearable's
  assignment and its resident's thresholds" (as 29, hourly), with a Capacity row:
  about 1 RCU on `by-type` plus about 10 RCU at once on the 3-RCU registry (20
  THRESHOLDS items × 0.5), absorbed by burst capacity, ≈ 0 RCU on average; and
  `api-thresholds` added to pattern 10's users (one Query per changed save).
- Cost: about 20 `GetThingShadow` per hourly run (≈ 15,000 a month) plus the Devices
  tab's reads and rare updates, within the 225,000 free operations a month; above
  that US$1.25 per million (US East price). Lambda and EventBridge within free tiers.

## IoT

- Shadow topics and document: criterion 1. Device policies: criterion 5.
- `vitals_alert.limit` range: criteria 1-3. No other message changes.
- **Deploy order:** merge and deploy before running the new simulator. AWS IoT closes a
  connection that subscribes without permission, so the new simulator against the old
  policy would keep reconnecting.
- Registry data it relies on: `seed.py` (assignments and thresholds). Without
  assignments every wearable gets 45 / 130 / 88. Emails: unchanged (each scenario
  still raises one critical alert, emailed to the nurse at once).

## API contract

### `GET /devices` (existing; adds `alert_limits`)

- Groups: `doctor`, `nurse`, `ops` (unchanged). Errors unchanged.
- Response `200`, staff (one wearable, one bedroom sensor; other fields unchanged):

```json
{"devices": [
  {"device_id": "wearable-0007", "device_type": "wearable", "...": "...",
   "alert_limits": {"status": "pending",
     "desired": {"hr_bpm": {"min": 45, "max": 110}, "spo2_pct": {"min": 88}},
     "reported": {"hr_bpm": {"min": 45, "max": 130}, "spo2_pct": {"min": 88}}}},
  {"device_id": "bedroom-0007", "device_type": "bedroom", "...": "...",
   "alert_limits": null}
]}
```

- Response `200`, ops: the same, with `alert_limits` `{"status": "pending"}` (no
  `desired`, no `reported`).

## Screens

- Devices tab (`/devices`): criteria 16-17. Loading, empty, and error states unchanged.
- Resident page (`/residents/<resident_id>`), thresholds section: criterion 18.

## Alerts and notifications

No new alert types or emails. Every device `vitals_alert` stays critical, emailed to the
nurse at once (user's decision). With a tightened threshold, a reading past it can now
give both the device's critical alert and the cloud's warning, as two alerts.

## Privacy

- Personal data touched: a wearable's limits are derived from a resident's clinical
  thresholds. The shadow holds only the limits, never the resident ID, name, or
  thresholds, and lives in AWS IoT in `ap-southeast-1`. Ops never sees the numbers.
- Retention and erasure: the shadow is a working copy, replaced within an hour when a
  device is reassigned or unassigned (an erased resident's devices are unassigned, so
  their limits disappear from the shadow). Deleting a thing does not delete its shadow
  (AWS docs), so retiring a device also needs its shadow deleted by the account owner;
  a line is added next to "Removing a device from the list and merging deletes both"
  in `docs/features/iot-core.md`: the account owner then deletes the thing's shadow.
- Never logged: limit values, thresholds, resident IDs (Lambdas, simulator).

## Out of scope

- The always-on EC2 simulator and Secrets Manager (next feature).
- Real device firmware and firmware rules in the contract.
- Named shadows, shadow deletion by code, anything in bedroom shadows.
- Skin temperature on the device; changes to the cloud's threshold check.
- Alerts or emails when a device stays `pending`.
- DynamoDB Streams or an SQS queue for the sync: the save writes the shadow directly,
  and the hourly reconcile covers the rest.
- Writing shadows from `seed.py` or on device moves: the hourly reconcile covers them.

## Open questions

-
