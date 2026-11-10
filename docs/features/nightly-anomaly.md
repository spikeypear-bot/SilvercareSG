---
status: built          # draft | ready | built
---

# Nightly anomaly score

## Summary

Each morning, after the nightly results are loaded, a SageMaker Random Cut Forest (RCF)
model scores every resident's previous Singapore night against that resident's own
usual nights, using the daily summaries (docs/features/curated-layer.md). A night that
is unusual for that person, even with every reading inside their thresholds, gets an
"unusual night" flag for doctors, with the two measures furthest from their usual. It
is a prompt to look, not an alert or a diagnosis. The shared model is retrained weekly.

## Decisions

Made by the user (2026-11-09):

- Use SageMaker's built-in **Random Cut Forest** (unsupervised: no labels).
- Flags go to **doctors for review**, never to nurses as alerts, never emailed.
- Training jobs and batch transform only; **no real-time endpoint** (it bills hourly).
- **Features:** `hr_bpm_avg`, `hr_bpm_min`, `hr_bpm_max`, `spo2_pct_avg`,
  `spo2_pct_min`, `skin_temp_c_avg`, `activity_pct_avg`, `bed_minutes`,
  `motion_events`. Falls and alert counts are left out: they already flag a night.
- **One shared model:** each resident's features are standardised against their own
  baseline first, so the score means "unusual for this person".
- **Baseline:** a resident's nights **8–37 days before** the scored night (30 nights,
  skipping the latest week, so a drift over a week or two still stands out). Read from
  `summaries/` in S3, since the Results table keeps only 30 nights.
- **Retrain weekly**, Mondays 10:00 SGT, on the **last 90 days**; the baseline is
  recomputed at every nightly scoring.
- **Cut-off:** unusual when the score is more than **3 standard deviations above the
  mean** of the model's training scores, recomputed at each retrain.
- **Explanation:** the **2 features furthest** from the resident's baseline, e.g.
  "HR avg 78 (usual 58) · bed 4 h 0 m (usual 7 h 6 m)", computed in Python.
- **Low wear:** a night with fewer than **720 vitals readings** (50 % of 1,440) isn't
  scored and isn't used in baselines or training; doctors see "Not scored: low wear
  time".
- **New residents:** scoring needs at least **20 usable baseline nights** of the 30;
  before that the resident page shows "Anomaly check needs 20 usable nights of history (N so far)" (N = usable baseline nights).
- **Shown in both** the doctor dashboard's "Last night" list (new reason, ranked
  **last**, after out-of-limit readings) and the resident page's "Nightly summary".
- **Storage:** a new bucket `silvercare-ml-<account_id>` holds model input, models,
  transform output, and `scores/` (the kept record).
- **Retention:** scores are derived records that informed care: `scores/` is kept for
  the resident's lifetime + 6 years (no expiry), plus a 30-night copy on the Results
  item. Each weekly **model** is kept as long as the scores it produced.
