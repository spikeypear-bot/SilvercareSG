---
status: built          # draft | ready | built
---

# Resident page

## Summary

A staff-only page per resident at `/residents/:id`, opened from the resident cards on the
nurse and doctor dashboards. It shows the resident's devices, the latest 5 messages of
each type, every stored alert, and their vitals thresholds, from `docs/data-model.md`
patterns 10, 11, 12, 18, and 27 *(proposed)*. There is no API yet: everything comes
from synthetic demo data, and actions change local state only.

## Users and access

| Group | Can see | Can do |
|---|---|---|
| nurse | every (demo) resident's page | acknowledge and resolve alerts in the alert history (local state only) |
| doctor | every (demo) resident's page | edit the vitals thresholds (local state only); no alert actions |
| caregiver | a "This page is for care staff." screen with a link back to their dashboard | nothing on this page |
| family | same as caregiver | nothing on this page |

- A user in both `nurse` and `doctor` gets the nurse behaviour (same rule as
  `sign-in.md`). A signed-in user with none of the four groups sees the existing
  no-access screen ("Your account isn't set up yet").
- Staff see every resident. Caregiver and family don't reach this page in this phase;
  showing them a linked resident's page needs the link check (pattern 13), which is
  out of scope.
- The UI only hides things. When the API exists it enforces the groups:
  - acknowledge/resolve (pattern 16) is open to any staff member, because the
    authorisation boundary is staff vs caregiver/family and "nurses handle alerts" is
    workflow, shown in the UI only (decided 2026-11-01);
  - changing thresholds (pattern 26) is doctor only.

## Acceptance criteria

### Route and states

1. `/residents/:id` is behind sign-in and takes the opaque `resident_id`
   (`res-0001`); no name is ever in the URL. The page title (header and
   `document.title`) is "Resident details" and never the resident's name.
2. Nurse and doctor see the page. Caregiver and family see the staff-only screen (title
   "Staff only", "This page is for care staff.", "Back to my dashboard" link to `/`)
   and no resident data is loaded. Users with no group see the no-access screen.
3. Loading shows a skeleton with a "Loading resident" status; a failed load shows
   "We couldn't load this resident." with "Try again"; an unknown ID shows "Resident
   not found". A "Back to dashboard" link is always shown and takes focus when the page
   opens, so the page starts at the top after a card is clicked.
4. Each resident card on both dashboards links to its page. The link is the resident's
   name (its accessible name), works with the keyboard, and covers the whole card.

### Sections

5. **Header:** name, resident ID, home ID, status badge with its sentence, and the
   wearable's worn/charging state.
6. **Devices** (pattern 10): wearable and bedroom sensor, each with device ID,
   Online/Offline (icon and text), battery (`battery_pct`, or "Mains powered" when the
   bedroom sensor's is `null`), last seen as a relative time, and firmware. A missing
   device shows "No wearable assigned." / "No bedroom sensor assigned."
7. **Latest readings** (pattern 11): up to 5 entries of each message type, newest
   first, using the `docs/metrics.md` fields:
   - `vitals` (`hr_bpm`, `spo2_pct`, `skin_temp_c`, `activity_pct`)
   - `fall` (`confidence`, `peak_g`, `still_s`)
   - wearable `status` (`battery_pct`, `worn`, `charging`)
   - bedroom `motion`, `bed` (`occupied`), and `status`
   Heart rate, SpO2, skin temperature, and activity also get a sparkline (the existing
   `Sparkline`, no charting library) with the values as screen-reader text. Each table
   has its own empty text. Only the latest 5 are shown, matching what DynamoDB keeps.
8. **Alert history** (pattern 12): every stored alert for the resident, newest first,
   including resolved and cancelled ones (each kept 30 days), each with:
   - severity badge, type, and time
   - Open/Acknowledged/Cancelled/Resolved label
   - "Acknowledged by …" and "Resolved by …" lines (names looked up by the API on
     read; "Former staff member" / "Former caregiver" once the account is deleted)
   - for a cancelled alert, "Cancelled by the resident ("I'm OK"), <time>"
     (`cancelled_at`, docs/features/alert-cancel.md)
   - for an open alert, "Escalated to nurses" once `escalated_at` is set, or before
     that the countdown to the API's computed `escalates_at` (60 s,
     `docs/features/escalation.md`)
   Empty: "No alerts for this resident in the last 30 days."
9. Nurses get Acknowledge (open alerts) and Resolve (open and acknowledged alerts)
   buttons in the alert history; cancelled and resolved alerts have none. They record the signed-in user's display name and the time; resolving
   moves focus to the section heading. Doctors see no alert buttons.
10. **Vitals thresholds** (pattern 18): heart rate min/max, SpO2 min, skin temperature
    min/max, and "Last changed by <name>, <time>". Nurses see them read-only with "Only
    doctors can change thresholds."
