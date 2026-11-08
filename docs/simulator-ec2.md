# The EC2 simulator

**The 40 simulated devices run 24/7 on an EC2 instance until 2026-11-30**
(`docs/features/simulator-ec2.md`, about US$14/month). The live system always has
device data. **Teammates don't need to run the simulator.** One person (the owner,
`spikeypear-bot` on GitHub) runs and looks after it; ask them for demo scenarios or if
something looks wrong.

## For teammates

- **Don't run `make simulate` against AWS.** AWS IoT allows one connection per device
  ID, so a second simulator kicks the EC2 one's devices off and the two keep
  disconnecting each other. `make simulate-dry-run` is fine (no AWS).
- **Is it running?** Open the SilverCare site's Devices tab: devices showing recent data
  means yes. If it stops for 15+ minutes, devices show offline and ops and the
  residents' caregivers get offline emails: tell the owner.
- **Want a fall, heart-rate spike, etc. for a demo?** Ask the owner: they type it into
  the running simulator. Every scenario sends real alerts and emails.

## For the owner

You need: AWS credentials (`silvercare-kakis`) and the AWS CLI with the
[Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html).
On Ubuntu:

```sh
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o /tmp/session-manager-plugin.deb
sudo dpkg -i /tmp/session-manager-plugin.deb
```

### Connect

```sh
aws ec2 describe-instances --region ap-southeast-1 \
  --filters Name=tag:role,Values=simulator Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text
aws ssm start-session --region ap-southeast-1 --target <instance-id>
```

Leaving the session (`exit`) never stops the simulator: systemd runs it.

### Is it running?

```sh
sudo -u simulator tmux ls                         # "sim: 1 windows ..." = running
systemctl status silvercare-simulator --no-pager  # "active (running)", since-time steady
sudo journalctl -u silvercare-simulator -n 30 --no-pager   # key fetch, restarts (IDs only)
```

`activating (auto-restart)` or a rising restart counter means it's crashing: read the
journal.

### Watch it and run demo scenarios

```sh
sudo -u simulator tmux attach -t sim
```

One line a minute (`connected 40/40 | sent ...`). Type `help`, or a scenario such as
`fall wearable-0003` (list in the README, "Alert scenarios").

- **Leave: Ctrl+B, then D.** It keeps running.
- **Ctrl+C stops the simulator.** systemd starts it again after 60 s (no emails; offline
  needs 15 minutes).

### Update the code

The instance runs `main` as of the last pull, not what CI deployed:

```sh
sudo -u simulator git -C /opt/silvercare pull
sudo systemctl restart silvercare-simulator
```

### Pause it (e.g. to run `make simulate` from a laptop)

```sh
sudo systemctl stop silvercare-simulator    # start again: sudo systemctl start ...
```

More than 15 minutes stopped: one ops summary email plus emails to each resident's
caregivers.

### Keys

The 40 device keys are SSM parameters `/silvercare/device-keys/<id>`, uploaded from
`secrets/device-keys/` with `make upload-device-keys` (host). Re-run it after replacing a
key, then `sudo systemctl restart silvercare-simulator`.

### Rebuilt instance (first start)

Only if the instance is ever replaced: it sets itself up on first boot (packages, repo,
service). Then `python scripts/seed.py` (or two wearables email the nurses) and
`make upload-device-keys`; within a minute it connects.

### Known problems (fixed)

- **No internet** (`TargetNotConnected`, `dnf` timeouts): the default VPC's network ACL
  had no inbound allow rule; `aws_network_acl_rule.simulator_replies` adds one (#44).
- **Restarts every 60 s, `tmux ls` says no sessions**: tmux used the `simulator` user's
  `nologin` shell. The unit now sets `Environment=SHELL=/bin/sh`; the first instance was
  patched by hand the same way (2026-11-09).
- **Setup failed on first boot**: re-run it once with
  `sudo bash /var/lib/cloud/instance/scripts/part-001`.

## Remove on 2026-11-30

1. PR deleting `infra/simulator.tf` and `infra/simulator/`. Merging terminates the
   instance; after 15 minutes, the offline emails above.
2. `make upload-device-keys ARGS=--delete` deletes the 40 key parameters.
