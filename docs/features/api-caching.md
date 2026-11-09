---
status: built          # draft | ready | built
---

# API caching: keep-warm and vitals cache

## Summary

Two small changes so common pages load faster, at no cost:

1. **Keep-warm:** one EventBridge schedule calls the busy API Lambdas every 5 minutes,
   so a warm container is usually ready and a request rarely hits a cold start.
2. **Vitals cache:** `silvercare-api-vitals` keeps Athena results in memory until the
   next daily refresh, so reopening a resident's vitals trends page skips the Athena
   query (a few seconds).

Decisions (user, 2026-11-09): keep-warm for the API Lambdas; vitals results cached in
memory until 09:30 SGT the next day, no pre-computing and no keep-warm for vitals;
keep-warm for 4 functions only (dashboard, residents, ops, devices).

## How it works (for the reader)

- A Lambda keeps its memory between requests while its container is warm. AWS drops an
  idle container after a while (not documented; often 5–15 minutes). The next request
  then starts cold: Python and Powertools load again (about 1 s) and in-memory caches
  are empty. Containers running at the same time each have their own memory.
- A ping every 5 minutes keeps **one** container per function warm. Two requests at the
  same moment can still start a second, cold container. Provisioned concurrency would
  guarantee warm containers but bills hourly, so it's not used.
- Athena data (hourly and daily rows) only changes when `silvercare-clean` runs at
  08:30 SGT (`infra/clean.tf`, `cron(30 0 * * ? *)`) and then `silvercare-summaries`
  (its on-success destination). Each has a 300 s timeout.
- Athena's own "reuse query results" can't be used: AWS docs say it isn't supported when
  a table's S3 location is registered in Lake Formation (Athena user guide, "Reuse query
  results", checked 2026-11-09).

## Cost

| Item | Per month |
|---|---|
| EventBridge rule, every 5 min: ~8,640 events | ~US$0 (EventBridge Scheduler free tier is 14 million invocations; the repo already uses scheduled rules for `clean` and `anomaly`) |
| Lambda: ~35,000 pings (4 functions), ~10 ms each | ~US$0 (always free: 1 million requests, 400,000 GB-s) |
| CloudWatch Logs | ~US$0: pings write no log lines |
| DynamoDB | none: pings make no reads |
| No alarm | the 10 free alarms are used; a missed ping only means a cold start |

## Users and access

No change to who sees what.

| Group | Can see | Can do |
|---|---|---|
| doctor | vitals trends as now; results may come from the cache (same rows) | nothing new |
| nurse / caregiver / ops | nothing new | nothing new |

- The vitals cache is filled and read only **after** the existing checks (group
  `doctor`, then parameters). It holds Athena rows only; the caller isn't part of the
  key because every doctor sees every resident (`vitals-trends.md`). If a non-staff
  group ever gets this route, its link check must run before the cache.
- A ping can't be forged from outside: API Gateway builds the HTTP API event, and a
  client can't set its top-level `source` field. Only the keep-warm rule may invoke the
  functions with it (`aws_lambda_permission` with that rule's `source_arn`).

## Acceptance criteria

### Keep-warm

1. `infra/keep_warm.tf`: one `aws_cloudwatch_event_rule` `silvercare-api-keep-warm`,
   `schedule_expression = "rate(5 minutes)"`, with one target each for
   `silvercare-api-dashboard`, `silvercare-api-residents`, `silvercare-api-ops`, and
   `silvercare-api-devices`. Each target's input is `{"source": "silvercare.keep-warm"}`.
2. One `aws_lambda_permission` per function: principal `events.amazonaws.com`,
   `source_arn` = that rule's ARN.
3. Each of the 4 handlers returns `{"warm": true}` at once when
   `event.get("source") == "silvercare.keep-warm"`: before any group check, AWS call,
   or log line. Test: a ping event makes no DynamoDB/Cognito calls and logs nothing.
4. An API Gateway event that carries `source` inside its body or headers is handled as a
   normal request (the 403/400 checks still apply). Test per handler.

### Vitals cache

5. `api-vitals` keeps a module-level cache of Athena results, keyed by the exact query
   string. The query text already contains the resident, device IDs, and date range, so
   a device reassignment or a different range is a different key.
6. Only the Athena rows are cached. The registry and THRESHOLDS reads (`interval=hour`)
   still run on every request, so a doctor's threshold change shows at once.
7. An entry expires at the next **09:30 SGT** after it was stored. That leaves room for
   clean and summaries after 08:30, including Lambda's 2 automatic retries each (about
   09:10 worst case); between 08:30 and 09:30 pages may show the previous day's rows.
   An expired entry is never returned. Test with a fixed clock: stored 10:00 → served at
   09:29 next day, not at 09:30; stored 09:00 → expires 09:30 the same day.
8. Only results of queries that finished `SUCCEEDED` are cached. Failures, timeouts
   (504), and the no-device case (no query) aren't.
9. At most 200 entries; when full, the oldest entry is dropped before a new one is
   stored. Test.
10. Logs add one field, `cache` = `hit` | `miss`. No rows, values, or SQL text are logged
    (unchanged rule).
11. A cache hit returns the same response body as a miss for the same request. Test.

### Checks

12. `make check` passes.

## Data

- Reads: unchanged. Fewer Athena queries on a hit (pattern 35/36 queries skipped).
- Writes: none. The cache is process memory only.

## IoT

None.

## API contract

Unchanged (`GET /residents/{resident_id}/vitals`, `docs/features/vitals-trends.md`).
Pings aren't API requests.

## Screens

None. Pages are the same, just faster on a warm container or a cache hit.

## Alerts and notifications

None.

## Privacy

- Personal data touched: vitals trend rows (health data) are now held in `api-vitals`'s
  memory for up to a day. They're in the same function and region as now, never written
  anywhere, gone when the container stops, and never logged.
- Erasure: a resident's cached rows go when the entry expires (by 09:30 SGT the next
  day) or the container is replaced (any deploy).
- Pings carry no personal data.

## Out of scope

- Pre-filling caches on a ping (it would use DynamoDB capacity every 5 minutes).
- Keep-warm for `api-vitals`, `api-nightly`, `api-nightly-overview`, `api-thresholds`,
  `api-alert-actions` (user chose the 4 busy ones).
- Pre-computing vitals results, S3 or DynamoDB caches, provisioned concurrency,
  ElastiCache, DAX, API Gateway caching.
- Clearing the cache by hand. While the owner re-runs clean by hand (the backfill
  reload), cached rows can be stale until the next 09:30 SGT; a deploy clears them.
  The freeze in `CLAUDE.md` doesn't cover `lambdas/api-vitals/` (it reads Athena, it
  doesn't change tables).
- Longer TTLs for the existing 5-minute caches.

## Open questions

None.
