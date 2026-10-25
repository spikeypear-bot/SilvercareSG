"""Smoke test one IoT device against AWS IoT Core (docs/features/iot-core.md).

Run with `make smoke-test DEVICE=<id> ENDPOINT=<iot_endpoint>` in the dev container,
after `make device-certs`. Authenticates as the device with its own key and certificate
(MQTT over TLS, port 8883, CA devices/AmazonRootCA1.pem). Uses no AWS credentials.

Each check runs on a fresh connection:
  1. connect with client ID = <id>                        -> connected
  2. publish a synthetic status message (QoS 1), own topic -> PUBACK
  3. publish to another device's status topic              -> broker closes connection
  4. publish a message type the device type may not send   -> broker closes connection
  5. connect with a client ID that isn't <id>              -> refused or closed

With --ingest (docs/features/ingest-live.md), after those checks pass it also
publishes, on one more connection as the device: one valid synthetic message of each
type the device may send, the first of them again (a duplicate), and one invalid
message (wearable: vitals with hr_bpm 999; bedroom: bed with occupied "yes"). It then
prints the message IDs and the scripts/ingest-check.py command that checks they were
stored, plus its --history variant that also checks the S3 history
(docs/features/ingest-history.md). Those steps need AWS credentials and run on the host.

Prints only check names, PASS / FAIL, and message IDs, never payloads, keys, or
certificates. Exits 0 only if every check passes.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import ssl
import sys
import time
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import paho.mqtt.client as mqtt

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CA_FILE = REPO_ROOT / "devices" / "AmazonRootCA1.pem"
DEFAULT_KEYS_DIR = REPO_ROOT / "devices" / "keys"

PORT = 8883
TIMEOUT_S = 10.0
SCHEMA_VERSION = 1
FW_VERSION = "1.0.0"

DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-([0-9]{4})$")
ENDPOINT_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
MESSAGE_RE = re.compile(r"^[a-z]+$")

# Per type: the messages its IoT policy allows, and one it doesn't (docs/metrics.md).
ALLOWED_MESSAGES = {
    "wearable": ("vitals", "fall", "status"),
    "bedroom": ("motion", "bed", "status"),
}
DISALLOWED_MESSAGE = {"wearable": "motion", "bedroom": "vitals"}

CHECK_CONNECT = "1 connect with own client ID"
CHECK_PUBLISH = "2 publish status to own topic (PUBACK)"
CHECK_OTHER_DEVICE = "3 publish to another device's topic is refused"
CHECK_DISALLOWED = "4 publish a disallowed message type is refused"
CHECK_OTHER_CLIENT_ID = "5 connect with another client ID is refused"
CHECK_INGEST = "6 publish ingest test messages (PUBACK for each)"

# Fall accel_window shape (docs/metrics.md): 25 Hz, 1 s before to 1 s after impact.
FALL_WINDOW_HZ = 25
FALL_WINDOW_START_OFFSET_MS = -1000
FALL_WINDOW_SAMPLES = 50

# The invalid message per device type: (message type, field, invalid value).
INVALID_INGEST = {
    "wearable": ("vitals", "hr_bpm", 999),
    "bedroom": ("bed", "occupied", "yes"),
}


# ---- pure helpers (unit tested, no network) ----


def device_type(device_id: str) -> str:
    """Return wearable or bedroom from a device ID. Raises ValueError if malformed."""
    match = DEVICE_ID_RE.fullmatch(device_id)
    if not match:
        raise ValueError("device ID must match ^(wearable|bedroom)-[0-9]{4}$")
    return match.group(1)


def build_topic(device_id: str, message: str) -> str:
    """Return silvercare/v1/<device_id>/<message>."""
    device_type(device_id)
    if not MESSAGE_RE.fullmatch(message):
        raise ValueError("message must be lowercase letters only")
    return f"silvercare/v1/{device_id}/{message}"


def other_device_id(device_id: str) -> str:
    """Return a different device ID of the same type (for the cross-device check)."""
    dtype = device_type(device_id)
    number = int(device_id.rsplit("-", 1)[1])
    other = 2 if number == 1 else 1
    return f"{dtype}-{other:04d}"


def other_client_id(device_id: str) -> str:
    """Return a client ID that isn't the device's thing name."""
    device_type(device_id)
    return f"{device_id}-not-me"


def format_ts(now: datetime) -> str:
    """ISO 8601 UTC with milliseconds and Z, e.g. 2026-10-18T03:15:00.000Z."""
    now = now.astimezone(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def build_envelope(now: datetime | None = None, message_id: str | None = None) -> dict:
    """The fields every message has (docs/metrics.md, Envelope)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "message_id": message_id or str(uuid.uuid4()),
        "ts": format_ts(now or datetime.now(UTC)),
    }


