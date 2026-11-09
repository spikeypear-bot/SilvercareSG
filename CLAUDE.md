# SilverCare SG

Monitoring system for elderly residents in Singapore. IoT sensors stream metrics to a
serverless AWS backend, which stores them, detects conditions that need attention, and
surfaces status and alerts to caregivers/family and to care staff.

The project is at the very start. Sections marked *(planned)* describe intended
structure, not code that exists yet. Update this file as decisions land.

## Working rules for Claude

- Build for me, yourself: use the skills in `.claude/`. Don't delegate to subagents or
  run multi-agent workflows unless I ask for one (removed 2026-11-08).
- I'm learning on this project. When you use a skill, say in a line which one and why.
- Ask me before making product decisions: what data is collected, who can see what,
  alert thresholds, retention.
- After fixing a bug, a reviewer finding, or a mistake I corrected, run the
  `improve-agents` skill: fix the skill or spec template that let it happen, without
  asking me, within that skill's guardrails. Changes are logged in
  `.claude/agent-changes.md`.
- When you finish a task (a feature build, a fix, a config or docs change), end with a
  recap using the `recap` skill: done, what I should check (including the expected plan
  and cost), what's left to do, and which skills were used. Skip it for quick questions
  that changed nothing.

## Architecture

- **Cloud:** AWS, region `ap-southeast-1` (Singapore). Everything is serverless: no EC2,
  no always-on containers, and no services with a fixed hourly cost unless explicitly agreed.
- **Environments:** one environment only (AWS account `753674274065`). There is no
  staging, so every merge to `main` changes the live system.
- **The one EC2 exception (agreed 2026-11-09, until 2026-11-30):** one `t4g.micro`
  instance runs the simulator 24/7 (`infra/simulator.tf`, `docs/features/simulator-ec2.md`,
  ~US$12/month). Nothing else uses EC2.
- **Backfill reload in progress (started about 20:05 SGT on 2026-11-09):** the owner is
  re-running `silvercare-clean`, then `silvercare-summaries`, by hand for every date from
  2026-02-26 to 2026-10-31 (`docs/features/backfill-variation.md`, Load status); expected
  to finish around 03:00 SGT on 2026-11-10. Until then, don't merge changes to the clean
  or summaries Lambdas or the data lake (`lambdas/clean/`, `lambdas/summaries/`,
  `infra/clean.tf`, `infra/summaries.tf`, `infra/results.tf`, Athena tables, Lake
  Formation). Remove this note when the owner confirms it's done.
- **Infrastructure as code:** Terraform. All AWS resources are defined in Terraform; do
  not create or change resources by hand in the console or with ad-hoc CLI calls.
  Exception: the device-key parameters `/silvercare/device-keys/<id>` are data written by
  `scripts/upload-device-keys.py`, since Terraform would store the keys in state.
- **Device ingestion:** AWS IoT Core over MQTT. Devices publish to topics, IoT Rules route
  messages to Lambda (and/or directly to storage). The metrics contract (topics, payloads,
  rates) is in `docs/metrics.md` (approved 2026-10-25); how the rules route messages is
  in `docs/decisions/0001-iot-rule-routing.md`.
- **Compute:** AWS Lambda, Python (`python3.13` runtime).
- **API:** API Gateway HTTP API → Lambda, protected by a Cognito JWT authorizer.
- **Database:** DynamoDB for live state only: the device registry, the latest 5
  messages per device and message type, and alerts. One agreed exception (2026-11-07):
  `silvercare-results` keeps the last 30 nights of each resident's daily summary for
  the doctors' portal, a copy of the S3 record (`docs/features/nightly-results.md`).
  Provisioned capacity with fixed
  limits inside the always-free tier (25 RCU / 25 WCU across all tables and indexes),
  no auto scaling. Tables, keys, and access patterns are in `docs/data-model.md`.
- **History and analytics:** every telemetry message and alert also goes to S3 for
  analysis. A daily `clean` Lambda runs Athena SQL that writes clean Parquet tables to a
  separate bucket, and Lake Formation governs Athena access (`docs/features/clean-data.md`).
  Each IoT rule also sends
  messages to an SQS queue, and a `history` Lambda writes them to S3 in 5-minute
  batches (`docs/decisions/0001-iot-rule-routing.md`, History path). Firehose and
  Kinesis are not used: they need AWS's paid plan.
