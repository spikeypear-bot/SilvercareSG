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

### Simulator

> **Until 2026-11-30 the simulator already runs 24/7 on EC2, operated by one person (see
> [docs/simulator-ec2.md](docs/simulator-ec2.md)). You don't need to run it, and don't
> run `make simulate` against AWS: it would knock the EC2 simulator's devices offline.
> `make simulate-dry-run` is fine. Ask the owner for demo scenarios.**

`simulator/` plays all 40 devices with normal data: vitals every minute, status every
5 minutes, and bed and motion events on a daily routine, each resident with their own
baseline from `scripts/seed-data.json` (`docs/features/simulator-core.md`).

```sh
make simulate-dry-run ARGS="--instant --duration 24h"   # a whole day at once, no AWS
make simulate ENDPOINT=$(terraform -chdir=infra output -raw iot_endpoint) ARGS="--duration 30m"
```

**Before a live run, seed the registry with `scripts/seed.py`** (see "Seeding the
registry") and wait 5 minutes. The cloud checks readings against the thresholds stored
in the registry; without the seed, two simulated residents (`wearable-0004` and
`wearable-0011`) fall outside the default limits and trigger warning emails to the
nurses.

A live run uses the same keys as the smoke test and prints one summary line a minute.
Stop it with Ctrl+C (or let `--duration` end it). It costs about US$2 a month if left
running all the time, so stop it when you're done. If the terminal was closed while it
ran, check with `docker compose ps` and stop leftovers with `docker compose down`.
Useful options: `--start-at 21:55` (start the routine at that time of day, to see
bedtime in minutes), `--devices wearable-0001,bedroom-0001`, `--random-seed N`.

#### Alert scenarios

Make one wearable have an emergency on purpose, while the other devices keep running
normally (`docs/features/simulator-alert-scenarios.md`):

| Scenario | What the wearable does | Expected alert |
|---|---|---|
| `fall` | a fall, then lies still until `im-ok` or 10 min | `fall` (critical) |
| `hr-spike` | heart rate 150 for 3 min | `hr_high` critical, then warning |
| `spo2-drop` | SpO2 84 % for 3 min | `spo2_low` critical, then warning |
| `no-pulse` | no pulse for 3 min | `hr_low` critical (no pulse) |
| `im-ok` | the "I'm OK" button | cancels the wearable's open alerts |

Start them with `--scenario <name>:<wearable>[@<time after start>]` (default 30 s), or
type `<name> <wearable>` (e.g. `fall wearable-0003`) into the running simulator:

```sh
make simulate ENDPOINT=<host> ARGS="--duration 10m --scenario fall:wearable-0003@1m --scenario im-ok:wearable-0003@2m"
make simulate-dry-run ARGS="--instant --duration 10m --scenario hr-spike:wearable-0003@1m"
```

The second line prints the messages without AWS.

**The alerts and emails are real.** Each scenario emails the caregivers subscribed to
that resident, and every alert not cancelled with `im-ok` also goes to all the nurse
subscribers (escalation). `hr-spike` and `spo2-drop` raise two alerts each (critical,
then warning); falls are always emailed; other repeats of the same alert for the same
resident are held back for 30 minutes. So one scenario can mean 2–4 emails per inbox:
keep live demos to a few scenarios. Don't run
`make smoke-test` on a device while the simulator runs: AWS allows one connection per
device, so the two would keep disconnecting each other.

#### History backfill (months of data for analytics)

`simulator/src/backfill.py` runs the simulator over past days in seconds and writes the
result as local files in the history bucket's raw layout, with 12 falls (3 of them
cancelled with "I'm OK") by default (`docs/features/simulator-backfill.md`). It connects
to nothing:

```sh
make backfill ARGS="--from 2026-03-01 --to 2026-10-31 --out backfill-data"
```

