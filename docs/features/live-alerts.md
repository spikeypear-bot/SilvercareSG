---
status: built          # draft | ready | built
---

# Live alerts (AppSync Events)

## Summary

When an alert or device problem is created, escalated, acknowledged, resolved, or
cancelled, open pages hear about it within about a second instead of at the next 30 s
poll. An AppSync Event API carries a small **ping** ("resident X's alerts changed") and
the page refetches through the existing API, which keeps enforcing who sees what.
Polling stays as the fallback.

Part 1 of 2. Part 2, `live-vitals`, pushes each wearable's reading and is specced after
this one works.

Decisions (user, 2026-11-09): AppSync Events for live data; alerts first, then vitals;
staff and caregivers get live updates; alert events are pings only (no health data);
30 s polling stays as fallback; caregiver access is checked by a Lambda; a caregiver
unlinked while connected may get bare pings for up to about 1 h (criterion 21a).

## Facts this rests on (checked 2026-11-09)

- AppSync is on the free plan's service list and in `ap-southeast-1` (AWS endpoints
  page). An Event API can use Cognito user pools for connect and subscribe, and IAM for
  publish, with separate modes per namespace (AppSync Events auth docs).
- With Cognito auth, a subscribe handler gets `identity.sub` and `identity.groups`
  (AppSync Events context reference). `onSubscribe` can be a **direct Lambda
  integration** (`behavior = "DIRECT"`, `invoke_type = "REQUEST_RESPONSE"`). Returning
  `null` allows the subscription; an error rejects it. Powertools'
  `AppSyncEventsResolver` handles this, and its `UnauthorizedException` rejects
  (Powertools docs).
- AppSync verifies the Cognito JWT itself. No JWT library is needed, so no packaging
  decision (CLAUDE.md open decision #5) is needed either.
- Terraform: `aws_appsync_api` (`event_config`), `aws_appsync_channel_namespace`
  (`handler_configs`, `publish_auth_mode`, `subscribe_auth_mode`), and
  `aws_appsync_datasource` exist in the pinned provider 6.69.1 (checked in its schema).
- The frontend already has `aws-amplify` 6.22, which includes the AppSync Events client
  (`events` from `aws-amplify/data`). No new npm package.
- Quotas: 200 subscriptions per connection; 5 segments and 50 characters per channel
  segment.
- Every alert write goes through `silvercare-alert-processor` (watchdog and
  api-alert-actions send SQS messages to it), so it's the only publisher.

## Cost

AppSync Events: US$1 per million operations (messages in and out, connects, subscribes,
pings) and US$0.08 per million connection-minutes. The free tier (250,000 updates,
600,000 connection-minutes a month) lasts for the account's first 12 months (AWS
pricing page). No hourly fee.

| Item (example: 8 pages open 8 h a day) | Per month |
|---|---|
| ~115,000 connection-minutes | ~US$0.01 |
| keep-alives and connects/subscribes (~120,000 operations) | ~US$0.12 |
| alert pings: a few hundred in, a few thousand out | ~US$0 |
| subscribe-check Lambda: one call per subscribe (page load) | ~US$0 (always-free) |
| DynamoDB: one GetItem per caregiver subscribe | inside the fixed 25 RCU |
| **Total** | **~US$0–0.15/month** (US$0 inside the free tier) |

No AppSync logging (`log_config` unset): no CloudWatch Logs cost, and no event data in logs.

## Users and access

| Group | Can subscribe to | Gets |
|---|---|---|
| doctor, nurse | `/health/*`, `/device/*`, or one resident's `/health/<id>`, `/device/<id>` | every resident's pings |
| caregiver | `/health/<id>` and `/device/<id>` only for residents they're linked to; no wildcards | linked residents' pings |
| ops | nothing (keeps polling; user chose staff + caregivers) | — |
| `family` or no group | nothing | — |
| anyone | can't publish: publish is IAM-only, and only alert-processor's role may | — |

How access to a specific resident is decided: the same rule as the API (`docs/data-model.md`
pattern 13). The registry item `pk = USER#<sub>`, `sk = RESIDENT#<resident_id>` must exist.

