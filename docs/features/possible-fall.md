---
status: ready          # draft | ready | built
---

# Possible fall at night (Track A4b)

## Summary

The bedroom unit (bed pressure mat + motion sensor) can show a fall the wearable
missed: at night the resident leaves the bed, does not come back for 20 minutes, and
nothing is moving in the bedroom. If the wearable isn't being worn (or we can't tell),
the `watchdog` (A4a) raises a **critical** `possible_fall` alert, which goes to the
caregivers and the nurses at once (alert-routing). It closes when a person cancels or
acknowledges it, when the resident presses "I'm OK", or automatically, with an email,
when the resident is back in bed.

## Users and access

No screen and no API in this feature.

| Group | Can see | Can do (Track B's API, `status_update`) |
|---|---|---|
| nurse | the alert by email at once and in-app; the "back in bed" follow-up | acknowledge (as any health alert) |
| caregiver | the alert by email and in-app for linked residents; the follow-up | cancel (as any health alert) |
| ops | nothing (health alert) | nothing |
| doctor | nothing new | nothing |

Access to a specific resident: as other health alerts (`resident_id` filter on
`silvercare-alerts`; nurses see every resident).

| Who | Does |
|---|---|
| `watchdog` | Every 5 minutes also checks every assigned bedroom unit for a possible fall, and resolves an open one when the resident is back in bed |
| `alert-processor` | Stores and routes it as a critical health alert; sends the "back in bed" follow-up; lets "I'm OK" cancel it |

## Product decisions (user, 2026-11-04)

| Decision | Value |
|---|---|
| Rule | the bed has been empty for at least **20 minutes**, the bed was vacated between **22:00 and 07:00 SGT**, and the bedroom motion sensor is **cleared** (nobody moving in the room) |
| Night window | the time the bed was vacated must be inside 22:00–06:59:59 SGT; the alert itself may fire after 07:00 |
| Wearable | alert **only when the resident's wearable is not worn**. Unknown counts as not worn: no wearable assigned, or no `status` from it in the last 15 minutes. A recent `worn = true` heartbeat from any of the resident's wearables means no alert |
| Severity and routing | `critical`: caregivers and nurses at once (alert-routing), never suppressed as a repeat |
| "I'm OK" | the wearable's "I'm OK" also cancels the resident's open possible-fall alert |
| Back in bed | the alert is resolved automatically (`system`) and caregivers and nurses get a follow-up email |
| Bedroom sensor offline | no possible-fall check on stale data; A4a's "Device offline" alert covers it |
| Live-check test mode (2026-11-04) | a developer can put one bedroom unit in test mode (a registry item, criterion 3a): for that unit only, any time counts as night and the wait is 2 minutes. It can only make that unit more sensitive, and it expires by itself 2 hours after it was set |

## Acceptance criteria

### Detection (`watchdog`)

1. Each run also Queries the latest `by-message` index for `watch_type = bed` and
   `watch_type = motion` (pattern 5, eventually consistent, paginated), alongside the
   A4a reads. The resident's wearables come from the same registry `by-type` read
   (assigned devices with a `wearable-` ID), their newest `status` from the A4a
   `status` read.
2. A bedroom unit's **event time** for an entry is its `ts`, or its `received_at` if
   the entry has `clock_skew = true` (a wrong device clock must not move the night
   window or the 20 minutes).
3. For each **assigned** bedroom unit that is **not offline** (A4a criterion 4), raise
   `possible_fall` when all hold:
   - its newest `bed` entry has `occupied = false`; call its event time `vacated_at`;
   - `vacated_at` is between 22:00:00 and 06:59:59.999 SGT (on any date);
   - `now − vacated_at ≥ 20 min` (20 min exactly counts);
   - its newest `motion` entry has `motion = false`, or there is no `motion` entry
     (no evidence of movement);
   - none of the resident's assigned wearables has a newest `status` with
     `worn = true` and `latest_ts` within 15 minutes of now.
   A unit with no `bed` entry is not checked.
3a. **Test mode** (live checks only). Each run also Queries the registry `by-type`
    index for `entity_type = TEST_MODE` (usually no items). A test-mode item is
    `pk = DEVICE#<bedroom id>`, `sk = TEST_MODE#<ISO 8601 UTC time it was set>`,
    `entity_type = TEST_MODE`, written by hand in the DynamoDB console (the shared
    developer user can write `silvercare-*` items). For that unit only, while the time
    in `sk` is at most 2 hours before now: the night window check is skipped and the
    wait is **2 minutes** instead of 20. Every other condition (assigned, not offline,
    bed empty, motion cleared, wearable not worn or unknown) is unchanged. An item
    with an unreadable or expired time, a future time, or a `pk` that isn't a bedroom
    unit is ignored. The alert is otherwise identical and also carries
    `details.test_mode = true`; emails add a line `Test mode: yes` so nobody mistakes
    it for a real night-time alert. The run summary log counts `test_mode_units`.
4. At most one open (`open` index: open or acknowledged) `possible_fall` per bedroom
   unit: if one is open, nothing is sent. Otherwise an alert message (A1 format):
   `alert_type = possible_fall`, `severity = critical`, `device_id` = the bedroom unit,
   `event_ts` = `vacated_at` (data-model: "ts of the bed → occupied: false event"),
   `details` = `{"out_of_bed_since": vacated_at, "wearable": "not_worn" |
   "no_recent_status" | "no_wearable"}`, plus `"test_mode": true` only in test mode (`not_worn` = a recent status with
   `worn = false`; `no_recent_status` = wearable assigned but nothing within 15 min;
   `no_wearable` = none assigned). `alert_id` uses the usual formula, so every run
   computes the same ID for the same vacate event. `source_message_id` = the `bed`
   entry's `message_id`.
5. Possible falls are never grouped (A4a criterion 8a counts only offline alerts).
6. The run summary log gains `possible_fall` counts (raised, resolved); per-alert
   lines as A4a (IDs only). No times, `details`, or worn state in logs.

### Resolving when back in bed

7. For each open (`open` or `acknowledged`) `possible_fall` whose bedroom unit's
   newest `bed` entry has `occupied = true` with an event time after the alert's
   `out_of_bed_since`, the watchdog sends a `status_update` `system` / `resolved`
   (A4a criterion 10 format), with a new optional field `reason = "back_in_bed"`
   (only on a `system` update; `message.py` rejects it anywhere else). Nothing else
   resolves a possible fall automatically (not unassignment, not the unit going
   offline: a person closes those).
8. `alert-processor` applies it as A4a criterion 11 (conditional on open or
   acknowledged, sets `resolved_at`, `resolved_by_role = system`, removes `open_pk`,
   `expires_at` + 30 days). **Unlike device problems**, a resolve with
   `reason = back_in_bed` sends a follow-up to the resident's caregivers
   (`silvercare-alerts`, `resident_id` attribute) and, if the item has `escalated_at`,
   the nurses (`silvercare-nurses`), then `status_notified_at` (retry rule as
   alert-cancel). Subject `SilverCare update: Possible fall - <resident_id> resolved`.
   First line: `The alert below was resolved at <changed_at> SGT: the resident is back
   in bed.`, then the usual lines.

### `alert-processor`

9. New alert type `possible_fall`: severity must be `critical`; `details` validated
   (`out_of_bed_since` timestamp; `wearable` one of the three values; optional
   `test_mode`, only `true`; unknown fields rejected). Label `Possible fall`.
10. Routing: a critical health alert (alert-routing criterion 1: caregivers and nurses
    at once, no escalation check). **No repeat check** (like falls: each vacate event
    is a separate emergency), no fall enrichment (`latest_vitals` is for wearable
    falls).
11. Emails (caregiver and nurse, alert-routing criteria 6 and 8): the usual body with
    `Device: <bedroom id>` and two lines after `When:`: `Out of bed since: <time>
    SGT` and `Wearable: not worn` / `Wearable: no recent status` / `Wearable: none
    assigned`, plus `Test mode: yes` when `details.test_mode` is true. No readings.
12. Role/type rule (A4a criterion 14a): `possible_fall` is a health alert: a nurse
    acknowledge and a resident or caregiver cancel are allowed; plus the `system`
    resolve (only with `reason = back_in_bed`). Ops and caregiver acknowledges get
    `not_allowed`.
13. "I'm OK" (resident `cancelled`, device target, wearable): the target Query also
    cancels the resident's open or acknowledged `possible_fall` alerts (any device
    of that resident), as well as the wearable's own health alerts. Follow-up as
    alert-cancel. Implementation: the pattern 24 filter becomes
    `(device_id = :d AND NOT alert_type IN (<device problems>)) OR alert_type =
    :possible_fall`, with `#status IN (:open, :acknowledged)`.

