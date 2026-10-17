---
name: data-model
description: Design or change DynamoDB tables, keys, and indexes, and S3 buckets for stored data. Use when adding a table, GSI, attribute, or bucket, when a new query is needed, or when turning the IoT metrics contract into storage.
---

# Data model (DynamoDB + S3)

DynamoDB tables are designed from the queries the app runs, not from entities. The
design lives in `docs/data-model.md`; Terraform implements it. Update the doc first.

## Steps

1. Read `docs/metrics.md` (what devices send) and `docs/data-model.md` (current design).
2. Write down the access pattern in `docs/data-model.md` before designing keys:

   | # | Who / what | Query | Key condition | Frequency |
   |---|---|---|---|---|
   | 1 | Staff dashboard | Latest status of every resident at a site | pk = SITE#<id> | every 30s per open dashboard |

3. Design or extend the keys and GSIs so every listed pattern is a `GetItem` or `Query`.
4. Update `docs/data-model.md`: tables, key schema, item types with example items, GSIs,
   TTL, and which access pattern each one serves.
5. Implement in Terraform (follow `tf-change`), then run `make fmt` and `make tf-check`.
6. Summarise the change, and flag anything that replaces an existing table.

## DynamoDB rules

- `billing_mode = "PAY_PER_REQUEST"`.
- `point_in_time_recovery { enabled = true }`, `deletion_protection_enabled = true`, and
  server-side encryption on.
- Changing a table's name, hash key, or range key **destroys the table**. Never do it to
  an existing table without the user agreeing to a migration plan.
- Every GSI must serve a listed access pattern. Project only the attributes it needs.
- No `Scan` in application code.
- Keep high-volume time-series readings in their own table with a TTL attribute
  (`expires_at`, epoch seconds). Their volume and retention differ from residents,
  devices, and alerts.
- Avoid hot partitions: for high-rate metrics, include a time bucket in the partition key
  (e.g. `DEVICE#<id>#2026-10-17`) so one device doesn't concentrate all its writes.
- Attribute names are `snake_case`. Sort-key timestamps use ISO 8601 UTC
  (`2026-10-17T09:30:00Z`), which sorts correctly as a string.
- Every write path must be idempotent: IoT Core and Lambda retries deliver duplicates.
  Use a deterministic key (device + timestamp) or a condition expression.

## S3 rules

- Bucket name: `silvercare-<purpose>-<account_id>` (bucket names are globally unique).
- Block all public access, enforce `BucketOwnerEnforced`, encrypt, and deny requests
  without TLS in the bucket policy.
- Turn on versioning where losing an object matters. Add a lifecycle rule that matches
  the retention decision.
- Frontend hosting buckets are covered by the `static-site` skill.

## Never

- Store NRIC numbers or other national ID numbers.
- Put personal data in partition or sort keys. Keys show up in logs, metrics, and error
  messages; use opaque IDs.
