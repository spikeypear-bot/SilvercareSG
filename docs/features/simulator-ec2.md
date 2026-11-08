---
status: built          # draft | ready | built
---

# Always-on simulator on EC2

## Summary

Run the existing simulator (`simulator/src/main.py`, `docs/features/simulator-core.md`)
24/7 on one small EC2 instance in `ap-southeast-1` until 2026-11-30, so the live system
always has device data. The 40 device keys and certs live in SSM Parameter Store
(SecureString, free) instead of only on a developer's laptop. Developers trigger demo
scenarios by opening a Session Manager session and typing into the running simulator. Follow-up to
`docs/features/device-alert-limits.md`.

## Decisions (user, 2026-11-09)

| Topic | Decision |
|---|---|
| EC2 | Agreed exception to "no EC2 / no fixed hourly cost" in `CLAUDE.md`, ending 2026-11-30 |
| Key store | SSM Parameter Store SecureString, standard tier (free). Changed from Secrets Manager (~US$16/month) the same day |
| Network | Default VPC, public IPv4, no inbound ports |
| Demos | Simulator runs in `tmux`; a developer attaches via SSM Session Manager and types scenario commands (`simulator-alert-scenarios` criterion 3). No new simulator code |
| Run window | 24/7 until 2026-11-30, then removed by a PR |
| Who | The shared developer IAM user may upload the keys and open sessions |
| Code | The instance clones the public repo's `main` at boot; updates by `git pull` + restart in a session. Nothing new in CI |

## What exists today (checked in code, 2026-11-09)

- `make simulate ENDPOINT=<host>` runs a live run in the dev container, keys read from
  `<keys-dir>/<device_id>.crt` / `.key`, CA `devices/AmazonRootCA1.pem`. A missing
  key file exits 2 listing device IDs only (`simulator-core` criteria 2, 16). Standard
  library + `paho-mqtt==2.1.0`, no AWS SDK.
- One MQTT connection per device (client ID = device ID), reconnect backoff 1–60 s,
  messages due while disconnected are dropped (`simulator-core` criterion 17). Live
  wearables subscribe to their shadow (`simulator/src/live.py`).
- Typed scenario commands work only when stdin is a terminal
  (`simulator/src/commands.py`, `simulator-alert-scenarios` criterion 3).
- AWS IoT allows one connection per client ID: a second simulator for the same device
  (e.g. on a laptop) disconnects the first, and they keep knocking each other off.
- The watchdog marks a device offline after 15 minutes without a `status`; 5 or more in
  one run → one ops summary email, plus each resident's caregivers get their own email;
  device problems never escalate to nurses (`docs/features/watchdog.md`).
- Live-run precondition: `scripts/seed.py` run against the live registry, otherwise two
  wearables raise warnings that escalate to the nurses by email (`simulator-core`,
  Alerts and notifications).
- Developer scripts that call real AWS run on the host with the developer's credentials
  (`scripts/requirements.txt`, `boto3==1.43.114`; `Makefile` seed comment).
- The developer IAM policy (`docs/iam/developer-policy.json`) has no Parameter Store,
  Session Manager, or KMS permissions.
- Device keys are EC P-256 (`scripts/device-keys.py`): key + cert PEM ≈ 1.3 KB, inside
  the 4096-byte standard-tier limit.
