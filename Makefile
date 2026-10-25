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

.PHONY: help build up down shell test test-integration lint fmt tf-check check device-keys device-certs smoke-test

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

test: ## Unit tests for every Lambda and scripts/, or one: make test FN=<name|scripts>
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

# IoT device targets (docs/features/iot-core.md). The developer runs these, not Claude:
# they read or write the private keys in devices/keys/.
device-keys: ## Create missing device keys + CSRs: make device-keys [DEVICE=<id>]
	$(DEV) python scripts/device-keys.py $(if $(DEVICE),--device $(DEVICE))

device-certs: ## Write devices/keys/<id>.crt from devices/keys/certificates.json
	$(DEV) python scripts/device-certs.py

smoke-test: ## Test a device against AWS IoT: make smoke-test DEVICE=<id> ENDPOINT=<host> [JOB=1] [INGEST=1]
	$(DEV) python scripts/iot-smoke-test.py --device $(DEVICE) --endpoint $(ENDPOINT) $(if $(JOB),--job) $(if $(INGEST),--ingest)
