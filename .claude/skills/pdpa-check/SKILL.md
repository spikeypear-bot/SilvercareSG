---
name: pdpa-check
description: Personal-data checklist for Singapore's PDPA. Use when a change collects, stores, logs, displays, or shares resident, caregiver, family, or staff data, and before PRs that touch such data.
---

# PDPA checklist

Personal data in this system includes: resident names, dates of birth, photos, health
readings, alerts, location and room data, device-to-resident links, and caregiver,
family, and staff names, emails, and phone numbers. Health and location data about
elderly people is sensitive, so treat it with extra care.

Go through each item that applies to the change.

## Collection
- [ ] Only collect fields the feature needs. Each new field has a stated purpose.
- [ ] No NRIC or other national ID numbers. Use internal opaque IDs.

## Storage
- [ ] Encrypted at rest (DynamoDB, S3) and in transit (TLS only).
- [ ] Stored in `ap-southeast-1`, including backups and logs.
- [ ] Has a retention mechanism (DynamoDB TTL, S3 lifecycle) matching the retention
      decision. If retention isn't decided yet, flag it rather than leaving data forever.
- [ ] No personal data in DynamoDB keys, S3 object keys, or resource names.

## Access
- [ ] Every API route checks the caller's group **and** their link to the specific
      resident on the server.
- [ ] Responses only include fields that role needs.
- [ ] IAM roles can reach only the tables and buckets they use.

## Logs and errors
- [ ] No personal data or payload bodies in Lambda logs, exceptions, metrics, or traces.
- [ ] Error responses don't reveal other users' data or whether a resident exists.
- [ ] Access to resident data can be traced (who accessed what, when) without logging
      the data itself. This helps with PDPA's mandatory data-breach notification.

## Frontend
- [ ] No personal data in browser storage, URLs, page titles, or console output.
- [ ] No third-party services receive personal data.

## Tests and docs
- [ ] Only synthetic data in tests, fixtures, seed data, examples, and docs.

Report each failed item with `file:line` and a fix. If you're unsure whether something
is allowed under the PDPA, say so and recommend the user check, rather than guessing.
