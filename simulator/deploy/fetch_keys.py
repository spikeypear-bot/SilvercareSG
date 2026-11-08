"""Write the device keys from Parameter Store into a tmpfs folder before the EC2
simulator starts (docs/features/simulator-ec2.md, criterion 10).

Run by the silvercare-simulator service (ExecStartPre) on the instance:

    python3.13 simulator/deploy/fetch_keys.py --out /run/silvercare-keys

Reads /silvercare/device-keys/<id> for every device in infra/devices/devices.json and
writes <id>.crt / <id>.key (mode 0600). Old files in the folder are deleted first.
Prints device IDs only, never key contents. Creates no AWS client when imported.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO

REGION = "ap-southeast-1"
PREFIX = "/silvercare/device-keys/"
DEFAULT_DEVICES_FILE = (
    Path(__file__).resolve().parents[2] / "infra" / "devices" / "devices.json"
)


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def fetch(client: Any, device_ids: list[str], out_dir: Path, out: TextIO) -> int:
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    for old in out_dir.iterdir():
        if old.is_file():
            old.unlink()
    bad: list[str] = []
    for device_id in device_ids:
        try:
            value = client.get_parameter(Name=PREFIX + device_id, WithDecryption=True)[
                "Parameter"
            ]["Value"]
            pair = json.loads(value)
            crt, key = pair["crt"], pair["key"]
            if not (isinstance(crt, str) and isinstance(key, str)):
                raise TypeError
        except Exception:  # noqa: BLE001 - never echo the value or the AWS message
            bad.append(device_id)
            continue
        _write_private(out_dir / f"{device_id}.crt", crt)
        _write_private(out_dir / f"{device_id}.key", key)
    if bad:
        print(f"error: missing or invalid key parameters: {', '.join(bad)}", file=out)
        return 1
    print(f"fetched keys for {len(device_ids)} devices", file=out)
    return 0


def main(
    argv: list[str] | None = None,
    client: Any | None = None,
    out: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description="Fetch device keys for the simulator.")
    parser.add_argument("--out", type=Path, required=True, help="folder to write keys")
    parser.add_argument("--devices-file", type=Path, default=DEFAULT_DEVICES_FILE)
    args = parser.parse_args(argv)
    try:
        device_ids = [
            d["id"] for d in json.loads(args.devices_file.read_text())["devices"]
        ]
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: device list: {type(exc).__name__}", file=out)
        return 2
    if client is None:
        import boto3  # only when run for real, never at import

        client = boto3.client("ssm", region_name=REGION)
    return fetch(client, device_ids, args.out, out)


if __name__ == "__main__":
    sys.exit(main())
