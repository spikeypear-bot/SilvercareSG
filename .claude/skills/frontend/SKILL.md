---
name: frontend
description: Conventions for the React + TypeScript + Tailwind frontend in frontend/ - structure, role-based views, API client, auth, accessibility, privacy, and checks. Use when creating or changing any page, component, or frontend config.
---

# Frontend

One Vite app in `frontend/` that serves the caregiver/family views, the nurse and doctor
dashboards, and the staff-only resident page, chosen by Cognito group.

## Structure

```
frontend/src/
├── main.tsx, App.tsx      # app entry and routes
├── features/<feature>/    # pages + components for one area (residents, alerts, auth)
├── components/            # shared UI building blocks
├── lib/api.ts             # typed API client: the only place that calls fetch
├── lib/auth.ts            # Amplify Auth wrapper (see cognito-auth)
└── types/                 # API request/response types
```

## Rules

- TypeScript `strict`. No `any`; type every API response.
- Function components and hooks only. A `.tsx` component file exports only components;
  put helpers it shares (predicates, formatters) in a plain `.ts` module, or lint warns
  `only-export-components`.
- **Tailwind v4:** use the `@tailwindcss/vite` plugin and `@import "tailwindcss";` in
  `src/index.css`. Style with utility classes. Pull repeated class sets into
  components, not `@apply`.
- **Config** from `import.meta.env.VITE_*` (API URL, user pool ID, client ID). Everything
  in the bundle is public, so no secrets.
- **API calls** only go through `lib/api.ts`, which attaches the ID token (`getApiToken()`
  in `lib/auth.ts`; not the access token, which lacks the `name` claim) and turns
  error responses into typed errors. Never send who did an action (name, email, sub):
  the server takes it from the token. A name shown before the response is display only.
- **Role-based views:** route guards read the user's groups. This only decides what to
  show; the API enforces access.
- Handle loading, empty, and error states on every data view.
- Whether an alert is still open: use `isOnOpenList` (`features/dashboard/stats.ts`:
  `open` or `acknowledged`, the data model's open list), in app code and demo data alike.
  Never `status !== 'resolved'`: `cancelled` closes an alert too, and so may later statuses.
- Views poll (`useLoaded` in `features/dashboard/useDashboardData.ts`), and alert actions
  are applied asynchronously after the `202`, so a refetch can still return the old
  status. Show an action's result through the hooks' overrides (kept while the server
  shows any status the alert had before this session's actions), never by writing it
  into the loaded data, which the next poll replaces. Forms that a poll could disturb
  call `useHoldRefresh` (`lib/refreshHold.ts`).

## Accessibility

Staff and family members watch elderly residents' alerts on these screens, so
accessibility matters.

- Alerts and states (offline, low battery, charging, ...) must never rely on colour or
  an `aria-hidden` icon alone. Use text too.
- Text that reads as one phrase ("12% · Low", "Wearable · wearable-0001") goes in one
  inline element: JSX drops the space between elements on separate lines, and a flex
  `gap` hides that on screen but not for screen readers.
- Colour contrast meets WCAG AA. Text is at least 16px. Tap targets are at least 44px.
- Semantic HTML (`button`, `nav`, headings in order), labelled form fields, and visible
  focus styles.
- Use an `aria-live` region for new alerts, rendered once per page and fed from the
  data, not from list rows that a tab or filter can unmount. Don't put lists with
  ticking times or countdowns inside a live region.

## Privacy

- No personal data in `localStorage`/`sessionStorage`, URLs or query strings (use opaque
  IDs), page titles, or `console.log`.
- No third-party analytics, fonts, or scripts that receive user data without the user
  deciding it.
- A field the backend also validates (e.g. a note) copies the backend's code, not a
  doc's summary of it, and its test uses the backend's own cases: U+2028/U+2029 are
  category Zl/Zp, not C, so a `\p{C}` check alone lets them through.
- A free-text field's hint says who receives what's typed and how long it is kept, from
  `docs/data-model.md` (Retention and the attribute's row), e.g. a health record kept for
  the resident's lifetime + 6 years, not a vague "kept in the record".
- When a screen starts showing new personal data (a note, another user's name), update
  that feature spec's Privacy section in the same change: data touched, retention, and
  what must not be logged.

## Checks

From `frontend/`: `npm run lint`, `npm run test` (Vitest + React Testing Library), and
`npm run build` must all pass before finishing. Run them one after another, and never
while another test run is going (e.g. a `frontend-tester` agent's): overlapping runs
skip files or fail workers, and the counts can't be trusted. Summarise the screens and
API calls changed.
