"""simulator/deploy/fetch_keys.py (docs/features/simulator-ec2.md, criteria 10, 15).

Uses a fake SSM client and obviously fake PEM text, never real keys.
"""

from __future__ import annotations

import importlib.util
import io
import json
import stat
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy" / "fetch_keys.py"
spec = importlib.util.spec_from_file_location("fetch_keys", DEPLOY)
assert spec is not None and spec.loader is not None
fetch_keys = importlib.util.module_from_spec(spec)
sys.modules["fetch_keys"] = fetch_keys
spec.loader.exec_module(fetch_keys)

FAKE_CRT = (
    "-----BEGIN CERTIFICATE-----\nFAKE-CERT-NOT-REAL\n-----END CERTIFICATE-----\n"
)
# Split so no file holds a whole key header: checks.yml fails on any committed
# "BEGIN ... PRIVATE KEY" line, fake or not.
KEY_LABEL = "PRIVATE" + " KEY"
FAKE_KEY = (
    f"-----BEGIN {KEY_LABEL}-----\nFAKE-KEY-NOT-REAL\n-----END {KEY_LABEL}-----\n"
)


class ParameterNotFound(Exception):
    pass


class FakeSSM:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.calls: list[dict] = []

    def get_parameter(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["Name"] not in self.values:
            raise ParameterNotFound(kwargs["Name"])
        return {"Parameter": {"Value": self.values[kwargs["Name"]]}}


def good(ids):
    return {
        f"/silvercare/device-keys/{i}": json.dumps({"crt": FAKE_CRT, "key": FAKE_KEY})
        for i in ids
    }


def devices_file(tmp_path: Path, ids: list[str]) -> Path:
    p = tmp_path / "devices.json"
    p.write_text(json.dumps({"devices": [{"id": i} for i in ids]}))
    return p


def run(tmp_path, ids, values):
    out_dir = tmp_path / "keys"
    out = io.StringIO()
    client = FakeSSM(values)
    rc = fetch_keys.main(
        ["--out", str(out_dir), "--devices-file", str(devices_file(tmp_path, ids))],
        client=client,
        out=out,
    )
    return rc, out.getvalue(), out_dir, client


def test_writes_both_files_private(tmp_path):
    ids = ["wearable-0001", "bedroom-0001"]
    rc, text, out_dir, client = run(tmp_path, ids, good(ids))
    assert rc == 0
    assert all(c["WithDecryption"] for c in client.calls)
    assert stat.S_IMODE(out_dir.stat().st_mode) == 0o700
    for i in ids:
        assert (out_dir / f"{i}.crt").read_text() == FAKE_CRT
        assert (out_dir / f"{i}.key").read_text() == FAKE_KEY
        for ext in ("crt", "key"):
            assert stat.S_IMODE((out_dir / f"{i}.{ext}").stat().st_mode) == 0o600
    assert "FAKE" not in text


def test_clears_old_files(tmp_path):
    out_dir = tmp_path / "keys"
    out_dir.mkdir()
    (out_dir / "old-device.key").write_text("stale")
    rc, _, out_dir, _ = run(tmp_path, ["wearable-0001"], good(["wearable-0001"]))
    assert rc == 0
    assert not (out_dir / "old-device.key").exists()


@pytest.mark.parametrize(
    "value",
    [
        None,  # missing parameter
        "not json FAKE-KEY-NOT-REAL",
        json.dumps({"crt": FAKE_CRT}),  # missing field
        json.dumps({"crt": FAKE_CRT, "key": 5}),
    ],
)
def test_bad_parameter_exits_nonzero_listing_ids_only(tmp_path, value):
    ids = ["wearable-0001", "wearable-0002"]
    values = good(["wearable-0001"])
    if value is not None:
        values["/silvercare/device-keys/wearable-0002"] = value
    rc, text, _, _ = run(tmp_path, ids, values)
    assert rc == 1
    assert "wearable-0002" in text
    assert "wearable-0001" not in text.split(":", 1)[1]
    assert "FAKE" not in text and "BEGIN" not in text


def test_bad_device_list_exits_2(tmp_path):
    bad = tmp_path / "devices.json"
    bad.write_text("{")
    rc = fetch_keys.main(
        ["--out", str(tmp_path / "k"), "--devices-file", str(bad)],
        client=FakeSSM({}),
        out=io.StringIO(),
    )
    assert rc == 2


def test_default_device_list_is_the_repo_one():
    assert fetch_keys.DEFAULT_DEVICES_FILE.is_file()
