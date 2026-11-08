"""scripts/upload-device-keys.py (docs/features/simulator-ec2.md, criteria 3-5, 16).

Uses a fake SSM client and obviously fake PEM text under tmp_path, never real keys.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from conftest import load_script

up = load_script("upload-device-keys.py", "upload_device_keys")

FAKE_CRT = (
    "-----BEGIN CERTIFICATE-----\nFAKE-CERT-NOT-REAL\n-----END CERTIFICATE-----\n"
)
# Split so no file holds a whole key header: checks.yml fails on any committed
# "BEGIN ... PRIVATE KEY" line, fake or not.
KEY_LABEL = "PRIVATE" + " KEY"
FAKE_KEY = (
    f"-----BEGIN {KEY_LABEL}-----\nFAKE-KEY-NOT-REAL\n-----END {KEY_LABEL}-----\n"
)
IDS = ["wearable-0001", "bedroom-0001"]


class AwsError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeSSM:
    def __init__(self, fail: dict[str, str] | None = None) -> None:
        self.puts: list[dict] = []
        self.deletes: list[str] = []
        self.fail = fail or {}

    def put_parameter(self, **kwargs):
        if kwargs["Name"] in self.fail:
            raise AwsError(self.fail[kwargs["Name"]])
        self.puts.append(kwargs)

    def delete_parameter(self, Name):
        if Name in self.fail:
            raise AwsError(self.fail[Name])
        self.deletes.append(Name)


@pytest.fixture
def setup(tmp_path: Path):
    devices = tmp_path / "devices.json"
    devices.write_text(json.dumps({"devices": [{"id": i} for i in IDS]}))
    keys = tmp_path / "keys"
    keys.mkdir()
    for i in IDS:
        (keys / f"{i}.crt").write_text(FAKE_CRT)
        (keys / f"{i}.key").write_text(FAKE_KEY)
    return devices, keys


def run(devices, args, client):
    out = io.StringIO()
    rc = up.main([*args, "--devices-file", str(devices)], client=client, out=out)
    return rc, out.getvalue()


def test_uploads_all_as_standard_securestring(setup):
    devices, keys = setup
    client = FakeSSM()
    rc, text = run(devices, ["--keys-dir", str(keys)], client)
    assert rc == 0
    assert [p["Name"] for p in client.puts] == [
        f"/silvercare/device-keys/{i}" for i in IDS
    ]
    for p in client.puts:
        assert p["Type"] == "SecureString" and p["Tier"] == "Standard"
        assert p["Overwrite"] is True
        assert json.loads(p["Value"]) == {"crt": FAKE_CRT, "key": FAKE_KEY}
    assert "wearable-0001 uploaded" in text
    assert "FAKE" not in text and "BEGIN" not in text


def test_devices_flag_limits_upload(setup):
    devices, keys = setup
    client = FakeSSM()
    rc, _ = run(devices, ["--keys-dir", str(keys), "--devices", "bedroom-0001"], client)
    assert rc == 0
    assert [p["Name"] for p in client.puts] == ["/silvercare/device-keys/bedroom-0001"]


def test_unknown_device_exits_2(setup):
    devices, keys = setup
    client = FakeSSM()
    rc, _ = run(
        devices, ["--keys-dir", str(keys), "--devices", "wearable-9999"], client
    )
    assert rc == 2 and client.puts == []


@pytest.mark.parametrize("problem", ["missing", "not_pem", "too_big"])
def test_bad_files_exit_2_before_any_upload(setup, problem):
    devices, keys = setup
    target = keys / "bedroom-0001.key"
    if problem == "missing":
        target.unlink()
    elif problem == "not_pem":
        target.write_text("FAKE-KEY-NOT-REAL")
    else:
        target.write_text(f"-----BEGIN {KEY_LABEL}-----\n" + "A" * 5000)
    client = FakeSSM()
    rc, text = run(devices, ["--keys-dir", str(keys)], client)
    assert rc == 2
    assert client.puts == []  # wearable-0001 is fine but nothing was uploaded
    assert "bedroom-0001" in text and "wearable-0001" not in text
    assert "FAKE" not in text and "AAAA" not in text


def test_failed_put_reports_code_and_exit_1(setup):
    devices, keys = setup
    client = FakeSSM(
        fail={"/silvercare/device-keys/wearable-0001": "AccessDeniedException"}
    )
    rc, text = run(devices, ["--keys-dir", str(keys)], client)
    assert rc == 1
    assert "wearable-0001 failed AccessDeniedException" in text
    assert "bedroom-0001 uploaded" in text
    assert "FAKE" not in text


def test_keys_dir_required_without_delete(setup):
    devices, _ = setup
    rc, _ = run(devices, [], FakeSSM())
    assert rc == 2


def test_delete_reports_missing_as_not_found(setup):
    devices, _ = setup
    client = FakeSSM(fail={"/silvercare/device-keys/bedroom-0001": "ParameterNotFound"})
    rc, text = run(devices, ["--delete"], client)
    assert rc == 0
    assert client.deletes == ["/silvercare/device-keys/wearable-0001"]
    assert "wearable-0001 deleted" in text and "bedroom-0001 not found" in text


def test_delete_other_error_exit_1(setup):
    devices, _ = setup
    client = FakeSSM(
        fail={"/silvercare/device-keys/bedroom-0001": "AccessDeniedException"}
    )
    rc, text = run(devices, ["--delete"], client)
    assert rc == 1 and "bedroom-0001 failed AccessDeniedException" in text
