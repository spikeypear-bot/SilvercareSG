"""The "I'm OK" cancel looks up the wearable's resident without the 5-minute cache
(docs/features/possible-fall.md, criterion 13: a cancel can close the resident's
possible-fall alerts too, so a just-reassigned wearable must not act for its previous
resident). Other paths (fall, telemetry) keep using the cache.

Pure registry tests use a fake client and clock; the handler tests use moto.
"""

from __future__ import annotations

import handler
import pytest
import registry
from conftest import assign_device
from events import WEARABLE, rule_event, ts_at
from test_registry import Clock, FakeRegistry

OLD = "res-0001"
NEW = "res-0009"


def items(resident_id: str) -> dict[str, dict]:
    return {WEARABLE: {"resident_id": {"S": resident_id}}}


def lookup(client, clock, **kw):
    return registry.resident_for_device(
        client, "silvercare-registry", WEARABLE, clock=clock, **kw
    )


@pytest.fixture(autouse=True)
def _empty_cache():
    registry.clear_cache()
    yield
    registry.clear_cache()


# ======== registry.resident_for_device(fresh=...) ========


def test_fresh_lookup_skips_a_cached_result():
    client, clock = FakeRegistry(items(OLD)), Clock()
    lookup(client, clock)
    client.items = items(NEW)

    assert lookup(client, clock, fresh=True) == NEW
    assert len(client.calls) == 2


def test_fresh_lookup_refreshes_the_cache_for_later_cached_lookups():
    client, clock = FakeRegistry(items(OLD)), Clock()
    lookup(client, clock)
    client.items = items(NEW)
    lookup(client, clock, fresh=True)

    assert lookup(client, clock) == NEW
    assert len(client.calls) == 2


def test_default_lookup_still_uses_the_cache():
    client, clock = FakeRegistry(items(OLD)), Clock()
    lookup(client, clock)
    client.items = items(NEW)

    assert lookup(client, clock) == OLD
    assert len(client.calls) == 1


def test_fresh_lookup_of_a_now_unassigned_wearable_is_unassigned():
    client, clock = FakeRegistry(items(OLD)), Clock()
    lookup(client, clock)
    client.items = {}

    assert lookup(client, clock, fresh=True) == "unassigned"


# ======== the handlers ========


def resident_of(alerts_queue) -> list[str]:
    return [body["resident_id"] for body in alerts_queue.bodies()]


def test_cancel_after_a_cached_lookup_uses_the_new_resident(latest_table, alerts_queue):
    assign_device(latest_table, WEARABLE, OLD)
    handler.critical_alert_handler(rule_event("fall"), None)
    assert resident_of(alerts_queue) == [OLD]
    assign_device(latest_table, WEARABLE, NEW)

    handler.critical_alert_handler(rule_event("alert_cancel"), None)

    assert resident_of(alerts_queue) == [NEW]


def test_fall_after_a_reassignment_still_uses_the_cached_resident(
    latest_table, alerts_queue
):
    assign_device(latest_table, WEARABLE, OLD)
    handler.critical_alert_handler(rule_event("fall"), None)
    alerts_queue.bodies()
    assign_device(latest_table, WEARABLE, NEW)

    handler.critical_alert_handler(rule_event("fall"), None)

    assert resident_of(alerts_queue) == [OLD]


def test_fall_after_a_cancel_uses_the_resident_the_cancel_refreshed(
    latest_table, alerts_queue
):
    assign_device(latest_table, WEARABLE, OLD)
    handler.critical_alert_handler(rule_event("fall"), None)
    assign_device(latest_table, WEARABLE, NEW)
    handler.critical_alert_handler(rule_event("alert_cancel"), None)
    alerts_queue.bodies()

    handler.critical_alert_handler(rule_event("fall"), None)

    assert resident_of(alerts_queue) == [NEW]


def test_telemetry_after_a_reassignment_still_uses_the_cached_resident(
    latest_table, alerts_queue
):
    assign_device(latest_table, WEARABLE, OLD)
    for minute, hr in ((13, 125), (14, 128)):
        handler.telemetry_handler(
            rule_event("vitals", ts=ts_at(minute), hr_bpm=hr), None
        )
    assert set(resident_of(alerts_queue)) == {OLD}
    assign_device(latest_table, WEARABLE, NEW)

    handler.telemetry_handler(rule_event("vitals", ts=ts_at(15), hr_bpm=130), None)

    residents = resident_of(alerts_queue)
    assert residents
    assert set(residents) == {OLD}