### IAM and Terraform

14. No new resources and no index change: the watchdog already has Query on the
    registry `by-type` and latest `by-message` indexes, SendMessage, and
    alert-processor has both topics. Only Lambda code updates.

### Live check support

15. `scripts/iot-smoke-test.py --possible-fall` (bedroom units only): after the
    checks, publishes `bed` `occupied = false`, then (3 s later) `motion = false`.
    It prints, before publishing, the exact test-mode item to create in the DynamoDB
    console (`silvercare-registry` → Create item → JSON view), with `sk` set to the
    current UTC time, e.g.
    `{"pk": {"S": "DEVICE#bedroom-0003"}, "sk": {"S": "TEST_MODE#2026-11-04T06:00:00.000Z"}, "entity_type": {"S": "TEST_MODE"}}`,
    and reminds the user: use a resident whose wearable the simulator isn't sending
    for (no status for 15 min counts as not worn) or who has none. Expect, about
    2–7 minutes later (2 minutes plus the next watchdog run), "SilverCare CRITICAL
    alert: Possible fall - <resident>" with `Test mode: yes` to caregivers and nurses.
    Without the item, the real rule applies (night only, 20 minutes). Then `--back-in-bed` (bedroom only)
    publishes `bed` `occupied = true`: within 5 minutes, "SilverCare update: Possible
    fall - <resident> resolved" to the same people, and then delete the test-mode
    item (it also expires by itself after 2 hours). `make smoke-test …
    POSSIBLE_FALL=1` / `BACK_IN_BED=1` (main session: Makefile). The smoke test needs
    no AWS credentials: the user creates the item.

