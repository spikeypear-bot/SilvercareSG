---
status: ready          # draft | ready | built
---

# Alerts map

## Summary

A map on the nurse dashboard showing the homes of residents with open alerts. The care
centre nurse doesn't travel to homes: they phone the caregiver or dispatch emergency
services, so the map gives an overview and the address to read out. The system holds no
home location today (`home_id` is an opaque label, `docs/data-model.md`), so this
feature adds an address and a map position per home, entered by developers, and
returns them to nurses through the existing `GET /dashboard`.

## Decisions

Made by the user (2026-11-08; retention for shared homes, the zoom cap, and logging
`home_id`s decided after the privacy review):

- **Purpose:** overview and dispatch, not travel. No routing or directions.
- **Location:** a pin (latitude / longitude) **and** the address text per home.
- **Map provider:** OneMap (Singapore Land Authority) basemap tiles, drawn in the
  browser with Leaflet.
- **Who sees it:** nurses only in this feature. Ops (with pins and addresses) is a
  follow-up spec, because it reverses the team decision of 2026-11-08 that ops sees
  devices without resident names or homes; the team and the Track B owner must agree
  first. Doctors and caregivers don't get the map.
- **Data entry:** developers add home items by hand (or `scripts/seed.py` for
  synthetic data). No edit screen.
- **Which homes:** only homes of residents with an alert on the nurse's open list
  (status `open` or `acknowledged`).
- **Pin:** a simple marker showing severity with an icon and text; tapping it shows the
  details: resident name, address, the alerts, and a link to the resident page.
- **No location on file:** such homes are listed under the map, so no alert silently
  disappears from it.
- **Retention:** a home's address and coordinates are kept while the resident is
  active and deleted 90 days after the **last** resident of that home is offboarded
  (as Cognito accounts; a shared home keeps its address while anyone still lives there).
- **No AppSync:** the existing HTTP API and the dashboard's 30-second polling are used.

Defaults chosen by Claude where the user didn't decide (change them here if wanted):
the map is a "List / Map" toggle in the nurse's open-alerts panel, List by default;
each pin shows the worst severity of its home's alerts.

## Users and access

| Group | Can see | Can do |
|---|---|---|
| nurse | the map: homes with open-list alerts, each home's address and position, resident name, alerts, link to `/residents/<id>` | switch List / Map; open a pin's details; follow the link (acknowledge and resolve stay in the list) |
| doctor | nothing new (`homes` is not in the doctor's response; no map) | nothing |
| caregiver, ops, family, no group | nothing new (`GET /dashboard` already returns `403`) | nothing |

How access to a specific resident is decided: nurses see every resident (staff rule,
`docs/data-model.md`); no link check. A user in both `nurse` and `doctor` gets the
nurse view (CLAUDE.md), so gets `homes`.

## Acceptance criteria

### Home items (registry)

1. New registry entity, one item per home: `pk = HOME#<home_id>`, `sk = PROFILE`,
   `home_id` (`^home-[0-9]{4}$`), `address` (string, 1–200 characters, street, block,
   and unit, no name), `lat` (number), `lng` (number), `created_at`, `updated_at`
   (ISO 8601 UTC). No `entity_type`, so it is in no index.
2. A home item is **valid** only if `address` is a non-empty string of at most 200
   characters, `lat` is in [1.16, 1.48], and `lng` is in [103.6, 104.1] (Singapore).
   Anything else counts as "no location on file"; the Lambda logs the `home_id` and
   the field names, never the values.
3. `docs/data-model.md` gains a "Home location" section under `silvercare-registry`
   (keys, fields, writer: developers by hand; reader: `api-dashboard`; retention:
   deleted 90 days after the last resident of that home is offboarded, a manual step
   listed in the Retention table and the Offboarding steps; erasure: delete
   the item, then it stays recoverable 35 days through point-in-time recovery), a new
   access pattern (next free number): `api-dashboard`, nurse callers: BatchGetItem
   `HOME#<home_id>` / `PROFILE` for homes with open-list alerts, projecting `pk`,
   `address`, `lat`, `lng`, eventually consistent, cached 5 minutes. It also adds
   `address`, `lat`, `lng` to "Before any real resident data arrives".

### `GET /dashboard` (`lambdas/api-dashboard/`)

4. For callers in `nurse` only, the response gains `homes`: one entry per distinct
   `home_id` of a resident who has at least one alert with status `open` or
   `acknowledged`, and whose home item is valid (criterion 2):
   `{"home_id", "address", "lat", "lng"}`. Order: by `home_id`.
5. For callers not in `nurse` (doctors), the response has no `homes` key, and the
   Lambda makes no home read.
6. Home items are read with BatchGetItem (keys `HOME#<home_id>` / `PROFILE`,
   projection `pk, address, lat, lng`, eventually consistent, chunks of 100,
   `UnprocessedKeys` retried with backoff as `batch_get_thresholds`) and cached per
   container for 5 minutes per `home_id`, including "no item" results, as the
   thresholds cache. A home with no item, or an invalid item, is left out of `homes`.
7. A failed home read (an error, or keys still unprocessed after retries) doesn't fail
   the dashboard: the response has `"homes": []` and `"homes_error": true`, and the
   Lambda logs `outcome` with `homes_error`. `homes_error` is absent otherwise.
8. Residents with no `home_id`, and alerts from unassigned devices
   (`resident_id = "unassigned"`), add nothing to `homes`.
9. Logs: the count of homes returned and missing, and the list of returned `home_id`s
   (opaque IDs, for auditing which addresses a session received); never addresses or
   coordinates.
10. IAM for `api-dashboard` (`infra/api.tf`): a new statement allowing
    `dynamodb:BatchGetItem` on the registry table with `dynamodb:LeadingKeys` limited
    to `HOME#*`, `dynamodb:Attributes` limited to `pk`, `sk`, `address`, `lat`, `lng`,
    `Null` `dynamodb:Attributes` = `false`, and `dynamodb:Select` IfExists
    `SPECIFIC_ATTRIBUTES` (as the `RegistryThresholds` statement). Nothing else
    changes.

### CloudFront

11. The content security policy in `infra/frontend_hosting.tf` adds
    `https://www.onemap.gov.sg` to `img-src` only. Nothing else in the policy changes.

### Frontend

12. `frontend/package.json` adds `leaflet` (1.9.x, the latest 1.9 release at least two
    weeks old) and its types; no wrapper library. Leaflet's CSS is imported from the
    package (bundled, so `style-src 'self'` still works). Markers use `L.divIcon`
    (no default marker image files).
13. The nurse's "Open alerts" panel (`AlertInbox` in
    `frontend/src/features/dashboard/NurseDashboard.tsx`) gets a "List / Map" toggle (two buttons, the active
    one with `aria-pressed="true"`); List is the default. The doctor dashboard shows no
    toggle.
