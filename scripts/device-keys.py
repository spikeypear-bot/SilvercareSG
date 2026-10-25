"""Create a private key and a CSR for every IoT device (docs/features/iot-core.md).

Run with `make device-keys [DEVICE=<id>]` in the dev container. Needs no AWS access.

For each device in infra/devices/devices.json:
  - no key at secrets/device-keys/<id>.key: create an EC P-256 key (mode 0600) and a CSR at
    infra/devices/csr/<id>.csr with subject CN=<id>
  - key but no CSR: recreate the CSR from the existing key
  - both exist: skip

An existing key is never overwritten or deleted. Only device IDs, file paths, and what
was done are printed, never key material. Commit the CSRs; never commit the keys.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEVICES_FILE = REPO_ROOT / "infra" / "devices" / "devices.json"
DEFAULT_KEYS_DIR = REPO_ROOT / "secrets" / "device-keys"
DEFAULT_CSR_DIR = REPO_ROOT / "infra" / "devices" / "csr"

DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-[0-9]{4}$")
DEVICE_TYPES = ("wearable", "bedroom")


class DeviceListError(ValueError):
    """devices.json is malformed or has an invalid entry. The message names the entry."""


class KeyFileError(ValueError):
    """An existing key file can't be used. The message holds no key material."""


def validate_devices(data: object) -> list[dict]:
    """Check the parsed devices.json with the same rules as Terraform.

    Returns the device entries. Raises DeviceListError naming the first bad entry.
    """
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        raise DeviceListError('devices.json must be an object with a "devices" list')

    devices = data["devices"]
    seen: set[str] = set()
    for index, entry in enumerate(devices):
        if not isinstance(entry, dict):
            raise DeviceListError(f"device entry #{index} is not an object")
        device_id = entry.get("id")
        device_type = entry.get("type")
        label = f'"{device_id}"' if isinstance(device_id, str) else f"#{index}"
        if not isinstance(device_id, str) or not DEVICE_ID_RE.fullmatch(device_id):
            raise DeviceListError(
                f"device entry {label}: id must match ^(wearable|bedroom)-[0-9]{{4}}$"
            )
        if device_type not in DEVICE_TYPES or not device_id.startswith(
            f"{device_type}-"
        ):
            raise DeviceListError(
                f"device entry {label}: type must be wearable or bedroom and match the id prefix"
            )
        if not isinstance(entry.get("canary"), bool):
            raise DeviceListError(f"device entry {label}: canary must be true or false")
        if device_id in seen:
            raise DeviceListError(f"device entry {label}: duplicate id")
        seen.add(device_id)
    return devices


def load_devices(devices_file: Path) -> list[dict]:
    try:
        data = json.loads(Path(devices_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeviceListError(
            f"cannot read {devices_file}: {type(exc).__name__}"
        ) from exc
    return validate_devices(data)


def build_csr(device_id: str, key: ec.EllipticCurvePrivateKey) -> bytes:
    """Return a PEM CSR with subject CN=<device_id>, signed by the key."""
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM)


def write_new_key(path: Path) -> ec.EllipticCurvePrivateKey:
    """Create an EC P-256 key at path with mode 0600. Fails if the file exists."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # O_EXCL: never overwrite an existing key, even if one appears meanwhile.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, pem)
    finally:
        os.close(fd)
    return key


def read_key(path: Path) -> ec.EllipticCurvePrivateKey:
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (ValueError, TypeError):
        raise KeyFileError(f"{path} is not an unencrypted PEM private key") from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise KeyFileError(f"{path} is not an EC P-256 private key")
    return key


def write_csr(path: Path, csr_pem: bytes) -> None:
    """Write the CSR atomically (temp file, then rename)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(csr_pem)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def process_device(device_id: str, keys_dir: Path, csr_dir: Path) -> str:
    """Create whatever is missing for one device. Returns one line describing it."""
    key_path = Path(keys_dir) / f"{device_id}.key"
    csr_path = Path(csr_dir) / f"{device_id}.csr"

    if not key_path.exists():
        key = write_new_key(key_path)
        write_csr(csr_path, build_csr(device_id, key))
        return f"{device_id}: created key {key_path} and CSR {csr_path}"
    if not csr_path.exists():
        write_csr(csr_path, build_csr(device_id, read_key(key_path)))
        return f"{device_id}: key exists, created CSR {csr_path}"
    return f"{device_id}: skipped (key and CSR exist)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create missing device keys and CSRs.")
    parser.add_argument("--device", help="handle only this device ID")
    parser.add_argument("--devices-file", type=Path, default=DEFAULT_DEVICES_FILE)
    parser.add_argument("--keys-dir", type=Path, default=DEFAULT_KEYS_DIR)
    parser.add_argument("--csr-dir", type=Path, default=DEFAULT_CSR_DIR)
    args = parser.parse_args(argv)

    try:
        devices = load_devices(args.devices_file)
    except DeviceListError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    ids = [d["id"] for d in devices]
    if args.device is not None:
        if args.device not in ids:
            # Echo the ID only if it is well formed (never echo arbitrary input).
            shown = args.device if DEVICE_ID_RE.fullmatch(args.device) else "<invalid>"
            print(
                f"error: unknown device {shown} (not in devices.json)", file=sys.stderr
            )
            return 1
        ids = [args.device]

    args.keys_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.csr_dir.mkdir(parents=True, exist_ok=True)

    status = 0
    for device_id in ids:
        try:
            print(process_device(device_id, args.keys_dir, args.csr_dir))
        except KeyFileError as exc:
            print(f"{device_id}: failed: {exc}", file=sys.stderr)
            status = 1
        except OSError as exc:
            # The exception type only, never file contents.
            print(f"{device_id}: failed ({type(exc).__name__})", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