- **Budget:** the AWS account is on the free plan with about US$120 of credits that
  must last until the project ends (end of November 2026). Prefer always-free options,
  and state the cost of any new service before adding it.
  A US$40/month AWS Budget (`infra/bootstrap/budget.tf`) emails alerts at 50/80/100% of
  actual and 100% of forecast spend, counted before credits.
- **Auth:** one Cognito user pool (`silvercare-users`) with groups `doctor`, `nurse`,
  `caregiver` (team decision 2026-11-04: `family` is removed, family members get
  caregiver accounts; Track B removes the group). "Staff" in the docs means `doctor` or
  `nurse`: they get the staff dashboard and see every resident. Nurses are a care centre
  (one or two nurses, one shared account, "care centre nurse" in emails and the UI) who
  watch alerts and acknowledge them; doctors (individual accounts) read resident data
  and set alert thresholds. The staff dashboard has two views: nurses get the nurse
  dashboard (alert response: acknowledge and resolve) and doctors the doctor dashboard
  (clinical review: trends, editing thresholds); a user in both groups gets the nurse
  view. `caregiver` gets the family view, sees only linked residents (several caregivers
  per resident), and can cancel their alerts; the frontend gives a user still in the
  `family` group the no-access screen. A new `ops` group (team decision 2026-11-04, in
  `infra/cognito.tf` since 2026-11-08) gets the ops dashboard (`GET /ops/device-problems`):
  device problems (offline, low battery, not worn) for every resident, never health
  alerts, and can acknowledge them, plus the devices tab (`GET /devices` without
  resident names or homes, since 2026-11-08). View precedence: nurse > doctor > ops > caregiver; caregivers can acknowledge their residents'
  device problems too. Sign-in is email + password, MFA (authenticator app) optional.
  Groups decide what a user can see; the API enforces it, the UI only hides things.
- **Frontend:** one React + TypeScript app (Vite, Tailwind CSS) serving both the
  caregiver/family views and the nurse and doctor dashboards, chosen by Cognito group,
  plus the resident page (`/residents/<resident_id>`, staff only), the doctors' vitals
  trends page (`/residents/<resident_id>/vitals`, hourly or daily rows from Athena, with a
  CSV download), and the devices view
  (`/devices`), a tab in the staff and ops areas. A notification bell in the header (all
  roles) lists alert changes the open page's polling noticed, scoped by role, in memory
  only. Static build
  hosted on S3 behind CloudFront (no server-side rendering).

## Repo layout *(planned)*

```
infra/               all Terraform
  *.tf               root config: CI runs plan/apply here
  backend.tf         S3 remote state (see /tf-state-bootstrap)
  modules/<name>/    reusable Terraform modules
  bootstrap/         state bucket, plan role, budget: applied once by hand, never by CI
  devices/           devices.json (the device list) and csr/<id>.csr (committed)
devices/
  AmazonRootCA1.pem  Amazon's public root CA (committed)
secrets/             git-ignored, never committed; developers share it privately
  device-keys/       device private keys and certs
  .env               frontend config (Vite's envDir)
scripts/             developer scripts (device keys, smoke test); tests in scripts/tests/
lambdas/<name>/
  src/handler.py     entry point: lambda_handler(event, context); a folder may hold
                     several functions that share code (e.g. lambdas/ingest/), each
                     with its own entry point in handler.py
  tests/             pytest tests for this function
simulator/
  src/main.py        plays the 40 devices over MQTT (docs/features/simulator-core.md)
  tests/             pytest tests for the simulator
frontend/            the React app
docs/metrics.md      IoT metrics contract
docs/data-model.md   DynamoDB tables, keys, access patterns
docs/features/       one spec per feature (what Claude builds from)
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
| `deploy.yml` | push to `main` | runs `checks.yml`, then `terraform plan` + `apply` with the `silvercare-cicd` role, then builds `frontend/` with the Cognito IDs from the Terraform outputs and uploads it to the S3 bucket (every deploy), then runs the clean-data self-check (pass or fail only) |

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
- The deploy role `silvercare-cicd` was created by hand (not in Terraform) and has the
  AWS managed `AdministratorAccess` policy, so applies don't need per-service CI
  permissions. Its trust policy must allow only `main` (see `/tf-state-bootstrap`).
  Only the plan role is in code (`infra/bootstrap/ci-plan-role.tf`).
- To remove a resource, delete it from the code and merge, rather than running
  `terraform destroy`. Resources with deletion protection need two merges: first turn
  the protection off, then remove the resource.

## Commands

Python and Terraform tooling runs in a Docker dev container (`docker/dev.Dockerfile`,
`docker-compose.yml`): Python 3.13 to match Lambda, pytest, moto, ruff, Terraform,
tflint, checkov. It has no AWS credentials. Use it through `make`, not host tools:

```sh
make up                     # start dev container + DynamoDB Local (faster runs, format hook)
make test                   # unit tests, each Lambda and scripts/ separately; one: make test FN=<name|scripts>
make test-integration       # tests marked @pytest.mark.integration, against DynamoDB Local
make lint                   # ruff check + format check
make fmt                    # ruff format + terraform fmt
make tf-check               # terraform fmt check, validate, tflint, checkov
make check                  # test + lint + tf-check
make shell                  # shell inside the container
make build                  # rebuild after changing docker/ or requirements-dev.txt
make device-keys            # create missing device keys + CSRs (I run it, never Claude)
make smoke-test DEVICE=<id> ENDPOINT=<host>   # test one device against AWS IoT (me only)
make simulate ENDPOINT=<host>                 # run the 40 simulated devices live (me only)
make simulate-dry-run       # print simulated messages, no keys or network
make upload-device-keys     # device keys → Parameter Store for the EC2 simulator (me only, host)
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
- Developers share one hand-made IAM user in the IAM group `silvercare-developers`,
  whose inline policy is kept in `docs/iam/developer-policy.json` (update both together).
  They can read all project config and data, edit DynamoDB items and Cognito users, and
  upload the frontend; they can't change infrastructure, write Terraform state, or change
  the shared password or MFA (only the account owner can). MFA is optional. Because the
  user is shared, CloudTrail can't tell developers apart. Data reads and the shared user
  are acceptable while all data is synthetic; revisit both before any real resident data
  arrives (full list: "Before any real resident data arrives" in `docs/data-model.md`).
