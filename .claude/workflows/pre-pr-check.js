export const meta = {
  name: 'pre-pr-check',
  description: 'Review the current branch before a PR: IaC review, privacy review, Lambda and frontend tests in parallel, then verify each set of findings',
  whenToUse: 'Before opening a pull request, after Terraform, Lambda, or frontend changes. Optional args: { base: "main" }',
  phases: [
    { title: 'Review', detail: 'iac-reviewer, privacy-reviewer, lambda-tester, frontend-tester on the branch changes' },
    { title: 'Verify', detail: 'one skeptic per reviewer re-checks its findings against the code' },
  ],
}

const BASE = (args && args.base) || 'main'

const SCOPE = `Scope: changes on the current branch compared with ${BASE}, plus uncommitted and untracked files. Find them with \`git diff ${BASE}...HEAD --stat\`, \`git diff\`, and \`git status --short\`. If nothing in scope is relevant to your role, return no findings and say so in checks_run.`

const FINDINGS = {
  type: 'object',
  properties: {
    checks_run: {
      type: 'array',
      items: { type: 'string' },
      description: 'Each command or check run and its result, or why it was skipped',
    },
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

const VERDICTS = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          index: { type: 'integer' },
          real: { type: 'boolean' },
          reason: { type: 'string' },
        },
        required: ['index', 'real', 'reason'],
      },
    },
  },
  required: ['verdicts'],
}

const REVIEWERS = [
  {
    agentType: 'iac-reviewer',
    prompt: `Review the Terraform changes. ${SCOPE}`,
  },
  {
    agentType: 'privacy-reviewer',
    prompt: `Review all changed code (Terraform, Lambdas, frontend, tests, docs) for PDPA and personal-data issues. ${SCOPE}`,
  },
  {
    agentType: 'lambda-tester',
    prompt: `Run the existing tests for every Lambda touched by the changes. Report failures as findings, and report changed behaviour that has no test as low-severity findings. This is a run-only check: do not create or edit any files. ${SCOPE}`,
  },
  {
    agentType: 'frontend-tester',
    prompt: `If any files under frontend/ changed, run npm run test, npm run lint, and npm run build there, and review the changed components for accessibility problems. Report failures and accessibility problems as findings, and changed behaviour with no test as low-severity findings. This is a run-only check: do not create or edit any files. ${SCOPE}`,
  },
]

const SEVERITY_ORDER = { high: 0, medium: 1, low: 2 }

const results = await pipeline(
  REVIEWERS,
  r => agent(r.prompt, { label: r.agentType, phase: 'Review', agentType: r.agentType, schema: FINDINGS }),
  (review, r) => {
    if (!review) {
      return { reviewer: r.agentType, error: 'reviewer did not return a result', checks_run: [], confirmed: [], rejected: [] }
    }
    if (review.findings.length === 0) {
      return { reviewer: r.agentType, checks_run: review.checks_run, confirmed: [], rejected: [] }
    }
    const list = review.findings
      .map((f, i) => `${i}. [${f.severity}] ${f.file}${f.line ? ':' + f.line : ''}: ${f.issue}`)
      .join('\n')
    return agent(
      `A ${r.agentType} reviewing the SilverCare repo reported the findings below. For each one, read the code it points to and try to refute it. Mark real=false if the code does not show the problem, the finding misreads the code, or it is out of scope for changes on this branch compared with ${BASE}. Mark real=true only if you can confirm it from the code. This is read-only: do not edit, create, or delete any files.\n\nFindings (index. [severity] location: issue):\n${list}`,
      { label: `verify:${r.agentType}`, phase: 'Verify', schema: VERDICTS },
    ).then(v => {
      if (!v) {
        return { reviewer: r.agentType, checks_run: review.checks_run, unverified: review.findings, confirmed: [], rejected: [] }
      }
      const byIndex = new Map(v.verdicts.map(x => [x.index, x]))
      const confirmed = []
      const rejected = []
      review.findings.forEach((f, i) => {
        const verdict = byIndex.get(i)
        if (verdict && !verdict.real) rejected.push({ ...f, reason: verdict.reason })
        else confirmed.push({ ...f, verified: Boolean(verdict) })
      })
      confirmed.sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity])
      return { reviewer: r.agentType, checks_run: review.checks_run, confirmed, rejected }
    })
  },
)

const reports = results.filter(Boolean)
const total = reports.reduce((n, r) => n + r.confirmed.length + (r.unverified ? r.unverified.length : 0), 0)
log(`${total} finding(s) to look at across ${reports.length} reviewer(s)`)

return { base: BASE, reports }
