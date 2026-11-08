# Running the EC2 simulator

How to start, demo, update, and remove the always-on simulator
(`docs/features/simulator-ec2.md`). Runs 24/7 until 2026-11-30, about US$12/month.

You need: AWS credentials for the shared developer user (the policy in
`docs/iam/developer-policy.json` applied), the AWS CLI with the
[Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html),
and `pip install -r scripts/requirements.txt` on the host.

## First start (in this order)

1. **Account owner:** apply `infra/bootstrap/` (adds `DenyProjectParameterReads`, so PR
   plans can't read the keys) and the updated developer policy. Do this **before** step 4.
2. Merge the PR. The deploy creates the instance; it installs everything and starts
   the service, which retries every 60 s until the keys exist.
3. Seed the live registry: `python scripts/seed.py`. Skipping this makes
   `wearable-0004` and `wearable-0011` raise warnings that are emailed to the nurses.
4. Upload the keys: `make upload-device-keys`. Expect `<device_id> uploaded` × 40.
   Within a minute the simulator connects all 40 devices.

## Find the instance and connect

```sh
aws ec2 describe-instances --region ap-southeast-1 \
  --filters Name=tag:role,Values=simulator Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text
aws ssm start-session --region ap-southeast-1 --target <instance-id>
```

Inside the session:

```sh
sudo -u simulator tmux attach -t sim
```

You see the per-minute line (`connected 40/40 | sent ...`). Type a scenario, e.g.
`help`, or `fall wearable-0003` (names in `docs/features/simulator-alert-scenarios.md`).
Every scenario sends real alerts and emails.

- **Detach: Ctrl+B, then D.** The simulator keeps running.
- **Ctrl+C stops the simulator.** systemd starts it again after 60 s (no emails: offline
  alerts need 15 minutes).

## Update the code

```sh
sudo -u simulator git -C /opt/silvercare pull
sudo systemctl restart silvercare-simulator
```

The instance runs whatever `main` was at the last pull, not what CI deployed.

## Check it

```sh
systemctl status silvercare-simulator
journalctl -u silvercare-simulator -n 50   # key fetch and restarts (device IDs only)
```

## Demo from your laptop instead

AWS IoT allows one connection per device ID. Before `make simulate` on a laptop:
`sudo systemctl stop silvercare-simulator`, and start it again afterwards. Stopped for
more than 15 minutes, all 40 devices go offline: one ops summary email, plus emails to
each resident's caregivers.

## Remove on 2026-11-30

1. PR deleting `infra/simulator.tf` and `infra/simulator/`. Merging terminates the
   instance: after 15 minutes, the offline emails above.
2. `make upload-device-keys ARGS=--delete` deletes the 40 parameters.
