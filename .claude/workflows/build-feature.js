export const meta = {
  name: 'build-feature',
  description: 'Build one feature from docs/features/<name>.md: plan, build backend + frontend in parallel, test, review, fix once, report. Never commits.',
  whenToUse: 'When a feature spec has status: ready and the user asks to build it. Args: "<feature-name>" or { feature: "<feature-name>" }',
  phases: [
    { title: 'Plan', detail: 'check the spec is ready and split it into backend and frontend tasks' },
    { title: 'Build', detail: 'backend-builder and frontend-builder in parallel' },
    { title: 'Test', detail: 'lambda-tester and frontend-tester write and run tests' },
    { title: 'Review', detail: 'iac-reviewer and privacy-reviewer on all changes' },
    { title: 'Fix', detail: 'builders fix confirmed high/medium issues and failing tests, once' },
  ],
}

const FEATURE = typeof args === 'string' ? args : args && args.feature
if (!FEATURE) {
  return { error: 'No feature given. Pass the spec name, e.g. "resident-status" for docs/features/resident-status.md.' }
}
const SPEC = `docs/features/${FEATURE}.md`

// Tells each agent the run was user-requested and keeps it inside its own files.
const MANDATE = `The user explicitly started the build-feature workflow for ${SPEC}. That is their request to implement this feature, limited to the files your agent definition allows. Do not commit or push.`

const STRINGS = { type: 'array', items: { type: 'string' } }

const PLAN = {
  type: 'object',
  properties: {
    ready: { type: 'boolean' },
    problems: { ...STRINGS, description: 'Why the spec is not ready to build (empty if ready)' },
    summary: { type: 'string' },
    acceptance_criteria: STRINGS,
    backend_tasks: { ...STRINGS, description: 'Concrete backend tasks; empty if the feature needs no backend work' },
    frontend_tasks: { ...STRINGS, description: 'Concrete frontend tasks; empty if the feature needs no frontend work' },
  },
  required: ['ready', 'problems', 'summary', 'acceptance_criteria', 'backend_tasks', 'frontend_tasks'],
}

const BUILD = {
  type: 'object',
  properties: {
    stopped: { type: 'boolean', description: 'true if you could not build (e.g. frontend app not set up)' },
    files_changed: STRINGS,
    summary: { type: 'string' },
    deviations: STRINGS,
    open_questions: STRINGS,
    checks_run: STRINGS,
  },
  required: ['stopped', 'files_changed', 'summary', 'deviations', 'open_questions', 'checks_run'],
}

const TEST = {
  type: 'object',
  properties: {
    tests_added: STRINGS,
    checks_run: STRINGS,
    failures: {
      type: 'array',
      items: {
        type: 'object',
        properties: { test: { type: 'string' }, file: { type: 'string' }, problem: { type: 'string' } },
        required: ['test', 'problem'],
      },
    },
    untested: STRINGS,
  },
  required: ['tests_added', 'checks_run', 'failures', 'untested'],
}

const FINDINGS = {
  type: 'object',
  properties: {
    checks_run: STRINGS,
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['high', 'medium', 'low'] },
          file: { type: 'string' },
          line: { type: 'integer' },
          issue: { type: 'string' },
          fix: { type: 'string' },
        },
        required: ['severity', 'file', 'issue', 'fix'],
      },
    },
  },
  required: ['checks_run', 'findings'],
}

const FIX = {
  type: 'object',
  properties: {
    fixed: STRINGS,
    skipped: {
      type: 'array',
      items: {
        type: 'object',
        properties: { issue: { type: 'string' }, reason: { type: 'string' } },
        required: ['issue', 'reason'],
      },
    },
    files_changed: STRINGS,
    checks_after: { ...STRINGS, description: 'Tests and checks re-run after fixing, with results' },
  },
  required: ['fixed', 'skipped', 'files_changed', 'checks_after'],
}

const bullets = xs => xs.map(x => `- ${x}`).join('\n')

