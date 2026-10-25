"""Write rule (docs/data-model.md, "Write rule (idempotent)"), criteria 16, 17, 20.

merge_entries and build_item are tested directly. add_message runs against moto, with
a recording/fault-injecting wrapper around the client for races and AWS errors.
"""

from __future__ import annotations

import boto3
import latest_store
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from conftest import TABLE_NAME
from events import ts_at
from latest_store import (
    DUPLICATE,
    STORED,
    TOO_OLD,
    WriteConflictError,
    add_message,
    build_item,
    merge_entries,
)


def entry(n: int, minute: int | None = None) -> dict:
    """A minimal entry with a synthetic id; minute defaults to n."""
    return {
        "message_id": f"00000000-0000-4000-8000-{n:012d}",
        "ts": ts_at(n if minute is None else minute),
        "received_at": ts_at(n if minute is None else minute, 0, 410),
        "clock_skew": False,
        "motion": True,
    }


def ids(entries) -> list[int]:
    return [int(e["message_id"].rsplit("-", 1)[1]) for e in entries]


# ---- merge_entries (pure) ----


def test_first_entry_is_stored():
    outcome, entries = merge_entries([], entry(1))

    assert outcome == STORED
    assert ids(entries) == [1]


def test_same_message_id_is_duplicate():
    existing = [entry(3), entry(2)]

    outcome, entries = merge_entries(existing, entry(2))

    assert outcome == DUPLICATE
    assert entries is None


def test_duplicate_detected_by_message_id_even_with_other_ts():
    existing = [entry(3)]
    again = entry(3, minute=40)

    assert merge_entries(existing, again) == (DUPLICATE, None)


def test_entries_sorted_newest_first():
    existing = [entry(5), entry(1)]

    _, entries = merge_entries(existing, entry(3))

    assert ids(entries) == [5, 3, 1]


def test_sixth_message_drops_the_oldest():
    existing = [entry(n) for n in (5, 4, 3, 2, 1)]

    outcome, entries = merge_entries(existing, entry(6))

    assert outcome == STORED
    assert ids(entries) == [6, 5, 4, 3, 2]


def test_out_of_order_message_newer_than_some_is_inserted():
    existing = [entry(n) for n in (10, 8, 6, 4, 2)]

    outcome, entries = merge_entries(existing, entry(5))

    assert outcome == STORED
    assert ids(entries) == [10, 8, 6, 5, 4]


def test_message_older_than_all_five_is_too_old():
    existing = [entry(n) for n in (10, 8, 6, 4, 2)]

    assert merge_entries(existing, entry(1)) == (TOO_OLD, None)


def test_older_message_is_stored_when_fewer_than_five():
    existing = [entry(n) for n in (10, 8, 6, 4)]

    outcome, entries = merge_entries(existing, entry(1))

    assert outcome == STORED
    assert ids(entries) == [10, 8, 6, 4, 1]


def test_merge_does_not_mutate_existing_list():
    existing = [entry(2), entry(1)]

    merge_entries(existing, entry(3))

    assert ids(existing) == [2, 1]


# ---- build_item (pure) ----


@pytest.mark.parametrize("message", ["vitals", "status", "motion", "bed"])
def test_watch_type_is_set_on_watched_types(message):
    item = build_item("wearable-0001", "wearable", message, [entry(1)], 1)

    assert item["watch_type"] == message


def test_fall_item_has_no_watch_type():
    item = build_item("wearable-0001", "wearable", "fall", [entry(1)], 1)

    assert "watch_type" not in item


def test_item_has_exactly_the_data_model_attributes():
    entries = [entry(2), entry(1)]

    item = build_item("bedroom-0001", "bedroom", "motion", entries, 7)

    assert item == {
        "pk": "DEVICE#bedroom-0001",
        "sk": "MSG#motion",
        "device_type": "bedroom",
        "entries": entries,
        "latest": entries[0],
        "latest_ts": entries[0]["ts"],
        "version": 7,
        "watch_type": "motion",
    }


# ---- add_message against moto ----


class Recorder:
    """Wraps a DynamoDB client: records calls, can run hooks before put_item."""

    def __init__(self, client, before_put=None):
        self.client = client
        self.calls: list[tuple[str, dict]] = []
        self.before_put = before_put
        self.put_count = 0

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        return self.client.get_item(**kwargs)

    def put_item(self, **kwargs):
        self.calls.append(("put_item", kwargs))
        self.put_count += 1
        if self.before_put:
            self.before_put(self.put_count)
        return self.client.put_item(**kwargs)


def add(client, e, device_id="bedroom-0001", message="motion"):
    return add_message(client, TABLE_NAME, device_id, "bedroom", message, e)


def read_item(client, device_id="bedroom-0001", message="motion"):
    from boto3.dynamodb.types import TypeDeserializer

    resp = client.get_item(
        TableName=TABLE_NAME,
        Key={"pk": {"S": f"DEVICE#{device_id}"}, "sk": {"S": f"MSG#{message}"}},
        ConsistentRead=True,
    )
    if "Item" not in resp:
        return None
    d = TypeDeserializer()
    return {k: d.deserialize(v) for k, v in resp["Item"].items()}


