"""Device -> resident lookup and its 5-minute cache (alerts-core criterion 6).

Uses a fake client and a fake clock; no moto needed.
"""

from __future__ import annotations

import pytest
import registry
from botocore.exceptions import ClientError


@pytest.fixture(autouse=True)
def _empty_cache():
    registry.clear_cache()
    yield
    registry.clear_cache()


class FakeRegistry:
    """GetItem against an in-memory dict of device_id -> low-level item."""

    def __init__(self, items: dict[str, dict] | None = None, error: str | None = None):
        self.items = items if items is not None else {}
        self.error = error
        self.calls: list[dict] = []

    def get_item(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise ClientError(
                {"Error": {"Code": self.error, "Message": "synthetic"}}, "GetItem"
            )
        device_id = kwargs["Key"]["pk"]["S"].removeprefix("DEVICE#")
        item = self.items.get(device_id)
        return {"Item": item} if item is not None else {}


class Clock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def assigned() -> dict[str, dict]:
    return {"wearable-0001": {"resident_id": {"S": "res-0001"}}}


def lookup(client, device_id="wearable-0001", clock=None):
    return registry.resident_for_device(
        client, "silvercare-registry", device_id, clock=clock or Clock()
    )


def test_assigned_device_returns_its_resident():
    assert lookup(FakeRegistry(assigned())) == "res-0001"


def test_lookup_reads_the_assignment_item_key():
    client = FakeRegistry(assigned())

    lookup(client)

    (call,) = client.calls
    assert call["TableName"] == "silvercare-registry"
    assert call["Key"] == {
        "pk": {"S": "DEVICE#wearable-0001"},
        "sk": {"S": "ASSIGNMENT"},
    }
    assert call["ProjectionExpression"] == "resident_id"
    assert not call.get("ConsistentRead")


def test_missing_item_is_unassigned():
    assert lookup(FakeRegistry({})) == "unassigned"


@pytest.mark.parametrize(
    "item",
    [
        {},
        {"resident_id": {"S": ""}},
        {"resident_id": {"NULL": True}},
        {"resident_id": {"N": "1"}},
    ],
)
def test_item_without_a_string_resident_id_is_unassigned(item):
    assert lookup(FakeRegistry({"wearable-0001": item})) == "unassigned"


def test_assigned_result_is_cached():
    client = FakeRegistry(assigned())
    clock = Clock()

    lookup(client, clock=clock)
    clock.now += 299
    assert lookup(client, clock=clock) == "res-0001"

    assert len(client.calls) == 1


def test_unassigned_result_is_cached_too():
    client = FakeRegistry({})
    clock = Clock()

    lookup(client, clock=clock)
    # Assigned after the first lookup: still unassigned until the cache expires.
    client.items.update(assigned())
    clock.now += 299

    assert lookup(client, clock=clock) == "unassigned"
    assert len(client.calls) == 1


@pytest.mark.parametrize(("elapsed", "calls"), [(299.9, 1), (300, 2), (301, 2)])
def test_cache_expires_after_five_minutes(elapsed, calls):
    client = FakeRegistry({})
    clock = Clock()
    lookup(client, clock=clock)
    client.items.update(assigned())
    clock.now += elapsed

    result = lookup(client, clock=clock)

    assert len(client.calls) == calls
    assert result == ("unassigned" if calls == 1 else "res-0001")


def test_cache_is_per_device():
    client = FakeRegistry(assigned())
    clock = Clock()

    assert lookup(client, "wearable-0001", clock) == "res-0001"
    assert lookup(client, "wearable-0002", clock) == "unassigned"

    assert len(client.calls) == 2


def test_clear_cache_forces_a_new_lookup():
    client = FakeRegistry(assigned())
    lookup(client)

    registry.clear_cache()
    lookup(client)

    assert len(client.calls) == 2


@pytest.mark.parametrize(
    "code", ["ProvisionedThroughputExceededException", "ThrottlingException"]
)
def test_aws_error_is_raised_and_not_cached(code):
    client = FakeRegistry(assigned(), error=code)

    with pytest.raises(ClientError):
        lookup(client)

    client.error = None
    assert lookup(client) == "res-0001"
