---
status: built          # draft | ready | built
---

# Simulator backfill: months of history for analytics

## Summary

A `backfill` tool (`simulator/src/backfill.py`) runs the simulator engine
(docs/features/simulator-core.md, docs/features/simulator-alert-scenarios.md) over a
past date range on an instant clock and writes the result as **local files in exactly
the history bucket's raw layout** (`telemetry/message=<m>/date=<d>/….jsonl.gz`,
docs/data-model.md, S3 history). Uploaded to the history bucket and run through the
existing `clean` and `summaries` jobs, it gives the analytics (clean tables, curated
`daily_summaries`, Tableau) months of realistic data: 20 residents, 40 devices, normal
routines, and a few falls, some of them cancelled with "I'm OK".

It never connects to anything: no MQTT, no AWS. Uploading and running the jobs is done
by someone with write access (see Loading the data).

Decisions (user, 2026-11-08): the range is 2026-03-01 to 2026-10-31 Singapore time
(8 months); 12 falls; at random times while the resident is up and wearing the watch;
about 1 in 4 falls is a false alarm the resident cancels with "I'm OK".

## Users and access

No Cognito group uses this feature. There is no API and no screen.

| Group | Can see | Can do |
|---|---|---|
| staff | nothing (new) | nothing (new) |
| caregiver | nothing (new) | nothing (new) |

How access to a specific resident is decided: not applicable. Once loaded, the data is
read through the existing clean and curated access rules (docs/data-model.md).

## Acceptance criteria

### Command line

1. `python simulator/src/backfill.py --from <yyyy-mm-dd> --to <yyyy-mm-dd> --out <dir>
   [--falls N] [--random-seed S] [--seed-file …] [--devices-file …]` (standard library
   only; paho is never imported). `--from`/`--to` are Singapore dates, inclusive.
   `--falls` defaults to 12, `--random-seed` to 1, the files to the simulator's defaults
   (`scripts/seed-data.json`, `infra/devices/devices.json`), loaded and validated the
   same way as `simulator/src/main.py` does (same residents, baselines, devices).
2. Exit 2 with a one-line `error: …` on stderr, before writing anything, when:
   - a date isn't a real `yyyy-mm-dd` date, or `--from` is after `--to`;
   - the range (lead-in included, criterion 4) is more than 400 days;
   - `--to` is today or later in Singapore time (only past days: live data fills today);
   - `--falls` isn't an integer from 0 to 100, or `--random-seed` isn't an integer;
   - `--out` exists and isn't an empty directory, is a symbolic link, or its parent
     folder doesn't exist (the tool creates only `--out` itself). No check names the repo's secrets folder:
     `simulator/src/` must never mention it (`test_no_source_file_mentions_secrets`),
     and the output is synthetic and harmless wherever it lands;
   - the seed or devices file is invalid (same messages as `main.py`).
3. `make backfill ARGS="--from … --to … --out …"` runs it in the dev container (added
   to the Makefile outside the build, see Out of scope).

### What is generated

4. **Window.** The engine starts at `--from` minus 2 days, 00:00 Singapore time (the
   *lead-in*: summaries for Singapore date D read clean partitions D−1 and D, and D−2
   for `bed` and `vitals_alert`, docs/features/curated-layer.md criterion 10, and fail
   when one is missing, criterion 4). It stops at `--to` + 1 day, 00:00 Singapore time.
   Messages whose `received_at` is at or after the stop are not written; events still
   scheduled at the stop are dropped and not reported. Falls are placed only inside
   [`--from`, `--to`] (criterion 9), never in the lead-in.
5. **Engine reuse.** One `Engine` (simulator/src/engine.py) over all devices in the
   devices file that the seed assigns, with `start_epoch_ms` = the window start,
   `start_routine_ms` = 0 (midnight Singapore time), `seed` = `--random-seed`, and
   `schedule_startup=True`. Normal behaviour, routines, offsets, battery, payload
   formats, and the fall and `im-ok` scenarios are the engine's own: the backfill adds
   no second implementation of any of them.
6. **Same output for the same input.** `message_id`s come from a `random.Random` seeded
   from `--random-seed` (passed to the engine as `new_id`, version-4 UUID format, valid
   for ingest's `UUID_V4_RE`), and every other random choice in the backfill (fall
   placement, cancel choice, `received_at` delays, file name UUIDs) from its own seeded
   `random.Random`. Two runs with the same arguments and input files write byte-identical
   files with the same names (gzip `mtime=0`), so re-uploading replaces objects instead
   of adding duplicates.
