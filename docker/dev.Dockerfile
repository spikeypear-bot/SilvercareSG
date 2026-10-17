# Dev/test toolbox for SilverCare: Python (matching the Lambda runtime), test
# packages, and Terraform tooling. Used via docker-compose.yml and the Makefile.
# Contains no AWS credentials.
FROM python:3.13.16-slim-trixie

ARG TERRAFORM_VERSION=1.16.6
ARG TFLINT_VERSION=0.64.0
ARG CHECKOV_VERSION=3.3.29

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl unzip git \
 && rm -rf /var/lib/apt/lists/*

# Terraform, checksum-verified
RUN set -eux; arch="$(dpkg --print-architecture)"; cd /tmp; \
    base="https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}"; \
    zip="terraform_${TERRAFORM_VERSION}_linux_${arch}.zip"; \
    curl -fsSLO "${base}/${zip}"; \
    curl -fsSL "${base}/terraform_${TERRAFORM_VERSION}_SHA256SUMS" | grep " ${zip}\$" | sha256sum -c -; \
    unzip -q "${zip}" terraform -d /usr/local/bin; rm "${zip}"

# tflint, checksum-verified
RUN set -eux; arch="$(dpkg --print-architecture)"; cd /tmp; \
    base="https://github.com/terraform-linters/tflint/releases/download/v${TFLINT_VERSION}"; \
    zip="tflint_linux_${arch}.zip"; \
    curl -fsSLO "${base}/${zip}"; \
    curl -fsSL "${base}/checksums.txt" | grep " ${zip}\$" | sha256sum -c -; \
    unzip -q "${zip}" tflint -d /usr/local/bin; rm "${zip}"

# checkov in its own venv so its dependencies don't clash with the test packages
RUN python -m venv /opt/checkov \
 && /opt/checkov/bin/pip install --no-cache-dir "checkov==${CHECKOV_VERSION}" \
 && ln -s /opt/checkov/bin/checkov /usr/local/bin/checkov

COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt

# The container runs as the host user (see docker-compose.yml), which has no
# home directory in the image, so give tools a writable HOME and caches.
RUN mkdir -p /cache/home /cache/terraform && chmod 1777 /cache/home /cache/terraform
ENV HOME=/cache/home \
    TF_PLUGIN_CACHE_DIR=/cache/terraform \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /work
