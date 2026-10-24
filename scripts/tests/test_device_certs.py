"""Tests for scripts/device-certs.py (docs/features/iot-core.md, criteria 21 and 25).

Certificates are self-signed and generated in the test; nothing is read from devices/.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from helpers import PRIVATE_KEY_TEXT

DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-[0-9]{4}$")
IDS = ["wearable-0001", "wearable-0002", "bedroom-0001"]


def make_cert_pem(device_id: str) -> str:
    """A throwaway self-signed certificate with CN=<device_id>."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def make_private_key_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


@pytest.fixture(scope="module")
def certs() -> dict[str, str]:
    return {device_id: make_cert_pem(device_id) for device_id in IDS}


@pytest.fixture
def keys_dir(tmp_path: Path) -> Path:
    return tmp_path / "keys"


@pytest.fixture
def run(device_certs, tmp_path, keys_dir, capsys):
    """Write data as JSON to tmp_path and run main(). Returns (code, stdout, stderr)."""

    def _run(data, *, raw: str | None = None) -> tuple[int, str, str]:
        source = tmp_path / "certificates.json"
        source.write_text(raw if raw is not None else json.dumps(data), "utf-8")
        code = device_certs.main(["--keys-dir", str(keys_dir), "--input", str(source)])
        out, err = capsys.readouterr()
        return code, out, err

    return _run


def written_files(keys_dir: Path) -> list[str]:
    return sorted(p.name for p in keys_dir.iterdir()) if keys_dir.exists() else []


# ---- valid input ----


def test_writes_a_crt_for_every_device(run, keys_dir, certs):
    code, _, _ = run(certs)

    assert code == 0
    assert written_files(keys_dir) == sorted(f"{d}.crt" for d in IDS)


def test_written_crt_holds_that_devices_certificate(run, keys_dir, certs):
    run(certs)

    for device_id, pem in certs.items():
        text = (keys_dir / f"{device_id}.crt").read_text(encoding="ascii")
        assert text == pem.strip() + "\n"
        cert = x509.load_pem_x509_certificate(text.encode("ascii"))
        cns = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        assert [a.value for a in cns] == [device_id]


def test_prints_only_device_ids(run, certs):
    _, out, err = run(certs)

    assert out.splitlines() == sorted(IDS)
    assert all(DEVICE_ID_RE.fullmatch(line) for line in out.splitlines())
    assert err == ""


def test_output_contains_no_certificate_text(run, certs):
    _, out, err = run(certs)

    assert "CERTIFICATE" not in out + err
    assert "-----BEGIN" not in out + err


@pytest.mark.parametrize(
    "wrap",
    [
        lambda m: {"value": m},
        lambda m: {"device_certificates": {"value": m}},
        lambda m: {"sensitive": False, "type": ["map", "string"], "value": m},
    ],
    ids=["value", "device_certificates.value", "terraform-output-object"],
)
def test_accepts_wrapped_terraform_output(run, keys_dir, certs, wrap):
    code, out, _ = run(wrap(certs))

    assert code == 0
    assert out.splitlines() == sorted(IDS)
    assert written_files(keys_dir) == sorted(f"{d}.crt" for d in IDS)


def test_reads_certificates_json_in_keys_dir_by_default(
    device_certs, keys_dir, certs, capsys
):
    keys_dir.mkdir()
    (keys_dir / "certificates.json").write_text(json.dumps(certs), "utf-8")

    code = device_certs.main(["--keys-dir", str(keys_dir)])

    assert code == 0
    assert capsys.readouterr().out.splitlines() == sorted(IDS)
    assert (keys_dir / "wearable-0001.crt").exists()


def test_replaces_an_older_certificate(run, keys_dir, certs):
    keys_dir.mkdir()
    (keys_dir / "wearable-0001.crt").write_text("synthetic old cert\n", "ascii")

    run(certs)

    assert (keys_dir / "wearable-0001.crt").read_text("ascii") == (
        certs["wearable-0001"].strip() + "\n"
    )


