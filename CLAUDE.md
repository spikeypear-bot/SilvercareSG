# SilverCare SG

Monitoring system for elderly residents in Singapore. IoT sensors stream metrics to a
serverless AWS backend, which stores them, detects conditions that need attention, and
surfaces status and alerts to caregivers/family and to care staff.

The project is at the very start. Sections marked *(planned)* describe intended
structure, not code that exists yet. Update this file as decisions land.

## Working rules for Claude

- Build for me. Use the skills, subagents, and workflows in `.claude/`, and delegate to
  the builder, tester, and reviewer agents wherever they fit.
- I'm new to agents and learning on this project. When you use a skill, agent, or
  workflow, say in a line which one and why.
- Ask me before making product decisions: what data is collected, who can see what,
  alert thresholds, retention.
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
- **Database:** DynamoDB for live state only: the device registry, the latest 5
  messages per device and message type, and alerts. Provisioned capacity with fixed
  limits inside the always-free tier (25 RCU / 25 WCU across all tables and indexes),
  no auto scaling. Tables, keys, and access patterns are in `docs/data-model.md`.
- **History and analytics:** every telemetry message and alert also goes to S3 for
  analysis; the data is cleaned later with AWS Lake Formation. Design to follow.
- **Budget:** the AWS account is on the free plan with about US$120 of credits that
  must last until the project ends (end of November 2026). Prefer always-free options,
  and state the cost of any new service before adding it.
  A US$40/month AWS Budget (`infra/bootstrap/budget.tf`) emails alerts at 50/80/100% of
  actual and 100% of forecast spend, counted before credits.
- **Auth:** Cognito user pool with groups `staff`, `caregiver`, `family` (initial names;
  adjust as needed). Groups decide what a user can see; the API enforces it, the UI only
  hides things.
- **Frontend:** one React + TypeScript app (Vite, Tailwind CSS) serving both the
  caregiver/family views and the staff dashboard, chosen by Cognito group. Static build
  hosted on S3 behind CloudFront (no server-side rendering).

## Repo layout *(planned)*

```
infra/               all Terraform
  *.tf               root config: CI runs plan/apply here
  backend.tf         S3 remote state (see /tf-state-bootstrap)
  modules/<name>/    reusable Terraform modules
  bootstrap/         state bucket, plan role, budget: applied once by hand, never by CI
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

GitHub Actions workflows in `.github/workflows/`:

| Workflow | When | What |
|---|---|---|
| `checks.yml` | every PR, and before every deploy | builds the dev image, then `make lint`, `make test`, `make test-integration`, `make tf-check`; frontend lint/test/build once `frontend/` exists. No secrets or AWS access. |
| `plan.yml` | every PR from this repo (not forks) | `terraform plan` with the read-only `silvercare-cicd-plan` role; posts a PR comment listing what will be created, updated, replaced, or **destroyed** |
| `deploy.yml` | push to `main` | runs `checks.yml`, then `terraform plan` + `apply` with the `silvercare-cicd` role |

Implications:

- Merging to `main` deploys to AWS. Work on branches, open PRs, and read the plan
  comment before merging, especially any 🔴 destroy or 🟠 replace rows.
- **The repository is public**, so Actions logs and PR comments are public too.
  Workflows never print full plans (`.github/scripts/plan-summary.sh` lists only
  resource addresses and actions). Never print secrets, plan values, or data in CI.
- The Terraform root config lives in `infra/` (the workflows' `working-directory`). Moving
  it means changing the workflows and `docker/tf-check.sh` too. With no `.tf` files, plan and deploy skip.
- Terraform is pinned to the same version in the workflows (`TERRAFORM_VERSION`) and in
  `docker/dev.Dockerfile`. Change both together.
- There is no remote state backend yet. CI runners are ephemeral, so the backend and the
  plan role must be set up (`/tf-state-bootstrap`) before the first real apply.
- To remove a resource, delete it from the code and merge, rather than running
  `terraform destroy`. Resources with deletion protection need two merges: first turn
  the protection off, then remove the resource.

## Commands

Python and Terraform tooling runs in a Docker dev container (`docker/dev.Dockerfile`,
`docker-compose.yml`): Python 3.13 to match Lambda, pytest, moto, ruff, Terraform,
tflint, checkov. It has no AWS credentials. Use it through `make`, not host tools:

```sh
make up                     # start dev container + DynamoDB Local (faster runs, format hook)
make test                   # unit tests, each Lambda separately; one: make test FN=<name>
make test-integration       # tests marked @pytest.mark.integration, against DynamoDB Local
make lint                   # ruff check + format check
make fmt                    # ruff format + terraform fmt
make tf-check               # terraform fmt check, validate, tflint, checkov
make check                  # test + lint + tf-check
make shell                  # shell inside the container
make build                  # rebuild after changing docker/ or requirements-dev.txt
```

Python dev dependencies live in `requirements-dev.txt` (pinned); tool versions are
pinned in `docker/dev.Dockerfile`. Ruff settings are in `ruff.toml`.

The frontend runs on the host's Node (in `frontend/`): `npm run dev`, `npm run build`,
`npm run lint`, `npm run test` (Vitest).

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
- `.claude/hooks/guard.sh` blocks `git commit`/`push`, the `aws` CLI, Terraform commands
  that change infrastructure or state, and anything touching `~/.aws`.
  `.claude/hooks/format.sh` formats edited files.
- `.claude/settings.json` hides AWS credentials from Claude (`AWS_SHARED_CREDENTIALS_FILE`
  and `AWS_CONFIG_FILE` are `/dev/null`), so Claude and its agents can't reach the real
  AWS account. Anything that needs real AWS (bootstrap apply, debugging live resources),
  I run myself in a separate terminal.
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
4. **Data retention:** how long history in S3, resolved alerts in DynamoDB, and user
   data are kept. (Decided: DynamoDB keeps at most the latest 5 messages per device and
   type.)
5. **Lambda packaging for third-party packages**, if a function ever needs more than
   the standard library, boto3, and Powertools.