- Parameter Store (AWS docs, "AWS KMS encryption for … SecureString parameters"):
  standard SecureString ≤ 4096 bytes, encrypted with the AWS managed key `aws/ssm` by
  default. The `aws/ssm` key policy can't be changed and gives Decrypt to every IAM
  principal in the account ("Restricting access to Parameter Store parameters using
  IAM policies"), so **`ssm:GetParameter*` on the parameter path is the only lock**.
  Fine-grained KMS control would need a customer managed key (US$1/month): not used.
- The CI plan role has `ReadOnlyAccess` (which includes `ssm:GetParameter*`) minus
  `DenyDataReads` in `infra/bootstrap/ci-plan-role.tf`, and any PR from this repo can
  use it.

## Users and access

No Cognito group uses this feature. No API, no screen.

| Who | Does |
|---|---|
| Developer (shared IAM user) | Uploads key values (`scripts/upload-device-keys.py`, host); opens Session Manager sessions on the simulator instance only; attaches to the simulator, types scenarios, `git pull` + restart |
| Simulator instance (instance role) | Reads the 40 device-key parameters; registers with Systems Manager. Nothing else |
| Devices (simulated) | Connect and publish, authorised only by their certificate and IoT policy (unchanged) |
| Claude | Builds and tests. Never runs the upload script, never reads `secrets/` (`guard.sh`), can't reach AWS |
| Staff / caregiver / ops | Nothing new; they see the simulated data as today |

How access to a specific resident is decided: not applicable (devices know nothing about
residents).

## Acceptance criteria

### Key parameters

1. One SSM parameter per device in `infra/devices/devices.json` (40), named
   `/silvercare/device-keys/<device_id>`, type `SecureString`, tier `Standard`, key
   `aws/ssm` (the default). Value: JSON `{"crt": "<PEM>", "key": "<PEM>"}`.
2. The parameters are **not** in Terraform: `aws_ssm_parameter` needs a value and reads
   it back into state on refresh, which would put private keys in state. They are data
   written by the upload script, like registry items written by `scripts/seed.py`. A
   one-line note in `CLAUDE.md` records this exception to "all resources in Terraform".
3. `scripts/upload-device-keys.py --keys-dir DIR [--devices id,id]` (host, developer
   credentials, region fixed to `ap-southeast-1`; device IDs from
   `infra/devices/devices.json`) reads `DIR/<id>.crt` and `.key` and calls
   `PutParameter` (`Overwrite=True`, `Type=SecureString`, `Tier=Standard`) for each.
   Before uploading anything it checks every selected device has both files, that each
   looks like PEM (`-----BEGIN`), and that the JSON value is ≤ 4096 bytes; otherwise
   exits 2 listing device IDs only. Prints one line per device: `<device_id> uploaded`
   or `<device_id> failed <error code>`; exit 1 if any failed. Never prints file
   contents.
4. `scripts/upload-device-keys.py --delete [--devices id,id]` deletes those parameters
   (missing ones reported as `<device_id> not found`, not an error). Used at removal on
   2026-11-30.
5. `make upload-device-keys [ARGS=...]` runs it on the host (not the dev container,
   which has no AWS credentials), and is blocked for Claude in `.claude/hooks/guard.sh`
   (like `device-keys`); `CLAUDE.md`'s guard list is updated.

### Instance

6. One `t4g.micro` instance (arm64), Amazon Linux 2023 latest AMI from the public SSM
   parameter, `ignore_changes = [ami]` and `user_data_replace_on_change = false` so a new
   AMI or edited user data never replaces it on deploy. Default VPC, public IPv4.
   Root volume 8 GB gp3, encrypted. IMDSv2 required. Detailed monitoring off.
7. Security group: **no inbound rules**. Outbound TCP 443 (SSM, GitHub, dnf, PyPI) and
   8883 (IoT) to anywhere; nothing else.
   The default VPC's network ACL has no inbound allow rule (found on the first boot,
   2026-11-09), so Terraform adds inbound rule 100: TCP 32768-65535 from anywhere, for
   replies only (NACLs are stateless; the security group still allows no inbound).
