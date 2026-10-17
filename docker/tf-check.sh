#!/bin/sh
# Static checks for the Terraform config. Needs no AWS credentials.
set -e

if ! ls ./*.tf >/dev/null 2>&1; then
  echo "tf-check: no .tf files at the repo root yet, skipping"
  exit 0
fi

echo "== terraform fmt";      terraform fmt -check -recursive
echo "== terraform init";     terraform init -backend=false -input=false >/dev/null
echo "== terraform validate"; terraform validate
echo "== tflint";             tflint --recursive
echo "== checkov";            checkov -d . --framework terraform --quiet --compact
