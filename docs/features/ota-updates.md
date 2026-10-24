---
status: ready          # draft | ready | built
---

# Over-the-air firmware updates

## Summary

Let a developer push a firmware update to a group of devices with AWS IoT Jobs: a
script uploads the firmware file to a private S3 bucket and creates a job that rolls out
in stages and stops by itself if too many devices fail. Devices receive jobs on their
own Jobs topics, download the file through a short-lived presigned URL, and report the
result. Builds on `iot-core` (`docs/features/iot-core.md`), which must be built and
applied first.

## Users and access

No Cognito group uses this feature. There is no API and no screen.

| Group | Can see | Can do |
|---|---|---|
| staff | nothing | nothing |
| caregiver | nothing | nothing |
| family | nothing | nothing |

How access to a specific resident is decided: not applicable.

Who does what:

| Who | Does |
|---|---|
| Developer (shared IAM user `silvercare-developers`, own terminal) | Runs the update script: upload firmware, start, check, cancel jobs |
| CI (`deploy.yml`) | Applies the Terraform: bucket, presign role, device policy changes |
| IoT Jobs (AWS) | Assumes the presign role to sign download URLs |
| Devices | Receive and answer jobs for their own thing name only |
| Claude and its agents | Build the code. They never run the update script (no AWS access) |

Because the developer IAM user is shared, CloudTrail can't tell which developer
started a job (accepted, see `CLAUDE.md`).

## Acceptance criteria

### Firmware bucket (Terraform)

1. S3 bucket `silvercare-firmware-753674274065` in `ap-southeast-1`:
   - all public access blocked, object ownership `BucketOwnerEnforced`
   - versioning on
   - SSE-S3 encryption (AWS-managed key)
   - bucket policy denies any request without TLS (`aws:SecureTransport = false`)
   - no lifecycle rule: every firmware file is kept
   - `force_destroy = false`
2. Object key layout: `firmware/<device_type>/<version>/firmware.bin`.
3. Any checkov skip (e.g. access logging, cross-region replication, KMS key, event
   notifications) has a comment with the reason (cost, data stays in Singapore).

### Presign role (Terraform)

4. IAM role `silvercare-iot-jobs-presign`, trusted only by `iot.amazonaws.com` with
   condition `aws:SourceAccount = 753674274065`. Its only permission is `s3:GetObject`
   on `arn:aws:s3:::silvercare-firmware-753674274065/firmware/*`.
5. Output `firmware_bucket` (bucket name) and `iot_jobs_presign_role_arn`.

### Device policies (change to `iot-core`)

6. Both device policies additionally allow, for the device's own thing name only:
   - `iot:Subscribe` on `topicfilter/$aws/things/${iot:Connection.Thing.ThingName}/jobs/*`
   - `iot:Receive` and `iot:Publish` on
     `topic/$aws/things/${iot:Connection.Thing.ThingName}/jobs/*`
7. Nothing else changes in the policies. A device still can't touch another device's
   Jobs topics.

### Job document (contract, added to `docs/metrics.md`)

8. `docs/metrics.md` gets a "Firmware updates (IoT Jobs)" section with the job
   document, the device behaviour, and the status reasons below. Its "Devices publish
   only" rule and "Out of scope for v1" list are updated: devices subscribe to their own
   Jobs topics, and firmware jobs are the only thing pushed to devices.
9. Job document:

   ```json
   {
     "schema_version": 1,
     "operation": "firmware_update",
     "device_type": "wearable",
     "version": "1.1.0",
     "url": "${aws:iot:s3-presigned-url:https://silvercare-firmware-753674274065.s3.ap-southeast-1.amazonaws.com/firmware/wearable/1.1.0/firmware.bin}",
     "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
     "size_bytes": 1024
   }
   ```

   IoT Jobs replaces the `url` placeholder with a presigned URL valid for 3600 s.
10. Device behaviour:
    1. On connect, subscribe to `$aws/things/<id>/jobs/notify-next` and publish to
       `$aws/things/<id>/jobs/start-next`, which sets the next job `IN_PROGRESS`.
    2. Check `operation` and `device_type`, download the file, check `size_bytes` and
       `sha256`, install.
    3. Report `SUCCEEDED` with `statusDetails: { "fw_version": "<version>" }`, then send
       a `status` message right away, so the new `fw_version` shows up.
    4. On any problem, report `FAILED` with `statusDetails: { "reason": "<reason>" }`.
       Reasons: `wrong_device_type`, `unsupported_operation`, `download_failed`,
       `checksum_mismatch`, `install_failed`.
    5. `statusDetails` never holds anything but `fw_version` or `reason`.

### Update script

11. `scripts/fleet-update.py`, run by the developer on their own machine with their own
    AWS credentials (not in the dev container, which has none). It uses `boto3`, pinned
    in `scripts/requirements.txt`, and region `ap-southeast-1`. Commands:
    - `start --type wearable|bedroom --version <v> --file <path> [--canary]`
    - `status <job-id>`
    - `cancel <job-id>`
12. `start`:
    1. Checks that `--version` matches `^[0-9]+\.[0-9]+\.[0-9]+$` and is at most 32
       characters, and that `--file` exists and is at most 8 MB.
    2. Computes `sha256` and `size_bytes`.
    3. Refuses (exit non-zero, nothing uploaded) if
       `firmware/<type>/<version>/firmware.bin` already exists. Versions are never
       overwritten.
    4. Uploads the file.
    5. Creates the job (below) and prints the job ID.
13. Job target: `wearables` or `bedroom-sensors` by `--type`; with `--canary`,
    `canary-wearables` or `canary-bedroom-sensors` instead. No other targets.
