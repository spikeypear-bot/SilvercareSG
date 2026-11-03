---
status: draft          # draft | ready | built
---

# Escalation to nurses (Track A3a)

## Summary

If nobody responds to an alert within 60 seconds of the first notification, nurses are
emailed. Every alert escalates (critical and warning), including alerts from
unassigned devices, unless it has been acknowledged or cancelled by then. The check is
a delayed message on the existing alerts queue, handled by `alert-processor`: no new
queue, function, or poller.

Builds on `alerts-core` and `vitals-alerts`. What stops an escalation comes in three
two places: the acknowledge button in the portal, pressed by staff or a linked
caregiver (Track B's API sets `status = acknowledged`, pattern 16), and the wearable
"I'm OK" cancel (A3b, `status = cancelled`). This feature only makes escalation
respect `status`.

## Users and access

No screen and no API in this feature.

| Group | Can see | Can do |
|---|---|---|
| staff (nurse) | escalation emails for every resident, if subscribed (`nurse_subscribers`) | nothing new here (acknowledge in the portal comes with Track B) |
| staff (doctor) | nothing new | nothing new |
| caregiver | nothing new | nothing new here (acknowledge for linked residents comes with Track B) |
| family | nothing new | nothing new |

Staff see every resident, so the nurse topic has no per-resident filter: each nurse
subscriber gets every escalated alert. Who is a nurse subscriber is set in Terraform
variables, like the ops topic.

| Who | Does |
|---|---|
| `alert-processor` | After notifying (or storing an unassigned alert), sends a delayed escalation check; 60 s later handles that check |
| Developer | Adds the nurse subscriber labels, confirms the subscriptions, runs the live check |

## Product decisions (user, 2026-11-03)

| Decision | Value |
|---|---|
| What escalates | every alert, critical and warning, after 60 s with no response |
| Unassigned devices | escalate to nurses after 60 s too (they still email no caregiver) |
| What stops it | `status` other than `open` when the check runs: `acknowledged` (portal button, Track B), `cancelled` (wearable A3b), `resolved` |
| Nurse channel | new SNS email topic `silvercare-nurses`, subscribers listed by label like ops |
| Nurse email | same content as the caregiver email plus an escalation line; subject `SilverCare ESCALATED alert: …` |
| Who can acknowledge | staff for every resident; caregivers for residents they are linked to (pattern 13). Family can view but not acknowledge. Enforced by Track B's API |
| No email link | caregivers acknowledge with the portal button; there is no acknowledge link in emails (no public endpoint) |
| Follow-up after a stop | caregivers get a short update email when an alert is cancelled or acknowledged. **Not in A3a**: sent by A3b / Track B when they change the status |
| Split | A3a escalation (this), A3b wearable cancel |

## Acceptance criteria

### Escalation check message

1. A new message kind on `silvercare-alerts`, alongside the alert message:
   `{"kind": "escalation_check", "schema_version": 1, "alert_id", "resident_id",
   "event_ts"}`, enough to build the alert's key (`pk = RESIDENT#<resident_id>`,
   `sk = ALERT#<event_ts>#<alert_id>`). Alert messages keep their format (no `kind`
   field means an alert). `message.py` validates both; an unknown `kind` or an invalid
   check is reported as a failure (DLQ), like any invalid body.
2. Sent with `DelaySeconds = 60` (per message; the queue's own delay stays 0, so alerts
   are not delayed).

### Queuing the check (alert path)

3. After step 4 (notify) of an assigned alert, and after step 2 (store) of an
   unassigned one, if the stored item has `status = open` and no
   `escalation_queued_at`: send the check, then set `escalation_queued_at` (ISO 8601
   ms Z) with an UpdateItem conditional on `attribute_not_exists(escalation_queued_at)`.
   A failed send fails the record (SQS retries it; the email is not sent again because
   `notified_at` is set). A crash between send and update can queue a second check:
   accepted, the check is idempotent (criterion 6).
4. Suppressed alerts and invalid messages queue no check. A redelivered alert whose
   item already has `escalation_queued_at` queues nothing.

### Handling the check

5. `alert-processor` handles an `escalation_check` without the repeat check:
   strongly consistent GetItem of the alert item.
   - No item: failure (retried, then DLQ and alarm).
   - `status` is not `open`: no email, outcome `not_escalated` with reason
     `status:<value>`.
   - `escalated_at` already set: no email, outcome `duplicate`.
   - Otherwise: Publish to `silvercare-nurses` (no message attributes needed), then set
     `escalated_at` with an UpdateItem conditional on `attribute_not_exists(escalated_at)`
     (a failed condition after Publish is logged as `duplicate`, not a failure).
     Outcome `escalated`. `status` stays `open`.
6. Idempotent like `notified_at`: a crash between Publish and the update sends a
   second nurse email on retry. A duplicate is accepted, a missing one isn't.
7. Log line per check: `alert_id`, `alert_type`, `severity`, `device_id`,
   `resident_id`, `kind = escalation_check`, outcome, reason, and
   `escalation_delay_ms` (Publish time minus `notified_at`, or minus `created_at` for
   unassigned alerts). No `details`, values, email addresses, or email body.
