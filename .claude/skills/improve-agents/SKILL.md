---
name: improve-agents
description: After a bug is fixed or a step goes wrong, find what in the Claude setup (skills, spec template) let it happen and fix that instruction, without asking the user, within strict guardrails. Use after fixing a failing test, a review finding, a mistake the user corrected, a wasted or failed step, or an answer Claude had to retract. Runs before the recap.
---

# Improve agents

The user has asked for the Claude setup in `.claude/` to improve itself from its own
mistakes, without them approving each change. They still see every change in the diff
before they commit, and the recap lists what changed. Because nobody approves the edit
up front, the guardrails below are strict: when in doubt, don't edit; put the idea in
the recap under "Next with Claude" instead.

## When to run

After any of these, once the actual fix is done:

- A test failed and the source was fixed, or a review finding was confirmed and fixed.
- The user corrected Claude, or Claude retracted something it had said.
- A step was wasted, failed, or needed rework.
- A gap in a spec only got caught by a review or a test.

## Steps

1. **Find the root cause in the setup.** For each fixed problem, ask: which instruction
   caused it, or should have prevented it? Look in `.claude/skills/*/SKILL.md` and
   `.claude/skills/feature-spec/template.md`. If no instruction was involved (a one-off
   typo, a flaky run), stop here: there is nothing to improve.
2. **Check it would happen again.** Only change the setup for problems that would recur
   in a later feature. Don't add rules for one-off cases.
3. **Pick the right place** and make the smallest edit that would have prevented it:
   - wrong or missing domain knowledge (e.g. how Cognito treats attributes) → the skill
     for that area
   - wasted, missing, or badly ordered steps → the skill that describes those steps
   - specs missing something the build needed → `feature-spec` (template or checklist)
   Prefer fixing an existing sentence over adding a new one. Match the file's style.
4. **Verify the edit.**
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
  push" (unless the user asks), the no-AWS rule, privacy and PDPA rules, least privilege,
  cost rules, or "ask the user before product decisions".
- Don't add subagents or workflows back: the user removed them (2026-11-08).
- No product decisions. If the fix needs one (data collected, who sees what, alert
  thresholds, retention), don't edit; list it under "You" in the recap.
- At most a few focused edits per task. If the setup needs a bigger change (a new
  skill, a new `make` target), propose it in the recap rather than
  doing it.
