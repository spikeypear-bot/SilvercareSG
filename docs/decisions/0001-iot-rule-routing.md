# 0001: IoT rule routing

Status: **proposed** · Date: 2026-10-18

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
being written straight to the alerts table).

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
```

| Rule | SQL | Action |
|---|---|---|
| `health` | `SELECT *, topic(3) AS device_id, timestamp() AS received_at FROM 'silvercare/v1/+/status'` | Lambda `device-health` |
| `telemetry` | `SELECT *, topic(3) AS device_id, topic(4) AS message, timestamp() AS received_at FROM 'silvercare/v1/+/+' WHERE topic(4) = 'vitals' OR topic(4) = 'motion' OR topic(4) = 'bed'` | Lambda `telemetry` |
| `alert` | `SELECT *, topic(3) AS device_id, timestamp() AS received_at FROM 'silvercare/v1/+/fall'` | Lambda `fall-alert` |

All rules use SQL version `2016-03-23` and have an `error_action` to a shared
destination for rejected or failed messages.

Responsibilities:

- **`device-health`**: validate, add the `status` to the device's latest 5 (the newest
  one is the current state: `last_seen`, battery, worn, charging). Clears an offline or
  low-battery alert when the device recovers.
- **`telemetry`**: validate, add the message to the device's latest 5 idempotently,
  compare vitals against the resident's thresholds. The newest `bed` and `motion`
  entries are the bedroom's current state. Sends an alert to the queue when vitals are
  abnormal.
- **`fall-alert`**: validate, send a fall alert to the queue, add the fall to the device's latest 5. Kept separate so a telemetry backlog,
  throttling, or bug can never delay a fall. Gets reserved concurrency.
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
alerts goes to S3 for analysis; that path is designed separately.

## Consequences

- Falls are isolated from the high-volume path.
- Alert delivery is decoupled from detection: changing how caregivers are notified
  touches only `alert-processor` and SNS.
- The queue absorbs spikes and retries failures; alerts that keep failing are kept in
  the DLQ instead of being lost.
- "Offline" is decided from `status` heartbeats alone, so the telemetry path never has
  to update `last_seen`.
- Five Lambdas instead of three. Each is small and has one job.
- Three Lambdas send alert messages, and only `alert-processor` writes alert items. The
  message and item shapes are defined once in `docs/data-model.md`.
- Thresholds, the night window, and N minutes for a possible fall are product decisions,
  set in the alerting spec.