8. Any AWS error fails only that record (ReportBatchItemFailures), as today.

### Nurse email

9. Subject `SilverCare ESCALATED alert: <label> - <resident_id>`, at most 100
   characters, for both severities (`<label>` as in vitals-alerts criterion 1, `Fall`
   for falls).
10. Body: the caregiver email (alerts-core and vitals-alerts criteria 21–22, SGT only)
    with one line after `Severity:`:
    `Escalated: no response within 60 s of the first alert.`
    For unassigned alerts, `Resident: unassigned` and a `Device: <device_id>` line
    (nurses need it to find the device; it is an opaque ID).

### Terraform

11. `aws_sns_topic.nurses` `silvercare-nurses`, encrypted with `alias/aws/sns` (it
    carries health data, like `silvercare-alerts`), with a TLS-only topic policy that
    lists the topic actions (`local.sns_topic_actions`, never `sns:*`).
12. Variable `nurse_subscribers` (list of labels, same validation as
    `ops_subscribers`: `^[a-z0-9-]{1,32}$`, each label in `subscriber_emails`). One
    email subscription per label, no filter policy.
13. `alert-processor` role adds `sqs:SendMessage` on `silvercare-alerts`, `sns:Publish`
    on `silvercare-nurses`, and `dynamodb:GetItem` on the alerts table if it doesn't
    have it (`LeadingKeys` `RESIDENT#*`). Env gains `ALERTS_QUEUE_URL` and
    `NURSES_TOPIC_ARN`. Its `UpdateItem` already covers the new attributes.
14. No new queue, function, event source mapping, or alarm. The alerts queue's DLQ
    alarm covers failed checks.

### CI (main session)

15. `plan.yml` and `deploy.yml` pass `TF_VAR_nurse_subscribers` from the repo variable
    `NURSE_SUBSCRIBERS` (`|| '[]'` in plan), and deploy's "Check subscriber settings"
    guard also requires it, so a missing variable can't destroy the nurse
    subscriptions.

### Docs

16. `docs/data-model.md`: alert item attributes `escalation_queued_at`,
    `escalated_at`; `status` values gain `cancelled` (set by A3b, listed now so the
    check handles it); new pattern: escalation check GetItem / UpdateItem by key;
    pattern 16 (acknowledge) becomes "staff, or caregiver after 13" and
    `acknowledged_by` may be a caregiver's `sub`.
    (Main session: README "Alerts" gets the nurse subscribers, the `NURSE_SUBSCRIBERS`
    variable, and the live check; decision record 0001 notes the delayed check on the
    alerts queue.)

### Tests

17. Unit tests cover: check message validation (valid, unknown kind, missing fields);
    the check is queued after notify (assigned) and after store (unassigned) with
    `DelaySeconds` 60; not queued for suppressed alerts or when
    `escalation_queued_at` exists; a failed send fails the record without a second
    caregiver email; handling: open → nurse email + `escalated_at`; acknowledged,
    cancelled, resolved → `not_escalated`; already escalated → `duplicate`; missing
    item → failure; condition failure after Publish → `duplicate`; nurse subject and
    body per severity, type, and unassigned (with device line); the check skips the
    repeat Query; no values in logs.

## Data

- Reads: the alert item by key (new pattern, strongly consistent).
- Writes: `escalation_queued_at`, `escalated_at` on the alert item (conditional
  UpdateItems).
- Capacity: one GetItem and one or two UpdateItems more per alert, within the current
  provisioned capacity (no change).

## IoT

None.

## API contract

None (Track B's acknowledge route sets `status = acknowledged`, pattern 16).

## Screens

None.

## Alerts and notifications

| Trigger | Who | When |
|---|---|---|
| any alert still `open` 60 s after the first notification | nurse subscribers (all residents) | ~60 s after the caregiver email |
| alert from an unassigned device | nurse subscribers | ~60 s after it is stored |
| acknowledged / cancelled / resolved within 60 s | nobody (no escalation) | — |

## Privacy

- Personal data touched: the same alert content as A1/A2 (opaque resident ID, type,
  reading), now also emailed to nurse subscribers. Unassigned escalations include the
  device ID (opaque).
- Nurses are staff and may see every resident (CLAUDE.md, Auth), so no filter is
  needed. Nurse emails, like caregiver emails, leave `ap-southeast-1` through the
  email provider: covered by the A1 decision (synthetic data only, revisit before real
  data).
- Nurse email addresses sit in `subscriber_emails` (secret) and Terraform state, as
  for A1. Labels in the public plan comment must not be names (`nurse-1`).
- Must not be logged: values, `details`, email addresses, email body.

## Cost

About US$0: one more SQS message and up to three DynamoDB requests per alert, nurse
emails within SNS's 1,000 free emails a month. No new poller, no fixed fees.

## Out of scope

- Wearable "I'm OK" cancel (A3b).
- The acknowledge button and API for staff and linked caregivers (Track B, pattern 16).
- An acknowledge link in emails: dropped (user, 2026-11-03).
- Follow-up emails after a cancel or acknowledge (with A3b / Track B).
- A second escalation level (e.g. doctors) or repeated nurse reminders.
- Watchdog alerts (A4).

## Open questions

None.