14. Job settings (from the decisions below):
    - job ID `fw-<type>-<version with dots as dashes>-<UTC yyyymmddHHMMSS>`, e.g.
      `fw-wearable-1-1-0-20261020083000`
    - `targetSelection = SNAPSHOT` (devices in the group when the job starts)
    - `presignedUrlConfig`: the presign role, `expiresInSec = 3600`
    - `jobExecutionsRolloutConfig.exponentialRate`: `baseRatePerMinute = 5`,
      `incrementFactor = 2`, `rateIncreaseCriteria.numberOfSucceededThings = 5`;
      `maximumPerMinute = 40`
    - `abortConfig`: `failureType = ALL`, `action = CANCEL`, `thresholdPercentage = 20`,
      `minNumberOfExecutedThings = 5`
    - `timeoutConfig.inProgressTimeoutInMinutes = 15`
15. `status <job-id>` prints the job status and the number of devices per execution
    status (queued, in progress, succeeded, failed, timed out, rejected, cancelled),
    and the device IDs that failed or timed out with their `reason`.
16. `cancel <job-id>` cancels the job without `force`, so devices already installing
    finish and report.
17. The script never prints presigned URLs, credentials, or AWS error bodies beyond the
    error code.
18. Unit tests in `scripts/tests/` (run by `make test`): moto for S3 (upload, refuse
    existing version), botocore `Stubber` for IoT asserting the exact `CreateJob`
    request (target ARN per `--type`/`--canary`, rollout, abort, timeout, presign
    config, job document), version and file validation, job ID format, and `status`
    output.

### Smoke test (extends `iot-core`)

19. `make smoke-test DEVICE=<id> ENDPOINT=<endpoint> JOB=1` adds a jobs check after the
    `iot-core` checks: it connects as the device, waits up to 5 minutes for a job on
    `notify-next`, runs the device behaviour above (download, check size and sha256,
    report `SUCCEEDED` with the new `fw_version`), and prints `PASS` / `FAIL`. Given a
    file whose sha256 doesn't match, it reports `FAILED` with `checksum_mismatch`.
20. Its job-handling logic has unit tests (with no network): document parsing, each
    failure reason, and the status update payloads.

### Developer IAM

21. `docs/iam/developer-policy.json` gets one new statement, `RunFirmwareUpdates`:
    - `s3:PutObject` on `arn:aws:s3:::silvercare-firmware-753674274065/firmware/*`
    - `iot:CreateJob` on `job/fw-*` and the four thing groups from `iot-core`
    - `iot:CancelJob` on `job/fw-*`
    - `iam:PassRole` on the presign role, with condition
      `iam:PassedToService = iot.amazonaws.com`

    (Reading jobs, executions, and S3 objects is already covered by the existing
    `iot:Describe*` / `iot:Get*` / `iot:List*` and `ReadProjectBuckets` statements.)
22. The developer group's inline policy is a hand-made resource, so after merging, the
    account owner pastes the updated JSON into IAM by hand. This step goes in the README.

### Docs

23. `README.md` "IoT devices" section gets "Updating firmware": install the script's
    requirements, run a canary update, check it, roll out to the whole group, cancel.

## Data

- Reads: none from DynamoDB.
- Writes: none to DynamoDB. Firmware files are written to S3 (bucket above).
- New access patterns or tables: none.

## IoT

- New topics, per device: `$aws/things/<id>/jobs/*` (AWS IoT Jobs MQTT API).
- No change to `silvercare/v1/...` topics or payloads. `status` keeps reporting
  `fw_version`.
- Contract change in `docs/metrics.md` (criterion 8).

Decided in this spec:

- **Jobs are started by a script the developer runs** from their own terminal with
  their own AWS credentials. Claude and CI never run it. Jobs are actions, not
  Terraform resources.
- **Targets:** all wearables, all bedroom sensors, or the canary group of either type.
- **Rollout:** start with 5 devices a minute, and double the rate each time 5 more
  devices succeed, up to 40 a minute. (IoT Jobs raises the rate by succeeded devices,
  not by elapsed time.) A full group of 20 takes about 3–4 minutes.
- **Abort:** cancel the job once more than 20% of executed devices have failed, timed
  out, or rejected it, counted only after at least 5 devices have finished. A canary
  group (1 device) never auto-aborts; check it with `status`.
- **Timeout:** 15 minutes per device.
- **Permissions:** the shared developer IAM user may run updates.
- **Retention:** every firmware file is kept forever (versioning on, no lifecycle), so
  any version can be rolled back to.

## API contract

None.

## Screens

None.

## Alerts and notifications

None. A failed or aborted rollout shows only in `fleet-update.py status` and the IoT
console.

## Privacy

- Personal data touched: none. Firmware files and job documents contain no personal
  data; `statusDetails` holds only `fw_version` or `reason`.
- Retention: firmware files are kept indefinitely. Job records follow IoT Jobs' own
  retention.
- Must not be logged or shown: presigned URLs, AWS credentials.

## Cost

- IoT Jobs: US$0.003 per device per job. Canary update ≈ US$0.003; one whole group (20)
  ≈ US$0.06; both groups ≈ US$0.12.
  ([pricing](https://aws.amazon.com/iot-device-management/pricing/), US rate; check
  `ap-southeast-1` in the console.)
- S3: a few small files, ≈ US$0.
- MQTT messages for Jobs: a handful per device per update, ≈ US$0.

## Out of scope

- Any UI or API for updates.
- Targeting single named devices.
- Signed firmware (AWS Signer / code signing). The sha256 check catches corruption,
  not a malicious file uploaded by someone with developer access.
- Real firmware: the smoke test and the simulator install a synthetic file.
- Automatic rollback to the previous version on failure.
- Other job types (reboot, config changes, commands).
- Scheduled jobs and maintenance windows.
- The full simulator (it will reuse the smoke test's job handling).

## Open questions

None.
