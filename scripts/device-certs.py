"""Write each device's certificate to secrets/device-keys/<id>.crt (docs/features/iot-core.md).

Run with `make device-certs` in the dev container, after saving the Terraform output:

    terraform -chdir=infra output -json device_certificates > secrets/device-keys/certificates.json

Accepts the bare map (device ID -> certificate PEM) that `output -json device_certificates`
prints, or the same map wrapped as {"value": {...}} or {"device_certificates": {"value": ...}}.
Prints only device IDs. Certificates are public, but they live next to the keys.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from cryptography import x509

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KEYS_DIR = REPO_ROOT / "secrets" / "device-keys"

DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-[0-9]{4}$")
CERT_HEADER = "-----BEGIN CERTIFICATE-----"


class CertificatesError(ValueError):
    """certificates.json is malformed. The message never holds file contents."""


def parse_certificates(data: object) -> dict[str, str]:
    """Return {device_id: certificate_pem} from the parsed certificates.json."""
    if isinstance(data, dict) and isinstance(data.get("device_certificates"), dict):
        data = data["device_certificates"]
    if isinstance(data, dict) and isinstance(data.get("value"), dict):
        data = data["value"]
    if not isinstance(data, dict) or not data:
        raise CertificatesError(
            "certificates.json must be a non-empty map of device ID to certificate PEM"
        )

    certs: dict[str, str] = {}
    for device_id, pem in data.items():
        # The ID becomes a file name, so it must be a well-formed device ID.
        if not isinstance(device_id, str) or not DEVICE_ID_RE.fullmatch(device_id):
            raise CertificatesError("certificates.json has an invalid device ID")
        if (
            not isinstance(pem, str)
            or not pem.lstrip().startswith(CERT_HEADER)
            or "PRIVATE KEY" in pem
        ):
            raise CertificatesError(f"{device_id}: value is not a PEM certificate")
        # Catch a truncated or corrupted value here, not later as a TLS error.
        try:
            x509.load_pem_x509_certificate(pem.encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            raise CertificatesError(
                f"{device_id}: value is not a valid PEM certificate"
            ) from None
        certs[device_id] = pem.strip() + "\n"
    return certs


def write_certificate(keys_dir: Path, device_id: str, pem: str) -> Path:
    """Write <keys_dir>/<id>.crt atomically, replacing an older certificate."""
    path = Path(keys_dir) / f"{device_id}.crt"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(pem, encoding="ascii")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write device certificates from certificates.json."
    )
    parser.add_argument("--keys-dir", type=Path, default=DEFAULT_KEYS_DIR)
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="certificates JSON (default: <keys-dir>/certificates.json)",
    )
    args = parser.parse_args(argv)
    source = args.input or args.keys_dir / "certificates.json"

    try:
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {source} ({type(exc).__name__})", file=sys.stderr)
        return 1
    try:
        certs = parse_certificates(data)
    except CertificatesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    args.keys_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    for device_id in sorted(certs):
        write_certificate(args.keys_dir, device_id, certs[device_id])
        print(device_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
