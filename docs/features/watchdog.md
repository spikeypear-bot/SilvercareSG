---
status: ready          # draft | ready | built
---

# Watchdog: device problems (Track A4a)

## Summary

A `watchdog` Lambda runs every 5 minutes and raises alerts for problems defined by
something *not* happening: a device that stopped sending (offline), a low battery, and
a wearable left off the wrist for a long time. They are emailed to the **ops team**
(`silvercare-ops-devices`) and to the **resident's caregivers** (`silvercare-alerts`), and
show in the portal for ops, caregivers, and nurses (Track B reads the alerts table);
nurses get no email. Ops and caregivers can acknowledge one ("I'm on it"), and each
resolves itself quietly when the device recovers.

The possible-fall-at-night check comes next (A4b) on the same watchdog.

## Users and access

No screen and no API in this feature.

| Group | Can see | Can do |
|---|---|---|
| ops (new Cognito group, Track B) | device-problem emails (`ops_subscribers`) and, in the portal, device problems for every resident | acknowledge a device problem |
| caregiver | device-problem emails and in-app alerts for the residents they're linked to | acknowledge a device problem of a linked resident (pattern 13); no cancel on device problems |
| nurse | device problems in the portal only (in-app), no email | nothing on device problems |
| doctor | nothing new | nothing |

(`family` was removed by alert-routing.) In-app alerts are Track B's: the portal reads
the alert items (pattern 12 per resident, pattern 9 for every open one).

| Who | Does |
|---|---|
| `watchdog` | Every 5 minutes: checks every assigned device, raises device-problem alerts, resolves the ones that cleared |
| `device-health` | Also records when a wearable came off the wrist (`worn_false_since`) |
| `alert-processor` | Stores device-problem alerts, emails the ops topic and the resident's caregivers; applies acknowledges and the watchdog's resolves |
| Ops subscribers (`ops_subscribers`) | Get device-problem emails |
| Track B API (later) | Sends acknowledge `status_update`s for ops users and caregivers (criterion 14a) |

## Product decisions (user, 2026-11-03)

