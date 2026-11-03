---
status: ready          # draft | ready | built
---

# Wearable "I'm OK" cancel and follow-up emails (Track A3b)

## Summary

The resident can cancel their alerts with an "I'm OK" button on the wearable. The
wearable sends a new `alert_cancel` message, and every alert from that wearable that is
still `open` becomes `cancelled`: it no longer escalates, and caregivers (plus nurses,
if they were already escalated to) get a short follow-up email.

The same follow-up path serves acknowledge: a new `status_update` message on the alerts
queue changes an alert's status and sends the follow-up. Cancels use it now; Track B's
acknowledge API sends it later, so the email rules live in one place.

Builds on `escalation` (A3a), which already stops escalating anything not `open`.

## Users and access

No screen and no API in this feature.

| Group | Can see | Can do |
|---|---|---|
| staff (nurse) | follow-up emails for alerts they were escalated, if subscribed | nothing new here (acknowledge via Track B) |
| staff (doctor) | nothing new | nothing new |
| caregiver | follow-up emails for the residents they're subscribed to | nothing new here (acknowledge via Track B) |
| family | follow-up emails for the residents they're subscribed to | nothing new |

Access to a specific resident: unchanged. Caregiver and family follow-ups use the
`silvercare-alerts` topic with the `resident_id` filter, as alerts do.

| Who | Does |
|---|---|
| Resident (wearable) | Presses "I'm OK"; the wearable publishes `alert_cancel` |
| `critical-alert` | Validates `alert_cancel`, looks up the resident, sends a `status_update` to the alerts queue, stores the message in the latest 5 |
| `alert-processor` | Applies a `status_update` to the matching open alerts and sends the follow-ups |
| Track B API (later) | Sends a `status_update` (`acknowledged`) when someone presses acknowledge |

## Product decisions (user, 2026-11-03)

| Decision | Value |
|---|---|
| What the resident can cancel | any open alert from their own wearable: falls, warnings, and critical vitals alerts (including no pulse) |
| When | while the alert is `open` (not yet acknowledged, cancelled, or resolved). Before 60 s it stops the escalation; after it, nurses get the follow-up too |
| Which alerts one press cancels | every alert from that wearable that is `open` when the press reaches the cloud |
| Follow-up recipients | caregivers/family subscribed to the resident, always; nurses only if they already got the ESCALATED email |
| Acknowledge follow-up | sent through the same `status_update` message, by Track B's API |
| Retention of cancelled alerts | like resolved: the cancel removes `open_pk` (off the dashboard's open list) and sets `expires_at` = cancel time + 30 days; the S3 archive copy stays |
| Acknowledger in emails | masked name plus role, PayNow-style: each word keeps its first 2 letters, the rest become `*` (word lengths kept): "Tan Mei Ling" → `Ta* Me* Li**`, then ` (staff)` or ` (caregiver)`. The full name shows only in the portal |
| Device contract | the wearable gains an "I'm OK" button and the `alert_cancel` message (the v1 "no SOS button" out-of-scope line is narrowed: still no SOS/help button) |

## Acceptance criteria

### Device message `alert_cancel`

1. `docs/metrics.md`: new v1 wearable message `alert_cancel` on
   `silvercare/v1/<device_id>/alert_cancel`, envelope only (`schema_version`,
   `message_id`, `ts`), no other fields. Sent once per press. Rate: rare (at most one
   per press, presses at least 5 s apart on the device). "Out of scope for v1" keeps
   the SOS/help button and notes the "I'm OK" button is in.
2. The wearable device policy allows publishing to
   `silvercare/v1/<own id>/alert_cancel`. Bedroom devices can't. In-place update.
3. The `silvercare_alert` rule's SQL also matches `alert_cancel` (with `fall` and
   `vitals_alert`), keeping its history and error actions. In-place update.

### `critical-alert` handles `alert_cancel`

4. Validates it (wearable only, envelope only; unknown fields rejected), then in the
   usual order: registry lookup (pattern 1), send a `status_update` (criterion 6) to
   `silvercare-alerts`, store the message in the device's latest 5
   (`MSG#alert_cancel`, no `watch_type`). A failed send raises (Lambda retries, then
   the critical-alert DLQ). Unassigned devices use `resident_id = unassigned`.