def test_does_not_touch_other_files_in_keys_dir(run, keys_dir, certs):
    keys_dir.mkdir()
    other = keys_dir / "wearable-0001.key"
    other.write_bytes(b"synthetic placeholder")

    run(certs)

    assert other.read_bytes() == b"synthetic placeholder"


def test_leaves_no_temp_files(run, keys_dir, certs):
    run(certs)

    assert not [p for p in keys_dir.iterdir() if p.suffix == ".tmp"]


# ---- invalid device IDs ----


@pytest.mark.parametrize(
    "bad_id",
    [
        "../wearable-0001",
        "wearable-0001/../../x",
        "wearable-001",
        "Wearable-0001",
        "watch-0001",
        "wearable-0001\n",
        "",
    ],
)
def test_rejects_invalid_device_id_and_writes_nothing(run, keys_dir, certs, bad_id):
    data = dict(certs)
    data[bad_id] = make_cert_pem("synthetic")

    code, out, err = run(data)

    assert code != 0
    assert "invalid device ID" in err
    assert out == ""
    assert written_files(keys_dir) == []


def test_invalid_device_id_is_not_written_outside_keys_dir(run, tmp_path, certs):
    data = {"../escape-0001": certs["wearable-0001"]}

    run(data)

    assert not (tmp_path / "escape-0001.crt").exists()
    assert not list(tmp_path.rglob("*.crt"))


# ---- non-PEM values ----


@pytest.mark.parametrize(
    "bad_value",
    [
        "not a certificate",
        "",
        123,
        None,
        ["-----BEGIN CERTIFICATE-----"],
        {"pem": "-----BEGIN CERTIFICATE-----"},
    ],
    ids=["text", "empty", "int", "null", "list", "object"],
)
def test_rejects_non_pem_value_and_writes_nothing(run, keys_dir, certs, bad_value):
    data = dict(certs)
    data["bedroom-0002"] = bad_value

    code, out, err = run(data)

    assert code != 0
    assert "bedroom-0002" in err
    assert "not a PEM certificate" in err
    assert out == ""
    assert written_files(keys_dir) == []


def test_rejects_truncated_certificate(run, keys_dir, certs):
    """A value with the PEM header but a corrupt body is not a certificate."""
    data = dict(certs)
    data["bedroom-0002"] = "-----BEGIN CERTIFICATE-----\nsynthetic-garbage\n"

    code, _, err = run(data)

    assert code != 0
    assert "bedroom-0002" in err
    assert written_files(keys_dir) == []


def test_rejects_private_key_value_without_echoing_it(run, keys_dir, certs):
    data = dict(certs)
    data["bedroom-0002"] = make_private_key_pem()

    code, out, err = run(data)

    assert code != 0
    assert PRIVATE_KEY_TEXT not in out + err
    assert "-----BEGIN" not in out + err
    assert written_files(keys_dir) == []


def test_rejects_certificate_bundled_with_private_key(run, keys_dir, certs):
    data = {"wearable-0001": certs["wearable-0001"] + make_private_key_pem()}

    code, out, err = run(data)

    assert code != 0
    assert PRIVATE_KEY_TEXT not in out + err
    assert written_files(keys_dir) == []


# ---- malformed file ----


@pytest.mark.parametrize(
    "data",
    [{}, [], "text", {"value": {}}, {"device_certificates": {"value": {}}}],
    ids=["empty-map", "list", "string", "empty-value", "empty-nested-value"],
)
def test_rejects_empty_or_non_map_input(run, keys_dir, data):
    code, out, err = run(data)

    assert code != 0
    assert "non-empty map" in err
    assert out == ""
    assert written_files(keys_dir) == []


def test_rejects_invalid_json_without_echoing_it(run, keys_dir):
    code, out, err = run(None, raw="synthetic-not-json-marker")

    assert code != 0
    assert "synthetic-not-json-marker" not in out + err
    assert written_files(keys_dir) == []


def test_missing_input_file_exits_non_zero(device_certs, tmp_path, keys_dir, capsys):
    code = device_certs.main(
        ["--keys-dir", str(keys_dir), "--input", str(tmp_path / "missing.json")]
    )

    assert code != 0
    assert "cannot read" in capsys.readouterr().err
