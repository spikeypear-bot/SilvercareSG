---
status: built          # draft | ready | built
---

# Sign-in and main page

## Summary

A Cognito user pool with groups `doctor`, `nurse`, `caregiver` (and `family`, removed by
alert-routing, see below) and a
React sign-in flow (email + password, optional authenticator-app MFA). After signing in,
users land on a main page whose layout depends on their group: nurses get the **nurse
dashboard** (alert response), doctors get the **doctor dashboard** (clinical review),
caregivers get the **caregiver view** ("My residents"; called the family view before
alert-routing). There is no API yet, so the main page
shows clearly labelled synthetic demo data from one local module.

**Changed by alert-routing (2026-11-04)** (docs/features/alert-routing.md): the
`family` group is removed (family members get caregiver accounts; Track B deletes the
Cognito group), so the frontend now gives a user in only that group the no-access
screen. Only nurses acknowledge, shown as "the care centre nurse" (a shared account);
caregivers cancel. Warnings escalate after 5 minutes; critical alerts and alerts from
unassigned devices go to the care centre nurse at once.

**Changed 2026-11-05 (user's decisions):**

- **Who acknowledges what:** nurses acknowledge **health alerts** only. **Device
  problems** (`device_offline`, `battery_low`, `not_worn`) are acknowledged by tech
  staff and by the resident's caregivers. Caregivers still cancel health alerts (main's
  rule; the Cancel button is Track B's). Tech staff are the `ops` group in
  `docs/data-model.md`. That group doesn't exist in Cognito yet (Track B creates it), so
  the portal has no tech-staff view or button yet.
- **Alert type names** match the backend: `battery_low` (was `low_battery`) and
  `not_worn` (was `inactivity`, "Wearable not worn").
- **Resident status** follows the rule in `docs/data-model.md` (Resident status):
  `urgent` = an open or acknowledged critical alert, `attention` = an open or
  acknowledged warning (including device problems) or a newest reading strictly
  outside the resident's thresholds, `ok` = no issues (all decided). The backend
  computes it; the UI only displays it.
- **Staff resolve:** nurses and doctors resolve **health alerts only**, through a
  `status_update` like acknowledge and cancel, with a **required note**; device problems
  are resolved only by the watchdog. The portal's Resolve sends no name (the actor comes
  from the token). Contract: `docs/data-model.md`, Track B route: resolve.
- **Caregiver names:** a caregiver's acknowledge of a device problem shows their full
  name ("Acknowledged by <name> (caregiver)") to everyone who can see the alert, as a
  caregiver cancel does (decided).
- **API token:** the portal will send the **ID token**, not the access token (see
  API contract).

## Users and access

| Group | View | Can see | Can do |
|---|---|---|---|
| nurse | nurse dashboard | every (demo) resident, all (demo) open and acknowledged alerts | sign in/out, set up or remove MFA, change password; acknowledge and resolve health alerts, not device problems (local state only until the API exists) |
| doctor | doctor dashboard | every (demo) resident with vitals trends, a summary of the most urgent (demo) alerts | sign in/out, set up or remove MFA, change password; no acknowledge or resolve |
| caregiver | caregiver view | only the (demo) residents linked to them | sign in/out, set up or remove MFA, change password; acknowledge their residents' device problems (local state only until the API exists) |
| family | no-access screen (group removed, see above) | nothing | sign out |

- `doctor` and `nurse` together are what the rest of the docs call **staff**: both see
  every resident. The two dashboards are the **staff dashboards**.
- A user in several groups: if any group is `nurse`, show the nurse dashboard;
  otherwise if any is `doctor`, the doctor dashboard; otherwise if any is `caregiver`,
  the caregiver view. The header role badge follows the same order.
- A signed-in user with none of the three groups (`family` alone counts as none) sees a
  "no access" screen (see Screens).
- How access to a specific resident is decided: in this feature all data is demo data,
  so nothing is enforced. When the API exists, staff see every resident, and caregivers
  see a resident only after the link check (data-model pattern 13). The group is
  read from the ID token's `cognito:groups` claim and only selects which view to render.
