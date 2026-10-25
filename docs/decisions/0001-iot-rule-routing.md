# 0001: IoT rule routing

Status: **accepted** 2026-10-25 · Proposed: 2026-10-18

## Context

Devices publish six message types (see `docs/metrics.md`). They differ in urgency and
in what the backend does with them:

- `status` heartbeats describe the device, not the resident.
- `vitals`, `motion`, and `bed` are telemetry: high volume, stored, and checked for
  abnormal values.
- `fall` is an alert raised by the device itself and must be handled as fast as possible.

Two constraints shape the design:

1. **An IoT rule only runs when a message arrives.** It cannot detect that something did
   *not* happen (a device going silent, a resident not returning to bed).
2. **Most alerts are derived, not sent.** Abnormal vitals need each resident's own
   thresholds, which IoT SQL cannot look up, and a possible fall from the bedroom sensor
   needs a sequence of events over time. Only `fall` arrives as an alert.

## Decision

Three IoT rules, split by pathway, plus a scheduled watchdog, and a single alerts queue
that every alert goes through (amended 2026-10-18: alerts go through SQS instead of
being written straight to the alerts table). Every rule also sends each message to a
history queue that is written to S3 in batches (amended 2026-10-25, see
[History path](#history-path)).

```
                    ┌─ rule: health ──── silvercare/v1/+/status ──────────► device-health λ ──► latest table
                    │
device ── MQTT ──► IoT Core ─ rule: telemetry ─ silvercare/v1/+/{vitals,motion,bed} ─► telemetry λ ──► latest table
                    │                                                                       │ abnormal vitals
                    └─ rule: alert ───── silvercare/v1/+/fall ────────────► fall-alert λ ───┤
                                                                                             ▼
             EventBridge schedule (5 min) ──► watchdog λ ── offline / possible fall ──► alerts queue (SQS) ── max retries ──► DLQ
                                                                                             │
                                                                                             ▼
                                                                                     alert-processor λ
                                                                                      ├─► SNS ──► caregivers
                                                                                      ├─► alerts table ──► staff (nurses) dashboard
                                                                                      └─► S3 (alert history)

every rule, second action ──► history queue (SQS) ── batches of up to 5 min ──► history λ ──► S3 (telemetry history)
                                     └── max retries ──► history DLQ
```

| Rule | SQL | Actions |
|---|---|---|
| `health` | `SELECT *, topic(3) AS device_id, topic(4) AS message, timestamp() AS received_at FROM 'silvercare/v1/+/status'` | Lambda `device-health`; SQS `silvercare-history` |
| `telemetry` | `SELECT *, topic(3) AS device_id, topic(4) AS message, timestamp() AS received_at FROM 'silvercare/v1/+/+' WHERE topic(4) = 'vitals' OR topic(4) = 'motion' OR topic(4) = 'bed'` | Lambda `telemetry`; SQS `silvercare-history` |
| `alert` | `SELECT *, topic(3) AS device_id, topic(4) AS message, timestamp() AS received_at FROM 'silvercare/v1/+/fall'` | Lambda `fall-alert`; SQS `silvercare-history` |

All rules use SQL version `2016-03-23`. A message that a Lambda rejects as invalid is
logged (`device_id`, `message_id`, reason) and dropped. Lambdas invoked by IoT run
asynchronously and get Lambda's two automatic retries. There is no error action and no
failed-message queue for the live path (decided 2026-10-25): a message whose Lambda
keeps failing is dropped and shows only as an error in the logs, and the raw message is
still in S3 history, because each rule action runs independently. Revisit this for
`fall-alert` when it starts raising alerts, where a dropped message would be a lost
fall alert.

Responsibilities:

- **`device-health`**: validate, add the `status` to the device's latest 5 (the newest
  one is the current state: `last_seen`, battery, worn, charging). Whether a device that
  recovers resolves its own offline or low-battery alert is decided in the alerting
  spec. If it does, `device-health` sends a resolve message to the alerts queue, so
  `alert-processor` stays the only writer of alert items.
- **`telemetry`**: validate, add the message to the device's latest 5 idempotently,
  compare vitals against the resident's thresholds. The newest `bed` and `motion`
  entries are the bedroom's current state. Sends an alert to the queue when vitals are
  abnormal.
- **`fall-alert`**: validate, send a fall alert to the queue, add the fall to the device's latest 5. Kept separate so a telemetry backlog,
  throttling, or bug can never delay a fall. Gets reserved concurrency if the account
  allows it: AWS keeps 10 concurrent executions unreserved, so an account whose limit is
  10 (common on new accounts) can't reserve any. Check Service Quotas → Lambda →
  Concurrent executions before relying on it.
- **`watchdog`** (EventBridge schedule, every 5 minutes): raises alerts for conditions
  defined by absence: a device with no `status` for 15 minutes (offline), and a
  resident out of bed at night with no motion and no return within N minutes (possible
  fall).
- **Alerts queue** (SQS): the single entry point for every alert, whatever raised it.
  Detectors only send a message; they don't notify or store anything themselves.
  A message that fails processing is retried, and after the maximum number of
  receives it moves to the **DLQ** (dead-letter queue).
- **`alert-processor`** (SQS trigger): for each alert, publishes to **SNS**, which
  notifies the resident's caregivers; writes the alert to the **alerts table**, which
  the staff (nurses) dashboard shows; and writes it to **S3** to keep track of every
  alert. The alert ID is deterministic, so a retried message can't create a duplicate
  alert item.

Storage (amended 2026-10-18): DynamoDB keeps only live state, at most the latest 5
messages per device and type (`docs/data-model.md`). Full history of telemetry and
alerts goes to S3 for analysis.

### History path

Added 2026-10-25. Amazon Data Firehose and Kinesis Data Streams are only available on
AWS's paid plan, and Kinesis Data Streams also has a fixed hourly fee, so neither is
used. An SQS queue and a small Lambda do Firehose's job instead: they collect messages
and write them to S3 in batches.

- **History queue** (SQS standard, `silvercare-history`, SSE-SQS encryption): the
  second action of every IoT rule. The message body is the rule's output: the device
  payload plus `device_id`, `message`, and `received_at`. After 5 failed receives a
  message moves to `silvercare-history-dlq` (kept 14 days).
- **`history` Lambda** (SQS event source): batches of up to 1,000 messages or 5
  minutes, whichever comes first. It groups the batch by message type and UTC date and
  writes one gzipped JSON Lines file per group to
  `s3://silvercare-history-<account_id>/telemetry/message=<message>/date=<yyyy-mm-dd>/<file>.jsonl.gz`.
  The prefixes give Lake Formation and Athena one table per message type, split by day.
- **Messages are stored as received**, including ones the ingest Lambdas reject, so
  cleaning happens later in Lake Formation. A redelivered batch can store a message
  twice; the cleaning step removes duplicates by `message_id`.
- **Alerts** go to the same bucket under `alerts/`, written by `alert-processor`.
- **Bucket:** all public access blocked, `BucketOwnerEnforced`, SSE-S3, TLS-only bucket
  policy, in `ap-southeast-1`. How long history is kept is Open decision #4 (retention);
  the lifecycle rule follows that decision.
- **Cost** (US prices from memory, check `ap-southeast-1`): SQS ~1.3M sends plus
  Lambda polling, a little over the 1M free requests, about US$0.50/month; ~43,000 S3
  PUTs (5 types × one file per 5 min) about US$0.25/month; Lambda inside the free tier;
  storage well under 1 GB/month. About **US$0.75/month** in total, with no fixed fee.

### Device safety net (added 2026-10-25)

When alerting is built, the wearable gains a `vitals_alert` message for critical
readings detected on the device (fixed limits, see `docs/metrics.md`). The `alert`
rule then receives `silvercare/v1/+/fall` **and** `silvercare/v1/+/vitals_alert`
(an in-place rule update, no replacement), so both device-raised alerts take the fast
path, separate from telemetry. The cloud's per-resident check of `vitals` stays as it
is; the two can raise alerts for the same episode, and repeat suppression (pattern 7
in `docs/data-model.md`) handles the overlap.

## Consequences

- Falls are isolated from the high-volume path.
- Alert delivery is decoupled from detection: changing how caregivers are notified
  touches only `alert-processor` and SNS.
- The queue absorbs spikes and retries failures; alerts that keep failing are kept in
  the DLQ instead of being lost.
- "Offline" is decided from `status` heartbeats alone, so the telemetry path never has
  to update `last_seen`.
- Six Lambdas instead of three, counting `history`. Each is small and has one job.
- History lands in S3 up to 5 minutes late. Nothing live reads it, so the delay doesn't
  matter.
- Three Lambdas send alert messages, and only `alert-processor` writes alert items. The
  message and item shapes are defined once in `docs/data-model.md`.
- Thresholds, the night window, and N minutes for a possible fall are product decisions,
  set in the alerting spec.
