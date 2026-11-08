---
status: ready          # draft | ready | built
---

# Simulator alert scenarios

## Summary

On-demand scenarios inside the running simulator (`simulator/`, docs/features/simulator-core.md):
a chosen wearable has a fall, a heart rate spike, no pulse, or an SpO2 drop, or its
resident presses "I'm OK". The simulated device sends exactly what a real wearable
would (`fall`, abnormal `vitals`, `vitals_alert`, `alert_cancel`, as docs/metrics.md
defines them), so the alert features of Track A (alerts-core, vitals-alerts,
alert-cancel, escalation) can be demoed and tested end to end with the other 39 devices
still running normally.

Scenarios are started with `--scenario` flags (scripted, e.g. for a demo) or by typing
a command into the running simulator. Device-problem scenarios (night fall with the
wearable off, low battery, device offline) are a later feature.

Decisions (user, 2026-11-04, accepting the proposed defaults): the 5 scenarios below;
heart rate 150 bpm and SpO2 84 % for 3 minutes; both flags and typed commands; after a
fall the resident stays down until `im-ok` or 10 minutes pass.

Existing work this does not repeat: `make smoke-test … ALERTS=1 / CANCEL=1`
(`scripts/iot-smoke-test.py`, Track A) is a one-off check from one connection. Both
vitals-alerts and alert-cancel list simulator scenarios as Track C's. The scenario
values and spacing follow the lessons recorded there (values past any personal limit,
messages whose order matters are spaced, cancels at least 5 s after the event).

## Users and access

No Cognito group uses this feature. It is a developer tool with no API and no screen.

| Group | Can see | Can do |
|---|---|---|
| staff | nothing new (alerts it causes appear as any alert) | nothing new |
| caregiver | nothing new (alerts it causes appear as any alert) | nothing new |
| family | nothing new | nothing new |

How access to a specific resident is decided: not changed.

| Who | Does |
|---|---|
| Developer | Runs `make simulate … ARGS="--scenario …"` against the live account, or types commands into it; runs `make simulate-dry-run` with scenarios |
| Claude and its agents | Build and test it in dry-run only (the guard hook blocks live runs) |

## Acceptance criteria

### Starting scenarios

1. **Names:** `fall`, `hr-spike`, `no-pulse`, `spo2-drop`, `im-ok`. Each targets one
   wearable (`wearable-NNNN`) that is part of the run (`--devices`, default all). A
   bedroom ID, an unknown device, or a device not in the run is an error.
2. **Flag:** `--scenario <name>:<wearable_id>[@<offset>]`, repeatable. `<offset>` is
   elapsed time since the start in the `--duration` format (`90s`, `2m`, `1h`); default
   `30s` (all devices are connected by then). A malformed value, an unknown name or
   device, or an offset at or past `--duration` exits 2 before anything connects, with a
   message naming the flag value's position and the problem (no file contents).
3. **Typed commands:** while a run is going (live, or dry-run without `--instant`) and
   stdin is a terminal, a line `<name> <wearable_id>` starts that scenario at once; `help`
   lists the names. A bad line prints one error line to stderr and the run continues.
   With `--instant`, or when stdin is not a terminal, typed commands are off (no stdin
   is read). Commands are read on a background thread and handed to the run loop, which
   alone touches the engine.
