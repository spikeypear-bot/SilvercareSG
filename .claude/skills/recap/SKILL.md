---
name: recap
description: Recap the work just finished - what was done, what the user needs to check, and what still needs doing. Use at the end of every task (a feature build, a fix, a workflow run, a docs or config change), or when the user asks for a recap, status, or "where are we".
argument-hint: [scope, e.g. "sign-in" or "since main"]
---

# Recap

Scope: $ARGUMENTS (if empty: everything done on the current branch, plus anything done
in this conversation that isn't on the branch yet).

The user commits and pushes themselves, and every merge to `main` deploys to the live
AWS account. The recap is what they read before they decide to do that, so it must be
accurate. Report facts you've verified, not what you expect to be true.

## Gather first

Read these before writing. Don't run anything that changes files.

- `git branch --show-current`, `git status --short`, and `git diff --stat main...` plus
  the uncommitted diff, to see every changed and new file.
- The results of the agents and workflows that ran in this task: their deviations,
  open questions, untested items, findings left unfixed, and checks run.
- Specs touched in `docs/features/` (their `status`), and changes to `CLAUDE.md`,
  `docs/`, and `.claude/`.
- If you're unsure whether checks still pass, re-run them: `make check` for Python and
  Terraform, and `npm run lint`, `npm run test`, `npm run build` in `frontend/`.
  Say which checks you re-ran and which results come from earlier.

## Output

Four sections, in this order, written for someone who is learning. Keep each bullet to
one or two lines and use file paths the user can click. Skip a section's bullets only if
there's genuinely nothing, and say "Nothing" rather than dropping the heading.

### 1. Done

- What was built or changed, grouped by area: infra, Lambdas, frontend, docs and
  decisions, Claude setup (`.claude/`). One line each, with the main files.
- Product decisions made in this task, and where they're recorded.
- Which skills, agents, and workflows were used, and what each did (details in
  section 4).
- Checks: each command and its result (e.g. "`npm run test`: 266/266 pass").

### 2. Check

What the user should look at themselves before committing, most important first:

- **Expected plan.** Work out from the Terraform diff what the PR's plan comment
  should show (you can't run `terraform plan`), as a table:

  | Action | Resource address | Why |
  |---|---|---|
  | ➕ create | `aws_cognito_user_pool.users` | new user pool |

  List every 🟠 replace or 🔴 destroy you expect, and say explicitly when you expect
  none. Call out stateful resources (DynamoDB tables, S3 buckets, the Cognito pool) and
  settings that force replacement. Tell the user that any row in the real plan that
  isn't in this table is a reason to stop and ask before merging.
- **Cost.** For each AWS service or resource added or changed: whether it's in the
  always-free tier, the expected monthly cost at this project's scale (20 homes, a few
  users), and anything that charges a fixed hourly or monthly fee. End with the total
  expected monthly cost and how it compares with the ~US$120 of credits that must last
  until the end of November 2026 (see `CLAUDE.md`). Say where a price is from memory
  rather than checked, and point to Billing → Free Tier to confirm.
- Deviations from the spec, and choices an agent made where the spec was silent.
- Things no test covers, e.g. what only works in a real browser or real AWS (contrast,
  layout, the CSP, OAC, sign-in against real Cognito).
- Review findings that weren't fixed, with their severity.
- Privacy: any new personal data that is collected, shown, logged, or stored.

### 3. To do

Split into two lists:

- **You:** actions only the user can take: commit and open the PR, read the plan
  comment, merge, run things that need real AWS (console steps, `terraform output`,
  build uploads), answer open questions. Give exact commands where there are any.
- **Next with Claude:** follow-up work, open questions in specs, open decisions in
  `CLAUDE.md` this work touches, and the suggested next feature or step.

### 4. How the work was done

Learning notes, because the user is learning agents. For each skill, agent, and
workflow used in this task, one or two lines:

- what it is and what it did here (e.g. "`build-feature` workflow: ran 9 agents, with
  the backend and frontend builders in parallel")
- why it fit better than the alternatives (doing it directly, one subagent, a
  workflow), and when the user would pick something else
- anything that went wrong or was wasted (an agent with nothing to do, a failing step,
  a gap in a spec that a reviewer had to catch), and what would prevent it next time

If nothing was delegated, say so in one line and why it wasn't worth it.

## Rules

- Never say something passed unless you saw it pass in this task. If a check wasn't
  run, say so.
- Don't repeat full diffs, plans, or file contents. Point to the file.
- Never include secrets, tokens, real personal data, or AWS account details beyond what
  is already in `CLAUDE.md`.
- Don't commit, push, or edit files as part of a recap.
