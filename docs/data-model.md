# Data model

Status: **approved** 2026-10-25, except the parts marked *(proposed)*, which are decided
with the resident pages feature:

- resident profile fields beyond `display_name` and `home_id`: `year_of_birth`,
  `medical_notes`, `emergency_contacts`, `photo_key`
- [Photos](#photos) (patterns 19 and 20)
- what caregiver and family users see (the first paragraph of
  [Access patterns](#access-patterns))
- `escalates_at` on alerts: computed by the API (not stored) from escalation's 60 s
  check, for the portal's countdown
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

*(proposed)* What each role sees: caregiver and family see the same things for the residents they
are linked to, including raw vitals, alert `details`, and who acknowledged or resolved
an alert.

**Actor names:**

- **Alerts** (docs/features/alert-cancel.md): `acknowledged_by` (a staff or caregiver
  `sub`, pattern 16) and `resolved_by` (staff) store only the `sub`; the user's name is
  never stored on the alert. The API looks the name up in Cognito when it returns the
  alert and never returns the `sub`. Once the account is deleted (90 days after the
  user's offboarding), it returns "Former staff member" or "Former caregiver" instead
  (decided 2026-11-04).
- **Threshold changes** (patterns 26, 27; decided 2026-11-01): `updated_by` and
  `changed_by` store the staff `sub` (for audit) **and** the name, copied from Cognito
  when the change is written (`updated_by_name`, `changed_by_name`), so the change
  history keeps the name after the account is deleted. The name is as it was at the
  time; a later rename doesn't change past records. The `sub` is never returned.

Caller groups (Cognito): **staff** means the `doctor` and `nurse` groups and can read every resident. **caregiver/family** can read a
resident only after the link check (pattern 13) passes, and the API also checks the
caller's current Cognito group, because `relationship` on the link is a copy that can
go stale. Lambdas without a caller are marked *backend*.

| # | Who / what | Query | Table / index | Key condition | Frequency |
|---|---|---|---|---|---|
| 1 | `telemetry`, `critical-alert` | Which resident is this device assigned to? | registry | GetItem `pk = DEVICE#<device_id>`, `sk = ASSIGNMENT` | `critical-alert`: every `fall` / `vitals_alert` / `alert_cancel`; `telemetry`: every `vitals` whose newest two entries are at most 3 min apart (cached 5 min per Lambda container). `device-health` reads no registry |
| 2 | `telemetry` | This resident's vitals thresholds | registry | GetItem `pk = THRESHOLDS#<resident_id>`, `sk = THRESHOLDS` | as pattern 1 for `vitals`, assigned devices only (cached 5 min, including "no item") |
| 3 | `telemetry`, `device-health`, `critical-alert` | Add a message to the latest 5 for its device and type | latest | GetItem + conditional PutItem `pk = DEVICE#<device_id>`, `sk = MSG#<message>` | ~0.5 / s |
| 4 | backend: `watchdog`; *(proposed)* staff only: devices view | Every device, assigned or not | registry `by-type` | Query `entity_type = DEVICE` | every 5 min; per devices-view load |
| 5 | backend: `watchdog`; staff only: nurse and doctor dashboards, *(proposed)* devices view (`status` only) | Newest `vitals` / `status` / `bed` / `motion` of every device | latest `by-message` | Query `watch_type = <message>` (watchdog: `status`, `bed`, `motion`; dashboard: all four) | every 5 min; every 30 s per open dashboard (polling) |
| 6 | backend: `alert-processor` | Write an alert exactly once | alerts | PutItem with `attribute_not_exists(pk)` | per alert |
| 7 | backend: `alert-processor` | This resident's recent alerts, to hold back a repeat (same `alert_type` and `severity`, and for `unassigned` the same `device_id`, within 30 min; never falls) | alerts | Query `pk = RESIDENT#<resident_id>`, `sk BETWEEN "ALERT#<event_ts − 30 min>" AND "ALERT#<event_ts>#~"` (`~` sorts after every `alert_id`, so alerts at the same `event_ts`, including this alert's own item on a retry, are returned), strongly consistent | per non-fall alert message |
| 8 | Staff only: nurse and doctor dashboards, *(proposed)* devices view (names for assigned devices) | All residents | registry `by-type` | Query `entity_type = RESIDENT` | every 30 s per open dashboard |
| 9 | Staff only: nurse and doctor dashboards | All open alerts, newest first | alerts `open` | Query `open_pk = OPEN`, descending | every 30 s per open dashboard |
| 10 | Resident page: staff, or caregiver/family after 13 | This resident's devices | registry `by-resident` | Query `gsi1_pk = RESIDENT#<resident_id>`, `begins_with(gsi1_sk, "DEVICE#")` | per page view |
| 11 | Resident page: staff, or caregiver/family after 13 | Latest 5 of every message type for those devices | latest | BatchGetItem (per device: wearable `vitals`, `vitals_alert`, `fall`, `status`; bedroom `motion`, `bed`, `status`) | per page view |
| 12 | Resident page: staff, or caregiver/family after 13 | This resident's alerts | alerts | Query `pk = RESIDENT#<resident_id>`, descending | per page view |
| 13 | API, caregiver/family | Is this user linked to this resident? | registry | GetItem `pk = USER#<sub>`, `sk = RESIDENT#<resident_id>` | every caregiver/family request for a resident |
| 14 | API, caregiver/family | Which residents can this user see? | registry | Query `pk = USER#<sub>`, `begins_with(sk, "RESIDENT#")` | caregiver/family home page |
| 15 | *(not used yet)* backend: `alert-processor` | Who is linked to this resident (to notify) | registry `by-resident` | Query `gsi1_pk = RESIDENT#<resident_id>`, `begins_with(gsi1_sk, "USER#")` | per new alert. Not used by alerts-core: it notifies through SNS email subscriptions whose filter policy matches `resident_id`, set in Terraform variables (docs/features/alerts-core.md, criteria 21 and 24), because SNS can only email confirmed subscribers |
| 16 | Acknowledge: staff, or caregiver after 13 (docs/features/escalation.md; family can't). Resolve: staff only | Acknowledge or resolve an alert | alerts | Acknowledge: the API sends a `status_update` to the alerts queue and `alert-processor` applies it (pattern 25; see [Status updates](#status-updates)). Resolve: UpdateItem `pk`, `sk`, condition `attribute_exists(pk)` | per action |
| 17 | Staff only, seed script | Add or edit a resident, assign a device, link a user | registry | PutItem / UpdateItem / DeleteItem | rare |
| 18 | Resident page: staff, or caregiver/family after 13 | This resident's profile and thresholds | registry | BatchGetItem `RESIDENT#<resident_id>` / `PROFILE` and `THRESHOLDS#<resident_id>` / `THRESHOLDS` | per page view |
| 19 | Resident page: staff, or caregiver/family after 13 | This resident's photo | S3 `silvercare-photos-<account_id>` | API returns a presigned GET URL for `photo_key`, valid 5 min | per page view |
| 20 | Staff only | Upload or replace a photo | S3 | API returns a presigned POST (JPEG only, max 2 MB) | rare |
| 21 | backend: `alert-processor` | A wearable's newest vitals, to add to a fall alert | latest | GetItem `pk = DEVICE#<device_id>`, `sk = MSG#vitals` (uses `entries[0]`) | per fall alert |
| 22 | backend: `alert-processor` | Re-read an alert that already exists (a redelivered message) | alerts | GetItem `pk = RESIDENT#<resident_id>`, `sk = ALERT#<event_ts>#<alert_id>`, strongly consistent | per redelivered alert message |
| 23 | backend: `alert-processor` | Escalation: mark the check queued; 60 s later read the alert's `status` and mark it escalated (docs/features/escalation.md) | alerts | UpdateItem `SET escalation_queued_at` with `attribute_not_exists(escalation_queued_at)`; GetItem `pk = RESIDENT#<resident_id>`, `sk = ALERT#<event_ts>#<alert_id>`, strongly consistent; UpdateItem `SET escalated_at` with `attribute_not_exists(escalated_at)` | per stored alert (one GetItem, up to two UpdateItems) |
| 24 | backend: `alert-processor` | Cancel: this resident's alerts from one device that are still `open` (or already `cancelled` but without `status_notified_at`, a retry), up to the press (docs/features/alert-cancel.md, criterion 8) | alerts | Query `pk = RESIDENT#<resident_id>`, `sk <= "ALERT#<changed_at>#~"` (alerts at exactly `changed_at` included, later ones not), strongly consistent, `FilterExpression` `device_id = :device AND (#status = :open OR (#status = :new AND attribute_not_exists(status_notified_at)))`, paginated; then per target a conditional UpdateItem (`#status = :open`) and `SET status_notified_at` with `attribute_not_exists(status_notified_at)` | per `alert_cancel` (rare) |
| 25 | backend: `alert-processor` | Acknowledge one alert (a `status_update` from Track B's API) | alerts | GetItem `pk = RESIDENT#<resident_id>`, `sk = ALERT#<event_ts>#<alert_id>`, strongly consistent; conditional UpdateItem (`#status = :open`); `SET status_notified_at` with `attribute_not_exists(status_notified_at)` | per acknowledge |
| 26 | *(proposed)* Doctor only: resident page | Change this resident's thresholds, and record the change (27) | registry | TransactWriteItems: UpdateItem `pk = THRESHOLDS#<resident_id>`, `sk = THRESHOLDS`, condition `attribute_exists(pk)` and `updated_at` unchanged since the read; PutItem of the change | rare |
| 27 | *(proposed)* Resident page: staff only | This resident's threshold changes, newest first | registry | Query `pk = THRESHOLD_LOG#<resident_id>`, descending | per page view |

No pattern uses `Scan`.

Within staff, pattern 16 is open to doctors and nurses alike (decided 2026-11-01). Who
may call it is set by its row: staff, plus a linked caregiver for acknowledge
(docs/features/escalation.md). That nurses handle alerts is workflow, shown only in the
UI (the doctor dashboard and the resident page show doctors no acknowledge or resolve
buttons). `attribute_exists(pk)` stops a resolve for a mistyped or expired alert from
creating an item; an acknowledge goes through `alert-processor`, which only updates an
`open` alert (pattern 25).

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

Visible to staff and to every caregiver and family member linked to the resident.
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
| `medical_notes` | `Synthetic note: uses a walking frame.` | care context for staff, caregivers, and family. Free text, max 2,000 characters |
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
  caregiver or family.
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

Links a caregiver or family user (Cognito) to a resident. Names, emails, and phone
numbers stay in Cognito, not here. Staff can see every resident and have no links.

| Attribute | Example | Notes |
|---|---|---|
| `pk` | `USER#3f2b8c1e-0000-4000-8000-000000000001` | Cognito `sub` (synthetic example) |
| `sk` | `RESIDENT#res-0003` | |
| `relationship` | `family` | `caregiver` or `family` (the user's Cognito group) |
| `linked_at`, `linked_by` | `2026-10-18T03:00:00Z`, staff `sub` | |
| `gsi1_pk` | `RESIDENT#res-0003` | |
| `gsi1_sk` | `USER#3f2b8c1e-0000-4000-8000-000000000001` | |

How links are created is Open decision #3 (user onboarding).

### Indexes

`by-type` puts residents and devices in one index, so the `watchdog` role, which only
needs devices, can also read resident display names through it. This is accepted:
names are the least sensitive field, nothing else from the profile is projected, and
Lambdas never log names.

| Index | Hash | Range | Projection | Serves |
|---|---|---|---|---|
| `by-type` | `entity_type` | `pk` | INCLUDE `display_name`, `home_id`, `photo_key`, `device_type`, `resident_id` | 4, 8, photo cleanup |
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
| `alert_type` | `fall` | `fall`, `hr_high`, `hr_low`, `spo2_low`, `skin_temp_high`, `skin_temp_low` (docs/features/vitals-alerts.md) |
| `severity` | `critical` | `critical` (falls, device `vitals_alert`) or `warning` (the cloud vitals check). Items stored before vitals alerts have none; they are all falls (critical) |
| `device_id` | `wearable-0007` | |
| `event_ts` | `2026-10-18T03:14:07.360Z` | see below |
| `received_at` | `2026-10-18T03:14:07.410Z` | when the IoT rule received the triggering message (from the alert message) |
| `created_at` | `2026-10-18T03:14:07.900Z` | when `alert-processor` stored the item |
| `status` | `open` | `open`, `acknowledged`, `cancelled` (the wearable "I'm OK" cancel, docs/features/alert-cancel.md), `resolved`. Anything but `open` stops escalation. Only `alert-processor` changes `open` to `cancelled` or `acknowledged`, through a [status update](#status-updates) |
| `source_message_id` | `c2a1d4f0-8b6e-4f0a-a3c9-7e15d2b9f804` | for message-triggered alerts |
| `details` | `{"confidence": 0.91, "peak_g": 3.8, "still_s": 20, "latest_vitals": {"hr_bpm": 72, "spo2_pct": 97, "ts": "2026-10-18T03:13:00.000Z"}}` | small, type-specific. Fall: `confidence`, `peak_g`, `still_s`, plus `latest_vitals` (`hr_bpm`, `spo2_pct`, `ts` of the device's newest vitals entry, values may be `null`; absent if the device has sent no vitals). Vitals warning: `metric`, `value`, `limit`, `direction`, `previous_value`, e.g. `{"metric": "hr_bpm", "value": 128, "limit": 120, "direction": "above", "previous_value": 125}`. Vitals critical: `metric`, `value`, `limit`, `direction`, `held_s` (`hr_low` `value` 0 = no pulse) |
| `notified_at` | `2026-10-18T03:14:08.300Z` | set once, after the SNS email was published; guards against a second email on redelivery. Absent for unassigned alerts |
| `notification` | `skipped_unassigned` | only on unassigned alerts (`pk = RESIDENT#unassigned`): nobody is emailed |
| `escalation_queued_at` | `2026-10-18T03:14:08.350Z` | set once, after the delayed escalation check was sent to the alerts queue (docs/features/escalation.md, criterion 3); guards against queuing a second check on redelivery. Absent on suppressed alerts and on alerts stored before escalation |
| `escalated_at` | `2026-10-18T03:15:08.700Z` | set once, after the nurse email was published (criterion 5); guards against a second nurse email. `status` stays `open` |
| `acknowledged_by`, `acknowledged_at` | staff or caregiver `sub`, timestamp | a caregiver may acknowledge alerts of residents they are linked to (pattern 13; docs/features/escalation.md). Set by `alert-processor` from a `status_update`: `by` and `changed_at`. The user's name (`by_name`) is never stored |
| `cancelled_at` | `2026-11-03T02:00:05.000Z` | when the cloud received the resident's "I'm OK" press: the `alert_cancel`'s `received_at` (the update's `changed_at`, server time, not the device's `ts`). Only on cancelled alerts |
| `cancelled_by_role` | `resident` | who cancelled; `resident` is the only value today (the wearable button). Only on cancelled alerts |
| `status_notified_at` | `2026-11-03T02:00:06.100Z` | set once, after the cancelled / acknowledged follow-up email(s) were published (docs/features/alert-cancel.md, criterion 10); an item with the new `status` but without it gets its follow-up on a retry |
| `resolved_by`, `resolved_at` | staff `sub`, timestamp | the `sub` is never returned (see Actor names) |
| `escalates_at` | `2026-10-18T03:15:08.300Z` | *(proposed)* **not stored**: the API adds it to an `open` alert that has no `escalated_at` yet, as `notified_at` (or `created_at` for an unassigned alert) + 60 s, so the portal can show a countdown to the escalation check in docs/features/escalation.md (decided 2026-11-03). Approximate: the check runs about 60 s after `escalation_queued_at` |
| `open_pk` | `OPEN` | present until resolved or cancelled; removing it takes the alert out of the `open` index |
| `expires_at` | `1765000000` | epoch seconds, `resolved_at` + 30 days, or `cancelled_at` + 30 days for a cancelled alert. See [Retention](#retention) |

An alert from a device with no assignment (no `DEVICE#<id>` / `ASSIGNMENT` item in the
registry, or one without `resident_id`) is still stored, with
`resident_id = unassigned`, so `pk = RESIDENT#unassigned` (docs/features/alerts-core.md).
No caregiver is emailed; it is escalated to the nurses after 60 s like any other open
alert (docs/features/escalation.md).

`event_ts` is the time of the triggering event, so `alert_id` and the key are the same
however many times the same event is processed:

| Alert | `event_ts` |
|---|---|
| fall, device `vitals_alert` | `ts` of the message |
| abnormal vitals (cloud check) | `ts` of the newest of the two abnormal `vitals` entries |
| device offline | the device's last `status` `ts` (same outage → same key on every watchdog run) |
| possible fall | `ts` of the `bed` → `occupied: false` event |

**Repeats** (docs/features/vitals-alerts.md, criteria 17-20): before storing any alert
except a fall, `alert-processor` runs pattern 7 for the 30 minutes up to its
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
`notified_at`, publish the email and `SET notified_at` with
`attribute_not_exists(notified_at)`. No `expires_at` until the alert is resolved.

**Escalation** (docs/features/escalation.md): last, if the item is `open` and has no
`escalation_queued_at` (after the email for an assigned alert, after the store for an
unassigned one), `alert-processor` sends an escalation check
(`{"kind": "escalation_check", "schema_version": 1, "alert_id", "resident_id",
"event_ts"}`) to the alerts queue with `DelaySeconds = 60`, then `SET
escalation_queued_at` with `attribute_not_exists(escalation_queued_at)` (pattern 23).
When the check arrives it reads the alert (pattern 23, strongly consistent, no repeat
check): a present `status` other than `open` → nothing (a missing `status` escalates);
`escalated_at` set → nothing; more than 15 minutes after `created_at` (e.g. redriven
from the DLQ) → nothing, reason `stale`; otherwise it emails the
nurses (SNS `silvercare-nurses`) and `SET escalated_at` with
`attribute_not_exists(escalated_at)`. A crash between a send and its update can send a
second check or nurse email; a duplicate is accepted, a missing one isn't.

### Status updates

docs/features/alert-cancel.md, criteria 6-14. A third message kind on the
`silvercare-alerts` queue (with alert messages and escalation checks), sent without
delay. `critical-alert` sends one for every wearable `alert_cancel`; **Track B's
acknowledge API** sends one when a staff member or caregiver presses acknowledge. This
is the contract that API uses; it needs only `sqs:SendMessage` on `silvercare-alerts`
in its role, and it doesn't update the alert item itself (so the follow-up email rules
live in one place).

`alert-processor` trusts every `status_update` it receives, so **the API must check
before sending**:

- take `by` (`sub`) and `by_name` (`name` claim) only from the verified Cognito token,
  never from the request body;
- map the caller's current group to `by_role`: `doctor` or `nurse` → `staff`,
  `caregiver` → `caregiver`; `family` is refused (`403`);
- for a caregiver, check the link to the resident (pattern 13), else `404`;
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

An acknowledge from Track B (synthetic values):

```json
{
  "kind": "status_update",
  "schema_version": 1,
  "resident_id": "res-0001",
  "new_status": "acknowledged",
  "changed_at": "2026-11-03T02:00:40.000Z",
  "by_role": "staff",
  "by": "3f2b8c1e-0000-4000-8000-000000000001",
  "by_name": "Test Nurse A",
  "target": { "alert_id": "6f1c2e0a-5b3d-5e8f-9a47-000000000001", "event_ts": "2026-11-03T02:00:00.000Z" }
}
```

| Field | Rules |
|---|---|
| `kind` | `status_update` |
| `schema_version` | `1` |
| `resident_id` | `res-NNNN`, or `unassigned` (a cancel from an unassigned wearable) |
| `new_status` | `cancelled` or `acknowledged` |
| `changed_at` | ISO 8601 UTC with ms and `Z`: for a cancel, when the IoT rule received the `alert_cancel` (`received_at`, server time, so a wrong device clock can't move the Query bound or the expiry); the API's time for an acknowledge |
| `by_role` | `resident` (wearable cancel), `staff`, or `caregiver` (Track B) |
| `by` | `null` for `resident`; the caller's Cognito `sub` (a lowercase UUID) for `staff` / `caregiver`, from the verified token |
| `by_name` | `null` for `resident`; for `staff` / `caregiver`, the `name` claim of the verified token: 1-100 characters, not only spaces, no control characters. Used only to build the masked name in the email; never stored, logged, or emailed in full |
| `target` | `{"device_id"}` (every open alert from that device up to `changed_at`) or `{"alert_id", "event_ts"}` (one alert) |

- A `resident` update must be `cancelled` with a device target and `by` / `by_name`
  `null`; a `staff` / `caregiver` update must be `acknowledged` with an alert target, a
  `sub` as `by`, and a `by_name`. Unknown fields are rejected. Anything invalid fails the
  record and ends in `silvercare-alerts-dlq` (where `by_name` can stay up to 14 days).
- `alert-processor` (no repeat check) finds the targets (pattern 24 for a device target,
  pattern 25 for an alert target; none: outcome `no_open_alerts` or `not_open`, not a
  failure). For each, an UpdateItem conditional on `#status = :open`: a cancel sets
  `status = cancelled`, `cancelled_at`, `cancelled_by_role`, `expires_at = changed_at +
  30 days`, and REMOVEs `open_pk` (off the dashboard's open list); an acknowledge sets
  `status = acknowledged`, `acknowledged_at`, `acknowledged_by` and keeps `open_pk` until
  resolved. A failed condition skips that alert without an email.
- Then the follow-up (one email per alert): caregivers/family through
  `silvercare-alerts` with the `resident_id` attribute (not for `unassigned`), and
  nurses through `silvercare-nurses` only if the item has `escalated_at` (an unassigned
  alert's follow-up adds `Device: <device_id>`, or `Device: invalid`); then `SET status_notified_at` with
  `attribute_not_exists(status_notified_at)`. An item that already has the new `status`
  (for an acknowledge, by the same `sub`) but no `status_notified_at` gets its follow-up
  on a retry; a duplicate is accepted, a missing one isn't.
- A cancelled alert's escalation check ends `not_escalated` (`status:cancelled`).
- The S3 `alerts/` copy is not updated: it stays as stored (open).

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
  rule at 365 days *(proposed)*, added only once daily summaries exist.
- **Key layout:**
  `telemetry/message=<message>/date=<yyyy-mm-dd>/<yyyymmddTHHMMSSZ>-<uuid4>.jsonl.gz`.
  `<message>` is the body's `message` if it matches `^[a-z]{1,16}$`, else `unknown`;
  bodies that aren't a JSON object go to `unparseable`. `<date>` is the UTC date of
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
  only, no write or delete), e.g. `scripts/ingest-check.py --history`. Lake Formation
  and Athena later.

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
| alerts | rare writes (plus up to two escalation UpdateItems per alert, and two per alert a status update changes); one strongly consistent Query per non-fall alert (repeat check) and per `alert_cancel` (pattern 24, rare), one strongly consistent GetItem per escalation check and per acknowledge; dashboard polling ≈ **< 1 RCU** |

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
| Alert copies and daily summaries (S3 `alerts/`, `summaries/`) | the resident's lifetime + 6 years (MOH Circular 85/2022) | per-resident deletion at date of death + 6 years; finding one resident's objects and the date of death are gaps (see [S3 history](#s3-history)); no expiry rule until then |
| Failed fall and `vitals_alert` events (SQS `silvercare-critical-alert-dlq`) | up to 14 days | SQS message retention (SSE-SQS encryption, TLS-only queue policy) |
| Latest device `vitals_alert`s | 5 per device | write rule |
| Latest `alert_cancel`s ("I'm OK" presses) | 5 per wearable | write rule |
| Status updates in `silvercare-alerts` (normally seconds; up to 4 days while retried) and `silvercare-alerts-dlq` (a Track B acknowledge carries the user's full name, `by_name`) | up to 4 days in the queue, 14 days in the DLQ | SQS message retention |
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
