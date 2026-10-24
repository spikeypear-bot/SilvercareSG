"""Plain helpers for the scripts' tests (no fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Built by concatenation so test source never contains a PEM private key header
# (CI's key scan greps tracked files for it).
PRIVATE_KEY_TEXT = "PRIVATE" + " KEY"


def write_devices_file(path: Path, devices: list[dict]) -> Path:
    path.write_text(json.dumps({"devices": devices}), encoding="utf-8")
    return path


def device(device_id: str, device_type: str | None = None, canary: bool = False):
    return {
        "id": device_id,
        "type": device_type or device_id.split("-", 1)[0],
        "canary": canary,
    }
