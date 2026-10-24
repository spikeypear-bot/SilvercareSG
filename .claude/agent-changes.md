# Agent setup changes

Changes the `improve-agents` skill made to skills, agents, and workflows after a bug or
a wasted step. Newest first. Review these in the diff before committing.

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
