# SilverCare SG

Monitoring system for elderly residents in Singapore. IoT sensors stream metrics to a
serverless AWS backend, which stores them, detects conditions that need attention, and
surfaces status and alerts to caregivers/family and to care staff.

The project is at the very start. Sections marked *(planned)* describe intended
structure, not code that exists yet. Update this file as decisions land.

## Working rules for Claude

- I am building this project myself. Help with guidance, answers, and review. Do not
  scaffold, implement, or build features unless I explicitly ask for that specific thing.
  When I start the `build-feature` workflow for a spec, that counts as asking for that
  feature.
- Only edit the files I ask you to edit.
- Never run `git commit` or `git push`. I commit and push myself.

## Architecture

- **Cloud:** AWS, region `ap-southeast-1` (Singapore). Everything is serverless: no EC2,
  no always-on containers, and no services with a fixed hourly cost unless explicitly agreed.
- **Environments:** one environment only (AWS account `753674274065`). There is no
  staging, so every merge to `main` changes the live system.
- **Infrastructure as code:** Terraform. All AWS resources are defined in Terraform; do
  not create or change resources by hand in the console or with ad-hoc CLI calls.
- **Device ingestion:** AWS IoT Core over MQTT. Devices publish to topics, IoT Rules route
  messages to Lambda (and/or directly to storage). The metrics contract (topics, payloads,
  rates) is in `docs/metrics.md` *(planned)*.
- **Compute:** AWS Lambda, Python (`python3.13` runtime).
- **API:** API Gateway HTTP API → Lambda, protected by a Cognito JWT authorizer.
- **Database:** DynamoDB (on-demand). Tables, keys, and access patterns are in
  `docs/data-model.md` *(planned)*.
- **Auth:** Cognito user pool with groups `staff`, `caregiver`, `family` (initial names;
  adjust as needed). Groups decide what a user can see; the API enforces it, the UI only
  hides things.
- **Frontend:** one React + TypeScript app (Vite, Tailwind CSS) serving both the
  caregiver/family views and the staff dashboard, chosen by Cognito group. Static build
  hosted on S3 behind CloudFront (no server-side rendering).

## Repo layout *(planned)*

```
*.tf                 root Terraform config (CI runs from the repo root)
backend.tf           S3 remote state (see /tf-state-bootstrap)
modules/<name>/      reusable Terraform modules
bootstrap/           state bucket config, applied once by hand, never by CI
lambdas/<name>/
  src/handler.py     entry point: lambda_handler(event, context)
  tests/             pytest tests for this function
frontend/            the React app
docs/metrics.md      IoT metrics contract
docs/data-model.md   DynamoDB tables, keys, access patterns
docs/features/       one spec per feature (input to build-feature)
docs/decisions/      decision records
```

Lambda packaging: Terraform's `archive_file` zips `lambdas/<name>/src/`. Functions use
only the standard library, `boto3` (included in the runtime), and AWS Lambda Powertools
(AWS's public Lambda layer). If a function needs other packages, packaging must be
revisited first (see Open decisions).

## Deployment

`.github/workflows/deploy.yml` runs `terraform init`, `plan`, and `apply` from the repo
root on every push to `main`, authenticating via GitHub OIDC to the IAM role
`silvercare-cicd`. Implications:

- Merging to `main` deploys to AWS. Work on branches and open PRs.
- Terraform config must live at the repo root (or the workflow's `working-directory`
  must change with it).
- There is no remote state backend yet. CI runners are ephemeral, so the backend must be
  set up (`/tf-state-bootstrap`) before the first real apply.
- `terraform plan` fails if there are no `.tf` files, so the first merge to `main` must
  include Terraform config.

## Commands

Tools must be installed locally; these are the intended defaults.

```sh
# Terraform (repo root)
terraform fmt -recursive
terraform init -backend=false && terraform validate   # local check without AWS creds

# Python Lambdas: run each function's tests separately (every function has a
# module named handler, so a single pytest run across all of them collides)
for d in lambdas/*/; do pytest "$d" || break; done
ruff check lambdas && ruff format lambdas

# Frontend (in frontend/)
npm run dev
npm run build
npm run lint
npm run test        # Vitest
```

Python dev dependencies (`pytest`, `moto`, `boto3`, `ruff`, `aws-lambda-powertools`) go
in `requirements-dev.txt` at the repo root.

## Data and privacy

This system handles health and location data of elderly people, which is personal data
under Singapore's PDPA. Treat it accordingly:

- Never put real resident data in code, tests, fixtures, logs, or commit messages. Use
  obviously synthetic data.
- Don't log full sensor payloads or personal identifiers from Lambdas; log IDs and
  event types.
- Keep data in `ap-southeast-1`. Encrypt at rest and in transit, and give each IAM role
  only the access it needs.
- The `pdpa-check` skill has the full checklist.

## Claude setup

- Skills in `.claude/skills/`. Subagents in `.claude/agents/`:
  - builders: `backend-builder`, `frontend-builder` (used by `build-feature`)
  - testers: `lambda-tester`, `frontend-tester`
  - reviewers (read-only): `iac-reviewer`, `privacy-reviewer`
- `.claude/hooks/` blocks `git commit`/`push` and formats edited files.
- Feature flow, one feature at a time:
  1. Write the spec with the `feature-spec` skill → `docs/features/<name>.md`, `status: ready`
  2. Ask Claude to run the `build-feature` workflow with the feature name
  3. Review the diff and run it locally, then commit yourself
- Before a PR, ask Claude to run the `pre-pr-check` workflow.

## Open decisions

Record the outcome here (and remove the item) once decided.

1. **IoT metrics contract:** which sensors exist, which metrics each sends, units, sample
   rate, MQTT topic structure, and payload format. Decide this first, because the
   DynamoDB table design depends on it.
2. **Alerting rules:** which conditions trigger alerts (e.g. fall, inactivity, abnormal
   vitals) and how alerts are delivered (push, SMS, email).
3. **User onboarding:** who creates accounts (staff invite only, or self sign-up for
   family) and how caregivers/family are linked to residents.
4. **Data retention:** how long raw sensor readings, alerts, and user data are kept.
5. **Lambda packaging for third-party packages**, if a function ever needs more than
   the standard library, boto3, and Powertools.
