# Agent setup changes

Changes the `improve-agents` skill made to skills, agents, and workflows after a bug or
a wasted step. Newest first. Review these in the diff before committing.

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