def build_status_payload(
    dtype: str, now: datetime | None = None, message_id: str | None = None
) -> dict:
    """A valid synthetic status message for the device type (docs/metrics.md)."""
    payload = build_envelope(now, message_id)
    if dtype == "wearable":
        payload.update(
            {
                "battery_pct": 100,
                "worn": False,
                "charging": True,
                "fw_version": FW_VERSION,
            }
        )
    elif dtype == "bedroom":
        payload.update({"battery_pct": 100, "fw_version": FW_VERSION})
    else:
        raise ValueError("device type must be wearable or bedroom")
    return payload


def build_message_payload(
    dtype: str, message: str, now: datetime | None = None, message_id: str | None = None
) -> dict:
    """A valid synthetic message of any type the device type may send (docs/metrics.md)."""
    if message not in ALLOWED_MESSAGES.get(dtype, ()):
        raise ValueError(f"{dtype} devices don't send {message}")
    if message == "status":
        return build_status_payload(dtype, now, message_id)
    payload = build_envelope(now, message_id)
    if message == "vitals":
        payload.update(
            {
                "hr_bpm": 72,
                "spo2_pct": 97,
                "skin_temp_c": 33.4,
                "activity_pct": 12,
                "accel_g": {"x": 0.02, "y": -0.11, "z": 0.99},
            }
        )
    elif message == "fall":
        payload.update(
            {
                "confidence": 0.91,
                "peak_g": 3.8,
                "still_s": 20,
                "accel_window": {
                    "hz": FALL_WINDOW_HZ,
                    "start_offset_ms": FALL_WINDOW_START_OFFSET_MS,
                    "samples": [[0.01, -0.1, 0.99]] * FALL_WINDOW_SAMPLES,
                },
            }
        )
    elif message == "motion":
        payload["motion"] = True
    elif message == "bed":
        payload["occupied"] = False
    return payload


def build_ingest_messages(
    dtype: str,
    now: datetime | None = None,
    new_id: Callable[[], str] | None = None,
) -> list[tuple[str, str, dict]]:
    """The --ingest messages in send order, as (kind, message type, payload).

    kind is valid, duplicate, or invalid: one valid message per allowed type, the
    first valid one again, then one message that fails validation.
    """
    if dtype not in ALLOWED_MESSAGES:
        raise ValueError("device type must be wearable or bedroom")
    new_id = new_id or (lambda: str(uuid.uuid4()))
    now = now or datetime.now(UTC)
    valid = [
        ("valid", message, build_message_payload(dtype, message, now, new_id()))
        for message in ALLOWED_MESSAGES[dtype]
    ]
    _, first_message, first_payload = valid[0]
    duplicate = ("duplicate", first_message, dict(first_payload))
    invalid_message, field, value = INVALID_INGEST[dtype]
    invalid_payload = build_message_payload(dtype, invalid_message, now, new_id())
    invalid_payload[field] = value
    return [*valid, duplicate, ("invalid", invalid_message, invalid_payload)]


def ingest_check_command(
    device_id: str,
    valid_ids: Iterable[str],
    invalid_ids: Iterable[str],
    history: bool = False,
) -> str:
    """The command that checks the --ingest messages were stored (run on the host).

    With history=True, the variant that also checks the S3 history.
    """
    return (
        f"python scripts/ingest-check.py --device {device_id}"
        f" --message-ids {','.join(valid_ids)}"
        f" --invalid-ids {','.join(invalid_ids)}"
        f"{' --history' if history else ''}"
    )


