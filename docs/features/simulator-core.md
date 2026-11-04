---
status: built          # draft | ready | built
---

# Simulator core

## Summary

A Python program in `simulator/` that plays all 40 devices in `infra/devices/devices.json`
(20 wearables, 20 bedroom sensors) and publishes **normal** data to AWS IoT Core over
MQTT, each device with its own X.509 certificate, exactly as `docs/metrics.md` defines.
Each of the 20 synthetic residents in `scripts/seed-data.json` has their own baseline
vitals and follows a daily routine (asleep in bed, awake in the bedroom, out of the
bedroom). A dry-run mode prints the messages instead of publishing, with no keys and no
network. This is the first of three features: on-demand scenarios (falls, spikes,
offline, …) are `simulator-scenarios`, and the burst test is `load-test`.

## Users and access

No Cognito group uses this feature. There is no API and no screen.

| Group | Can see | Can do |
|---|---|---|
| staff | nothing (new) | nothing (new) |
| caregiver | nothing (new) | nothing (new) |
| family | nothing (new) | nothing (new) |

How access to a specific resident is decided: not applicable. Devices know nothing about
residents (`docs/metrics.md`); the simulator only uses the seed file to pick each
device's behaviour.

Who does what:

| Who | Does |
|---|---|
| Developer (dev container) | Runs `make simulate ENDPOINT=<host>` (live) and `make simulate-dry-run` |
| Devices (simulated) | Connect and publish, authorised only by their certificate and IoT policy |
| Claude and its agents | Build and test the simulator; may run the dry-run. Never run a live run or read `secrets/` (blocked by `.claude/hooks/guard.sh`) |

## Acceptance criteria

### Layout and running

1. Code in `simulator/src/` (entry point `simulator/src/main.py`, run as
   `python simulator/src/main.py`), tests in `simulator/tests/`. Standard library plus
   `paho-mqtt` (already in `requirements-dev.txt`); runs in the dev container
   (Python 3.13). No AWS SDK, no AWS credentials.
2. `make simulate ENDPOINT=<iot endpoint> [ARGS=...]` runs a live run in the dev
   container with keys from `secrets/device-keys/<device_id>.crt` / `.key` and CA
   `devices/AmazonRootCA1.pem` (the same files as `make smoke-test`).
   `make simulate-dry-run [ARGS=...]` runs `--dry-run`. `make test` and CI run
   `simulator/tests/` (`docker/run-lambda-tests.sh`, `make test FN=simulator`).
3. Flags: `--endpoint HOST` (required unless `--dry-run`; must end in
   `.iot.ap-southeast-1.amazonaws.com`, so data stays in Singapore), `--keys-dir DIR`
   (required unless `--dry-run`, no default, so a live run always names the keys folder
   on the command line), `--dry-run`, `--instant`
   (dry-run only: generate the whole `--duration` at once, without waiting),
   `--duration` (e.g. `30m`, `2h`, `24h`; default: until Ctrl+C; required with
   `--instant`), `--start-at HH:MM` (routine time of day at start, Singapore time;
   default: the real Singapore time), `--devices id,id,...` (default: all 40),
   `--random-seed N` (default random; the same number and `--start-at` give the same
   routine offsets and values), `--ca-file` (default `devices/AmazonRootCA1.pem`),
   `--seed-file`
   (default `scripts/seed-data.json`), `--devices-file`
   (default `infra/devices/devices.json`). Bad or conflicting flags exit 2 before any
   connection.

### Residents and baselines

4. `scripts/seed-data.json` gains a `baseline` object per resident:
   `{"hr_bpm": <int>, "spo2_pct": <int>, "skin_temp_c": <number, 1 decimal>}`.
   Exceptions: `res-0004` `spo2_pct` 91, `res-0007` `hr_bpm` 105, `res-0011` `hr_bpm`
   50. Every other resident: `hr_bpm` 62–82, `spo2_pct` 95–98, `skin_temp_c` 32.8–34.2,
   varied between residents. `scripts/seed.py` ignores `baseline` (it is not written to
   the registry) and its existing validation still passes.
5. The simulator validates the seed file before connecting: every resident has a
   `baseline`; each baseline value lies inside that resident's `thresholds` with a
   margin (heart rate at least 8 bpm inside min and max, SpO2 at least 3 above min,
   skin temperature at least 1.0 °C inside min and max); every device in
   `infra/devices/devices.json` is assigned to a resident in the seed file. Otherwise
   exit 2 naming IDs only.