Check order in the subscribe Lambda (so a refusal can't reveal whether a resident
exists): group → channel shape (`/health/<id>` or `/device/<id>`, `<id>` matching
`res-[0-9]{4}`, or a staff wildcard) → link GetItem (caregivers only). Every refusal
returns the same `UnauthorizedException` text.

What other users see about who did something: nothing new. A ping names no user.

## Channels and pings

- Namespaces: `health` (health alerts) and `device` (device problems: offline, low
  battery, not worn).
- Channel per resident: `/health/<resident_id>`, `/device/<resident_id>`.
- Ping payload, exactly: `{"type": "changed", "resident_id": "res-0003"}`. No alert
  type, status, values, names, or user IDs.

## Acceptance criteria

### Infra (`infra/realtime.tf`)

1. `aws_appsync_api` `silvercare-events`: auth providers `AMAZON_COGNITO_USER_POOLS`
   (pool `silvercare-users`, region `ap-southeast-1`, `app_id_client_regex` = the web
   client ID) and `AWS_IAM`. Connection and default subscribe mode Cognito, default
   publish IAM. No `log_config`.
2. Namespaces `health` and `device`: `publish_auth_mode` `AWS_IAM`,
   `subscribe_auth_mode` `AMAZON_COGNITO_USER_POOLS`, `on_subscribe` DIRECT to the
   subscribe Lambda (`REQUEST_RESPONSE`). No `on_publish` handler (events pass through).
3. New Lambda `silvercare-realtime-subscribe` (`lambdas/realtime-subscribe/`) via the
   `python-lambda` module. Its role may only `dynamodb:GetItem` on the registry table.
   An `aws_appsync_datasource` (type `AWS_LAMBDA`) with its own service role that may
   only `lambda:InvokeFunction` that function.
4. `silvercare-alert-processor`'s role gains `appsync:EventPublish` on the two
   namespace ARNs only, and `appsync:EventConnect` is not granted. Its environment gains
   `EVENTS_HTTP_DNS`.
5. Outputs `events_http_dns` and `events_realtime_dns` (not sensitive, like `api_url`).
   `deploy.yml` passes `VITE_EVENTS_URL=https://<events_http_dns>/event` to the build.
6. The CloudFront CSP `connect-src` adds `https://<events_http_dns>` and
   `wss://<events_realtime_dns>`, and nothing else.

### Subscribe Lambda

7. doctor or nurse: wildcard and per-resident channels in both namespaces are allowed,
   with no DynamoDB call.
8. caregiver: `/health/<id>` or `/device/<id>` is allowed only when the link item
   exists (GetItem, `ProjectionExpression` `pk`). A missing link is refused. A wildcard
   is always refused, with no DynamoDB call.
9. ops, `family`, no groups, or any other group: refused with no DynamoDB call. A user
   in nurse and caregiver is treated as staff.
10. Bad channel shapes (an unknown namespace, extra segments, an ID not matching
    `res-[0-9]{4}`, an empty segment) are refused before any DynamoDB call.
11. Every refusal has the same error text. Logs: outcome, namespace, `sub` (if it's
    valid UUID format, else `invalid`), groups, `resident_id` (if valid). Never the
    token or claims.
12. A DynamoDB error refuses the subscription (fail closed) and logs only the error code.

### Publishing (alert-processor)

13. After a successful write, one ping is published to the alert's resident channel
    (`health` or `device` by `alert_type` in `DEVICE_PROBLEM_TYPES`), for these
    outcomes: new alert stored (`STORED`, `NOTIFIED`), `ESCALATED`, status `UPDATED`.
    No ping for `DUPLICATE`, `SUPPRESSED`, `SKIPPED_UNASSIGNED` (no resident), `STALE`,
    `NOT_ESCALATED`, `SKIPPED`, `NO_OPEN_ALERTS`, `NOT_OPEN`, `NOT_ALLOWED`, or `INVALID`.
14. A status update that changes several alerts of one resident sends one ping.
15. The publish is an HTTP POST to `https://<EVENTS_HTTP_DNS>/event`, signed with SigV4
    (service `appsync`) using botocore's `SigV4Auth` and `urllib` (no new package), with
    the timeouts in 16a.