def report_ingest(
    device_id: str, messages: list[tuple[str, str, dict]], out: TextIO | None = None
) -> None:
    """Print the sent message IDs (no other fields) and the next step."""
    out = out or sys.stdout
    print("Ingest messages sent:", file=out)
    for kind, message, payload in messages:
        print(f"  {kind:<9}  {message:<6}  {payload['message_id']}", file=out)
    valid_ids = [p["message_id"] for k, _, p in messages if k == "valid"]
    invalid_ids = [p["message_id"] for k, _, p in messages if k == "invalid"]
    # The plain command stays the last line (scripts and tests read it from there).
    print(
        "Next, on the host with your AWS credentials, check silvercare-latest and"
        " the S3 history (takes up to 7 minutes):",
        file=out,
    )
    print(
        f"  {ingest_check_command(device_id, valid_ids, invalid_ids, history=True)}",
        file=out,
    )
    print("or silvercare-latest only:", file=out)
    print(f"  {ingest_check_command(device_id, valid_ids, invalid_ids)}", file=out)


def encode_payload(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def report(results: Iterable[tuple[str, bool]], out: TextIO | None = None) -> int:
    """Print PASS / FAIL per check and return the exit code (0 only if all passed)."""
    out = out or sys.stdout
    results = list(results)
    for name, passed in results:
        print(f"{'PASS' if passed else 'FAIL'}  {name}", file=out)
    passed_count = sum(1 for _, passed in results if passed)
    print(f"{passed_count}/{len(results)} checks passed", file=out)
    return 0 if results and passed_count == len(results) else 1


# ---- MQTT (needs the network; not unit tested) ----


class Connection:
    """One MQTT/TLS connection, driven by a manual loop (no thread, no auto reconnect)."""

    def __init__(self, endpoint: str, client_id: str, tls: dict[str, str]) -> None:
        self.endpoint = endpoint
        self.connected = False
        self.refused = False
        self.closed = False
        self.acked: set[int] = set()
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        self.client.tls_set(
            ca_certs=tls["ca"],
            certfile=tls["cert"],
            keyfile=tls["key"],
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            self.refused = True
        else:
            self.connected = True

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.closed = True

    def _on_publish(self, client, userdata, mid, reason_code, properties) -> None:
        self.acked.add(mid)

    def _run_until(self, done: Callable[[], bool]) -> None:
        deadline = time.monotonic() + TIMEOUT_S
        while not done() and time.monotonic() < deadline:
            if self.client.loop(timeout=0.2) != mqtt.MQTT_ERR_SUCCESS:
                self.closed = True

    def connect(self) -> str:
        """Return connected, refused, closed, timeout, or error."""
        try:
            self.client.connect(self.endpoint, PORT, keepalive=30)
        except (OSError, ValueError):
            # DNS, TCP, or TLS failure (ssl errors are OSErrors): the check can't run.
            return "error"
        self._run_until(lambda: self.connected or self.refused or self.closed)
        if self.connected:
            return "connected"
        if self.refused:
            return "refused"
        return "closed" if self.closed else "timeout"

    def publish(self, topic: str, payload: bytes) -> str:
        """Publish at QoS 1. Return puback, closed, or timeout."""
        info = self.client.publish(topic, payload, qos=1, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            self.closed = True
        self._run_until(lambda: info.mid in self.acked or self.closed)
        if info.mid in self.acked:
            return "puback"
        return "closed" if self.closed else "timeout"

    def close(self) -> None:
        """Best-effort clean disconnect, so the next check starts fresh."""
        with contextlib.suppress(OSError, ValueError):
            if self.connected and not self.closed:
                self.client.disconnect()
                self.client.loop(timeout=0.2)


def run_checks(
    device_id: str, endpoint: str, tls: dict[str, str]
) -> list[tuple[str, bool]]:
    dtype = device_type(device_id)

    def with_connection(client_id: str, action: Callable[[Connection], bool]) -> bool:
        conn = Connection(endpoint, client_id, tls)
        try:
            return action(conn)
        finally:
            conn.close()

    def check_connect(conn: Connection) -> bool:
        return conn.connect() == "connected"

    def check_publish(conn: Connection) -> bool:
        if conn.connect() != "connected":
            return False
        topic = build_topic(device_id, "status")
        return (
            conn.publish(topic, encode_payload(build_status_payload(dtype))) == "puback"
        )

    def expect_publish_refused(
        topic: str, payload: dict
    ) -> Callable[[Connection], bool]:
        def check(conn: Connection) -> bool:
            if conn.connect() != "connected":
                return False
            return conn.publish(topic, encode_payload(payload)) == "closed"

        return check

    def check_other_client_id(conn: Connection) -> bool:
        return conn.connect() in ("refused", "closed")

    other_topic = build_topic(other_device_id(device_id), "status")
    disallowed_topic = build_topic(device_id, DISALLOWED_MESSAGE[dtype])
    return [
        (CHECK_CONNECT, with_connection(device_id, check_connect)),
        (CHECK_PUBLISH, with_connection(device_id, check_publish)),
        (
            CHECK_OTHER_DEVICE,
            with_connection(
                device_id,
                expect_publish_refused(other_topic, build_status_payload(dtype)),
            ),
        ),
        (
            CHECK_DISALLOWED,
            with_connection(
                device_id, expect_publish_refused(disallowed_topic, build_envelope())
            ),
        ),
        (
            CHECK_OTHER_CLIENT_ID,
            with_connection(other_client_id(device_id), check_other_client_id),
        ),
    ]


def run_ingest(
    device_id: str,
    endpoint: str,
    tls: dict[str, str],
    messages: list[tuple[str, str, dict]],
) -> bool:
    """Publish the --ingest messages in order on one connection. True if all got PUBACK."""
    conn = Connection(endpoint, device_id, tls)
    try:
        if conn.connect() != "connected":
            return False
        for _, message, payload in messages:
            topic = build_topic(device_id, message)
            if conn.publish(topic, encode_payload(payload)) != "puback":
                return False
        return True
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test one device against AWS IoT Core."
    )
    parser.add_argument("--device", required=True, help="device ID, e.g. wearable-0001")
    parser.add_argument(
        "--endpoint", required=True, help="the iot_endpoint Terraform output"
    )
    parser.add_argument(
        "--job",
        action="store_true",
        help="reserved for the ota-updates jobs check (not available yet)",
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="after the checks, publish ingest test messages and print the ingest-check command",
    )
    parser.add_argument("--ca-file", type=Path, default=DEFAULT_CA_FILE)
    parser.add_argument("--keys-dir", type=Path, default=DEFAULT_KEYS_DIR)
    args = parser.parse_args(argv)

    if args.job:
        print(
            "error: the jobs check comes with the ota-updates feature", file=sys.stderr
        )
        return 2
    try:
        device_type(args.device)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not ENDPOINT_RE.fullmatch(args.endpoint):
        print(
            "error: --endpoint must be a host name (the iot_endpoint output)",
            file=sys.stderr,
        )
        return 2

    tls = {
        "ca": args.ca_file,
        "cert": args.keys_dir / f"{args.device}.crt",
        "key": args.keys_dir / f"{args.device}.key",
    }
    missing = [str(p) for p in tls.values() if not p.is_file()]
    if missing:
        print(
            f"error: missing {', '.join(missing)} (see README: IoT devices)",
            file=sys.stderr,
        )
        return 2

    tls_paths = {k: str(v) for k, v in tls.items()}
    results = run_checks(args.device, args.endpoint, tls_paths)
    if not args.ingest:
        return report(results)

    if not all(passed for _, passed in results):
        print("Skipping the ingest messages: an earlier check failed.", file=sys.stderr)
        return report([*results, (CHECK_INGEST, False)])
    messages = build_ingest_messages(device_type(args.device))
    published = run_ingest(args.device, args.endpoint, tls_paths, messages)
    code = report([*results, (CHECK_INGEST, published)])
    if published:
        report_ingest(args.device, messages)
    return code


if __name__ == "__main__":
    sys.exit(main())
