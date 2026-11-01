---
status: built          # draft | ready | built
---

# Device health

## Summary

A read-only "Devices" tab in the staff area (`/devices`) listing every wearable and
bedroom sensor, with problems first, so staff can see which devices need a visit. There
is no API yet: the data is synthetic demo data.

## Users and access

| Group | Can see | Can do |
|---|---|---|
| nurse | every device, with its assigned resident's name and home ID | filter the list; open a resident's page |
| doctor | same as nurse | same as nurse |
| caregiver | a "This page is for care staff." screen with a link back to their dashboard | nothing on this page |
| family | same as caregiver | nothing on this page |

- Users with no group see the existing no-access screen. A user in both nurse and doctor
  sees the same page.
- No new Cognito group. Staff see every device, as they see every resident. The UI only
  hides the page; when the API exists, the route checks the caller's group server-side.
- Read-only: there are no device actions.

## Acceptance criteria

1. The staff area has two tabs, "Dashboard" and "Devices", on the nurse and doctor
   dashboards and on the Devices page. The current tab has `aria-current="page"` and a
   bar under it.
2. `/devices` is behind sign-in. Nurse and doctor see the page; caregiver/family see the
   staff-only screen; no group sees the no-access screen. Nothing is loaded for
   non-staff. The page title is "Devices".
3. Four tiles: All devices, Offline, Low battery (below 20 %, the existing display
   filter, not an alert threshold), and Reporting normally (online, battery not low).
   They are toggle buttons (`aria-pressed`), like the nurse dashboard's tiles. Choosing
   one filters the list and announces "Showing N of M devices: <filter>." through the
   page's one screen-reader announcer. The default is All.
4. One row per device, with these columns:
   - device ID
   - type ("Wearable" / "Bedroom sensor", with an icon)
   - assigned resident (a link named with the resident's name, to
     `/residents/<resident_id>`) and home ID, or "Not assigned"
   - Online/Offline (icon and text)
   - battery: "64%", "12% · Low", "58% · Charging", "Mains powered" (a bedroom sensor
     that reported no battery), or "Unknown" (no level, e.g. never reported)
   - last seen as a relative time, or "Never"
   - firmware, or "Unknown"
5. Problems first:
   - offline, longest silent first, with never-reported devices first of all
   - then low battery, lowest first
   - then the rest
   Within each group, ties go by device ID.
6. Offline uses the rule in `docs/metrics.md`: no `status` for 15 minutes (still
   *proposed*). The backend sends `online`; the UI never computes it.
7. States:
   - loading: skeleton and a "Loading devices" status
   - error: "We couldn't load the devices." with "Try again"
   - no devices: "No devices yet"
   - a filter with no matches: its own text (e.g. "All devices are online.") and "Show
     all devices"
8. The announcer also says when devices go offline or come back online between data
   updates. It gives the device type and ID only, never a resident name. The table is
   not a live region.
9. `npm run lint`, `npm run test`, and `npm run build` pass in `frontend/`.

## Data

- **Reads:**
  - Demo data only: `createDeviceFleet` in `frontend/src/features/dashboard/demoData.ts`,
    through `loadDeviceFleet` / `useDeviceFleet` in `useDashboardData.ts`.
  - When the API exists: patterns 4 (every device), 5 (newest `status`), and 8 (names)
    *(proposed staff use)*. The route returns exactly the fields listed under "Devices
    view" in `docs/data-model.md`.
- **Writes:** none.
- **Demo data:** 40 devices (one wearable and one bedroom sensor per resident), built
  from the dashboard data so they agree with it:
  - **offline for varying times:** 18 min, 52 min, 70 min, 185 min, 26 h, 2 days
  - **one bedroom sensor offline** while its wearable is fine (Test Resident I)
  - **both devices offline** (Test Resident O)
  - **three low batteries:** two wearables and one bedroom sensor

## IoT

None directly. The view shows each device's newest `status` (`docs/metrics.md`).

## API contract

None yet. The devices route comes with the API feature, staff only, with the field list
in `docs/data-model.md`.

## Screens

### Devices (`/devices`)

- Shown to: nurse and doctor.
- Shows: staff tabs, demo data banner, tiles, and the device table (criteria 1–8).
- Actions: filter; open a resident's page.
- Loading / empty / error states: criterion 7.

## Alerts and notifications

None. Device-offline and low-battery alerts come from the alerting rules (Open
decision #2). The demo's extra offline devices have no matching alerts.

## Privacy

- **Personal data touched:** each assigned resident's display name and home ID, and
  which devices are theirs. No vitals, profile fields, photo, or `worn` state.
- **Retention:** nothing new is stored; the view reads live state only.
- **Must not be logged or shown:**
  - no personal data in the URL, page title, browser storage, or console
  - the announcer names devices, never residents
  - when the API exists, the route's audit log line records the caller's `sub`, the
    route, and the number of devices returned, never the payload
    (`docs/data-model.md`, Notes for later specs)

## Out of scope

- Device actions (reassigning, restarting, firmware updates).
- Polling: the announcer's offline/online messages only matter once data refreshes.
- Spare devices in the demo: the table handles them ("Not assigned"), but the demo
  fleet has none.

## Open questions

Must be empty before `status: ready`.

-