Four residents (`res-0003`, `res-0008`, `res-0014`, `res-0019`) are unusual
(`docs/features/backfill-variation.md`): wider noise, a mild decline over the range,
bad days, irregular sleep, readings that may cross their thresholds (and send
`vitals_alert` like a real wearable), plus 176 heart-rate spikes and SpO2 drops that
become more frequent over the range (about 1 per resident in the first month, about 10
in the last).

Options: `--falls N` (0–100), `--vitals-episodes N` (0–400), `--random-seed N` (same
seed, identical files). It also writes 2 lead-in days before `--from`, which the daily
summaries need. `backfill-data/` is git-ignored. No stored alerts or emails are
produced; falls and `vitals_alert`s show up only in the clean tables and the daily
summaries.

**Replacing an earlier load.** File names change between versions, so uploading over
an earlier load would keep both copies and count every day twice. Generate with the
same `--from`/`--to` into an empty folder, skip steps 1 and 2 below (already done for
these dates), and before step 3 delete only the earlier generated objects on the
generated UTC dates, in **every** message folder: falls and cancels can land on other
days than last time, so a folder the new run doesn't have can still hold old lines.

```sh
B=silvercare-history-<acct>
ls -d backfill-data/telemetry/*/date=* | grep -o 'date=[0-9-]*' | sort -u > /tmp/gen-dates
for m in $(aws s3 ls "s3://$B/telemetry/" | awk '{print $2}'); do  # message=<type>/
  while read -r d; do
    aws s3 ls "s3://$B/telemetry/$m$d/" | awk '{print $4}' | while read -r f; do
      k="telemetry/$m$d/$f"
      src=$(aws s3api head-object --bucket "$B" --key "$k" --query Metadata.source --output text)
      [ "$src" = simulator-backfill ] && aws s3 rm "s3://$B/$k"
    done
  done < /tmp/gen-dates
done
```

Then steps 3 and 4 for every generated date: re-running clean and summaries replaces
each date's output, including the summaries.

**Loading it into AWS** needs write access to the history bucket, which the shared
developer user doesn't have, so the account owner runs these steps from the host, in
order. Summaries are kept forever and only the summaries job can replace them, so don't
skip steps 1 and 2.

1. **Re-seed:** run `.venv/bin/python scripts/seed.py`. The seed gives the test devices
   a fixed `assigned_at` of 1 Feb 2026. The daily summaries ignore readings from before
   it, so without this step every generated day is summarised as empty.
2. **Check for overlap with live data** (replace `<acct>`). This compares every
   generated UTC day with the days that already hold data and prints the days that
   clash:

   ```sh
   B=s3://silvercare-history-<acct>/telemetry
   ls -d backfill-data/telemetry/*/date=* | grep -o 'date=[0-9-]*' | sort -u > /tmp/gen-dates
   aws s3 ls --recursive $B/ | grep -o 'date=[0-9-]*' | sort -u > /tmp/live-dates
   comm -12 /tmp/gen-dates /tmp/live-dates
   ```

   If it prints anything, don't upload: re-generate into an empty folder with `--to`
   set at least one day before the first printed date. Overlapping days would hold both
   and count twice.
3. **Upload**, marked as generated:

   ```sh
   aws s3 sync backfill-data/telemetry $B/ --metadata source=simulator-backfill
   ```

   Upload again only with the same `make backfill` arguments, which replaces the same
   files. If you change the arguments, delete the earlier generated dates first, or
   both copies count.
