---
name: frontend
description: Conventions for the React + TypeScript + Tailwind frontend in frontend/ - structure, role-based views, API client, auth, accessibility, privacy, and checks. Use when creating or changing any page, component, or frontend config.
---

# Frontend

One Vite app in `frontend/` that serves the caregiver/family views and the staff
dashboard, chosen by Cognito group.

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
- Function components and hooks only.
- **Tailwind v4:** use the `@tailwindcss/vite` plugin and `@import "tailwindcss";` in
  `src/index.css`. Style with utility classes. Pull repeated class sets into
  components, not `@apply`.
- **Config** from `import.meta.env.VITE_*` (API URL, user pool ID, client ID). Everything
  in the bundle is public, so no secrets.
- **API calls** only go through `lib/api.ts`, which attaches the access token and turns
  error responses into typed errors.
- **Role-based views:** route guards read the user's groups. This only decides what to
  show; the API enforces access.
- Handle loading, empty, and error states on every data view.

## Accessibility

Staff and family members watch elderly residents' alerts on these screens, so
accessibility matters.

- Alerts must never rely on colour alone. Use text and an icon too.
- Colour contrast meets WCAG AA. Text is at least 16px. Tap targets are at least 44px.
- Semantic HTML (`button`, `nav`, headings in order), labelled form fields, and visible
  focus styles.
- Use an `aria-live` region for new alerts.

## Privacy

- No personal data in `localStorage`/`sessionStorage`, URLs or query strings (use opaque
  IDs), page titles, or `console.log`.
- No third-party analytics, fonts, or scripts that receive user data without the user
  deciding it.

## Checks

From `frontend/`: `npm run lint`, `npm run test` (Vitest + React Testing Library), and
`npm run build` must all pass before finishing. Summarise the screens and API calls
changed.
