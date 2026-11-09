# Agent setup changes

Changes the `improve-agents` skill made to skills, agents, and workflows after a bug or
a wasted step. Newest first. Review these in the diff before committing.

## 2026-11-09

- **Problem:** the WAF spec was marked `ready` with a CloudFront web ACL in `us-east-1`
  before checking the free plan's Region rules, which may block WAF on CloudFront; the
  user paused WAF. **Changed:** `.claude/skills/tf-change/SKILL.md` (free-plan rule):
  also check the project Regions page for anything outside `ap-southeast-1`, before `ready`.

- **Problem:** the backfill reload cleanup only looked in the new run's partitions,
  so old fall files on other days would have been kept and counted twice (caught
  before it was run). **Changed:** `.claude/skills/feature-spec/SKILL.md` (checklist):
  replacing a load deletes from every partition the earlier run could have written.
- **Problem:** backfill-variation's sizes were set without measuring their effect; the
  full run gave 100–400 alert episodes per resident per month and the user had them
  reduced. **Changed:** `.claude/skills/feature-spec/SKILL.md` (checklist): sizes for
  generated data come with the counts a quick run gives, before `ready`.
- **Problem:** the backfill-variation spec said ordinary residents' lines stay the same
  except message IDs, but `received_at` delays also come from a stream all lines share,
  so the test failed and the spec was corrected. **Changed:**
  `.claude/skills/feature-spec/SKILL.md` (checklist): "unchanged output" claims name
  the fields drawn from shared random streams.
- **Problem:** vitals-trends tests of `lambda_handler` used the real clock, so date
  checks failed until a `now_utc()` seam was added. **Changed:**
  `.claude/skills/lambda-test/SKILL.md` (Rules): date-dependent handlers read time
  through a patched `now_utc()`.
- **Problem:** the vitals-trends spec asked for a test that runs SQL, which the dev
  image can't (no Trino/Athena); it had to become a go-live check. **Changed:**
  `.claude/skills/feature-spec/SKILL.md` (checklist) and
  `.claude/skills/lambda-test/SKILL.md`: SQL runs belong in go-live checks.

- **Problem:** the EC2 simulator restarted every 60 s without running: tmux ran its
  command through the `simulator` user's `/sbin/nologin` shell. The template test checked
  arguments, not the environment. **Changed:** `.claude/skills/tf-change/SKILL.md`
  (Rules, templatefile): check the user and shell the commands run under.

- **Problem:** the EC2 simulator had no internet: the default VPC's network ACL has no
  inbound allow rule, and the spec only assumed the default VPC works. **Changed:**
  `.claude/skills/tf-change/SKILL.md` (Rules): check the default VPC's NACL, not just
  that it exists.

- **Problem:** CI's "No private keys in the repo" check failed on two new tests whose
  fake PEM fixtures held a literal private-key header. **Changed:**
  `.claude/skills/lambda-test/SKILL.md` (Data): never write a whole private-key header
  in a file; build it at runtime or generate a throwaway key.

- **Problem:** the EC2 simulator's boot script had `--endpoint <host>--keys-dir` (a
  dropped space); `make tf-check` doesn't render templates, so only a manual render
  caught it. **Changed:** `.claude/skills/tf-change/SKILL.md` (Rules): test every
  `templatefile()` by rendering it with fake values, checking commands and `bash -n`.

- **Problem:** the device-alert-limits deploy failed: listing each shadow reply topic
  took both IoT device policies past AWS IoT's 2048-character limit, which no local
  check catches. **Changed:** `.claude/skills/iam-policy/SKILL.md` (IoT device policies):
  the size limit, prefer one wildcard per request's replies, and the size test.

## 2026-11-08

- **User's request:** remove the agent workflows and agents. **Changed:** deleted
  `.claude/workflows/` (`build-feature`, `pre-pr-check`) and `.claude/agents/` (2
  builders, 2 testers, 2 reviewers); `CLAUDE.md` working rules and Claude setup, and the
  `improve-agents`, `recap`, `feature-spec` (+ template), `lambda-python`, and `frontend`
  skills now describe Claude building, testing, and reviewing itself. Entries below
  name files that no longer exist.