- Accounts are created by an admin only (no self sign-up). Admins create users in the
  Cognito console (users are data, not Terraform resources) and add them to a group.
  When creating a user, the admin sets **Email** (and ticks "Mark email address as
  verified", otherwise password reset can't work) and **Name**, sends the invitation
  email, then adds the user to exactly one group. To change a user's email later, the
  admin changes it and marks it verified in the same edit. Users can't change their own
  name or email.

## Acceptance criteria

### Cognito (Terraform)

1. One `aws_cognito_user_pool` named `silvercare-users` in `infra/cognito.tf` with:
   `username_attributes = ["email"]`, `auto_verified_attributes = ["email"]`,
   `user_pool_tier = "LITE"`, `deletion_protection = "ACTIVE"`,
   `admin_create_user_config { allow_admin_create_user_only = true }`, temporary
   password validity 7 days, account recovery by verified email only, and the default
   Cognito email sender (no SES).
2. Password policy: minimum length 12, requires lowercase, uppercase, numbers, and
   symbols.
3. MFA: `mfa_configuration = "OPTIONAL"` with `software_token_mfa_configuration {
   enabled = true }`. No SMS MFA.
4. Schema: only the standard `email` and `name` attributes, both **not required** (no
   `schema` blocks). Cognito lets every app client write required attributes, so making
   them optional is what allows criterion 6 to make them read-only for users. Admins set
   both when creating a user, and only admins can change them (decided by the user). No
   custom attributes. `name` is shown in the app header and will be used by the API to
   show who acknowledged an alert (see `docs/data-model.md`); the app falls back to
   "Your account" / no name in the greeting if it is missing.
5. Four `aws_cognito_user_group` resources: `doctor`, `nurse`, `caregiver`, `family`,
   each with a one-line description. No IAM roles on the groups.
6. One `aws_cognito_user_pool_client` named `silvercare-web`: `generate_secret = false`,
   `explicit_auth_flows = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]`,
   `prevent_user_existence_errors = "ENABLED"`, `enable_token_revocation = true`,
   access and ID token validity 60 minutes, refresh token validity 7 days (units set
   explicitly), no OAuth flows / hosted UI, and `read_attributes = ["email", "email_verified", "name"]`, and
   `write_attributes = ["zoneinfo"]` (an attribute the app never reads: an empty list
   would mean "all writable"), so users can't change their own name or email.
7. Outputs in `infra/outputs.tf`: `cognito_user_pool_id`, `cognito_user_pool_client_id`,
   `cognito_user_pool_arn`. `make tf-check` passes.

### Frontend app

8. `frontend/.env.example` lists `VITE_USER_POOL_ID` and `VITE_USER_POOL_CLIENT_ID` with
   placeholder values. `src/lib/auth.ts` configures Amplify v6 (`aws-amplify/auth` only)
   from these and is the only module that imports from `aws-amplify`.
9. Given a signed-out user, when they open any route other than `/login` or
   `/forgot-password`, then they are redirected to `/login`.
10. Given valid credentials, when the user submits the login form, then they are taken
    to `/`, which renders the view for their group (rules under Users and access).
11. Given wrong credentials or an unknown email, then the form shows the same message
    "Incorrect email or password." (never revealing whether the account exists), keeps
    the email, and clears the password.
12. Given an admin-created user signing in with a temporary password, then the login
    page asks for a new password (with confirmation and the password rules shown as a
    checklist that updates as they type), and on success signs them in.
13. Given a user with an authenticator app enabled, then after the password the login
    page asks for the 6-digit code; a wrong code shows "That code didn't work. Try
    again." and keeps the user on the code step.
14. `/forgot-password`: entering an email sends a reset code (always showing "If an
    account exists for that email, we've sent a code." regardless of outcome), then the
    user enters the code and a new password (same rules checklist) and is sent to
    `/login` with a success notice.
15. `/account` (signed in, any group): shows the user's name, email, and role; lets them
    change their password (current + new), and set up an authenticator app (shows a QR
    code generated locally from the `otpauth://` URI and the secret as text, then
    verifies a code and enables TOTP as preferred MFA) or turn it off.
16. Sign out (from the header user menu) calls Amplify `signOut()` and returns to
    `/login`. Fields with invalid input show inline, labelled error messages.
17. Every form field has a visible `<label>`; error messages are linked with
    `aria-describedby`; submit buttons show a busy state and are disabled while a
    request is in flight; the password field has a show/hide toggle with an accessible
    name.

### Main page (demo data)

18. All main-page data comes from `src/features/dashboard/demoData.ts` through an async
    function in `src/features/dashboard/useDashboardData.ts` (returns a promise, ~400 ms
    simulated delay) so loading and error states are real and the module can later be
    swapped for `src/lib/api.ts` calls. Demo data follows the shapes and units of
    `docs/metrics.md` (`hr_bpm`, `spo2_pct`, `skin_temp_c`, `activity_pct`,
    `battery_pct`, `worn`, `charging`) and uses only synthetic names ("Test Resident A"…)
    and IDs (`res-0001`…, `wearable-0001`…, `bedroom-0001`…).
19. The UI never computes "normal/abnormal" from vitals. Each demo resident and alert
    carries its own `status` (`ok` | `attention` | `urgent`) and alert `severity`
    (`warning` | `critical`, as `docs/data-model.md` defines them since the alerting
    features), as the API will later provide. The backend computes the resident status
    with the rule in `docs/data-model.md` (Resident status, decided 2026-11-05):
    `ok` = no issues; `attention` = a non-critical warning or an abnormal reading, not
    life-threatening; `urgent` = critical, life-threatening. The demo data follows the
    same rule, and a demo-data test checks every demo resident against it.
20. A "Demo data" banner is visible on the main page in both views, with text (not
    only colour): "You are viewing synthetic demo data. No real resident information is
    shown."
21. The nurse and doctor dashboards show 20 demo residents and their open demo alerts;
    the caregiver view shows the 2 demo residents linked to the signed-in demo account (the
    same 2 for every caregiver user).
22. Statuses and alert severities are always shown with an icon **and** a text label,
    never colour alone. Every view, the caregiver view included (changed by
    `docs/features/alert-actions-ui.md`, criterion 16), has one screen-reader
    announcer per page, fed from the alert data (new alerts, escalations, nurse
    actions); their alert lists are not live regions, so countdowns aren't read out.
23. `npm run lint`, `npm run test`, and `npm run build` pass in `frontend/`.

### Dependencies

Allowed runtime npm packages (already installed by the app scaffold): `react`,
`react-dom`, `react-router-dom`, `aws-amplify`, `lucide-react`,
`@fontsource-variable/inter`, `qrcode.react`. No others.

## Data

- Reads: none from AWS. Main page reads only `demoData.ts`. Auth reads the user's own
  Cognito attributes (`name`, `email`) and groups from the ID token.
- Writes: Cognito only, the user's own password and MFA settings. The nurse dashboard's
  acknowledge and resolve, and the caregiver view's device-problem acknowledge, change
  only local state.
- New access patterns or tables: none. When the API exists, both staff dashboards map
  to patterns 5, 8, 9, the nurse dashboard's acknowledge and resolve to pattern 16, and
  the caregiver view to patterns 14, 10–12 in `docs/data-model.md`.

## IoT

None directly. Demo data mirrors the `vitals` and `status` messages in `docs/metrics.md`.

## API contract

None. This feature calls only Cognito (via Amplify). `src/lib/api.ts` is not created yet.

**Which token the API gets (decided 2026-11-05):** the portal sends the Cognito **ID
token** as `Authorization: Bearer <token>`, not the access token. `src/lib/auth.ts`
provides it through `getApiToken()`, the only function that hands a token out (for
that header only; never stored, logged, or put in a URL).

Why: the API needs the caller's `name`, for `by_name` on a caregiver's cancel or
device-problem acknowledge, and `updated_by_name` / `changed_by_name` on a threshold
change (`docs/data-model.md`). Cognito puts `name` (and `email`) only in the ID token.
The access token carries `sub` and `cognito:groups` but no profile attributes, and
adding claims to it needs a pre-token-generation trigger on a paid Cognito tier (the
pool is Lite). The ID token also carries `sub` and `cognito:groups`, and its `aud`
is the app client ID, which the API Gateway JWT authorizer checks (`audience`). The
portal already reads the groups from the ID token. Both tokens last 60 minutes.

## Screens

Design direction for all screens: a calm, modern health-monitoring look. Primary colour
teal (Tailwind `teal-600`/`teal-700` for actions, meeting AA contrast on white), slate
neutrals, a soft off-white page background (`slate-50`), white cards with `rounded-2xl`,
subtle borders/shadows, generous spacing. Status colours: emerald (ok), amber
(attention), rose (urgent), always paired with an icon and text. Body text ≥16px,
tap targets ≥44px, visible focus rings (`focus-visible:ring`). Responsive from 360px
phone width up to desktop. Font: Inter, self-hosted via `@fontsource-variable/inter`
(no Google Fonts or other third-party requests). Icons: `lucide-react`. Create original
SVG assets in `src/assets/`: a SilverCare logo mark + wordmark, a favicon
(`public/favicon.svg`), a login-page illustration (abstract: heart-rate line, home,
caring hands; no photos of people), and an empty-state illustration. Shared components
in `src/components/` (at least: `Button`, `TextField`, `PasswordField`, `Card`,
`StatusBadge`, `Alert`/`Notice`, `Spinner`, `Logo`, `AppHeader`, `EmptyState`).

### Login (`/login`)

- Shown to: signed-out users (signed-in users are redirected to `/`).
- Shows: split layout on desktop: left brand panel (teal gradient, logo, illustration,
  tagline "Peace of mind for every family", three short benefit points); right a card
  with "Sign in to SilverCare", email, password (show/hide), "Forgot password?" link,
  and "Sign in" button. On mobile the brand panel collapses to a header with the logo.
  A footer line: "Accounts are created by your care provider."
- Steps within the same page: password → (new password required) → (TOTP code), as in
  criteria 10–13.
- Loading / empty / error states: busy button while signing in; inline error notice
  for failed sign-in; a generic "Something went wrong. Please try again." for network
  or unexpected errors.

### Forgot password (`/forgot-password`)

- Shown to: signed-out users.
- Shows: same layout as login; step 1 email, step 2 code + new password + checklist.
- Loading / error: busy buttons; "That code is invalid or has expired." for a bad
  code; generic error for anything else; "Back to sign in" link on every step.

### Main page (`/`), staff dashboards — shared parts

Both the nurse and doctor dashboards have:

- Header: logo, page title ("Nurse dashboard" or "Doctor dashboard"), user menu (name,
  role badge "Nurse" or "Doctor", "Account settings", "Sign out").
- Staff area tabs: "Dashboard" (this page) and "Devices" (the staff-only devices view,
  `/devices`), with the current tab marked by `aria-current="page"` and a bar.
- Demo data banner.
- Greeting ("Good morning, <name>", based on Asia/Singapore time) and current date.
- Four stat tiles: Residents monitored, Open alerts (critical count shown too), Devices
  offline, Wearables low on battery (< 20 %, computed from demo `battery_pct`: this is a
  display filter, not an alert threshold).
- Alert rows: severity badge, alert type (the backend's names: Fall detected, High / Low
  heart rate, Low blood oxygen, High / Low skin temperature, Possible fall, and the
  device problems Device offline, Low battery, Wearable not worn), "Open" or "Acknowledged" label (a
  cancelled or resolved alert leaves the open list), resident name, resident ID and home ID, relative time ("12 min ago") with the
  exact time in a tooltip/`<time>`, "Acknowledged by the care centre nurse, <time>"
  once a nurse acknowledged a health alert, with the nurse's note under it if there is
  one ("Message from the care centre:"), or "Acknowledged by <name> (caregiver),
  <time>" for a device problem a caregiver acknowledged (the name is looked up by the
  API, like a caregiver cancel's), and, for an open alert, its routing to the care centre
  nurse (device problems never go to the nurse, so they have none):
  - **Doctor dashboard** (and the resident page): "Sent to the care centre nurse at
    once" for a critical alert or one from an unassigned device; for a warning,
    "Escalated to the care centre nurse" once `escalated_at` is set, or before that a
    live countdown "Escalates to the care centre nurse in m:ss" to the API's computed
    `escalates_at` (the 300 s check in `docs/features/escalation.md`, alert-routing.md).
  - **Nurse dashboard inbox:** none of these lines and no countdown, because the nurse
    is the recipient (the Critical badge already says a critical alert went to her, and
    the countdown is the caregiver's deadline, not hers). A warning still waiting to
    escalate shows a quiet "Not yet escalated" (icon and text, no timer), which goes
    away when `escalates_at` passes; the page announcer still says "Escalated to the
    care centre nurse: <type>, <resident>." at that moment.
- Residents panel: search by name or resident ID, filter chips by status
  (All / Urgent / Attention / OK, with counts), and a responsive grid of resident cards:
  initials avatar, name, resident ID, status badge, latest heart rate, SpO2, skin
  temperature, wearable battery and worn/charging state, and "last updated" time.
  Each card links to the staff-only resident page (`/residents/<resident_id>`).
- Loading: skeleton tiles and panels. Empty: "No residents yet", or if search/filter
  matches nothing, `EmptyState` "No residents match your search" with a "Clear filters"
  button; "No open alerts." for an empty alert list. Error: a notice "We couldn't load
  the dashboard." with a "Try again" button.

### Main page (`/`), nurse dashboard — nurse

- The stat tiles are toggle buttons that choose the panel below: Open alerts (the
  default), Devices offline, Wearables low on battery, or Residents.
- Open alerts inbox: open and acknowledged alerts, critical pinned to the top, then
  newest first. Each row has "Acknowledge" (open **health** alerts; opens the optional
  note form of `docs/features/alert-actions-ui.md`) and "Resolve" (open and acknowledged
  **health** alerts) buttons, which change local state; resolve shows the nurse's display
  name. Device problems have neither: tech staff or caregivers acknowledge them and the
  watchdog resolves them (decided 2026-11-05, kept at the 2026-11-07 merge). Rows show no
  routing lines and no countdown, only "Not yet escalated" on a warning still waiting
  (see the shared parts above). Resolved alerts move to a collapsed "Resolved since this
  page opened" list. With the API, a resolve needs a note (data-model.md, Track B route:
  resolve); the portal has no resolve note box yet.
- Devices offline panel (empty: "All devices are online.") and low-battery panel,
  lowest first (empty: "No wearables are below 20%.").

### Main page (`/`), doctor dashboard — doctor

- The stat tiles are plain cards, not buttons.
- Open alerts summary: the critical and acknowledged counts, "Nurses respond to
  alerts", and the 3 most urgent alerts as tiles, with no acknowledge or resolve
  buttons.
- Residents panel as above, with a sparkline of the last 5 readings for heart rate,
  SpO2, and skin temperature on each card.

### Main page (`/`), caregiver view — caregiver

- Header: logo, "My residents", user menu (as above, role badge "Caregiver").
- Demo data banner, greeting.
- One large card per linked resident: initials avatar, name, status badge with a plain
  sentence ("All readings look normal" / "Needs attention" / "Urgent: check now",
  taken from the demo data, not computed), latest vitals tiles (heart rate, SpO2, skin
  temperature, activity) each with a small inline SVG sparkline of the last 5 readings
  (matching DynamoDB's latest-5) and the time of the latest reading, device status
  (wearable battery, worn/charging, bedroom sensor online/offline), and a "Recent
  alerts" timeline for that resident (up to 5). Each alert shows its severity, type,
  and time; while it is open, its routing to the care centre nurse, worded for the
  caregiver, who isn't the recipient: "Sent to the care centre nurse at once" for a
  critical alert (or one from an unassigned device); for a warning, before its 5-minute
  check, a live countdown "The care centre nurse will be alerted in m:ss unless you
  cancel this alert" (in a `role="timer"`, not a live region; the page announcer says
  "Escalated to the care centre nurse: <type>, <resident>." when it reaches zero), then
  "Escalated to the care centre nurse"; and, once
  acknowledged, "Acknowledged by the care centre nurse, <time>" with the nurse's note
  if there is one (main's rule: everyone who can see the alert sees the note). Each
  alert also shows its lifecycle label and its cancel or resolve line, and an open or
  acknowledged **health** alert has a "Cancel alert" button with a confirmation step
  (local state only; `docs/features/alert-actions-ui.md`). Device problems have no
  Cancel: an **open device problem** has an "Acknowledge" button instead (accessible name
  "Acknowledge: <type>, <resident>"; decided 2026-11-05, kept at the 2026-11-07 merge).
  It acknowledges at once in local state, shows "Acknowledged by <your name>
  (caregiver), <time>" (the name is display only and never sent), and moves focus to
  that resident's "Recent alerts" heading. No resolve buttons.
- Loading: skeleton cards with "Loading your residents' information". Empty: `EmptyState` "No residents are linked to your account
  yet. Your care provider can link you." Error: as the staff dashboards.

### No access (`/`, signed in but none of the three groups; `family` alone counts as none)

- Shows: logo, "Your account isn't set up yet", "Ask your care provider to assign you a
  role.", and a "Sign out" button.

### Account settings (`/account`)

- Shown to: any signed-in user (criterion 15).
- Shows: profile card (name, email, role, read only), change password card, two-step
  verification card (status On/Off, "Set up authenticator app" flow with QR code, or
  "Turn off").
- Loading / error: busy buttons, inline errors, success notices.

## Alerts and notifications

None delivered by the portal. Demo alerts are display-only apart from the nurse
dashboard's local acknowledge and resolve; the escalation countdown only displays the
demo `escalates_at` / `escalated_at`. Alert emails and escalation are backend features
(`docs/features/alerts-core.md`, `vitals-alerts.md`, `escalation.md`).

## Privacy

- Personal data touched: the user's own email, name, and group (from Cognito/ID token);
  password and TOTP secret go only to Cognito via Amplify. No real resident data (demo
  only). On screen: the synthetic residents' readings and alerts; in the caregiver view,
  for the linked residents' alerts only, when the care centre nurse acknowledged and
  their note (`ack_note`, free text that may hold health or logistics details). The
  caregiver view's alert timeline is an `aria-live` region, so a note that arrives with
  new data is read out to that caregiver's own screen reader.
- The shared nurse account's Cognito `name` is "Care centre nurse" (set by the admin),
  so a resolve by the nurse shows "Resolved by Care centre nurse" and no individual
  nurse is named; the app has no special case for it.
- Retention (decided 2026-11-01, Open decision #4): when a user leaves (offboarding),
  an admin **disables** their Cognito account, so they can't sign in, then **deletes**
  it 90 days later. Accounts are not patient health records, and PDPA data
  minimisation applies, so they aren't kept longer. Both are manual admin steps in the
  Cognito console; nothing deletes accounts automatically, so the admin tracks the
  90 days. Deleting an account doesn't break the threshold change history: it
  captures the staff member's name when it's written (`changed_by_name`,
  `updated_by_name`). Alerts store only the `sub` (docs/features/alert-cancel.md); an
  acknowledge of a health alert is always shown as "the care centre nurse" (a device
  problem acknowledged by a caregiver shows that caregiver's name, looked up by the API
  like a cancel's, "Former caregiver" once deleted), and once the account is
  deleted an old resolve or caregiver cancel shows "Former staff member" or "Former
  caregiver" (see Actor names in `docs/data-model.md`). Tokens: Amplify's default token storage
  (browser `localStorage`), ID/access tokens expire after 60 min, refresh after 7 days;
  sign-out clears them and revokes the refresh token. The ID token sent to the API
  carries the user's `name` and `email`; it goes only to the API, over HTTPS, in the
  `Authorization` header, and the API never logs it (see API contract).
- Must not be logged or shown: no `console.log` of tokens, emails, names, passwords,
  codes, TOTP secrets, or the nurse's note. No personal data in URLs, query strings, or page titles (page
  title is "SilverCare" plus the page name only). The TOTP secret is shown only on the
  setup screen and not stored by the app. QR code is generated in the browser (no
  external QR service).
- No third-party analytics, fonts, or CDNs (CSP in `frontend-hosting` allows only
  `'self'` and Cognito).

## Out of scope

- API Gateway, Lambdas, real resident data, resident detail pages, saving acknowledged
  or resolved alerts (the nurse dashboard keeps them in local state only).
- Self sign-up, admin screens for creating users or linking them to residents.
- Hosted UI / OAuth / social login, SMS or email MFA, SES.
- Required MFA for staff (MFA is optional pool-wide for now).
- Uploading the build to S3 / CI deploy steps (see `frontend-hosting`).
- Translations (English only).

## Open questions

Must be empty before `status: ready`.
