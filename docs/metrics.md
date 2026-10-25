# IoT metrics contract

Status: **approved** 2026-10-25. The offline timeout below stays *proposed* until the
alerting rules are decided.

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
acceleration is not streamed.

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
- Unknown fields are ignored. A missing required field, a wrong type, or an
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
| wearable | `fall` | event | when a fall is detected |
| wearable | `status` | heartbeat | every 5 min, and immediately when `worn` or `charging` changes |
| bedroom | `motion` | event | when motion state changes |
| bedroom | `bed` | event | when bed occupancy changes |
| bedroom | `status` | heartbeat | every 5 min |

A device is considered offline after 15 minutes with no `status` message (three
missed heartbeats). *(Proposed; confirm with the alerting rules.)*

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
- **Possible fall:** bedroom `bed` → `occupied: false`, then `motion: false` and no
  `occupied: true` within N minutes, at night. Stronger when the wearable is not worn.
- **Abnormal vitals:** `vitals` compared against **per-resident** thresholds (each
  resident has their own limits for heart rate, SpO2, and skin temperature).
- **Critical vitals (device safety net):** a wearable `vitals_alert` event, sent at
  once without waiting for the 60 s `vitals` message (below).

### Planned: `wearable` → `vitals_alert`

Decided 2026-10-25, built with the alerting feature: **not** in the v1 contract yet.
Devices don't send it, the device policy doesn't allow its topic, and no rule routes
it until then.

An instant safety net alongside the cloud's per-resident check, for emergencies where
waiting up to 60 s for the next `vitals` message is too slow. The limits are fixed and
the same for every resident, because a device doesn't know who wears it:

| Metric | Sends when |
|---|---|
| heart rate | below 45 or above 130 bpm |
| SpO2 | below 88 % |

- A reading must stay past the limit for **10 seconds** before the device sends, to
  filter motion glitches. Skin temperature is not included (wrist temperature is
  unreliable).
- Sent once when the condition starts, then again every 5 minutes while it lasts.
- Payload fields to be finalised in the alerting spec; expected: `metric`
  (`hr_bpm` or `spo2_pct`), `value`, `limit`, `direction` (`below` or `above`),
  `held_s`, plus the envelope.
- The limits must be checked with a clinician before any real use.
- **Device problems:** device offline, low battery, wearable not worn for a long time.

## Volume

| Device | Messages / day / device | × 20 |
|---|---|---|
| wearable | ~1,440 vitals + ~300 status + rare falls ≈ 1,750 | ~35,000 |
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

- SOS / help button on the wearable.
- Commands or configuration pushed to devices.
- Raw accelerometer streaming and cloud-side fall detection.
- GPS or in-home room-level location (location is the home the device is registered to).
