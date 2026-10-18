#!/bin/sh
# Static checks for the Terraform config. Needs no AWS credentials.
set -e

# The root config is infra/; tflint and checkov also cover infra/modules and infra/bootstrap.
if ! ls infra/*.tf >/dev/null 2>&1; then
  echo "tf-check: no .tf files in infra/ yet, skipping"
  exit 0
fi
cd infra

# Keep the checks' init separate from a real `terraform init` in infra/.terraform, which
# points at the S3 backend and needs AWS credentials the container doesn't have.
export TF_DATA_DIR=/tmp/tf-check-data

echo "== terraform fmt";      terraform fmt -check -recursive
echo "== terraform init";     terraform init -backend=false -input=false >/dev/null
echo "== terraform validate"; terraform validate
echo "== tflint";             tflint --recursive
echo "== checkov";            checkov -d . --framework terraform --quiet --compact
