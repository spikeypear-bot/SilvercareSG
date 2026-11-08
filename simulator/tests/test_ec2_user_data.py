"""infra/simulator/user-data.sh.tftpl (docs/features/simulator-ec2.md, criteria 9-10).

Renders the boot script with fake values (as Terraform's templatefile would) and
checks the service's commands, without AWS or an instance.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = (
    Path(__file__).resolve().parents[2] / "infra" / "simulator" / "user-data.sh.tftpl"
)
ENDPOINT = "example-ats.iot.ap-southeast-1.amazonaws.com"


def rendered() -> str:
    text = TEMPLATE.read_text()
    text = text.replace("${repo_url}", "https://example.invalid/repo.git")
    text = text.replace("${iot_endpoint}", ENDPOINT)
    # Terraform turns $${ into ${; anything else ${...} is an unknown template variable.
    leftover = re.findall(r"(?<!\$)\$\{([a-z_]+)\}", text)
    assert leftover == [], f"unrendered template variables: {leftover}"
    return text.replace("$${", "${")


def unit_value(text: str, key: str) -> str:
    return re.search(rf"^{key}=(.*)$", text, re.MULTILINE).group(1)


def test_simulator_command_arguments():
    start = shlex.split(unit_value(rendered(), "ExecStart"))
    assert start[:5] == ["/usr/bin/tmux", "new-session", "-d", "-s", "sim"]
    sim = shlex.split(start[5])
    assert sim == [
        "/opt/silvercare-venv/bin/python",
        "simulator/src/main.py",
        "--endpoint",
        ENDPOINT,
        "--keys-dir",
        "/run/silvercare-keys",
    ]


def test_key_fetch_writes_where_the_simulator_reads():
    text = rendered()
    pre = shlex.split(unit_value(text, "ExecStartPre"))
    assert pre[1:] == [
        "simulator/deploy/fetch_keys.py",
        "--out",
        "/run/silvercare-keys",
    ]
    assert unit_value(text, "RuntimeDirectory") == "silvercare-keys"
    assert unit_value(text, "RuntimeDirectoryMode") == "0700"
    assert unit_value(text, "User") == "simulator"


def test_restarts_forever_every_60s():
    text = rendered()
    assert unit_value(text, "Restart") == "always"
    assert unit_value(text, "RestartSec") == "60"
    assert unit_value(text, "StartLimitIntervalSec") == "0"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_shell_syntax(tmp_path):
    script = tmp_path / "user-data.sh"
    script.write_text(rendered())
    subprocess.run(["bash", "-n", str(script)], check=True)
