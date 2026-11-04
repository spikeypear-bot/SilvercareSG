---
status: built          # draft | ready | built
---

# Alerts core (fall alerts end to end)

## Summary

Turn a wearable `fall` into an alert that is stored and emailed, and make sure no fall
alert can be lost on the way. `fall-alert` looks up the device's resident and sends an
alert message to a new **alerts queue** (SQS). A new **`alert-processor`** Lambda writes
the alert to `silvercare-alerts` and to S3 `alerts/`, then emails the resident's
subscribed caregivers and family through **SNS**. Every failure point ends in a
dead-letter queue watched by a CloudWatch alarm that emails the team.

This is Track A1 and the first part of the alerts path in
`docs/decisions/0001-iot-rule-routing.md`. Builds on `ingest-live` and
`ingest-history`. Nurse escalation after 60 s (A3), vitals alerts (A2), and the
watchdog (A4) come later and reuse the queue and `alert-processor` built here.

## Users and access

No Cognito group uses this feature. There is no API and no screen. The staff dashboard
won't show these alerts until the API feature (Track B) reads `silvercare-alerts`.

| Group | Can see | Can do |
|---|---|---|
| staff | nothing in the portal yet | nothing |
| caregiver | alert emails for the residents they're subscribed to | nothing |
| family | alert emails for the residents they're subscribed to | nothing |