16. A failed publish (HTTP error, timeout, signing error) is logged by error code only
    and **never** fails the SQS record. The alert is already stored, emailed, and
    escalated as before; polling shows it within 30 s. Test: a publish that raises still
    leaves the record successful, with no change to emails or escalation.
16a. Pings never slow the alert path (added 2026-11-10): each invocation may spend at
    most 1.5 s on pings in total, each network step times out after 1 s, and after the
    first failed ping the rest of that batch's pings are skipped (logged as `skipped`).
    Worst case about 3.5 s per batch, well inside the 30 s timeout, so an AppSync outage
    can't make a batch time out or reach the DLQ.
17. Publish happens after the DynamoDB write, never before, so a refetch sees the change.

### Frontend

18. `Amplify.configure` adds the Events endpoint from `VITE_EVENTS_URL`, region
    `ap-southeast-1`, default auth `userPool`. Without `VITE_EVENTS_URL` (local dev),
    nothing connects and polling works as now.
19. Nurse and doctor dashboards subscribe to `/health/*` and `/device/*`. The resident
    page (staff) subscribes to that resident's two channels. The caregiver view
    subscribes to each linked resident's two channels, using the IDs it already loads.
    The ops dashboard and devices page don't subscribe.
20. On a ping, the page refetches its data through its existing hook, at most once per
    2 s (pings within 2 s share one refetch). The 30 s polling keeps running.
21. A connect or subscribe failure isn't shown to the user. It's logged to the console
    without the token, and polling continues. Subscriptions close when the page unmounts
    or the user signs out.
21a. Every 55 minutes an open page closes its subscriptions and subscribes again with
    the current (refreshed) token, so an unlinked caregiver is refused within about 1 h.
    AWS's docs (checked 2026-11-09) don't say whether AppSync closes an open connection
    when its token expires, so the page does it. Test with fake timers.
22. Tests (Vitest, the Events client mocked): each role subscribes to exactly the
    channels above; a ping triggers one refetch; two pings within 2 s trigger one; no
    `VITE_EVENTS_URL` means no subscription; re-subscribe after 55 minutes.

### Checks

23. `make check` and the frontend lint, test, and build pass.

### Go-live checks (owner, after merge)

- Open the nurse dashboard. Use `make smoke-test ... ALERTS=1` (owner only) or a
  simulator `fall`. The alert should appear within about 2 s, not at the next poll.
- Sign in as a caregiver linked to `res-0001` (seeded links). A fall on `res-0001` shows
  live; one on an unlinked resident doesn't.

## Data

- Reads: registry pattern 13 (GetItem `USER#<sub>` / `RESIDENT#<id>`), now also from
  `realtime-subscribe`, on caregiver subscribes only. Update pattern 13's "used by" in
  `docs/data-model.md`.
- Writes: none. AppSync Events stores nothing.

## IoT

None.

## API contract

No HTTP API changes. Event API contract: see Channels and pings.

## Screens

No new screens. Nurse dashboard, doctor dashboard, resident page, and caregiver view
update sooner. Loading, empty, and error states are unchanged.

## Alerts and notifications

No change to which alerts exist or who is emailed. Pings only make pages refresh.

## Privacy

- Personal data touched: the opaque `resident_id` in pings, which tells a subscriber
  "something changed for this resident". Caregivers only get their linked residents.
  No health data, names, or user IDs in events.
- Not stored: AppSync Events keeps no messages; AppSync logging is off.
- Logs: the subscribe Lambda logs `sub`, groups, and `resident_id`, like the API Lambdas.

## Out of scope

- Live vitals (`live-vitals`, next spec): those events will carry the reading.
- Ops and the devices page going live.
- A "live" indicator or connection status in the UI.
- Changing the polling interval.
- AppSync logging, a custom domain, WAF.

## Open questions

None. (Unlinked while connected, user 2026-11-09: acceptable. Bare pings, with no data,
for up to about 1 h until the next re-subscribe; the API refuses the refetch at once.)
