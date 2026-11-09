"""Shared setup for the api-residents tests: src/ on the path and fake AWS settings set
before anything imports boto3, so no test can reach real AWS."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "ap-southeast-1"
os.environ["AWS_SHARED_CREDENTIALS_FILE"] = "/dev/null"
os.environ["AWS_CONFIG_FILE"] = "/dev/null"
for name, value in {
    "ALERTS_TABLE": "silvercare-alerts",
    "REGISTRY_TABLE": "silvercare-registry",
    "LATEST_TABLE": "silvercare-latest",
    "USER_POOL_ID": "ap-southeast-1_testpool0",
    "POWERTOOLS_SERVICE_NAME": "silvercare-api-residents-test",
}.items():
    os.environ.setdefault(name, value)
