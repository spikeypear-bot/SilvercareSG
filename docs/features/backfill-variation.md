---
status: built          # draft | ready | built
---

# Backfill variation: unusual residents and vitals alerts in history

## Summary

Extends the simulator backfill (docs/features/simulator-backfill.md) so the generated
history has something to find. Four fixed residents become "unusual": wider noise, a
mild gradual decline, bad days, irregular sleep, readings that may cross their
thresholds, and wearable `vitals_alert`s from heart-rate spike and SpO2 drop episodes
that become more frequent over the range (and whenever their own readings go past the
wearable's limits). The other 16 residents stay as today. The live simulator doesn't
change.

Decisions (user, 2026-11-09): residents `res-0003`, `res-0008`, `res-0014`, `res-0019`;
all four kinds of variation; moderate bad days and sleep; scenario episodes
(`hr-spike` and `spo2-drop`, no `no-pulse`); readings may cross thresholds, and a
reading past the wearable's limit alerts like a real device; the 8-month backfill is
already loaded, so this spec includes how to replace it.

Revised after the first full run (user, 2026-11-09): the strong decline (+20 bpm /
−5 %) gave 100–400 alert episodes per unusual resident per month, too many. Natural
crossings jump from none to hundreds (per-minute noise on a whole bad day), so the
decline is now mild (+8 / −2, no natural crossings with the seed baselines) and the
alerts come from planned episodes ramping from about 1 to about 10 per resident per
month.

What exists (checked in code, 2026-11-09):

- A normal reading is baseline + phase delta (HR −8 asleep / 0 bedroom / +10 out) + uniform
  noise (HR ±4, SpO2 ±1, temp ±0.4 °C), clamped inside the resident's thresholds
  narrowed by margins (`simulator/src/payloads.py` `vitals_values`).
- The engine's `vitals_alert` comes only from scenario readings: `_reading` returns
  `None` for normal readings (`simulator/src/engine.py`), so a normal reading never
  alerts. A scenario's reading past a device limit starts an episode: a `vitals_alert`
  10 s later, repeated every 5 minutes while past, sent only while worn.
- Every seeded resident chosen here has thresholds equal to the fixed device limits
  (HR 45–130, SpO2 min 88).
- Summaries count `vitals_alert` episodes (a new episode after a 10-minute gap per
  metric) and readings outside thresholds (`hr_bpm_below/above` etc.) from the clean
  tables (docs/features/curated-layer.md criteria 13–14). Re-running `clean` or
  `summaries` for a date replaces that date's output (clean-data.md criterion 3,
  curated-layer.md criterion 16).

## Users and access

No Cognito group uses this feature. There is no API and no screen. Once loaded, the data
is read through the existing clean and curated access rules (docs/data-model.md).

| Group | Can see | Can do |
|---|---|---|
| staff | nothing (new) | nothing (new) |
| caregiver | nothing (new) | nothing (new) |

## Acceptance criteria

All numbers below apply only to the four unusual residents' wearables (`UNUSUAL`, a
constant in `simulator/src/backfill.py`) and only in the backfill. The live simulator
(`simulator/src/main.py`) and `--dry-run` output stay byte-identical to today. The
other 16 residents' `vitals` values stay as today (message IDs and `received_at`
delays may change: they come from streams shared by every line).

### Unusual readings

1. **Wider noise.** HR ±12 bpm, SpO2 ±3 %, skin temperature ±0.8 °C (uniform, as
   today's noise), instead of ±4 / ±1 / ±0.4.
2. **Mild decline.** The baseline moves linearly from 0 at `--from` 00:00 SGT to HR
   +8 bpm and SpO2 −2 % at `--to` 24:00 SGT (0 during the lead-in). Skin temperature
   doesn't drift.
3. **Bad days.** Each unusual resident's Singapore day inside [`--from`, `--to`] is a bad
   day with probability 1/10, drawn independently: HR +15 bpm and SpO2 −3 % for the
   whole day, on top of the decline.
4. **Irregular sleep.** For each unusual resident and night, the start of the asleep
   phase (22:00 + their offset) moves by `b` and the end (07:00 + offset) by `w`, `b` and
   `w` independent whole minutes uniform in [−60, +60]. The bedroom hour before bedtime
   and after waking move with them; the evening `out` phase shrinks or grows to fill
   (it may be 0 minutes long); charging stays at 19:00 + offset. Bed, motion, worn, and
   phase deltas all follow the shifted phases. Lead-in nights are not shifted.
5. **Crossing allowed.** Readings are not clamped to the thresholds; they are clamped
   only to the contract's valid range (`lambdas/ingest/src/validation.py`: HR 20–250,
   SpO2 50–100, temp as today). Every line still passes `validate_event`.

### Vitals alerts

6. **Readings past a limit alert.** A normal `vitals` reading of an unusual resident is
   treated as held until that wearable's next reading. If HR is below/above or SpO2 below
   the wearable's device limits, it starts an episode exactly as a scenario reading does
   (the engine's existing timer: first `vitals_alert` 10 s later, repeats every 5 min,
   only while worn), and the next reading back inside ends it. A null reading ends
   nothing and starts nothing.
