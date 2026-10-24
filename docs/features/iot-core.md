---
status: ready          # draft | ready | built
---

# IoT Core foundation

## Summary

Set up AWS IoT Core so the 40 devices in `docs/metrics.md` (simulated wearables and
bedroom sensors) can connect over MQTT with mutual TLS, each with its own X.509
certificate, and publish only under their own topics. Every device is registered as an
IoT thing with a type and groups. Nothing processes the messages yet: IoT rules, ingest
Lambdas, and storage are a later feature. Over-the-air firmware updates are the next
feature, `ota-updates` (`docs/features/ota-updates.md`), which builds on this one.

## Users and access

No Cognito group uses this feature. There is no API and no screen.

| Group | Can see | Can do |
|---|---|---|
| staff | nothing | nothing |
| caregiver | nothing | nothing |
| family | nothing | nothing |

How access to a specific resident is decided: not applicable. Devices know nothing about
residents (`docs/metrics.md`).

Who does what:

| Who | Does |
|---|---|
| Developer (local machine) | Runs the key script, commits the CSRs, runs the smoke test |
| CI (`deploy.yml`) | Applies the Terraform: things, certificates, policies |
| Devices | Connect and publish, authorised only by their certificate and IoT policy |
| Claude and its agents | Build the code. They can't read the private keys and don't run the smoke test |

## Acceptance criteria

### Device list

1. `infra/devices/devices.json` is the single list of devices, read by both Terraform
   (`jsondecode(file(...))`) and the scripts:

   ```json
   {
     "devices": [
       { "id": "wearable-0001", "type": "wearable", "canary": true },
       { "id": "wearable-0002", "type": "wearable", "canary": false },
       { "id": "bedroom-0001", "type": "bedroom", "canary": true }
     ]
   }
   ```

   It lists `wearable-0001`…`wearable-0020` and `bedroom-0001`…`bedroom-0020` (40
   devices). Only `wearable-0001` and `bedroom-0001` have `canary: true`.
2. Given an entry whose `id` doesn't match `^(wearable|bedroom)-[0-9]{4}$`, whose `id`
   prefix doesn't match its `type`, or a duplicate `id`, then `terraform plan` fails
   with an error naming the entry, and so does the key script.

### Registry (Terraform)

3. Thing types `wearable` and `bedroom` exist.
4. Every device in the list is an `aws_iot_thing` named by its `id`, with the matching
   thing type.
5. Static thing groups:
   - `wearables`: every wearable
   - `bedroom-sensors`: every bedroom sensor
   - `canary-wearables`: wearables with `canary: true`
   - `canary-bedroom-sensors`: bedroom sensors with `canary: true`

   A canary device is in both its type group and its canary group. There is one canary
   group per type because firmware is per type (see `ota-updates`).
6. For every device, an `aws_iot_certificate` is created from
   `infra/devices/csr/<id>.csr` (`csr = file(...)`, `active = true`) and attached to
   the thing and to its type's policy. No `aws_iot_certificate` in the repo is created
   without a CSR, so no private key ever appears in Terraform state.
7. Given a device in the list with no CSR file, `terraform plan` fails with an error
   that names the device and says to run `make device-keys`.
8. Outputs:
   - `iot_endpoint`: the account's ATS data endpoint (`data.aws_iot_endpoint`,
     `endpoint_type = "iot:Data-ATS"`)
   - `device_certificates`: map of device ID → certificate PEM.

   Both are marked `sensitive`: certificates aren't secret, but `terraform apply` in
   `deploy.yml` prints every non-sensitive output to the public Actions log. The
   developer reads them with `terraform output`, which shows sensitive values.

### Device policies

9. Two IoT policies, one per device type: `silvercare-device-wearable` and
   `silvercare-device-bedroom`. Using policy variables, each allows only:
   - `iot:Connect` on `client/${iot:Connection.Thing.ThingName}`, with condition
     `iot:Connection.Thing.IsAttached = true`
   - `iot:Publish` on `topic/silvercare/v1/${iot:Connection.Thing.ThingName}/<message>`
     for that type's own messages only: wearable `vitals`, `fall`, `status`; bedroom
     `motion`, `bed`, `status`
10. The policies allow no `iot:Subscribe`, `iot:Receive`, shadow, or `iot:*` actions, and
    no `*` resources. (`ota-updates` adds the Jobs topics.)
11. Everything passes `make tf-check`. Any checkov skip has a comment explaining why.

### Key script

12. `scripts/device-keys.py`, run with `make device-keys` (in the dev container, no AWS
    access needed). For each device in `devices.json`:
    - no key at `devices/keys/<id>.key`: creates an EC P-256 private key (file mode
      `0600`) and a CSR at `infra/devices/csr/<id>.csr` with subject `CN=<id>`
    - key exists but no CSR: recreates the CSR from the existing key
    - both exist: does nothing
13. It never overwrites or deletes an existing key. `--device <id>` handles one device
    only; an unknown ID exits non-zero.
14. It prints only device IDs, file paths, and what it did (created / skipped). It never
    prints key material.