4. **Run the clean job, then the summaries,** once per day, oldest first:

   ```sh
   run() { [ "$(AWS_MAX_ATTEMPTS=1 aws lambda invoke --function-name "$1" --cli-read-timeout 330 --cli-binary-format raw-in-base64-out --payload "{\"date\":\"$2\"}" --query FunctionError --output text /dev/null)" = None ] || { echo "$1 failed on $2"; return 1; } }
   for d in $(sed 's/date=//' /tmp/gen-dates); do run silvercare-clean "$d" || break; done
   for d in $(sed 's/date=//' /tmp/gen-dates | tail -n +4); do run silvercare-summaries "$d" || break; done
   ```

   - The clean job runs for every generated UTC day.
   - The summaries skip the first 3 UTC days, which are the lead-in before `--from`. So
     they run for each Singapore day from `--from` to `--to`, with the dates read from
     the generated folders.
   - `run` waits up to 330 s per job (the jobs' timeout is 300 s) and never retries,
     because a retry could run a job twice.
   - A failed job stops the loop with `<function> failed on <date>`. Fix the cause,
     then restart the loop from that date.
   - The calls are synchronous, so they don't fill the doctors' nightly Results table
     with months of nights.
   - Each loop runs about 250 jobs one after another, so expect it to take a while.

## Alerts

A wearable `fall` or `vitals_alert` (a critical reading on the device, including no
pulse) becomes a **critical** alert: `critical-alert` sends it to the
`silvercare-alerts` queue. Two abnormal `vitals` in a row (per-resident thresholds)
become a **warning**, sent by `telemetry`. `alert-processor` holds back repeats (same
resident, type, and severity within 30 minutes), stores the alert in
`silvercare-alerts`, archives it in S3 `alerts/`, and emails the resident's
subscribers. The specs are `docs/features/alerts-core.md` and
`docs/features/vitals-alerts.md`. Who else is emailed (`docs/features/alert-routing.md`):

- **critical** alerts, and every alert from an unassigned device: the nurse
  subscribers (the care centre) at once, with the caregivers;
- **warnings**: the nurse subscribers only if the alert is still open 5 minutes after
  the first email (nobody acknowledged or cancelled it): "ESCALATED"
  (`docs/features/escalation.md`).

Nurses acknowledge in the portal (with an optional note, emailed to the caregivers);
caregivers cancel. Both buttons come with Track B's API.

### Subscribers

Keep your working copy in the git-ignored `secrets/subscribers.json` and share it
privately with the rest of `secrets/`:

```json
{
  "subscriber_emails": { "caregiver-c1": "you@example.com", "ops-1": "you@example.com", "nurse-1": "you@example.com" },
  "alert_subscribers": { "caregiver-c1": { "resident_ids": ["res-0001"] } },
  "ops_subscribers": ["ops-1"],
  "nurse_subscribers": ["nurse-1"]
}
```

CI can't read `secrets/`. A repo admin copies the values into GitHub (Settings →
Secrets and variables → Actions), each as compact JSON. Copy only the **value** of
each key, not the key itself or the file's outer braces (otherwise the plan fails with
"string required, but have object"):

| Name | Kind | Value (example) |
|---|---|---|
| `SUBSCRIBER_EMAILS` | secret | the value of `subscriber_emails`, label → email: `{"caregiver-c1":"you@example.com","ops-1":"you@example.com","nurse-1":"you@example.com"}` |
| `ALERT_SUBSCRIBERS` | variable | the value of `alert_subscribers`, label → resident IDs: `{"caregiver-c1":{"resident_ids":["res-0001"]}}` |
| `OPS_SUBSCRIBERS` | variable | the value of `ops_subscribers`, labels that get DLQ alarm emails: `["ops-1"]` |
| `NURSE_SUBSCRIBERS` | variable | the value of `nurse_subscribers`, labels that get critical, unassigned, and escalated alerts for every resident: `["nurse-1"]` |

Renaming a label (e.g. `family-c1` → `caregiver-c1`) shows in the plan as a 🔴 destroy
of the old email subscription and a ➕ create of the new one: expected, nothing else is
lost, but the address has to confirm the new subscription.

- Labels appear in the public plan comment: use `caregiver-c1`, `ops-1`, never names.
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
3 test Cognito users (doctor, nurse, caregiver), and the caregiver's links), use `scripts/seed.py`
(`docs/features/seed-data.md`). It asks you to type `seed`, can run before or after
`seed-registry.py`, and writes the test users' passwords to `secrets/test-users.txt`
(share that file privately):

