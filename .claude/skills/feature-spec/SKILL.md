---
name: feature-spec
description: Help the user write or review a feature spec in docs/features/<name>.md, the input Claude builds the feature from. Use when the user wants to plan, spec, or describe a new feature, or asks whether a spec is ready to build.
argument-hint: [feature-name]
---

# Feature specs

Feature: $ARGUMENTS

Claude builds the feature from this spec, often in a later session without this
conversation. If something is missing from the spec, it gets left out or guessed, so the
spec has to answer every question up front.

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

- [ ] Every acceptance criterion is testable: a tester could write a pass/fail test for it
      in the dev image. Athena SQL can't run there (only its text is tested), so a check
      that needs a query run is a go-live check for the owner, not a test criterion.
- [ ] Anything the spec says about existing behaviour was checked in the code that runs it,
      not a comment or memory, and anything about AWS behaviour in AWS's docs, and
      about a third-party tool (what it stores on the user's machine, which edition
      has which feature) in that tool's docs. Every "see Open questions" pointer has a
      matching item. Data made for another component (generated history, imports,
      fixtures loaded live) is checked against every filter that component's code
      applies (e.g. `assigned_at`, partitions it requires, de-duplication), and against
      what can't be undone once loaded (outputs with no expiry or delete access).
      A claim that some generated output stays unchanged names which fields still
      change because they come from a random stream every line shares (IDs, delays).
      Sizes for generated data (noise, drift, event rates) come with the counts they
      give (e.g. alert episodes per month), measured on a quick run, before `ready`.
      Replacing an earlier load deletes from every partition the earlier run could
      have written, not only the new run's (rare events land on other days).
- [ ] Each role's access is stated: which groups can see or do what, and how access to a
      **specific resident** is decided. A new group (e.g. `ops`) also lists which
      existing access patterns in `docs/data-model.md` it gets and which it's refused,
      and the API's check order (group → link → read → type rule), so an error code
      can't reveal that a record the caller may not see exists.
- [ ] When a group gains a new action (e.g. caregivers acknowledge), the spec says what
      other users see about who did it (name, role, or nothing): that's a who-sees-what
      decision for the user.
- [ ] A message or contract another component sends (e.g. a queue message from Track B's
      API carrying who did something) states the checks the **sender** must make
      (identity from the verified token, group, resident link): the receiver can't.
      The sender also validates any user-typed field with the receiver's rules and
      returns `400` (otherwise it fails later in a DLQ with no error for the user), and
      never logs request bodies.
- [ ] A changed delay, retry, or timeout is rechecked against the other timings it
      interacts with (stale cutoffs, visibility timeout × receive count, DLQ alarms).
- [ ] Anything that acts on a resident's alert using **device** data (a resolve, a
      cancel, a follow-up) says what happens after the device is reassigned: data from
      the new resident must not close or be emailed about the old resident's alert,
      including through cached device → resident lookups.
- [ ] Live checks (smoke tests against AWS) don't rely on shared data teammates can
      change (e.g. thresholds: use values that fail any sensible limit), and space out
      messages whose order matters (sent together, they run in parallel). Anything that
      sends data to the live system (smoke tests, the simulator) names the registry data
      it relies on (e.g. "run `seed.py` first") and what happens without it, and
      counts the emails it causes from the alert specs (escalation, alerts never
      suppressed), not from one rule.
- [ ] Timed behaviour says what happens at its edges: events due at the same moment
      (which runs first), and work still scheduled when the run or process stops (sent,
      dropped, or reported).
- [ ] Every API route has method, path, groups, a request example, a response example
      per role, and error cases.
- [ ] Every screen lists what each role sees, plus loading, empty, and error states.
- [ ] Data reads and writes map to access patterns in `docs/data-model.md` (or new ones
      are described).
- [ ] A change to a message's validation rules names every copy of the rule: ingest's
      `validation.py`, clean's `sql.py`, and clean's self-check cases
      (`lambdas/clean/src/cases.json`), which the deploy runs against real Athena.
- [ ] Alerting inputs: validation ranges don't reject the emergency itself. Ranges
      copied from routine readings can (heart rate 0 = no pulse, cardiac arrest); for
      each alert, check the worst real reading is still accepted. Likewise, a list that
      surfaces residents needing attention says what happens to a stored item it can't
      fully read: one bad field (e.g. a label) mustn't hide a fall or alert in the rest.
- [ ] Personal data touched is listed, with its retention, and how one person's data would
      be erased, checked against how it's really stored (e.g. files batching many devices).
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
