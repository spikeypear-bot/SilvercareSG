---
status: built          # draft | ready | built
---

# Sign-in and main page

## Summary

A Cognito user pool with four groups (`doctor`, `nurse`, `caregiver`, `family`) and a
React sign-in flow (email + password, optional authenticator-app MFA). After signing in,
users land on a main page whose layout depends on their group: doctors and nurses get
the **staff dashboard**, caregivers and family get the **family view**. There is no API
yet, so the main page shows clearly labelled synthetic demo data from one local module.

## Users and access

| Group | View | Can see | Can do |
|---|---|---|---|
| doctor | staff dashboard | every (demo) resident, all (demo) open alerts | sign in/out, set up or remove MFA, change password |
| nurse | staff dashboard | same as doctor | same as doctor |
| caregiver | family view | only the (demo) residents linked to them | sign in/out, set up or remove MFA, change password |
| family | family view | only the (demo) residents linked to them | sign in/out, set up or remove MFA, change password |

- `doctor` and `nurse` together are what the rest of the docs call **staff**.
- A user in several groups: if any group is `doctor` or `nurse`, show the staff
  dashboard; otherwise if any is `caregiver` or `family`, show the family view.
- A signed-in user with none of the four groups sees a "no access" screen (see Screens).
- How access to a specific resident is decided: in this feature all data is demo data,
  so nothing is enforced. When the API exists, staff see every resident, and caregiver/
  family see a resident only after the link check (data-model pattern 13). The group is
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
    (`info` | `warning` | `critical`), as the API will later provide. Alert thresholds
    are not defined in this feature.
20. A "Demo data" banner is visible on the main page in both views, with text (not
    only colour): "You are viewing synthetic demo data. No real resident information is
    shown."
21. Staff dashboard shows 20 demo residents and their open demo alerts; the family view
    shows the 2 demo residents linked to the signed-in demo account (the same 2 for every
    caregiver/family user).
22. Statuses and alert severities are always shown with an icon **and** a text label,
    never colour alone. The alerts list is inside an `aria-live="polite"` region.
23. `npm run lint`, `npm run test`, and `npm run build` pass in `frontend/`.

### Dependencies

Allowed runtime npm packages (already installed by the app scaffold): `react`,
`react-dom`, `react-router-dom`, `aws-amplify`, `lucide-react`,
`@fontsource-variable/inter`, `qrcode.react`. No others.

## Data

- Reads: none from AWS. Main page reads only `demoData.ts`. Auth reads the user's own
  Cognito attributes (`name`, `email`) and groups from the ID token.
- Writes: Cognito only, the user's own password and MFA settings.
- New access patterns or tables: none. When the API exists, the staff dashboard maps to
  patterns 5, 8, 9 and the family view to patterns 14, 10–12 in `docs/data-model.md`.

## IoT

None directly. Demo data mirrors the `vitals` and `status` messages in `docs/metrics.md`.

## API contract

None. This feature calls only Cognito (via Amplify). `src/lib/api.ts` is not created yet.

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

### Main page (`/`), staff dashboard — doctor, nurse

- Header: logo, "Staff dashboard", user menu (name, role badge e.g. "Doctor",
  "Account settings", "Sign out").
- Demo data banner.
- Greeting ("Good morning, <name>", based on Asia/Singapore time) and current date.
- Four stat tiles: Residents monitored, Open alerts (critical count shown too), Devices
  offline, Wearables low on battery (< 20 %, computed from demo `battery_pct`: this is a
  display filter, not an alert threshold).
- Open alerts panel, newest first: severity badge, alert type (Fall detected, Possible
  fall, Inactivity, Vitals need attention, Device offline, Low battery), resident name
  and ID, relative time ("12 min ago") with the exact time in a tooltip/`<time>`.
  No acknowledge/resolve buttons.
- Residents panel: search by name or resident ID, filter chips by status
  (All / Urgent / Attention / OK, with counts), and a responsive grid of resident cards:
  initials avatar, name, resident ID, status badge, latest heart rate, SpO2, skin
  temperature, wearable battery and worn/charging state, and "last updated" time.
  Cards are not clickable yet.
- Loading: skeleton tiles and cards. Empty: if search/filter matches nothing,
  `EmptyState` "No residents match your search" with a "Clear filters" button.
  Error: a notice "We couldn't load the dashboard." with a "Try again" button.

### Main page (`/`), family view — caregiver, family

- Header: logo, "My family", user menu (as above, role badge "Caregiver"/"Family").
- Demo data banner, greeting.
- One large card per linked resident: initials avatar, name, status badge with a plain
  sentence ("All readings look normal" / "Needs attention" / "Urgent: check now",
  taken from the demo data, not computed), latest vitals tiles (heart rate, SpO2, skin
  temperature, activity) each with a small inline SVG sparkline of the last 5 readings
  (matching DynamoDB's latest-5) and the time of the latest reading, device status
  (wearable battery, worn/charging, bedroom sensor online/offline), and a "Recent
  alerts" timeline for that resident (up to 5).
- Loading: skeleton cards. Empty: `EmptyState` "No residents are linked to your account
  yet. Your care provider can link you." Error: as staff.

### No access (`/`, signed in but none of the four groups)

- Shows: logo, "Your account isn't set up yet", "Ask your care provider to assign you a
  role.", and a "Sign out" button.

### Account settings (`/account`)

- Shown to: any signed-in user (criterion 15).
- Shows: profile card (name, email, role, read only), change password card, two-step
  verification card (status On/Off, "Set up authenticator app" flow with QR code, or
  "Turn off").
- Loading / error: busy buttons, inline errors, success notices.

## Alerts and notifications

None delivered. Demo alerts are display-only; alerting rules are still Open decision #2.

## Privacy

- Personal data touched: the user's own email, name, and group (from Cognito/ID token);
  password and TOTP secret go only to Cognito via Amplify. No resident data (demo only).
- Retention: Cognito accounts stay until an admin deletes them (user-data retention is
  still Open decision #4 in `CLAUDE.md`). Tokens: Amplify's default token storage
  (browser `localStorage`), ID/access tokens expire after 60 min, refresh after 7 days;
  sign-out clears them and revokes the refresh token.
- Must not be logged or shown: no `console.log` of tokens, emails, names, passwords,
  codes, or TOTP secrets. No personal data in URLs, query strings, or page titles (page
  title is "SilverCare" plus the page name only). The TOTP secret is shown only on the
  setup screen and not stored by the app. QR code is generated in the browser (no
  external QR service).
- No third-party analytics, fonts, or CDNs (CSP in `frontend-hosting` allows only
  `'self'` and Cognito).

## Out of scope

- API Gateway, Lambdas, real resident data, resident detail pages, acknowledging or
  resolving alerts.
- Self sign-up, admin screens for creating users or linking them to residents.
- Hosted UI / OAuth / social login, SMS or email MFA, SES.
- Required MFA for staff (MFA is optional pool-wide for now).
- Uploading the build to S3 / CI deploy steps (see `frontend-hosting`).
- Translations (English only).

## Open questions

Must be empty before `status: ready`.