7. **Scenario episodes.** `--vitals-episodes N` (default 176, integer 0–400) places N
   episodes on the unusual residents' wearables: `round(N / 2)` `hr-spike`, the rest
   `spo2-drop`, each on a resident chosen at random, at a random time inside [`--from`,
   `--to` + 1 day) SGT whose likelihood rises linearly to 10 times as likely at the end
   as at the start (for 8 months and 4 residents: about 1.6 per resident in the first
   month, about 9.4 in the last), while the wearable is worn for the scenario's whole 3 minutes,
   not within 10 minutes of a fall or another scenario on that wearable. They use the
   engine's existing scenarios. If N can't be placed, exit 2 before writing anything
   (as for falls). A run whose devices file has no unusual wearable places none.
   Falls stay as today (any resident).
8. **Edges.** A scenario and a normal-reading episode for the same metric at once are
   one episode (the engine keeps one per wearable and metric). Episodes still open at
   the window stop are dropped, as today.

### Output

9. The success summary on stderr adds the unusual resident IDs, and per resident the
   number of bad days and `vitals_alert` lines; one line per scenario episode
   (`hr-spike wearable-NNNN yyyy-mm-dd HH:MM SGT`). Synthetic IDs only, no payload values.
10. Same arguments and input files still give byte-identical files with the same names.
    New random draws (bad days, sleep shifts, episode placement) each use their own
    `random.Random` seeded from `--random-seed`, never the devices' streams.
11. `docs/features/simulator-backfill.md` criterion 11 ("no `vitals_alert` lines") and
    the README's backfill section are updated to match.

### Tests

12. Unit tests (short ranges, few devices, `tmp_path` only) cover: an ordinary resident's
    `vitals` values match today's backfill; unusual noise width; decline at the start
    and end of the range; a forced bad day's shift; sleep shifts within ±60 min and none
    in the lead-in; an unclamped reading past a limit gives a `vitals_alert` 10 s later
    and repeats, a reading back inside ends it; scenario counts (`round(N/2)` HR spikes),
    placement rules, and exit 2 when they can't be placed; `--vitals-episodes` bad
    values exit 2; every line passes `validate_event`; byte-identical output for the
    same seed; live simulator output unchanged (an existing engine test still passes).

## Replacing the loaded backfill

The owner runs this from the host after merge (developers can't write the history
bucket or invoke Lambdas). The README gets these steps.

1. Generate with the **same** `--from`/`--to` as the earlier load into an empty folder.
2. Delete the old generated telemetry: under `telemetry/` in the history bucket, only
   objects with metadata `source=simulator-backfill` in the generated dates'
   partitions (check each object's metadata with `head-object`; leave any other object).
   File names changed, so uploading over them would keep both copies and count twice.
3. Upload as in the README (`--metadata source=simulator-backfill`).
4. Re-run `clean` then `summaries` for every generated date, oldest first, as in the
   README. Each re-run replaces that date's output, including the summaries.

The re-seed and overlap steps aren't needed again (seed `assigned_at` is unchanged, and
these dates were checked at the first load).

### Load status

Reload of 2026-03-01 to 2026-10-31 (UTC dates 2026-02-26 to 2026-10-31, 248 days;
1,122 generated files), run by the owner on 2026-11-09:

- [x] Old generated telemetry deleted (only `source=simulator-backfill` objects).
- [x] Overlap on 2026-10-25: 6 live test files (10:59–11:12 UTC, 2 status, 3 vitals,
      1 fall) shared that date with the backfill; the owner deleted them.
- [x] New files uploaded.
- [ ] `silvercare-clean` for all 248 dates: running since about 20:05 SGT, about 1 min
      per date (2026-03-10 took 62 s), expected done around 00:30 SGT 2026-11-10.
- [ ] `silvercare-summaries` for 245 dates (the lead-in skipped): after clean, about
      1.5–3 h, expected done around 03:00 SGT 2026-11-10.
- [ ] Checked: alert episodes rise from about 1 to about 10 per month for the unusual
      residents; ordinary residents have none.

## Data

- Reads: `scripts/seed-data.json`, `infra/devices/devices.json` (as today).
- Writes: local files under `--out` only (as today). After loading: history bucket
  `telemetry/`, clean tables, `summaries/` (as today).

## IoT

None: nothing is sent. Lines use the existing `vitals` and `vitals_alert` payloads
(docs/metrics.md).

## API contract

None.

## Screens

None.

## Alerts and notifications

None produced: no emails, no DynamoDB alerts, no `alerts/` records, no warnings from the
`telemetry` Lambda. `vitals_alert` lines appear only in the clean tables and summaries.

## Privacy

- Personal data touched: none (synthetic residents from `scripts/seed-data.json`).
- Retention: `telemetry/` as the history bucket; `summaries/` kept forever, replaced by
  re-running the summaries job (Replacing the loaded backfill).
- Must not be logged or shown: payload values (as today).

## Out of scope

- Stored alerts (`alerts/`), warnings, emails, and dashboard alerts for generated history.
- Variation for the live simulator or `--dry-run`.
- `no-pulse` episodes; temperature drift; changing which residents are unusual by flag.
- Refreshing Tableau extracts after the reload.

## Open questions

None. Sizes confirmed by the user 2026-11-09: noise ±12 bpm / ±3 % / ±0.8 °C,
decline +8 bpm / −2 % (revised from +20 / −5), bad days +15 bpm / −3 % at 1 in 10,
episodes ramping to about 10 per resident per month.
