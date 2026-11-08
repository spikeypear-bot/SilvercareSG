"""Upload device keys and certs to SSM Parameter Store for the EC2 simulator
(docs/features/simulator-ec2.md, criteria 1-5).

Run on the host with your own AWS credentials (region ap-southeast-1), never by Claude:

    pip install -r scripts/requirements.txt
    python scripts/upload-device-keys.py --keys-dir secrets/device-keys
    python scripts/upload-device-keys.py --keys-dir secrets/device-keys --devices wearable-0001
    python scripts/upload-device-keys.py --delete          # at removal, 2026-11-30

Each device in infra/devices/devices.json gets /silvercare/device-keys/<id>, a standard
SecureString (AWS managed key aws/ssm) holding {"crt": "<PEM>", "key": "<PEM>"}.
Every file is checked before anything is uploaded. Prints device IDs and AWS error
codes only, never file contents. Creates no AWS client when imported.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

REGION = "ap-southeast-1"
PREFIX = "/silvercare/device-keys/"
MAX_VALUE_BYTES = 4096  # standard-tier parameter limit
DEFAULT_DEVICES_FILE = (
    Path(__file__).resolve().parent.parent / "infra" / "devices" / "devices.json"
)


def load_device_ids(path: Path) -> list[str]:
    return [d["id"] for d in json.loads(path.read_text())["devices"]]


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", type(exc).__name__))
    return type(exc).__name__


def build_values(
    keys_dir: Path, device_ids: list[str]
) -> tuple[dict[str, str], list[str]]:
    """Parameter values per device, and the device IDs whose files are unusable."""
    values: dict[str, str] = {}
    bad: list[str] = []
    for device_id in device_ids:
        try:
            crt = (keys_dir / f"{device_id}.crt").read_text()
            key = (keys_dir / f"{device_id}.key").read_text()
        except (OSError, UnicodeDecodeError):
            bad.append(device_id)
            continue
        value = json.dumps({"crt": crt, "key": key})
        if (
            not (crt.startswith("-----BEGIN") and key.startswith("-----BEGIN"))
            or len(value.encode()) > MAX_VALUE_BYTES
        ):
            bad.append(device_id)
            continue
        values[device_id] = value
    return values, bad


def upload(client: Any, values: dict[str, str], out: TextIO) -> int:
    failed = 0
    for device_id, value in values.items():
        try:
            client.put_parameter(
                Name=PREFIX + device_id,
                Value=value,
                Type="SecureString",
                Tier="Standard",
                Overwrite=True,
            )
            print(f"{device_id} uploaded", file=out)
        except Exception as exc:  # noqa: BLE001 - report the AWS error code only
            failed += 1
            print(f"{device_id} failed {_error_code(exc)}", file=out)
    return failed


def delete(client: Any, device_ids: list[str], out: TextIO) -> int:
    failed = 0
    for device_id in device_ids:
        try:
            client.delete_parameter(Name=PREFIX + device_id)
            print(f"{device_id} deleted", file=out)
        except Exception as exc:  # noqa: BLE001 - report the AWS error code only
            code = _error_code(exc)
            if code == "ParameterNotFound":
                print(f"{device_id} not found", file=out)
            else:
                failed += 1
                print(f"{device_id} failed {code}", file=out)
    return failed


def main(
    argv: list[str] | None = None,
    client: Any | None = None,
    out: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(
        description="Upload device keys to Parameter Store for the EC2 simulator."
    )
    parser.add_argument(
        "--keys-dir", type=Path, help="folder with <id>.crt and <id>.key"
    )
    parser.add_argument("--devices", help="comma-separated device IDs (default: all)")
    parser.add_argument("--delete", action="store_true", help="delete the parameters")
    parser.add_argument(
        "--devices-file",
        type=Path,
        default=DEFAULT_DEVICES_FILE,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    try:
        known = load_device_ids(args.devices_file)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: device list: {type(exc).__name__}", file=out)
        return 2
    device_ids = known
    if args.devices:
        device_ids = [d.strip() for d in args.devices.split(",") if d.strip()]
        unknown = [d for d in device_ids if d not in known]
        if unknown or not device_ids:
            print(f"error: unknown devices: {', '.join(unknown)}", file=out)
            return 2

    if not args.delete:
        if args.keys_dir is None:
            print("error: --keys-dir is required (unless --delete)", file=out)
            return 2
        values, bad = build_values(args.keys_dir, device_ids)
        if bad:
            print(
                f"error: missing or invalid key files, nothing uploaded: {', '.join(bad)}",
                file=out,
            )
            return 2

    if client is None:
        import boto3  # only when run for real, never at import

        client = boto3.client("ssm", region_name=REGION)

    if args.delete:
        failed = delete(client, device_ids, out)
    else:
        failed = upload(client, values, out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
