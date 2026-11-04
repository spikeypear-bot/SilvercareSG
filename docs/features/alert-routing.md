---
status: ready          # draft | ready | built
---

# Alert routing: critical to nurses at once, caregiver cancel, nurse acknowledge note (Track A3c)

## Summary

Team changes (meeting, 2026-11-04) to who is emailed and when:

- **Critical alerts** (fall, the wearable's own `vitals_alert`, including no pulse) and
  **alerts from unassigned devices** go to the nurses **at once**, together with the
  caregivers. No 60 s wait.
- **Warnings** (two abnormal readings in a row) still escalate, but after **5 minutes**
  instead of 60 s, so a caregiver has time to reach the resident first.
- In the portal, **nurses acknowledge** (with an optional note, e.g. "Ambulance on the
  way, 8 minutes", emailed to the caregivers) and **caregivers cancel**. A cancel emails
  every caregiver of the resident, and the nurses if they were involved.
- The `family` role is dropped: family members are caregivers. Nurses are a care
  centre (one or two nurses on one shared account) and appear in emails as "the care
  centre nurse". Doctors don't acknowledge or cancel.

This changes `alert-processor` and the `status_update` contract (A3b) that Track B's
API will send. The buttons, the API route, and the Cognito group change are Track B's.

## Users and access

No screen and no API in this feature.

| Group | Can see | Can do (through Track B's API, which sends a `status_update`) |
|---|---|---|
| nurse | every critical and unassigned alert at once; every warning still open after 5 min; cancel follow-ups for alerts they were emailed about or acknowledged | acknowledge any alert, with an optional note |
| doctor | nothing new (no alert emails) | nothing (no acknowledge, no cancel) |
| caregiver | every alert of the residents they're subscribed to; follow-ups (cancelled, acknowledged with the note) | cancel alerts of residents they are linked to (pattern 13) |
| family | removed: family members get caregiver accounts | nothing (refused by the API until Track B removes the group) |

Access to a specific resident: unchanged. Caregiver emails use the `silvercare-alerts`
topic with the `resident_id` filter, so every caregiver subscribed to a resident gets
them (there can be several). Nurses use `silvercare-nurses`, no filter.

| Who | Does |
|---|---|
| `alert-processor` | Routes each alert (caregivers, nurses at once, or a 5-minute escalation check); applies cancels and acknowledges; sends the follow-ups |
| Track B API (later) | Sends `status_update` messages for the nurse acknowledge and the caregiver cancel, after the checks in criterion 9 |

## Product decisions (user, 2026-11-04)

| Decision | Value |
|---|---|
| Escalation wait | 5 minutes (was 60 s), for warnings from assigned devices |
| Nurses at once | critical alerts (fall, device `vitals_alert` including no pulse; later A4b possible fall) and every alert from an unassigned device, any severity |
| Who acknowledges | nurses only, any resident. Caregivers no longer acknowledge; doctors neither |
| Who cancels | the resident ("I'm OK", A3b) and caregivers linked to the resident (portal) |
| When a cancel works | while the alert is `open` or `acknowledged` (not once `cancelled` or `resolved`); applies to the wearable button too |
| Acknowledge note | optional, up to 500 characters, typed by the nurse; emailed to the caregivers and stored on the alert so the portal can show it |
| Acknowledger in emails | "the care centre nurse" (the account is shared, so no name) |
| Canceller in emails | a caregiver: masked name PayNow-style, as A3b (`Ta* Me* Li** (caregiver)`); the resident: "the resident" |
| Follow-up after a nurse acknowledge | caregivers only, with the note |
| Follow-up after a cancel | every caregiver of the resident (the one who cancelled too: email can't leave one out); nurses if they were emailed about the alert or had acknowledged it |
| Family role | removed; family members are caregivers (Track B removes the Cognito group) |
| Nurse wording | "care centre nurse" in emails and the UI; technical names (`silvercare-nurses`, `NURSE_SUBSCRIBERS`, group `nurse`) stay |
| SMS for caregivers | not now (cost per SMS, the US$1/month default SMS cap, phone number verification); a later feature |

## Acceptance criteria

### Routing a new alert (`alert-processor`, alert path)

1. After storing (and archiving) an alert, `alert-processor` routes it by resident and
   severity:

   | Alert | Caregivers (`silvercare-alerts`) | Nurses (`silvercare-nurses`) | Escalation check |
   |---|---|---|---|
   | assigned, `critical` | at once | at once | none |
   | assigned, `warning` | at once | after 5 min if still `open` | `DelaySeconds = 300` |
   | `unassigned`, any severity | nobody | at once | none |

   A stored item with no `severity` (stored before vitals alerts: falls) counts as
   critical. Device-problem types from A4a are not affected (they don't exist yet).
2. Order and idempotency for an assigned critical alert: caregiver Publish → `SET
   notified_at` (as today) → nurse Publish → `SET escalated_at` with
   `attribute_not_exists(escalated_at)`. For unassigned: nurse Publish → `SET
   escalated_at`. `escalated_at` now means "the nurses were emailed" (at once or after
   the wait). A redelivered message whose item has `notified_at` but no `escalated_at`
   sends only the nurse email; one with both sends nothing. A crash between a Publish
   and its update can send a duplicate (accepted); a missing email isn't.
3. A failed nurse Publish fails the record (SQS retries it; the caregiver email is not
   sent again because `notified_at` is set). No escalation check is queued for
   critical or unassigned alerts, so `escalation_queued_at` is not set on them.
4. Warnings keep A3a's path with the new delay: the check is sent with
   `DelaySeconds = 300`. Handling the check is unchanged (`open` → nurse email; any
   other `status`, `escalated_at` set, or more than **20** minutes after `created_at` →
   nothing). The stale limit goes from 15 to 20 minutes (user, 2026-11-04): a check
   that keeps failing is retried every 180 s, and its 5th try (about 17 minutes) must
   still count, so it reaches the DLQ and its alarm instead of being dropped silently.
5. Logs: the alert's log line gains `nurses` = `immediate`, `escalation_queued`, or
   `none` (suppressed alerts). No other new fields.

### Emails for a new alert

6. Nurse email sent at once: same subject as the caregiver email (`SilverCare CRITICAL
   alert: <label> - <resident_id>` or `SilverCare alert: …`), body as the caregiver
   email with one line after `Severity:`:
   - critical: `Sent to the care centre at once: critical alert.`
   - unassigned: `Sent to the care centre at once: device not assigned to a resident.`
     (plus the existing `Device: <device_id>` line).
7. The escalation email (warnings) line becomes
   `Escalated: no response within 5 minutes of the first alert.`
8. The caregiver email for a critical alert adds, after `When:`, a blank line and
   `The care centre nurse has also been alerted.` Warnings: no such line.

### `status_update` contract (changed)

9. Rules, replacing A3b's `staff`/`caregiver` acknowledge:

   | `by_role` | `new_status` | `target` | `by` | `by_name` | `note` |
   |---|---|---|---|---|---|
   | `resident` | `cancelled` | `{"device_id"}` | `null` | `null` | absent |
   | `caregiver` | `cancelled` | `{"alert_id", "event_ts"}` | Cognito `sub` | `name` claim, 1–100 characters (as A3b) | absent |
   | `nurse` | `acknowledged` | `{"alert_id", "event_ts"}` | Cognito `sub` | `null` | optional |

   - `note`: a string of 1–500 characters, not only whitespace, no control or other
     non-printing characters (Unicode category C: bidi overrides, zero-width
     characters) except newline (`\n`). Absent or `null` means no note.
   - `staff`, `family`, or any other `by_role`, or any other combination, is invalid
     (failure → DLQ), as today. Unknown fields are rejected.
   - The sender's checks (Track B), written into `docs/data-model.md` "Status updates":
     identity (`sub`, `name`) only from the verified token; a nurse acknowledge needs
     the caller's current group to be `nurse` (else `403`); a caregiver cancel needs
     `caregiver` (else `403`) and the link to the resident (pattern 13, else `404`);
     `doctor` and `family` get `403`; the note comes from the request body, trimmed;
     `resident_id`, `alert_id`, `event_ts` from the stored alert (read it first).

### Applying a status update

10. Targets: a device target Queries as today (pattern 24) but filters
    `#status IN (:open, :acknowledged)`; an alert target is a strongly consistent
    GetItem (pattern 25). A cancel applies to `open` or `acknowledged` alerts; an
    acknowledge only to `open` ones. Nothing to change: outcome `no_open_alerts`
    (device target) or `not_open` (alert target), not a failure.
11. UpdateItem, conditional on the status it may change from:
    - cancel: condition `#status IN (:open, :acknowledged)`; sets `status = cancelled`,
      `cancelled_at`, `cancelled_by_role` (`resident` or `caregiver`), `cancelled_by`
      (caregiver `sub`; absent for the resident), `expires_at` = `changed_at` + 30 days,
      REMOVEs `open_pk`. Keeps `acknowledged_at`/`acknowledged_by`/`ack_note` if present.
    - acknowledge: condition `#status = :open`; sets `status = acknowledged`,
      `acknowledged_at`, `acknowledged_by` (nurse `sub`), and `ack_note` if a note was
      sent; keeps `open_pk`.
    A failed condition skips the alert without an email (as today). The retry rule
    stays: an item already at the new `status` (for a caregiver cancel or nurse
    acknowledge, by the same `sub`) without `status_notified_at` gets its follow-up.
12. Log line per alert as today, `by_role` values `resident`, `caregiver`, `nurse`,
    plus `has_note` (true/false) for acknowledges. Never `by`, `by_name`, `note`,
    `details`, or email content.

### Follow-up emails

13. Recipients:
    - cancel (resident or caregiver): caregivers through `silvercare-alerts` with the
      `resident_id` attribute (not for `unassigned`); nurses through `silvercare-nurses`
      if the item has `escalated_at` **or** was `acknowledged` before the cancel.
    - acknowledge: caregivers only (not for `unassigned`: nobody is emailed, and
      `status_notified_at` is still set).
14. Subjects unchanged (`SilverCare update: <label> - <resident_id> cancelled` /
    `… acknowledged`).
15. Body first line:
    - `The alert below was cancelled by the resident at <time> SGT.`
    - `The alert below was cancelled by Ta* Me* Li** (caregiver) at <time> SGT.`
      (A3b masking of `by_name`).
    - `The alert below was acknowledged by the care centre nurse at <time> SGT.`
    - A cancel after an acknowledge adds, after the first line: `It had been
      acknowledged by the care centre nurse at <time> SGT.`
    Then the usual `Type`/`Severity`/`Resident`/(`Device`)/`When` lines. An
    acknowledge with a note adds, after `When:`, a blank line,
    `Message from the care centre:`, and the note's lines, each prefixed with `> `
    (user, 2026-11-04: so a note can't pass for SilverCare's own lines, e.g. a fake
    portal link). Then the portal link and the footer.

### Docs

16. `docs/data-model.md`:
    - "Status updates": the new rules table, `note`, the sender's checks (criterion 9);
      the cancel/acknowledge outcomes; nurses' follow-up rule.
    - Alert item: `escalated_at` = nurses emailed (at once or after the wait);
      `cancelled_by_role` values `resident`, `caregiver`; new `cancelled_by`
      (caregiver `sub`), `ack_note` (free text, max 500, shown in the portal to
      everyone who can see the alert, never logged); `acknowledged_by` is a nurse `sub`.
    - Pattern 16: acknowledge = nurse; cancel = caregiver after 13; resolve unchanged.
    - "Escalation": the routing table (criterion 1) and 300 s.
    - Remove `family` from the role text ("caregiver/family" → "caregiver"), noting the
      group is removed by Track B; `relationship` on links is `caregiver`.
    - Retention: `ack_note` is deleted with the alert (30 days after resolve or
      cancel); it can sit in `silvercare-alerts-dlq` up to 14 days if processing fails.
17. `docs/features/escalation.md` and `alert-cancel.md`: a short "Changed by
    alert-routing (2026-11-04)" note under their decisions tables (60 s → 5 min,
    critical/unassigned at once, nurse-only acknowledge, caregiver cancel). (Main
    session: README "Alerts" timings and live-check expectations; CLAUDE.md Auth;
    decision record 0001 escalation line.)

### Live check support

18. `scripts/iot-smoke-test.py`: the printed expectations change, no new flag:
    - `--alerts`: the two critical alerts → caregiver **and** nurse emails at once
      (subject `SilverCare CRITICAL alert`, with the care-centre line); the warning →
      caregiver email at once, nurse `ESCALATED` email about 5 minutes later.
    - `--cancel`: the fall email to caregivers and nurses at once, then a `cancelled`
      follow-up to **both** (the nurses were emailed).
    - `--ingest` (fall): caregiver and nurse emails at once.
    The caregiver cancel and nurse acknowledge can't be checked live until Track B's
    API exists; unit tests cover them.

### Tests

19. Unit tests cover:
    - routing table: assigned critical (caregiver + nurse at once, no check, both
      timestamps), assigned warning (check with `DelaySeconds` 300, no nurse email),
      unassigned warning and critical (nurse at once, no check), missing severity →
      critical; redelivery with `notified_at` only → nurse email only; with both →
      nothing; failed nurse Publish fails the record without a second caregiver email;
    - nurse email lines (critical, unassigned), escalation line (5 minutes), caregiver
      critical line, warning without it;
    - `status_update` validation: each row of criterion 9, `staff`/`family` rejected,
      note length 500 vs 501, whitespace-only, control characters, newline allowed,
      note on a cancel rejected, `by_name` on a nurse update rejected;
    - cancel of an `acknowledged` alert (device and alert target), keeping the
      acknowledge fields, nurses emailed even without `escalated_at`; cancel of
      `cancelled`/`resolved` → nothing; acknowledge of `acknowledged` → `not_open`;
    - acknowledge stores `ack_note`, emails caregivers only, with the note block;
      without a note, no block; unassigned acknowledge emails nobody and sets
      `status_notified_at`;
    - caregiver cancel body (masked name), resident cancel body, cancel-after-acknowledge
      line;
    - logs: `nurses` field, `has_note`, never `note`, `by`, `by_name`.

## Data

- Reads: unchanged patterns (24 with the wider status filter, 25).
- Writes: `escalated_at` on critical and unassigned alerts at once; new
  `cancelled_by`, `ack_note`. One more Publish and UpdateItem per critical alert.
- Capacity: unchanged within provisioned capacity.

## IoT

None.

## API contract

None in this feature. The `status_update` message (criterion 9) is the contract
Track B's acknowledge and cancel routes send.

## Screens

None (Track B: the nurse "Acknowledge" button with a note box, and the caregiver
"Cancel alert" button).

## Alerts and notifications

| Trigger | Caregivers | Nurses |
|---|---|---|
| critical alert, assigned | at once | at once |
| warning, assigned | at once | after 5 min if still open ("ESCALATED") |
| any alert, unassigned device | — | at once |
| resident or caregiver cancels | "cancelled" | "cancelled", if they were emailed or had acknowledged |
| nurse acknowledges | "acknowledged by the care centre nurse" + note | — |

## Privacy

- Personal data touched: alert content as before; the nurse's note (free text that may
  contain health or logistics details); a caregiver's `sub` (stored as `cancelled_by`)
  and full name (`by_name`, only to mask, never stored or logged).
- The note is emailed to the resident's caregivers (team addresses for now, synthetic
  data only), stored on the alert item, deleted with it (30 days after resolve or
  cancel; an acknowledged alert that is never resolved keeps its note, so Track B's
  resolve must exist before real data arrives: user, 2026-11-04), never logged, not written to the S3 archive (the archive keeps the alert as
  stored). It can sit in the alerts queue up to 4 days and the DLQ up to 14 days if
  processing fails.
- Nurses now get critical alerts at once: they are staff and may see every resident.
- Must not be logged: `note`, `by`, `by_name`, `details`, email content.

## Cost

About US$0: one more email per critical alert (SNS email, 1,000 free a month), no new
resources.

## Out of scope

- The portal buttons, note box, and API routes (Track B).
- Removing the `family` Cognito group and frontend views (Track B).
- SMS for caregivers (later feature).
- Renaming technical names (topic, variables, group) for "care centre nurse".
- Nurse notes after the acknowledge (only one note, at acknowledge time); undoing a
  cancel.
- Watchdog alerts (A4a), possible fall at night (A4b).

## Open questions

None.
