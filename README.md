Silvercare monitoring system

## Local secrets

Everything private to your machine lives in one git-ignored folder, `secrets/`, which
the Makefile and the frontend read from:

```
secrets/
  device-keys/   device private keys, certificates, certificates.json
  .env           frontend config (copy frontend/.env.example and fill it in)
```

To give another developer a working setup, send them the whole `secrets/` folder
over a private channel (not a public chat, an issue, or the repo), and they put it at the
repo root. The private keys let anyone connect as those devices, so treat the folder like
a password.

**Moving from the old layout** (keys in `devices/keys/`, env in `frontend/.env.local`),
once, from the repo root:

```sh
mkdir -p secrets
mv devices/keys secrets/device-keys
mv frontend/.env.local secrets/.env
```

## IoT devices

Every device (`wearable-0001`…`wearable-0020`, `bedroom-0001`…`bedroom-0020`) is an
AWS IoT thing with its own X.509 certificate, and connects over MQTT with mutual TLS.
The device list is `infra/devices/devices.json`; the feature spec is
`docs/features/iot-core.md`.

Each device's **private key** stays on your machine in `secrets/device-keys/` (see Local
secrets). That folder is git-ignored and must never be committed (CI fails if a private
key is tracked). Only the CSRs in `infra/devices/csr/` are committed. Terraform sends
them to AWS, which signs the certificates.

Run these yourself. Claude is blocked from the key targets.

### First setup

1. Create a key and a CSR for every device:

   ```sh
   make device-keys
   ```

2. Download Amazon's public root CA, which devices use to check they're talking to AWS:

   ```sh
   curl -o devices/AmazonRootCA1.pem https://www.amazontrust.com/repository/AmazonRootCA1.pem
   ```

3. Commit `infra/devices/` and `devices/AmazonRootCA1.pem` on a branch and open a PR.
   `git status` must not show anything under `secrets/device-keys/`. The plan comment should
   list only creates: thing types, things, thing groups and memberships, certificates,
   policies, and attachments.
4. Merge. The deploy creates the things and certificates.

### Smoke test

After the deploy, from `infra/` with your own AWS credentials. These `terraform`
commands run on your machine, not in the dev container (it has no AWS credentials), so
install Terraform 1.16.6 on the host first, the same version CI and the container use:

```sh
curl -fsSLO https://releases.hashicorp.com/terraform/1.16.6/terraform_1.16.6_linux_amd64.zip
curl -fsSL https://releases.hashicorp.com/terraform/1.16.6/terraform_1.16.6_SHA256SUMS | grep linux_amd64.zip | sha256sum -c -
mkdir -p ~/.local/bin && unzip -o terraform_1.16.6_linux_amd64.zip terraform -d ~/.local/bin && rm terraform_1.16.6_linux_amd64.zip
terraform version
```

Then:

```sh
terraform init
terraform output -raw iot_endpoint
terraform output -json device_certificates > ../secrets/device-keys/certificates.json
cd .. && make device-certs
make smoke-test DEVICE=wearable-0001 ENDPOINT=$(terraform -chdir=infra output -raw iot_endpoint)
```

Every check should print `PASS`.

### Checking ingest

Checks that messages reach the `silvercare-latest` table (`docs/features/ingest-live.md`).
The second step reads DynamoDB, so it runs on your machine with your own AWS
credentials, not in the dev container. Once, install its requirements on the host. The
scripts need Python 3.11 or newer; on macOS `python3` may be Apple's 3.9, so check
`python3 --version` first and use a newer Python (Homebrew, conda, or python.org) if needed:

```sh
python3 -m venv .venv && .venv/bin/pip install -r scripts/requirements.txt
```

Host scripts (`scripts/ingest-check.py`, `scripts/seed.py`) then run with
`.venv/bin/python`.

Then publish synthetic test messages as a device, and check them:

```sh
make smoke-test DEVICE=wearable-0001 ENDPOINT=$(terraform -chdir=infra output -raw iot_endpoint) INGEST=1
# copy the ingest-check command it prints, and run it with the venv's Python:
.venv/bin/python scripts/ingest-check.py --device wearable-0001 --message-ids ... --invalid-ids ... --history
```

