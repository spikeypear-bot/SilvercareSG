"""Shared fixtures for the developer scripts' tests (docs/features/iot-core.md).

The scripts have dashes in their file names, so they are loaded by path with importlib.
Every test writes only under pytest's tmp_path, never to devices/ in the repo.
"""

from __future__ import annotations

import builtins
import importlib.util
import io
import os
import socket
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent


def load_script(file_name: str, module_name: str) -> ModuleType:
    """Import scripts/<file_name> as module_name."""
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / file_name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def device_keys() -> ModuleType:
    return load_script("device-keys.py", "device_keys")


@pytest.fixture(scope="session")
def device_certs() -> ModuleType:
    return load_script("device-certs.py", "device_certs")


@pytest.fixture(scope="session")
def smoke() -> ModuleType:
    return load_script("iot-smoke-test.py", "iot_smoke_test")


@pytest.fixture
def seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """scripts/seed.py, with its default passwords file moved into tmp_path."""
    module = load_script("seed.py", "seed")
    monkeypatch.setattr(
        module,
        "DEFAULT_PASSWORDS_FILE",
        tmp_path / "default-secrets" / "test-users.txt",
    )
    return module


# The real secrets/ folder (lexical path and, if it is a symlink, its target). Tests
# must never read, write, list, or stat anything in it.
_SECRETS_PATHS = tuple(
    {
        os.path.abspath(SCRIPTS_DIR.parent / "secrets"),
        os.path.realpath(SCRIPTS_DIR.parent / "secrets"),
    }
)


def _in_secrets(value: object) -> bool:
    if isinstance(value, int):
        return False  # a file descriptor
    try:
        path = os.path.abspath(os.fsdecode(value))  # type: ignore[arg-type]
    except TypeError:
        return False
    return any(path == s or path.startswith(s + os.sep) for s in _SECRETS_PATHS)


def _guarded(original, label: str, path_args: int = 1):
    def wrapper(*args, **kwargs):
        if any(_in_secrets(arg) for arg in args[:path_args]):
            raise RuntimeError(f"tests must not touch secrets/ ({label})")
        return original(*args, **kwargs)

    return wrapper


@pytest.fixture(autouse=True)
def no_secrets_access(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that tries to open, write, stat, or delete anything in secrets/."""
    for module, name, path_args in (
        (builtins, "open", 1),
        (io, "open", 1),
        (os, "open", 1),
        (os, "stat", 1),
        (os, "listdir", 1),
        (os, "scandir", 1),
        (os, "mkdir", 1),
        (os, "makedirs", 1),
        (os, "chmod", 1),
        (os, "unlink", 1),
        (os, "remove", 1),
        (os, "rmdir", 1),
        (os, "replace", 2),
        (os, "rename", 2),
    ):
        monkeypatch.setattr(
            module, name, _guarded(getattr(module, name), name, path_args)
        )


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that tries to open a network connection."""

    def refuse(*args, **kwargs):
        raise RuntimeError("tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


# ---- moto fixtures for scripts/seed.py (docs/features/seed-data.md) ----

GROUP_NAMES = ("doctor", "nurse", "caregiver", "family")
FAKE_ENV = {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_DEFAULT_REGION": "ap-southeast-1",
    "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
    "AWS_CONFIG_FILE": "/dev/null",
}


@pytest.fixture
def aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in FAKE_ENV.items():
        monkeypatch.setenv(name, value)
    for name in (
        "AWS_PROFILE",
        "AWS_SESSION_TOKEN",
        "AWS_REGION",
        "AWS_ENDPOINT_URL",
        "DYNAMODB_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def moto(aws_env):
    from moto import mock_aws

    with mock_aws():
        yield


def boto_client(service: str, region: str = "ap-southeast-1"):
    import boto3

    return boto3.client(service, region_name=region)


@pytest.fixture
def ddb(moto, seed):
    """DynamoDB client with silvercare-registry created as in infra/dynamodb.tf."""
    client = boto_client("dynamodb")
    client.create_table(
        TableName="silvercare-registry", **seed.REGISTRY_TABLE_DEFINITION
    )
    return client


@pytest.fixture
def cognito(moto):
    """Pool like infra/cognito.tf: returns (client, pool_id, app_client_id)."""
    client = boto_client("cognito-idp")
    pool_id = client.create_user_pool(
        PoolName="silvercare-users",
        UsernameAttributes=["email"],
        UsernameConfiguration={"CaseSensitive": False},
        Policies={
            "PasswordPolicy": {
                "MinimumLength": 12,
                "RequireLowercase": True,
                "RequireUppercase": True,
                "RequireNumbers": True,
                "RequireSymbols": True,
            }
        },
        AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
    )["UserPool"]["Id"]
    for group in GROUP_NAMES:
        client.create_group(UserPoolId=pool_id, GroupName=group)
    app_client_id = client.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName="test-app",
        ExplicitAuthFlows=[
            "ALLOW_ADMIN_USER_PASSWORD_AUTH",
            "ALLOW_REFRESH_TOKEN_AUTH",
        ],
    )["UserPoolClient"]["ClientId"]
    return client, pool_id, app_client_id


class Recorder:
    """Wraps a boto3 client: records every call and can fail one on purpose.

    fail maps an operation (snake_case) to (call number, error code, message): that
    call raises ClientError instead of reaching moto.
    """

    def __init__(self, client, fail: dict | None = None):
        self._client = client
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail or {}

    def __getattr__(self, name):
        target = getattr(self._client, name)
        if (
            name.startswith("_")
            or name in {"meta", "exceptions"}
            or not callable(target)
        ):
            return target

        def call(*args, **kwargs):
            self.calls.append((name, kwargs))
            if name in self.fail:
                number, code, message = self.fail[name]
                if sum(1 for n, _ in self.calls if n == name) == number:
                    from botocore.exceptions import ClientError

                    raise ClientError(
                        {"Error": {"Code": code, "Message": message}},
                        "".join(part.title() for part in name.split("_")),
                    )
            return target(*args, **kwargs)

        return call

    def ops(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def live(ddb, cognito):
    """Recording clients for a live-mode run against moto: (clients, pool_id, app_id)."""
    cog_client, pool_id, app_client_id = cognito
    clients = {
        "dynamodb": Recorder(ddb),
        "cognito-idp": Recorder(cog_client),
        "sts": Recorder(boto_client("sts")),
    }
    return clients, pool_id, app_client_id