15. It uses the `cryptography` package, pinned in `requirements-dev.txt`.
16. Unit tests in `scripts/tests/` (run by `make test`) cover the three cases above,
    `--device` with an unknown ID, file mode `0600`, `CN=<id>` in the CSR, that the
    CSR's public key matches the key, and that output contains no `PRIVATE KEY` text.
    Tests use a temporary directory, never `devices/keys/`.

### Key safeguards

17. `.gitignore` and `.dockerignore` both contain `devices/keys/`.
18. `.claude/settings.json` denies `Read(devices/keys/**)` and `Edit(devices/keys/**)`.
    `.claude/hooks/guard.sh` blocks Bash commands that contain `devices/keys`.
19. `checks.yml` gets a step that fails if any file tracked by git is under
    `devices/keys/` or contains `-----BEGIN` followed by `PRIVATE KEY-----`. The step
    prints only file paths, never contents.
20. `devices/AmazonRootCA1.pem` (Amazon's public root CA, which devices use to verify
    the server) is committed. The developer downloads it once from
    `https://www.amazontrust.com/repository/AmazonRootCA1.pem` (README step); builders
    don't fetch it.

### Smoke test

21. `make device-certs` reads `devices/keys/certificates.json` (the developer saves it
    from `terraform -chdir=infra output -json device_certificates`) and writes
    `devices/keys/<id>.crt` for every device. It prints only device IDs.
22. `scripts/iot-smoke-test.py`, run with
    `make smoke-test DEVICE=<id> ENDPOINT=<iot_endpoint>` in the dev container. It
    authenticates with the device's key and certificate (no AWS credentials), using
    `paho-mqtt` (pinned in `requirements-dev.txt`), MQTT over TLS on port 8883, and
    `devices/AmazonRootCA1.pem` as the CA.
23. It runs each check on a fresh connection and prints `PASS` / `FAIL` per check:
    1. connect with client ID = `<id>` → connected
    2. publish one valid synthetic `status` message (per `docs/metrics.md`, QoS 1) to
       its own topic → PUBACK received
    3. publish to `silvercare/v1/<another device id>/status` → broker closes the
       connection
    4. publish a message type its type isn't allowed (wearable → `motion`, bedroom →
       `vitals`) → broker closes the connection
    5. connect with a client ID that isn't `<id>` → connection refused or closed
24. Exit code `0` only if every check passes. It never prints payloads, keys, or
    certificates, only check names and results.
25. Unit tests for the smoke test's helpers (payload building, topic building, result
    reporting) and for `device-certs` run with `make test`, with no network.

### Docs

26. `README.md` gets an "IoT devices" section: generate keys, commit the CSRs, merge,
    get the endpoint and certificates, run the smoke test, add a device.
27. `CLAUDE.md` repo layout lists `infra/devices/`, `devices/`, and `scripts/`.

## Data

- Reads: none. No DynamoDB.
- Writes: none. No DynamoDB. `DEVICE#` registry items come later (seed script or
  onboarding).
- New access patterns or tables: none.

## IoT

From `docs/metrics.md` (already decided):

- Thing name = device ID = MQTT client ID, format `<type>-<4 digits>`.
- Topic `silvercare/v1/{device_id}/{message}`; QoS 1, retain false, max payload 8 KB.
- One X.509 certificate per device, used for mutual TLS on every connection.
- Devices publish only and subscribe to nothing (until `ota-updates`).

Decided in this spec:

- **Certificates from CSRs.** A local script creates a private key and a CSR for each
  device on the developer's machine. Terraform registers the certificate from the CSR
  (AWS signs it) and attaches it to the device's thing and policy. Private keys never
  leave that machine and never go into Terraform state.
- **Keys live in the repo folder, git-ignored** (`devices/keys/`), with the safeguards
  in criteria 17–19.
- **Fleet:** 20 wearables + 20 bedroom sensors, no spares.
- **One policy per device type**, so each device can publish only its own type's
  messages.
- Published messages go nowhere yet: no IoT rule matches them, so IoT Core drops them.

## API contract

None.

## Screens

None.

## Alerts and notifications

None.

## Privacy

- Personal data touched: none. Device IDs are opaque; smoke-test payloads are
  synthetic.
- Retention: a thing and its certificate exist while the device is in `devices.json`.
  Removing a device from the list and merging deletes both.
- Must not be logged or shown: private keys, anywhere (scripts, CI, Claude). The smoke
  test does not log payloads.

## Cost

- Things, thing types, groups, certificates, and policies: free.
- Smoke-test runs: a few connections and messages each, ≈ US$0.
- Running costs start with the simulator (a few US dollars a month if it runs 24/7,
  see `docs/metrics.md`).

## Out of scope

- IoT topic rules, ingest Lambdas, storing messages, and the S3 history path.
- The device simulator.
- Over-the-air updates (next feature: `ota-updates`).
- Fleet provisioning (devices fetching their own certificate on first boot). May be
  considered later, once the simulator approach is settled.
- `DEVICE#` items in DynamoDB.
- Certificate rotation and revocation.
- IoT Core logging to CloudWatch, Device Defender, fleet indexing, device shadows.
- Any UI or API for devices.

## Open questions

None.
