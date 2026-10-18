---
name: improve-agents
description: After a bug is fixed or a step goes wrong, find what in the Claude setup (skills, agents, workflows, spec template) let it happen and fix that instruction, without asking the user, within strict guardrails. Use after fixing a failing test, a reviewer finding, a mistake the user corrected, a wasted or failed workflow step, or an answer Claude had to retract. Runs before the recap.
---

# Improve agents

The user has asked for the Claude setup in `.claude/` to improve itself from its own
mistakes, without them approving each change. They still see every change in the diff
before they commit, and the recap lists what changed. Because nobody approves the edit
up front, the guardrails below are strict: when in doubt, don't edit; put the idea in
the recap under "Next with Claude" instead.

## When to run

After any of these, once the actual fix is done:

- A test failed and the source was fixed, or a reviewer finding was confirmed and fixed.
- The user corrected Claude, or Claude retracted something it had said.
- A workflow step was wasted (an agent with nothing to do), failed, or needed rework.
- A gap in a spec only got caught by a reviewer or a tester.

## Steps

1. **Find the root cause in the setup.** For each fixed problem, ask: which instruction
   caused it, or should have prevented it? Look in `.claude/skills/*/SKILL.md`,
   `.claude/agents/*.md`, `.claude/workflows/*.js`, and
   `.claude/skills/feature-spec/template.md`. If no instruction was involved (a one-off
   typo, a flaky run), stop here: there is nothing to improve.
2. **Check it would happen again.** Only change the setup for problems that would recur
   in a later feature. Don't add rules for one-off cases.
3. **Pick the right place** and make the smallest edit that would have prevented it:
   - wrong or missing domain knowledge (e.g. how Cognito treats attributes) → the skill
     for that area
   - an agent doing the wrong job, or blocked by its file scope → that agent's file
   - wasted, missing, or badly ordered steps → the workflow script
   - specs missing something builders needed → `feature-spec` (template or checklist)
   Prefer fixing an existing sentence over adding a new one. Match the file's style.
4. **Verify the edit.**
   - Workflows: `node --check .claude/workflows/<name>.js`, and re-read the changed part.
   - Anything that changes what a `make` target runs: run that target.
   - Re-read the whole edited section to check it doesn't contradict the rest of the
     file or `CLAUDE.md`.
5. **Log it** in `.claude/agent-changes.md` (newest first): date, the problem in one
   line, the file changed, and the change in one line. No personal data, secrets, or
   plan output: the repo is public.
6. **Report it** in the recap's section 4 ("How the work was done").

## Guardrails

Never change these without the user asking for it in their own words:

- `.claude/settings.json` (permissions, environment, hooks config), anything in
  `.claude/hooks/`, `.github/`, `docker/`, `Makefile`, `infra/bootstrap/`.
- `CLAUDE.md` working rules, architecture, budget, or decisions. You may only fix a
  factual description of the Claude setup (the "Claude setup" section) so it matches
  what exists.
- Anything that makes the setup less safe: never remove or weaken "never commit or
  push", read-only reviewers, the no-AWS rule, privacy and PDPA rules, least privilege,
  cost rules, or "ask the user before product decisions".
- File scopes: an agent's allowed files may grow only within its role (a tester gains
  test files, a builder gains source files). Never give a reviewer write access, and
  never let any agent edit `.claude/`, `.github/`, `infra/bootstrap/`, or credentials.
- No product decisions. If the fix needs one (data collected, who sees what, alert
  thresholds, retention), don't edit; list it under "You" in the recap.
- Don't edit an agent, skill, or workflow while a workflow that uses it is running.
  Wait until it finishes.
- At most a few focused edits per task. If the setup needs a bigger change (a new
  agent, a new workflow phase, a new `make` target), propose it in the recap rather than
  doing it.
