"""MQTT connections for a live run (criteria 16 and 17), and each device's classic
shadow (docs/features/device-alert-limits.md, criteria 19 and 20).

One MQTT/TLS connection per device (port 8883, CA verification on, client ID =
device ID), driven by a manual `loop()` from the run loop: no paho threads and no paho
auto-reconnect, so the connect rate and reconnect backoff are ours and testable with a
fake client.

Importing this module opens nothing and reads no files: paho is imported, and key
files are read, only when `paho_client_factory` builds a client in a live run.

After every accepted connection a device subscribes (QoS 1, one SUBSCRIBE) to its
shadow's get/accepted, get/rejected, and update/delta, and once that is acknowledged
publishes an empty message to shadow/get. Shadow messages received are handed to the
run loop (take_shadow_messages); shadow_limits() decides what a wearable does with one.

Output names device IDs and reason codes only, never payloads, limit values, key or
certificate contents, or exception messages (which can hold file paths).
"""

from __future__ import annotations

import contextlib
import json
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from residents import DeviceLimits, parse_device_limits

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

# Shadow topic suffixes (after $aws/things/<device_id>/shadow/).
SHADOW_GET = "get"
SHADOW_UPDATE = "update"
SHADOW_GET_ACCEPTED = "get/accepted"
SHADOW_GET_REJECTED = "get/rejected"
SHADOW_DELTA = "update/delta"
SHADOW_SUBSCRIPTIONS = (SHADOW_GET_ACCEPTED, SHADOW_GET_REJECTED, SHADOW_DELTA)
SHADOW_NOT_FOUND = 404

IDLE = "idle"
CONNECTING = "connecting"
CONNECTED = "connected"
WAITING = "waiting"
CLOSED = "closed"


class MqttClient(Protocol):
    """The part of paho.mqtt.client.Client the simulator uses."""

    on_connect: Any
    on_disconnect: Any
    on_subscribe: Any
    on_message: Any

    def connect(self, host: str, port: int, keepalive: int) -> Any: ...
    def loop(self, timeout: float) -> int: ...
    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> Any: ...
    def subscribe(self, topic: list[tuple[str, int]]) -> Any: ...
    def disconnect(self) -> Any: ...


ClientFactory = Callable[[str], MqttClient]


def shadow_topic(device_id: str, suffix: str) -> str:
    return f"$aws/things/{device_id}/shadow/{suffix}"


# What a wearable does with a shadow message (shadow_limits).
APPLY = "apply"  # apply and report these limits
REPORT = "report"  # no shadow: report the limits it has
REJECT = "reject"  # invalid limits: ignore, print "<device_id>: limits rejected"
IGNORE = "ignore"  # nothing to do


def _merge(current: Any, change: Any) -> Any:
    """A delta's state over the current value: a delta holds only what differs, so
    nested objects are merged key by key."""
    if not isinstance(current, dict) or not isinstance(change, dict):
        return change
    merged = dict(current)
    for key, value in change.items():
        merged[key] = _merge(current.get(key), value)
    return merged


def shadow_limits(
    suffix: str, payload: bytes, current: DeviceLimits
) -> tuple[str, DeviceLimits | None]:
    """(action, limits) for a wearable's shadow message (criterion 20):
    get/accepted with state.desired.limits, or update/delta with state.limits ->
    APPLY if valid, REJECT if not; get/rejected 404 -> REPORT the current limits;
    anything else (no limits in it, another code) -> IGNORE."""
    try:
        doc = json.loads(payload) if payload else {}
    except ValueError:
        doc = None
    if suffix == SHADOW_GET_REJECTED:
        code = doc.get("code") if isinstance(doc, dict) else None
        return (REPORT, current) if code == SHADOW_NOT_FOUND else (IGNORE, None)
    if suffix not in (SHADOW_GET_ACCEPTED, SHADOW_DELTA):
        return IGNORE, None
    if not isinstance(doc, dict):
        return REJECT, None
    state = doc.get("state")
    if suffix == SHADOW_GET_ACCEPTED:
        state = state.get("desired") if isinstance(state, dict) else None
    if not isinstance(state, dict) or "limits" not in state:
        return IGNORE, None
    raw = state["limits"]
    if suffix == SHADOW_DELTA:
        raw = _merge(current.to_dict(), raw)
    limits = parse_device_limits(raw)
    return (APPLY, limits) if limits is not None else (REJECT, None)


def reported_limits_payload(limits: DeviceLimits) -> bytes:
    return json.dumps(
        {"state": {"reported": {"limits": limits.to_dict()}}}, separators=(",", ":")
    ).encode("utf-8")


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
        self._shadow_in: list[tuple[str, str, bytes]] = []
        self.links: dict[str, _Link] = {}
        for device_id in device_ids:
            link = _Link(device_id, client_factory(device_id))
            link.client.on_connect = self._on_connect_cb(link)
            link.client.on_disconnect = self._on_disconnect_cb(link)
            link.client.on_subscribe = self._on_subscribe_cb(link)
            link.client.on_message = self._on_message_cb(link)
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

    @staticmethod
    def _on_subscribe_cb(link: _Link) -> Callable[..., None]:
        def on_subscribe(
            client, userdata, mid, reason_code_list=None, properties=None
        ) -> None:
            link.events.append(("suback", list(reason_code_list or [])))

        return on_subscribe

    @staticmethod
    def _on_message_cb(link: _Link) -> Callable[..., None]:
        def on_message(client, userdata, message) -> None:
            link.events.append(("message", (message.topic, message.payload)))

        return on_message

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
                    self._subscribe_shadow(link)
            elif kind == "suback" and link.state == CONNECTED:
                if any(_is_failure(rc) for rc in reason_code):
                    self._log(f"{link.device_id}: shadow subscribe refused")
                elif (
                    self.publish(
                        link.device_id, shadow_topic(link.device_id, SHADOW_GET), b""
                    )
                    != "sent"
                ):
                    self._log(f"{link.device_id}: shadow get not sent")
            elif kind == "message" and link.state == CONNECTED:
                topic, payload = reason_code
                prefix = shadow_topic(link.device_id, "")
                if isinstance(topic, str) and topic.startswith(prefix):
                    self._shadow_in.append(
                        (link.device_id, topic[len(prefix) :], bytes(payload or b""))
                    )
            elif kind == "disconnect" and link.state in (CONNECTING, CONNECTED):
                self._log(f"{link.device_id}: disconnected ({_reason(reason_code)})")
                self._retry_later(link, now_ms)

    def _subscribe_shadow(self, link: _Link) -> None:
        """Every connection (clean session): subscribe again, then get on SUBACK."""
        topics = [(shadow_topic(link.device_id, s), 1) for s in SHADOW_SUBSCRIPTIONS]
        try:
            result = link.client.subscribe(topics)
        except (OSError, ValueError):
            result = -1
        rc = result[0] if isinstance(result, tuple) else result
        if rc != MQTT_ERR_SUCCESS:
            self._log(f"{link.device_id}: shadow subscribe failed")

    def take_shadow_messages(self) -> list[tuple[str, str, bytes]]:
        """(device_id, topic suffix, payload) of every shadow message received since
        the last call, in order."""
        out, self._shadow_in = self._shadow_in, []
        return out

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
