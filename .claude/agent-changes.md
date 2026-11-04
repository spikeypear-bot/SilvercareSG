# Agent setup changes

Changes the `improve-agents` skill made to skills, agents, and workflows after a bug or
a wasted step. Newest first. Review these in the diff before committing.

## 2026-11-04

- **Problem:** renaming `FamilyView` to `CaregiverView`, Claude ran a Python edit script
  and an `rm` of the originals on separate lines of one shell call; the script failed,
  the `rm` still ran, and `FamilyView.test.tsx` (never read by the main session) was
  lost and had to be recreated by the frontend-tester. **Changed:**
  `.claude/skills/local-dev/SKILL.md` (`make lint` row): never put a delete after a
  script in the same call; create, check, then delete separately.

- **Problem:** while adapting the frontend to `alert-routing`, Claude exported a helper
  (`sentToNurseAtOnce`) from `AlertRow.tsx` (lint `only-export-components`, found by
  frontend-tester), and the resident page began showing `ack_note` and caregiver names
  without its spec's Privacy section saying so (privacy-reviewer, low). **Changed:**
  `.claude/skills/frontend/SKILL.md`: component files export only components; new
  personal data on a screen updates that spec's Privacy section in the same change.

- **Problem:** `alert-routing` raised the escalation delay from 60 s to 300 s but kept
  the 15-minute stale cutoff, so a check failing on every try would be dropped as stale
  and never reach the DLQ alarm (found by iac-reviewer). **Changed:**
  `.claude/skills/lambda-python/SKILL.md`: the stale cutoff must sit above the last
  retry and be rechecked when the delay changes; `.claude/skills/feature-spec/SKILL.md`:
  checklist item to recheck changed timings against the others. (Fix: cutoff 20 min.)

- **Problem:** the nurse's free-text note, emailed to caregivers, accepted bidi,
  zero-width, and U+2028 characters and could imitate the email's own lines (found by
  privacy-reviewer). **Changed:** `.claude/skills/lambda-python/SKILL.md`: rules for
  free text in emails (reject category C except `\n` and U+2028/2029; quote it).

- **Problem:** the spec's sender checks for Track B didn't say to validate the note
  before sending (a bad note would sit in the DLQ, no error for the nurse) or to never
  log request bodies. **Changed:** `.claude/skills/feature-spec/SKILL.md`: the
  sender-checks item now includes both.

- **Problem:** on two merges of `main` into `frontend-views`, Claude's proposed access
  patterns (21/22, then 24/25) clashed with numbers main's alerting branches had taken,
  and each merge meant renumbering docs, specs, and code comments. **Changed:**
  `.claude/skills/data-model/SKILL.md` step 2: number after `origin/main`'s highest,
  refer to unmerged patterns by name in code, and on a clash keep main's number.

## 2026-11-03

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

- **Problem:** while resolving the merge, Claude edited 11 files with Python scripts on
  the Windows host, and text-mode writes turned their LF line endings into CRLF, against
  `.gitattributes`. **Changed:** `.claude/skills/local-dev/SKILL.md`: keep LF; on
  Windows open files with `newline=''` or in binary, or use Edit/Write.

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

- **Problem:** twice the screen-reader text lost a space between JSX elements in a flex
  row ("Wearable· id", "12%· Low"), and a charging state was shown only by a hidden
  icon. The tester caught both. **Changed:** `.claude/skills/frontend/SKILL.md`: states
  need text, not just colour or a hidden icon; one-phrase text goes in one inline element.

- **Problem:** Claude added staff use of patterns 4, 5, and 8 for the devices view
  without saying which fields the route returns, though the indexes project more
  (`photo_key`, `worn`, ...). The privacy reviewer caught it. **Changed:**
  `.claude/skills/data-model/SKILL.md` step 2: for a user-facing route, note the exact
  fields returned and the projected attributes dropped.

- **Problem:** twice a frontend test run gave wrong counts (21 worker errors; 25 of 26
  files) because it overlapped another run: the tester's tests with its lint, and
  Claude's run with the tester's. **Changed:** `.claude/skills/frontend/SKILL.md`: run
  the checks one after another, never alongside another test run.

- **Problem:** Claude's proposed pattern 21 (doctor edits thresholds) used `UpdateItem`
  without `attribute_exists(pk)`, so it could create orphan items for mistyped or
  offboarded residents; it also didn't reject unknown body keys or say the actor is
  returned as a name. The privacy reviewer caught it. **Changed:**
  `.claude/skills/data-model/SKILL.md` (updates to existing items use
  `attribute_exists(pk)`) and `.claude/skills/api-route/SKILL.md` (accept only
  documented body keys and build the item; return `*_by` as a name, never the `sub`).

- **Problem:** four "live region is quiet" assertions used `toHaveTextContent('')`,
  which passes for any text, so they tested nothing. **Changed:**
  `.claude/agents/frontend-tester.md`: use `toBeEmptyDOMElement()` for emptiness.

- **Problem:** escalation announcements ran inside alert rows, so they were lost while
  the nurse viewed another panel. The tester caught it. **Changed:**
  `.claude/skills/frontend/SKILL.md`: one live region per page, fed from the data, and
  no ticking lists inside live regions.

- **Problem:** the nurse dashboard's acknowledge/resolve hook took the staff name from
  the client and was documented as the future API seam, inviting an API that trusts a
  client-chosen actor. The privacy reviewer caught it. **Changed:**
  `.claude/skills/api-route/SKILL.md` (handler takes `*_by` actors from the `sub` claim,
  ignores the body) and `.claude/skills/frontend/SKILL.md` (never send who did an
  action; a name shown early is display only).

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
