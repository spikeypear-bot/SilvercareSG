# SilverCare SG

Monitoring system for elderly residents in Singapore. IoT sensors stream metrics to a
serverless AWS backend, which stores them, detects conditions that need attention, and
surfaces status and alerts to caregivers/family and to care staff.

The project is at the very start: only the README and the deploy workflow exist. Sections
marked *(planned)* describe intended structure, not code that exists yet. Update this file
as decisions land.

## Working rules for Claude

- I am building this project myself. Help with guidance, answers, and review. Do not
  scaffold, implement, or build features unless I explicitly ask for that specific thing.
- Only edit the files I ask you to edit.
- Never run `git commit` or `git push`. I commit and push myself.

## Architecture

- **Cloud:** AWS, region `ap-southeast-1` (Singapore). Everything is serverless: no EC2,
  no always-on containers, and no services with a fixed hourly cost unless explicitly agreed.
- **Infrastructure as code:** Terraform. All AWS resources are defined in Terraform; do
  not create or change resources by hand in the console or with ad-hoc CLI calls.
- **Device ingestion:** AWS IoT Core over MQTT. Devices publish to topics, IoT Rules route
  messages to Lambda (and/or directly to storage).
- **Compute:** AWS Lambda, written in Python.
- **Database:** not decided yet (see Open decisions).
- **Clients:** a caregiver/family app (alerts + one resident's status) and a staff dashboard
  (many residents at once). Both are *(planned)*; frontend stack not chosen.

## Deployment

`.github/workflows/deploy.yml` runs `terraform init`, `plan`, and `apply` from the repo
root on every push to `main`, authenticating via GitHub OIDC to the IAM role
`silvercare-cicd`. Implications:

- Merging to `main` deploys to AWS. Work on branches and open PRs.
- Terraform config must live at the repo root (or the workflow's `working-directory`
  must change with it).
- There is no remote state backend yet. CI runners are ephemeral, so a backend
  (S3 bucket with a lockfile/DynamoDB lock) must be configured before the first real apply.
  Without it, each run starts with empty state.
- `terraform plan` fails if there are no `.tf` files, so the first merge to `main` must
  include Terraform config.

Before committing Terraform changes, run:

```sh
terraform fmt -recursive
terraform validate
```

## Data and privacy

This system handles health and location data of elderly people, which is personal data
under Singapore's PDPA. Treat it accordingly:

- Never put real resident data in code, tests, fixtures, logs, or commit messages. Use
  obviously synthetic data.
- Don't log full sensor payloads or personal identifiers from Lambdas; log IDs and
  event types.
- Keep data in `ap-southeast-1`. Encrypt at rest and in transit, and give each IAM role
  only the access it needs.

## Open decisions

Record the outcome here (and remove the item) once decided.

1. **IoT metrics contract:** which sensors exist, which metrics each sends, units, sample
   rate, MQTT topic structure, and payload format. Decide this first, because the database
   schema depends on it.
2. **Database:** DynamoDB alone, DynamoDB plus a time-series store, or Aurora Serverless v2.
   Choose based on the access patterns that follow from (1) and from what the clients show.
3. **Alerting rules:** which conditions trigger alerts (e.g. fall, inactivity, abnormal
   vitals) and how alerts are delivered (push, SMS, email).
4. **Auth:** how caregivers, family, and staff sign in and what each role can see.
5. **Frontend stack** for the caregiver app and the staff dashboard.
6. **Python tooling:** Python version, dependency manager, test runner, linter/formatter.
   Add the commands to this file once chosen.