// ---------- Plan ----------
phase('Plan')
const plan = await agent(
  `Read the feature spec ${SPEC}, plus CLAUDE.md, docs/metrics.md, and docs/data-model.md if they exist. Read-only: do not edit any files.

Decide whether the spec is ready to build. It is NOT ready if: the file doesn't exist, its frontmatter status isn't "ready", Open questions isn't empty, or an acceptance criterion, API route, or screen is too vague to implement and test. If it's not ready, set ready=false and list the problems.

If it is ready, split the work into backend tasks (Terraform, DynamoDB, IoT rules, API routes, Lambda source, developer scripts in scripts/) and frontend tasks (pages, components, routes, API client). Copy the acceptance criteria as written. Also check whether frontend/ contains a Vite app; if frontend tasks exist but it doesn't, add that to problems and set ready=false.`,
  { label: 'plan', phase: 'Plan', schema: PLAN },
)

if (!plan) return { feature: FEATURE, error: 'Planning agent did not return a result.' }
if (!plan.ready) {
  log(`Spec not ready: ${plan.problems.length} problem(s)`)
  return { feature: FEATURE, spec: SPEC, ready: false, problems: plan.problems }
}
log(`Plan: ${plan.backend_tasks.length} backend task(s), ${plan.frontend_tasks.length} frontend task(s)`)

const TRACKS = [
  {
    key: 'backend',
    builder: 'backend-builder',
    tester: 'lambda-tester',
    tasks: plan.backend_tasks,
    other: 'The frontend is being built in parallel by another agent. Do not touch frontend/. Besides infra/ and lambdas/<name>/src/, you may write developer scripts (scripts/*.py) and data files in infra/devices/. Do not change docker/, Makefile, requirements-dev.txt, README.md, .github/, .claude/, or CLAUDE.md: list needed changes there under deviations.',
    testerScope: 'You may write tests under lambdas/<name>/tests/ and scripts/tests/ (run with make test FN=<name> or make test FN=scripts). Script tests write only to tmp_path and generate any keys or certificates inside the test; never commit PEM fixtures.',
  },
  {
    key: 'frontend',
    builder: 'frontend-builder',
    tester: 'frontend-tester',
    tasks: plan.frontend_tasks,
    other: 'The backend is being built in parallel by another agent against the same API contract. Do not touch anything outside frontend/.',
    testerScope: '',
  },
].filter(t => t.tasks.length > 0)

const CRITERIA = bullets(plan.acceptance_criteria)

