---
name: tf-module
description: Create or restructure a reusable Terraform module in this repo. Use when grouping resources into a module (e.g. a Lambda + its role + log group, an IoT rule, a data store) or when a pattern is repeated more than once.
argument-hint: [module-name]
---

# Terraform modules

Module to work on: $ARGUMENTS

CI runs Terraform from the repo root only. Modules live under `modules/` and do
nothing until the root config calls them.

## Layout

```
modules/<module-name>/
├── main.tf        # resources
├── variables.tf   # inputs
├── outputs.tf     # outputs
├── versions.tf    # required_providers (no provider block)
└── README.md      # purpose, inputs, outputs, example call
```

Module names are kebab-case nouns describing what they create: `python-lambda`,
`iot-ingest-rule`, `alert-topic`.

## Rules

- **No `provider` blocks in modules.** Declare `required_providers` with a version
  constraint in `versions.tf`; the root config owns provider configuration.
- **No hardcoded region, account ID, or environment.** Use
  `data "aws_region"` / `data "aws_caller_identity"` or take them as variables.
- **Every variable** has `description` and `type`. Add `validation` blocks for
  values with a fixed format (names, ARNs, durations). Give a `default` only when a
  sensible one exists; required inputs have none.
- **Every output** has a `description`. Output what callers need to wire things
  together (ARNs, names, IDs), not everything.
- **Tags:** accept a `tags` variable (`map(string)`, default `{}`) and apply it to
  every taggable resource, merged with any module-specific tags.
- **Secure by default:** encryption on, public access off, least-privilege IAM
  (follow the `iam-policy` skill). Callers should not need to opt in to security.
- **Serverless only.** Do not put fixed-cost resources in a module.
- Keep modules single-purpose. If a module needs a flag to behave like two
  different things, split it.

## Steps

1. Check `modules/` for an existing module that does this, or nearly does. Extend it
   rather than duplicating it.
2. Create the files above. Start from the inputs: what must the caller decide?
3. Write `README.md` with one example `module "..." {}` call.
4. Add or update the call in the root config.
5. Run `terraform fmt -recursive` and `terraform validate` from the repo root.
   (`validate` needs `terraform init`; use `terraform init -backend=false` if no
   AWS credentials are available.)
6. Summarise what the module creates, its inputs/outputs, and any resources that
   will be replaced if this refactors existing root resources into a module. Moving
   resources into a module changes their addresses; add `moved {}` blocks so they
   are not destroyed and recreated.

## Never

- Run `terraform apply` or `terraform destroy`.
- Commit or push.
