"""worn_false_since on wearable MSG#status items (docs/features/watchdog.md, criteria
9 and 20): set, kept, removed, unchanged by a status that isn't the newest, never on
bedroom units, and not in the by-message projection. The pure rule is tested directly;
the rest end to end through device_health_handler against moto."""

from __future__ import annotations

import handler
import latest_store
import pytest
from boto3.dynamodb.types import TypeDeserializer
from conftest import TABLE_NAME
from events import BEDROOM, WEARABLE, rule_event, ts_at

_deserializer = TypeDeserializer()


def invoke(event: dict) -> dict:
    return handler.device_health_handler(event, None)


def read_item(client, device_id: str = WEARABLE) -> dict:
    resp = client.get_item(
        TableName=TABLE_NAME,
        Key={"pk": {"S": f"DEVICE#{device_id}"}, "sk": {"S": "MSG#status"}},
        ConsistentRead=True,
    )
    return {k: _deserializer.deserialize(v) for k, v in resp["Item"].items()}


def wearable_status(minute: int, worn: bool, charging: bool = False) -> dict:
    return rule_event(
        "status", WEARABLE, ts=ts_at(minute), worn=worn, charging=charging
    )


# ======== the pure rule ========


def entry(n: int, worn) -> dict:
    return {
        "message_id": f"00000000-0000-4000-8000-{n:012d}",
        "ts": ts_at(n),
        "worn": worn,
    }


def rule(previous, entries, new, device_type="wearable", message="status"):
    return latest_store.worn_false_since(device_type, message, previous, entries, new)


def test_rule_sets_since_from_the_newest_entry_when_none_yet():
    new = entry(5, False)

    assert rule(None, [new], new) == ts_at(5)


def test_rule_keeps_an_existing_since_while_still_off_wrist():
    new = entry(5, False)

    assert rule(ts_at(1), [new, entry(1, False)], new) == ts_at(1)


def test_rule_removes_since_when_worn_again():
    new = entry(5, True)

    assert rule(ts_at(1), [new, entry(1, False)], new) is None


@pytest.mark.parametrize("previous", [None, ts_at(1)])
@pytest.mark.parametrize("worn", [True, False])
def test_rule_keeps_the_value_as_read_for_an_entry_that_is_not_the_newest(
    previous, worn
):
    late = entry(3, worn)

    assert rule(previous, [entry(9, False), late], late) == previous


@pytest.mark.parametrize(
    ("device_type", "message"),
    [("bedroom", "status"), ("wearable", "vitals"), ("bedroom", "motion")],
)
def test_rule_never_writes_since_for_other_items(device_type, message):
    new = entry(5, False)

    assert rule(ts_at(1), [new], new, device_type, message) is None


def test_build_item_adds_since_only_when_given():
    entries = [entry(5, False)]

    with_since = latest_store.build_item(
        WEARABLE, "wearable", "status", entries, 1, ts_at(5)
    )
    without = latest_store.build_item(WEARABLE, "wearable", "status", entries, 1)

    assert with_since["worn_false_since"] == ts_at(5)
    assert "worn_false_since" not in without


# ======== end to end ========


def test_first_off_wrist_status_sets_since_to_its_ts(latest_table):
    invoke(wearable_status(10, worn=False))

    assert read_item(latest_table)["worn_false_since"] == ts_at(10)


def test_later_off_wrist_status_keeps_the_first_since(latest_table):
    invoke(wearable_status(10, worn=False))
    invoke(wearable_status(20, worn=False))

    item = read_item(latest_table)
    assert item["worn_false_since"] == ts_at(10)
    assert item["latest_ts"] == ts_at(20)


def test_off_wrist_while_charging_still_counts_from_the_first_worn_false(
    latest_table,
):
    """Charging is checked by the watchdog, not here: since tracks worn only."""
    invoke(wearable_status(10, worn=False, charging=True))

    assert read_item(latest_table)["worn_false_since"] == ts_at(10)


def test_worn_status_removes_since(latest_table):
    invoke(wearable_status(10, worn=False))
    invoke(wearable_status(20, worn=True))

    assert "worn_false_since" not in read_item(latest_table)


def test_off_wrist_again_after_being_worn_starts_a_new_since(latest_table):
    invoke(wearable_status(10, worn=False))
    invoke(wearable_status(20, worn=True))
    invoke(wearable_status(30, worn=False))

    assert read_item(latest_table)["worn_false_since"] == ts_at(30)


def test_worn_status_never_sets_since(latest_table):
    invoke(wearable_status(10, worn=True))

    assert "worn_false_since" not in read_item(latest_table)


def test_late_off_wrist_status_that_is_not_the_newest_sets_nothing(latest_table):
    invoke(wearable_status(20, worn=True))
    invoke(wearable_status(10, worn=False))

    item = read_item(latest_table)
    assert "worn_false_since" not in item
    assert len(item["entries"]) == 2


def test_late_worn_status_that_is_not_the_newest_keeps_since(latest_table):
    invoke(wearable_status(20, worn=False))
    invoke(wearable_status(10, worn=True))

    assert read_item(latest_table)["worn_false_since"] == ts_at(20)


def test_duplicate_status_leaves_since_unchanged(latest_table):
    event = wearable_status(10, worn=False)
    invoke(event)
    before = read_item(latest_table)

    assert invoke(dict(event)) == {"outcome": "duplicate"}

    assert read_item(latest_table) == before


def test_bedroom_status_never_has_since(latest_table):
    invoke(rule_event("status", BEDROOM, ts=ts_at(10)))

    assert "worn_false_since" not in read_item(latest_table, BEDROOM)


def test_since_is_not_in_the_by_message_projection(latest_table):
    invoke(wearable_status(10, worn=False))

    resp = latest_table.query(
        TableName=TABLE_NAME,
        IndexName="by-message",
        KeyConditionExpression="watch_type = :t",
        ExpressionAttributeValues={":t": {"S": "status"}},
    )
    (item,) = resp["Items"]
    assert "worn_false_since" not in item
    assert "latest" in item