It prints `PASS` when every valid message is stored exactly once in `silvercare-latest`
and the invalid one isn't. With `--history` (the first command the smoke test prints),
it then waits up to 7 minutes for all the messages, the invalid one included, to appear
in the S3 history bucket, which is written in batches of up to 5 minutes. Leave out
`--history` to check only the live table. If it prints `FAIL`, look at the Lambda logs
(`/aws/lambda/silvercare-*`) in the console; for history, also the
`silvercare-history-dlq` queue.

### Adding, removing, or replacing a device

- **Add:** add an entry to `infra/devices/devices.json`, run
  `make device-keys DEVICE=<id>`, then commit the new CSR and merge.
- **Remove:** delete the entry and its CSR, merge, then delete its files in
  `secrets/device-keys/`.
- **Replace a lost or leaked key:** delete `secrets/device-keys/<id>.key` and
  `infra/devices/csr/<id>.csr`, run `make device-keys DEVICE=<id>`, commit the new CSR,
  and merge. The plan shows that device's certificate as replaced, which is expected.

## Alerts

A wearable `fall` or `vitals_alert` (a critical reading on the device, including no
pulse) becomes a **critical** alert: `critical-alert` sends it to the
`silvercare-alerts` queue. Two abnormal `vitals` in a row (per-resident thresholds)
become a **warning**, sent by `telemetry`. `alert-processor` holds back repeats (same
resident, type, and severity within 30 minutes), stores the alert in
`silvercare-alerts`, archives it in S3 `alerts/`, and emails the resident's
subscribers. The specs are `docs/features/alerts-core.md` and
`docs/features/vitals-alerts.md`. If an alert is still open 60 s after the first
email (nobody acknowledged or cancelled it), `alert-processor` emails the nurse
subscribers too ("ESCALATED"; `docs/features/escalation.md`).

### Subscribers

Keep your working copy in the git-ignored `secrets/subscribers.json` and share it
privately with the rest of `secrets/`:

```json
{
  "subscriber_emails": { "family-c1": "you@example.com", "ops-1": "you@example.com", "nurse-1": "you@example.com" },
  "alert_subscribers": { "family-c1": { "resident_ids": ["res-0001"] } },
  "ops_subscribers": ["ops-1"],
  "nurse_subscribers": ["nurse-1"]
}
```

CI can't read `secrets/`. A repo admin copies the values into GitHub (Settings →
Secrets and variables → Actions), each as compact JSON:

| Name | Kind | Value |
|---|---|---|
| `SUBSCRIBER_EMAILS` | secret | `subscriber_emails` (label → email) |
| `ALERT_SUBSCRIBERS` | variable | `alert_subscribers` (label → resident IDs) |
| `OPS_SUBSCRIBERS` | variable | `ops_subscribers` (labels that get DLQ alarm emails) |
| `NURSE_SUBSCRIBERS` | variable | `nurse_subscribers` (labels that get escalated alerts for every resident) |

- Labels appear in the public plan comment: use `family-c1`, `ops-1`, never names.
- Use team addresses, not real family members': emails are stored in the Terraform
  state, which developers can read.
- The next deploy creates one subscription per label. Each address gets an AWS
  "Subscription Confirmation" email: click **Confirm subscription**, or nothing is
  delivered. Unconfirmed subscriptions expire after 3 days and can't be deleted by
  Terraform until then.
- The "unsubscribe" link at the bottom of alert emails removes the subscription
  without Terraform noticing. To get it back, remove the label, deploy, add it again,
  deploy.

### Seeding the registry

Alerts need device → resident assignments. Once, with your own AWS credentials (the
venv from "Checking ingest"):

```sh
.venv/bin/python scripts/seed-registry.py --dry-run   # prints the keys it would write
.venv/bin/python scripts/seed-registry.py
```

It creates synthetic residents `res-0001`–`res-0003`, assigns `wearable-000N` and
`bedroom-000N` (N = 1–3) to `res-000N`, and adds every other device as an unassigned
spare. It also gives `res-0002` a personal SpO2 limit of 88 % (`THRESHOLDS#res-0002`).
Existing items are left alone (`exists`), so running it again is safe.

