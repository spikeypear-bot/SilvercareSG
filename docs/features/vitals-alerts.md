---
status: draft          # draft | ready | built
---

# Vitals alerts (Track A2)

## Summary

Raise alerts for abnormal vitals, from two sources that share one path:

- **Cloud check (warning):** `telemetry` compares every wearable `vitals` message with
  the resident's thresholds and raises an alert after **2 abnormal readings in a row**.
- **Device safety net (critical):** the wearable's instant `vitals_alert` message (fixed
  limits, held 10 s; planned in `docs/metrics.md`) joins the v1 contract and goes
  through the fast `alert` rule to `fall-alert`, which raises a critical alert at once.

Both send to the alerts queue from A1. `alert-processor` stores, archives, and emails
them as it does falls, and now holds back repeats: the same alert for the same resident
and severity at most once every 30 minutes.

Builds on `alerts-core` (`docs/features/alerts-core.md`), which must be merged first.
Escalation and cancel (A3) and the watchdog (A4) come later.

## Users and access

No Cognito group uses this feature. There is no API and no screen (alerts show on the
staff dashboard once Track B reads `silvercare-alerts`).

| Group | Can see | Can do |
|---|---|---|
| staff | nothing in the portal yet | nothing |
| caregiver | alert emails for the residents they're subscribed to | nothing |
| family | alert emails for the residents they're subscribed to | nothing |

How access to a specific resident is decided: unchanged from `alerts-core`. SNS delivers
an alert only to subscribers whose `alert_subscribers` entry lists its `resident_id`.

| Who | Does |
|---|---|
| Wearables (simulated) | Send `vitals` every 60 s, and `vitals_alert` when a critical reading lasts 10 s |
| `telemetry` | Store `vitals` as today, then run the cloud check and send warnings to the queue |
| `fall-alert` | Also handles `vitals_alert`: send a critical alert, then store it |
| `alert-processor` | Hold back repeats, then store, archive, email (as in A1) |
| Developer | Runs the seed script and the live check |

## Product decisions (user, 2026-11-02)

| Decision | Value |
|---|---|
| Default thresholds | heart rate below 50 or above 120 bpm; SpO2 below 92 %; skin temperature below 30.0 or above 38.0 °C |
| Per resident | the resident's `THRESHOLDS` item when present, per metric; otherwise the defaults |
| Cloud check | alert after 2 consecutive abnormal readings |
| Repeats | same resident + alert type + severity at most once per 30 minutes |
| Severities | cloud check = `warning`, device `vitals_alert` = `critical`, fall = `critical`. A warning never holds back a critical alert |

The limits are synthetic and must be checked with a clinician before any real use.

## Acceptance criteria

### Alert types and messages (shared)

1. New alert types, one per metric and direction:

   | `alert_type` | Raised when | Email label |
   |---|---|---|
   | `hr_high` | heart rate above max | High heart rate |
   | `hr_low` | heart rate below min | Low heart rate |
   | `spo2_low` | SpO2 below min | Low blood oxygen |
   | `skin_temp_high` | skin temperature above max | High skin temperature |
   | `skin_temp_low` | skin temperature below min | Low skin temperature |

   The device's `vitals_alert` maps `hr_bpm` + `above` → `hr_high`, `hr_bpm` + `below`
   → `hr_low`, `spo2_pct` + `below` → `spo2_low`.
2. The alert message (A1 criterion 1) gains a required `severity` field: `warning` or
   `critical`. Falls are `critical`. `alert_id` stays
   `uuid5(ALERT_NAMESPACE, f"{event_ts}|{alert_type}|{device_id}")`.
3. `details` for vitals alerts:
   - cloud (`warning`): `{"metric", "value", "limit", "direction", "previous_value"}`,
     e.g. `{"metric": "hr_bpm", "value": 128, "limit": 120, "direction": "above",
     "previous_value": 125}`
   - device (`critical`): `{"metric", "value", "limit", "direction", "held_s"}`
4. `alert-processor`'s validation (`message.py`) accepts the new types, `severity`, and
   their `details` (numbers in each metric's valid range from `docs/metrics.md`;
   `direction` `above` or `below`; `held_s` 10–600). A message without a valid
   `severity` is reported as a failure (DLQ), like any invalid body.

### Thresholds

5. A shared module in `lambdas/ingest/src/` (e.g. `thresholds.py`) holds the defaults
   from the decisions table and resolves a resident's limits: GetItem
   `pk = THRESHOLDS#<resident_id>`, `sk = THRESHOLDS` on `silvercare-registry`
   (pattern 2), item format as in `docs/data-model.md` (`hr_bpm {min, max}`,
   `spo2_pct {min}`, `skin_temp_c {min, max}`). Each limit present and valid in the
   item replaces that default; a missing or invalid limit keeps the default (an
   invalid one logs `resident_id` and the field name, never the value). Results,
   including "no item", are cached per container for 5 minutes.
6. A value is abnormal when it is strictly below `min` or strictly above `max`
   (`hr_bpm` 50 and 120 are normal). `null` is never abnormal.