- **Erasure:** delete the resident's `scores/` objects and Results items; models stay
  (they hold tree statistics over standardised values, no IDs, and are needed to
  reproduce other residents' flags).
- **No alarms:** failures show as "Not scored" and in logs only; nothing is emailed
  (the account already uses its 10 free alarms).
- **One spec**, not split (user agreed to the size).

## Users and access

| Group | Can see | Can do |
|---|---|---|
| doctor | every resident's anomaly result per night (score, flag, reasons, status) on the resident page and in "Last night" | read only |
| nurse | nothing new (the sections aren't shown; routes already return `403`) | nothing |
| caregiver, ops, family, no group | nothing (`403`, unchanged) | nothing |

- No route or group check changes: both routes already require `doctor`
  (`lambdas/api-nightly/src/handler.py`). Doctors see every resident (staff rule).
- Roles:

| Role | Reads | Writes |
|---|---|---|
| `silvercare-anomaly-score` Lambda | `summaries/` in the history bucket (List + Get, prefix-limited); `models/current.json` in the ML bucket | `input/score/`, `input/score-meta/` in the ML bucket; `sagemaker:CreateTransformJob` on `anomaly-score-*`; `lambda:InvokeFunction` on collect |
| `silvercare-anomaly-train` Lambda | summaries (as score) | `input/train/` in the ML bucket; `sagemaker:CreateTrainingJob` on `anomaly-train-*`; `iam:PassRole` on the SageMaker role only |
| `silvercare-anomaly-collect` Lambda | `output/`, `input/score-meta/` in the ML bucket | `scores/` (with its tag), `models/<version>/cutoff.json`, `models/current.json` in the ML bucket; `sagemaker:CreateModel` on `anomaly-train-*`, `CreateTransformJob` on `anomaly-cal-*`; `iam:PassRole` on the SageMaker role only; `dynamodb:UpdateItem` on `silvercare-results` |
| `silvercare-sagemaker-anomaly` (SageMaker execution role) | `input/`, `models/` in the ML bucket | `models/`, `output/` in the ML bucket; its own CloudWatch logs |
| API (`api-nightly`) | unchanged (reads the new attributes on the same items) | none |

## How it works

```
summaries → results ──on-success──▶ anomaly-score ──▶ SageMaker batch transform
                                                         │ (EventBridge: job state change)
weekly schedule ──▶ anomaly-train ──▶ training job ──▶ model + transform of training set
                                                         ▼
                                              anomaly-collect ──▶ scores/ + Results item
```

## Acceptance criteria

### Features and baseline (shared code, unit-tested)

1. A night is **usable** when its summary has `vitals_readings` ≥ 720 and none of the 9
   features is null. `bed_minutes` null makes the night unusable.
2. The baseline for night D is the resident's usable nights dated D−37 to D−8
   inclusive. Mean and standard deviation per feature (population SD); an SD below a
   floor (HR 2 bpm, SpO2 0.5 %, skin temp 0.2 °C, activity 2 %, bed 20 min, motion 3)
   uses the floor, so a perfectly steady resident doesn't produce huge z-scores.
3. A night is **scoreable** when it is usable and its baseline has at least 20 usable
   nights. Each feature becomes z = (value − mean) / SD, rounded to 4 decimals.
4. The explanation is the 2 features with the largest |z|, ties by the feature order in
   Decisions, each as `{"feature", "value", "usual"}` (`usual` = baseline mean, 1
   decimal).

### Nightly scoring

5. `results` gains an on-success destination, `silvercare-anomaly-score`, and its role
   gains `lambda:InvokeFunction` on that function only. Input: Lambda's on-success
   record (`responsePayload.date`, `responsePayload.loaded`) or `{"date": "yyyy-mm-dd"}`
   manually; anything else raises before any AWS call without repeating the value.
   `loaded: 0` logs and returns.
6. If `models/current.json` (the current model) is missing, the Lambda logs a warning
   and writes status `no_model` for each resident with a summary for D (criterion 12).
7. Otherwise it reads summaries for D and D−37…D−8 (paginated list per date), builds
   one CSV row of 9 z-scores per scoreable resident to `input/score/date=<D>/<run>.csv`
   with a sidecar `input/score-meta/date=<D>/<run>/rows.json` (outside the
   transform's input prefix, which it reads whole) (resident order, explanations, status of every other
   resident: `low_wear` or `not_enough_history` with its usable-night count), and
   starts transform job `anomaly-score-<D>-<run>` on the current model:
   `ml.m5.large`, 1 instance, `SplitType: Line`, CSV in and out. No scoreable
   residents: no job; the collect step is invoked directly with the sidecar.
8. Re-running `results` for a date overwrites its items without anomaly fields
   (unconditional PutItem), and the chained run rescores them. A manual `results` run
   (synchronous) doesn't chain: the owner also runs `anomaly-score` with the date. The
   docs say so.

### Weekly training

9. An EventBridge Scheduler schedule runs `anomaly-train` every Monday 10:00 SGT. It
   builds training rows from every scoreable resident-night in the last 90 days (D
   from today−90 to yesterday), each standardised against its own baseline (criterion
   2). Fewer than 200 rows: logs, raises, no job.
10. It starts training job `anomaly-train-<yyyymmdd>-<run>` with the built-in RCF image
    (Terraform `aws_sagemaker_prebuilt_ecr_image`, `randomcutforest`):
    `ml.m5.large`, 1 instance, `feature_dim=9`, `num_trees=100`,
    `num_samples_per_tree=256`, max runtime 30 minutes, output `models/<version>/`.
    `<version>` is the job name.
11. When training completes (EventBridge "SageMaker Training Job State Change",
    `Completed`, name prefix `anomaly-train-`), `anomaly-collect` creates the SageMaker
    model and a transform job `anomaly-cal-<version>` over the training rows. When that
    completes, it writes `models/<version>/cutoff.json`
    (`{"mean", "sd", "cutoff": mean + 3·sd, "rows"}`) and only then updates
    `models/current.json` to `<version>`. A failed or stopped job leaves the current
    model unchanged and raises.

### Collecting scores

12. On "SageMaker Transform Job State Change" `Completed` for `anomaly-score-*`,
    `anomaly-collect` reads the output (one `{"score": x}` line per input row) with the
    sidecar and, per resident with a summary for D, writes:
    - S3 `scores/date=<D>/<uuid4>.json` in the ML bucket with tag
      `resident_id=<id>`: `resident_id`, `date`, `status` (`scored`, `low_wear`,
      `not_enough_history`, `no_model`), `score`, `cutoff`, `unusual`
      (`score > cutoff`), `reasons` (criterion 4), `usable_nights`, `model_version`,
      `generated_at`, `schema_version: 1`. A rerun for D deletes `scores/date=<D>/`
      objects first (as summaries).
    - `UpdateItem` on `RESIDENT#<id>` / `NIGHT#<D>` in `silvercare-results` setting
      `anomaly` (the same fields minus `resident_id`, `date`, `generated_at`), with
      `attribute_exists(pk)` so no stub items are created; a failed condition is logged
      with the ID and skipped. Writes retry throttling (adaptive).
13. A failed scoring transform job raises in `anomaly-collect`; no scores are
    written for D, and the nightly routes show no anomaly for that night (criterion 16).
14. Output row count not matching the sidecar raises before any write.

### API (`lambdas/api-nightly`)

15. `GET /residents/{resident_id}/nightly`: each night gains `anomaly` (shape in API
    contract) from the item's `anomaly` map; numbers converted as other fields.
16. A night without an `anomaly` map returns `"anomaly": null` (not scored yet, or the
    job failed).
17. `GET /nightly/overview`: a resident whose last-night item has `anomaly.unusual =
    true` is flagged, with `unusual: true` and `anomaly_reasons` in its row. Order:
    falls, alert episodes, out-of-limit readings, then unusual-only rows by score
    (highest first), then resident ID. `clear_count` no longer counts them.
18. A malformed `anomaly` map on one item (wrong type, missing field) makes that
    resident's `anomaly` null and is logged with the ID; it never hides the rest of the
    night or other residents.

### Screens

19. Resident page "Nightly summary" gains an "Anomaly check" row per status
    (Screens). Score and cut-off are shown to 2 decimals.
20. "Last night" rows gain "unusual night (HR avg 78, usual 58 · bed 4 h 0 m, usual
    7 h 6 m)" as the last reason; an unusual-only row shows just that.
21. Both new rows are text, not colour-only; the badge has an accessible label
    ("Unusual night for this resident").

### Infrastructure, alarms, logs

22. Bucket `silvercare-ml-<account_id>`: private, SSE-S3 (`AES256`, as the history
    bucket, `infra/history.tf`), TLS-only policy, versioning off (as the history bucket, so a delete
    really deletes for erasure), public access blocked,
    `ap-southeast-1`. Lifecycle: `input/` and `output/` expire after 30 days; `scores/`
    and `models/` never expire. The bucket policy denies `PutObject`/`DeleteObject` on
    `scores/*` to every principal but `anomaly-collect`.
23. Lambdas: Python 3.13, standard library, boto3, Powertools only (z-scores and
    statistics in plain Python); 128 MB; timeouts 120 s (score), 300 s (train: 127 dates of
    summaries, read in parallel), 60 s (collect); log retention 30 days; async retries 2, max event age 1 hour.
24. No CloudWatch alarms (user decision): failures show as "Not scored for this night"
    in the portal and in the Lambdas' logs. Each Lambda logs one `ERROR` line with the
    date and job name when it fails.
25. Logs: dates, job names, counts (residents, scoreable, low wear, not enough
    history, unusual), model version. Never feature values, z-scores, scores, or
    explanations per resident; skipped items by ID only.
26. Docs: `docs/data-model.md` gains the `anomaly` attribute on Results items, the ML
    bucket layout, retention, and the erasure step; CLAUDE.md's architecture gains the
    SageMaker exception (jobs only, no endpoint) and the cost.

### Go-live checks (owner, after merge, not tests)

27. Before merge: check Billing → Credits shows enough credit left (cost table).
28. Run `anomaly-train` manually; check a model and `cutoff.json` appear and
    `models/current.json` names it. Then run `anomaly-score` for yesterday.
29. Validation: score the backfill's last 90 nights and compare flagged nights for
    `res-0003`, `res-0008`, `res-0014`, `res-0019` (backfill-variation) with the other
    16, and with a z-score rule (any |z| > 3). Record the result in this spec.

## Data

- Reads: `summaries/date=<D>/` in the history bucket (as `results`); Results items via
  the existing `GET` patterns.
- Writes: `anomaly` map on existing Results items (UpdateItem, idempotent: same key,
  same value on rerun); `scores/` objects (rerun deletes the date first).
- New: the ML bucket (criterion 22). Results items grow by about 400 bytes, staying
  under 4 KB (0.5 RCU per eventually consistent read, unchanged). No capacity change
  (20 RCU / 19 WCU).

## IoT

None.

## API contract

### `GET /residents/{resident_id}/nightly` (changed)

Unchanged except each night gains `anomaly`:

```json
"anomaly": {
  "status": "scored",
  "score": 2.84,
  "cutoff": 2.31,
  "unusual": true,
  "reasons": [
    {"feature": "hr_bpm_avg", "value": 78.0, "usual": 58.2},
    {"feature": "bed_minutes", "value": 240, "usual": 426.0}
  ],
  "usable_nights": 30,
  "model_version": "anomaly-train-20261116-a1b2"
}
```

`status` `low_wear`, `not_enough_history`, `no_model`: `score`, `cutoff`, `unusual`
null, `reasons` `[]`. Not scored: `"anomaly": null`. Errors unchanged.

### `GET /nightly/overview` (changed)

Each `flagged` row gains `"unusual": true|false` and `"anomaly_reasons": [...]`
(same shape as `reasons`). Unusual-only residents appear in `flagged`. Errors unchanged.

## Screens

### Nightly summary (resident page, doctors), new row

```
│ Anomaly check  ⚠ Unusual night for this resident (score 2.84, cut-off 2.31) │
│                HR avg 78 (usual 58) · bed 4 h 0 m (usual 7 h 6 m)           │
```

Other states: "Usual night" (score shown); "Not scored: low wear time"; "Anomaly check needs 20 usable nights of history (N so far)"; "No anomaly model yet"; `anomaly` null: "Not
scored for this night". Loading / error: the section's existing states.

### "Last night" (doctor dashboard)

```
│ 5 ⚠ Ng Kim Seng   unusual night (HR avg 78, usual 58 · bed 4 h 0 m, usual 7 h 6 m) │
```

(Synthetic name.) Loading / empty / error: unchanged.

## Alerts and notifications

None. Job and Lambda failures are not emailed (criterion 24).

## Cost

Rough, from US prices from memory, **to be checked** against ap-southeast-1 SageMaker
pricing before building:

| Item | Basis | Monthly |
|---|---|---|
| Nightly transform | ~30 jobs × ~6 min on `ml.m5.large` (~US$0.13/h) | ~US$0.40 |
| Weekly training + calibration transform | ~4 × (~6 + ~6 min) | ~US$0.10 |
| S3, Lambda, EventBridge | tiny | ~US$0.05 |

Total under US$1 a month. No endpoint, no idle cost.

**Free plan (checked 2026-11-09):** AWS's list of services for new free-plan accounts
(accounts reference, "Supported AWS services") includes Amazon SageMaker AI, and the
Free Tier page lists it as "Available on both plans", so jobs run on the free plan and draw on the credits. The
2-month SageMaker free trial is a short-term trial, which AWS's billing docs give only
to the Paid plan, so it doesn't apply here. The first training run (criterion 28)
proves it works.

## Privacy

- Personal data touched: daily health summaries (read), per-night anomaly scores and
  explanations (health-derived), keyed by opaque `resident_id`, no names. Doctors only.
- Retention: `scores/` lifetime + 6 years; Results copy 30 nights; models as long as
  their scores; `input/` and `output/` (z-scores per resident) 30 days.
- Erasure: delete `scores/` objects tagged with the resident's ID and their Results
  items; `input/`/`output/` expire within 30 days (or delete by date); models stay
  (Decisions).
- Not logged or shown: feature values, z-scores, scores, explanations in logs.
- Synthetic data only; the model says nothing clinical while trained on simulator data.

## Out of scope

- Disease prediction or diagnosis (HSA Software as a Medical Device question).
- Real-time scoring of single readings; SageMaker endpoints.
- Alerts, emails, or nurse/caregiver views of the score.
- Doctors tuning the cut-off or features from the UI.
- Backfilling scores beyond what criterion 29 needs.

## Open questions

