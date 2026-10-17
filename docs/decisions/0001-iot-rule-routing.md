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

Three IoT rules, split by pathway, plus a scheduled watchdog and a single alerts table
that every alert goes through.

```
                    ┌─ rule: health ──── silvercare/v1/+/status ──────────► device-health λ ──► devices table
                    │
device ── MQTT ──► IoT Core ─ rule: telemetry ─ silvercare/v1/+/{vitals,motion,bed} ─► telemetry λ ──► readings table
                    │                                                                       │ abnormal vitals
                    └─ rule: alert ───── silvercare/v1/+/fall ────────────► fall-alert λ ───┤
                                                                                             ▼
             EventBridge schedule (5 min) ──► watchdog λ ── offline / possible fall ──► alerts table
                                                                                             │ DynamoDB Stream
                                                                                             ▼
                                                                                       notifier λ ──► delivery (TBD)
```

| Rule | SQL | Action |
|---|---|---|
| `health` | `SELECT *, topic(3) AS device_id, timestamp() AS received_at FROM 'silvercare/v1/+/status'` | Lambda `device-health` |
| `telemetry` | `SELECT *, topic(3) AS device_id, topic(4) AS message, timestamp() AS received_at FROM 'silvercare/v1/+/+' WHERE topic(4) = 'vitals' OR topic(4) = 'motion' OR topic(4) = 'bed'` | Lambda `telemetry` |
| `alert` | `SELECT *, topic(3) AS device_id, timestamp() AS received_at FROM 'silvercare/v1/+/fall'` | Lambda `fall-alert` |

All rules use SQL version `2016-03-23` and have an `error_action` to a shared
destination for rejected or failed messages.

Responsibilities:

- **`device-health`**: validate, update the device's current state (`last_seen`,
  battery, worn, charging). Clears an offline or low-battery alert when the device recovers.
- **`telemetry`**: validate, store the reading idempotently, compare vitals against the
  resident's thresholds, update the bedroom's current state (in bed / out of bed, last
  motion). Writes an alert when vitals are abnormal.
- **`fall-alert`**: validate, write a fall alert. Kept separate so a telemetry backlog,
  throttling, or bug can never delay a fall. Gets reserved concurrency.
- **`watchdog`** (EventBridge schedule, every 5 minutes): raises alerts for conditions
  defined by absence: a device with no `status` for 15 minutes (offline), and a
  resident out of bed at night with no motion and no return within N minutes (possible
  fall).
- **Alerts table**: the single place every alert ends up, whatever raised it. Writes
  are conditional, so a retried message cannot create a duplicate alert.
- **`notifier`** (DynamoDB Stream on the alerts table): delivers new alerts. Delivery
  channels are Open decision #2.

## Consequences

- Falls are isolated from the high-volume path.
- Alert delivery is decoupled from detection: adding SMS or push later touches only
  the notifier.
- "Offline" is decided from `status` heartbeats alone, so the telemetry path never has
  to update `last_seen`.
- Five Lambdas instead of three. Each is small and has one job.
- Three Lambdas write alert items. The alert item shape is defined once in
  `docs/data-model.md`; until shared code is packaged (Open decision #5), each Lambda
  carries its own small copy of the write.
- Thresholds, the night window, and N minutes for a possible fall are product decisions,
  set in the alerting spec.