11. Doctors can choose "Edit thresholds". The form:
    - takes heart rate and SpO2 as whole numbers, and skin temperature with up to 1
      decimal place
    - accepts the `vitals` valid ranges in `docs/metrics.md` (heart rate 20–250, SpO2
      50–100, skin temperature 20.0–45.0), with each lower limit below its upper limit
    - shows inline errors linked with `aria-describedby`, and focuses the first invalid
      field
    Save updates the values and "Last changed by", announces "Thresholds saved.", and
    returns focus to the Edit button; Cancel changes nothing.
12. **Change history** (pattern 27, proposed), under thresholds: earlier changes, newest
    first, each with "Changed by <name>, <relative time>" and one line per changed value
    ("Heart rate, upper limit: from 110 bpm to 120 bpm"). Read-only for every role.
    Empty: "No changes to these thresholds have been recorded."
13. Saving thresholds in the UI does **not** add a change-history entry: the server
    writes it with the pattern 26 route.

### Accessibility and checks

14. Status, severity, lifecycle, and online state always have text, never colour alone.
    Headings run h1 (page) → h2 (resident and sections) → h3 (device subsections). The
    page has one screen-reader announcer (`role="status"`), fed from the data: new
    alerts, escalations, nurse actions, and "Thresholds saved.". Lists are not live
    regions.
15. `npm run lint`, `npm run test`, and `npm run build` pass in `frontend/`.

## Data

- **Reads**, from demo data only: `createResidentDetail` in
  `frontend/src/features/dashboard/demoData.ts`, through `loadResidentDetail` /
  `useResidentDetail` in `useDashboardData.ts` (the one place real API calls will go).
  The future API maps them to:
  - patterns 10, 11, 12, and 18
  - pattern 27 for the change history
- **Writes:** none outside the browser. `acknowledgeAlert`, `resolveAlert`, and
  `updateThresholds` change local state. Names shown for who acted are display only: the
  API will take the actor from the token's `sub`, and the browser never sends it.
- **New access patterns,** both *(proposed)*, in `docs/data-model.md`:
  - 26: doctor changes thresholds; `attribute_exists(pk)`; exact body keys; the
    token's group claim, with up to 60 minutes' delay after removal accepted
  - 27: threshold change history
  Pattern 16 now has `attribute_exists(pk)`.
- **Demo data:**
  - Latest 5 per device and message type, built from the dashboard data so the two
    agree.
  - 1–4 resolved alerts per resident over the last 30 days. Test Resident N has none,
    for the empty state.
  - Thresholds start at the backend's defaults (heart rate 50–120 bpm, SpO2 at least
    92 %, skin temperature 30.0–38.0 °C; `docs/data-model.md`, decided 2026-11-02).
    Each resident's change history is applied on top: 0–2 entries each. Only Test
    Resident B (`res-0002`) has its own limit, SpO2 minimum 92 → 88, matching the
    backend seed script. Residents with no changes have no "Last changed by" (the page
    says "Not changed since setup.").

## IoT

None directly. Readings mirror the `vitals`, `fall`, `status`, `motion`, and `bed`
messages in `docs/metrics.md`.

## API contract

None yet. The routes for patterns 10–12, 16, 18, 26, and 27 come with the API feature
(see the `api-route` skill).

## Screens

### Resident page (`/residents/:id`)

- Shown to: nurse and doctor.
- Shows: back link, demo data banner, then header, Devices, Latest readings, Alert
  history, Vitals thresholds, and Change history (criteria 5–13).
- Actions: nurse acknowledges and resolves alerts; doctor edits thresholds.
- Loading / empty / error states: criterion 3, plus the empty text of each section.

### Staff only (`/residents/:id`, caregiver or family)

- Shows: "This page is for care staff.", a sentence that their dashboard shows their
  linked residents, and "Back to my dashboard".

## Alerts and notifications

None delivered by the portal. Alert actions and the escalation countdown are local and
display only, as on the dashboards. Alert emails and escalation are backend features
(`docs/features/escalation.md`).

## Privacy

- **Personal data touched:** synthetic residents' health readings, alerts, and
  thresholds, plus the signed-in staff member's display name (shown for actions they
  take).
- **Retention:**
  - resolved alerts: 30 days (TTL)
  - latest messages: 5 per type
  - change history: the resident's lifetime + 6 years, as for electronic patient health
    records (MOH Circular 85/2022, under the Healthcare Services Act 2020). Kept after
    offboarding; deleted by TTL at date of death + 6 years once a date of death is
    recorded, and no expiry until then. Recording a date of death has no field or
    access pattern yet *(proposed gap, see `docs/data-model.md`)*.
- **Must not be logged or shown:** no personal data in the URL, page title, browser
  storage, or console. Names shown for who acted are display only and never sent.

## Out of scope

- The API and real data; caregiver/family access to a linked resident (pattern 13).
- Writing change-history entries (server side, with the pattern 26 route).
- History longer than the latest 5 readings (would come from the S3 history, not
  DynamoDB).
- Resident profile fields beyond name and home ID, photos (patterns 19, 20), and
  editing the profile.

## Open questions

Must be empty before `status: ready`.

-
