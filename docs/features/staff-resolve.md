---
status: ready          # draft | ready | built
---

# Staff resolve with a closing note (Track A5)

## Summary

Track B's portal has a **Resolve** button for nurses and doctors. Their API sends a
`status_update` with `new_status = resolved`, which `alert-processor` rejects today
(only the watchdog's `system` resolve is accepted), so staff resolves end in
`silvercare-alerts-dlq` and trip its alarm. This feature accepts a staff resolve with
a required closing note, emails the resident's caregivers, and writes the alert's
final record (with the acknowledge and resolve notes) to S3, so the notes outlive the
30-day DynamoDB expiry as the incident's paper trail.

## Users and access

No screen and no API in this feature (Track B owns the button and route).

| Group | Can see | Can do (Track B's API, `status_update`) |
|---|---|---|
| nurse | the resolve in the portal; no email (they did it) | resolve any health alert or possible fall, with a note |
| doctor | as nurse | as nurse |
| caregiver | "resolved by the care centre" email with the note, for linked residents | nothing new |
| ops | nothing new (device problems are resolved only by the watchdog) | nothing new |

## Product decisions

| Decision | Value |
|---|---|
| Who resolves (Track B, 2026-11-07) | nurses and doctors (staff, data-model pattern 16), any resident |
| Which alerts | health alerts (falls, vitals, possible falls). Device problems: only the watchdog resolves them |
| Closing note (Track B, 2026-11-07) | **required** on a staff resolve, same rules as the acknowledge note; the paper trail for how each incident was closed |
| Resolve email (user, 2026-11-07) | the resident's caregivers, with the note quoted; nurses are not emailed |
| Permanent record (user, 2026-11-07) | on **every** resolve (staff and `system`), the final alert record, including `ack_note` and `resolve_note`, is written to S3 `alerts/` next to the original copy, under the same retention (resident's lifetime + 6 years) |
| Resolver in emails | nurse: "the care centre nurse"; doctor: "a SilverCare doctor" (no names; `by_name` is null) |

## Acceptance criteria

### `status_update` contract (`message.py`)

1. New rows in the rules table:
   `nurse | resolved | alert target | Cognito sub | null | note required` and
   `doctor | resolved | alert target | Cognito sub | null | note required`.
   - `by`: a lowercase UUID (Cognito `sub`); `by_name` must be `null`; no `reason` key.
   - `note`: required (a missing or `null` note is `invalid:note`), same rules as
     `_valid_note` (1–500 characters, not only whitespace, no category C except `\n`,
     no U+2028/U+2029).
2. Unchanged: the note stays optional on a nurse `acknowledged`, and is rejected on
   every other row, including the `system` resolve. A `doctor` with any other
   `new_status` is invalid.

### Applying a staff resolve (`handler.py`)

3. Alert target only: strongly consistent GetItem (pattern 25), then the role/type
   rule: `nurse`/`doctor` + `resolved` is allowed on health alerts and `possible_fall`;
   on `device_offline`, `battery_low`, `not_worn` the outcome is `not_allowed` (no
   change, no email). Missing or not open/acknowledged: `not_open`.
4. UpdateItem conditional on `#status IN (:open, :acknowledged)`, as the system
   resolve: sets `status = resolved`, `resolved_at` (`changed_at`), `resolved_by_role`
   (`nurse`/`doctor`), `resolved_by` (`by`), `resolve_note` (the note), `expires_at` =
   `changed_at` + 30 days, REMOVEs `open_pk`. Keeps the acknowledge fields and
   `ack_note`.
5. Then the final record (criterion 8), then the follow-up (criterion 6), then
   `status_notified_at` (`attribute_not_exists`). Retry rule as alert-cancel: an item
   already `resolved` by the same `by` and role with no `status_notified_at` gets the
   final record and follow-up again (duplicates accepted, a missing email isn't).
6. Follow-up: to the resident's caregivers only (`silvercare-alerts`, `resident_id`
   attribute; nobody for `unassigned`, `status_notified_at` still set). Subject
   `SilverCare update: <label> - <resident_id> resolved`. First line `The alert below
   was resolved by the care centre nurse at <time> SGT.` (or `by a SilverCare doctor`),
   then the usual lines, then `Closing note:` and the note's lines each quoted with
   `> ` (as the acknowledge note), the portal link and footer.
7. Log line as other status updates, with `by_role` and `has_note`; never `note`, `by`,
   or `by_name`.

### Final record in S3

8. On every resolve, staff or `system` (device problems, back in bed), after the
   UpdateItem succeeds (or on the retry path), `alert-processor` writes the item as it
   now stands (`ReturnValues = ALL_NEW` or a strongly consistent read) to the history
   bucket at `alerts/date=<same date as the original>/<alert_id>.final.json`, same
   JSON format and content type as the original copy, overwritten on a retry. The
   original `<alert_id>.json` is unchanged. The record never contains `by_name` (it is
   never stored). IAM: the existing `HistoryAlertObjects` statement already allows
   `s3:PutObject` on `alerts/*`; no Terraform change. A failed write fails the record
   (retried, then DLQ), before any email.

### Docs

9. `docs/data-model.md`: the new rows and Track B's sender checks (caller's current
   group `nurse` or `doctor`, else `403`; read the alert first; a device problem →
   `409`; the note from the request body, `\r\n` → `\n`, trimmed, checked with the same
   rules, else `400` without echoing it; never log it); alert item attributes
   `resolved_by` (now a nurse or doctor `sub`, or absent for `system`),
   `resolved_by_role` values, `resolve_note`; pattern 16 (resolve = nurse or doctor,
   health alerts only); the S3 final record and its retention (health record, the
   resident's lifetime + 6 years, as `alerts/`; the notes may hold health details).

### Tests

10. Unit tests: validation (each new row; note missing, null, blank, 500 vs 501,
    control characters, U+2028; `by_name` or `reason` on a staff resolve rejected; note
    still rejected on `system` resolve and cancels, optional on nurse acknowledge;
    `doctor` acknowledge rejected); apply (open and acknowledged resolve, fields set,
    ack fields kept, device problem → `not_allowed`, not open → `not_open`); final
    record key, content (both notes, no `by_name`), written for staff and `system`
    resolves, before the email, a failed write fails the record without an email;
    follow-up recipients (caregivers only, unassigned nobody), exact body for nurse and
    doctor with the quoted note; retry; logs never hold the note.

## Data

- Writes: `resolved_by`, `resolved_by_role`, `resolve_note` on the alert item; one more
  S3 object per resolved alert.
- No new access pattern or index.

## IoT

None.

## API contract

None (Track B's resolve route sends the `status_update`, criterion 1).

## Screens

None.

## Alerts and notifications

| Trigger | Caregivers | Nurses |
|---|---|---|
| nurse or doctor resolves | "resolved by …" with the closing note | — |

## Privacy

- New data: the closing note (free text, may hold health or logistics details) and the
  resolver's Cognito `sub`.
- The note is emailed to the resident's caregivers, stored on the alert item (deleted
  30 days after resolve), and kept in S3 in the final record as a health record for
  the resident's lifetime + 6 years (user, 2026-11-07), together with `ack_note`.
  Never logged.
- `by_name` is never stored, so no names reach S3.

## Cost

About US$0: one more small S3 PUT per resolved alert, one email per staff resolve.

## Out of scope

- The Resolve button, note box, and API route (Track B).
- A final record on cancel (a cancelled alert's `ack_note` still expires with the item).
- Redriving the staff resolves already in `silvercare-alerts-dlq` (the owner redrives
  them after the deploy; they are applied then).

## Open questions

None.