```sh
.venv/bin/python scripts/seed.py                    # seed (or reset) the registry and users
.venv/bin/python scripts/seed.py --reset-passwords  # new passwords for the 3 test users
.venv/bin/python scripts/seed.py --delete           # remove everything the seed created
make seed-local                                     # same data in DynamoDB Local, no AWS
```

### Checking alerts

After the deploy, the subscription confirmations, and the seed:

```sh
make smoke-test DEVICE=wearable-0001 ENDPOINT=$(terraform -chdir=infra output -raw iot_endpoint) INGEST=1
```

The smoke test sends a valid `fall`. Expect, within a minute:

- one email to `res-0001`'s subscribers and one to each nurse subscriber (a fall is
  critical), and none to anyone else;
- one item under `RESIDENT#res-0001` in `silvercare-alerts`, and one object under
  `alerts/date=<today, UTC>/` in the history bucket;
- both `silvercare-alerts-dlq` and `silvercare-critical-alert-dlq` empty (the shared
  developer user can't read SQS: check that CloudWatch alarms
  `silvercare-alerts-dlq-not-empty` and `silvercare-critical-alert-dlq-not-empty` are
  OK);
- a datapoint for the `AlertDispatchLatency` metric (CloudWatch → Metrics →
  `SilverCare`).

Then run it with `DEVICE=wearable-0010` (unassigned): one item under
`RESIDENT#unassigned`, no caregiver email, and one nurse email at once. If a DLQ alarm email arrives, look at the queue's
messages and the `/aws/lambda/silvercare-critical-alert` and
`/aws/lambda/silvercare-alert-processor` logs.

Vitals alerts, from an assigned wearable:

```sh
make smoke-test DEVICE=wearable-0001 ENDPOINT=$(terraform -chdir=infra output -raw iot_endpoint) ALERTS=1
```

It sends two high heart-rate `vitals` (one **warning**, "High heart rate"), a device
`vitals_alert` at 140 bpm and one with no pulse (two **CRITICAL** emails, to the
caregivers and the nurses at once), then a third
high `vitals` that is suppressed (no email). Expect three caregiver emails, two nurse
emails at once, an "ESCALATED" nurse email for the warning about 5 minutes later, three new items under
`RESIDENT#res-0001`, and the suppressed one only as `suppressed` in the
`/aws/lambda/silvercare-alert-processor` log. Running it again within 30 minutes
suppresses the warning and both critical alerts. No warning email? The resident may
have a personal limit (`THRESHOLDS#<resident>` in `silvercare-registry`) above the
test readings (180–185 bpm): then they are normal and no warning is correct.

Escalation (warnings only): after a warning email, wait about 5 minutes. Each nurse
subscriber gets "SilverCare ESCALATED alert: …" if the warning is still `open` (the
`/aws/lambda/silvercare-alert-processor` log shows `escalated`). To check that an
acknowledged warning doesn't escalate, run `ALERTS=1`, then within 5 minutes open the
warning's item in DynamoDB → `silvercare-alerts` and set `status` to `acknowledged`: no
ESCALATED email, and the log shows `not_escalated` with reason `status:acknowledged`.
If an escalation fails (an AWS error), it is retried after 3 minutes, so the nurse
email can arrive about 8 minutes after the alert; after 5 failures it lands in
`silvercare-alerts-dlq` (alarm email). A check handled more than 20 minutes after the
alert was stored (e.g. one redriven from the DLQ later) emails nobody
(`not_escalated`, reason `stale`).

Subscriptions to `silvercare-alerts` and `silvercare-nurses` can only be added by the
deploy (Terraform); adding one in the console is denied on purpose.

Cancel ("I'm OK" on the wearable, `docs/features/alert-cancel.md`):

