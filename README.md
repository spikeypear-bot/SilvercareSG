Silvercare monitoring system

## IoT devices

Every device (`wearable-0001`…`wearable-0020`, `bedroom-0001`…`bedroom-0020`) is an
AWS IoT thing with its own X.509 certificate, and connects over MQTT with mutual TLS.
The device list is `infra/devices/devices.json`; the feature spec is
`docs/features/iot-core.md`.

Each device's **private key** stays on your machine in `devices/keys/`. That folder is
git-ignored and must never be committed (CI fails if a private key is tracked). Only
the CSRs in `infra/devices/csr/` are committed. Terraform sends them to AWS, which signs
the certificates.

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
   `git status` must not show anything under `devices/keys/`. The plan comment should
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
terraform output -json device_certificates > ../devices/keys/certificates.json
cd .. && make device-certs
make smoke-test DEVICE=wearable-0001 ENDPOINT=$(terraform -chdir=infra output -raw iot_endpoint)
```

Every check should print `PASS`.

### Checking ingest

Checks that messages reach the `silvercare-latest` table (`docs/features/ingest-live.md`).
The second step reads DynamoDB, so it runs on your machine with your own AWS
credentials, not in the dev container. Once, install its requirements on the host:

```sh
python3 -m venv .venv && .venv/bin/pip install -r scripts/requirements.txt
```

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
`--history` to check only the live table. If it prints `FAIL`, look at the Lambda logs (`/aws/lambda/silvercare-*`) and the
`silvercare-ingest-failed` queue in the console; for history, the
`/aws/lambda/silvercare-history` logs and the `silvercare-history-dlq` queue.

### Adding, removing, or replacing a device

- **Add:** add an entry to `infra/devices/devices.json`, run
  `make device-keys DEVICE=<id>`, then commit the new CSR and merge.
- **Remove:** delete the entry and its CSR, merge, then delete its files in
  `devices/keys/`.
- **Replace a lost or leaked key:** delete `devices/keys/<id>.key` and
  `infra/devices/csr/<id>.csr`, run `make device-keys DEVICE=<id>`, commit the new CSR,
  and merge. The plan shows that device's certificate as replaced, which is expected.
