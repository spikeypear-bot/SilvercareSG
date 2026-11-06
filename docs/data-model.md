# Data model

Status: **approved** 2026-10-25, except the parts marked *(proposed)*, which are decided
with the resident pages feature:

- resident profile fields beyond `display_name` and `home_id`: `year_of_birth`,
  `medical_notes`, `emergency_contacts`, `photo_key`
- [Photos](#photos) (patterns 19 and 20)
- what caregiver users see (the first paragraph of
  [Access patterns](#access-patterns))
- `escalates_at` on alerts: computed by the API (not stored) from escalation's 300 s
  check on warnings, for the portal's countdown
- who edits thresholds: [Resident thresholds](#resident-thresholds) and pattern 26
- [Threshold change history](#threshold-change-history) (pattern 27)
- the staff devices view reading patterns 4, 5, and 8
- recording a resident's date of death: a gap, with no field or access pattern yet
  (see [Threshold change history](#threshold-change-history))
- the S3 history retention figures (90 / 365 days) and daily summaries (see
  [S3 history](#s3-history) and [Retention](#retention))

DynamoDB holds the **live state** the portal and the backend Lambdas read: the device
registry, the latest 5 messages per device and message type, and alerts. It does not
keep history. Every message and alert also goes to S3 for analysis; see
[S3 history](#s3-history).

Built from `docs/metrics.md` and `docs/decisions/0001-iot-rule-routing.md`. Change this
doc first, then the Terraform.

## Decisions so far

- **Latest 5 of every message type.** For each device, DynamoDB keeps at most the 5
  newest messages of each type (`vitals`, `vitals_alert`, `fall`, `alert_cancel`,
  `status`, `motion`, `bed`). Storage
  stays bounded no matter how long the system runs.
- **Provisioned capacity inside the AWS always-free tier.** The always-free tier covers
  25 GB of storage and 25 read + 25 write capacity units of *provisioned* capacity per
  account and region, shared by all tables and indexes. On-demand requests are not
  covered. The account is on the AWS free plan with US$120 of credits that must last
  until the end of November 2026. (Check the current terms under Billing → Free Tier.)
- **The staff dashboards (nurse and doctor) show every resident's latest vitals**, by
  polling the API every 30 s (pattern 5).
- **Fixed capacity, no auto scaling.** Auto scaling adds CloudWatch alarms (a small
  monthly cost) and could scale past the free tier. See [Capacity](#capacity).

## Tables

| Table | Holds | Keys | Indexes | Stream | TTL |
|---|---|---|---|---|---|
| `silvercare-registry` | residents, device assignments, user ↔ resident links, threshold changes *(proposed)* | `pk`, `sk` | `by-type`, `by-resident` | no | no; *(proposed)* `expires_at`, set only on threshold changes once a date of death is recorded |
| `silvercare-latest` | latest 5 messages per device and message type | `pk`, `sk` | `by-message` (sparse) | no | no |
| `silvercare-alerts` | alerts | `pk`, `sk` | `open` (sparse) | no | `expires_at` |

All keys are strings. Key values use opaque IDs only (`res-0003`, `wearable-0007`, a
Cognito `sub`), never names or other personal data.

## Access patterns

*(proposed)* What each role sees: caregivers see the same things for the residents they
are linked to, including raw vitals, alert `details`, the nurse's acknowledge note
(`ack_note`), and who acknowledged, cancelled, or resolved an alert. An acknowledge is
shown as "the care centre nurse" (a shared nurse account, so no name); the API turns
`cancelled_by` (caregiver `sub`) / `resolved_by` (staff `sub`) into the user's name
from Cognito; the `sub` itself is not returned.

**Actor names:**

- **Alerts** (docs/features/alert-cancel.md, alert-routing.md): `acknowledged_by` (the
  shared nurse account's `sub`), `cancelled_by` (caregiver), and `resolved_by` (staff)
  store only the `sub`; the user's name is never stored on the alert. The API returns
  no acknowledger (the portal shows "the care centre nurse"), looks the canceller's and
  resolver's names up in Cognito when it returns the alert, and never returns a `sub`.
  Once that account is deleted (90 days after the user's offboarding), it returns
  "Former caregiver" or "Former staff member" instead (decided 2026-11-04). The shared
  nurse account's Cognito `name` is "Care centre nurse" (decided 2026-11-04), so a
  resolve by the nurse is shown as "Care centre nurse" with no special case.
- **Threshold changes** (patterns 26, 27; decided 2026-11-01): `updated_by` and
  `changed_by` store the staff `sub` (for audit) **and** the name, copied from Cognito
  when the change is written (`updated_by_name`, `changed_by_name`), so the change
  history keeps the name after the account is deleted. The name is as it was at the
  time; a later rename doesn't change past records. The `sub` is never returned.

Caller groups (Cognito): **staff** means the `doctor` and `nurse` groups and can read every resident. **caregiver** can read a
resident only after the link check (pattern 13) passes, and the API also checks the
caller's current Cognito group, because `relationship` on the link is a copy that can
go stale. The `family` group is removed (docs/features/alert-routing.md, 2026-11-04):
family members get caregiver accounts. Until Track B deletes the group, the API refuses
it (`403`). Lambdas without a caller are marked *backend*.

**ops** (Cognito group `ops`, created by Track B; docs/features/watchdog.md) sees **only
device problems**, for every resident: pattern 9 (and 12 for one resident) filtered
server-side to `alert_type IN (device_offline, battery_low, not_worn)` (a
`FilterExpression` in the API, never in the browser), with a response built from only
`pk` (as `resident_id`), `sk`, `alert_id`, `alert_type`, `severity`, `device_id`,
`event_ts`, `status`, `details`, `acknowledged_at` / `acknowledged_by_role` /
`resolved_at` / `resolved_by_role` (`acknowledged_by` only as "the ops team" or the
caregiver's name, as for the other roles). ops get `403` on health alerts and on the
resident profile, photo, and latest-readings patterns (8, 10, 11, 12 for health alerts,
18, 19) unless Track B later agrees otherwise. Nurses see device problems in the portal
through pattern 9 (staff already read every alert); they get no device-problem email.

| # | Who / what | Query | Table / index | Key condition | Frequency |
|---|---|---|---|---|---|
| 1 | `telemetry`, `critical-alert` | Which resident is this device assigned to? | registry | GetItem `pk = DEVICE#<device_id>`, `sk = ASSIGNMENT` | `critical-alert`: every `fall` / `vitals_alert` / `alert_cancel`; `telemetry`: every `vitals` whose newest two entries are at most 3 min apart (cached 5 min per Lambda container; `alert_cancel` always reads it uncached, docs/features/possible-fall.md). `device-health` reads no registry |
| 2 | `telemetry` | This resident's vitals thresholds | registry | GetItem `pk = THRESHOLDS#<resident_id>`, `sk = THRESHOLDS` | as pattern 1 for `vitals`, assigned devices only (cached 5 min, including "no item") |
| 3 | `telemetry`, `device-health`, `critical-alert` | Add a message to the latest 5 for its device and type | latest | GetItem + conditional PutItem `pk = DEVICE#<device_id>`, `sk = MSG#<message>` | ~0.5 / s |
| 4 | backend: `watchdog` (docs/features/watchdog.md); *(proposed)* staff only: devices view | Every device, assigned or not (the watchdog keeps only assigned ones: `resident_id` present) | registry `by-type` | Query `entity_type = DEVICE` (watchdog: eventually consistent, paginated) | every 5 min; per devices-view load |
| 5 | backend: `watchdog`; staff only: nurse and doctor dashboards, *(proposed)* devices view (`status` only) | Newest `vitals` / `status` / `bed` / `motion` of every device | latest `by-message` | Query `watch_type = <message>`, paginated (watchdog: `status` for device problems; `bed`, `motion` with the possible-fall check, A4b; dashboard: all four) | every 5 min; every 30 s per open dashboard (polling) |
| 6 | backend: `alert-processor` | Write an alert exactly once | alerts | PutItem with `attribute_not_exists(pk)` | per alert |
| 7 | backend: `alert-processor` | This resident's recent alerts, to hold back a repeat (same `alert_type` and `severity`, and for `unassigned` the same `device_id`, within 30 min; never falls) | alerts | Query `pk = RESIDENT#<resident_id>`, `sk BETWEEN "ALERT#<event_ts − 30 min>" AND "ALERT#<event_ts>#~"` (`~` sorts after every `alert_id`, so alerts at the same `event_ts`, including this alert's own item on a retry, are returned), strongly consistent | per non-fall alert message |
| 8 | Staff only: nurse and doctor dashboards, *(proposed)* devices view (names for assigned devices) | All residents | registry `by-type` | Query `entity_type = RESIDENT` | every 30 s per open dashboard |
| 9 | Staff only: nurse and doctor dashboards; backend: `watchdog` | All open alerts (and acknowledged ones, which keep `open_pk`), newest first | alerts `open` | Query `open_pk = OPEN`, descending (watchdog: eventually consistent, paginated; it uses only the three device-problem types and `possible_fall`, to keep one open alert per device and type and to find the ones to resolve) | every 30 s per open dashboard; every 5 min |
| 10 | Resident page: staff, or caregiver after 13 | This resident's devices | registry `by-resident` | Query `gsi1_pk = RESIDENT#<resident_id>`, `begins_with(gsi1_sk, "DEVICE#")` | per page view |
| 11 | Resident page: staff, or caregiver after 13 | Latest 5 of every message type for those devices | latest | BatchGetItem (per device: wearable `vitals`, `vitals_alert`, `fall`, `status`; bedroom `motion`, `bed`, `status`) | per page view |
| 12 | Resident page: staff, or caregiver after 13 | This resident's alerts | alerts | Query `pk = RESIDENT#<resident_id>`, descending | per page view |
| 13 | API, caregiver | Is this user linked to this resident? | registry | GetItem `pk = USER#<sub>`, `sk = RESIDENT#<resident_id>` | every caregiver request for a resident |
| 14 | API, caregiver | Which residents can this user see? | registry | Query `pk = USER#<sub>`, `begins_with(sk, "RESIDENT#")` | caregiver home page |
| 15 | *(not used yet)* backend: `alert-processor` | Who is linked to this resident (to notify) | registry `by-resident` | Query `gsi1_pk = RESIDENT#<resident_id>`, `begins_with(gsi1_sk, "USER#")` | per new alert. Not used by alerts-core: it notifies through SNS email subscriptions whose filter policy matches `resident_id`, set in Terraform variables (docs/features/alerts-core.md, criteria 21 and 24), because SNS can only email confirmed subscribers |
| 16 | Health alerts: acknowledge `nurse` only, any resident; cancel caregiver after 13. Device problems (docs/features/watchdog.md): acknowledge `ops`, any resident, or caregiver after 13; no cancel. Resolve: staff only (docs/features/alert-routing.md; `doctor` neither acknowledges nor cancels); device problems are resolved by the `watchdog` | Acknowledge, cancel, or resolve an alert | alerts | Acknowledge and cancel: the API sends a `status_update` to the alerts queue and `alert-processor` applies it (pattern 25; see [Status updates](#status-updates)). Resolve: UpdateItem `pk`, `sk`, condition `attribute_exists(pk)`; the watchdog's resolve is a `status_update` too (`system` / `resolved`) | per action |
| 17 | Staff only, seed script | Add or edit a resident, assign a device, link a user | registry | PutItem / UpdateItem / DeleteItem | rare |
| 18 | Resident page: staff, or caregiver after 13 | This resident's profile and thresholds | registry | BatchGetItem `RESIDENT#<resident_id>` / `PROFILE` and `THRESHOLDS#<resident_id>` / `THRESHOLDS` | per page view |
| 19 | Resident page: staff, or caregiver after 13 | This resident's photo | S3 `silvercare-photos-<account_id>` | API returns a presigned GET URL for `photo_key`, valid 5 min | per page view |
| 20 | Staff only | Upload or replace a photo | S3 | API returns a presigned POST (JPEG only, max 2 MB) | rare |
| 21 | backend: `alert-processor` | A wearable's newest vitals, to add to a fall alert | latest | GetItem `pk = DEVICE#<device_id>`, `sk = MSG#vitals` (uses `entries[0]`) | per fall alert |
| 22 | backend: `alert-processor` | Re-read an alert that already exists (a redelivered message) | alerts | GetItem `pk = RESIDENT#<resident_id>`, `sk = ALERT#<event_ts>#<alert_id>`, strongly consistent | per redelivered alert message |
| 23 | backend: `alert-processor` | Escalation (warnings from assigned devices): mark the check queued; 300 s later read the alert's `status` and mark it escalated (docs/features/escalation.md, alert-routing.md). Critical and unassigned alerts only `SET escalated_at` after the nurse email sent at once | alerts | UpdateItem `SET escalation_queued_at` with `attribute_not_exists(escalation_queued_at)`; GetItem `pk = RESIDENT#<resident_id>`, `sk = ALERT#<event_ts>#<alert_id>`, strongly consistent; UpdateItem `SET escalated_at` with `attribute_not_exists(escalated_at)` | per stored alert (one GetItem, up to two UpdateItems) |
| 24 | backend: `alert-processor` | Resident cancel: this resident's alerts from one device that are `open` or `acknowledged` (or already `cancelled` but without `status_notified_at`, a retry), up to the press, except device problems, plus the resident's possible falls from any device (docs/features/alert-cancel.md, criterion 8; alert-routing.md, criterion 10; watchdog.md, criterion 14a; possible-fall.md, criterion 13) | alerts | Query `pk = RESIDENT#<resident_id>`, `sk <= "ALERT#<changed_at>#~"` (alerts at exactly `changed_at` included, later ones not), strongly consistent, `FilterExpression` `((device_id = :device AND NOT alert_type IN (:offline, :battery, :not_worn)) OR alert_type = :possible_fall) AND (#status IN (:open, :acknowledged) OR (#status = :new AND attribute_not_exists(status_notified_at)))` (the `OR alert_type = :possible_fall` only for a wearable target, which every resident cancel is), paginated; then per target a conditional UpdateItem (`#status IN (:open, :acknowledged)`) and `SET status_notified_at` with `attribute_not_exists(status_notified_at)` | per `alert_cancel` (rare) |
| 25 | backend: `alert-processor` | Acknowledge or cancel of one alert (a `status_update` from Track B's API: nurse or caregiver for health alerts, ops or caregiver for device problems), or the watchdog's resolve of a device problem | alerts | GetItem `pk = RESIDENT#<resident_id>`, `sk = ALERT#<event_ts>#<alert_id>`, strongly consistent; then the role/type rule (else `not_allowed`); conditional UpdateItem (acknowledge `#status = :open`; cancel and resolve `#status IN (:open, :acknowledged)`); `SET status_notified_at` with `attribute_not_exists(status_notified_at)` (not for a resolve, which emails nobody) | per acknowledge, caregiver cancel, or resolve |
| 26 | *(proposed)* Doctor only: resident page | Change this resident's thresholds, and record the change (27) | registry | TransactWriteItems: UpdateItem `pk = THRESHOLDS#<resident_id>`, `sk = THRESHOLDS`, condition `attribute_exists(pk)` and `updated_at` unchanged since the read; PutItem of the change | rare |
| 27 | *(proposed)* Resident page: staff only | This resident's threshold changes, newest first | registry | Query `pk = THRESHOLD_LOG#<resident_id>`, descending | per page view |
| 28 | backend: `watchdog` | `worn_false_since` of the wearables whose newest `status` has `worn = false` (docs/features/watchdog.md, criterion 2) | latest | BatchGetItem `pk = DEVICE#<device_id>`, `sk = MSG#status` (up to 100 keys per call, `UnprocessedKeys` retried), eventually consistent, projecting `pk`, `worn_false_since` | every 5 min, only for wearables off the wrist |
| 27 | backend: `watchdog` (docs/features/possible-fall.md, criterion 3a) | Bedroom units in possible-fall test mode | registry `by-type` | Query `entity_type = TEST_MODE` (as pattern 4), eventually consistent, paginated, projecting `pk`, `sk`; usually returns nothing | every 5 min |

No pattern uses `Scan`.

Who may call pattern 16 is set by its row (docs/features/alert-routing.md, 2026-11-04):
acknowledge is `nurse` only, cancel is a linked caregiver only, and resolve is open to
doctors and nurses alike (decided 2026-11-01). That nurses resolve alerts is workflow,
shown only in the UI (the doctor dashboard and the resident page show doctors no
acknowledge or resolve buttons). `attribute_exists(pk)` stops a resolve for a mistyped
or expired alert from creating an item; an acknowledge or cancel goes through
`alert-processor`, which only updates an `open` (or, for a cancel, `acknowledged`)
alert (pattern 25).

*(proposed)* **Devices view** (patterns 4, 5, and 8, staff only): the route returns
exactly, per device, `device_id`, `device_type`, `online`, `last_seen` (the newest
`status` `ts`), `battery_pct`, `charging`, `fw_version`, and `assigned_to`
(`resident_id`, `display_name`, `home_id`, or null for a spare). It looks up names only
for the `resident_id`s on DEVICE items, so residents with no device aren't returned, and
it drops everything else the indexes project: `photo_key` (pattern 8), and `worn`,
`received_at`, `clock_skew`, and `message_id` from `latest` (pattern 5). `worn` shows
when a resident took the wearable off, which this view doesn't need. A route test checks
the exact response keys. Pattern 4 doesn't project `assigned_at`, so the route can't
drop status entries older than the assignment: it relies on reassignment being one
transaction (see [Device assignment](#device-assignment)). Adding `assigned_at` to the
`by-type` projection would replace that index, so it's decided when the route is built.

## `silvercare-registry`

Low traffic: written by staff and the seed script, read by everything else.

### Resident profile

*(proposed: fields beyond `display_name` and `home_id`)*

Visible to staff and to every caregiver linked to the resident.
Only staff can edit it.

| Attribute | Example | Purpose / notes |
|---|---|---|
| `pk` | `RESIDENT#res-0003` | |
| `sk` | `PROFILE` | |
| `entity_type` | `RESIDENT` | `by-type` hash key |
| `resident_id` | `res-0003` | opaque |
| `display_name` | `Test Resident C` | identify the resident in the portal |
| `home_id` | `home-0011` | opaque; which home the devices are in. No address is stored |
| `year_of_birth` | `1941` | show age. Year only, not a full date of birth |
| `medical_notes` | `Synthetic note: uses a walking frame.` | care context for staff and caregivers. Free text, max 2,000 characters |
| `emergency_contacts` | `[{"name": "Test Contact C1", "relationship": "daughter", "phone": "+65 0000 0001"}]` | who to call in an emergency. Max 2 contacts |
| `photo_key` | `photos/7d0e4b6a-0000-4000-8000-000000000003.jpg` | S3 object key of the photo; absent if none. See [Photos](#photos) |
| `created_at`, `updated_at` | `2026-10-18T03:00:00Z` | |

- Medical notes are free text, so the edit form warns staff not to enter NRIC numbers
  or other ID numbers.
- Emergency contacts are third parties without portal accounts. Staff record them only
  with the contact's agreement.

### Resident thresholds

Kept in its own partition so the `telemetry` Lambda can read the limits but never the
medical notes or contacts. IAM can only restrict access by **partition** key
(`dynamodb:LeadingKeys`), not by sort key, so the `telemetry` role is limited to
partitions starting with `DEVICE#` and `THRESHOLDS#`. The `critical-alert` role gets
`DEVICE#` only; `device-health` gets no registry access.

| Attribute | Example | Notes |
|---|---|---|
| `pk` | `THRESHOLDS#res-0002` | |
| `sk` | `THRESHOLDS` | |
| `hr_bpm` | `{"min": 50, "max": 120}` | integers, each 20–250 |
| `spo2_pct` | `{"min": 88}` | integer, 50–100. No max |
| `skin_temp_c` | `{"min": 30.0, "max": 38.0}` | numbers, each 20.0–45.0 |
| `updated_at`, `updated_by` | `2026-10-18T03:00:00Z`, staff `sub` | absent on seed items. `updated_by` is never returned (see Actor names) |
| `updated_by_name` | `Test Doctor A` | copied from Cognito at write time; returned to staff only; absent on seed items |

Every attribute and limit is optional (docs/features/vitals-alerts.md, criteria 5-6):

- **Defaults** (user's decision, 2026-11-02; synthetic, to be checked with a clinician
  before any real use): heart rate below 50 or above 120 bpm, SpO2 below 92 %, skin
  temperature below 30.0 or above 38.0 °C.
- **Resolution:** each limit present and valid (a number in the metric's valid range
  from `docs/metrics.md`, an integer for `hr_bpm` and `spo2_pct`) replaces that
  default; a missing or invalid limit keeps it. If a metric's resolved `min` is not
  below its `max`, both of its limits keep the defaults. `telemetry` logs an invalid
  limit with `resident_id` and the field name, never the value. Results, including
  "no item", are cached per Lambda container for 5 minutes, so an edit takes effect
  within 5 minutes. Unassigned devices always use the defaults.
- **Comparison:** a value is abnormal when it is strictly below `min` or strictly
  above `max` (`hr_bpm` 50 and 120 are normal). `null` is never abnormal.
- The seed script creates `THRESHOLDS#res-0002` with `spo2_pct {"min": 88}` (a
  synthetic resident with a lower normal SpO2).

*(proposed)* The resident page uses this shape: `hr_bpm` has `min` and `max`, `spo2_pct`
only `min`, `skin_temp_c` `min` and `max`, so a reading below a `min` or above a `max`
is outside the limits (strictly, as above). Only doctors change them (pattern 26). The API:

- checks that the `cognito:groups` claim of the caller's access token includes
  `doctor`, so a doctor removed from the group can still change thresholds until the
  token expires (up to 60 minutes). This is a deliberate trade-off (decided
  2026-11-01): faster revocation would need token revocation (signing the user out
  everywhere, which stops refresh) plus shorter access-token lifetimes;
- accepts exactly `hr_bpm.min`, `hr_bpm.max`, `spo2_pct.min`, `skin_temp_c.min`, and
  `skin_temp_c.max`, rejects any other key (including `updated_by`, `updated_by_name`, `updated_at`) with
  400, and builds the item itself rather than copying the body;
- rejects values outside the `vitals` valid ranges in `docs/metrics.md` (heart rate
  20–250, SpO2 50–100, skin temperature 20.0–45.0), a `min` that isn't below its
  `max`, decimals in heart rate or SpO2, or more than 1 decimal place in skin
  temperature;
- sets `updated_by` from the token's `sub`, `updated_by_name` from that user's Cognito
  `name`, and `updated_at` from the server clock;
- updates only an existing item (`attribute_exists(pk)`), so it can't create
  thresholds for a mistyped or offboarded resident; a failed condition returns 404;
- records the change in the same transaction (see
  [Threshold change history](#threshold-change-history)).

On read (pattern 18) the API returns each limit as resolved above (the item's value or
the default) and the stored `updated_by_name` (alert actors, by contrast, are looked up
on read; see Actor names); the `sub` itself is not
returned. Nurses see the thresholds read-only. The frontend demo uses the defaults
above, and `spo2_pct {"min": 88}` for `res-0002`, as the seed script creates.

### Threshold change history

*(proposed)* Who changed which resident's thresholds, when, and the old and new values
(pattern 27). One item per save. Its own partition, so the `telemetry` role (limited to
`DEVICE#` and `THRESHOLDS#` partitions) can't read who changed what.

| Attribute | Example | Notes |
|---|---|---|
| `pk` | `THRESHOLD_LOG#res-0011` | |
| `sk` | `CHANGE#2026-10-20T03:00:00Z#9b2f0c1e-0000-4000-8000-000000000001` | `CHANGE#<changed_at>#<change_id>`, so a Query sorts by time |
| `change_id` | `9b2f0c1e-0000-4000-8000-000000000001` | UUID v4 |
| `changed_at` | `2026-10-20T03:00:00Z` | server clock, same as the thresholds' new `updated_at` |
| `changed_by` | staff `sub` | from the token, never the request body. Never returned |
| `changed_by_name` | `Test Doctor A` | copied from Cognito at write time, so the history keeps the name after the account is deleted (see Actor names) |
| `changes` | `[{"field": "hr_bpm.max", "old_value": 110, "new_value": 120}]` | only the values that changed; `field` is one of `hr_bpm.min`, `hr_bpm.max`, `spo2_pct.min`, `skin_temp_c.min`, `skin_temp_c.max` |

- **Write path: not implemented.** It lands with the pattern 26 route. The route reads
  the thresholds, then writes the new values and this item in one `TransactWriteItems`,
  with the condition that `updated_at` hasn't changed since the read, so two doctors
  saving at once can't record the wrong old values. A save that changes nothing writes
  no item.
- Read by staff only (nurses and doctors), read-only for everyone. Not shown to
  caregivers.
- Retention (decided 2026-11-01, Open decision #4): the resident's lifetime + 6 years,
  as for electronic patient health records (MOH Circular 85/2022, Licence Conditions on the Retention Periods of Patient Health Records, under the Healthcare Services Act 2020). Offboarding does **not** delete
  them or set `expires_at`. When a date of death is recorded, each item gets
  `expires_at` = date of death + 6 years, and TTL on `silvercare-registry` deletes them
  (enabling TTL on an existing table doesn't replace it). Until then they have no
  expiry.
- *(proposed)* **Gap: recording a date of death.** There is no field or access pattern
  for it yet: the resident profile has no date of death, and no route sets one or the
  `expires_at` that follows from it. Both are needed before this TTL can ever apply;
  until they exist, these items are never deleted automatically.

### Device assignment

One item per device, including spares that aren't assigned to anyone yet.

| Attribute | Example | Notes |
|---|---|---|
| `pk` | `DEVICE#wearable-0007` | device ID = IoT thing name |
| `sk` | `ASSIGNMENT` | |
| `entity_type` | `DEVICE` | `by-type` hash key |
| `device_type` | `wearable` | `wearable` or `bedroom` |
| `resident_id` | `res-0003` | absent when unassigned |
| `home_id` | `home-0011` | absent when unassigned |
| `assigned_at` | `2026-10-18T03:00:00Z` | |
| `gsi1_pk` | `RESIDENT#res-0003` | absent when unassigned |
| `gsi1_sk` | `DEVICE#wearable-0007` | absent when unassigned |

Moving a device to another resident, or unassigning it, is one `TransactWriteItems`:
rewrite this item **and delete the device's items in `silvercare-latest`**. Otherwise
the new resident's page would show the previous resident's readings. As a second
safeguard, the API drops any entry whose `ts` is earlier than `assigned_at`.

### User ↔ resident link

Links a caregiver user (Cognito) to a resident. Names, emails, and phone
numbers stay in Cognito, not here. Staff can see every resident and have no links.

| Attribute | Example | Notes |
|---|---|---|
| `pk` | `USER#3f2b8c1e-0000-4000-8000-000000000001` | Cognito `sub` (synthetic example) |
| `sk` | `RESIDENT#res-0003` | |
| `relationship` | `caregiver` | `caregiver` (the user's Cognito group; `family` is removed, docs/features/alert-routing.md: family members are caregivers) |
| `linked_at`, `linked_by` | `2026-10-18T03:00:00Z`, staff `sub` | |
| `gsi1_pk` | `RESIDENT#res-0003` | |
| `gsi1_sk` | `USER#3f2b8c1e-0000-4000-8000-000000000001` | |

How links are created is Open decision #3 (user onboarding).

### Possible-fall test mode

docs/features/possible-fall.md, criterion 3a. For live checks only: written and
deleted by hand in the DynamoDB console (the shared developer user can write
`silvercare-*` items); `scripts/iot-smoke-test.py --possible-fall` prints the exact
item. No personal data.

| Attribute | Example | Notes |
|---|---|---|
| `pk` | `DEVICE#bedroom-0003` | must be a bedroom unit, else ignored |
| `sk` | `TEST_MODE#2026-11-04T06:00:00.000Z` | when it was set, ISO 8601 UTC with ms and `Z` |
| `entity_type` | `TEST_MODE` | `by-type` hash key (pattern 27) |

While the time in `sk` is at most 2 hours before now (and not in the future), the
watchdog's possible-fall check for that unit only skips the night window and waits 2
minutes instead of 20; every other condition is unchanged. An unreadable, expired, or
future time is ignored, so the item stops working by itself after 2 hours (delete it
anyway). An alert that only test mode allowed has `details.test_mode = true`.

### Indexes

`by-type` puts residents and devices in one index, so the `watchdog` role, which only
needs devices, can also read resident display names through it. This is accepted:
names are the least sensitive field, nothing else from the profile is projected, and
Lambdas never log names.

| Index | Hash | Range | Projection | Serves |
|---|---|---|---|---|
| `by-type` | `entity_type` | `pk` | INCLUDE `display_name`, `home_id`, `photo_key`, `device_type`, `resident_id` | 4, 8, 27, photo cleanup |
| `by-resident` | `gsi1_pk` | `gsi1_sk` | INCLUDE `device_type`, `relationship` | 10, 15 |

## Photos

*(proposed)*

Resident photos are images, so they go in S3 rather than DynamoDB.

- Bucket `silvercare-photos-<account_id>` in `ap-southeast-1`: all public access
  blocked, `BucketOwnerEnforced`, SSE-S3 encryption, TLS-only bucket policy, no
  CloudFront.
- Object key `photos/<uuid>.jpg`: a random UUID, so the key reveals nothing about the
  resident. Objects are tagged `resident_id=<id>` (opaque) for cleanup.
- Versioning **off**, so deleting a photo really deletes it (losing a photo only means
  re-uploading it).
- **Upload** goes through a quarantine prefix, because a presigned POST only checks
  the *declared* content type, not the file:
  1. The API gives staff a presigned POST for one exact key `uploads/<uuid>`, chosen by
     the server (no `starts-with`), limited to `image/jpeg` and 2 MB.
  2. An S3 event triggers the `photo-intake` Lambda. It checks the JPEG header bytes,
     removes every APPn metadata segment (EXIF, including GPS location, which could
     reveal the home address) by walking the JPEG markers with the standard library,
     writes `photos/<uuid>.jpg`, sets `photo_key` on the profile, and deletes the
     upload. Anything that isn't a valid JPEG is deleted and the upload fails.
  3. A lifecycle rule deletes anything left under `uploads/` after 1 day.

  The browser also re-encodes the image before upload, which keeps files small.
- **Viewing:** the API checks access (pattern 13), then builds a presigned GET URL only
  from the stored `photo_key`, never from a key sent by the client. The URL is valid
  for 5 minutes and sets `response-cache-control: no-store`. It is never logged, saved
  in browser storage, or put in a page URL.
- **Deleting** (replacing a photo or offboarding): delete the S3 object first, retrying
  until it succeeds, then update or delete the profile. A daily run of `photo-intake`
  also deletes any `photos/` object whose key no resident's `photo_key` points to (it
  reads `photo_key` from `by-type`), so a failed delete can't leave a photo behind.
- Cost: 20 photos at a few hundred KB is well under 1 cent a month.

## `silvercare-latest`

One item per device and message type: 20 wearables × 5 types + 20 bedroom sensors × 3
types = 160 items, each with at most 5 entries. This is the busiest table.

| Attribute | Example | Notes |
|---|---|---|
| `pk` | `DEVICE#wearable-0007` | |
| `sk` | `MSG#vitals` | `MSG#<message>` from the topic |
| `device_type` | `wearable` | |
| `entries` | list, newest first, max 5 | each entry: `message_id`, `ts`, `received_at`, `clock_skew`, plus only the fields listed below for its type |
| `latest` | copy of `entries[0]` | top-level so the `by-message` index can project it |
| `latest_ts` | `2026-10-18T03:12:00.000Z` | `ts` of `entries[0]` |
| `version` | `412` | optimistic lock, +1 on every write |
| `watch_type` | `vitals` | on `vitals`, `status`, `bed`, and `motion` items; not on `fall`, `vitals_alert`, or `alert_cancel`, which makes `by-message` sparse |
| `worn_false_since` | `2026-11-04T01:00:00.000Z` | wearable `MSG#status` items only (docs/features/watchdog.md, criterion 9): the `ts` of the newest entry since which `worn` has been false. Kept by `device-health` when a wearable `status` becomes the newest entry: `worn` false and none yet → set to that entry's `ts`; `worn` false and set → kept; `worn` true → removed. A status that isn't the newest keeps it as read. Read by the `watchdog` (pattern 26) for the not-worn check. Not in the `by-message` projection |

Example `vitals` item (2 of 5 entries shown):

```json
{
  "pk": "DEVICE#wearable-0007",
  "sk": "MSG#vitals",
  "device_type": "wearable",
  "latest_ts": "2026-10-18T03:12:00.000Z",
  "version": 412,
  "entries": [
    {"message_id": "5f0c9a52-3c1e-4c8e-9f57-0d6f2b7a1e11", "ts": "2026-10-18T03:12:00.000Z",
     "received_at": "2026-10-18T03:12:00.410Z", "clock_skew": false,
     "hr_bpm": 72, "spo2_pct": 97, "skin_temp_c": 33.4, "activity_pct": 12},
    {"message_id": "a1b2c3d4-0000-4000-8000-000000000002", "ts": "2026-10-18T03:11:00.000Z",
     "received_at": "2026-10-18T03:11:00.380Z", "clock_skew": false,
     "hr_bpm": 71, "spo2_pct": 98, "skin_temp_c": 33.3, "activity_pct": 9}
  ],
  "latest": {"…": "same as entries[0]"}
}
```

Stored fields per type (an allow-list, so a new device field is not stored until it is
added here with a purpose):

| Message | Fields kept | Purpose |
|---|---|---|
| `vitals` | `hr_bpm`, `spo2_pct`, `skin_temp_c`, `activity_pct` | recent vitals and activity on the resident page; threshold checks |
| `fall` | `confidence`, `peak_g`, `still_s` | fall details on the resident page |
| `vitals_alert` | `metric`, `value`, `limit`, `direction`, `held_s` | the device's critical readings on the resident page |
| `alert_cancel` | none (envelope only: `message_id`, `ts`, `received_at`, `clock_skew`) | when the resident pressed "I'm OK" (docs/features/alert-cancel.md). Item `MSG#alert_cancel`, no `watch_type` |
| `status` | `battery_pct`, `worn`, `charging`, `fw_version` | device state, offline / low battery / not worn |
| `motion` | `motion` | bedroom state, possible-fall detection |
| `bed` | `occupied` | bedroom state, possible-fall detection |

Left out: `accel_g` (vitals) and `accel_window` (fall). The portal doesn't show raw
acceleration, and `accel_window` (about 1.5 KB) would make every fall write cost about
9 write units.

### Write rule (idempotent)

Messages arrive at least once and sometimes out of order. To add a message:

1. GetItem the item with a strongly consistent read.
2. If an entry with the same `message_id` is already there, stop (duplicate).
3. Insert the entry, sort by `ts` (newest first), and keep the first 5. If the new
   message is older than all 5, stop.
4. PutItem with `attribute_not_exists(pk) OR version = :read_version`, setting
   `version + 1`.
5. If the condition fails (another write got there first), go back to step 1, at most
   3 times.

The newest `status` entry is the device's current state (`last_seen`, battery, worn,
charging). The newest `bed` and `motion` entries are the bedroom's current state. No
separate state item is needed.

### Index

| Index | Hash | Range | Projection | Serves |
|---|---|---|---|---|
| `by-message` | `watch_type` | `pk` | INCLUDE `device_type`, `latest`, `latest_ts` | 5 |

The nurse and doctor dashboards poll this index every 30 s for every device's newest
vitals and state. `fall`, `vitals_alert`, and `alert_cancel` items have no
`watch_type`: they reach the dashboards as alerts (or, for a cancel, as cancelled
alerts).

A write that changes an index entry also needs write capacity on the index. If the
index runs out, DynamoDB throttles writes to the **base table** too, so `by-message`
gets enough write capacity for every vitals write.

## `silvercare-alerts`

| Attribute | Example | Notes |
|---|---|---|
| `pk` | `RESIDENT#res-0003` | |
| `sk` | `ALERT#2026-10-18T03:14:07.360Z#6f1c2e0a-5b3d-5e8f-9a47-000000000001` | `ALERT#<event_ts>#<alert_id>` |
| `alert_id` | `6f1c2e0a-5b3d-5e8f-9a47-000000000001` | UUID v5 of `"<event_ts>\|<alert_type>\|<severity>\|<device_id>"` (docs/features/vitals-alerts.md, criterion 2; items stored before vitals alerts have the older ID without `severity`): deterministic, so retries can't duplicate, and opaque, so the key doesn't reveal the type of health event |
| `alert_type` | `fall` | health alerts: `fall`, `hr_high`, `hr_low`, `spo2_low`, `skin_temp_high`, `skin_temp_low` (docs/features/vitals-alerts.md), `possible_fall` (docs/features/possible-fall.md, raised by the watchdog); device problems: `device_offline`, `battery_low`, `not_worn` (docs/features/watchdog.md) |
| `severity` | `critical` | `critical` (falls, device `vitals_alert`, possible falls) or `warning` (the cloud vitals check; always for device problems). Items stored before vitals alerts have none; they are all falls (critical) |
| `device_id` | `wearable-0007` | |
| `event_ts` | `2026-10-18T03:14:07.360Z` | see below |
| `received_at` | `2026-10-18T03:14:07.410Z` | when the IoT rule received the triggering message (from the alert message) |
| `created_at` | `2026-10-18T03:14:07.900Z` | when `alert-processor` stored the item |
| `status` | `open` | `open`, `acknowledged` (health alerts by a nurse; device problems by ops or a caregiver), `cancelled` (health alerts only: the wearable "I'm OK" cancel, docs/features/alert-cancel.md, or a caregiver in the portal, docs/features/alert-routing.md), `resolved` (device problems: by the watchdog when the problem cleared, docs/features/watchdog.md; possible falls: by the watchdog when the resident is back in bed, docs/features/possible-fall.md). Anything but `open` stops escalation. Only `alert-processor` changes `open` to `acknowledged`, or `open` / `acknowledged` to `cancelled` or (device problems) `resolved`, through a [status update](#status-updates) |
| `source_message_id` | `c2a1d4f0-8b6e-4f0a-a3c9-7e15d2b9f804` | for message-triggered alerts |
| `details` | `{"confidence": 0.91, "peak_g": 3.8, "still_s": 20, "latest_vitals": {"hr_bpm": 72, "spo2_pct": 97, "ts": "2026-10-18T03:13:00.000Z"}}` | small, type-specific. Fall: `confidence`, `peak_g`, `still_s`, plus `latest_vitals` (`hr_bpm`, `spo2_pct`, `ts` of the device's newest vitals entry, values may be `null`; absent if the device has sent no vitals). Vitals warning: `metric`, `value`, `limit`, `direction`, `previous_value`, e.g. `{"metric": "hr_bpm", "value": 128, "limit": 120, "direction": "above", "previous_value": 125}`. Vitals critical: `metric`, `value`, `limit`, `direction`, `held_s` (`hr_low` `value` 0 = no pulse). Device problems (docs/features/watchdog.md, criteria 8, 8a): `device_offline` `{"last_seen": "<ts>"}` plus `"grouped": true` when the watchdog raised 5 or more in one run; `battery_low` `{"battery_pct": 15}` (integer 0-100); `not_worn` `{"since": "<ts>"}`. Possible fall (docs/features/possible-fall.md, criteria 4 and 9): `{"out_of_bed_since": "<event time of the bed → occupied: false entry>", "wearable": "not_worn" \| "no_recent_status" \| "no_wearable"}`, plus `"test_mode": true` only when the alert was allowed by test mode alone (no other fields) |
| `notified_at` | `2026-10-18T03:14:08.300Z` | set once, after the caregiver SNS email was published (device problems too); guards against a second email on redelivery. Absent for unassigned alerts |
| `ops_notified_at` | `2026-11-04T02:00:01.200Z` | device problems only (docs/features/watchdog.md, criterion 13): set once, after the email to the ops topic (`silvercare-ops-devices`) was published, with `attribute_not_exists(ops_notified_at)`. Absent on grouped offline alerts (the watchdog emailed ops one summary instead) |
| `notification` | `skipped_unassigned` | only on unassigned alerts (`pk = RESIDENT#unassigned`): no caregiver is emailed (the nurses are, at once) |
| `escalation_queued_at` | `2026-10-18T03:14:08.350Z` | set once, after the delayed escalation check was sent to the alerts queue (docs/features/escalation.md, criterion 3); guards against queuing a second check on redelivery. Only on warnings from assigned devices (since docs/features/alert-routing.md); absent on critical and unassigned alerts, suppressed alerts, and alerts stored before escalation |
| `escalated_at` | `2026-10-18T03:14:08.700Z` | the nurses were emailed: set once, after the nurse email was published, at once (critical and unassigned alerts) or after the 5-minute wait (warnings, escalation criterion 5); docs/features/alert-routing.md, criterion 2. Guards against a second nurse email. `status` stays `open` |
| `acknowledged_by`, `acknowledged_at` | user `sub`, timestamp | health alerts: only nurses acknowledge (docs/features/alert-routing.md; the nurse account is shared, so emails and the portal say "the care centre nurse"); device problems: ops or a caregiver (docs/features/watchdog.md). Set by `alert-processor` from a `status_update`: `by` and `changed_at`. Kept when the alert is later cancelled or resolved |
| `acknowledged_by_role` | `ops` | device problems only: `ops` or `caregiver`, who acknowledged (watchdog criterion 14a). Emails say "the ops team" for ops (no person's name) and the caregiver's masked name. Absent on nurse acknowledges of health alerts. Kept when resolved |
| `ack_note` | `Synthetic note: ambulance on the way.` | the nurse's optional note at acknowledge: free text, 1-500 characters, newlines allowed. Emailed to the resident's caregivers and shown in the portal to everyone who can see the alert; never logged, not in the S3 archive. Kept when the alert is later cancelled; deleted with the item |
| `cancelled_at` | `2026-11-03T02:00:05.000Z` | the update's `changed_at`: for the resident's "I'm OK" press, when the cloud received it (the `alert_cancel`'s `received_at`, server time, not the device's `ts`); for a caregiver, the API's time. Only on cancelled alerts |
| `cancelled_by_role` | `resident` | who cancelled: `resident` (the wearable button) or `caregiver` (the portal). Only on cancelled alerts |
| `cancelled_by` | caregiver `sub` | the caregiver who cancelled; absent when the resident cancelled. The user's name (`by_name`) is never stored |
| `status_notified_at` | `2026-11-03T02:00:06.100Z` | set once, after the follow-up email(s) for the current `status` (`acknowledged` or `cancelled`) were published, or when nobody is emailed (an unassigned acknowledge) (docs/features/alert-cancel.md, criterion 10); an item with the new `status` but without it gets its follow-up on a retry. A cancel of an acknowledged alert REMOVEs the acknowledge's value, so the cancel's follow-up is tracked on its own |
| `resolved_by`, `resolved_at` | staff `sub`, timestamp | the `sub` is never returned (see Actor names). `resolved_at` is the resolve's `changed_at`. A watchdog resolve (device problems) has no `resolved_by` |
| `resolved_by_role` | `system` | who resolved: `system` (the watchdog, when the device problem cleared, docs/features/watchdog.md, criterion 11, or the resident of a possible fall is back in bed, docs/features/possible-fall.md, criterion 8) |
| `escalates_at` | `2026-10-18T03:19:08.300Z` | *(proposed)* **not stored**: the API adds it to an `open` **warning** from an assigned device that has no `escalated_at` yet, as `notified_at` + 300 s, so the portal can show a countdown to the escalation check (docs/features/escalation.md, alert-routing.md; decided 2026-11-03, delay changed 2026-11-04). Never on critical or unassigned alerts: the nurses are emailed at once and `escalated_at` is set. Approximate: the check runs about 300 s after `escalation_queued_at` |
| `open_pk` | `OPEN` | present until resolved or cancelled (an acknowledged alert keeps it, so the watchdog still sees an acknowledged device problem and resolves it); removing it takes the alert out of the `open` index |
| `expires_at` | `1765000000` | epoch seconds, `resolved_at` + 30 days, or `cancelled_at` + 30 days for a cancelled alert. See [Retention](#retention) |

An alert from a device with no assignment (no `DEVICE#<id>` / `ASSIGNMENT` item in the
registry, or one without `resident_id`) is still stored, with
`resident_id = unassigned`, so `pk = RESIDENT#unassigned` (docs/features/alerts-core.md).
No caregiver is emailed; the nurses are emailed at once, whatever the severity
(docs/features/alert-routing.md).

`event_ts` is the time of the triggering event, so `alert_id` and the key are the same
however many times the same event is processed:

| Alert | `event_ts` |
|---|---|
| fall, device `vitals_alert` | `ts` of the message |
| abnormal vitals (cloud check) | `ts` of the newest of the two abnormal `vitals` entries |
| device offline | the device's last `status` `ts` (same outage → same key on every watchdog run) |
| low battery | `ts` of the newest `status` (the one with the low reading) |
| wearable not worn | the status item's `worn_false_since` |
| possible fall | `ts` of the `bed` → `occupied: false` event, or its `received_at` if the entry has `clock_skew = true` (docs/features/possible-fall.md, criterion 2); also `details.out_of_bed_since` |

**Device problems** (docs/features/watchdog.md): the `watchdog` Lambda, every 5 minutes,
raises `device_offline` (no `status` for more than 15 minutes; a device that never sent
one is not checked), `battery_low` (`battery_pct` below 20, not charging; `null` never),
and `not_worn` (wearable `worn = false`, not charging, `worn_false_since` at least 2 hours
ago, only between 08:00 and 22:00 SGT) for **assigned** devices; an offline device is not
checked for battery or worn. At most one open alert per device and type (pattern 9). It
resolves an open one when it clears (back online, battery at or above 20 or charging,
worn again or charging, or the device is no longer assigned to that resident) with a
`system` / `resolved` [status update](#status-updates). A not-worn alert stays open
overnight until the wearable is worn or charged.

**Possible fall** (docs/features/possible-fall.md): in the same run, the `watchdog`
reads every bedroom unit's newest `bed` and `motion` entry (pattern 5) and the
test-mode items (pattern 27). An entry's event time is its `ts`, or its `received_at`
when `clock_skew = true`. For each assigned bedroom unit that is not offline (a unit
without any `status` is not offline) and has a `bed` entry, it raises a **critical**
`possible_fall` when the newest `bed` entry is `occupied = false` (event time =
`vacated_at`), `vacated_at` is in 22:00:00–06:59:59.999 SGT, `now − vacated_at ≥ 20
min`, the newest `motion` entry is `motion = false` or absent, and none of the
resident's assigned wearables has a newest `status` with `worn = true` less than 15
minutes old. Test mode (pattern 27) skips the night window and makes the wait 2 minutes
for that unit. At most one open possible fall per unit (pattern 9); never grouped. An
open one is resolved by a `system` / `resolved` status update with `reason =
back_in_bed` when the unit's newest `bed` entry is `occupied = true` with an event time
after the alert's `event_ts` (= `out_of_bed_since`); nothing else resolves it
automatically. `alert-processor` routes it as a critical health alert (caregivers and
nurses at once, no repeat check, no fall enrichment).

**Repeats** (docs/features/vitals-alerts.md, criteria 17-20): before storing any alert
except a fall, a possible fall (docs/features/possible-fall.md, criterion 10), or a
device problem, `alert-processor` runs pattern 7 for the 30 minutes up to its
`event_ts`, alerts at the same `event_ts` included. If an alert with the same `alert_type`, the same `severity`, and a
different `alert_id` is there (for `unassigned`, also the same `device_id`), the new
one is **suppressed**: not stored, archived, or emailed, only logged (IDs and outcome
`suppressed`). An earlier alert exactly 30 minutes before doesn't count. A warning
never holds back a critical alert or the other way round, and falls are never held
back. Times come from `event_ts`, not the clock, and an alert already stored is never
suppressed on a retry, so a redelivered message decides the same way. Two different
alerts processed at the same moment may both be emailed (accepted).

Alerts reach this table through the alerts queue: `telemetry`, `critical-alert`, and
`watchdog` send a message to SQS, and `alert-processor` notifies caregivers through SNS,
writes the item here for the nurse and doctor dashboards, and writes the alert to S3 for tracking
(see `docs/decisions/0001-iot-rule-routing.md`). The queue message carries the same
fields as the item, so `alert_id` is computed once, by the detector.

`alert-processor` handles each alert message in this order (docs/features/alerts-core.md,
docs/features/vitals-alerts.md): hold back a repeat (pattern 7, not for falls); add
`details.latest_vitals` for a fall (pattern 21); PutItem with
`attribute_not_exists(pk)` (pattern 6), or, if the item already exists, re-read it
(pattern 22) and use it from there; write it to S3 `alerts/` (see
[S3 history](#s3-history)); then, if the resident is assigned and the item has no
`notified_at`, publish the caregiver email and `SET notified_at` with
`attribute_not_exists(notified_at)`; then route it to the nurses (below). No
`expires_at` until the alert is resolved or cancelled.

Device problems (docs/features/watchdog.md, criteria 12-14) skip the repeat check (the
watchdog keeps one open alert per device and type), the fall enrichment, and the nurse
routing: store and archive as above, then the caregiver email (same as above, then
`notified_at`; not for `unassigned`), then the ops email to `silvercare-ops-devices` (encrypted; `silvercare-ops` carries only alarms) unless
`details.grouped` is true, then `SET ops_notified_at` with
`attribute_not_exists(ops_notified_at)`. Each step is skipped if its timestamp is set; a
failed Publish fails the record. Both emails have the subject `SilverCare device
problem: <label> - <device_id> (<resident_id>)` and a body with the type, device,
resident, and one line (`Last heartbeat:` / `Battery:` / `Not worn since:`), no health
readings. No nurse email, no escalation. The log line has `nurses = none` and `ops =
emailed` or `grouped`.

**Escalation** (docs/features/escalation.md, changed by docs/features/alert-routing.md
on 2026-11-04): last, `alert-processor` routes the alert to the nurses by resident and
the stored item's `severity` (no `severity`, i.e. a fall stored before vitals alerts,
counts as critical):

| Alert | Caregivers (`silvercare-alerts`) | Nurses (`silvercare-nurses`) | Escalation check |
|---|---|---|---|
| assigned, `critical` | at once | at once | none |
| assigned, `warning` | at once | after 5 min if still `open` | `DelaySeconds = 300` |
| `unassigned`, any severity | nobody | at once | none |
| device problem (`device_offline`, `battery_low`, `not_worn`; not routed by this table) | at once (not for `unassigned`) | never (portal only); ops (`silvercare-ops-devices`) at once instead, unless grouped | none |

- **At once** (critical or unassigned): if the item has no `escalated_at`, publish the
  nurse email (the caregiver email plus `Sent to the care centre at once: critical
  alert.` or `... device not assigned to a resident.`), then `SET escalated_at` with
  `attribute_not_exists(escalated_at)`. Order for an assigned critical alert: caregiver
  Publish, `notified_at`, nurse Publish, `escalated_at`, so a redelivery with
  `notified_at` but no `escalated_at` sends only the nurse email, and one with both
  sends nothing. A failed nurse Publish fails the record (SQS retries it).
- **Warnings**: if the item is `open` and has no `escalation_queued_at`,
  `alert-processor` sends an escalation check (`{"kind": "escalation_check",
  "schema_version": 1, "alert_id", "resident_id", "event_ts"}`) to the alerts queue
  with `DelaySeconds = 300`, then `SET escalation_queued_at` with
  `attribute_not_exists(escalation_queued_at)` (pattern 23). When the check arrives it
  reads the alert (pattern 23, strongly consistent, no repeat check): a present
  `status` other than `open` → nothing (a missing `status` escalates); `escalated_at`
  set → nothing; more than 20 minutes after `created_at` (e.g. redriven from the DLQ)
  → nothing, reason `stale`; otherwise it emails the nurses (`Escalated: no response
  within 5 minutes of the first alert.`) and `SET escalated_at` with
  `attribute_not_exists(escalated_at)`. A check that keeps failing is retried every
  180 s, so its 4th try (about 14 minutes) is the last that can still email.

A crash between a send and its update can send a second check or nurse email; a
duplicate is accepted, a missing one isn't. The alert's log line has `nurses` =
`immediate`, `escalation_queued`, or `none` (suppressed).

### Status updates

docs/features/alert-cancel.md, criteria 6-14, changed by docs/features/alert-routing.md
(2026-11-04), criteria 9-15, and docs/features/watchdog.md, criteria 10-11 and 14a. A
third message kind on the `silvercare-alerts` queue (with alert messages and escalation
checks), sent without delay. `critical-alert` sends one for every wearable
`alert_cancel`; the `watchdog` sends one (`system` / `resolved`) when a device problem
clears; **Track B's API** sends one when a nurse acknowledges a health alert, a caregiver
cancels one, or ops or a caregiver acknowledges a device problem. This is the contract
that API uses; it needs only `sqs:SendMessage` on `silvercare-alerts` in its role, and it
doesn't update the alert item itself (so the follow-up email rules live in one place).
The `system` role is sent only by the watchdog, never by Track B's API.

`alert-processor` trusts every `status_update` it receives, so **the API must check
before sending**:

- take `by` (`sub`) and, for a caregiver, `by_name` (`name` claim) only from the
  verified Cognito token, never from the request body; `by_name` is `null` for a nurse;
- a nurse acknowledge needs the caller's current group to be `nurse` (else `403`); a
  caregiver cancel needs `caregiver` (else `403`) and the link to the resident
  (pattern 13, else `404`; never for `unassigned`); `doctor` and `family` get `403`;
- device problems (docs/features/watchdog.md, criterion 14a): an `ops` acknowledge needs
  the caller's current group to be `ops` (any resident); a caregiver acknowledge needs
  `caregiver` and the link (pattern 13). Both only for the three device-problem types
  (`device_offline`, `battery_low`, `not_worn`); a cancel or a nurse acknowledge only
  for health alerts;
- check in this order, for every acknowledge and cancel: (1) the caller's current group
  (else `403`); (2) for a caregiver, the link to the resident, pattern 13 (else `404`;
  never for `unassigned`); (3) GetItem of the alert (pattern 25; missing → `404`);
  (4) the type rule above (else `409`). For `ops`, any alert that is not a device
  problem gets `404`, not `409`, so ops can't learn that a health alert exists;
- take the note from the request body: change `\r\n` to `\n`, trim it, then check it
  with the same rules as `alert-processor` (`note` row below); if it fails, return
  `400` without echoing the note. A note that fails in `alert-processor` instead ends
  in the DLQ and the nurse never sees an error. (Emoji joined with U+200D are rejected
  too, since U+200D is a format character);
- never log request bodies, the note, or the `name` claim (no Powertools
  `log_event`, no body in error logs);
- take `resident_id`, `alert_id`, and `event_ts` from the stored alert's key (read it
  first), not from the client.

```json
{
  "kind": "status_update",
  "schema_version": 1,
  "resident_id": "res-0001",
  "new_status": "cancelled",
  "changed_at": "2026-11-03T02:00:05.000Z",
  "by_role": "resident",
  "by": null,
  "by_name": null,
  "target": { "device_id": "wearable-0001" }
}
```

A nurse acknowledge from Track B (synthetic values):

```json
{
  "kind": "status_update",
  "schema_version": 1,
  "resident_id": "res-0001",
  "new_status": "acknowledged",
  "changed_at": "2026-11-03T02:00:40.000Z",
  "by_role": "nurse",
  "by": "3f2b8c1e-0000-4000-8000-000000000001",
  "by_name": null,
  "note": "Synthetic note: ambulance on the way, 8 minutes.",
  "target": { "alert_id": "6f1c2e0a-5b3d-5e8f-9a47-000000000001", "event_ts": "2026-11-03T02:00:00.000Z" }
}
```

A caregiver cancel has `"by_role": "caregiver"`, `"new_status": "cancelled"`, the
caregiver's `sub` as `by`, their `name` claim as `by_name`, an alert target, and no
`note` key. A caregiver acknowledge of a device problem is the same with
`"new_status": "acknowledged"`; an ops acknowledge has `"by_role": "ops"`,
`"new_status": "acknowledged"`, the user's `sub`, `"by_name": null`, and no `note` key.
The watchdog's resolve (synthetic values):

```json
{
  "kind": "status_update",
  "schema_version": 1,
  "resident_id": "res-0001",
  "new_status": "resolved",
  "changed_at": "2026-11-04T02:05:00.000Z",
  "by_role": "system",
  "by": null,
  "by_name": null,
  "target": { "alert_id": "6f1c2e0a-5b3d-5e8f-9a47-000000000002", "event_ts": "2026-11-04T01:40:00.000Z" }
}
```

| `by_role` | `new_status` | `target` | `by` | `by_name` | `note` |
|---|---|---|---|---|---|
| `resident` | `cancelled` | `{"device_id"}` | `null` | `null` | absent |
| `caregiver` | `cancelled` | `{"alert_id", "event_ts"}` | Cognito `sub` | `name` claim, 1-100 characters | absent |
| `caregiver` | `acknowledged` | `{"alert_id", "event_ts"}` | Cognito `sub` | `name` claim, 1-100 characters | absent |
| `nurse` | `acknowledged` | `{"alert_id", "event_ts"}` | Cognito `sub` | `null` | optional |
| `ops` | `acknowledged` | `{"alert_id", "event_ts"}` | Cognito `sub` | `null` | absent |
| `system` | `resolved` | `{"alert_id", "event_ts"}` | `null` | `null` | absent |

`resolved` is valid only from `system`, and `system` only with `resolved`.

`reason` (docs/features/possible-fall.md, criterion 7) is an optional field allowed
only on a `system` update, and only with the value `back_in_bed` (any other value, or
the key on any other role's update, is invalid). The watchdog sends it on the resolve
of a possible fall:

```json
{
  "kind": "status_update",
  "schema_version": 1,
  "resident_id": "res-0003",
  "new_status": "resolved",
  "changed_at": "2026-11-04T16:05:00.000Z",
  "by_role": "system",
  "by": null,
  "by_name": null,
  "reason": "back_in_bed",
  "target": { "alert_id": "6f1c2e0a-5b3d-5e8f-9a47-000000000003", "event_ts": "2026-11-04T15:40:00.000Z" }
}
```

A device-problem resolve keeps the shape above **without** the `reason` key, so the
watchdog's existing resolves stay valid; a `system` resolve with `reason` on a device
problem, or without it on a possible fall, is `not_allowed` (checked after reading the
target).

**Which role may change which type** (docs/features/watchdog.md, criterion 14a), checked
by `alert-processor` after reading the target (it can't be checked earlier):

| Alert type | Allowed |
|---|---|
| device problems (`device_offline`, `battery_low`, `not_worn`) | `ops` or `caregiver` `acknowledged`; `system` `resolved` (no `reason`) |
| `possible_fall` (docs/features/possible-fall.md, criterion 12) | `nurse` `acknowledged`; `resident` or `caregiver` `cancelled`; `system` `resolved` with `reason = back_in_bed` |
| health alerts (every other type) | `nurse` `acknowledged`; `resident` or `caregiver` `cancelled` |

Anything else (e.g. a nurse acknowledging a low battery, a caregiver cancelling one, ops
acknowledging a fall): nothing changes, no email, outcome `not_allowed`, not a failure.

| Field | Rules |
|---|---|
| `kind` | `status_update` |
| `schema_version` | `1` |
| `resident_id` | `res-NNNN`, or `unassigned` |
| `new_status` | `cancelled`, `acknowledged`, or `resolved`, per the table above |
| `changed_at` | ISO 8601 UTC with ms and `Z`: for a resident cancel, when the IoT rule received the `alert_cancel` (`received_at`, server time, so a wrong device clock can't move the Query bound or the expiry); the API's time for a caregiver cancel or nurse acknowledge |
| `by_role` | `resident` (wearable cancel), `caregiver`, `nurse`, or `ops` (Track B), `system` (the watchdog only). `staff`, `family`, or anything else is invalid |
| `by` | `null` for `resident` and `system`; the caller's Cognito `sub` (a lowercase UUID) for `caregiver` / `nurse` / `ops`, from the verified token |
| `by_name` | only for `caregiver`: the `name` claim of the verified token, 1-100 characters, not only spaces, no control characters. Used only to build the masked name in the email; never stored, logged, or emailed in full. `null` for every other role |
| `note` | only for `nurse`, optional: absent or `null` means no note; else a string of 1-500 characters, not only whitespace, no control or other non-printing characters (Unicode category C, e.g. bidi overrides, zero-width spaces, and the line/paragraph separators U+2028/U+2029) except newline (`\n`). Each line is emailed quoted with `> `. Stored as `ack_note`, emailed to caregivers, never logged. A cancel must not have the key |
| `reason` | only for `system`, optional: absent (device-problem resolve) or `back_in_bed` (possible-fall resolve, docs/features/possible-fall.md, criterion 7). Any other value, `null`, or the key on another role's update is invalid |
| `target` | `{"device_id"}` (every open or acknowledged alert from that device up to `changed_at`, plus, for a wearable, the resident's possible falls) or `{"alert_id", "event_ts"}` (one alert) |

- Any other combination or an unknown field is invalid: it fails the record and ends
  in `silvercare-alerts-dlq` (where `by_name` and `note` can stay up to 14 days).
- `alert-processor` (no repeat check) finds the targets (pattern 24 for a device target,
  which leaves out device problems, so "I'm OK" never cancels them; pattern 25 for an
  alert target, then the role/type rule above). A cancel and a resolve apply to `open`
  or `acknowledged` alerts, an acknowledge only to `open` ones. None: outcome
  `no_open_alerts` (device target) or `not_open` (alert target), not a failure.
- For each, an UpdateItem conditional on the status it may change from:
  - cancel: `#status IN (:open, :acknowledged)`; sets `status = cancelled`,
    `cancelled_at`, `cancelled_by_role`, `cancelled_by` (caregiver only), `expires_at =
    changed_at + 30 days`, and REMOVEs `open_pk` (off the dashboard's open list) and
    `status_notified_at` (an acknowledge's). Keeps `acknowledged_at`, `acknowledged_by`,
    and `ack_note`;
  - acknowledge: `#status = :open`; sets `status = acknowledged`, `acknowledged_at`,
    `acknowledged_by`, and `ack_note` if there is a note (plus `acknowledged_by_role`
    `ops` / `caregiver` for a device problem); keeps `open_pk` until resolved;
  - resolve (`system`, device problems): `#status IN (:open, :acknowledged)`; sets
    `status = resolved`, `resolved_at` (`changed_at`), `resolved_by_role = system`,
    `expires_at = changed_at + 30 days`, and REMOVEs `open_pk`. Keeps the acknowledge's
    fields. **No follow-up email** and no `status_notified_at`; an alert already
    resolved (or missing) ends `not_open`. A possible fall's back-in-bed resolve
    (`reason = back_in_bed`, docs/features/possible-fall.md, criterion 8) is applied
    the same way but does get a follow-up (below).
  A failed condition skips that alert without an email.
- Then the follow-up (one email per alert, then `SET status_notified_at` with
  `attribute_not_exists(status_notified_at)`):
  - cancel (resident or caregiver): caregivers through `silvercare-alerts` with the
    `resident_id` attribute (not for `unassigned`); nurses through `silvercare-nurses`
    if the item has `escalated_at` (they were emailed about it) or `acknowledged_at`
    (they had acknowledged it). The first line names the canceller (`the resident`, or
    the caregiver's masked name, `Ta* Me* Li** (caregiver)`); a cancel of an
    acknowledged alert adds `It had been acknowledged by the care centre nurse at ...`;
  - acknowledge: caregivers only, `... acknowledged by the care centre nurse at ...`,
    plus `Message from the care centre:` and the note if there is one. Unassigned:
    nobody is emailed, and `status_notified_at` is still set;
  - device-problem acknowledge (ops or caregiver): the caregivers (`silvercare-alerts`,
    `resident_id` attribute; not for `unassigned`) **and** ops (`silvercare-ops-devices`), no
    nurses. Subject `SilverCare update: <label> - <device_id> (<resident_id>)
    acknowledged`; first line `The device problem below was acknowledged by the ops team
    at <time> SGT.` or `... by Ta* Me* Li** (caregiver) at ...`, then the device-problem
    lines (type, device, resident, detail line);
  - back-in-bed resolve of a possible fall: the caregivers (`silvercare-alerts`,
    `resident_id` attribute; not for `unassigned`) and, if the item has
    `escalated_at`, the nurses (`silvercare-nurses`). Subject `SilverCare update:
    Possible fall - <resident_id> resolved`; first line `The alert below was resolved
    at <changed_at> SGT: the resident is back in bed.`, then the usual lines. An item
    already `resolved` by `system` without `status_notified_at` gets its follow-up on
    a retry.
  An unassigned alert's follow-up adds `Device: <device_id>`, or `Device: invalid`. An
  item that already has the new `status` from the same kind of update (the resident;
  or the same caregiver, nurse, or ops `sub`) but no `status_notified_at` gets its
  follow-up on a retry; a duplicate is accepted, a missing one isn't.
- The log line per alert has `by_role` and, for an acknowledge, `has_note`; never
  `by`, `by_name`, or `note`.
- A cancelled or acknowledged alert's escalation check ends `not_escalated`
  (`status:cancelled` / `status:acknowledged`).
- The S3 `alerts/` copy is not updated: it stays as stored (open), without `ack_note`.

### Index

| Index | Hash | Range | Projection | Serves |
|---|---|---|---|---|
| `open` | `open_pk` | `sk` | INCLUDE `alert_type`, `device_id`, `status`, `event_ts` | 9 |

`sk` starts with the event time, so the index sorts open alerts by time across all
residents.

## S3 history

Every device message, as received, for later cleaning and analysis with Lake Formation
and Athena (docs/features/ingest-history.md; the History path in
[`docs/decisions/0001-iot-rule-routing.md`](decisions/0001-iot-rule-routing.md#history-path)).

- **Bucket** `silvercare-history-<account_id>` in `ap-southeast-1`: all public access
  blocked, `BucketOwnerEnforced`, SSE-S3, TLS-only bucket policy. Versioning **off**,
  so a delete really deletes (e.g. for a PDPA erasure request). One lifecycle rule:
  abort incomplete multipart uploads after 1 day. Retention is decided (see
  [Retention](#retention)) but not in Terraform yet: `telemetry/` needs an expiry
  rule at 365 days *(proposed)*, added only once daily summaries exist. The clean job must
  refuse dates older than that limit in the same change: a re-run deletes a date's output
  first, so with its raw files gone it would leave that date empty.
- **Key layout:**
  `telemetry/message=<message>/date=<yyyy-mm-dd>/<yyyymmddTHHMMSSZ>-<uuid4>.jsonl.gz`.
  `<message>` is the body's `message` if it matches `^[a-z_]{1,16}$`, else `unknown`;
  bodies that aren't a JSON object go to `unparseable`. Files written before underscores
  were allowed hold `vitals_alert` and `alert_cancel` under `unknown`, the only names this
  affected; they aren't moved, and the clean job reads them from there. `<date>` is the UTC date of
  `received_at` (else the SQS sent time). The timestamp is when the file was written;
  the UUID is random, so keys hold no device or resident IDs.
- **Alerts:** `alerts/date=<yyyy-mm-dd>/<alert_id>.json`, one object per alert:
  `<date>` is the UTC date of `event_ts`, the body is the `silvercare-alerts` item as
  one JSON object (`Content-Type: application/json`), as stored before the email is
  sent (so without `notified_at`). A redelivered message overwrites the same key. Only
  `alert-processor` may write `alerts/` (its role, plus a Deny for every other
  principal in the bucket policy). The `alert_id` key reveals neither the resident nor
  the type of event.
- **Daily summaries:** `summaries/` is reserved for them *(proposed, not built)*.
- **Retention** (decided 2026-11-01; see [Retention](#retention)):
  - **Derived records that informed care** (alerts under `alerts/`, daily summaries
    under `summaries/`): the resident's lifetime + 6 years, the same rule as the
    threshold change history (MOH Circular 85/2022, Licence Conditions on the Retention Periods of Patient Health Records, under the Healthcare Services Act 2020).
  - **Raw telemetry** (`telemetry/`): downsampled to daily summaries per resident and
    metric after 90 days *(proposed)*, and the raw files deleted at 365 days
    *(proposed)* by a lifecycle expiry rule. Raw readings aren't themselves the clinical
    record, and keeping every reading for lifetime + 6 years would cost more and serve
    no clinical purpose.
  - *(proposed)* **Gap: deleting one resident's derived records.** Lifetime + 6 years
    is per resident, but neither key layout names the resident: `telemetry/` batches
    many devices into one file, and an `alerts/` key holds only the date and
    `alert_id`. Deleting one resident's alerts and summaries at date of death + 6 years
    needs a way to find them (for example, an object tag with the opaque `resident_id`,
    like photos), designed with the summary job. This needs the date-of-death field too
    (see [Threshold change history](#threshold-change-history)).
- **Format:** gzip-compressed JSON Lines (`Content-Type: application/gzip`, no
  `Content-Encoding`), UTF-8, one compact JSON object per line in the order received,
  ending with a newline. Each line is the IoT rule's output (device payload plus
  `device_id`, `message`, `received_at`), stored as received: invalid messages are
  kept, and a redelivered batch can store a message twice (duplicates are removed later
  by `message_id`). Unparseable bodies are stored as `{"raw_body": "<body>"}`.
- **Writers:** the `history` Lambda (`silvercare-history`), one file per message type and
  UTC day per batch (up to 1,000 messages or 5 minutes) from the `silvercare-history`
  queue. It may only add objects under `telemetry/`. `alert-processor` writes
  `alerts/` only.
- **Readers:** developers (the shared IAM user's `ReadProjectBuckets` statement; read
  only, no write or delete), e.g. `scripts/ingest-check.py --history`. The `clean`
  Lambda, through Athena, reads `telemetry/` only (see [S3 clean layer](#s3-clean-layer)).

## S3 clean layer

The first layer of the data lake (raw → clean → curated), built by the daily `clean`
Lambda from one UTC day of `telemetry/` (docs/features/clean-data.md). Raw files are
never changed. Clean data identifies devices only: linking to residents is for the later
curated layer. No DynamoDB access, so no RCU or WCU use.

- **Buckets** in `ap-southeast-1`, both with all public access blocked,
  `BucketOwnerEnforced`, SSE-S3, a TLS-only bucket policy, and versioning off:
  - `silvercare-clean-<account_id>`: `tables/<table>/date=<yyyy-mm-dd>/`, Parquet
    (Snappy). Expires at 365 days *(proposed)*, see [Retention](#retention).
  - `silvercare-athena-results-<account_id>`: `clean/` for the Lambda, `analysis/` for
    developers. Objects expire after 7 days.
- **Glue database** `silvercare_lake`:
  - `raw_telemetry`: one `line string` column (text input, gzip read automatically),
    partitioned by `message` and `date`, at `s3://silvercare-history-<account_id>/telemetry/`.
    `alerts/` is not part of it.
  - The eight clean tables and `rejected` below, each partitioned by `date` (the raw
    file's `date`, from `received_at`).

| Table | From raw `message` | Columns besides the common ones |
|---|---|---|
| `vitals` | `vitals` | `hr_bpm` int, `spo2_pct` int, `skin_temp_c` double, `activity_pct` int, `accel_g` string (JSON), `accel_valid` boolean |
| `vitals_alert` | `vitals_alert` | `metric` string, `value` int, `limit` int, `direction` string, `held_s` int |
| `fall` | `fall` | `confidence` double, `peak_g` double, `still_s` int, `accel_window` string (JSON), `accel_valid` boolean |
| `alert_cancel` | `alert_cancel` | none |
| `wearable_status` | `status`, wearable | `battery_pct` int, `worn` boolean, `charging` boolean, `fw_version` string |
| `bedroom_status` | `status`, bedroom | `battery_pct` int, `fw_version` string |
| `motion` | `motion` | `motion` boolean |
| `bed` | `bed` | `occupied` boolean |

- **Common columns:** `message_id` string, `device_id` string, `ts` timestamp,
  `received_at` timestamp, `schema_version` int, `clock_skew` boolean (`ts` more than 5
  minutes from `received_at`).
- **What is kept:** a line is in a clean table only if ingest's `validate_event` would
  accept it, ignoring its accelerometer checks. A line under `unknown` whose `message`
  names a contract type is processed as that type, for that date. Within a table and date
  the earliest `received_at` per `message_id` is kept, across the type's own partition and
  `unknown`; a duplicate landing on another date isn't removed.
- **Fields:** unknown fields are dropped, at the top level and inside the accelerometer
  columns, which are rebuilt from known fields only. `accel_g` is `{"x":…,"y":…,"z":…}`,
  with `accel_valid` true when all three are numbers from −16 to 16. `accel_window` is
  `hz`, `start_offset_ms`, and `samples`, with `accel_valid` true when they are 25, −1000,
  and 50 arrays of 3 numbers from −16 to 16. Otherwise `accel_valid` is false and the
  column is null, and the message stays in its table.
- **`rejected`:** `message` (the raw partition), `reason`, `raw_line`, and `date`. Every
  line that fails validation, every line under `unparseable`, and every line under
  `unknown` that doesn't name a contract type (`unknown_message`), exact duplicates once.
  `reason` uses ingest's `Rejection` strings plus `unknown_message` and `unparseable`.
  `raw_line` holds the message as received.
- **Writes:** for each table the job deletes the date's folder, then writes it again, so
  a re-run replaces that date. It is the only writer.
- **Access:**

  | Who | Through Athena and the catalog (Lake Formation) | Direct S3 |
  |---|---|---|
  | `silvercare-clean` role | runs queries in `silvercare-clean`; reads `raw_telemetry`, adds partitions | read `telemetry/` (no write or delete); list, write, and delete `tables/` in the clean bucket; own results prefix |
  | Developers | run queries in `silvercare-analysis`; the eight clean tables only, not `raw_telemetry` or `rejected` | read everything, including `rejected` and raw, through `ReadProjectBuckets`; write only to `analysis/` results |

  Lake Formation doesn't limit direct S3 reads. That matches today's rule while all data
  is synthetic.
- **Retention:** clean tables, `rejected` included, expire 365 days after a file is
  written *(proposed, see [Retention](#retention))*. Athena result files last 7 days; Athena keeps query text in its history for 45 days.
- **Erasure: not solved.** Raw files batch many devices together, so erasing one device
  means rewriting those files without it and re-running the affected dates. Those rewrites
  restart the expiry clock for every other device on those dates, so the tool must account
  for that. `unparseable` lines have no parsed `device_id`, but `raw_body` may still contain
  one as text, so the tool must search that text or rely on expiry. Athena results and
  query history age out rather than being erased.
- **Before any real resident data arrives:**
  - the per-device erasure tool above exists
  - direct S3 reads can be traced (who read which object, when): none are recorded today
  - the shared developer user and its read access are reviewed
  - raw telemetry's expiry rule exists

## Capacity

Estimated load at 40 devices (from the volume table in `docs/metrics.md`). A write unit
(WCU) covers 1 KB written per second; a read unit (RCU) covers one strongly consistent
read of up to 4 KB per second, or two eventually consistent ones.

| | Average load |
|---|---|
| latest writes | vitals 0.33/s × 2 WCU (item ~1–2 KB) + status 0.13/s × 1 + bed/motion ~0.02/s ≈ **0.9 WCU** |
| latest reads (write rule step 1) | ~0.5/s × 1 RCU ≈ **0.5 RCU** |
| `by-message` index writes | vitals 0.33/s + status/bed/motion 0.15/s, 1 WCU each ≈ **0.5 WCU** |
| registry reads | cached in Lambdas; dashboards ≈ **< 1 RCU** |
| `watchdog` (every 5 min) | eventually consistent Queries of `by-type` (~40 devices, plus `TEST_MODE`, normally empty), `by-message` `status` (~40 items, a few KB), `bed` and `motion` (~20 items each), and `open`, plus one BatchGetItem for wearables off the wrist: a few RCU per run, about 1 run per 300 s ≈ **< 0.1 RCU** on average |
| alerts | rare writes (plus up to two escalation UpdateItems per alert: `escalated_at` at once for critical and unassigned alerts, or `escalation_queued_at` and `escalated_at` for warnings; and two per alert a status update changes); one strongly consistent Query per non-fall alert (repeat check) and per `alert_cancel` (pattern 24, rare), one strongly consistent GetItem per escalation check, per acknowledge, and per caregiver cancel; dashboard polling ≈ **< 1 RCU** |

Provisioned (fixed, no auto scaling):

| Table / index | RCU | WCU |
|---|---|---|
| `silvercare-latest` | 4 | 8 |
| `silvercare-latest` / `by-message` | 3 | 4 |
| `silvercare-registry` | 3 | 1 |
| `silvercare-registry` / `by-type` | 2 | 1 |
| `silvercare-registry` / `by-resident` | 2 | 1 |
| `silvercare-alerts` | 3 | 2 |
| `silvercare-alerts` / `open` | 2 | 1 |
| **Total** | **19 of 25** | **18 of 25** |

This leaves headroom in the free tier for one more table or index.

**Bursts:** if all 20 wearables publish vitals in the same second, that is about 40 WCU
for one second. DynamoDB keeps up to 5 minutes of unused capacity as burst capacity,
which absorbs this, but only on a best-effort basis. So:

- The simulator spreads devices across the minute (random offset per device).
- Lambdas use boto3's `adaptive` retry mode, so throttled writes back off and retry.
- IoT rules invoke Lambdas asynchronously, with Lambda's two automatic retries. There
  is no failed-message queue for `telemetry` and `device-health` (see
  `docs/decisions/0001-iot-rule-routing.md`), so a vitals warning from a `telemetry`
  invocation that keeps failing is lost (Known gap in docs/features/vitals-alerts.md);
  `critical-alert` (was `fall-alert`) sends events that still fail to
  `silvercare-critical-alert-dlq` (docs/features/alerts-core.md,
  docs/features/vitals-alerts.md).

**`silvercare-critical-alert-dlq` contents:** raw `fall` payloads (including
`accel_window`) and raw `vitals_alert` payloads (heart rate or SpO2 reading and limit),
linked to the `device_id` from the topic, plus Lambda error details (from
critical-alert's on-failure destination and the `silvercare_alert` rule's error
action). Kept up to 14 days (SQS maximum retention), encrypted with SSE-SQS, and the
queue policy denies requests without TLS and sends from any role other than
critical-alert and `silvercare-iot-rules`.

## Settings (all tables)

| Setting | Value | Cost |
|---|---|---|
| `billing_mode` | `PROVISIONED` | free within 25 RCU / 25 WCU |
| Encryption at rest | on, AWS owned key (the default) | free. A customer-managed KMS key costs about US$1/month per key, so checkov's KMS rule (`CKV_AWS_119`) gets a documented skip. Trade-off: no CloudTrail record of key use, and the key can't be revoked |
| Point-in-time recovery | on | billed per GB-month; KB of data ≈ US$0 |
| Deletion protection | on | free |
| Streams | none | |
| TTL | alerts table, `expires_at` | deletes are free and use no write capacity |

## Retention

| Data | How long | Mechanism |
|---|---|---|
| Latest messages | 5 per device and type | write rule |
| Open and acknowledged alerts | until resolved | — |
| Resolved alerts | 30 days after `resolved_at` | TTL `expires_at` |
| Cancelled alerts | 30 days after the cancel (`cancelled_at`, when the press was received) | TTL `expires_at` (the S3 `alerts/` copy stays, see below) |
| Residents (profile, thresholds, photo), devices, links | while the resident or user is in the programme | deleted at offboarding (below) |
| Threshold change history *(proposed)* | the resident's lifetime + 6 years (MOH Circular 85/2022); kept after offboarding | TTL `expires_at` = date of death + 6 years, set when a date of death is recorded (no field for it yet); no expiry until then |
| History (S3 `telemetry/`, raw) | daily summaries made by 90 days, raw deleted at 365 days *(both proposed)* | S3 lifecycle expiry on `telemetry/` at 365 days (not in Terraform yet) |
| Clean tables (S3 `silvercare-clean-<account_id>`, `tables/`, `rejected` included) | 365 days after a file is written *(proposed)*, matching raw; re-running a date restarts its clock | S3 lifecycle expiry on `tables/` |
| Athena results and query history | result files 7 days; query text 45 days (AWS's fixed limit) | S3 lifecycle expiry; AWS ages out the history |
| Alert copies and daily summaries (S3 `alerts/`, `summaries/`) | the resident's lifetime + 6 years (MOH Circular 85/2022) | per-resident deletion at date of death + 6 years; finding one resident's objects and the date of death are gaps (see [S3 history](#s3-history)); no expiry rule until then |
| Failed fall and `vitals_alert` events (SQS `silvercare-critical-alert-dlq`) | up to 14 days | SQS message retention (SSE-SQS encryption, TLS-only queue policy) |
| Latest device `vitals_alert`s | 5 per device | write rule |
| Latest `alert_cancel`s ("I'm OK" presses) | 5 per wearable | write rule |
| Nurse acknowledge notes (`ack_note` on the alert item) | deleted with the alert: 30 days after resolve or cancel (an acknowledged alert that is never resolved keeps it, see below); never in the S3 `alerts/` copy | TTL `expires_at` |
| Status updates in `silvercare-alerts` (normally seconds; up to 4 days while retried) and `silvercare-alerts-dlq` (a Track B caregiver cancel, and a caregiver acknowledge of a device problem, carry the user's full name, `by_name`; a nurse acknowledge may carry the `note`) | up to 4 days in the queue, 14 days in the DLQ | SQS message retention |
| Device-problem alerts (`device_offline`, `battery_low`, `not_worn`; docs/features/watchdog.md) | open (or acknowledged) until the watchdog resolves them; resolved (or, for any cancelled one, cancelled) → 30 days after `resolved_at` / `cancelled_at`. An alert for a device that never recovers stays open until it is resolved by unassigning the device (the next watchdog run then resolves it) | TTL `expires_at` (the S3 `alerts/` copy stays, Open decision #4) |
| `worn_false_since` (wearable `MSG#status` item) | while the wearable is off the wrist: overwritten or removed by `device-health` with the next newest status, gone when the item is replaced or removed (e.g. the device is unassigned or moved) | write rule |
| Suppressed repeat alerts | not kept (log line with IDs and outcome only, 30 days) | — |
| User accounts (Cognito) | disabled at the user's offboarding, deleted 90 days later | admin steps in the Cognito console (see `docs/features/sign-in.md`) |

Point-in-time recovery keeps deleted and TTL-expired items restorable for up to 35
days, so "deleted" data still exists in backups for that long.

**Offboarding** (a resident leaves the programme), done by a staff action that the API
runs in one go:

1. Resolve the resident's open and acknowledged alerts, which sets their TTL.
2. Unassign their devices, which also deletes the devices' `latest` items.
3. Delete their photo from S3 (before the profile, see [Photos](#photos)).
4. Delete their user links, then their `PROFILE` and `THRESHOLDS` items. Their
   `THRESHOLD_LOG#` items (pattern 27) are **kept**, and this step does not set
   `expires_at`: that waits for a recorded date of death (see
   [Threshold change history](#threshold-change-history)).

Acknowledged alerts that are never resolved have no TTL, on purpose: deleting an
unhandled alert automatically could hide a fall. The nurse dashboard lists them until
someone resolves them, and the doctor dashboard counts them in its alert summary.

## Notes for later specs

- **API auditing:** each request logs the caller's `sub`, the route, and the
  `resident_id`, with no payloads, so access to a resident's data can be traced.

## Open questions

None for the tables. Threshold defaults and vitals alert rules are set by
docs/features/vitals-alerts.md (Open decision #2); how links are created comes from
user onboarding (Open decision #3).
Recording a date of death (needed for the threshold change history's TTL) has no field
or access pattern yet.