| Decision | Value |
|---|---|
| Conditions | device offline, low battery, wearable not worn (A4a); possible fall at night (A4b) |
| Offline | no `status` heartbeat for 15 minutes (3 missed heartbeats) |
| Low battery | `battery_pct` below 20, not while charging (mains-powered bedroom units, `battery_pct` null, never) |
| Not worn | wearable `worn = false` (and not charging) for 2 hours, checked between 08:00 and 22:00 SGT |
| Who is told (2026-11-04) | email + in-app: ops and the resident's caregivers; in-app only: nurses. Severity `warning`, never escalated |
| Recovery | the open alert is resolved automatically when the condition clears; no email |
| Mass offline | if 5 or more devices go offline in the same run (e.g. the simulator stopped), ops get **one** summary email instead of one per device; each alert is still stored. Caregivers still get their own resident's email (2026-11-04) |
| Split | A4a device problems (this), A4b possible fall at night |
| Alert routing (2026-11-04) | device problems are not routed like health alerts (alert-routing's table): ops + caregivers by email, no nurse email, no escalation |
| "I'm OK" button (2026-11-04) | cancels health alerts only (falls, vitals); device problems stay open until the problem clears |
| Portal buttons (2026-11-04) | **Acknowledge** for ops (any resident) and caregivers (linked residents); the follow-up goes to ops and the resident's caregivers by email. No cancel; nurses have no button. It still resolves automatically |
| Ops login (2026-11-04) | new Cognito group `ops` and an ops view (Track B); `by_role = ops` in status updates. Emails name "the ops team", not the person |
| Resolve email | unchanged: none (the in-app alert shows it resolved) |
| Ops email topic (2026-11-04) | device-problem emails, follow-ups, and the mass-offline summary go through a new topic `silvercare-ops-devices`, encrypted with the free AWS managed key, subscribed by the same `ops_subscribers`; `silvercare-ops` stays for CloudWatch alarms only (they can't publish to an `aws/sns`-encrypted topic) |
| Watchdog not running (2026-11-04) | a second alarm emails ops if the watchdog has had no invocation for 15 minutes (schedule stopped) |
| What ops see (2026-11-04) | device problems only, for every resident; not health alerts or resident profiles (Track B enforces; `docs/data-model.md`) |

## Acceptance criteria

### Schedule and inputs

1. New Lambda `silvercare-watchdog` (`lambdas/watchdog/src/handler.py`, python3.13,
   arm64, Powertools layer, like the others), invoked every 5 minutes by an
   EventBridge schedule rule (`rate(5 minutes)`). Reserved concurrency is not needed:
   a run takes seconds.
2. Each run reads, all eventually consistent:
   - every device (registry `by-type`, Query `entity_type = DEVICE`, pattern 4),
     keeping only **assigned** devices (`resident_id` present). Spares are not
     checked;
   - every device's newest `status` (latest `by-message`, Query `watch_type = status`,
     pattern 5);
   - every open alert (alerts `open`, Query `open_pk = OPEN`, pattern 9),
   all paginated. For wearables whose newest status has `worn = false`, a
   BatchGetItem of their `MSG#status` items reads `worn_false_since` (criterion 9).
3. "Now" is the run's start time. Each check below uses the newest status entry
   (`latest`) and `latest_ts`.

### Conditions (alert types)

4. `device_offline`: the device has a `status` item and `now − latest_ts > 15 min`.
   A device that has never sent a `status` is not checked (nothing to compare).
   `event_ts` = `latest_ts` (the last heartbeat).
5. `battery_low`: newest status `battery_pct` is a number below 20 and `charging` is
   not true. Bedroom units with `battery_pct` null are skipped. `event_ts` = that
   status's `ts`.
6. `not_worn`: wearable, newest status `worn = false` and `charging` not true,
   `worn_false_since` at least 2 hours before now, and now between 08:00 and 22:00
   SGT. `event_ts` = `worn_false_since`.
7. An offline device is not also checked for battery or worn (its data is stale).
8. At most one open alert per device and type: if one is already open (from the
   `open` index, same `device_id` and `alert_type`), nothing is sent. Otherwise the
   watchdog sends an alert message (A1 format, `severity = warning`, `details` per
   type: offline `{"last_seen"}`, battery `{"battery_pct"}`, not worn
   `{"since"}`) to `silvercare-alerts`. `alert_id` follows the usual formula, so a
   retry of the same run doesn't make a second alert.

8a. Mass offline: if one run raises `device_offline` for 5 or more devices, each of
    those alert messages has `details.grouped = true`, and the watchdog publishes one
    email to `silvercare-ops-devices` itself: subject `SilverCare device problem: <n> devices
    offline`, body listing the device IDs and their residents with last heartbeat
    times (SGT). `alert-processor` stores and archives grouped alerts and still emails
    each one's caregivers (criterion 13), but sends **no ops email** for them. Fewer
    than 5: no grouping, one ops email per alert as usual. Resolves are never grouped
    (they send no email anyway).

### `worn_false_since` in `device-health`

9. When `device-health` stores a wearable `status` that becomes the newest entry: if
   `worn` is false and the item has no `worn_false_since`, it sets it to that entry's
   `ts`; if `worn` is true, it removes it. Stored on the `MSG#status` item (not in the
   `by-message` projection, so no index change). Bedroom statuses are unchanged.

### Resolving

10. For each open device-problem alert whose condition no longer holds (device back
    online, battery at or above 20 or charging, worn again, or the device is no longer
    assigned), the watchdog sends a `status_update` (the alert-routing format) with
    `new_status = resolved`, `by_role = system`, `by = null`, `by_name = null`, no
    `note` key, `changed_at` = the run's start, and an alert target. It is a new row
    in alert-routing's criterion 9 table (`system | resolved | alert target | null |
    null | absent`); `message.py` accepts `resolved` only from `system` and `system`
    only with `resolved`. A not-worn alert stays open overnight (outside 08:00–22:00 nothing
    new is raised, but nothing is resolved either) until the wearable is worn again
    or put on charge.
11. `alert-processor` applies it like other status updates: conditional on
    `#status IN (:open, :acknowledged)` (an acknowledged device problem keeps
    `open_pk`, so the watchdog still sees it and resolves it), sets `status = resolved`, `resolved_at` (`changed_at`),
    `resolved_by_role = system`, removes `open_pk`, sets `expires_at` = `changed_at`
    + 30 days. **No follow-up email** for system resolves. Not open (already resolved,
    or missing): outcome `not_open`, not a failure. Keeps `acknowledged_at` /
    `acknowledged_by` / `acknowledged_by_role` if present.

