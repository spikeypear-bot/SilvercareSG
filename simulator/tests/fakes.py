"""A fake MQTT client (paho VERSION2 callback signatures) and a fake clock, so live-run
logic can be tested without a network, keys, or real time."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace


class FakeTime:
    """monotonic() and sleep() for clock.RealClock; sleep only moves time forward and
    runs any hooks that are due."""

    def __init__(self) -> None:
        self.start = 5000.0
        self.now = self.start
        self.hooks: list[tuple[float, Callable[[], None]]] = []

    def elapsed_ms(self) -> int:
        return round((self.now - self.start) * 1000)

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds > 0
        self.now += seconds
        for hook in list(self.hooks):
            at, action = hook
            if self.now - self.start >= at:
                self.hooks.remove(hook)
                action()


class Reason:
    """Like paho's ReasonCode: is_failure and str() is the name."""

    def __init__(self, failure: bool, name: str) -> None:
        self.is_failure = failure
        self.name = name

    def __str__(self) -> str:
        return self.name


OK = "ok"
REFUSE = "refuse"
RAISE = "raise"
SILENT = "silent"


@dataclass
class World:
    """Everything the fake clients did, and how the fake broker behaves.

    policy(device_id, attempt_number, elapsed_ms) -> OK | REFUSE | RAISE | SILENT
    drop(device_id, elapsed_ms) -> True to drop a connected client now.
    suback_ok(device_id) -> False to refuse a SUBSCRIBE (failure reason code).
    shadow(device_id, suffix, payload) -> [(suffix, payload)]: the fake shadow
    service's replies to a publish on $aws/things/<id>/shadow/<suffix>, delivered on
    the client's next loop(). incoming(device_id, elapsed_ms) -> [(suffix, payload)]:
    shadow messages pushed to a connected client on this loop() (e.g. update/delta).

    `published` holds telemetry only; shadow publishes ($aws/...) go to
    `shadow_published`, so telemetry counts don't change with the shadow traffic.
    """

    time: FakeTime
    policy: Callable[[str, int, int], str] = lambda d, n, t: OK
    drop: Callable[[str, int], bool] = lambda d, t: False
    publish_rc: Callable[[str], int] = lambda d: 0
    suback_ok: Callable[[str], bool] = lambda d: True
    shadow: Callable[[str, str, bytes], list] = lambda d, s, p: []
    incoming: Callable[[str, int], list] = lambda d, t: []
    secret_text: str = "Synthetic secret text"
    created: list[str] = field(default_factory=list)
    connects: list[tuple[int, str, str, int, int]] = field(default_factory=list)
    connacks: list[tuple[int, str, bool]] = field(default_factory=list)
    drops: list[tuple[int, str]] = field(default_factory=list)
    published: list[dict] = field(default_factory=list)
    shadow_published: list[dict] = field(default_factory=list)
    subscribes: list[tuple[int, str, list]] = field(default_factory=list)
    received: list[tuple[int, str, str]] = field(default_factory=list)
    disconnects: list[tuple[int, str]] = field(default_factory=list)
    clients: dict[str, FakeClient] = field(default_factory=dict)

    def factory(self, device_id: str) -> FakeClient:
        self.created.append(device_id)
        client = FakeClient(device_id, self)
        self.clients[device_id] = client
        return client

    def connect_times(self, device_id: str) -> list[int]:
        return [t for t, d, *_ in self.connects if d == device_id]

    def publishes(self, device_id: str) -> list[dict]:
        return [p for p in self.published if p["device_id"] == device_id]

    def shadow_publishes(self, device_id: str) -> list[dict]:
        return [p for p in self.shadow_published if p["device_id"] == device_id]


class FakeClient:
    def __init__(self, device_id: str, world: World) -> None:
        self.device_id = device_id
        self.world = world
        self.on_connect = None
        self.on_disconnect = None
        self.on_subscribe = None
        self.on_message = None
        self.attempts = 0
        self.suback_mid: int | None = None
        self.inbox: list[tuple[str, bytes]] = []
        self.mid = 0
        self.pending: str | None = None
        self.connected = False

    def connect(self, host: str, port: int, keepalive: int):
        now = self.world.time.elapsed_ms()
        self.attempts += 1
        self.world.connects.append((now, self.device_id, host, port, keepalive))
        behaviour = self.world.policy(self.device_id, self.attempts, now)
        if behaviour == RAISE:
            raise OSError(
                f"/secrets/device-keys/{self.device_id}.key {self.world.secret_text}"
            )
        self.pending = behaviour
        return 0

    def loop(self, timeout: float = 1.0) -> int:
        now = self.world.time.elapsed_ms()
        flags = SimpleNamespace(session_present=False)
        if self.pending == OK:
            self.pending = None
            self.connected = True
            self.world.connacks.append((now, self.device_id, True))
            self.on_connect(self, None, flags, Reason(False, "Success"), None)
        elif self.pending == REFUSE:
            self.pending = None
            self.world.connacks.append((now, self.device_id, False))
            self.on_connect(self, None, flags, Reason(True, "Not authorized"), None)
        elif self.connected:
            self._deliver(now)
            if self.connected and self.world.drop(self.device_id, now):
                self.connected = False
                self.suback_mid = None
                self.inbox = []
                self.world.drops.append((now, self.device_id))
                self.on_disconnect(
                    self, None, flags, Reason(True, "Unspecified error"), None
                )
        return 0

    def _deliver(self, now: int) -> None:
        """SUBACK, then shadow replies, then pushed messages (paho VERSION2)."""
        if self.suback_mid is not None:
            mid, self.suback_mid = self.suback_mid, None
            reason = (
                Reason(False, "Granted QoS 1")
                if self.world.suback_ok(self.device_id)
                else Reason(True, "Unspecified error")
            )
            self.on_subscribe(self, None, mid, [reason, reason, reason], None)
        messages, self.inbox = self.inbox, []
        messages += self.world.incoming(self.device_id, now)
        for suffix, payload in messages:
            topic = f"$aws/things/{self.device_id}/shadow/{suffix}"
            self.world.received.append((now, self.device_id, suffix))
            self.on_message(self, None, SimpleNamespace(topic=topic, payload=payload))

    def subscribe(self, topic: list[tuple[str, int]]):
        assert self.connected, "subscribed on a closed connection"
        self.mid += 1
        self.world.subscribes.append(
            (self.world.time.elapsed_ms(), self.device_id, list(topic))
        )
        self.suback_mid = self.mid
        return (0, self.mid)

    def publish(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False):
        if not self.connected and topic.startswith("$aws/"):
            # A SUBACK and the close can arrive in one loop(): paho returns
            # MQTT_ERR_NO_CONN for the shadow get then, it doesn't raise.
            return SimpleNamespace(rc=4, mid=0)
        assert self.connected, "published on a closed connection"
        record = {
            "at": self.world.time.elapsed_ms(),
            "device_id": self.device_id,
            "topic": topic,
            "payload": payload,
            "qos": qos,
            "retain": retain,
        }
        prefix = f"$aws/things/{self.device_id}/shadow/"
        if topic.startswith("$aws/"):
            self.world.shadow_published.append(record)
            if topic.startswith(prefix):
                suffix = topic[len(prefix) :]
                self.inbox += self.world.shadow(self.device_id, suffix, payload)
        else:
            self.world.published.append(record)
        return SimpleNamespace(rc=self.world.publish_rc(self.device_id), mid=1)

    def disconnect(self):
        self.world.disconnects.append((self.world.time.elapsed_ms(), self.device_id))
        self.connected = False
        self.pending = None
        self.suback_mid = None
        self.inbox = []
        return 0