6. Each resident's devices are the wearable and bedroom sensor assigned to them in
   `scripts/seed-data.json` (`devices`). The simulator never reads DynamoDB.

### Daily routine (Singapore time, UTC+8)

7. Each resident follows the shared routine, with their own fixed offset drawn once per
   run, uniformly in −30…+30 minutes, applied to every boundary:

   | Routine time | Phase | Bedroom sensor | Wearable |
   |---|---|---|---|
   | 22:00–07:00 | `asleep` | bed occupied, no motion | worn |
   | 07:00–08:00 | `bedroom` | bed empty, motion | worn |
   | 08:00–19:00 | `out` | bed empty, no motion | worn |
   | 19:00–20:00 | `charging` | bed empty, no motion | off the wrist, on the charger |
   | 20:00–21:00 | `out` | bed empty, no motion | worn |
   | 21:00–22:00 | `bedroom` | bed empty, motion | worn |

8. The routine clock is `--start-at` (or the real Singapore time) plus the real time
   elapsed. Message `ts` is always the **real** UTC time of the reading
   (`YYYY-MM-DDTHH:MM:SS.mmmZ`), never the routine clock, so the backend never flags
   `clock_skew`.

### Messages (all fields, types, and ranges exactly as `docs/metrics.md`)

9. Every payload has `schema_version` 1, a new UUID v4 `message_id`, and `ts`; never
   `device_id`, `message`, or `received_at`. Topic `silvercare/v1/<device_id>/<message>`,
   QoS 1, retain false. Each payload is under 8 KB.
10. **Wearable `vitals`:** every 60 s while worn (none while charging), at the device's
    fixed second within the minute (see 15). Values from the resident's baseline:
    - `hr_bpm`: baseline − 8 asleep, + 0 in the bedroom, + 10 out, plus noise ±4.
    - `spo2_pct`: baseline ± 1, at most 100.
    - `skin_temp_c`: baseline ± 0.4 (+0.3 asleep), one decimal.
    - `activity_pct`: asleep 0–5, bedroom 5–30, out 20–70.
    - `accel_g`: one sample, magnitude 0.9–1.1 g (gravity plus noise), each axis in
      −16…16.
    - Every value stays inside both the contract's valid range and the resident's
      thresholds (clamped to the threshold margin in 5), so normal data never looks
      abnormal.
    - About 2 % of readings have `hr_bpm` null, 2 % `skin_temp_c` null, and `spo2_pct`
      null 2 % of the time (5 % while `out`).
11. **Wearable `status`:** every 5 minutes, and immediately when `worn` or `charging`
    changes. `worn` is false and `charging` true during the `charging` phase, else
    `worn` true, `charging` false. `battery_pct` starts at a random 60–95, falls 1.5 %
    per hour while not charging, rises 40 % per hour while charging, capped at 100,
    published as an integer. `fw_version` `"1.0.0"`.
12. **Bedroom `bed`:** `{"occupied": true}` when the phase becomes `asleep`,
    `{"occupied": false}` when it ends. Only on change.
13. **Bedroom `motion`:** `{"motion": true}` when a `bedroom` phase starts;
    `{"motion": false}` 2 minutes after it ends (the PIR hold time). Only on change.
14. **Bedroom `status`:** every 5 minutes, `battery_pct` null (mains powered),
    `fw_version` `"1.0.0"`.
15. **Start-up and spreading:** each device publishes its `status` as soon as it is
    connected (or, in dry-run, at the same staggered start times, at most 10 devices per
    second), and each bedroom sensor its current `bed` and `motion` state once. After
    that, the 20 wearables get distinct, evenly spaced seconds within the minute for
    `vitals` (every 3 s), and all 40 devices distinct, evenly spaced offsets within the
    5 minutes for `status` (every 7.5 s), so steady-state publishes are spread out.

### Live run

16. Before connecting, the simulator checks that the `.crt` and `.key` of every selected
    device and the CA file exist; otherwise exit 2 listing device IDs only (never key
    contents or file contents).
17. One MQTT connection per device over TLS (port 8883, CA verification on), client ID =
    device ID. Connections are started at most 10 per second. A dropped connection
    reconnects with backoff starting at 1 s (never sooner: AWS IoT drops reconnects with
    the same client ID within 1 s), doubling up to 60 s. Messages due while a device is
    disconnected are dropped and counted, not queued.
18. Output: one line per minute, e.g.
    `12:03 SGT routine 21:58 | connected 40/40 | sent vitals 20 status 8 bed 0 motion 3 | dropped 0 | failed 0`.
    Connection problems print the device ID and the reason code only. Never a payload,
    certificate, or key path content.
