"""Live-alert pings (docs/features/live-alerts.md, criteria 13-17).

After an alert change is written, one ping per (namespace, resident) goes to the
silvercare-events AppSync Event API: `/health/<resident_id>` or `/device/<resident_id>`,
payload exactly {"type": "changed", "resident_id": ...}. Pages refetch through the API,
which decides what each user may see.

Publishing never fails the SQS record: the alert is already stored and emailed, and
pages poll every 30 s anyway. `publish` returns None on success or an error code to
log. Signed with SigV4 (service `appsync`) using botocore, sent with urllib.

Budget (criterion 16a): pings must never slow the alert path. `start_batch` (called
once per invocation) allows BATCH_BUDGET_S of pinging in total; after the first failed
ping, or once the budget is spent, the rest of the batch is skipped ("skipped").
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from collections.abc import Iterable
from functools import cache
from typing import Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

TIMEOUT_S = 1.0  # per network step; urlopen applies it to connect and read separately
BATCH_BUDGET_S = 1.5
SKIPPED = "skipped"

# Per invocation: deadline (time.monotonic) and whether a ping has failed. No deadline
# (handle_record called on its own, e.g. in tests): no budget.
_batch: dict[str, Any] = {"deadline": None, "tripped": False}


def start_batch() -> None:
    _batch["deadline"] = time.monotonic() + BATCH_BUDGET_S
    _batch["tripped"] = False


def _timeout() -> float | None:
    """This ping's timeout, or None to skip it."""
    if _batch["tripped"]:
        return None
    deadline = _batch["deadline"]
    if deadline is None:
        return TIMEOUT_S
    remaining = deadline - time.monotonic()
    return min(TIMEOUT_S, remaining) if remaining > 0 else None


HEALTH = "health"
DEVICE = "device"


@cache
def _credentials() -> Any:
    return boto3.Session().get_credentials()


def namespace_for(alert_type: Any, device_problem_types: Iterable[str]) -> str:
    return DEVICE if alert_type in set(device_problem_types) else HEALTH


def channel(namespace: str, resident_id: str) -> str:
    return f"/{namespace}/{resident_id}"


def _post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> int:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


def publish(namespace: str, resident_id: str) -> str | None:
    """Publish one ping. None on success, else a short error code (never raises)."""
    host = os.environ.get("EVENTS_HTTP_DNS")
    if not host:
        return None  # not configured (local runs and tests): nothing to tell
    timeout = _timeout()
    if timeout is None:
        return SKIPPED
    try:
        url = f"https://{host}/event"
        payload = {"type": "changed", "resident_id": resident_id}
        body = json.dumps(
            {
                "channel": channel(namespace, resident_id),
                "events": [json.dumps(payload)],
            },
            separators=(",", ":"),
        ).encode()
        signed = AWSRequest(
            method="POST",
            url=url,
            data=body,
            headers={"content-type": "application/json", "host": host},
        )
        region = os.environ.get("AWS_REGION", "ap-southeast-1")
        SigV4Auth(_credentials(), "appsync", region).add_auth(signed)
        status = _post(url, body, dict(signed.headers.items()), timeout)
    except Exception as exc:  # noqa: BLE001 - a ping must never fail the record
        _batch["tripped"] = True
        return type(exc).__name__
    if 200 <= status < 300:
        return None
    _batch["tripped"] = True
    return f"http_{status}"