```sh
make smoke-test DEVICE=wearable-0001 ENDPOINT=$(terraform -chdir=infra output -raw iot_endpoint) CANCEL=1
```

It sends a `fall`, then an `alert_cancel` 5 s later. Expect the fall email to the
caregivers and the nurses (critical, at once), then "SilverCare update: Fall - res-0001
cancelled" to both (the nurses had been emailed). The alert item shows `status` `cancelled`, has no
`open_pk`, and has an `expires_at` 30 days out. A press cancels every open alert from
that wearable, so earlier test alerts that are still open get cancelled (and emailed)
too.

### Device problems (watchdog)

Every 5 minutes the `silvercare-watchdog` Lambda checks every assigned device
(`docs/features/watchdog.md`): no heartbeat for 15 minutes (**Device offline**),
battery below 20 % and not charging (**Low battery**), or a wearable off the wrist for
2 hours between 08:00 and 22:00 SGT (**Wearable not worn**). Each is emailed to the ops
subscribers and the resident's caregivers ("SilverCare device problem: …"); nurses see
it only in the portal. When the problem clears, the alert resolves itself with no
email. If 5 or more devices go offline in one run (e.g. the simulator stopped), ops get
one summary email instead of one per device; caregivers still get their own.

Live check, with an assigned device the simulator isn't sending for (a simulator
heartbeat with a normal battery would overwrite the test one):

```sh
make smoke-test DEVICE=wearable-0002 ENDPOINT=$(terraform -chdir=infra output -raw iot_endpoint) LOW_BATTERY=1
```

Within 5 minutes, "SilverCare device problem: Low battery - wearable-0002 (res-0002)"
reaches the ops subscribers and `res-0002`'s caregivers, and no nurse. Run the plain
smoke test (normal battery) and the alert resolves at the next run (the item shows
`status` `resolved`, `resolved_by_role` `system`), with no email. If no further
heartbeat comes, the device is reported offline about 15 minutes after the last one.
If runs fail for 15 minutes in a row, the `silvercare-watchdog-errors` alarm emails ops.

Device-problem emails come from their own topic, `silvercare-ops-devices` (encrypted;
`silvercare-ops` carries only CloudWatch alarms). After the deploy that adds it, every
ops subscriber gets one more AWS "Subscription Confirmation" email: confirm it, or
device problems reach caregivers only. The `silvercare-watchdog-not-running` alarm
emails ops if the watchdog hasn't run for 15 minutes.

### Possible fall at night

The watchdog also raises a **critical** "Possible fall" (caregivers and nurses at once)
when a bedroom unit's bed was vacated between 22:00 and 07:00 SGT, has been empty for
20 minutes, nothing is moving in the bedroom, and the resident's wearable isn't worn
(or has sent nothing for 15 minutes, or there is none). It resolves itself when the
resident is back in bed, with a "resolved: the resident is back in bed" email, and
"I'm OK" on the wearable cancels it (`docs/features/possible-fall.md`).

Live check at any time of day, with a resident whose wearable the simulator isn't
sending for (e.g. `res-0003`):

```sh
make smoke-test DEVICE=bedroom-0003 ENDPOINT=$(terraform -chdir=infra output -raw iot_endpoint) POSSIBLE_FALL=1
```

It prints a **test-mode item**: create it in DynamoDB → `silvercare-registry` →
Create item (JSON view). For that unit only, any time counts as night and the wait is
2 minutes; the item stops working 2 hours after the time in its `sk`. Within about
2–7 minutes, "SilverCare CRITICAL alert: Possible fall - res-0003" (with `Test mode:
yes`) reaches `res-0003`'s caregivers and the nurses. Then:

```sh
make smoke-test DEVICE=bedroom-0003 ENDPOINT=$(terraform -chdir=infra output -raw iot_endpoint) BACK_IN_BED=1
```

Within 5 minutes, "SilverCare update: Possible fall - res-0003 resolved" reaches the
same people. Delete the test-mode item afterwards.