5. Log line: `device_id`, message, `message_id`, outcome, plus
   `status_update` = `queued`. No other fields.

### `status_update` message

6. A third message kind on `silvercare-alerts`:

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

   - `new_status`: `cancelled` or `acknowledged`.
   - `by_role`: `resident` (wearable cancel), `staff`, or `caregiver` (Track B).
   - `by`: `null` for the resident; the Cognito `sub` for staff and caregivers.
   - `by_name`: `null` for the resident; for staff and caregivers, the user's full
     name from their verified Cognito token (`name` claim), 1–100 characters. Used only
     to build the masked name in the email; never stored, logged, or emailed in full.
   - `target`: either `{"device_id"}` (every open alert from that device, used by
     cancel) or `{"alert_id", "event_ts"}` (one alert, used by acknowledge).
   - `changed_at` is the time AWS IoT received the cancel (the rule's `received_at`,
     not the device's `ts`, so a wrong device clock can't skip alerts or shift the
     expiry), and the API's time for an acknowledge.
   - `message.py` validates it; anything invalid is a failure (DLQ). Rules: a
     `resident` update must be `cancelled` with a device target and `by = null`;
     `staff`/`caregiver` updates must be `acknowledged` with an alert target, a
     `sub`-shaped `by`, and a `by_name`.
7. Sent without delay. `docs/data-model.md` documents it as the contract Track B's API
   uses (with the IAM it needs: `sqs:SendMessage` on `silvercare-alerts`).

### `alert-processor` applies the update

8. No repeat check. Find the target alerts:
   - device target: Query `pk = RESIDENT#<resident_id>`, `sk <= "ALERT#<changed_at>#~"`,
     strongly consistent, filter `device_id = :d AND #status = :open` (new pattern);
     paginate.
   - alert target: strongly consistent GetItem by key.
   No matching open alert: outcome `no_open_alerts` (cancel) or `not_open`
   (acknowledge), not a failure.
9. For each target: UpdateItem conditional on `#status = :open`, setting
   `status = new_status` and `cancelled_at`/`cancelled_by_role` (cancel) or
   `acknowledged_at`/`acknowledged_by` (acknowledge). A cancel also removes `open_pk`
   and sets `expires_at` = `changed_at` + 30 days (epoch seconds), like resolve. A
   failed condition (it changed meanwhile) skips that alert without an email.
10. After a successful update, send the follow-up (criterion 12), then set
    `status_notified_at` with an UpdateItem conditional on
    `attribute_not_exists(status_notified_at)`. On a retry, an alert whose `status`
    already equals `new_status` but has no `status_notified_at` gets its follow-up
    then (so a crash after the status update doesn't lose the email; a duplicate is
    accepted).
11. Log line per alert: `alert_id`, `alert_type`, `severity`, `device_id`,
    `resident_id`, `kind = status_update`, `new_status`, `by_role`, outcome
    (`updated`, `skipped`, `no_open_alerts`, `not_open`). Never `by`, `by_name`,
    `details`, or email content.

### Follow-up emails

12. To caregivers/family: Publish to `silvercare-alerts` with the `resident_id`
    attribute (skipped for unassigned). To nurses: Publish to `silvercare-nurses` only
    if the item has `escalated_at`.
13. Subject: `SilverCare update: <label> - <resident_id> cancelled` or
    `… acknowledged`, at most 100 characters (`Alert` label for an unknown type).
14. Body (SGT only):

    ```
    SilverCare update

    The alert below was cancelled by the resident at 2026-11-03 10:00:05 SGT.

    Type: Fall
    Severity: Critical
    Resident: res-0001
    When: 2026-11-03 10:00:00 SGT

    Open the SilverCare portal: https://<cloudfront domain>/

    This is an automated message. Do not reply.
    ```

    Acknowledge: `…was acknowledged by Ta* Me* Li** (staff) at …` or
    `… (caregiver) at …`, using the masked name (decisions table): each word of
    `by_name` keeps its first 2 characters and every further character becomes `*`;
    a 1–2 character word is shown as is; words are split on spaces.
    Unassigned alerts (nurse follow-up only) add `Device: <device_id>` after
    `Resident: unassigned`, or `Device: invalid` if it doesn't match the format. Subject:
    `… acknowledged` (no name). No reading or details lines.

