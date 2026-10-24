"""Shared fixtures for the developer scripts' tests (docs/features/iot-core.md).

The scripts have dashes in their file names, so they are loaded by path with importlib.
Every test writes only under pytest's tmp_path, never to devices/ in the repo.
"""

from __future__ import annotations

import importlib.util
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


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that tries to open a network connection."""

    def refuse(*args, **kwargs):
        raise RuntimeError("tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