14. The map uses the tile URL `https://www.onemap.gov.sg/maps/tiles/Default/{z}/{x}/{y}.png`
    and shows the OneMap logo and the attribution "OneMap © contributors | Singapore
    Land Authority" with a link to OneMap; the attribution can't be hidden. (OneMap's
    docs require the logo and attribution but give no wording; this wording is from
    OneMap-style Leaflet examples.) It fits all pins on load (at most zoom 14, so the
    first view covers a neighbourhood, not a street; a single pin is shown at zoom 14),
    else shows all of Singapore. Zoom is limited to 11–19 and panning to OneMap's
    bounds `[103.502, 1.16]`–`[104.11475, 1.56073]` (both from OneMap's TileJSON,
    checked 2026-11-08).
15. One pin per home in `homes`. Each pin shows the worst severity of that home's
    open-list alerts (critical, else warning) by an icon and a text label, never colour
    alone, and is keyboard-focusable; Enter or a tap opens its details.
16. Pin details: for each resident of that home with open-list alerts, the resident's
    name, the address, each alert (as the list's `describeAlert` text, its status, and
    when it happened), and a "View resident" link to `/residents/<resident_id>`.
17. Under the map, when any resident with an open-list alert has no entry in `homes`,
    a line per such resident: "<name> (<home_id>): no location on file". Alerts from
    unassigned devices are listed as "Unassigned device <device_id>: no home".
18. States: while the dashboard loads, the existing loading state; with no open-list
    alerts, the existing empty state (no map drawn); with `homes_error`, a notice
    "Couldn't load home locations. The alert list still works." above the map and every
    alert listed under it as in criterion 17; if the tiles fail to load, the pins and
    the list under the map still show.
19. Pins update with the dashboard's existing 30-second polling; the map keeps the
    user's pan and zoom across refreshes (it re-fits only on first load).
20. Works at phone width (map height at least 300 px, no horizontal scroll); the toggle
    and the pin details work with the keyboard and a screen reader (the details are
    real text, not inside the tile image).

### Seed data