For the full synthetic data set (20 residents with thresholds, all 40 devices assigned,
4 test Cognito users, one per group, and their links), use `scripts/seed.py`
(`docs/features/seed-data.md`). It asks you to type `seed`, can run before or after
`seed-registry.py`, and writes the test users' passwords to `secrets/test-users.txt`
(share that file privately):

```sh
.venv/bin/python scripts/seed.py                    # seed (or reset) the registry and users
.venv/bin/python scripts/seed.py --reset-passwords  # new passwords for the 4 test users
.venv/bin/python scripts/seed.py --delete           # remove everything the seed created
make seed-local                                     # same data in DynamoDB Local, no AWS
```

### Checking alerts

After the deploy, the subscription confirmations, and the seed:

```sh
make smoke-test DEVICE=wearable-0001 ENDPOINT=$(terraform -chdir=infra output -raw iot_endpoint) INGEST=1
```

The smoke test sends a valid `fall`. Expect, within a minute:

- one email to `res-0001`'s subscribers, and none to anyone else;
- one item under `RESIDENT#res-0001` in `silvercare-alerts`, and one object under
  `alerts/date=<today, UTC>/` in the history bucket;
- both `silvercare-alerts-dlq` and `silvercare-critical-alert-dlq` empty (the shared
  developer user can't read SQS: check that CloudWatch alarms
  `silvercare-alerts-dlq-not-empty` and `silvercare-critical-alert-dlq-not-empty` are
  OK);
- a datapoint for the `AlertDispatchLatency` metric (CloudWatch → Metrics →
  `SilverCare`).

Then run it with `DEVICE=wearable-0010` (unassigned): one item under
`RESIDENT#unassigned`, and no email. If a DLQ alarm email arrives, look at the queue's
messages and the `/aws/lambda/silvercare-critical-alert` and
`/aws/lambda/silvercare-alert-processor` logs.

Vitals alerts, from an assigned wearable:

```sh
make smoke-test DEVICE=wearable-0001 ENDPOINT=$(terraform -chdir=infra output -raw iot_endpoint) ALERTS=1
```

It sends two high heart-rate `vitals` (one **warning**, "High heart rate"), a device
`vitals_alert` at 140 bpm and one with no pulse (two **CRITICAL** emails), then a third
high `vitals` that is suppressed (no email). Expect three emails, three new items under
`RESIDENT#res-0001`, and the suppressed one only as `suppressed` in the
`/aws/lambda/silvercare-alert-processor` log. Running it again within 30 minutes
suppresses the warning and both critical alerts. No warning email? The resident may
have a personal limit (`THRESHOLDS#<resident>` in `silvercare-registry`) above the
test readings (180–185 bpm): then they are normal and no warning is correct.

Escalation: after any alert email, wait about a minute. Each nurse subscriber gets
"SilverCare ESCALATED alert: …" for every alert that is still `open` (the
`/aws/lambda/silvercare-alert-processor` log shows `escalated`). To check that an
acknowledged alert doesn't escalate, send a fall (`INGEST=1`), then within 60 s open
its item in DynamoDB → `silvercare-alerts` and set `status` to `acknowledged`: no
nurse email, and the log shows `not_escalated` with reason `status:acknowledged`. If an
escalation fails (an AWS error), it is retried after 3 minutes, so the nurse email
can arrive about 4 minutes late; after 5 failures it lands in `silvercare-alerts-dlq`
(alarm email). A check older than 15 minutes, e.g. one redriven from the DLQ later,
emails nobody (`not_escalated`, reason `stale`).

Subscriptions to `silvercare-alerts` and `silvercare-nurses` can only be added by the
deploy (Terraform); adding one in the console is denied on purpose.

Cancel ("I'm OK" on the wearable, `docs/features/alert-cancel.md`):

```sh
make smoke-test DEVICE=wearable-0001 ENDPOINT=$(terraform -chdir=infra output -raw iot_endpoint) CANCEL=1
```

It sends a `fall`, then an `alert_cancel` 5 s later. Expect the fall email, then
"SilverCare update: Fall - res-0001 cancelled", and **no** nurse email (the cancel
lands before the 60 s escalation). The alert item shows `status` `cancelled`, has no
`open_pk`, and has an `expires_at` 30 days out. A press cancels every open alert from
that wearable, so earlier test alerts that are still open get cancelled (and emailed)
too.