- **Problem:** the device-alert-limits spec changed `vitals_alert.limit` validation in
  ingest and clean's SQL but not clean's self-check cases, which would have failed the
  deploy's self-check. **Changed:** `.claude/skills/feature-spec/SKILL.md` checklist: a
  validation-rule change names every copy, including `lambdas/clean/src/cases.json`.
- **Problem:** during the alerts-map build, backend-builder ran `git stash` / `git stash
  pop` for a baseline test run, which reverted the frontend agents' files on disk for a
  few minutes while they worked in parallel. **Changed:**
  `.claude/workflows/build-feature.js` (`MANDATE`, sent to every agent): never run git
  stash, checkout, reset, or restore, because other agents share the working tree.
- **Problem:** the nightly-overview spec didn't say what happens to a Results item with
  unreadable thresholds; the build counted the whole night as "no summary", hiding a
  fall (the lambda tester flagged it; the user chose to flag it with `thresholds: null`).
  **Changed:** `.claude/skills/feature-spec/SKILL.md` (Alerting inputs): a list that
  surfaces residents needing attention says how it treats a partly unreadable item.
- **Problem:** the nightly-overview spec and build limited the new role's `by-type`
  Query to `pk`, `entity_type`, leaving out `sk`, which a second page's
  `ExclusiveStartKey` holds (the backend builder flagged it). **Changed:**
  `.claude/skills/iam-policy/SKILL.md` (DynamoDB attribute lists): for a paginated index
  Query, the allowed keys include the base table's `pk` and `sk`.
