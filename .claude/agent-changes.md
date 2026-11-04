# Agent setup changes

Changes the `improve-agents` skill made to skills, agents, and workflows after a bug or
a wasted step. Newest first. Review these in the diff before committing.

## 2026-11-03

- **Problem:** the live `seed-data` run stopped on 34 devices: it treated unassigned
  spares (written by Track A's `scripts/seed-registry.py`) as "not owned", the same as
  devices assigned to another resident. **Changed:** `.claude/agents/backend-builder.md`:
  "not owned" means holding someone else's data; check what other scripts on `main`
  already write.

- **Problem:** the live `--alerts` check (spec by Claude) sent heart rates 125/128 at
  once: a teammate's personal limit of 130 made them normal, and the two readings ran
  in parallel, so no warning came. **Changed:** `.claude/skills/feature-spec/SKILL.md`:
  live checks avoid shared mutable data and space out order-dependent messages.
  (Fix: `scripts/iot-smoke-test.py` now sends 180/185/182 with a 3 s pause.)

- **Problem:** in `alert-cancel` tests, `\u200b`/`\u202e` escapes were saved as raw
  invisible characters, which ruff rejected, and took extra steps to repair.
  **Changed:** `.claude/skills/lambda-test/SKILL.md`: keep such characters as escapes
  and check the saved file.

- **Problem:** the `alert-cancel` spec (written by Claude) told Track B its API "needs
  only `sqs:SendMessage`" for `status_update`, without the identity, group, and link
  checks it must make before sending (privacy-reviewer, low). **Changed:**
  `.claude/skills/feature-spec/SKILL.md`: contracts other components send state the
  sender's checks.

- **Problem:** the `escalation` spec (written by Claude) let caregivers acknowledge
  without saying what others see about who acknowledged (privacy-reviewer, medium), and
  didn't say what a check redriven from the DLQ hours later does (iac-reviewer, low).
  **Changed:** `.claude/skills/feature-spec/SKILL.md` (readiness check: what others see
  about a new action) and `.claude/skills/lambda-python/SKILL.md` (decide late-message
  behaviour for time-bound notifications).

- **Problem:** Claude edited a test with a Python one-off script, which skips the format
  hook, so the user's lint check failed. **Changed:** `.claude/skills/local-dev/SKILL.md`:
  run `ruff format` after shell-script edits.

- **Problem:** the fix for the flaky log tests only left out `timestamp`; history's
  test still failed in the A2 PR checks because an S3 key's random UUID contained
  `187` (and an ingest test could hit `999` in `message_id`). **Changed:**
  `.claude/skills/lambda-test/SKILL.md`: drop timestamp/location **and** replace UUIDs.

- **Problem:** two log-privacy tests (`test_fall_log_has_no_resident_or_fall_values`,
  history's `test_logs_contain_no_bodies_ids_or_values`) checked the raw log text, so a
  timestamp containing `33` or `187` failed them at random. **Changed:**
  `.claude/skills/lambda-test/SKILL.md`: check parsed records without `timestamp` and
  `location`.

- **Problem:** the `vitals-alerts` spec (written by Claude) bounded the suppression
  Query at `ALERT#<event_ts>`, which excludes `ALERT#<event_ts>#<alert_id>`, so a
  retried alert was suppressed by an earlier one and never emailed (lambda-tester
  found it). **Changed:** `.claude/skills/data-model/SKILL.md`: end composite sort-key
  ranges with `#~` when same-`ts` items belong in the result.

- **Problem:** Claude told the user the `fall-alert` → `critical-alert` rename loses
  alerts for "a few seconds"; iac-reviewer (low) showed it's up to about a minute
  (destroy first, then IAM role propagation). **Changed:**
  `.claude/skills/tf-change/SKILL.md`: state that window when summarising a replaced
  Lambda on an alert path.

## 2026-11-02

- **Problem:** the `vitals-alerts` spec (written by Claude) copied the routine heart-rate
  range 20–250 into the device `vitals_alert`, so a no-pulse emergency (0) would be
  rejected and never alert; the user caught it. **Changed:**
  `.claude/skills/feature-spec/SKILL.md`: readiness check that alert validation ranges
  accept the worst real reading.

- **Problem:** the `alerts-core` deploy failed: both SNS topic policies used `sns:*` in
  a TLS-only Deny, which SNS rejects at apply ("action out of service scope"); tf-check
  and both reviewers missed it. **Changed:** `.claude/skills/iam-policy/SKILL.md`: list
  explicit SNS topic actions, never `sns:*`.

- **Problem:** a "only fall-alert may send" Deny on `silvercare-alerts` (added on the
  2026-11-02 rule below) blocked DLQ redrive for every human, admins included
  (iac-reviewer, medium). **Changed:** `.claude/skills/iam-policy/SKILL.md`: no such
  Deny on actions operators need for recovery, e.g. sends into a redrive target.

- **Problem:** in `alerts-core`, queue and topic policies had Allow statements naming
  roles created in the same apply, which can fail the first apply with PrincipalNotFound
  (iac-reviewer, low). **Changed:** `.claude/skills/iam-policy/SKILL.md`: limit resource
  policies to our roles with a Deny on `aws:PrincipalArn`, no role-principal Allow.

- **Problem:** `test_main_ingest_output_has_no_payload_values_or_keys` flaked (~3%):
  it asserted `"999"` was absent from output that prints random UUIDs. **Changed:**
  `.claude/skills/lambda-test/SKILL.md`: strip random IDs or use fixed ones before
  "not in output" checks.
- **Problem:** on a Windows host without `make`, tests failed: CRLF checkouts broke
  `sh docker/run-lambda-tests.sh`, ruff EXE002 flagged every file, and port 8000 was
  taken. **Changed:** `.claude/skills/local-dev/SKILL.md`: Windows commands, LF
  scripts, port note. (Repo fixes outside `.claude/`: `.gitattributes`, `ruff.toml`.)
- **Problem:** Claude told the user to run `git checkout-index --force --all` to refresh
  line endings; it overwrote three uncommitted edits, which had to be redone.
  **Changed:** `.claude/skills/local-dev/SKILL.md`: fix CRLF per file, never suggest
  bulk checkout or reset while anything is uncommitted.

## 2026-11-01

- **Problem:** the rebuilt `seed-data` script crashed with `ImportError` on the user's
  macOS `python3` (3.9) instead of explaining the version; the README said `python3`.
  The privacy-reviewer found reserved IDs written down but not enforced, and device
  assignments overwritten with no ownership check (medium). **Changed:**
  `.claude/agents/backend-builder.md`: host-run scripts check Python 3.11+ first; seed
  scripts enforce reserved ID ranges and don't overwrite items they don't own. README
  host-script setup now states the Python version.

## 2026-10-30

- **Problem:** in `seed-data`, `--delete` found the seed's link items through the test
  users' current Cognito subs, so links of a user already deleted from Cognito were left
  behind (lambda-tester failure, privacy-reviewer low). The passwords file was rewritten,
  then truncated in place (privacy-reviewer low). The builder's scratch check ran
  `--delete` without `--passwords-file`, so cleanup code ran against the real default
  path in `secrets/` (no file existed). **Changed:** `.claude/agents/backend-builder.md`:
  scratch runs pass explicit scratch paths; script output files merge and write
  atomically; cleanup finds its items by its own data marker.

## 2026-10-25

- **Problem:** `make smoke-test` failed "publish status to own topic" against real AWS:
  each check reconnected as the same client ID within a second, and AWS IoT dropped the
  connection (the user's single-connection test got a PUBACK). **Changed:**
  `.claude/skills/iot-metric/SKILL.md`: device-side code waits 1 s between reconnects.
- **Problem:** Claude recommended Data Firehose for the S3 history path, then had to
  retract: Firehose and Kinesis are paid-plan only on AWS free-plan accounts.
  **Changed:** `.claude/skills/tf-change/SKILL.md` (check free-plan availability before
  proposing a new service) and `.claude/skills/data-model/SKILL.md` (telemetry goes to
  S3 through the history queue and Lambda).
- **Problem:** in `ingest-live`, the handler logged any string a device sent as
  `message_id`, before validation (privacy-reviewer, low). **Changed:**
  `.claude/skills/lambda-python/SKILL.md`: payload IDs are logged only if they match
  their format, otherwise as `invalid`.
- **Problem:** the `silvercare-iot-rules` trust policy had `aws:SourceAccount` but no
  `aws:SourceArn`, because the spec named only one (iac-reviewer, low). **Changed:**
  `.claude/skills/iam-policy/SKILL.md`: IoT rule roles get both, with the
  `rule/silvercare_*` pattern, whatever the spec lists.
- **Problem:** the `ingest-live` spec (written by Claude) put `aws:SourceAccount` on the
  Lambda execution roles, which AWS doesn't document for Lambda; an unsupported key
  would make creating the functions fail. Removed after the user agreed. **Changed:**
  `.claude/skills/iam-policy/SKILL.md`: Lambda execution roles have no trust conditions.
- **Problem:** the `ingest-history` spec (written by Claude) set the history queue's
  visibility timeout to 6 × the Lambda timeout, ignoring the 300 s batching window
  (iac-reviewer, medium). **Changed:** `.claude/skills/lambda-python/SKILL.md`: SQS
  trigger visibility timeout = 6 × timeout + batching window.
- **Problem:** not a mistake: the user dropped the live path's failed-message queue,
  which two skills required. **Changed:** `.claude/skills/lambda-python/SKILL.md` and
  `.claude/skills/iot-metric/SKILL.md`: on-failure destinations and error actions are
  the default unless a spec decides against it; anything raising alerts keeps one.

## 2026-10-24

- **Problem:** the `iot-core` README told the user to run `terraform output` on their
  machine, which had no Terraform: it only exists in the dev container, which has no
  AWS credentials. **Changed:** `.claude/skills/local-dev/SKILL.md`: README steps the
  user runs on the host say how to install the tool, at the pinned version.
- **Problem:** `pre-pr-check` compared against the local `main`, which was behind
  `origin/main`, so `frontend-tester` reviewed the already-merged frontend and all 9 of
  its findings were rejected as out of scope. **Changed:**
  `.claude/workflows/pre-pr-check.js` defaults the base to `origin/main`; the
  `iac-reviewer`, `privacy-reviewer`, `lambda-tester`, and `frontend-tester` agents and
  the `recap` skill diff against `origin/main` too.

## 2026-10-19

- **Problem:** in the `iot-core` build, `lambda-tester` was skipped because
  `backend-builder` reported absolute paths and the skip check only matched paths
  starting with `scripts/`. **Changed:** `.claude/workflows/build-feature.js` matches
  `lambdas/` or `scripts/` anywhere in the path.
- **Problem:** the `iot-core` builder assumed a `precondition` runs before `file()`
  (it doesn't, so the custom "run make device-keys" message never showed), and the spec
  assumed CI never prints outputs (deploy's `apply` prints non-sensitive ones; a
  reviewer caught it). **Changed:** `.claude/skills/tf-change/SKILL.md`: mark outputs
  sensitive unless publishable; guard `file()` with `fileexists()` and test on
  `terraform_data`.
- **Problem:** the `iot-core` spec needs developer scripts in `scripts/` with tests, but
  `backend-builder` could only write `infra/` and `lambdas/`, `lambda-tester` only
  `lambdas/*/tests/`, and the workflow skipped the tester when no Lambda changed. The
  build would have left half the spec undone. **Changed:**
  `.claude/agents/backend-builder.md` (may write `scripts/*.py` and `infra/devices/`;
  lists the files only Claude changes), `.claude/agents/lambda-tester.md` (may write
  `scripts/tests/`, only in `tmp_path`, no PEM fixtures), and
  `.claude/workflows/build-feature.js` (plans script tasks as backend, runs the tester
  when `scripts/` changed).

- **Problem:** a `LoginPage` test checked `document.title` (set in a `useEffect`) right
  after the page appeared. It passed locally and on the PR, then failed on the merge to
  `main`, which stopped the deploy. **Changed:** `.claude/agents/frontend-tester.md`:
  wrap effect results in `waitFor`, don't lower timeouts, and run the suite twice to
  catch flaky tests.

## 2026-10-18

- **Problem:** the `sign-in` spec and the builder let users edit their own `name`/`email`
  (required attributes are always writable; empty `write_attributes` means all writable).
  A reviewer had to catch it. **Changed:** `.claude/skills/cognito-auth/SKILL.md`, adding
  these Cognito rules, the "mark email verified" admin step, and `prevent_destroy` on the
  pool.
- **Problem:** both reviewers reported the user's recorded decision (deploy role has
  `AdministratorAccess`) as a finding. **Changed:** `.claude/agents/iac-reviewer.md` and
  `.claude/agents/privacy-reviewer.md`: recorded decisions are accepted risks, not
  findings.
- **Problem:** the `sign-in` spec had five screens plus Cognito, and the build took
  ~40 min. **Changed:** `.claude/skills/feature-spec/SKILL.md`: the size check is now a
  required step and a checklist item before `status: ready`.
- **Problem:** `lambda-tester` ran on Terraform-only changes with nothing it was allowed
  to test. **Changed:** `.claude/workflows/build-feature.js` skips it when no files
  under `lambdas/` changed.
