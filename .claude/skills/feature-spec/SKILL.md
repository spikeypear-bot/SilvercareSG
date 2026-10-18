---
name: feature-spec
description: Help the user write or review a feature spec in docs/features/<name>.md, the input the build-feature workflow builds from. Use when the user wants to plan, spec, or describe a new feature, or asks whether a spec is ready to build.
argument-hint: [feature-name]
---

# Feature specs

Feature: $ARGUMENTS

The `build-feature` workflow sends this spec to agents that can't ask questions. If
something is missing from the spec, they'll either leave it out or guess, so the spec
has to answer every question up front.

The user owns the product decisions. Your job is to ask questions, structure the
answers, and point out gaps. Don't fill gaps with your own product decisions.

## Steps

1. If `docs/features/<name>.md` doesn't exist, create it from [template.md](template.md)
   with `status: draft`. `<name>` is kebab-case (`resident-status`, `fall-alerts`).
2. Read `CLAUDE.md`, `docs/metrics.md`, `docs/data-model.md`, and the other specs in
   `docs/features/`, so the spec matches what exists.
3. Go through the template section by section. Ask the user about anything you can't
   answer from what they've told you. Ask a few questions at a time, not all at once.
4. Write their answers into the spec. Record anything still undecided under
   **Open questions**.
5. Check the spec against the readiness checklist below and tell the user what's missing.
6. Set `status: ready` only when the user agrees and the checklist passes.

## Readiness checklist

- [ ] Every acceptance criterion is testable: a tester could write a pass/fail test for it.
- [ ] Each role's access is stated: which groups can see or do what, and how access to a
      **specific resident** is decided.
- [ ] Every API route has method, path, groups, a request example, a response example
      per role, and error cases.
- [ ] Every screen lists what each role sees, plus loading, empty, and error states.
- [ ] Data reads and writes map to access patterns in `docs/data-model.md` (or new ones
      are described).
- [ ] Personal data touched is listed, with its retention.
- [ ] Out of scope is filled in.
- [ ] Open questions is empty.
- [ ] Size: at most about two screens and two API routes, or the user agreed to the size.

## Keep features small

A feature that's small enough to review in one sitting is the right size: roughly one
or two API routes and one or two screens. If a spec grows past that, suggest splitting
it into several features that can be built one at a time.

This is a required check before `status: ready`, not a suggestion: count the screens
(each route, including settings pages and sub-steps that have their own route) and API
routes. If it's over, propose a split to the user and get their answer first. Example:
the first `sign-in` spec had Cognito plus five screens and took about 40 minutes to
build; it would have been better as Cognito + login, then account settings, then the
dashboard.
