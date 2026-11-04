---
status: built          # draft | ready | built
---

# Alert actions in the portal: nurse acknowledge note and caregiver cancel

## Summary

The portal side of `docs/features/alert-routing.md` (Track B), in **local state only**:
the care centre nurse can add an optional note when acknowledging an alert, and a
caregiver can cancel an alert of a resident they are linked to. Nothing is sent to an
API or saved: a reload brings back the demo data. `useDashboardData.ts` stays the one
seam where the API calls described under **API contract** will go, so the swap needs no
component changes.

## Users and access

| Group | Can see | Can do |
|---|---|---|
| nurse | every resident's alerts (nurse dashboard, resident page), with acknowledge lines, notes, and cancel lines | acknowledge an `open` alert, with an optional note; resolve (unchanged). No Cancel button |
| doctor | the same alert lines (doctor dashboard, resident page) | nothing new: no acknowledge, no cancel |
| caregiver | the caregiver view ("My residents"): their linked residents' recent alerts, with acknowledge lines, notes, cancel lines, and resolve lines | cancel an `open` or `acknowledged` alert of a linked resident, after confirming |
| `family` (removed group) | the no-access screen (unchanged) | nothing |

- Access to a specific resident: staff see every resident; a caregiver sees and cancels
  only residents they are linked to (pattern 13). In this phase the demo links every
  caregiver to res-0002 and res-0005 (`FAMILY_LINKED_RESIDENT_IDS`); the API will check
  the link and refuse with `404` otherwise.
- The UI only hides things; the API (Track B) enforces the groups: acknowledge needs the
  `nurse` group, cancel needs `caregiver` plus the link (docs/data-model.md, Status
  updates). A user in both `nurse` and `caregiver` gets the nurse view, so no Cancel
  button.