// ---------- Build → Test (per track, no barrier between them) ----------
const tracks = await pipeline(
  TRACKS,
  t => agent(
    `${MANDATE}

Implement the ${t.key} of the feature in ${SPEC}. Read the spec in full first.

Your tasks:
${bullets(t.tasks)}

Acceptance criteria:
${CRITERIA}

${t.other}`,
    { label: t.builder, phase: 'Build', agentType: t.builder, schema: BUILD },
  ),
  (build, t) => {
    if (!build || build.stopped || build.files_changed.length === 0) {
      return { track: t.key, build, test: null }
    }
    // lambda-tester can only write tests under lambdas/ and scripts/; on Terraform-only
    // changes it has nothing to test, so skip it rather than spend an agent on a no-op.
    if (t.key === 'backend' && !build.files_changed.some(f => /(^|\/)(lambdas|scripts)\//.test(f))) {
      log('Backend changed no Lambdas or scripts: skipping lambda-tester (Terraform is checked by make tf-check and iac-reviewer)')
      return { track: t.key, build, test: null }
    }
    return agent(
      `${MANDATE}

Write and run tests for the ${t.key} code just built for ${SPEC}. Read the spec's acceptance criteria and cover each one that applies to the ${t.key}.

Files changed by the builder:
${bullets(build.files_changed)}

Acceptance criteria:
${CRITERIA}

${t.testerScope}`,
      { label: t.tester, phase: 'Test', agentType: t.tester, schema: TEST },
    ).then(test => ({ track: t.key, build, test }))
  },
)

const built = tracks.filter(Boolean).filter(r => r.build && !r.build.stopped && r.build.files_changed.length > 0)
if (built.length === 0) {
  return { feature: FEATURE, spec: SPEC, plan, tracks, error: 'Nothing was built. See the build reports.' }
}

// ---------- Review (barrier: reviewers look at the whole change) ----------
const SCOPE = `Scope: the uncommitted changes in the working tree for the feature in ${SPEC} (git diff, git status --short). Only report problems in those changes.`
const REVIEWERS = [
  ...(built.some(r => r.track === 'backend')
    ? [{ agentType: 'iac-reviewer', prompt: `Review the Terraform changes. ${SCOPE}` }]
    : []),
  { agentType: 'privacy-reviewer', prompt: `Review all changed code for PDPA and personal-data issues. ${SCOPE}` },
]
const reviews = (await parallel(REVIEWERS.map(r => () =>
  agent(r.prompt, { label: r.agentType, phase: 'Review', agentType: r.agentType, schema: FINDINGS })
    .then(res => res && { reviewer: r.agentType, ...res }),
))).filter(Boolean)

// ---------- Fix (once, per track) ----------
const isFrontend = file => file.startsWith('frontend/') || file.includes('/frontend/')
const allFindings = reviews.flatMap(r => r.findings.map(f => ({ ...f, reviewer: r.reviewer })))
const toFix = allFindings.filter(f => f.severity !== 'low')
const lowFindings = allFindings.filter(f => f.severity === 'low')

const fixJobs = built.map(r => {
  const t = TRACKS.find(x => x.key === r.track)
  const findings = toFix.filter(f => (r.track === 'frontend') === isFrontend(f.file))
  const failures = r.test ? r.test.failures : []
  const issues = [
    ...findings.map(f => `[${f.severity}, from ${f.reviewer}] ${f.file}${f.line ? ':' + f.line : ''}: ${f.issue} (suggested fix: ${f.fix})`),
    ...failures.map(f => `[failing test] ${f.test}${f.file ? ' in ' + f.file : ''}: ${f.problem}`),
  ]
  return { track: r.track, builder: t.builder, issues }
}).filter(j => j.issues.length > 0)

// Findings in an area that wasn't built this run have no builder to fix them.
const builtTracks = built.map(r => r.track)
const unrouted = toFix.filter(f => !builtTracks.includes(isFrontend(f.file) ? 'frontend' : 'backend'))

const fixes = fixJobs.length === 0 ? [] : (await parallel(fixJobs.map(j => () =>
  agent(
    `${MANDATE}

You are fixing issues found after the ${j.track} of ${SPEC} was built. For each issue, first check in the code that it is real. Fix the real ones within the files you may change. Skip any that aren't real, or that would need a product decision or a change outside your files, and explain why. Do not edit tests. For a failing test, fix the source unless the test contradicts the spec, in which case skip it and say so.

Issues:
${bullets(j.issues)}

Afterwards, re-run the checks for your area (backend: make lint, make tf-check, make test FN=<name> for the affected lambdas or FN=scripts; frontend: npm run lint, npm run test, npm run build in frontend/) and report the results.`,
    { label: `fix:${j.builder}`, phase: 'Fix', agentType: j.builder, schema: FIX },
  ).then(res => res && { track: j.track, ...res }),
))).filter(Boolean)

log(`Built ${built.map(r => r.track).join(' + ')}; ${toFix.length} high/medium finding(s), ${fixes.reduce((n, f) => n + f.fixed.length, 0)} fixed`)

return {
  feature: FEATURE,
  spec: SPEC,
  summary: plan.summary,
  acceptance_criteria: plan.acceptance_criteria,
  tracks: tracks.filter(Boolean),
  reviews,
  fixes,
  findings_without_builder: unrouted,
  low_findings_not_fixed: lowFindings,
  next_steps: 'Review the diff, run it locally, and commit yourself. Set the spec status to built once you are happy with it.',
}