### Cloud check in `telemetry`

7. Only for wearable `vitals`, after the existing latest-5 write, and only when the
   outcome is `stored` or `duplicate` (a retry re-runs the check; the deterministic
   `alert_id` prevents a second alert) and the message is the newest entry
   (`entries[0]`). `too_old`, `motion`, and `bed` are never checked.
8. Look up the resident (A1 `registry.py`, pattern 1). An unassigned device uses the
   defaults and `resident_id = unassigned`.
9. For each of `hr_bpm`, `spo2_pct`, `skin_temp_c` separately: raise when the newest
   **two** entries are both abnormal in the **same direction** and their `ts` are at
   most 3 minutes apart (so a wearable taken off for hours doesn't pair old and new
   readings). One message can raise several alerts (e.g. `hr_high` and `spo2_low`).
10. `event_ts` = the newest entry's `ts`; `limit` = the threshold crossed; `value` and
    `previous_value` from the two entries. Send with A1's `send_alert`; `telemetry`
    raises if the send fails (Lambda's async retry runs it again).
11. `telemetry` env gains `REGISTRY_TABLE` and `ALERTS_QUEUE_URL`. Its log line gains
    `alert_ids` (a list, empty when nothing was raised). No values in logs.
12. `device-health` is unchanged and still reads no registry and sends nothing.

### Device `vitals_alert` in `fall-alert`

13. `docs/metrics.md`: `vitals_alert` moves from "Planned" into the v1 contract
    (`schema_version` stays `1`; adding a message type is an addition). Fields:

    | Field | Type | Valid range | Description |
    |---|---|---|---|
    | `metric` | string | `hr_bpm` or `spo2_pct` | |
    | `value` | integer | `hr_bpm` 20–250, `spo2_pct` 50–100 | the reading that triggered it |
    | `limit` | integer | same as `value` | the fixed device limit crossed (45/130 bpm, 88 %) |
    | `direction` | string | `above` or `below`; `spo2_pct` only `below` | |
    | `held_s` | integer | 10–600 | seconds the reading stayed past the limit |

14. The wearable device policy allows publishing to `silvercare/v1/<own id>/vitals_alert`
    (`infra/modules/iot-devices/main.tf` wearable `messages`). In-place policy update.
15. The `silvercare_alert` rule's SQL covers both `fall` and `vitals_alert`
    (`topic(4) = 'fall' OR topic(4) = 'vitals_alert'`, reserved fields undefined). It
    keeps its history action and error action. In-place update, no replacement.
16. `fall-alert` validates `vitals_alert` (validation.py, wearable only), then in A1's
    order: registry lookup, send a `critical` alert, store the message in the device's
    latest 5 (`MSG#vitals_alert`, no `watch_type`, like `fall`). The function keeps
    its name `silvercare-fall-alert` (renaming would replace it).

### Repeat suppression in `alert-processor`

17. Before storing, for every alert type except `fall`: Query `silvercare-alerts`
    `pk = RESIDENT#<resident_id>`, `sk BETWEEN "ALERT#<event_ts − 30 min>" AND
    "ALERT#<event_ts>"` (pattern 7, now run by `alert-processor` instead of the
    detectors). If an item with the same `alert_type`, the same `severity`, and a
    different `alert_id` exists, the alert is **suppressed**: not stored, not
    archived, not emailed, logged with outcome `suppressed`, and the SQS message is
    deleted (not a failure). Times come from `event_ts`, not the clock, so a retry
    decides the same way.
18. For `resident_id = unassigned`, suppression also requires the same `device_id`.
19. A `warning` never suppresses a `critical` alert and the other way round (criterion
    17 compares `severity`). Falls are never suppressed.
20. Two copies of the same alert processed at the same moment may both pass the check;
    the conditional PutItem (A1) still stores it once. Two *different* alerts racing
    may both be emailed: accepted.

### Email

21. Subject: `SilverCare alert: <label> - <resident_id>` for `warning`,
    `SilverCare CRITICAL alert: <label> - <resident_id>` for `critical`. Falls become
    `SilverCare CRITICAL alert: Fall - res-0003`. At most 100 characters.
22. The body gains `Severity: Warning` or `Severity: Critical` after `Type:`. Vitals
    alerts replace the fall lines with one reading line. SGT only, as in A1:

    ```
    SilverCare alert

    Type: High heart rate
    Severity: Warning
    Resident: res-0002
    When: 2026-10-18 11:14:00 SGT
    Reading: heart rate 128 bpm, above the limit of 120 (previous reading 125 bpm)

    Open the SilverCare portal: https://<cloudfront domain>/

    This is an automated message. Do not reply.
    ```

    Critical: `Reading: heart rate 140 bpm for 10 s, above the device limit of 130`.
    Units: `bpm`, `%` (SpO2), `°C` (skin temperature, one decimal). Fall emails keep
    their fall and latest-vitals lines.

### IAM and Terraform

23. `telemetry` role adds: `dynamodb:GetItem` on `silvercare-registry` with
    `LeadingKeys` `DEVICE#*` and `THRESHOLDS#*` (it can never read `RESIDENT#`
    profiles), and `sqs:SendMessage` on `silvercare-alerts`. `device-health` keeps
    exactly today's policy (needs its own policy document if it shares one now).
24. `alert-processor` role adds `dynamodb:Query` on the `silvercare-alerts` table
    (not the index), `LeadingKeys` `RESIDENT#*`.
25. No new resources except as needed above; no capacity change (registry reads are
    cached; suppression queries are one per vitals alert).

### Seed script

26. `scripts/seed-registry.py` also creates `THRESHOLDS#res-0002` / `THRESHOLDS` with
    `spo2_pct {"min": 88}` (a synthetic resident with a lower normal SpO2, to show
    personal thresholds), `updated_at`, and no `updated_by`. Same conditional put and
    `--dry-run` rules as A1.

### Live check support

27. `scripts/iot-smoke-test.py` gets `--alerts` (wearables only). After the existing
    checks it publishes, as the device: `vitals` with `hr_bpm` 125 (warning threshold
    crossed, not yet 2), `vitals` with `hr_bpm` 128 (→ `hr_high` warning),
    `vitals_alert` `hr_bpm` 140 above 130 held 10 s (→ critical), then `vitals` with
    `hr_bpm` 126 (→ suppressed). All other fields normal and synthetic. It prints the
    message IDs and what to expect. Needs no AWS credentials.

### Docs

28. `docs/data-model.md`: alert types and `severity`; `THRESHOLDS` defaults and
    resolution; pattern 7 now run by `alert-processor` with the 30-minute window;
    `MSG#vitals_alert` items in `silvercare-latest`. `docs/metrics.md`: criterion 13
    and the cloud check summary. (Main session: decision record 0001 "Device safety
    net" marked built; README "Checking alerts" gains the `--alerts` run.)

### Tests

29. Unit tests cover: threshold resolution (item, partial item, invalid limit, no
    item, cache); strict boundaries (50, 120, 92, 30.0, 38.0 normal); 2-in-a-row in the
    same direction only; nulls breaking a streak; the 3-minute pairing limit;
    duplicate and too-old messages; several alerts from one message; unassigned
    devices; `vitals_alert` validation (each field, `spo2_pct` + `above` rejected,
    bedroom rejected) and mapping to alert types; suppression (same type and severity
    within 30 min suppressed; at exactly 30 min and beyond not; different severity
    not; falls never; unassigned per device); the email subject and body per
    severity and type; `device-health` still reads no registry and sends nothing; no
    values in logs.

## Data

- Reads: registry `DEVICE#` assignment (pattern 1) and `THRESHOLDS#` item (pattern 2),
  both cached; the device's `MSG#vitals` item (already read by the write rule); alerts
  Query (pattern 7).
- Writes: alerts as in A1; `MSG#vitals_alert` in `silvercare-latest`; seed
  `THRESHOLDS#res-0002`.
- Capacity: unchanged.

## IoT

- New v1 message `wearable` → `vitals_alert` (criterion 13), device policy update,
  `silvercare_alert` rule SQL update. `vitals` unchanged.

## API contract

None.

## Screens

None.

## Alerts and notifications

| Trigger | Severity | Who |
|---|---|---|
| 2 abnormal `vitals` in a row (per-resident thresholds) | warning | the resident's subscribers |
| device `vitals_alert` | critical | the resident's subscribers |
| repeat within 30 min (same type and severity) | — | nobody (suppressed) |

Nurses are still not notified (A3). Unassigned devices: stored, nobody emailed.

## Privacy

- Personal data touched: heart rate, SpO2, skin temperature, linked to an opaque
  resident ID; per-resident thresholds (health-related settings).
- Email: the reading that triggered the alert and its limit, under the decision
  accepted in A1 (health data in email, synthetic data only, revisit before real data).
- Retention: as A1. Suppressed alerts are not kept anywhere except the log line (IDs
  and outcome).
- Must not be logged: values, limits, thresholds, `details`. Logs hold IDs, alert
  types, severities, outcomes, and reason codes.

## Cost

About US$0: a few more Lambda invocations and SQS messages per alert, cached registry
reads, one DynamoDB Query per vitals alert. No new resources with fees.

## Out of scope

- Escalation to nurses, cancel, acknowledge, resolve (A3).
- Offline devices, low battery, possible falls (A4).
- Editing thresholds in the portal (Track B).
- Simulator scenarios for abnormal vitals (Track C; the smoke test covers the live
  check).
- Alerts on `activity_pct`.

## Open questions

- **`telemetry` failures:** vitals alerts raised in `telemetry` have no dead-letter
  queue. Today a `telemetry` invocation that fails twice is dropped (decided
  2026-10-25, before alerts existed). Add an on-failure destination
  `silvercare-telemetry-dlq` (14 days, alarm to the ops topic) like `fall-alert`? It
  would also hold failed raw `vitals`, `motion`, and `bed` messages for 14 days.