7. **Lines as the IoT rules produce them.** Each line is one compact JSON object: the
   device payload plus `device_id`, `message` (as `SELECT *, topic(3) AS device_id,
   topic(4) AS message, timestamp() AS received_at`, infra/ingest.tf), and
   `received_at` = the time the engine sends the message (its `elapsed_ms` after the
   window start, as epoch milliseconds) plus a delay drawn uniformly from 100 to 900 ms
   (integer). That is `ts` + 100–900 ms for every message except `fall`, whose `ts` is
   the impact and which is sent 20 s later, so its `received_at` is `ts` + 20.1–20.9 s,
   as from a real wearable. So `clock_skew` is false for every line. Every line
   passes ingest's `validate_event` (lambdas/ingest/src/validation.py), including
   `fall`, `alert_cancel`, `status`, `motion`, `bed`, and `vitals`.
8. **Files.** One gzipped JSON Lines file per message type and **UTC** date of
   `received_at` (as the history Lambda groups, lambdas/history/src/handler.py), at
   `<out>/telemetry/message=<message>/date=<yyyy-mm-dd>/<yyyymmdd>T235959Z-<uuid>.jsonl.gz`,
   lines in `received_at` order. `<message>` is the message type (`vitals`, `status`,
   `motion`, `bed`, `fall`, `alert_cancel`). A message type with no messages on a date
   has no file. The tool writes nothing outside `<out>`. Files are written as the run
   goes (one open file per message type at a time), so memory doesn't grow with the
   range.

### Falls

9. **Placement.** `--falls` falls, each on a wearable of a resident chosen at random
   (a resident may fall more than once, never twice on the same Singapore day), at a
   random time inside [`--from` 00:00, `--to` + 1 day 00:00) Singapore time such that
   the resident's routine phase (simulator/src/routine.py, with their offset) is
   `bedroom` or `out` (up and wearing the watch, not asleep, not charging) from the
   fall's start until 10 minutes later (the fall's maximum time down). A fall is
   started with the engine's `fall` scenario at that time, so it sends the `fall`
   message 20 s later and lowers activity while the resident is down, as in the
   scenarios spec. If the requested number can't be placed (e.g. a very short range),
   exit 2 before writing anything.
10. **"I'm OK" false alarms.** `round(N / 4)` of the N falls (3 of 12), chosen at random,
    are cancelled: the engine's `im-ok` scenario starts on that wearable at a random
    time 30 to 90 s after the fall's start (10 to 70 s after the `fall` message), which
    sends one `alert_cancel` and ends the fall. The other falls end after 10 minutes.
11. **Every fall happens.** No fall or cancel is skipped (the engine's `skipped` notice
    must not appear for them); a test checks the output has exactly N `fall` lines and
    `round(N / 4)` `alert_cancel` lines, and no `vitals_alert` lines.

### Output and summary

12. On success, exit 0 and print to stderr only:
    - one line: `backfill: <from> to <to> (lead-in from <date>), <devices> devices,
      random seed <S>`;
    - one line per fall: `fall <wearable_id> <yyyy-mm-dd HH:MM> SGT` plus
      ` (cancelled)` for the cancelled ones (synthetic IDs only);
    - totals: lines per message type, files written, and the first and last UTC
      partition date.
    Nothing is printed to stdout. No payload values are printed.
13. A full 8-month run (2026-03-01 to 2026-10-31, 40 devices) finishes in under 15
    minutes in the dev container and writes roughly 10 million lines. (Reported by the
    builder from one run; not a unit test.)

### Tests

14. Unit tests in `simulator/tests/` (no network, no AWS, output only under `tmp_path`)
    cover: every exit-2 case in criterion 2; window start and stop at the Singapore-time
    boundaries and the lead-in (criterion 4); every line passes ingest's validation and
    has `received_at` 100 to 900 ms after `ts`; files grouped by UTC date of
    `received_at` with the exact key format; byte-identical output for the same seed and
    different output for another seed; fall count, cancel count, fall times only in
    `bedroom`/`out` phases inside the range, none in the lead-in, at most one per
    resident per Singapore day; cancel 10 to 70 s after its `fall` message; no
    `vitals_alert`; nothing written on any error. Use short ranges (a few days) and,
    where useful, fewer devices to keep tests fast.

## Data

- Reads: `scripts/seed-data.json` and `infra/devices/devices.json` (local files), as the
  simulator does.
