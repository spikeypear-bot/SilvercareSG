"""MQTT connections for a live run (criteria 16 and 17).

One MQTT/TLS connection per device (port 8883, CA verification on, client ID =
device ID), driven by a manual `loop()` from the run loop: no paho threads and no paho
auto-reconnect, so the connect rate and reconnect backoff are ours and testable with a
fake client.

Importing this module opens nothing and reads no files: paho is imported, and key
files are read, only when `paho_client_factory` builds a client in a live run.

Output names device IDs and reason codes only, never payloads, key or certificate
contents, or exception messages (which can hold file paths).
"""

from __future__ import annotations

import contextlib
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

PORT = 8883
KEEPALIVE_S = 60
MQTT_ERR_SUCCESS = 0
# Connections are started at most 10 per second.
CONNECT_INTERVAL_MS = 100
CONNACK_TIMEOUT_MS = 10_000
# At least 1 s: AWS IoT drops a reconnect with the same client ID within 1 s of the
# last one.
BACKOFF_START_MS = 1_000
BACKOFF_MAX_MS = 60_000
# The backoff goes back to 1 s once a connection has stayed up this long, so a
# connection the broker keeps closing right away still backs off to 60 s.
STABLE_MS = 60_000

IDLE = "idle"
CONNECTING = "connecting"
CONNECTED = "connected"
WAITING = "waiting"
CLOSED = "closed"


class MqttClient(Protocol):
    """The part of paho.mqtt.client.Client the simulator uses."""

    on_connect: Any
    on_disconnect: Any

    def connect(self, host: str, port: int, keepalive: int) -> Any: ...
    def loop(self, timeout: float) -> int: ...
    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> Any: ...
    def disconnect(self) -> Any: ...


ClientFactory = Callable[[str], MqttClient]


@dataclass(frozen=True)
class TlsFiles:
    ca: Path
    cert: Path
    key: Path


def tls_files(device_id: str, keys_dir: Path, ca_file: Path) -> TlsFiles:
    return TlsFiles(
        ca=ca_file,
        cert=keys_dir / f"{device_id}.crt",
        key=keys_dir / f"{device_id}.key",
    )


def missing_key_files(
    device_ids: list[str], keys_dir: Path, ca_file: Path
) -> tuple[list[str], bool]:
    """(device IDs whose .crt or .key is missing, whether the CA file is missing).
    Checks existence only; reads nothing."""
    missing = [
        device_id
        for device_id in device_ids
        if not (keys_dir / f"{device_id}.crt").is_file()
        or not (keys_dir / f"{device_id}.key").is_file()
    ]
    return missing, not ca_file.is_file()


