# IoT metrics contract

Status: **approved** 2026-10-25. The offline timeout below was decided with the
watchdog feature (2026-11-03).

The contract between devices and the backend. Devices, the simulator, the IoT topic
rules, and the ingest Lambdas are all built from this file. Change it first.

There is no real hardware. All devices are simulated by a local Python script (see
[Simulator](#simulator)) that connects to AWS IoT Core over MQTT exactly as a real
device would.

## Fleet

| Item | Count |
|---|---|
| Homes (one resident each) | 20 |
| Wearables (one per resident) | 20 |
| Bedroom sensors (one per home, PIR + bed pressure mat) | 20 |

Devices know nothing about residents or homes. A device registry in DynamoDB maps
`device_id` → home → resident (see `docs/data-model.md`). Moving a device to another
home is a registry change, not a device change.

## Device types

### Wearable (`wearable`)

Worn on the wrist. Measures heart rate, SpO2, skin temperature, and 3-axis acceleration.
Runs fall detection on the device and sends a `fall` event when it detects one; raw
acceleration is not streamed. Also sends a `vitals_alert` at once when heart rate or
SpO2 stays past a fixed critical limit for 10 seconds (the device safety net). Has an
"I'm OK" button: pressing it sends an `alert_cancel`, which cancels the wearable's open
alerts.

### Bedroom sensor (`bedroom`)

One unit per bedroom combining a PIR motion sensor and a pressure mat under the
mattress. Sends state changes only (motion detected/cleared, bed occupied/vacated). It
cannot detect a fall itself; the backend infers a *possible* fall from the pattern of
events (e.g. bed vacated at night, then no motion and no return to bed).

## Conventions (all messages)

### Device IDs

- Format: `<type>-<4 digits>`, e.g. `wearable-0007`, `bedroom-0012`.
- Opaque: the number has no relationship to a home or resident number.
- The device ID is the AWS IoT thing name and the MQTT client ID.

### Topics

```
silvercare/v1/{device_id}/{message}
```

| Segment | IoT SQL | Example |
|---|---|---|
| `{device_id}` | `topic(3)` | `wearable-0007` |
| `{message}` | `topic(4)` | `vitals` |

- The backend takes the device ID from the topic, never from the payload. The device
  policy only allows a device to publish under its own thing name, so the topic is
  trusted.
- `v1` is the topic version. It changes only if the topic layout itself changes.
- Devices publish only. They subscribe to nothing in v1 (no commands or config push).

### MQTT settings

| Setting | Value |
|---|---|
| QoS | 1 (at least once): duplicates will happen, the backend must de-duplicate |
| Retain | false |
| Auth | One X.509 certificate per device |
| Max payload | 8 KB (IoT Core allows 128 KB; billing is per 5 KB) |

### Envelope

Every payload is a JSON object with these fields, plus the message-specific fields
below.

| Field | Type | Description |
|---|---|---|
| `schema_version` | integer | Payload schema version. Currently `1`. |
| `message_id` | string | UUID v4, unique per message. Used to de-duplicate. |
| `ts` | string | When the reading was taken or the event happened, device clock, ISO 8601 UTC with `Z` and millisecond precision, e.g. `2026-10-18T03:12:45.120Z`. |

- The backend also records `received_at` (IoT rule `timestamp()`), because device clocks
  can be wrong. If `ts` is more than 5 minutes off `received_at`, the message is still
  stored, and flagged `clock_skew: true`.
- Payloads never contain names, NRIC numbers, addresses, or resident IDs.
- Payloads must not contain `device_id`, `message`, or `received_at`. The IoT rules add
  these (`device_id` and `message` from the topic, `received_at` from `timestamp()`), and
  a message that already has any of them is dropped by the rule, so a device can't
  claim to be another device.
- Unknown fields are ignored, except in `alert_cancel`, which is envelope only and is
  rejected if it has any other field. A missing required field, a wrong type, or an
  out-of-range value rejects the message: the ingest Lambda logs `device_id`,
  `message_id`, and the reason, and drops it. The raw message is still kept in S3
  history (see `docs/decisions/0001-iot-rule-routing.md`).

### Versioning

- Adding an optional field: no version change.
- Removing, renaming, or retyping a field, or changing units: bump `schema_version`. The
  backend keeps accepting the previous version until all devices are updated.
- An unknown `schema_version` is rejected.

## Messages

### Summary

| Device | Message | Kind | Rate |
|---|---|---|---|
| wearable | `vitals` | reading | every 60 s while worn |
| wearable | `vitals_alert` | event | when heart rate or SpO2 stays past a fixed critical limit for 10 s, then every 5 min while it lasts (only while worn) |
| wearable | `fall` | event | when a fall is detected |
| wearable | `alert_cancel` | event | once per "I'm OK" press (rare; presses at least 5 s apart) |
| wearable | `status` | heartbeat | every 5 min, and immediately when `worn` or `charging` changes |
| bedroom | `motion` | event | when motion state changes |
| bedroom | `bed` | event | when bed occupancy changes |
| bedroom | `status` | heartbeat | every 5 min |

A device is considered offline after 15 minutes with no `status` message (three
missed heartbeats). (Decided 2026-11-03, docs/features/watchdog.md.)

Each message type is routed by one of three IoT rules (`health`, `telemetry`, `alert`);
see `docs/decisions/0001-iot-rule-routing.md`.

### `wearable` → `vitals`

One message per minute while the wearable is worn. Not sent while it is off the wrist;
`status` reports that instead.

| Field | Type | Unit | Valid range | Description |
|---|---|---|---|---|
| `hr_bpm` | integer or null | beats/min | 20–250 | Heart rate, averaged over the last 60 s. `null` if no reading. |
| `spo2_pct` | integer or null | % | 50–100 | Blood oxygen saturation. `null` if no reading (common during movement). |
| `skin_temp_c` | number or null | °C, 1 decimal | 20.0–45.0 | Wrist skin temperature. Normally ~32–35 °C, lower than core body temperature. |
| `activity_pct` | integer | % | 0–100 | Share of the last 60 s with movement above 0.1 g. 0 = still, 100 = moving the whole minute. |
| `accel_g` | object | g | each axis −16 to 16 | One acceleration sample at `ts`: `{ "x": number, "y": number, "z": number }`. |

Valid range means "physically possible", not "normal". Values outside it are sensor
errors and rejected; values inside it but abnormal are what alerts check.

```json
{
  "schema_version": 1,
  "message_id": "5f0c9a52-3c1e-4c8e-9f57-0d6f2b7a1e11",
  "ts": "2026-10-18T03:12:00.000Z",
  "hr_bpm": 72,
  "spo2_pct": 97,
  "skin_temp_c": 33.4,
  "activity_pct": 12,
  "accel_g": { "x": 0.02, "y": -0.11, "z": 0.99 }
}
```

### `wearable` → `vitals_alert`

Added to v1 on 2026-11-02 (docs/features/vitals-alerts.md); `schema_version` stays `1`,
because adding a message type is an addition. An instant safety net alongside the
cloud's per-resident check of `vitals`, for emergencies where waiting up to 60 s for
the next `vitals` message is too slow. The limits are fixed and the same for every
resident, because a device doesn't know who wears it:

| Metric | Sends when |
|---|---|
| heart rate | below 45 or above 130 bpm, or no pulse (0 bpm) |
| SpO2 | below 88 % |

- A reading must stay past the limit for **10 seconds** before the device sends, to
  filter motion glitches. Skin temperature is not included (wrist temperature is
  unreliable).
- Sent once when the condition starts, then again every 5 minutes while it lasts.
- Sent only while the wearable is worn (`worn` true, skin contact). `hr_bpm` `0` means
  skin contact but no pulse found for 10 s (e.g. cardiac arrest). Off the wrist the
  wearable sends nothing (routine `vitals` use `null` for "no reading", which is never
  an alert, because a loose strap looks the same).
- The limits are synthetic and must be checked with a clinician before any real use.

| Field | Type | Unit | Valid range | Description |
|---|---|---|---|---|
| `metric` | string | — | `hr_bpm` or `spo2_pct` | Which reading crossed its limit. |
| `value` | integer | bpm or % | `hr_bpm` 0 or 20–250, `spo2_pct` 50–100 | The reading that triggered it. `hr_bpm` 0 = no pulse detected while worn. |
| `limit` | integer | bpm or % | `hr_bpm` 45 or 130, `spo2_pct` 88 | The fixed device limit crossed. |
| `direction` | string | — | `above` or `below`; `spo2_pct` only `below`; `value` 0 only `below` | Which side of the limit. |
| `held_s` | integer | s | 10–600 | Seconds the reading stayed past the limit. |

```json
{
  "schema_version": 1,
  "message_id": "3b9e2f1a-7c4d-4e8b-9a6f-1d2c3b4a5e60",
  "ts": "2026-10-18T03:15:10.000Z",
  "metric": "hr_bpm",
  "value": 140,
  "limit": 130,
  "direction": "above",
  "held_s": 10
}
```

No pulse:

```json
{
  "schema_version": 1,
  "message_id": "8c1d2e3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f",
  "ts": "2026-10-18T03:15:20.000Z",
  "metric": "hr_bpm",
  "value": 0,
  "limit": 45,
  "direction": "below",
  "held_s": 10
}
```

The `alert` IoT rule routes it, with `fall`, to `critical-alert`, which raises a
critical alert at once (`hr_bpm` + `above` → `hr_high`, `hr_bpm` + `below` → `hr_low`,
`spo2_pct` + `below` → `spo2_low`).

### `wearable` → `fall`

Sent once when on-device detection sees a fall: an impact above ~2.5 g, then a change
of orientation, then little movement. `ts` is the time of the impact.

| Field | Type | Unit | Valid range | Description |
|---|---|---|---|---|
| `confidence` | number | — | 0.0–1.0 | Detector confidence. |
| `peak_g` | number | g | 0–16 | Peak acceleration magnitude at impact. |
| `still_s` | integer | s | 0–600 | Seconds of near-stillness observed after impact before sending. |
| `accel_window` | object | — | — | Acceleration around the impact (below). |

`accel_window`:

| Field | Type | Description |
|---|---|---|
| `hz` | integer | Sample rate, `25`. |
| `start_offset_ms` | integer | Offset of the first sample from `ts`, `-1000` (window is 1 s before to 1 s after impact). |
| `samples` | array | 50 samples, each `[x, y, z]` in g. |

```json
{
  "schema_version": 1,
  "message_id": "c2a1d4f0-8b6e-4f0a-a3c9-7e15d2b9f804",
  "ts": "2026-10-18T03:14:07.360Z",
  "confidence": 0.91,
  "peak_g": 3.8,
  "still_s": 20,
  "accel_window": {
    "hz": 25,
    "start_offset_ms": -1000,
    "samples": [[0.01, -0.10, 0.99], [0.03, -0.12, 1.02], "… 48 more"]
  }
}
```

### `wearable` → `alert_cancel`

Added to v1 on 2026-11-03 (docs/features/alert-cancel.md); `schema_version` stays `1`,
because adding a message type is an addition. Sent once when the resident presses the
wearable's "I'm OK" button. The device sends at most one per press and accepts presses
at least 5 seconds apart, so the rate is rare. `ts` is the time of the press.

Envelope only: `schema_version`, `message_id`, `ts`, and no other fields. Unlike the
other messages, an `alert_cancel` with any other field is rejected (not ignored).
Bedroom sensors can't send it (their device policy doesn't allow the topic).

```json
{
  "schema_version": 1,
  "message_id": "6d2a4c8e-1f3b-4a5d-9e7c-2b4d6f8a0c13",
  "ts": "2026-11-03T02:00:05.000Z"
}
```

The `alert` IoT rule routes it, with `fall` and `vitals_alert`, to `critical-alert`,
which looks up the wearable's resident and sends a `status_update` to the alerts queue
(contract in `docs/data-model.md`). `alert-processor` then cancels **every** alert from
that wearable that is still `open` with `event_ts` up to the time the IoT rule
received the press (`received_at`, server time, not the device's `ts`) (falls,
warnings, and critical vitals alerts, including no pulse): they stop escalating, and
caregivers (plus nurses, if already escalated to) get a short "cancelled" follow-up
email. The message is also stored in the device's latest 5 (`MSG#alert_cancel`).

### `wearable` → `status`

| Field | Type | Unit | Valid range | Description |
|---|---|---|---|---|
| `battery_pct` | integer | % | 0–100 | Battery level. |
| `worn` | boolean | — | — | On the wrist (skin contact detected). |
| `charging` | boolean | — | — | On the charger. |
| `fw_version` | string | — | max 32 chars | Firmware version, e.g. `1.0.0`. |

```json
{
  "schema_version": 1,
  "message_id": "0b6f4e8e-1d2a-4a51-8a0e-9a3b5c7d2f60",
  "ts": "2026-10-18T03:15:00.000Z",
  "battery_pct": 64,
  "worn": true,
  "charging": false,
  "fw_version": "1.0.0"
}
```

### `bedroom` → `motion`

Sent when the PIR state changes. "Detected" is sent on the first motion; "cleared" is
sent after 2 minutes with no motion (the PIR hold time).

| Field | Type | Description |
|---|---|---|
| `motion` | boolean | `true` = motion detected, `false` = cleared. |

```json
{
  "schema_version": 1,
  "message_id": "9d3e2c1b-6a5f-4e7d-b8c9-1f2a3b4c5d6e",
  "ts": "2026-10-18T03:13:52.000Z",
  "motion": true
}
```

### `bedroom` → `bed`

Sent when bed occupancy changes and the new state has held for 10 seconds (so turning
over in bed does not send events).

| Field | Type | Description |
|---|---|---|
| `occupied` | boolean | `true` = someone in bed, `false` = bed empty. |

```json
{
  "schema_version": 1,
  "message_id": "4e5f6a7b-8c9d-4e0f-a1b2-c3d4e5f6a7b8",
  "ts": "2026-10-18T03:13:40.000Z",
  "occupied": false
}
```

### `bedroom` → `status`

| Field | Type | Unit | Valid range | Description |
|---|---|---|---|---|
| `battery_pct` | integer or null | % | 0–100 | Battery level, `null` if mains-powered. |
| `fw_version` | string | — | max 32 chars | Firmware version. |

```json
{
  "schema_version": 1,
  "message_id": "7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d",
  "ts": "2026-10-18T03:15:00.000Z",
  "battery_pct": 88,
  "fw_version": "1.0.0"
}
```

## How the backend uses these (for later specs)

Not part of the contract, recorded here so the data model leaves room for it. The
conditions and thresholds are decided under Open decision #2 (alerting rules). Which
component raises each one is in `docs/decisions/0001-iot-rule-routing.md`.

- **Confirmed fall:** a wearable `fall` event.
- **Possible fall (built in docs/features/possible-fall.md):** every 5 minutes the
  `watchdog` checks every assigned bedroom unit that is not offline: a **critical**
  `possible_fall` when its newest `bed` entry is `occupied: false`, vacated between
  22:00 and 06:59:59 SGT, at least 20 minutes ago, its newest `motion` entry is
  `motion: false` (or there is none), and the resident's wearable is not worn (no
  wearable, no `status` in the last 15 minutes, or a recent one with `worn: false`; a
  recent `worn: true` from any of the resident's wearables means no alert). Times are
  the entry's `ts`, or `received_at` when `clock_skew` is true. Emailed to caregivers
  and nurses at once; resolved automatically, with a follow-up email, when the newest
  `bed` entry is `occupied: true` again; the wearable's "I'm OK" also cancels it. A
  developer can put one unit in test mode for live checks (any time counts as night,
  2-minute wait, expires after 2 hours).
- **Abnormal vitals (cloud check, built in docs/features/vitals-alerts.md):**
  `telemetry` compares every wearable `vitals` message with the resident's thresholds
  (their `THRESHOLDS` item in the registry, per limit, else the defaults: heart rate
  below 50 or above 120 bpm, SpO2 below 92 %, skin temperature below 30.0 or above
  38.0 °C; a value equal to a limit is normal, `null` is never abnormal). For each
  metric it raises a **warning** when the device's newest two readings are both
  abnormal in the same direction and at most 3 minutes apart. Alert types: `hr_high`,
  `hr_low`, `spo2_low`, `skin_temp_high`, `skin_temp_low`.
- **Critical vitals (device safety net, built):** a wearable `vitals_alert` event (above),
  sent at once without waiting for the 60 s `vitals` message; a **critical** alert.
- **Repeats:** the same alert type and severity for the same resident is emailed at
  most once per 30 minutes (`alert-processor`); falls are never held back.
- **Cancel (built in docs/features/alert-cancel.md):** a wearable `alert_cancel`
  cancels that wearable's open alerts up to the time the press was received
  (`received_at`).
- **Device problems (built in docs/features/watchdog.md):** the `watchdog` Lambda checks
  every assigned device every 5 minutes against its newest `status`: **device offline**
  after more than 15 minutes with no `status` (three missed heartbeats); **low battery**
  when `battery_pct` is below 20 and `charging` is not true (`null`, mains-powered
  bedroom units, never); **wearable not worn** when `worn` has been false (and
  `charging` not true) for at least 2 hours, checked between 08:00 and 22:00 SGT. An
  offline device is not checked for battery or worn. All `warning`, emailed to the ops
  team and the resident's caregivers (nurses see them in the portal only), never
  escalated, and resolved without email when the condition clears.

## Volume

| Device | Messages / day / device | × 20 |
|---|---|---|
| wearable | ~1,440 vitals + ~300 status + rare falls, vitals alerts, and cancels ≈ 1,750 | ~35,000 |
| bedroom | ~290 status + ~70 motion/bed ≈ 360 | ~7,200 |
| **Total** | | **~42,000 / day ≈ 1.3 M / month** |

That is a few US dollars a month across IoT Core and DynamoDB if the simulator runs
24/7, and close to nothing when it is stopped.

## Simulator

A Python script in `simulator/` *(planned)* plays all 40 devices:

- 20 synthetic residents, each with their own baseline vitals (e.g. one with a lower
  normal SpO2, one with a higher resting heart rate) and a daily routine (sleep, awake
  in the bedroom, out of the bedroom).
- Scenarios triggered on demand: wearable fall, night-time fall with the wearable off,
  heart rate spike, SpO2 drop, low battery, device offline.
- A dry-run mode that prints payloads instead of publishing, for tests and for working
  without AWS access.
- Uses one X.509 certificate per device, so it exercises the real device policies.

All simulator data is synthetic. It follows this contract exactly; if it needs a field
the contract does not have, change the contract first.

## Out of scope for v1

- SOS / help button on the wearable. (The "I'm OK" button, which only cancels the
  wearable's open alerts with `alert_cancel`, is in v1.)
- Commands or configuration pushed to devices.
- Raw accelerometer streaming and cloud-side fall detection.
- GPS or in-home room-level location (location is the home the device is registered to).