- The `pdpa-check` skill has the full checklist.

## Claude setup

- Skills in `.claude/skills/`. No project subagents or workflows (the builder, tester,
  and reviewer agents and the `build-feature` / `pre-pr-check` workflows were removed
  2026-11-08); Claude builds, tests, and reviews itself.
- `.claude/hooks/guard.sh` blocks, the `aws` CLI, Terraform commands
  that change infrastructure or state, anything touching `~/.aws`, and anything touching
  device private keys and local secrets (`secrets/`, `make device-keys|device-certs|smoke-test|simulate|upload-device-keys`,
  and the simulator without `--dry-run`).
  `.claude/hooks/format.sh` formats edited files.
- `.claude/settings.json` hides AWS credentials from Claude (`AWS_SHARED_CREDENTIALS_FILE`
  and `AWS_CONFIG_FILE` are `/dev/null`), so Claude can't reach the real
  AWS account. Anything that needs real AWS (bootstrap apply, debugging live resources),
  I run myself in a separate terminal.
- Feature flow, one feature at a time:
  1. Write the spec with the `feature-spec` skill → `docs/features/<name>.md`, `status: ready`
  2. Ask Claude to build it from the spec: code, tests for each acceptance criterion,
     then `make check` and the frontend lint/test/build
  3. Review the diff and run it locally
- Before a PR, ask Claude to check the branch: `make check`, the frontend checks, a review
  of the Terraform changes (`tf-change`, `iam-policy`), and the `pdpa-check` skill.

## Open decisions

Record the outcome here (and remove the item) once decided. Numbers stay fixed because
other docs refer to them (#1, the metrics contract, was approved 2026-10-25; #4, data
retention, was decided 2026-11-01, see Retention in `docs/data-model.md`: patient
health records, meaning the threshold change history and derived records that informed
care (alerts, daily summaries), are kept for the patient's lifetime + 6 years, per MOH
Circular 85/2022 under the Healthcare Services Act 2020; raw S3 telemetry is
downsampled to daily summaries after 90 days and deleted at 365 days (both figures
proposed); Cognito accounts are disabled at a user's offboarding and deleted 90 days
later; DynamoDB keeps at most the latest 5 messages per device and type).

2. **Alerting rules:** which conditions trigger alerts (e.g. fall, inactivity, abnormal
   vitals) and how alerts are delivered (push, SMS, email). (Decided: wearables also
   send an instant `vitals_alert` for heart rate <45 or >130 bpm or SpO2 <88 % held
   10 s, on top of the cloud's per-resident check; see `docs/metrics.md`.)
3. **User onboarding:** how caregivers/family are linked to residents. (Decided: accounts
   are created by an admin only, no self sign-up.)
5. **Lambda packaging for third-party packages**, if a function ever needs more than
   the standard library, boto3, and Powertools.