4. **When a scenario starts,** one line goes to the summary stream:
   `scenario <name> <wearable_id> started` and, when it ends, `scenario <name>
   <wearable_id> ended`. A scenario that can't start prints `scenario <name>
   <wearable_id> skipped: <reason>` and the run continues. Reasons:
   - `not worn` (the wearable is in its `charging` phase; all but `im-ok`)
   - `busy` (another scenario other than `im-ok` is active on that wearable)
   - `too soon` (`im-ok` within 5 s of when the previous `im-ok`'s `alert_cancel` is
     sent, or will be if it was deferred; this keeps cancels at least 5 s apart)

   A scenario ending at time `t` is over for everything else due at `t`, so another
   one can start on that wearable at `t`. When the run stops (`--duration` or Ctrl+C),
   each scenario still going, and each `im-ok` whose `alert_cancel` wasn't sent yet,
   prints `scenario <name> <wearable_id> cut off: run ended`.

### What each scenario sends

All messages follow docs/metrics.md exactly (topics, fields, ranges, `ts` format) and
pass the same contract validator as normal data. Times below are from the scenario's
start `t0`. Readings for the affected metric bypass the resident's normal clamp; every
other field stays normal for the resident and phase.

Since `device-alert-limits` (criteria 22-23), a `vitals_alert` is no longer scheduled
by the scenario: the wearable sends it when its reading stays past its current device
limit for 10 s. The scenario values cross any valid limit, so the messages and times
below are unchanged; `limit` is the device's limit (45 / 130 / 88 for the seed data).

5. **`hr-spike`** (3 minutes):
   - every `vitals` due in `[t0, t0 + 3 min)` has `hr_bpm` 150–155 (never `null`);
   - one `vitals_alert` at `t0 + 10 s`: `metric` `hr_bpm`, `value` = the current
     spike value, `limit` 130, `direction` `above`, `held_s` 10;
   - from `t0 + 3 min`, readings are normal again.
6. **`spo2-drop`** (3 minutes): the same, for SpO2: `spo2_pct` 82–84 (never `null`),
   one `vitals_alert` at `t0 + 10 s` with `metric` `spo2_pct`, `limit` 88, `direction`
   `below`.
7. **`no-pulse`** (3 minutes):
   - every `vitals` due in `[t0, t0 + 3 min)` has `hr_bpm` `null`, `spo2_pct` `null`,
     and `activity_pct` 0 (`vitals` accepts heart rate 20–250 only, so "no pulse" is
     `null` there);
   - one `vitals_alert` at `t0 + 10 s`: `metric` `hr_bpm`, `value` 0, `limit` 45,
     `direction` `below`, `held_s` 10.
8. A `vitals_alert` repeats every 5 minutes while its condition lasts (docs/metrics.md);
   with 3-minute scenarios there is exactly one per scenario. A `vitals_alert` is sent
   only while the wearable is worn.
9. **`fall`:**
   - `t0` is the impact. One `fall` message is sent at `t0 + 20 s` with `ts` = `t0`,
     `confidence` 0.85–0.95, `peak_g` 3.0–4.5, `still_s` 20, and `accel_window` with
     `hz` 25, `start_offset_ms` -1000, and 50 samples `[x, y, z]`: about 1 g before the
     impact, one sample at index 25 with magnitude `peak_g`, then about 1 g in a lying
     orientation (mostly on `x`). Every axis within -16..16, rounded to 3 decimals.
   - Until the resident gets up: every `vitals` has `activity_pct` 0–3 and `accel_g` in
     the lying orientation; heart rate, SpO2, and temperature stay normal.
   - The resident gets up at `t0 + 10 min`, or when `im-ok` is sent for that wearable,
     whichever is first; then the scenario ends.
10. **`im-ok`:** sends one `alert_cancel` (envelope only: `schema_version`,
    `message_id`, `ts`, nothing else) at once. It also ends an active `fall` on that
    wearable. If that wearable's `fall` message hasn't been sent yet or was sent less
    than 5 s ago, the `alert_cancel` is sent 5 s after the `fall` message instead, and
    the started line says `(after the fall message)`. Works whether or not the wearable
    is worn.
11. At most one scenario other than `im-ok` per wearable at a time. Scenarios on
    different wearables run independently.
12. Scenario values are deterministic for the same `--random-seed`, start time, and
    scenarios.

### Output and run

13. The per-minute summary and the final total count the new message types:
    `sent vitals 20 status 8 bed 0 motion 3 fall 0 vitals_alert 0 alert_cancel 0 | …`.
14. Messages of scenarios follow the normal rules: published on the device's own
    connection (QoS 1), dropped and counted if it is disconnected (not queued). Nothing
    new is printed about values: output names scenario, device ID, and reason only.
15. `--dry-run` with `--scenario` prints the scenario messages in time order with the
    normal ones, so a whole demo can be checked without AWS.
16. Without `--scenario` and typed commands, output is identical to simulator-core's
    (same seed, same messages).

### Tests

17. Tests cover, per scenario: the messages, values, and timing above; that every
    message passes the contract validator (extended to `fall`, `vitals_alert`,
    `alert_cancel`); the skip reasons; flag errors (exit 2); typed commands from a fake
    command source (no real stdin); and that a run without scenarios is unchanged.
    No network, no keys.

## Data

- Reads: `scripts/seed-data.json` and `infra/devices/devices.json`, as simulator-core.
- Writes: none directly. Live runs cause the normal ingest and alert writes
  (`silvercare-latest`, `silvercare-alerts`, S3 history and `alerts/`).
- New access patterns or tables: none.

**Precondition for a live run** (as simulator-core): `scripts/seed.py` has been run
against the live registry, so every wearable is assigned to its resident. Without it, an
unassigned wearable's alerts are stored under `RESIDENT#unassigned` and go to the nurses
only, and the normal readings of `wearable-0004` and `wearable-0011` raise warnings of
their own.

## IoT

Wearable messages `fall`, `vitals_alert`, `alert_cancel` (and `vitals`), as
docs/metrics.md defines them. The wearable device policy already allows all of them. No
contract, rule, policy, or Terraform change.

## API contract

None.

## Screens

None.

## Alerts and notifications

This feature exists to raise them. Expected in the live system, for the wearable's
resident:

| Scenario | Alert(s) | Severity |
|---|---|---|
| `fall` | `fall` | critical |
| `hr-spike` | `hr_high` from `vitals_alert` at about `t0 + 10 s`; `hr_high` warning after the 2nd abnormal `vitals` (1–2 minutes) | critical, warning |
| `spo2-drop` | `spo2_low` the same way | critical, warning |
| `no-pulse` | `hr_low` (no pulse) from `vitals_alert` | critical |
| `im-ok` | cancels the wearable's open alerts; "cancelled" follow-up emails | — |

- 150 bpm is above every seeded personal limit (highest: 140) and the device limit
  (130); 84 % is below every seeded SpO2 limit (lowest: 85) and the device limit (88).
- Repeat suppression: the same alert type and severity for the same resident is
  emailed at most once per 30 minutes, except falls, which are never held back. A
  critical and a warning `hr_high` (or `spo2_low`) are different severities, so an
  `hr-spike` or `spo2-drop` emails twice.
- Emails are real: they go to the caregivers subscribed to that resident, and every
  alert not cancelled in time (`im-ok`) also goes to all the nurse subscribers
  (escalation, per Track A's routing). One scenario can mean 2–4 emails per inbox, and
  nothing limits how many scenarios a run starts, so keep live demos to a few.

## Privacy

- Personal data touched: none. Residents, devices, and readings are synthetic.
- Retention: alerts and history caused by scenarios follow the normal retention.
- Must not be logged or shown: payloads in live runs, key or certificate contents, file
  paths. Output names scenario names, device IDs, and reasons only.

## Out of scope

- Device-problem scenarios: night fall with the wearable off, low battery, device
  offline (later feature, after agreeing numbers with Track A's watchdog).
- Bedroom-sensor scenarios, skin temperature scenarios, random or automatic scenarios.
- Changes to the smoke test, the contract, the IoT rules, or alert handling.
- The load test and OTA updates.

## Open questions

None.