def test_first_write_creates_item_with_version_1(latest_table):
    assert add(latest_table, entry(1)) == STORED

    item = read_item(latest_table)
    assert item["version"] == 1
    assert ids(item["entries"]) == [1]


def test_each_stored_write_increments_version(latest_table):
    for n in (1, 2, 3):
        add(latest_table, entry(n))

    assert read_item(latest_table)["version"] == 3


def test_get_item_is_strongly_consistent(latest_table):
    rec = Recorder(latest_table)

    add(rec, entry(1))

    get_calls = [kw for name, kw in rec.calls if name == "get_item"]
    assert get_calls and all(kw["ConsistentRead"] is True for kw in get_calls)


def test_put_item_is_conditioned_on_read_version(latest_table):
    add(latest_table, entry(1))
    rec = Recorder(latest_table)

    add(rec, entry(2))

    (put,) = [kw for name, kw in rec.calls if name == "put_item"]
    assert put["ConditionExpression"] == (
        "attribute_not_exists(pk) OR version = :read_version"
    )
    assert put["ExpressionAttributeValues"] == {":read_version": {"N": "1"}}
    assert put["Item"]["version"] == {"N": "2"}


def test_duplicate_writes_nothing(latest_table):
    add(latest_table, entry(1))
    rec = Recorder(latest_table)

    assert add(rec, entry(1)) == DUPLICATE

    assert [name for name, _ in rec.calls] == ["get_item"]
    assert read_item(latest_table)["version"] == 1


def test_too_old_writes_nothing(latest_table):
    for n in (10, 8, 6, 4, 2):
        add(latest_table, entry(n))
    rec = Recorder(latest_table)

    assert add(rec, entry(1)) == TOO_OLD

    assert [name for name, _ in rec.calls] == ["get_item"]
    item = read_item(latest_table)
    assert ids(item["entries"]) == [10, 8, 6, 4, 2]
    assert item["version"] == 5


def test_lost_race_is_retried_and_both_messages_are_stored(latest_table):
    def other_writer_wins(put_number):
        if put_number == 1:
            # Another invocation stores its message between our GetItem and PutItem.
            assert add(latest_table, entry(2)) == STORED

    rec = Recorder(latest_table, before_put=other_writer_wins)

    assert add(rec, entry(1)) == STORED

    item = read_item(latest_table)
    assert ids(item["entries"]) == [2, 1]
    assert item["version"] == 2
    assert [name for name, _ in rec.calls] == [
        "get_item",
        "put_item",
        "get_item",
        "put_item",
    ]


def test_retry_that_finds_a_duplicate_stops(latest_table):
    def same_message_lands_first(put_number):
        if put_number == 1:
            add(latest_table, entry(1))

    rec = Recorder(latest_table, before_put=same_message_lands_first)

    assert add(rec, entry(1)) == DUPLICATE

    assert ids(read_item(latest_table)["entries"]) == [1]


def test_three_lost_races_raise(latest_table):
    counter = iter(range(100, 200))

    def other_writer_always_wins(put_number):
        add(latest_table, entry(next(counter)))

    rec = Recorder(latest_table, before_put=other_writer_always_wins)

    with pytest.raises(WriteConflictError):
        add(rec, entry(1))

    assert [name for name, _ in rec.calls].count("get_item") == 3
    assert [name for name, _ in rec.calls].count("put_item") == 3
    stored = ids(read_item(latest_table)["entries"])
    assert 1 not in stored


def test_max_attempts_is_three():
    assert latest_store.MAX_ATTEMPTS == 3


def _stubbed_client():
    client = boto3.client(
        "dynamodb",
        region_name="ap-southeast-1",
        config=Config(retries={"max_attempts": 1, "mode": "standard"}),
    )
    return client, Stubber(client)


@pytest.mark.parametrize(
    "code",
    [
        "ProvisionedThroughputExceededException",
        "ThrottlingException",
        "InternalServerError",
        "RequestLimitExceeded",
    ],
)
def test_transient_error_on_get_is_raised(code):
    client, stub = _stubbed_client()
    stub.add_client_error("get_item", service_error_code=code, http_status_code=400)

    with stub, pytest.raises(ClientError) as exc:
        add(client, entry(1))

    assert exc.value.response["Error"]["Code"] == code


def test_throttled_put_is_raised_not_retried_as_conflict():
    client, stub = _stubbed_client()
    stub.add_response("get_item", {})
    stub.add_client_error(
        "put_item",
        service_error_code="ProvisionedThroughputExceededException",
        http_status_code=400,
    )

    with stub, pytest.raises(ClientError) as exc:
        add(client, entry(1))

    assert exc.value.response["Error"]["Code"] == (
        "ProvisionedThroughputExceededException"
    )
    stub.assert_no_pending_responses()
