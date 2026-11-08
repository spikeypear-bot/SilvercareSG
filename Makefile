# Dev/test commands. Everything runs in the dev container (docker-compose.yml),
# so Docker is the only thing you need installed. Run `make help` for the list.

export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

COMPOSE := docker compose
DEVOPTS :=
# Reuse the dev container if `make up` is running it; otherwise start a throwaway one.
DEV = $(if $(shell $(COMPOSE) ps --status running -q dev 2>/dev/null),$(COMPOSE) exec -T $(DEVOPTS) dev,$(COMPOSE) run --rm -T $(DEVOPTS) dev)

FN ?=
DEVICE ?=
ENDPOINT ?=
JOB ?=
INGEST ?=
ARGS ?=
ALERTS ?=
CANCEL ?=
LOW_BATTERY ?=
POSSIBLE_FALL ?=
BACK_IN_BED ?=
# Local secrets (device keys and certs, the frontend .env), git-ignored. Share this one
# folder privately with other developers (README.md, Sharing local secrets).
SECRETS_DIR := secrets
KEYS_DIR := $(SECRETS_DIR)/device-keys

.PHONY: help build up down shell test test-integration lint fmt tf-check check seed-local simulate-dry-run backfill device-keys device-certs smoke-test simulate upload-device-keys

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

build: ## Build the dev image (after changing docker/ or requirements-dev.txt)
	$(COMPOSE) build dev

up: ## Start the dev container and DynamoDB Local in the background
	$(COMPOSE) up -d --quiet-pull dev dynamodb-local

down: ## Stop all containers
	$(COMPOSE) down

shell: ## Open a shell in the dev container
	$(COMPOSE) run --rm dev bash

test: ## Unit tests for every Lambda, scripts/, simulator/, or one: make test FN=<name|scripts|simulator>
	$(DEV) sh docker/run-lambda-tests.sh unit $(FN)

test-integration: DEVOPTS = -e DYNAMODB_ENDPOINT=http://dynamodb-local:8000
test-integration: ## Integration tests against DynamoDB Local: make test-integration FN=<name>
	$(COMPOSE) up -d --quiet-pull dynamodb-local
	$(DEV) sh docker/run-lambda-tests.sh integration $(FN)

lint: ## Ruff lint + format check on all Python
	$(DEV) sh -c 'ruff check . && ruff format --check .'

fmt: ## Format Python and Terraform
	$(DEV) sh -c 'ruff format . && terraform fmt -recursive'

tf-check: ## Terraform fmt check, validate, tflint, checkov (no AWS credentials needed)
	$(DEV) sh docker/tf-check.sh

check: test lint tf-check ## Everything above except integration tests

# Seed data (docs/features/seed-data.md). The live-account seed runs on the host with your
# own AWS credentials: python scripts/seed.py
seed-local: DEVOPTS = -e DYNAMODB_ENDPOINT=http://dynamodb-local:8000
seed-local: ## Seed the registry in DynamoDB Local with synthetic data: make seed-local [ARGS=--delete]
	$(COMPOSE) up -d --quiet-pull dynamodb-local
	$(DEV) python scripts/seed.py --local $(ARGS)

# Simulator (docs/features/simulator-core.md). Both use their own `compose run` (not
# $(DEV), which may `exec` into a running container) so Ctrl+C reaches the simulator.
# `@` keeps make's own command line out of stdout, so the dry-run output stays JSON lines.
simulate-dry-run: ## Print simulated device messages, no keys or network: make simulate-dry-run [ARGS="--instant --duration 1h"]
	@$(COMPOSE) run --rm --quiet-pull dev python simulator/src/main.py --dry-run $(ARGS)

# docs/features/simulator-backfill.md: past days as local raw-history files, no network.
backfill: ## Write past days of simulated history to local files: make backfill ARGS="--from 2026-03-01 --to 2026-10-31 --out backfill-data"
	$(COMPOSE) run --rm --quiet-pull dev python simulator/src/backfill.py $(ARGS)

# docs/features/simulator-ec2.md: the EC2 simulator's keys in Parameter Store. Runs on the
# host (the dev container has no AWS credentials), with your own AWS credentials.
upload-device-keys: ## Upload device keys to Parameter Store (host): make upload-device-keys [ARGS="--devices <id>" | ARGS=--delete]
	python scripts/upload-device-keys.py --keys-dir $(KEYS_DIR) $(ARGS)

# IoT device targets (docs/features/iot-core.md). The developer runs these, not Claude:
# they read or write the private keys in $(KEYS_DIR)/.
device-keys: ## Create missing device keys + CSRs: make device-keys [DEVICE=<id>]
	$(DEV) python scripts/device-keys.py --keys-dir $(KEYS_DIR) $(if $(DEVICE),--device $(DEVICE))

device-certs: ## Write secrets/device-keys/<id>.crt from secrets/device-keys/certificates.json
	$(DEV) python scripts/device-certs.py --keys-dir $(KEYS_DIR)

smoke-test: ## Test a device against AWS IoT: make smoke-test DEVICE=<id> ENDPOINT=<host> [JOB=1] [INGEST=1] [ALERTS=1] [CANCEL=1] [LOW_BATTERY=1] [POSSIBLE_FALL=1] [BACK_IN_BED=1]
	$(DEV) python scripts/iot-smoke-test.py --device $(DEVICE) --endpoint $(ENDPOINT) --keys-dir $(KEYS_DIR) $(if $(JOB),--job) $(if $(INGEST),--ingest) $(if $(ALERTS),--alerts) $(if $(CANCEL),--cancel) $(if $(LOW_BATTERY),--low-battery) $(if $(POSSIBLE_FALL),--possible-fall) $(if $(BACK_IN_BED),--back-in-bed)

simulate: ## Run the 40 simulated devices against AWS IoT until Ctrl+C: make simulate ENDPOINT=<host> [ARGS="--duration 30m"]
	$(COMPOSE) run --rm dev python simulator/src/main.py --endpoint $(ENDPOINT) --keys-dir $(KEYS_DIR) $(ARGS)