21. `scripts/seed.py` writes a home item for each seeded `home-0001` … `home-0020`,
    with an obviously synthetic address (`"Test address <n> (synthetic)"`) and
    coordinates spread across HDB towns, each on the town's MRT station (public
    transport, never a residential block), so no pin lands on a real home (user's
    request, 2026-11-08). The 20 points are a fixed table in `seed.py`, one per home
    number, each with its station name in a comment: 0001 Ang Mo Kio, 0002 Bishan,
    0003 Toa Payoh, 0004 Queenstown, 0005 Redhill, 0006 Clementi, 0007 Jurong East,
    0008 Bukit Batok, 0009 Choa Chu Kang, 0010 Bukit Panjang, 0011 Woodlands, 0012
    Yishun, 0013 Sembawang, 0014 Sengkang, 0015 Punggol, 0016 Hougang, 0017 Serangoon,
    0018 Tampines, 0019 Pasir Ris, 0020 Bedok. Validation checks each point is in the table and
    inside criterion 2's Singapore bounds. Its existing validation and tests cover the
    new items.

### Tests

22. `api-dashboard` (pytest + moto): `homes` for nurse, absent for doctor (and no home
    read); a nurse who is also a doctor gets `homes`; only homes with `open` or
    `acknowledged` alerts; invalid items (address too long or empty, lat/lng outside
    Singapore, missing fields) left out; unassigned alerts add nothing; the 5-minute
    cache (a second call inside 5 minutes doesn't read; after 5 minutes it does);
    `homes_error` on a read failure with the rest of the response intact; logs contain
    no address or coordinate; IAM text check for the new statement.
23. Frontend (Vitest): the toggle (default List, `aria-pressed`); no toggle for
    doctors; one pin per home with the worst severity as text; pin details content and
    link; the "no location on file" and "Unassigned device" lines; the `homes_error`
    notice; empty state.

## Data

- Reads: alerts `open` index (pattern 9, unchanged); registry home items (new pattern,
  criterion 3), at most one BatchGetItem per container per 5 minutes per home, about
  0.5 RCU per home. With ~20 homes that's at most ~10 RCU every 5 minutes, within the
  registry's burst capacity.
- Writes: none by the app. Developers write home items by hand; `seed.py` writes
  synthetic ones.
- New entity: home item in `silvercare-registry` (no new table or index, no capacity
  change).

## IoT

None.

## API contract

### `GET /dashboard` (changed)

- Groups: `nurse`, `doctor` (unchanged)
- Path / query parameters: none (unchanged)
- Request body: none
- Response `200`, nurse (new `homes`; everything else unchanged):

```json
{
  "residents": ["…unchanged…"],
  "alerts": ["…unchanged…"],
  "homes": [
    {"home_id": "home-0003", "address": "Test address 3 (synthetic)", "lat": 1.3612, "lng": 103.8031}
  ]
}
```

- Response `200`, nurse, home read failed:

```json
{"residents": ["…"], "alerts": ["…"], "homes": [], "homes_error": true}
```

- Response `200`, doctor: unchanged (no `homes`, no `homes_error`).
- Errors: unchanged (`400` query string or body, `401`, `403` not staff, `500`).

## Screens

### Nurse dashboard: open-alerts panel, Map view (`/`)

- Shown to: nurses (the nurse view).
- Shows: a "List / Map" toggle; in Map view, the OneMap map with one pin per home
  (criteria 14–16), and under it the homes with no location (criterion 17).
- Actions: switch view; open a pin's details; "View resident".
- Loading / empty / error states: criterion 18.

## Alerts and notifications

None (the map shows existing alerts; it raises none).

## Privacy

- Personal data touched: each resident's home address and its coordinates (new, and
  sensitive: where a vulnerable elderly person lives), keyed by the opaque `home_id`.
  Shown to nurses only, and only for homes with open alerts.
- The browser fetches OneMap tiles for the area being viewed, which tells OneMap
  roughly where alerting homes are. Addresses, names, and pins are never sent to
  OneMap.
- Retention: deleted 90 days after the last resident of that home is offboarded (manual, by a
  developer); recoverable 35 days more through point-in-time recovery. Cached in the
  Lambda's memory for at most 5 minutes.
- Erasure of one home: delete the `HOME#<home_id>` item.
- Must not be logged: addresses, coordinates. Test data: synthetic addresses and
  coordinates on HDB towns' MRT stations only (criterion 21); the repo is public.
- Stays in `ap-southeast-1` (DynamoDB); in transit over HTTPS.

## Out of scope

- Ops map and ops access to addresses (a follow-up spec, after team agreement).
- Doctor and caregiver maps.
- Routing, directions, travel times.
- A screen or API to edit addresses; geocoding postal codes in the app.
- Homes without an open-list alert on the map.
- Live push (AppSync or WebSockets).
- Automating the 90-day deletion.

## Before building (owner)

- Register on OneMap and accept its Developer Agreement: OneMap's Terms of Use require
  this for web apps (checked 2026-11-08, onemap.gov.sg/legal/termsofuse.html). Tiles
  need no token (onemap.gov.sg/docs/maps/).

## Open questions

None.
