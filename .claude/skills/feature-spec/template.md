---
status: draft          # draft | ready | built
---

# <Feature name>

## Summary

One or two sentences: what this feature does and why.

## Users and access

| Group | Can see | Can do |
|---|---|---|
| staff | | |
| caregiver | | |
| family | | |

How access to a specific resident is decided (e.g. "family members see only residents
they are linked to"):

## Acceptance criteria

Numbered and testable. The builders implement these, and the testers test them.

1. Given ..., when ..., then ...
2.

## Data

- Reads: which items, via which access pattern in `docs/data-model.md`
- Writes: which items, and how duplicates are handled
- New access patterns or tables (if any):

## IoT

Metrics or topics involved (see `docs/metrics.md`), or "none".

## API contract

### `METHOD /path`

- Groups:
- Path / query parameters:
- Request body:

```json
{}
```

- Response `200` (per role, if responses differ):

```json
{}
```

- Errors: `400` (invalid input), `403` (wrong group), `404` (not found or not linked)

## Screens

### <Screen name> (`/route`)

- Shown to:
- Shows:
- Actions:
- Loading / empty / error states:

## Alerts and notifications

What triggers an alert, who receives it, and how. Or "none".

## Privacy

- Personal data touched:
- Retention:
- Anything that must not be logged or shown:

## Out of scope

Things that are deliberately not part of this feature.

## Open questions

Must be empty before `status: ready`.

-