def paho_client_factory(keys_dir: Path, ca_file: Path) -> ClientFactory:
    """Build real paho clients (paho-mqtt 2.x), one per device, TLS with the device's
    own certificate and CA verification on."""

    def make(device_id: str) -> MqttClient:
        import paho.mqtt.client as mqtt  # only in a live run

        files = tls_files(device_id, keys_dir, ca_file)
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=device_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        client.tls_set(
            ca_certs=str(files.ca),
            certfile=str(files.cert),
            keyfile=str(files.key),
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        return client

    return make


def _is_failure(reason_code: Any) -> bool:
    flag = getattr(reason_code, "is_failure", None)
    if flag is not None:
        return bool(flag)
    return reason_code != 0


def _reason(reason_code: Any) -> str:
    """A reason code as a short string (paho ReasonCode str() is its name)."""
    if isinstance(reason_code, int):
        return f"rc {reason_code}"
    return str(reason_code)


@dataclass
class _Link:
    device_id: str
    client: MqttClient
    state: str = IDLE
    next_attempt_ms: int = 0
    backoff_ms: int = BACKOFF_START_MS
    last_attempt_ms: int | None = None
    connected_since_ms: int | None = None
    ever_connected: bool = False
    first_attempt_done: bool = False
    events: list[tuple[str, Any]] = field(default_factory=list)


class LinkManager:
    """Connects, reconnects, and publishes for every device."""

    def __init__(
        self,
        device_ids: list[str],
        endpoint: str,
        client_factory: ClientFactory,
        log: Callable[[str], None],
    ) -> None:
        self.endpoint = endpoint
        self._log = log
        self._last_start_ms: int | None = None
        self.links: dict[str, _Link] = {}
        for device_id in device_ids:
            link = _Link(device_id, client_factory(device_id))
            link.client.on_connect = self._on_connect_cb(link)
            link.client.on_disconnect = self._on_disconnect_cb(link)
            self.links[device_id] = link

    # paho VERSION2 callback signatures
    @staticmethod
    def _on_connect_cb(link: _Link) -> Callable[..., None]:
        def on_connect(client, userdata, flags, reason_code, properties=None) -> None:
            link.events.append(("connack", reason_code))

        return on_connect

    @staticmethod
    def _on_disconnect_cb(link: _Link) -> Callable[..., None]:
        def on_disconnect(
            client, userdata, flags=None, reason_code=None, properties=None
        ) -> None:
            link.events.append(("disconnect", reason_code))

        return on_disconnect

    # ---- state ----

    def connected_count(self) -> int:
        return sum(1 for link in self.links.values() if link.state == CONNECTED)

    def ever_connected_count(self) -> int:
        return sum(1 for link in self.links.values() if link.ever_connected)

    def none_could_connect(self) -> bool:
        """True once every device's first attempt has failed and none connected."""
        return all(
            link.first_attempt_done and not link.ever_connected
            for link in self.links.values()
        )

    def _retry_later(self, link: _Link, now_ms: int) -> None:
        link.state = WAITING
        link.first_attempt_done = True
        link.connected_since_ms = None
        link.next_attempt_ms = now_ms + link.backoff_ms
        link.backoff_ms = min(link.backoff_ms * 2, BACKOFF_MAX_MS)

    # ---- driving ----

    def _start(self, link: _Link, now_ms: int) -> None:
        self._last_start_ms = now_ms
        link.last_attempt_ms = now_ms
        link.state = CONNECTING
        link.events.clear()
        try:
            link.client.connect(self.endpoint, PORT, KEEPALIVE_S)
        except (OSError, ValueError) as exc:
            # DNS, TCP, or TLS failure. Only the exception type: messages can hold paths.
            self._log(f"{link.device_id}: connect failed ({type(exc).__name__})")
            self._retry_later(link, now_ms)

    def _handle_events(self, link: _Link, now_ms: int, newly: list[str]) -> None:
        events, link.events = link.events, []
        for kind, reason_code in events:
            if kind == "connack" and link.state == CONNECTING:
                if _is_failure(reason_code):
                    self._log(
                        f"{link.device_id}: connect refused ({_reason(reason_code)})"
                    )
                    with contextlib.suppress(OSError, ValueError):
                        link.client.disconnect()
                    self._retry_later(link, now_ms)
                else:
                    link.state = CONNECTED
                    link.first_attempt_done = True
                    link.connected_since_ms = now_ms
                    if not link.ever_connected:
                        link.ever_connected = True
                        newly.append(link.device_id)
            elif kind == "disconnect" and link.state in (CONNECTING, CONNECTED):
                self._log(f"{link.device_id}: disconnected ({_reason(reason_code)})")
                self._retry_later(link, now_ms)

    def poll(self, now_ms: int) -> list[str]:
        """Drive every connection once. Returns the devices that just connected for
        the first time (they publish their start-up messages)."""
        newly: list[str] = []
        for link in self.links.values():
            if link.state not in (CONNECTING, CONNECTED):
                continue
            try:
                rc = link.client.loop(0)
            except (OSError, ValueError) as exc:
                rc = -1
                self._log(f"{link.device_id}: connection error ({type(exc).__name__})")
            self._handle_events(link, now_ms, newly)
            if link.state in (CONNECTING, CONNECTED) and rc != MQTT_ERR_SUCCESS:
                self._log(f"{link.device_id}: connection lost (rc {rc})")
                self._retry_later(link, now_ms)
            elif (
                link.state == CONNECTING
                and link.last_attempt_ms is not None
                and now_ms - link.last_attempt_ms >= CONNACK_TIMEOUT_MS
            ):
                self._log(f"{link.device_id}: connect timed out")
                with contextlib.suppress(OSError, ValueError):
                    link.client.disconnect()
                self._retry_later(link, now_ms)
            elif (
                link.state == CONNECTED
                and link.connected_since_ms is not None
                and now_ms - link.connected_since_ms >= STABLE_MS
            ):
                link.backoff_ms = BACKOFF_START_MS

        # Start at most one connection per CONNECT_INTERVAL_MS, in device order.
        if (
            self._last_start_ms is None
            or now_ms - self._last_start_ms >= CONNECT_INTERVAL_MS
        ):
            for link in self.links.values():
                if link.state in (IDLE, WAITING) and link.next_attempt_ms <= now_ms:
                    self._start(link, now_ms)
                    break
        return newly

    def publish(self, device_id: str, topic: str, payload: bytes) -> str:
        """QoS 1, retain false. Returns sent, dropped (not connected), or failed."""
        link = self.links[device_id]
        if link.state != CONNECTED:
            return "dropped"
        try:
            info = link.client.publish(topic, payload, qos=1, retain=False)
        except (OSError, ValueError):
            return "failed"
        rc = getattr(info, "rc", info)
        return "sent" if rc == MQTT_ERR_SUCCESS else "failed"

    def close(self) -> None:
        """Disconnect every device cleanly."""
        for link in self.links.values():
            if link.state in (CONNECTING, CONNECTED):
                with contextlib.suppress(OSError, ValueError):
                    link.client.disconnect()
                    link.client.loop(0.1)
            link.state = CLOSED