19. Ctrl+C or the end of `--duration` disconnects every device cleanly, prints a final
    total, and exits 0. Exit 1 if no device could connect.

### Dry-run

20. `--dry-run` opens no network connection and reads no key files. It prints one JSON
    line per message: `{"topic": ..., "payload": {...}}`, in time order, then the same
    per-minute summary lines go to stderr. In real time by default; with `--instant` it
    generates the whole `--duration` immediately with `ts` values advancing as if it
    had run (starting from the real current time).

### Tests

21. Unit tests (no network, injected clock and random seed) cover:
    - every generated payload validates against the contract's fields, types, and ranges
      (one validator in the tests, written from `docs/metrics.md`);
    - topics, QoS 1, no forbidden fields, unique `message_id`s;
    - the routine table and per-resident offset (phase boundaries, `--start-at`);
    - over a simulated 24 h `--instant` dry-run for all 40 devices: ~1,440 − 60 vitals
      per wearable (none while charging), status every 5 min plus on each worn/charging
      change, exactly the expected `bed`/`motion` transitions, values inside thresholds,
      the null rates within tolerance, and, after the first minute, no second with more
      than 4 publishes;
    - `ts` is real time, never the routine clock;
    - baseline validation and the seed file's new `baseline` blocks; `scripts/seed.py`
      tests still pass;
    - live-run logic with a fake MQTT client: connect rate, reconnect backoff ≥ 1 s,
      dropped messages while disconnected, clean shutdown, missing key files exit 2, no
      payloads in output.

## Data

- Reads: `scripts/seed-data.json`, `infra/devices/devices.json`. No DynamoDB, no AWS
  API.
- Writes: MQTT messages only, which the existing IoT rules route to the ingest Lambdas,
  `silvercare-latest`, and the history bucket (access pattern 3; no new patterns).
- `scripts/seed-data.json` gains `baseline` per resident (not written to the registry).

## IoT

Topics `silvercare/v1/<device_id>/{vitals,status,fall,motion,bed}` as in
`docs/metrics.md`; this feature sends `vitals`, `status` (both types), `bed`, and
`motion` only. No contract change, no device policy change, no Terraform change.

## API contract

None.

## Screens

None.

## Alerts and notifications

None intended. Normal data stays inside every resident's thresholds and sends no `fall`,
so no alert should be raised. Values are clamped to each resident's thresholds narrowed
by the margins in criterion 5 (e.g. `res-0011`'s heart rate never goes below 48), so
normal data also stays clear of the device `vitals_alert` limits.

**Precondition for a live run:** the cloud checks `vitals` against each resident's
`THRESHOLDS` item in the registry, not against `scripts/seed-data.json`, and uses its
defaults (heart rate 50–120, SpO2 92) for a resident without one or an unassigned
device. So run `scripts/seed.py` against the live registry first (it writes all 20
residents' thresholds and assigns all 40 devices), then wait 5 minutes for the
`telemetry` caches to expire. Without it (only `scripts/seed-registry.py` run, or after
`seed.py --delete`), `wearable-0004` (SpO2 about 91) and `wearable-0011` (heart rate 48
asleep) raise warnings that are escalated to the nurses by email.

## Privacy

- Personal data touched: none. All residents and readings are synthetic, generated from
  the seed file.
- Device private keys: read only in a live run, run by the developer; never printed,
  logged, or copied. Claude is blocked from live runs by the safety hook.
- Output never contains payloads in a live run (counts and IDs only). Dry-run prints
  synthetic payloads to the terminal only.
- Retention: the messages are stored like any device data (latest 5 per device and type
  in DynamoDB; history in S3 until Open decision #4).

## Cost

- Demo runs only: about US$0.
- Left running 24/7: about 1.3 M messages a month (`docs/metrics.md`, Volume). Roughly
  US$1.30 IoT Core messaging, US$0.15 connection minutes, and a few tens of cents of
  Lambda, SQS, and CloudWatch Logs above their free tiers: about US$2 a month. DynamoDB
  stays within the provisioned capacity already sized for this load.

## Out of scope

- Scenarios: falls, night fall with the wearable off, heart-rate spike, SpO2 drop, low
  battery, device offline (`simulator-scenarios`).
- Load and burst tests (`load-test`).
- `vitals_alert` (not in the v1 contract yet).
- OTA firmware updates; anything the devices receive.
- Night-time bathroom trips or naps.

## Open questions

- None.