- **Problem:** the `simulator-backfill` spec (written by Claude) said generated history
  would flow into the daily summaries, but summaries drop readings before each device's
  `assigned_at` (the seed's run time), so loading it would have written months of empty,
  undeletable summaries (privacy-reviewer, high). **Changed:**
  `.claude/skills/feature-spec/SKILL.md` (readiness checklist): data made for another
  component is checked against that component's filters and what can't be undone once
  loaded. (Fix: seed's fixed `assigned_at`, load steps re-seed and check overlap.)
- **Problem:** the curated-layer go-live checklist said to clean only D−1 and D before a
  manual summaries run, but summaries also needs D−2 for `bed` and `vitals_alert`; the
  run failed with "clean partition missing". **Changed:**
  `.claude/skills/recap/SKILL.md` (To do → You): manual commands and checklists take
  their inputs and prerequisites from the defining code or doc, covering every
  precondition the job checks.
- **Problem:** the tableau-access spec said a live Tableau connection stores no data on
  the laptop (Tableau caches query results; a .twb can hold values), from memory; the
  privacy reviewer caught it. **Changed:** `.claude/skills/feature-spec/SKILL.md`
  (readiness checklist): check a third-party tool's behaviour in its own docs.
- **Problem:** the Tableau IAM user had a `path`, but its ARN is a Lake Formation
  principal; iac-reviewer flagged it. **Changed:** `.claude/skills/iam-policy/SKILL.md`:
  no path on users or roles that get Lake Formation grants.
- **Problem:** with polling added, an acknowledge followed by a resolve showed the alert
  as open again (the second action's override replaced the first's starting status);
  the resident page tests caught it. **Changed:** `.claude/skills/frontend/SKILL.md`
  (Rules): show action results through the data hooks' overrides, never by writing
  into polled data, and hold refreshes while a form is open.

## 2026-11-07

- **Problem:** the nightly-results section added a second `role="status"` to the
  resident page, breaking a test that found the page's announcer as the only status
  region; frontend-tester fixed the helper. **Changed:**
  `.claude/skills/frontend/SKILL.md`: find the announcer by text or container.
- **Problem:** frontend-tester saved raw zero-width and bidi characters into
  `ackNote.test.ts` and `ResolveNoteForm.test.tsx` instead of `\u` escapes (oxlint
  doesn't flag them). **Changed:** `.claude/agents/frontend-tester.md`: write them as
  escapes and search test files for raw ones before reporting, as lambda-test does.
- **Problem:** the resolve note hint said "kept in the resident's record" without the
  retention; the privacy reviewer caught it. **Changed:**
  `.claude/skills/frontend/SKILL.md` (Privacy): a free-text hint names who receives it
  and how long it is kept, from docs/data-model.md.
- **Problem:** 83 summaries tests errored in the dev container: they used moto's Glue,
  which needs `pyparsing`, and the image installs moto only for DynamoDB, S3, Cognito.
  **Changed:** `.claude/skills/lambda-test/SKILL.md`: use moto only for those services;
  fake the rest (Athena, Glue) with a small recording client.
- **Problem:** build-feature skipped lambda-tester for curated-layer: its "did the
  backend change a Lambda?" check matched only `/` paths, and the builder reported
  Windows `\` paths. **Changed:** `.claude/workflows/build-feature.js`: normalise
  backslashes before the check.
- **Problem:** the summaries role could Query the registry `by-type` index with no
  conditions (the index projects names and photo keys), copied a peer's Glue `table/*`
  wildcard, and its `summaries/` Deny left tagging open; the reviewers caught all three.
  **Changed:** `.claude/skills/iam-policy/SKILL.md`: index Queries on personal data
  need `dynamodb:Attributes` + `dynamodb:Select` conditions; don't copy peer wildcards;
  tag-reliant prefix Denies also deny tagging.
- **Problem:** `node --check` on a workflow always fails (top-level `return`), so the
  improve-agents verify step couldn't pass. **Changed:**
  `.claude/skills/improve-agents/SKILL.md`: wrap the script before checking.
- **Problem:** `api-residents`' read statements listed allowed attributes with
  `ForAllValues`, which passes when no projection is sent, so the role could read
  `medical_notes` and emergency contacts; the user caught it.
  **Changed:** `.claude/skills/iam-policy/SKILL.md`: pair attribute lists with
  `Null dynamodb:Attributes = false` on every read statement that hides fields.
- **Problem:** `GET /dashboard` turned skin temperature into a float before comparing it
  with a Decimal limit (36.6 at a 36.6 limit read as abnormal), and a list value in an
  alert item crashed a set lookup into a 500; lambda-tester caught both.
  **Changed:** `.claude/skills/lambda-python/SKILL.md`: type-check raw item values before
  set lookups; compare numbers as Decimal, convert to float only for the response.
- **Problem:** the stage throttle was assumed to cap the whole API, but
  `default_route_settings` applies per route, so each new route added load on the
  shared indexes; iac-reviewer caught it. **Changed:**
  `.claude/skills/api-route/SKILL.md`: routes reading a shared index get their own
  `route_settings`.
- **Problem:** the first name lookup used Cognito `ListUsers`, which lets the role list
  every user's email; iac-reviewer caught it. **Changed:**
  `.claude/skills/cognito-auth/SKILL.md`: look names up with `AdminGetUser` by `sub`.
- **Problem:** the API's CORS-origin guard was a `check` block, whose warning never
  reaches the PR comment (plan stdout is discarded); the iac-reviewer caught it.
  **Changed:** `.claude/skills/tf-change/SKILL.md`: guards that must be seen before
  merging are preconditions (an output precondition adds no cycle), never checks.
- **Problem:** the first API stage was throttled to expected traffic (10 rps, burst
  20), enough to use up shared Lambda concurrency and `by-type`'s 2 RCU; and the
  handler's logs had no request ID to join with the access log. Reviewers caught both.
  **Changed:** `.claude/skills/api-route/SKILL.md`: size throttling to shared
  concurrency and index RCU; new handler step 7, log `requestContext.requestId`.

- **Problem:** after merging main, the seed tests failed with `ModuleNotFoundError`
  because main had changed `requirements-dev.txt` and the local dev image was older.
  **Changed:** `.claude/skills/local-dev/SKILL.md` (Changing the toolbox): run
  `make build` after a pull or merge that changes `requirements-dev.txt` or `docker/`.

## 2026-11-06

- **Problem:** the demo bed data treated a possible fall as open with
  `status !== 'resolved'`, so a cancelled one still showed the resident out of bed
  (found by frontend-tester, no test covered it). **Changed:**
  `.claude/skills/frontend/SKILL.md` (Rules): decide "still open" with `isOnOpenList`,
  never by excluding `resolved`.

- **Problem:** integration tests couldn't start DynamoDB Local because host port 8000
  was taken by another project's container, and the first workaround failed (a
  `docker compose run` container has no `dynamodb-local` alias). **Changed:**
  `.claude/skills/local-dev/SKILL.md` (Windows paragraph): run an unpublished DynamoDB
  Local by name and point `DYNAMODB_ENDPOINT` at it.
- **Problem:** the clean job's SQL passed on local Trino but rejected every valid line on
  real Athena, with `try()` hiding the error. **Changed:**
  `.claude/skills/lambda-test/SKILL.md` (Rules): local Trino isn't proof, so the owner runs
  new functions on real Athena before merging.
- **Problem:** a `data` lookup of an IAM group that `CLAUDE.md` said existed failed the PR
  plan, because the group had never been made. **Changed:**
  `.claude/skills/tf-change/SKILL.md` (Rules): the owner confirms such names in AWS before
  the first push, since docs aren't proof.
- **Problem:** three clean-data gaps: a Lake Formation grant on a `default` database that
  didn't exist would have failed the apply, the deploy plan step left stderr unredacted in
  a public log, and the plan role could probably read Athena query text. **Changed:**
  `.claude/skills/tf-change/SKILL.md` (Rules): confirm or create names Terraform doesn't
  create, redact stderr in workflow steps, extend `DenyDataReads` for new data services.

- **Problem:** on a Windows host, `ruff` in the dev container failed with `Permission
  denied` writing `.ruff_cache` on the mounted repo, so lint looked broken. **Changed:**
  `.claude/skills/local-dev/SKILL.md` (Windows host): run ruff with `--no-cache`.

## 2026-11-05

- **Problem:** two skills told builders the frontend sends the Cognito access token to
  the API, but the API needs the `name` claim, which only the ID token has (user's
  correction). **Changed:** `.claude/skills/cognito-auth/SKILL.md` (API authorizer) and
  `.claude/skills/frontend/SKILL.md` (API calls): send the ID token via `getApiToken()`.
- **Problem:** Lambda test fixtures created every GSI with `ProjectionType: ALL`, so a
  handler reading an attribute the real index doesn't project could never fail a test
  (user's correction). **Changed:** `.claude/skills/lambda-test/SKILL.md` (conftest
  rules): fixtures mirror each index's projection from `infra/dynamodb.tf` via
  `tf_projection()`, never `ALL`.

## 2026-11-07

- **Problem:** in `staff-resolve`, the follow-up email used the incoming message's note,
  so a second resolve from the shared nurse account (treated as a retry) could email
  caregivers a note the stored record doesn't hold (found by privacy-reviewer).
  **Changed:** `.claude/skills/lambda-python/SKILL.md`: on a retry, side effects come
  from the stored item. (Fix: `_stored_note` in alert-processor.)

- **Problem:** resolving an acknowledged alert kept the acknowledge's
  `status_notified_at`, so a retry after a failed email or S3 write saw it as done and
  lost both (found by lambda-tester). **Changed:** `.claude/skills/lambda-python/SKILL.md`:
  a "done" marker reused across steps is cleared when the next step starts. (Fix: the
  resolve also REMOVEs `status_notified_at`, as the cancel did.)

## 2026-11-04

- **Problem:** the `simulator-alert-scenarios` spec (written by Claude) left two timing
  edges open: a flag at the previous scenario's end was skipped as `busy` (flags ran
  before the same-time end; lambda-tester found it), and messages still scheduled at
  `--duration` were silently lost, so an "I'm OK" could print `started` and never send
  (privacy-reviewer, low). It also promised one email per 30 min, ignoring escalation
  and never-suppressed falls (privacy-reviewer, medium). **Changed:**
  `.claude/skills/feature-spec/SKILL.md`: a checklist item for same-moment ordering
  and work pending at stop, and live tools count emails from all the alert specs.
  (Fix: ends sort first, `cut off: run ended` lines, README and spec warning.)

- **Problem:** the `simulator-core` spec (written by Claude) said normal data raises no
  alert, but only if `seed.py` thresholds are in the live registry; with only
  `seed-registry.py` data, two devices fall outside the cloud's defaults and nurses get
  emails (privacy-reviewer, medium). **Changed:** `.claude/skills/feature-spec/SKILL.md`:
  anything sending data to the live system names the registry data it relies on.
  (Fix: precondition in the spec and README.)

- **Problem:** the lambda-tester temporarily edited ingest's source to prove a test, then
  restored it. **Changed:** `.claude/agents/lambda-tester.md`: no source edits even
  temporarily; use `monkeypatch` or a copy under `tmp_path`.

- **Problem:** the clean-data spec draft misstated existing accelerometer validation,
  repeated an AWS role limit from memory, had a pointer to a missing open question, and
  had an erasure plan that didn't fit batched raw files; only the design check caught
  them. **Changed:** `.claude/skills/feature-spec/SKILL.md` (readiness checklist): verify
  claims in running code and AWS docs, match pointers, and check erasure against storage.

- **Problem:** twice, Claude's inline Python heredoc that edited TypeScript tests failed
  with "unexpected EOF" because the script contained backticks (template literals), and
  had to be redone. **Changed:** `.claude/skills/local-dev/SKILL.md` (`make lint` row):
  write such scripts to the scratchpad and run them as a file.

- **Problem:** the `alert-actions-ui` note check (spec and code by Claude) followed the
  data-model summary "Unicode category C, e.g. ... U+2028/U+2029", but those two are
  categories Zl/Zp, so the form let them through while `alert-processor` refuses them;
  Claude's own new tests caught it. **Changed:** `.claude/skills/frontend/SKILL.md`:
  a field the backend also validates copies the backend's code and its test cases.
- **Problem:** removing the family test user from `seed-data`, the first version deleted
  the retired `test-family` account by username alone, and pruned old seed links with an
  unconditional batch delete after reading them, so an account or link a teammate had
  since taken over could be deleted (privacy-reviewer, low ×2). **Changed:**
  `.claude/agents/backend-builder.md`: scripts check at delete time that what they
  delete is still as they left it. (Fix: name and group check, conditional deletes.)

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

- **Problem:** in `possible-fall`, a back-in-bed resolve used the bedroom unit's data
  without checking the unit was still the alert's resident's, and "I'm OK" used a
  5-minute cached device → resident lookup, so after a reassignment one resident's data
  could close (and email about) another's alert (found by privacy-reviewer).
  **Changed:** `.claude/skills/feature-spec/SKILL.md`: checklist item on device
  reassignment for anything acting on alerts with device data. (Fix: resident check in
  the watchdog, uncached lookup for `alert_cancel`.)

- **Problem:** for `watchdog`, Claude offered the user only "unencrypted ops topic" or
  "paid KMS key"; privacy-reviewer pointed out the free option (a separate `aws/sns`
  topic for Lambda emails, alarms on their own topic). **Changed:**
  `.claude/skills/tf-change/SKILL.md`: the encryption rule names that option.

- **Problem:** the `watchdog` Lambda had only an `Errors` alarm, which can't notice a
  stopped schedule (found by iac-reviewer). **Changed:**
  `.claude/skills/lambda-python/SKILL.md`: scheduled functions also get an
  `Invocations < 1` alarm with missing data breaching.

- **Problem:** the spec added an `ops` group without saying which existing read
  patterns it may use or the API's check order, so Track B could have shown ops every
  health alert or leaked an alert's existence through 409 vs 404 (found by
  privacy-reviewer). **Changed:** `.claude/skills/feature-spec/SKILL.md`: the access
  checklist item covers new groups and the check order.

- **Problem:** backend-builder ran a repo-wide `ruff format` while lambda-tester was
  writing tests in parallel. **Changed:** `.claude/agents/backend-builder.md`: format
  only its own files when a tester may be running.

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

- **Problem:** in `simulator-core`, the simulator defaulted `--keys-dir` to the device
  keys folder, so a live run never named the path, and the new guard rule (block
  `simulator/src/main.py` without `--dry-run`) could be bypassed with `cd`, `-m main`, or
  a stray `--dry-run` (privacy-reviewer, high). **Changed:**
  `.claude/agents/backend-builder.md`: scripts take secret locations as a required
  argument, never a default. `.claude/hooks/guard.sh` (setup change the user approved for
  this feature): block `--endpoint` next to any way of starting the simulator, and
  `gmake`; tested against 22 cases.

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