### `alert-processor` for device problems

12. New alert types `device_offline`, `battery_low`, `not_worn`: `severity` must be
    `warning`; their `details` are validated per criterion 8 (`last_seen`, `since`
    timestamps; `battery_pct` 0–100; optional `grouped` boolean, offline only).
13. They skip the repeat check (the watchdog keeps one open alert per device and
    type) and the fall enrichment, are stored and archived as usual, then emailed:
    - caregivers: Publish to `silvercare-alerts` with the `resident_id` attribute
      (as health alerts), then `SET notified_at` (`attribute_not_exists`);
    - ops: Publish to `silvercare-ops-devices` (`OPS_TOPIC_ARN`), unless `details.grouped`
      is true, then `SET ops_notified_at` (`attribute_not_exists`).
    Each step is skipped if its timestamp is already set (a redelivery); a failed
    Publish fails the record; a crash between a Publish and its update can send a
    duplicate (accepted). No nurse email, no escalation check, no `escalated_at`.
    Unassigned is impossible (only assigned devices are checked); if
    `resident_id = unassigned` arrives anyway, it is stored and emailed to ops only.
14. Both emails (caregivers and ops) use the same subject and body. Subject:
    `SilverCare device problem: <label> - <device_id> (<resident_id>)`, labels
    `Device offline`, `Low battery`, `Wearable not worn`, at most 100 characters.
    Body (SGT only): `SilverCare device problem`, type, device, resident, one line
    `Last heartbeat: <time>` / `Battery: 15 %` / `Not worn since: <time>`, no health
    readings, the portal link, and the usual footer.