### IAM and Terraform

15. `critical-alert` role: unchanged except that it already sends to
    `silvercare-alerts` (check). `alert-processor` role adds `dynamodb:Query` on the
    alerts table for the device-target query if not already covered (`LeadingKeys`
    `RESIDENT#*`) and `sns:Publish` on `silvercare-alerts` (already) and
    `silvercare-nurses` (already). Only in-place policy updates.
16. No new resources.

### Live check support

17. `scripts/iot-smoke-test.py --cancel` (wearables only): after the existing checks,
    publish a `fall`, wait 5 s, then `alert_cancel`. Prints what to expect: the fall
    email, then a "cancelled" update email, and **no** nurse email (the cancel lands
    within 60 s). Needs no AWS credentials. `make smoke-test … CANCEL=1` (main
    session: Makefile).

### Docs

18. `docs/metrics.md` (criterion 1), `docs/data-model.md`: the `status_update`
    contract, the new query pattern, attributes `cancelled_at`, `cancelled_by_role`,
    `status_notified_at`, and `MSG#alert_cancel`; retention table: cancelled alerts
    30 days after the cancel. (Main session: README "Alerts" live
    check; decision record 0001 rule table.)

### Tests

19. Unit tests cover: `alert_cancel` validation (wearable only, envelope only, bedroom
    rejected) and its path (lookup → send → store, failed send raises, unassigned);
    `status_update` validation (each rule in criterion 6); device target cancels every
    open alert of that device and no other device's or already non-open alert, and none
    after `changed_at`; alert target acknowledges one; condition failure skips
    without email; follow-up to caregivers always (not unassigned), to nurses only with
    `escalated_at`; retry after the status update sends the missing follow-up once;
    subjects and bodies for cancel and both acknowledge roles; name masking
    (one word, several words, 1–2 letter words, extra spaces, non-Latin letters); a cancelled alert's later
    escalation check is `not_escalated`; no `by`, values, or details in logs.

## Data

- Reads: registry assignment (pattern 1); the resident's alerts by device and status
  (new pattern, strongly consistent Query); one alert by key.
- Writes: `status`, `cancelled_at`, `cancelled_by_role`, `acknowledged_at`,
  `acknowledged_by`, `status_notified_at` on alert items; `MSG#alert_cancel` in
  `silvercare-latest`.
- Capacity: a press is rare; no change.

## IoT

New wearable message `alert_cancel` (criteria 1–3).

## API contract

None in this feature. The `status_update` message (criterion 6) is the contract
Track B's acknowledge route uses.

## Screens

None.

## Alerts and notifications

| Trigger | Who | What |
|---|---|---|
| resident presses "I'm OK" | caregivers/family of the resident; nurses if already escalated | "SilverCare update: … cancelled" |
| staff or caregiver acknowledges (Track B, later) | same | "SilverCare update: … acknowledged" |
| cancel within 60 s | nurses | nothing (the escalation is skipped) |

## Privacy

- Personal data touched: alert status changes and who changed them (role; a Cognito
  `sub` for acknowledge, stored on the item, never logged or emailed).
- Follow-up emails carry type, severity, resident ID, times, and for acknowledge a
  masked name and role (user decision). Full names never appear in email or logs.
- `by_name` (a full name) passes through the alerts queue, encrypted, and can sit in
  `silvercare-alerts-dlq` for up to 14 days if processing keeps failing; it is not
  written to the alert item or the S3 archive.
- Retention: cancelled alerts are deleted from DynamoDB 30 days after the cancel (TTL), like resolved ones; the S3 `alerts/` copy follows Open decision #4.

## Cost

About US$0: a press adds one IoT message, one SQS message, one Query, and one or two
emails.

## Out of scope

- An SOS/help button (still out of scope for v1).
- Undoing a cancel.
- Resolving alerts, and the acknowledge button/API itself (Track B).
- Simulator support for the button (Track C; the smoke test covers the live check).

## Open questions

None.