How access to a specific resident is decided: each email subscriber is listed, by a
label, with the resident IDs they follow (criterion 24). SNS delivers an alert only to
subscribers whose list contains the alert's `resident_id`. This list is kept by hand in
GitHub, separately from the registry's user ↔ resident links, because SNS can only
email addresses that have subscribed and confirmed (user's decision, 2026-11-02).

Who does what:

| Who | Does |
|---|---|
| Devices (simulated) | Publish `fall` as today. No contract change |
| `fall-alert` | Validate, look up the resident, send the alert to the queue, then store the fall |
| `alert-processor` | Store the alert (DynamoDB, S3), email subscribers |
| Team (operators) | Get DLQ alarm emails |
| Developer | Sets the GitHub secret and variables, confirms subscriptions, runs the seed script and the live check |
| CI (`deploy.yml`) | Applies the Terraform with the subscriber variables |
| Claude and its agents | Build the code. No AWS access |

## Acceptance criteria

### Alert message (shared)

1. One shared module in `lambdas/ingest/src/` (e.g. `alerts.py`) builds alert
   messages, so A2 and A4 reuse it. An alert message is a JSON object:

   | Field | Example | Notes |
   |---|---|---|
   | `schema_version` | `1` | |
   | `alert_id` | `6f1c2e0a-5b3d-5e8f-9a47-000000000001` | UUID v5 (below) |
   | `alert_type` | `fall` | only `fall` in this feature |
   | `resident_id` | `res-0003` or `unassigned` | from the registry (criterion 6) |
   | `device_id` | `wearable-0007` | |
   | `event_ts` | `2026-10-18T03:14:07.360Z` | the fall message's `ts` |
   | `received_at` | `2026-10-18T03:14:07.410Z` | from the IoT rule, ISO 8601 ms `Z` |
   | `source_message_id` | `c2a1d4f0-…` | the fall message's `message_id` |
   | `details` | `{"confidence": 0.91, "peak_g": 3.8, "still_s": 20}` | fall fields only; `accel_window` is never included |

2. `alert_id` = `uuid5(ALERT_NAMESPACE, f"{event_ts}|{alert_type}|{device_id}")`, where
   `ALERT_NAMESPACE` is one fixed UUID constant in the shared module. The same fall
   processed any number of times gives the same `alert_id`.
3. Numbers survive the queue without float errors (e.g. `0.91` stays `0.91` in
   DynamoDB): parse the body with `Decimal` for floats.

### `fall-alert` changes

4. Order for a valid `fall`: (a) registry lookup, (b) `SendMessage` to the alerts
   queue, (c) the existing latest-5 write. Sending first keeps the alert off the slower
   write path. If (b) or (c) raises, the function raises, so Lambda's async retry runs
   it again; the duplicate alert message is harmless because `alert_id` is the same.
5. A rejected (invalid) `fall` raises no alert, as today: it's logged and dropped, and the raw      message is still in S3 history.
6. Registry lookup (pattern 1): strongly consistent is not needed; GetItem
   `pk = DEVICE#<device_id>`, `sk = ASSIGNMENT` on `silvercare-registry`. `resident_id`
   is the item's `resident_id`, or `unassigned` if the item is missing or has no
   `resident_id` (user's decision: alert anyway, email nobody). The result, including
   "unassigned", is cached per container for 5 minutes.
7. The send uses `MessageAttributes` `alert_type` (String) and no message group or
   delay. The log line for the fall gains `alert_id` and `alert_outcome: "queued"`.
8. `fall-alert` env gains `ALERTS_QUEUE_URL` and `REGISTRY_TABLE`.
9. Async invoke config for `silvercare-fall-alert` only (the module's
   `on_failure_destination`): `maximum_retry_attempts = 2`,
   `maximum_event_age_in_seconds = 3600`, on failure → `silvercare-fall-alert-dlq`. 
   `device-health` and `telemetry` are unchanged (no destination).
10. The `silvercare_alert` IoT rule gets an `error_action`: SQS
    `silvercare-fall-alert-dlq`, using the existing `silvercare-iot-rules` role, so a
    fall the rule can't hand to the Lambda is kept too. The other two rules are
    unchanged. (This amends "no error action" in the decision record for `fall-alert`
    only.)

### Queues

11. `silvercare-alerts` (SQS standard): SSE-SQS, visibility timeout 180 s (6 ×
    `alert-processor` timeout), retention 4 days, redrive to `silvercare-alerts-dlq`
    after 5 receives. Queue policy denies non-TLS access, like the history queue.
12. `silvercare-alerts-dlq` and `silvercare-fall-alert-dlq`: SSE-SQS, retention 14
    days, TLS-only queue policy. `silvercare-alerts-dlq` has a redrive-allow policy for
    `silvercare-alerts` only. `silvercare-fall-alert-dlq` allows `SendMessage` from the
    `silvercare-fall-alert` role (destination) and the `silvercare-iot-rules` role
    (rule error action) only.

### `alert-processor`

13. New function `silvercare-alert-processor` in `lambdas/alert-processor/src/handler.py`
    (`lambda_handler`): `python3.13`, `arm64`, 128 MB, timeout 30 s, the same pinned
    Powertools layer, 30-day log group. Env: `ALERTS_TABLE`, `LATEST_TABLE`,
    `HISTORY_BUCKET`, `ALERTS_TOPIC_ARN`, `PORTAL_URL` (the CloudFront URL output),
    `POWERTOOLS_SERVICE_NAME`, `POWERTOOLS_METRICS_NAMESPACE=SilverCare`.
14. SQS event source mapping on `silvercare-alerts`: batch size 10, no batching window
    (`maximum_batching_window_in_seconds = 0`), `ReportBatchItemFailures`. Each message
    is handled on its own; a failure reports only that message's ID.
15. A body that isn't a valid alert message (criterion 1: missing field, wrong type,
    unknown `alert_type`, bad UUID) is logged with a reason code and **reported as a
    failure**, so it ends in `silvercare-alerts-dlq` and raises the alarm. It is never
    silently dropped.
16. For a valid message, in this order:
    1. **Enrich (fall only):** GetItem `pk = DEVICE#<device_id>`, `sk = MSG#vitals` on
       `silvercare-latest` and add `details.latest_vitals` =
       `{"hr_bpm", "spo2_pct", "ts"}` from `entries[0]` (values may be `null`). No item
       → no `latest_vitals`. (User's decision: fall emails include the latest vitals
       with when they were taken.)
    2. **Store** (pattern 6): PutItem on `silvercare-alerts` with condition
       `attribute_not_exists(pk)`. Item = the `silvercare-alerts` attributes in
       `docs/data-model.md`: `pk = RESIDENT#<resident_id>`,
       `sk = ALERT#<event_ts>#<alert_id>`, `alert_id`, `alert_type`, `device_id`,
       `event_ts`, `received_at`, `created_at` (now), `status = open`,
       `source_message_id`, `details`, `open_pk = OPEN`. No `expires_at` (set on
       resolve, later). On a failed condition (a retry), GetItem the existing item and
       use it from here on.
    3. **Archive:** PutObject `s3://<history bucket>/alerts/date=<event_ts UTC date>/<alert_id>.json`,
       the item as one JSON object, `Content-Type: application/json`. The same key is
       overwritten on a retry, so there's one object per alert.
    4. **Notify**, only if `resident_id` isn't `unassigned` and the item has no
       `notified_at`: SNS `Publish` to the alerts topic with `Subject` and `Message`
       from criterion 18 and message attribute `resident_id` (String). Then UpdateItem
       `SET notified_at = :now` with condition `attribute_not_exists(notified_at)`. If
       the function dies between Publish and this update, the retry emails again: a
       duplicate email is accepted, a missing one isn't.
    5. Unassigned alerts set `notification = "skipped_unassigned"` on the item in the
       same PutItem and are never published.
17. Throttling or other AWS errors in steps 1–4 fail that message (reported in
    `batchItemFailures`), so SQS redelivers it; after 5 receives it moves to the DLQ.
18. Email (plain text, no HTML). Times in Singapore time (SGT) only, no UTC (user's
    decision, 2026-11-02). No names, no device ID, no email addresses:

    ```
    Subject: SilverCare alert: Fall - res-0003

    SilverCare alert

    Type: Fall
    Resident: res-0003
    When: 2026-10-18 11:14:07 SGT
    Fall details: confidence 0.91, peak 3.8 g, still for 20 s
    Latest vitals (taken 11:13:00 SGT): heart rate 72 bpm, SpO2 97 %

    Open the SilverCare portal: https://<cloudfront domain>/

    This is an automated message. Do not reply.
    ```

    A `null` vital prints "no reading". No `latest_vitals` prints
    "Latest vitals: none received". The subject is at most 100 characters.
19. Dispatch latency: after a successful Publish, log `dispatch_latency_ms` = Publish
    return time − `received_at`, and emit it as a Powertools metric
    `AlertDispatchLatency` (Milliseconds, dimension `alert_type`). This is the evidence
    for the "under 2 s to notification dispatched" goal (A5 analyses it).
20. Log one line per message with `alert_id`, `alert_type`, `device_id`, `resident_id`,
    and the outcome (`stored`, `duplicate`, `notified`, `skipped_unassigned`,
    `invalid`, `error`). Never `details`, vitals, email addresses, or the email body.

### SNS and alarms

21. Topic `silvercare-alerts`: encrypted with the AWS managed key `alias/aws/sns`
    (free). Topic policy allows `sns:Publish` only from the `alert-processor` role and
    denies non-TLS. One email subscription per label in `alert_subscribers`
    (criterion 24) with `filter_policy_scope = "MessageAttributes"` and
    `filter_policy = {"resident_id": [<that label's resident IDs>]}`.
22. Topic `silvercare-ops` for alarms: **not encrypted**, because CloudWatch alarms
    can't publish to a topic using the AWS managed SNS key and a customer-managed key
    costs about US$1/month. It carries only alarm names and states, no personal data;
    give checkov's encryption rule a documented skip. Topic policy allows
    `sns:Publish` only from `cloudwatch.amazonaws.com` for this account's alarms. One
    email subscription per label in `ops_subscribers`.
23. Two CloudWatch alarms, `silvercare-alerts-dlq-not-empty` and
    `silvercare-fall-alert-dlq-not-empty`: `ApproximateNumberOfMessagesVisible`,
    Maximum, period 300 s, 1 datapoint, threshold ≥ 1, missing data not breaching,
    `alarm_actions` and `ok_actions` → `silvercare-ops`.

### Subscriber configuration

24. Three Terraform variables in `infra/variables.tf`, each defaulting to empty so
    `tf-check` and local validation work without them:
    - `subscriber_emails`: `map(string)`, **`sensitive = true`**, label → email.
    - `alert_subscribers`: `map(object({ resident_ids = list(string) }))`, label →
      resident IDs. Not sensitive.
    - `ops_subscribers`: `list(string)` of labels. Not sensitive.

    Resources use `for_each` over the **labels** (never over emails, which can't be
    `for_each` keys when sensitive and would appear in resource addresses in the public
    plan comment). Validation: labels match `^[a-z0-9-]{1,32}$`, resident IDs match
    `^res-[0-9]{4}$`, and every label in `alert_subscribers` and `ops_subscribers` has
    an entry in `subscriber_emails`.
25. `plan.yml` and `deploy.yml` pass them as `TF_VAR_subscriber_emails` from the
    repository **secret** `SUBSCRIBER_EMAILS`, and `TF_VAR_alert_subscribers` and
    `TF_VAR_ops_subscribers` from repository **variables** `ALERT_SUBSCRIBERS` and
    `OPS_SUBSCRIBERS`. (The `.github/` edit is made by the main Claude session, not the
    builders.) Developers keep their working copy of the three values in the git-ignored
    `secrets/subscribers.json` (`{"subscriber_emails": {…}, "alert_subscribers": {…},
    "ops_subscribers": […]}`) and share it privately like the rest of `secrets/`. CI
    can't read `secrets/`, so the values reach Terraform only through the GitHub secret
    and variables. Until those are set, CI applies with the empty defaults: everything
    is created except the email subscriptions.

### IAM (follow the `iam-policy` skill)

26. `silvercare-fall-alert` role adds, on top of today's statement: `dynamodb:GetItem`
    on the `silvercare-registry` table ARN with `dynamodb:LeadingKeys` `DEVICE#*`;
    `sqs:SendMessage` on `silvercare-alerts`; and (added by the module)
    `sqs:SendMessage` on `silvercare-fall-alert-dlq`. `device-health` and `telemetry`
    keep exactly today's policy, so they need their own policy document.
27. `silvercare-alert-processor` role, nothing more:
    - `dynamodb:PutItem`, `GetItem`, `UpdateItem` on the `silvercare-alerts` table ARN
      (not the index), `LeadingKeys` `RESIDENT#*`
    - `dynamodb:GetItem` on `silvercare-latest`, `LeadingKeys` `DEVICE#*`
    - `s3:PutObject` on `<history bucket ARN>/alerts/*` only
    - `sns:Publish` on the `silvercare-alerts` topic only
    - `sqs:ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes` on
      `silvercare-alerts` only
    - `kms:GenerateDataKey` and `kms:Decrypt` on the `alias/aws/sns` key, only if the
      `iam-policy` skill shows the publisher needs them; otherwise none
    - its own log group (module)
28. The `silvercare-iot-rules` role adds `sqs:SendMessage` on
    `silvercare-fall-alert-dlq`.
29. The history bucket policy is unchanged except as needed for criterion 27 (no
    principal other than `alert-processor` may write `alerts/`).

### Seed script (developer runs it)

30. `scripts/seed-registry.py`, run on the host with the developer's AWS credentials,
    region `ap-southeast-1`, creates obviously synthetic registry items:
    - `RESIDENT#res-0001` … `res-0003` / `PROFILE`: `entity_type = RESIDENT`,
      `display_name` `Test Resident A` … `C`, `home_id` `home-0001` … `0003`,
      `created_at`, `updated_at`. No other profile fields (they're still proposed).
    - One `DEVICE#<id>` / `ASSIGNMENT` item for **every** device in
      `infra/devices/devices.json`, `entity_type = DEVICE`, `device_type`.
      `wearable-000N` and `bedroom-000N` for N = 1–3 are assigned to `res-000N`
      (`resident_id`, `home_id`, `assigned_at`, `gsi1_pk`, `gsi1_sk`); the rest are
      unassigned spares.
    - PutItem with `attribute_not_exists(pk)`: existing items are left as they are and
      reported as `exists`, so running it twice changes nothing and it never
      reassigns a device.
    - `--dry-run` prints the items instead of writing. Prints only keys and outcomes.
    - Creates no AWS client when imported.

### Docs

31. `docs/data-model.md`: alerts item gains `received_at`, `notified_at`, and
    `notification`; `resident_id = unassigned` (`pk = RESIDENT#unassigned`) for
    devices with no assignment; S3 `alerts/` layout from criterion 16.3; two new
    patterns: `alert-processor` reads a wearable's latest vitals (GetItem
    `DEVICE#<id>` / `MSG#vitals`) and re-reads an existing alert (GetItem on alerts).
32. After the build, the main Claude session updates
    `docs/decisions/0001-iot-rule-routing.md` (rule error action and destination for
    `fall-alert`) and `README.md` ("Alerts": set the secret and variables, confirm the
    subscription emails, run the seed script, run the live check).

### Tests

33. Unit tests (moto for DynamoDB, SQS, S3, SNS) in `lambdas/ingest/tests/` and
    `lambdas/alert-processor/tests/` cover: `alert_id` determinism; assigned and
    unassigned lookups and the cache; send-before-store order; a send failure raising;
    an invalid fall raising no alert; alert-processor storing, archiving, and notifying
    once; a redelivered message creating no second item and no second email; a
    message whose earlier attempt stored but didn't notify getting notified;
    unassigned alerts never published; invalid bodies reported as failures;
    `latest_vitals` present, with nulls, and absent; the email text and subject length;
    Decimal round trip; partial batch failure reporting only the failed ID; log lines
    containing no health values or emails.
34. `scripts/tests/` covers the seed script's items and `--dry-run` with no network.

### Live check (real AWS, the developer runs it)

35. After deploy: confirm the subscription emails; run the seed script; run
    `make smoke-test DEVICE=wearable-0001 ENDPOINT=<host> INGEST=1` (it already sends a
    valid `fall`). Pass when: the res-0001 subscriber gets one email within a minute
    and other subscribers get none; `silvercare-alerts` has one item under
    `RESIDENT#res-0001`; `alerts/date=<today>/` has one object; both DLQs are empty;
    the `AlertDispatchLatency` metric has a datapoint. Then smoke-test `wearable-0010`
    (unassigned): one item under `RESIDENT#unassigned`, no email.

## Data

- Reads: registry `DEVICE#<id>` / `ASSIGNMENT` (pattern 1, cached); latest
  `DEVICE#<id>` / `MSG#vitals` (new); alerts item on retry (new).
- Writes: alerts item, conditional on `attribute_not_exists(pk)` (pattern 6), then
  `notified_at` once; S3 `alerts/` one object per alert; registry seed items
  (pattern 17, conditional).
- Duplicates: deterministic `alert_id` plus the conditional PutItem; `notified_at`
  guards the email.
- Capacity: alerts are rare (a few per day in testing), within the existing
  `silvercare-alerts` 3 RCU / 2 WCU. No capacity change.

## IoT

Topic `silvercare/v1/+/fall` only. No contract change, no device policy change. The
`silvercare_alert` rule gains an error action (criterion 10).

## API contract

None.

## Screens

None. Track B's API will show `silvercare-alerts` on the staff dashboard.

## Alerts and notifications

- Trigger: a valid wearable `fall` message.
- Who: subscribers whose `alert_subscribers` entry lists the resident. Nobody for an
  unassigned device (the alert is still stored).
- How: SNS email, sent once per alert (rarely twice after a crash, accepted).
- Nurses: not notified in this feature. Escalation after 60 s is A3.
- Operators: DLQ alarm emails to `ops_subscribers`.

## Privacy

- Personal data touched: falls and the latest heart rate and SpO2 of a resident,
  linked to an opaque resident ID; subscriber email addresses (GitHub secret, SNS
  subscriptions); synthetic resident display names in the seed.
- **Health data in email (user's decision, 2026-11-02):** fall details and latest
  vitals are emailed with the opaque resident ID, no name. Email copies them to mail
  providers outside `ap-southeast-1`. Accepted while all data is synthetic; **revisit
  before real resident data** (with the shared IAM user and data-read items in
  CLAUDE.md).
- Retention: alert items until resolved + 30 days (TTL, set later); S3 `alerts/` with
  no expiry until Open decision #4; DLQ messages 14 days (`silvercare-fall-alert-dlq`
  holds the raw fall payload, including `accel_window`, linked to `device_id`, plus
  Lambda error details; encrypted, TLS-only); Lambda logs 30 days (IDs and outcomes
  only); subscriber emails until removed from the secret. Subscriber emails are also
  stored in plain text in the Terraform state, which developers can read: use team
  addresses, not real family members', until that is revisited.
- Must not be logged or shown: `details`, vitals, email addresses, email bodies,
  display names. Subscriber labels must not be people's names (e.g. `caregiver-c1`, not
  `alice`), because they appear in the public plan comment.

## Cost

US prices from memory; check `ap-southeast-1` under Billing → Free Tier. At a few
alerts a day in testing:

- SNS: email 1,000/month free; publishes within the 1M free requests
- SQS, Lambda: within the always-free tiers
- CloudWatch: 2 alarms of the 10 free; 1 custom metric of the 10 free
- S3: a few PUTs, ≈ US$0
- KMS (`alias/aws/sns`): AWS managed key, no monthly fee; requests within the 20,000
  free

About **US$0/month**. No fixed fees.

## Out of scope

- Nurse escalation after 60 s, cancelling, acknowledging, resolving (A3).
- Vitals thresholds, `vitals_alert` (A2). Watchdog: offline and possible fall (A4).
- Repeat suppression (pattern 7): not needed for falls, each fall is its own event.
- Showing alerts in the portal (Track B).
- Raising alerts for invalid `fall` messages.
- SMS, push, or deriving recipients from registry user links.
- Reserved concurrency for `fall-alert` (needs the account's Lambda quota).
- An alarm on `silvercare-history-dlq` (Track E can add it with the ops topic).
- Thresholds, user links, and Cognito users in the seed script.

## Open questions

None.