14a. Status updates and device problems (user, 2026-11-04):
    - Routing: the three types bypass alert-routing's criterion 1 table (criterion
      13 instead); the alert log line has `nurses = none` and `ops` =
      `emailed` / `grouped`.
    - Contract: two new rows in alert-routing's criterion 9 table:
      `ops | acknowledged | alert target | Cognito sub | null | absent` and
      `caregiver | acknowledged | alert target | Cognito sub | name claim | absent`
      (caregiver `cancelled` stays). Plus `system | resolved` (criterion 10).
    - Which role may change which type, checked by `alert-processor` after reading
      the target (it can't be validated earlier): device problems accept only an
      `ops` or `caregiver` acknowledge and the `system` resolve; health alerts accept
      only a `nurse` acknowledge and a `resident` / `caregiver` cancel (as
      alert-routing). Anything else (e.g. a nurse acknowledging a low battery, a
      caregiver cancelling one, ops acknowledging a fall): nothing changes, no email,
      outcome `not_allowed`, not a failure.
    - A device-problem acknowledge sets `status = acknowledged`, `acknowledged_at`,
      `acknowledged_by` (`sub`), `acknowledged_by_role` (`ops` / `caregiver`), keeps
      `open_pk`, conditional on `#status = :open`; then a follow-up to **both** the
      resident's caregivers (`silvercare-alerts`, `resident_id` attribute) and ops
      (`silvercare-ops-devices`), then `status_notified_at` (as alert-cancel). No nurse email.
      Subject `SilverCare update: <label> - <device_id> (<resident_id>) acknowledged`;
      first line `The device problem below was acknowledged by the ops team at <time>
      SGT.` or `... by Ta* Me* Li** (caregiver) at ...` (alert-cancel masking), then
      the criterion 14 lines. The retry rule of alert-cancel applies (same `sub`).
    - "I'm OK" (resident, device target): the target Query skips the three types
      (filter `NOT alert_type IN (:offline, :battery, :not_worn)`), so they stay open.
    - Nurses: no email for device problems at all; they see them in the portal.

### Errors and monitoring

15. Any AWS error fails the run (raise): EventBridge retries async invocations (Lambda's
    2 retries), and the next run 5 minutes later starts fresh. No DLQ: nothing is lost
    because each run recomputes everything. A CloudWatch alarm `silvercare-watchdog-errors`
    (Lambda `Errors` ≥ 1 in 3 consecutive 5-minute periods) emails `silvercare-ops`.
    A second alarm `silvercare-watchdog-not-running` (Lambda `Invocations` < 1 in 3
    consecutive 5-minute periods, missing data = breaching) emails `silvercare-ops`
    when the schedule stops.
16. Log one summary line per run: devices checked, alerts raised and resolved per
    type, and one line per raised or resolved alert (`alert_id`, `alert_type`,
    `device_id`, `resident_id`). No battery values or timestamps of devices beyond
    IDs.

### IAM and Terraform

17. `watchdog` role: `dynamodb:Query` on the registry `by-type` index, the latest
    `by-message` index, and the alerts `open` index; `dynamodb:BatchGetItem` on the
    latest table (`LeadingKeys` `DEVICE#*`); `sqs:SendMessage` on `silvercare-alerts`;
    `sns:Publish` on `silvercare-ops-devices` (summary email); its own log group.
    `device-health` role: unchanged (it already writes `MSG#status`).
    `alert-processor`: `sns:Publish` on `silvercare-ops-devices` (it already has
    `silvercare-alerts`); the new topic (encrypted with `alias/aws/sns`, TLS-only, explicit actions) allows only these two Lambdas to publish and only the deploy role to subscribe; `silvercare-ops` keeps its alarms-only policy (keep the
    CloudWatch Allow; follow the iam-policy skill). Env `OPS_TOPIC_ARN`. The Cognito
    `ops` group is Track B's (not in this feature).
18. EventBridge rule + target + `aws_lambda_permission` for `events.amazonaws.com`
    with `source_arn` = the rule.

### Live check support

18a. `scripts/iot-smoke-test.py --low-battery` (any device type): after the checks,
    publishes one `status` with `battery_pct` 10, not charging. Prints what to expect:
    within 5 minutes (next watchdog run) "SilverCare device problem: Low battery -
    <device> (<resident>)" to ops **and** to the resident's caregivers, none to
    nurses; then run the plain smoke test (normal battery) and
    the alert resolves quietly at the next run. Also notes that ~15 minutes after the
    last status the device is reported offline. It also prints: use an assigned device
    the simulator isn't sending for (a simulator heartbeat with a normal battery would
    overwrite the test one and resolve or prevent the alert). `make smoke-test …
    LOW_BATTERY=1` (main session: Makefile).

### Docs