### Docs

16. `docs/data-model.md`: alert type `possible_fall` and its `details`; pattern 5 now
    reads `bed` and `motion` (watchdog); the `TEST_MODE` registry item and the
    pattern 4-style Query that reads it (`entity_type = TEST_MODE`); pattern 24's new
    filter; `reason` on system
    status updates; role/type table row. `docs/metrics.md` "Possible fall" bullet:
    built, with the rule. (Main session: README "Alerts" live check, decision record
    0001.)

### Tests

17. Unit tests: each condition at its boundary (20 min exactly alerts, 19:59 doesn't;
    vacated 21:59:59 / 22:00 / 06:59:59 / 07:00 SGT; alert firing after 07:00 for a
    06:50 vacate); motion true blocks, no motion entry alerts; worn true within 15 min
    blocks (and at exactly 15 min / older doesn't); several wearables, one worn,
    blocks; no wearable → `no_wearable`; skewed entry uses `received_at`; offline
    bedroom unit skipped; unassigned skipped; one open per unit; same `alert_id`
    across runs; back in bed resolves (open and acknowledged) and sends the follow-up
    to caregivers and, with `escalated_at`, nurses; back in bed with an earlier event
    time than `out_of_bed_since` doesn't resolve; `reason` validation; alert-processor
    routes it to caregivers and nurses at once without a repeat check; email lines;
    role/type rule; "I'm OK" cancels a possible fall from the bedroom unit of the same
    resident and not another resident's; logs carry no times or worn state;
    `--possible-fall` / `--back-in-bed` payloads and the printed test-mode item;
    test mode: any time of day counts, 2 minutes exactly alerts and 1:59 doesn't,
    the other conditions still apply, the item expires at exactly 2 hours, future /
    unreadable times and non-bedroom `pk` ignored, only the named unit affected,
    `test_mode` in details and the `Test mode: yes` email line.

## Data

- Reads per run: two more `by-message` Queries (`bed`, `motion`: ~20 items each) and
  one `by-type` Query for `TEST_MODE` (normally empty), within provisioned capacity.
- New item: `DEVICE#<bedroom id>` / `TEST_MODE#<time>` in the registry, created and
  deleted by hand for live checks; no personal data.
- Writes: alerts via the queue (rare).

## IoT

None (uses existing `bed`, `motion`, `status` messages).

## API contract

None. Caregiver cancel and nurse acknowledge work as for other health alerts.

## Screens

None.

## Alerts and notifications

| Trigger | Caregivers | Nurses |
|---|---|---|
| bed empty ≥ 20 min (vacated 22:00–07:00), no motion, wearable not worn / unknown | email + in-app, at once | email + in-app, at once |
| back in bed | "resolved: back in bed" email | same |
| "I'm OK", caregiver cancel | "cancelled" follow-up | same (they were emailed) |
| nurse acknowledge | "acknowledged by the care centre nurse" | — |

## Privacy

- Data touched: when a resident got out of and back into bed at night, bedroom
  motion, and whether the wearable was worn: sensitive routine information. Emailed
  only to the resident's caregivers and the nurses, with the opaque resident ID; no
  names, no readings.
- Retention: like other alerts (30 days after resolve or cancel); S3 archive per Open
  decision #4. An open or acknowledged possible fall has no expiry until a person
  closes it (or the resident is back in bed): resolve must exist (Track B) and S3
  retention must be decided before real data arrives.
- A back-in-bed resolve only uses the bedroom unit while it is still assigned to the
  alert's resident (after a reassignment the bed data is someone else's), and an "I'm
  OK" cancel looks up the wearable's resident without the 5-minute cache.
- Must not be logged: bed and motion times, worn state, `details`.

## Cost

About US$0: two more small Queries per watchdog run, rare alerts and emails.

## Out of scope

- Using the hallway/bathroom (no sensors) or a second bedroom unit.
- Daytime possible falls.
- Learning a resident's usual night pattern.
- Simulator scenarios (Track C).

## Open questions

None.