8. Instance role: `ssm:GetParameter` on `parameter/silvercare/device-keys/*`, plus
   Session Manager's documented minimum written out (not `AmazonSSMManagedInstanceCore`,
   which allows `ssm:GetParameter*` on every parameter): `ssm:UpdateInstanceInformation`
   and `ssmmessages:CreateControlChannel` / `CreateDataChannel` / `OpenControlChannel` /
   `OpenDataChannel` on `*` (AWS supports only `*` for them; AWS docs, "Create a custom
   IAM role for Session Manager"). No IoT, DynamoDB, S3, or other permissions.
8a. `infra/bootstrap/ci-plan-role.tf` gets a deny of `ssm:GetParameter`,
   `GetParameters`, `GetParametersByPath`, `GetParameterHistory` on
   `parameter/silvercare/*` (scoped, so plans can still read AWS's public AMI
   parameter). The owner applies it by hand **before** uploading keys.
9. User data (first boot): installs `git`, `tmux`, `python3.13` and `python3.13-pip` from
   the AL2023 repo (AL2023 docs, "Python in AL2023": 3.13 is a version-namespaced
   package, `/usr/bin/python3.13`; never change `/usr/bin/python3`, which stays 3.9); installs
   `paho-mqtt==2.1.0` and `boto3` at the version in `scripts/requirements.txt`;
   creates a non-login user `simulator`; clones the public repo's `main` to
   `/opt/silvercare` (owned by `simulator`); installs and enables the systemd service
   from criterion 10. The IoT endpoint comes from Terraform (`aws_iot_endpoint`,
   `iot:Data-ATS`) into the unit's start command. Python packages go in a venv
   (`/opt/silvercare-venv`), never the system Python.

### Service

10. systemd service `silvercare-simulator`, user `simulator`, enabled at boot:
   - before start, `simulator/deploy/fetch_keys.py` reads the 40 parameters
     (`GetParameter`, `WithDecryption=True`; device IDs from the cloned
     `infra/devices/devices.json`) and writes `<id>.crt` / `<id>.key` into
     `/run/silvercare-keys` (systemd `RuntimeDirectory`: tmpfs, dir `0700`, owner
     `simulator`, removed by systemd when the service stops; files `0600`),
     deleting any old files there first. A missing parameter or bad JSON → exit
     non-zero listing device IDs only; nothing logged about contents.
   - then starts `python3.13 simulator/src/main.py --endpoint $ENDPOINT
     --keys-dir /run/silvercare-keys` inside a detached `tmux` session named `sim`, so
     stdin is a terminal and typed commands work.
   - if the simulator or key fetch exits for any reason, it is started again after 60 s,
     forever (no start limit), so missing keys mean a quiet retry loop, not a fast one.
   - stop: the simulator gets SIGINT (its clean shutdown, `simulator-core` criterion 19),
     then the key files are deleted.
11. Simulator output (its per-minute line) stays in the `tmux` scrollback only; the key
    fetch and restarts go to the journal (`journalctl -u silvercare-simulator`). No
    CloudWatch Logs agent.
12. `docs/simulator-ec2.md` (runbook) states: how to open a session
    (`aws ssm start-session --target <id>`, needs the Session Manager plugin), attach
    (`sudo -u simulator tmux attach -t sim`), detach (Ctrl+B then D: **Ctrl+C stops the
    simulator**, systemd restarts it in 60 s), update (`git pull` as `simulator`, then
    `sudo systemctl restart silvercare-simulator`), and that running `make simulate` on a
    laptop needs `sudo systemctl stop silvercare-simulator` first, or the two fight over
    each device's connection. Stopping it for more than 15 minutes sends offline emails.

### Developer access

13. `docs/iam/developer-policy.json` gets: `ssm:PutParameter` and `ssm:DeleteParameter`
    on `parameter/silvercare/device-keys/*` only (never `GetParameter*`, so the user can
    write keys but not read them back; no KMS statement needed with `aws/ssm`);
    `ssm:StartSession` on the simulator instance (by tag `Project=silvercare,
    Role=simulator`) and the `SSM-SessionManagerRunShell` document;
    `ssm:TerminateSession` / `ResumeSession` on the caller's own sessions;
    `ec2:DescribeInstances` to find the instance ID. The user applies it by hand
    (account owner), as today.
14. Terraform outputs the instance ID (not secret).

### Tests

15. `fetch_keys.py`, with a fake SSM client: writes both files per device with modes `0600`; clears
    old files; a missing parameter / bad JSON / missing field exits non-zero and its
    output holds device IDs but no PEM text.
16. `upload-device-keys.py`, with a fake SSM client: uploads all as `SecureString`; missing file,
    non-PEM, or value over 4096 bytes exits 2 before any upload; a failed put is
    reported with the code and exit 1; `--delete` deletes and reports missing ones;
    output never contains PEM text. Tests use synthetic, obviously fake PEM strings,
    never real keys.
17. `make tf-check` passes (checkov: any skip has a one-line reason in the code, e.g.
    no CloudWatch Logs for sessions).

## Data

- Reads: device-key parameters (instance only).
- Writes: none new. Simulated telemetry is stored like any device data (latest 5 per
  device and type in DynamoDB, history in S3), now 24/7: ~1.3 M messages/month.
- New access patterns or tables: none.

## IoT

No change to topics, payloads, rules, or device policies. Same 40 certificates, same
client IDs, connecting from the instance instead of a laptop.

## API contract

None.

## Screens

None.

## Alerts and notifications

- Normal running: none expected (seeded thresholds), as `simulator-core`.
- **Order for first start** (runbook): merge → run `scripts/seed.py` against the live
  registry → `make upload-device-keys ARGS="--keys-dir secrets/device-keys"`. Until keys are uploaded the service retries
  every 60 s without connecting. Uploading before seeding → `wearable-0004` and
  `wearable-0011` warnings escalated to the nurses by email.
- Demo scenarios: the emails each scenario causes are as in
  `simulator-alert-scenarios` (unchanged).
- Restart / `git pull` / reboot: devices are back within seconds to a couple of
  minutes, under the 15-minute offline cutoff: no emails.
- Instance down 15+ minutes (stopped, AWS maintenance, removed on 2026-11-30): all 40
  devices go offline → one ops mass-offline summary email, plus for each of the 20
  residents an email to each linked caregiver per offline device (wearable and bedroom
  unit: up to 2 each). Nurses get none. Removal order: PR removing the instance merged →
  `make upload-device-keys ARGS=--delete`.

## Privacy

- Personal data touched: none (all synthetic).
- Device private keys: in Parameter Store (SecureString, `aws/ssm`, Singapore), in tmpfs on the
  instance while running (gone on stop/reboot), never in state, logs, CI, or output.
  Anyone with a session on the instance can read them (the shared developer user):
  acceptable while devices are simulated.
- No session logging (CloudWatch/S3 cost); CloudTrail records `StartSession` by the
  shared user (can't tell developers apart, as `CLAUDE.md` notes).
- Retention: parameters deleted with `--delete` at removal (no recovery window); the
  local `secrets/device-keys/` copy stays.

## Cost

Singapore prices, per month 24/7 (730 h). EC2 and EBS from AWS's public price data
(`b0.p.awsstatic.com` EC2/EBS price files, checked 2026-11-09); IPv4 from AWS's
public IPv4 pricing announcement:

| Item | ~US$/month |
|---|---|
| Parameter Store, 40 standard parameters | 0 |
| t4g.micro, US$0.0106/h | 7.74 |
| Public IPv4, US$0.005/h | 3.65 |
| EBS 8 GB gp3, US$0.096/GB-month | 0.77 |
| IoT + Lambda/SQS/logs from 24/7 traffic (`simulator-core`) | 2.00 |
| KMS calls (`aws/ssm`, ~40 per restart), SSM sessions | ~0 |
| **Total** | **~14** |

For 2026-11-10 → 11-30 (~21 days): about **US$10** from the ~US$120 credits, about 35%
of the US$40/month budget on its own (counted before credits).

## Out of scope

- New simulator code (remote commands, CloudWatch output, health checks).
- Auto-deploy of simulator changes from CI; instance replacement on code change.
- IPv6-only networking, NAT, VPC endpoints.
- Session logging, alarms on the instance, auto-recovery beyond systemd restarts.
- Running past 2026-11-30.
- Real devices.

## Open questions

- None.