- Writes: local files only, under `--out`. No DynamoDB, S3, or IoT access.
- New access patterns or tables: none. Once uploaded, the files are ordinary raw
  telemetry (docs/data-model.md, S3 history, Key layout) read by `clean`.

### Loading the data (documented in the README, run by someone with write access)

The shared developer user can read the history bucket but not write to it
(docs/iam/developer-policy.json), and Claude has no AWS access. Someone with write
access (the account owner) runs, from the host:

0. **Re-run `scripts/seed.py`** so the device assignments have the fixed `assigned_at`
   (docs/features/seed-data.md criterion 6). Without it, summaries ignore every
   generated reading and store empty summaries that only the summaries job can replace.
1. **Check for overlap, automatically.** Compare the generated UTC `date=` folders
   (all message types; they run from `--from` minus 3 days to `--to`, because the
   lead-in starts at 00:00 SGT = 16:00 UTC the day before) with the `date=` folders
   already under every `telemetry/message=*/` prefix, and stop if any date is in both:
   the clean job keeps every distinct `message_id`, so overlapping days would hold both
   and double-count in summaries. Then re-generate with an earlier `--to`.
2. `aws s3 sync <out>/telemetry s3://silvercare-history-<account_id>/telemetry/
   --metadata source=simulator-backfill`, so the objects can be told apart later.
   Re-upload only with identical arguments (same keys, replaced); after a run with
   other arguments, delete the earlier generated objects first, or both are counted.
3. Invoke `silvercare-clean` **synchronously**, once per generated UTC date, in date
   order, with `{"date": "<yyyy-mm-dd>"}`.
4. Invoke `silvercare-summaries` **synchronously**, once per Singapore date from the
   first to the last generated Singapore day (`--from` to `--to`, read from the
   generated folders, not typed), with `{"date": "<yyyy-mm-dd>"}`. Synchronous runs
   don't trigger the on-success `results` chain (docs/features/nightly-results.md
   criterion 5a), so the Results table isn't filled with months of nights.

The README gives these as copy-paste commands. Summaries are kept with no expiry and
only the summaries job can delete or replace them, so step 0 and step 1 come first.

## IoT

Message types and payloads exactly as docs/metrics.md (via the engine): `vitals`,
`status` (wearable and bedroom), `motion`, `bed`, `fall`, `alert_cancel`. No topics are
used: nothing is published.

## API contract

None.

## Screens

None.

## Alerts and notifications

None. Nothing goes through IoT Core, ingest, or alert-processor, so no alert is stored
and no email is sent. The falls appear only in the analytics (clean `fall` and
`alert_cancel` tables, `daily_summaries` falls and cancels counts).

## Privacy

- Personal data touched: none real. Synthetic residents and devices from the seed file
  (reserved IDs `res-0001`…`res-0020`) only.
- Retention: once uploaded, raw telemetry and clean tables expire 365 days *(proposed)*
  after the objects are **written** (docs/data-model.md Retention), so March data
  uploaded in November 2026 lasts until about November 2027. The daily summaries built
  from it are kept with no expiry, like all summaries. The raw objects carry the S3
  metadata `source=simulator-backfill` (upload step 2); the lines themselves carry no
  marker.
- Erasure: the raw `telemetry/` objects of the generated dates can be deleted by
  whoever can write the bucket, and the clean tables rebuilt by re-running clean for
  those dates. Summaries can only be replaced by re-running the summaries job, which
  writes zero-count summaries for days with no readings rather than removing them;
  erasure is otherwise "not solved", as docs/data-model.md says for all clean data.
  `docs/data-model.md` lists removing or setting apart the generated history before
  real resident data arrives (its device IDs are real hardware).
- Must not be logged or printed: payload values. Only IDs, dates, times of falls, and
  counts (criterion 12).

## Out of scope

- Uploading or invoking anything in AWS (the tool only writes local files).
- `vitals_alert`, heart rate, SpO2, low battery, offline, or not-worn incidents; falls
  at night (wearable off or asleep); possible-fall (bedroom) events.
- Stored alerts (`alerts/`, DynamoDB alerts) and emails for the generated falls.
- Changing the simulator's live or dry-run behaviour (`main.py`, `runner.py`).
- The Makefile target (criterion 3), `.gitignore` entry, and README section: Claude adds
  them outside the build (the builder may not edit those files).
- Filling the nightly Results table for past nights.

## Open questions

None.