- **Who sees who acted** (main's decisions, docs/data-model.md Actor names):
  - an acknowledge is shown to everyone who can see the alert as "the care centre
    nurse", never a name, with the note;
  - a caregiver cancel is shown to everyone who can see the alert (staff and the
    resident's caregivers) as "Cancelled by <caregiver's name> (caregiver)". Emails mask
    the name (`Ta* Me* Li**`); the portal shows it in full (main's text). Before the API
    exists, the name shown right after a cancel is the signed-in caregiver's own display
    name, display only.

## Acceptance criteria

### Nurse acknowledge note (nurse dashboard inbox, resident page alert history)

1. Given an `open` alert and a nurse, when they press **Acknowledge**, then the alert is
   not acknowledged yet: a note form opens inside that alert's row, with a labelled
   multi-line field "Note for the resident's caregivers (optional)", a hint "Emailed to
   this resident's caregivers and shown to them in the portal.", and two buttons:
   **Acknowledge** (submit) and **Cancel**. Focus moves to the field. The row's other
   buttons (Resolve) stay.
2. **Cancel** closes the form, leaves the alert exactly as it was (status, fields, no
   announcement), and returns focus to the row's Acknowledge button.
3. Submitting with an empty field, or only whitespace, acknowledges the alert with no
   note. Submitting with text acknowledges it with the note.
4. The note is normalised before use: `\r\n` → `\n`, then trimmed at both ends. The
   field accepts at most 500 characters (`maxLength=500`, so typing or pasting stops at
   500).
5. From 400 characters, a count "N of 500 characters" shows under the field (text, not
   only colour) and is linked to the field with `aria-describedby`. Below 400 there is
   no count.
6. A note containing a control or other non-printing character other than a newline
   (Unicode category C, e.g. a tab, a zero-width space, a bidi override) or a line or
   paragraph separator (U+2028/U+2029, categories Zl/Zp; `_valid_note` in
   `lambdas/alert-processor/src/message.py` refuses both) is refused: the form stays open, an inline error "Remove hidden or control
   characters from the note." is shown and linked with `aria-describedby` /
   `aria-invalid`, focus goes to the field, and nothing is acknowledged. These are
   main's `note` rules (docs/data-model.md, Status updates).
7. After a successful submit: the alert's status is `acknowledged`, `acknowledged_at`
   is now, `ack_note` is the normalised note (absent when there was none); the row shows
   "Acknowledged by the care centre nurse, <time>" and, with a note, "Message from the
   care centre:" and the note (plain text, line breaks kept). Focus moves to the row's
   Resolve button, and the page announcer says "Acknowledged: <type>, <resident>." The
   announcement never contains the note.
8. The acknowledge and note lines show wherever acknowledge lines already appear: the
   nurse inbox, the doctor dashboard summary, the resident page, and the caregiver view
   (for linked residents).
9. Only `open` alerts can be acknowledged; an alert already acknowledged keeps its
   first `acknowledged_at` and note.

### Caregiver cancel (caregiver view)

10. In the caregiver view, each `open` or `acknowledged` alert in a resident's "Recent
    alerts" has a **Cancel alert** button (accessible name includes the alert type and
    resident, e.g. "Cancel alert: Fall detected, Test Resident B"). `cancelled` and
    `resolved` alerts have none. Nurses and doctors never see a Cancel alert button
    anywhere.
11. Pressing **Cancel alert** doesn't cancel yet: an inline confirmation in that alert's
    item says "Cancel this alert? It will no longer be escalated to the care centre
    nurse. This resident's caregivers, and the care centre nurse if they were alerted,
    will be emailed that you cancelled it." with **Yes, cancel alert** and **Keep
    alert**. Focus moves to **Keep alert** (the safe choice).
12. **Keep alert** closes the confirmation, changes nothing, and returns focus to the
    Cancel alert button.
13. **Yes, cancel alert** sets the alert `cancelled`: `cancelled_at` now,
    `cancelled_by_role` `caregiver`, `cancelled_by` the signed-in caregiver's display
    name (display only); `acknowledged_at` and `ack_note` are kept. The item then shows
    the "Cancelled" label and "Cancelled by <name> (caregiver), <time>", and no Cancel
    alert button. Focus moves to that resident's "Recent alerts" heading, and the page
    announcer says "Alert cancelled: <type>, <resident>."
14. Each caregiver-view alert item shows its lifecycle as text (Open / Acknowledged /
    Cancelled / Resolved label, with an icon, never colour alone), and the lines for
    its state: the at-once line (unchanged), the acknowledge line and note, the cancel
    line (resident: `Cancelled by the resident ("I'm OK"), <time>`; caregiver:
    `Cancelled by <name> (caregiver), <time>`), and `Resolved by <name>, <time>`.
15. Cancelling an alert in the caregiver view doesn't change the staff dashboards'
    local state (separate pages, local state only); with the API, a cancelled alert
    leaves the staff open list (`isOnOpenList`), as already built.

### Page announcer

16. The caregiver view has **one** screen-reader announcer for the page (the existing
    `AlertAnnouncer` + `useAlertAnnouncer`, as on the staff pages), fed from the data
    (new alerts after the first load) and from actions (criterion 13). The per-card
    `aria-live` regions around the "Recent alerts" timelines are removed, so lists with
    ticking times aren't live regions. This replaces `sign-in.md` criterion 22's
    "alerts timeline is inside an `aria-live` region" for the caregiver view.

### Seam and checks

17. `useDashboardData.ts` exposes, for both views:
    - `acknowledgeAlert(alertId: string, ackNote?: string): void`: no actor name.
    - `cancelAlert(alertId: string, by: string): void`: `by` is the display name for
      the local line only and is never sent.
    The hook comments say which API call replaces each body (API contract below).
    Components call only these.
18. Strict TypeScript, no `any`; Tailwind v4 utilities; every state shown with text;
    text at least 16px, tap targets at least 44px; no note, name, or alert data in the
    URL, page title, browser storage, or console. `npm run lint`, `npm run test`, and
    `npm run build` pass; axe finds no violations with the note form open, the
    confirmation open, and after each action.

## Data

- Reads: unchanged (patterns 9, 12 for staff; 14, 10–12 for caregivers, after 13).
- Writes: none in this feature (local state only). With the API: acknowledge and cancel
  are pattern 16, sent as a `status_update` to the alerts queue and applied by
  `alert-processor` (pattern 25; docs/data-model.md, Status updates).
- New access patterns: none.

## IoT

None.

## API contract

*(Proposed, for Track B's API. Not built in this feature: the frontend only keeps
local state. These are what the API must accept so `useDashboardData.ts` can call them
without changing components.)* Both routes: API Gateway HTTP API, Cognito JWT
authorizer; they don't change the alert item themselves but send one `status_update`
to `silvercare-alerts` after the checks in docs/data-model.md "Status updates".

### `POST /residents/{resident_id}/alerts/{alert_id}/acknowledge`

- Groups: `nurse` only (else `403`), any resident (no link check).
- Path: `resident_id` (`res-NNNN` or `unassigned`), `alert_id` (UUID). The hook takes
  both, and `event_ts`, from the alert it already holds.
- Request body (exactly these keys; any other key → `400`):

```json
{ "event_ts": "2026-11-03T02:00:00.000Z", "note": "Synthetic note: ambulance on the way, 8 minutes." }
```

  `note` may be absent or `null` (no note). The API normalises it (`\r\n` → `\n`, trim;
  empty → no note) and checks main's rules: 1–500 characters (code points), no Unicode
  category C except `\n`, and no U+2028/U+2029 (Zl/Zp), as `_valid_note` in
  `alert-processor` does; on failure `400` without echoing the note.
- The API reads the alert (GetItem `pk = RESIDENT#<resident_id>`,
  `sk = ALERT#<event_ts>#<alert_id>`) and takes `resident_id`, `alert_id`, `event_ts`
  from the stored item; `by` is the token's `sub`, `by_name` `null`, `by_role` `nurse`.
- Response `202` (the change is applied asynchronously; the next poll shows it):

```json
{ "alert_id": "6f1c2e0a-5b3d-5e8f-9a47-000000000001", "requested_status": "acknowledged" }
```

- Errors: `400` (bad body, unknown key, bad note, bad `event_ts`), `403` (not a nurse,
  including doctors and `family`), `404` (no such alert), `409` (alert not `open`).

### `POST /residents/{resident_id}/alerts/{alert_id}/cancel`

- Groups: `caregiver` only (else `403`, including nurses, doctors, and `family`), and
  only for a resident the caller is linked to (pattern 13, else `404`; never for
  `unassigned`).
- Request body (exactly this key):

```json
{ "event_ts": "2026-11-03T02:00:00.000Z" }
```

  No note, and no name: `by` is the token's `sub`, `by_name` its `name` claim,
  `by_role` `caregiver`.
- Response `202`:

```json
{ "alert_id": "6f1c2e0a-5b3d-5e8f-9a47-000000000001", "requested_status": "cancelled" }
```

- Errors: `400` (bad body, unknown key, bad `event_ts`), `403` (not a caregiver), `404`
  (not linked, `unassigned`, or no such alert), `409` (alert not `open` or
  `acknowledged`).

Both routes never log request bodies, the note, or the `name` claim.

When the API exists, the hook adds a pending state (button busy, no double submit) and
an error state ("We couldn't acknowledge this alert. Try again." / "We couldn't cancel
this alert. Try again.") with the local change rolled back; that is out of scope here.

## Screens

### Nurse dashboard (`/`, nurse) and resident page (`/residents/:id`, nurse)

- Shown to: nurses (doctors see the same rows without buttons).
- Shows: alert rows as today; an open alert's Acknowledge opens the note form
  (criteria 1–7).
- Actions: Acknowledge (with optional note), Resolve.
- Loading / empty / error: unchanged page states. The form itself has the inline error
  of criterion 6.

### Caregiver view (`/`, caregiver)

- Shown to: caregivers.
- Shows: per linked resident, "Recent alerts" items with lifecycle label and lines
  (criterion 14), and Cancel alert on open or acknowledged alerts.
- Actions: Cancel alert, with confirmation (criteria 10–13).
- Loading / empty / error: unchanged ("Loading your residents' information", the
  no-linked-residents empty state, "We couldn't load the dashboard." with Try again).

## Alerts and notifications

None from the portal in this feature (local state only). With the API, a nurse
acknowledge emails the caregivers (with the note), and a caregiver cancel emails every
caregiver of the resident and the nurses if they were alerted (docs/features/
alert-routing.md).

## Privacy

- Personal data touched: the nurse's note (free text that may hold health or logistics
  details), typed in the browser and kept only in page memory; the signed-in
  caregiver's display name, shown on their own cancel line (display only, never sent);
  alert content as before (synthetic demo data).
- Retention: nothing is stored by this feature; a reload discards every local change.
  With the API, `ack_note` follows main's retention (deleted with the alert, 30 days
  after resolve or cancel; an acknowledged alert never resolved keeps it).
- Must not be logged or shown: the note and names never go to the console, URL, page
  title, browser storage, or announcements. The note is rendered as plain text (React
  escapes it), never as HTML or links. The hint tells the nurse the note is emailed to
  caregivers.

## Out of scope

- Real API calls, the two routes above, pending and error states for them, and
  rollback (Track B API feature).
- Undoing a cancel or editing a note after acknowledge (main: one note, at acknowledge).
- Cancel on the resident page (staff only) or for `unassigned` alerts.
- Removing the `family` Cognito group (Terraform, separate PR).

## Open questions

None.