19. `docs/data-model.md`: alert types, `worn_false_since`, `resolved_by_role`,
    patterns 4, 5 and 9 now used by `watchdog`, status update `resolved`/`system` (a
    row in the rules table; the `system` role is sent only by the watchdog, never by
    Track B's API), the `ops` and caregiver acknowledge rows, the type/role rule and
    `not_allowed` outcome (criterion 14a), attributes `ops_notified_at` and
    `acknowledged_by_role`, and sender checks for Track B: an `ops` acknowledge needs
    the caller's current group `ops`; a caregiver acknowledge needs `caregiver` and
    the link (pattern 13); both only for the three device-problem types, and a cancel
    or nurse acknowledge only for health alerts (else `409`). Pattern 16 gains the
    device-problem acknowledge.
    `docs/metrics.md` "Device problems" bullet: built with these thresholds. (Main
    session: CLAUDE.md Auth gains the `ops` group; README "Alerts" ops and caregiver
    check; decision record 0001 watchdog.)

### Tests

20. Unit tests: each condition at its boundary (15 min exactly is not offline; 20 % is
    not low; 2 h exactly is not-worn; 07:59 / 08:00 / 21:59 / 22:00 SGT); charging
    skips battery and not-worn; null battery skipped; never-seen and unassigned devices
    skipped; offline suppresses battery/worn; one open alert per device and type;
    resolve when cleared (each type) and when unassigned; `worn_false_since` set,
    kept, removed in `device-health`; alert-processor emails the 3 types to
    caregivers and ops (not nurses), redelivery idempotency per timestamp, failed
    ops Publish fails the record without a second caregiver email, no repeat check,
    no escalation; system resolve of an open and of an acknowledged alert sets the
    fields and sends no email;
    validation of the new types and the resolved/system rule; ops subject and body;
    run summary log without values; grouping at 4 vs 5 offline devices (summary email
    once, grouped alerts emailed to caregivers but not to ops by alert-processor);
    `--low-battery` payload; criterion 14a: ops and caregiver acknowledge of a device
    problem (fields, follow-up to caregivers and ops with the right first line, no
    nurse email, retry), every disallowed role/type pair gives `not_allowed` with no
    change and no email, "I'm OK" leaves device problems open while cancelling a health
    alert from the same wearable; validation of the new rows (`ops` only with
    `acknowledged`, `system` only with `resolved`, caregiver `acknowledged` with
    `by_name`).

## Data

- Reads per run: registry `by-type` (~40 devices), latest `by-message` `status` (~40
  items), alerts `open`, and a BatchGetItem for wearables currently off the wrist.
  About 288 runs a day, each a few RCU on the indexes: within the current provisioned
  capacity.
- Writes: alerts via the queue (rare); `worn_false_since` on `MSG#status` items (only
  when `worn` changes).

## IoT

None (uses existing `status` messages).

## API contract

None in this feature. The ops and caregiver acknowledge `status_update` rows
(criterion 14a) are the contract Track B's API sends.

## Screens

None.

## Alerts and notifications

| Trigger | Who | Severity |
|---|---|---|
| no heartbeat for 15 min | email + in-app: ops, the resident's caregivers; in-app: nurses | warning |
| battery below 20 % (not charging) | same | warning |
| wearable off for 2 h (08:00–22:00 SGT, not charging) | same | warning |
| 5+ devices offline in one run | ops: one summary email; caregivers: their own resident's email | warning |
| ops or a caregiver acknowledges | email + in-app: ops and the resident's caregivers | — |
| condition clears | nobody by email (resolved quietly; in-app shows it) | — |

## Privacy

- Data touched: device IDs, heartbeat times, battery level, whether a wearable is worn
  (which hints at a resident's routine). Emailed to ops subscribers and to the
  resident's caregivers (team addresses for now) with the opaque resident ID; no
  names, no health readings. An acknowledge stores the `sub` and role; caregivers'
  names appear only masked in emails, ops users as "the ops team".
- Retention: device-problem alerts like other alerts (resolved → 30 days after
  `resolved_at`); S3 archive per Open decision #4.
- Must not be logged: battery values, timestamps beyond IDs, `details`.

## Cost

About US$0: ~8,640 Lambda runs a month (free tier), the EventBridge schedule (free),
a few RCU per run within provisioned capacity, two more alarms (3 → 5 of the 10 free), one more SNS topic (free; the AWS managed key has no monthly fee),
rare ops emails.

## Out of scope

- Possible fall at night (A4b).
- The portal: in-app alerts, the ops view, the Acknowledge buttons, and the Cognito
  `ops` group (Track B).
- Checking spare (unassigned) devices.

## Open questions

None.
