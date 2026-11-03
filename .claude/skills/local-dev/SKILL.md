---
name: local-dev
description: How to run and test SilverCare locally - the Docker dev container, make targets, unit tests with moto, DynamoDB Local integration tests, the Vite dev server, and what can't be emulated. Use when setting up local development, Docker, running tests, or adding integration tests.
---

# Local development

Python and Terraform tooling runs in the dev container (`docker/dev.Dockerfile`,
`docker-compose.yml`), driven by the `Makefile`. Don't install or run these tools on the
host. The container has no AWS credentials: AWS calls go to moto or DynamoDB Local.

## Make targets

| Target | What it does |
|---|---|
| `make up` / `make down` | Start/stop the dev container and DynamoDB Local in the background. While `up`, commands use `exec` (faster), and the format hook can format Python and Terraform. |
| `make test` / `make test FN=<name>` | Unit tests for every Lambda, each in its own pytest run (they all have a module named `handler`) |
| `make test-integration [FN=<name>]` | Only tests marked `@pytest.mark.integration`, against DynamoDB Local |
| `make lint` / `make fmt` | Ruff check + format check / ruff format + terraform fmt. The format hook only sees Edit/Write: after changing files with a shell script or `sed`, run `make fmt` (or `docker compose run --rm -T dev ruff format <file>`) before reporting done. Keep LF line endings (`.gitattributes`): on the Windows host, Python writes CRLF in text mode, so open files with `newline=''` or in binary, or use Edit/Write. |
| `make tf-check` | terraform fmt check, validate (`init -backend=false`), tflint, checkov |
| `make check` | test + lint + tf-check |
| `make shell` | Shell in the container, for anything else |
| `make build` | Rebuild after changing `docker/` or `requirements-dev.txt` |

For one-off commands: `docker compose run --rm -T dev <command>` (or
`docker compose exec -T dev <command>` while `make up` is running).

On a Windows host without `make`, run what the target runs (see the `Makefile`), e.g.
`docker compose up -d dev dynamodb-local`, then
`docker compose exec -T dev sh docker/run-lambda-tests.sh unit [<name>]`. Shell scripts
must stay LF (`.gitattributes`), or `sh` fails in the container with `: not found`.
Fix a CRLF script by stripping `\r` from that file only (`sed -i 's/\r$//' <file>`).
Never suggest `git checkout-index --force --all` or `git reset --hard` to refresh line
endings while anything is uncommitted: they silently overwrite uncommitted edits. If
port 8000 is taken by another container, the dev container still runs and unit tests
work; only DynamoDB Local (integration tests) needs the port.

## Levels of testing

1. **Unit tests (default):** pytest + moto. See `lambda-test`.
2. **Integration tests:** handlers against DynamoDB Local, to catch key-schema,
   condition-expression, and query mistakes that mocks can hide.
3. **Frontend:** `npm run dev` in `frontend/` on the host's Node (Vite on
   `http://localhost:5173`), against the deployed API and Cognito. The API's CORS allows
   localhost for this.

## Integration tests

- Mark them `@pytest.mark.integration` (registered in `pytest.ini`). `make test` skips
  them, so unit runs never need DynamoDB Local.
- `make test-integration` sets `DYNAMODB_ENDPOINT=http://dynamodb-local:8000` inside the
  container. Handlers pass `os.environ.get("DYNAMODB_ENDPOINT")` as `endpoint_url` to
  boto3. It's `None` in AWS, so the real endpoint is used there.
- Fixtures create tables with the same key schema and GSIs as Terraform, and delete them
  afterwards. DynamoDB Local runs in memory, so restarting it clears everything.
- From the host, DynamoDB Local is at `http://localhost:8000`.

## Changing the toolbox

- Python packages: edit `requirements-dev.txt` (keep versions pinned), then `make build`.
- Tool versions (Terraform, tflint, checkov, Python): edit the `ARG`s or `FROM` line in
  `docker/dev.Dockerfile`, then `make build`. Keep Python on the Lambda runtime version.

## What can't run locally

- **IoT Core / MQTT:** no free local emulator. Test handlers with fake rule events (see
  `lambda-test`). End-to-end MQTT tests need a test thing in the real account, and the
  user does those.
- **Cognito and the API Gateway JWT authorizer:** test handlers with fake claims. Test
  real sign-in against the deployed user pool.
- **Anything needing real AWS credentials** (`terraform plan` against the real state,
  `apply`, `terraform output`): the user runs these outside Claude Code, on the host,
  where nothing is installed by default. Any README step that has the user run a tool
  on the host (e.g. Terraform) also says how to install it, at the version pinned in
  `docker/dev.Dockerfile`.

## Rules

- Never mount `~/.aws` or pass real credentials into the container.
- Only synthetic data, locally too.
