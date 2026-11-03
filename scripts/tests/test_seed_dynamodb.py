"""Registry writes, queries, re-runs, delete, pacing (criteria 8, 15, 16, 19, 21).

DynamoDB is moto (conftest's ddb fixture). No real AWS.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError
from helpers import REPO_ROOT

TABLE = "silvercare-registry"
NOW = datetime(2026, 11, 1, 3, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 2, 4, 5, 6, tzinfo=UTC)
TIMESTAMP_FIELDS = {"created_at", "updated_at", "assigned_at", "linked_at"}
FAMILY_SUB = "00000000-0000-4000-8000-000000000004"
CAREGIVER_SUB = "00000000-0000-4000-8000-000000000003"

FOREIGN_ITEMS = [
    {
        "pk": {"S": "RESIDENT#res-9001"},
        "sk": {"S": "PROFILE"},
        "entity_type": {"S": "RESIDENT"},
        "display_name": {"S": "Hand Made Resident"},
    },
    {
        "pk": {"S": "THRESHOLDS#res-9001"},
        "sk": {"S": "THRESHOLDS"},
        "updated_by": {"S": "handmade-user"},
    },
    {
        "pk": {"S": "DEVICE#wearable-9001"},
        "sk": {"S": "ASSIGNMENT"},
        "entity_type": {"S": "DEVICE"},
        "gsi1_pk": {"S": "RESIDENT#res-9001"},
        "gsi1_sk": {"S": "DEVICE#wearable-9001"},
    },
    {"pk": {"S": "USER#handmade-sub"}, "sk": {"S": "PROFILE"}},
    # A link to a seeded resident made by someone else: must survive --delete.
    {
        "pk": {"S": "USER#handmade-sub"},
        "sk": {"S": "RESIDENT#res-0002"},
        "relationship": {"S": "family"},
        "linked_by": {"S": "admin"},
        "gsi1_pk": {"S": "RESIDENT#res-0002"},
        "gsi1_sk": {"S": "USER#handmade-sub"},
    },
    # A link to a seeded resident with no linked_by at all.
    {
        "pk": {"S": "USER#other-sub"},
        "sk": {"S": "RESIDENT#res-0001"},
        "relationship": {"S": "caregiver"},
        "gsi1_pk": {"S": "RESIDENT#res-0001"},
        "gsi1_sk": {"S": "USER#other-sub"},
    },
]


def no_sleep(seconds: float) -> None:
    pass


def scan(client, table=TABLE):
    items, kwargs = [], {"TableName": table, "ConsistentRead": True}
    while True:
        response = client.scan(**kwargs)
        items.extend(response["Items"])
        if "LastEvaluatedKey" not in response:
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def keyed(items):
    return {(i["pk"]["S"], i["sk"]["S"]): i for i in items}


def without_timestamps(items):
    return {
        key: {k: v for k, v in item.items() if k not in TIMESTAMP_FIELDS}
        for key, item in keyed(items).items()
    }


def query(client, index, name, value, prefix=None, sk_name=None):
    expr = "#k = :v"
    names = {"#k": name}
    values = {":v": {"S": value}}
    if prefix:
        expr += " AND begins_with(#s, :p)"
        names["#s"] = sk_name
        values[":p"] = {"S": prefix}
    kwargs = {
        "TableName": TABLE,
        "KeyConditionExpression": expr,
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
    }
    if index:
        kwargs["IndexName"] = index
    return client.query(**kwargs)["Items"]


@pytest.fixture
def data(seed):
    return seed.load_seed()


@pytest.fixture
def seeded(seed, ddb, data):
    count = seed.seed_local(ddb, table=TABLE, seed=data, now=NOW, sleep=no_sleep)
    assert count == 85
    return ddb


def put_foreign(client):
    for item in FOREIGN_ITEMS:
        client.put_item(TableName=TABLE, Item=item)


def seed_links(client):
    return [i for i in scan(client) if i.get("linked_by", {}).get("S") == "seed"]


# ---- criterion 8: query results after a full seed ----


def test_pattern_4_by_type_device_returns_40(seeded):
    assert len(query(seeded, "by-type", "entity_type", "DEVICE")) == 40


def test_pattern_8_by_type_resident_returns_20(seeded):
    items = query(seeded, "by-type", "entity_type", "RESIDENT")
    assert sorted(i["pk"]["S"] for i in items) == [
        f"RESIDENT#res-{n:04d}" for n in range(1, 21)
    ]


def test_pattern_10_devices_of_res_0002(seeded):
    items = query(
        seeded, "by-resident", "gsi1_pk", "RESIDENT#res-0002", "DEVICE#", "gsi1_sk"
    )
    assert sorted(i["gsi1_sk"]["S"] for i in items) == [
        "DEVICE#bedroom-0002",
        "DEVICE#wearable-0002",
    ]


def test_pattern_14_family_users_residents(seeded):
    items = query(seeded, None, "pk", f"USER#{FAMILY_SUB}", "RESIDENT#", "sk")
    assert sorted(i["sk"]["S"] for i in items) == [
        "RESIDENT#res-0002",
        "RESIDENT#res-0005",
    ]


def test_pattern_14_caregiver_users_residents(seeded):
    items = query(seeded, None, "pk", f"USER#{CAREGIVER_SUB}", "RESIDENT#", "sk")
    assert sorted(i["sk"]["S"] for i in items) == [
        "RESIDENT#res-0001",
        "RESIDENT#res-0002",
        "RESIDENT#res-0003",
    ]


def test_pattern_15_users_of_res_0002(seeded):
    items = query(
        seeded, "by-resident", "gsi1_pk", "RESIDENT#res-0002", "USER#", "gsi1_sk"
    )
    assert sorted(i["gsi1_sk"]["S"] for i in items) == [
        f"USER#{CAREGIVER_SUB}",
        f"USER#{FAMILY_SUB}",
    ]
    assert {i["relationship"]["S"] for i in items} == {"caregiver", "family"}


def test_thresholds_are_stored_as_numbers(seeded):
    item = seeded.get_item(
        TableName=TABLE,
        Key={"pk": {"S": "THRESHOLDS#res-0001"}, "sk": {"S": "THRESHOLDS"}},
    )["Item"]
    temp = item["skin_temp_c"]["M"]
    assert set(temp["min"]) == {"N"} and Decimal(temp["min"]["N"]) == Decimal(30)
    assert set(temp["max"]) == {"N"} and Decimal(temp["max"]["N"]) == Decimal(38)
    assert set(item["hr_bpm"]["M"]["min"]) == {"N"}


# ---- criterion 15: re-runs ----


def test_rerun_is_identical_except_timestamps(seed, seeded, data):
    first = scan(seeded)
    seed.seed_local(seeded, table=TABLE, seed=data, now=LATER, sleep=no_sleep)
    second = scan(seeded)
    assert len(second) == 85
    assert without_timestamps(first) == without_timestamps(second)
    profile = keyed(second)[("RESIDENT#res-0001", "PROFILE")]
    assert profile["updated_at"] == {"S": "2026-11-02T04:05:06Z"}


def test_rerun_resets_hand_edited_seed_items(seed, seeded, data):
    key = {"pk": {"S": "RESIDENT#res-0001"}, "sk": {"S": "PROFILE"}}
    seeded.update_item(
        TableName=TABLE,
        Key=key,
        UpdateExpression="SET display_name = :n, photo_key = :p",
        ExpressionAttributeValues={":n": {"S": "Edited"}, ":p": {"S": "photo.jpg"}},
    )
    seed.seed_local(seeded, table=TABLE, seed=data, now=NOW, sleep=no_sleep)
    item = seeded.get_item(TableName=TABLE, Key=key)["Item"]
    assert item["display_name"] == {"S": "Test Resident A"}
    assert "photo_key" not in item


def test_seed_never_touches_foreign_items(seed, ddb, data):
    put_foreign(ddb)
    seed.seed_local(ddb, table=TABLE, seed=data, now=NOW, sleep=no_sleep)
    seed.seed_local(ddb, table=TABLE, seed=data, now=LATER, sleep=no_sleep)
    stored = keyed(scan(ddb))
    for item in FOREIGN_ITEMS:
        assert stored[(item["pk"]["S"], item["sk"]["S"])] == item
    assert len(stored) == 85 + len(FOREIGN_ITEMS)


# ---- device ownership: never move a device assigned outside the seed ----


def put_device(client, device_id, resident_id=None):
    item = {
        "pk": {"S": f"DEVICE#{device_id}"},
        "sk": {"S": "ASSIGNMENT"},
        "entity_type": {"S": "DEVICE"},
        "device_type": {"S": device_id.split("-")[0]},
    }
    if resident_id is not None:
        item["resident_id"] = {"S": resident_id}
    client.put_item(TableName=TABLE, Item=item)


def device_resident(client, device_id):
    item = client.get_item(
        TableName=TABLE,
        Key={"pk": {"S": f"DEVICE#{device_id}"}, "sk": {"S": "ASSIGNMENT"}},
    )["Item"]
    return item.get("resident_id", {}).get("S")


def test_seed_assigns_unassigned_spares(seed, ddb, data):
    # Spares as scripts/seed-registry.py writes them: no resident_id.
    seed.ensure_table(ddb, TABLE, sleep=no_sleep)
    put_device(ddb, "wearable-0004")
    put_device(ddb, "bedroom-0020")
    assert seed.foreign_devices(ddb, data, table=TABLE, sleep=no_sleep) == []
    assert seed.seed_local(ddb, table=TABLE, seed=data, now=NOW, sleep=no_sleep) == 85
    assert device_resident(ddb, "wearable-0004") == "res-0004"
    assert device_resident(ddb, "bedroom-0020") == "res-0020"


def test_seed_refuses_device_assigned_to_another_resident(seed, ddb, data):
    seed.ensure_table(ddb, TABLE, sleep=no_sleep)
    put_device(ddb, "wearable-0004", "res-9001")
    put_device(ddb, "bedroom-0005")  # unassigned: not a reason to refuse
    with pytest.raises(seed.SeedError, match="wearable-0004") as raised:
        seed.seed_local(ddb, table=TABLE, seed=data, now=NOW, sleep=no_sleep)
    assert "bedroom-0005" not in str(raised.value)
    assert len(scan(ddb)) == 2  # nothing written
    assert device_resident(ddb, "wearable-0004") == "res-9001"


def test_delete_keeps_device_assigned_to_another_resident(seed, seeded, data):
    put_device(seeded, "wearable-0004", "res-9001")
    skipped = []
    seed.delete_seed(seeded, data, table=TABLE, sleep=no_sleep, skipped=skipped)
    assert skipped == ["wearable-0004"]
    assert keyed(scan(seeded)).keys() == {("DEVICE#wearable-0004", "ASSIGNMENT")}


def test_delete_removes_unassigned_seed_device(seed, seeded, data):
    put_device(seeded, "wearable-0004")
    skipped = []
    seed.delete_seed(seeded, data, table=TABLE, sleep=no_sleep, skipped=skipped)
    assert skipped == []
    assert scan(seeded) == []


# ---- criterion 16: delete ----


def test_delete_removes_everything_the_seed_owns(seed, seeded, data):
    links, others = seed.delete_seed(seeded, data, table=TABLE, sleep=no_sleep)
    assert (links, others) == (5, 80)
    assert scan(seeded) == []


def test_delete_leaves_foreign_items_and_links(seed, seeded, data):
    put_foreign(seeded)
    seed.delete_seed(seeded, data, table=TABLE, sleep=no_sleep)
    stored = keyed(scan(seeded))
    assert stored == {(i["pk"]["S"], i["sk"]["S"]): i for i in FOREIGN_ITEMS}


def test_delete_on_partly_deleted_seed_succeeds(seed, seeded, data):
    for key in seed.owned_keys(data)[::2]:
        seeded.delete_item(TableName=TABLE, Key=key)
    seeded.delete_item(
        TableName=TABLE,
        Key={"pk": {"S": f"USER#{FAMILY_SUB}"}, "sk": {"S": "RESIDENT#res-0002"}},
    )
    links, _ = seed.delete_seed(seeded, data, table=TABLE, sleep=no_sleep)
    assert links == 4
    assert scan(seeded) == []


def test_delete_twice_succeeds(seed, seeded, data):
    seed.delete_seed(seeded, data, table=TABLE, sleep=no_sleep)
    assert seed.delete_seed(seeded, data, table=TABLE, sleep=no_sleep) == (0, 80)
    assert scan(seeded) == []


def test_delete_finds_seed_links_of_any_sub(seed, seeded, data):
    # A seed link of a user deleted by hand (sub not known to Cognito any more).
    seeded.put_item(
        TableName=TABLE,
        Item={
            "pk": {"S": "USER#dead0000-0000-4000-8000-000000000000"},
            "sk": {"S": "RESIDENT#res-0003"},
            "linked_by": {"S": "seed"},
            "relationship": {"S": "caregiver"},
            "gsi1_pk": {"S": "RESIDENT#res-0003"},
            "gsi1_sk": {"S": "USER#dead0000-0000-4000-8000-000000000000"},
        },
    )
    links, _ = seed.delete_seed(seeded, data, table=TABLE, sleep=no_sleep)
    assert links == 6
    assert seed_links(seeded) == []


def test_find_seed_links_reads_linked_by(seed, seeded, data):
    put_foreign(seeded)
    ids = [r["resident_id"] for r in data["residents"]]
    found = seed.find_seed_links(seeded, ids, table=TABLE, sleep=no_sleep)
    assert len(found) == 5
    assert all(k["pk"]["S"].startswith("USER#00000000-") for k in found)


def test_delete_does_not_touch_other_tables(seed, seeded, data):
    for name in ("silvercare-latest", "silvercare-alerts"):
        seeded.create_table(
            TableName=name,
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        seeded.put_item(
            TableName=name,
            Item={"pk": {"S": "DEVICE#wearable-0001"}, "sk": {"S": "MSG#vitals"}},
        )
    seed.delete_seed(seeded, data, table=TABLE, sleep=no_sleep)
    for name in ("silvercare-latest", "silvercare-alerts"):
        assert len(scan(seeded, name)) == 1


# ---- criterion 19: ensure_table matches infra/dynamodb.tf ----


def registry_block() -> str:
    text = (REPO_ROOT / "infra" / "dynamodb.tf").read_text(encoding="utf-8")
    start = text.index('module "registry_table"')
    return text[start : text.index('module "', start + 10)]


def test_table_definition_matches_terraform(seed):
    block = registry_block()
    definition = seed.REGISTRY_TABLE_DEFINITION
    top = block.split("global_secondary_indexes")[0]
    assert re.search(r'hash_key\s*=\s*"pk"', top)
    assert re.search(r'range_key\s*=\s*"sk"', top)
    rcu = int(re.search(r"read_capacity\s*=\s*(\d+)", top).group(1))
    wcu = int(re.search(r"write_capacity\s*=\s*(\d+)", top).group(1))
    assert definition["ProvisionedThroughput"] == {
        "ReadCapacityUnits": rcu,
        "WriteCapacityUnits": wcu,
    }
    assert definition["KeySchema"] == [
        {"AttributeName": "pk", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"},
    ]
    tf_indexes = {}
    for chunk in block.split("{")[2:]:
        name = re.search(r'name\s*=\s*"([^"]+)"', chunk)
        if not name:
            continue
        tf_indexes[name.group(1)] = {
            "hash": re.search(r'hash_key\s*=\s*"([^"]+)"', chunk).group(1),
            "range": re.search(r'range_key\s*=\s*"([^"]+)"', chunk).group(1),
            "attrs": re.findall(
                r'"([^"]+)"',
                re.search(r"non_key_attributes\s*=\s*\[([^\]]*)\]", chunk).group(1),
            ),
            "rcu": int(re.search(r"read_capacity\s*=\s*(\d+)", chunk).group(1)),
            "wcu": int(re.search(r"write_capacity\s*=\s*(\d+)", chunk).group(1)),
        }
    assert set(tf_indexes) == {"by-type", "by-resident"}
    py_indexes = {g["IndexName"]: g for g in definition["GlobalSecondaryIndexes"]}
    assert set(py_indexes) == set(tf_indexes)
    for name, tf in tf_indexes.items():
        gsi = py_indexes[name]
        assert gsi["KeySchema"] == [
            {"AttributeName": tf["hash"], "KeyType": "HASH"},
            {"AttributeName": tf["range"], "KeyType": "RANGE"},
        ]
        assert gsi["Projection"]["ProjectionType"] == "INCLUDE"
        assert sorted(gsi["Projection"]["NonKeyAttributes"]) == sorted(tf["attrs"])
        assert gsi["ProvisionedThroughput"] == {
            "ReadCapacityUnits": tf["rcu"],
            "WriteCapacityUnits": tf["wcu"],
        }


def test_ensure_table_creates_missing_table(seed, moto):
    from conftest import boto_client

    client = boto_client("dynamodb")
    assert seed.ensure_table(client, "registry-test", sleep=no_sleep) is True
    table = client.describe_table(TableName="registry-test")["Table"]
    assert table["KeySchema"] == seed.REGISTRY_TABLE_DEFINITION["KeySchema"]
    assert {g["IndexName"] for g in table["GlobalSecondaryIndexes"]} == {
        "by-type",
        "by-resident",
    }
    assert table["ProvisionedThroughput"]["WriteCapacityUnits"] == 1


def test_ensure_table_keeps_existing_table(seed, seeded):
    assert seed.ensure_table(seeded, TABLE, sleep=no_sleep) is False
    assert len(scan(seeded)) == 85


def test_seed_local_creates_table_and_writes(seed, moto, data):
    from conftest import boto_client

    client = boto_client("dynamodb")
    assert (
        seed.seed_local(client, table="fresh", seed=data, now=NOW, sleep=no_sleep) == 85
    )
    assert len(scan(client, "fresh")) == 85


def test_seed_local_default_seed_is_the_committed_file(seed, moto):
    from conftest import boto_client

    client = boto_client("dynamodb")
    assert seed.seed_local(client, table="fresh", sleep=no_sleep) == 85


# ---- criterion 21: batching and retries (fake client, no moto) ----


def throttle(operation="BatchWriteItem"):
    return ClientError(
        {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "slow",
            }
        },
        operation,
    )


class FakeBatchClient:
    """batch_write_item that follows a script of outcomes per call.

    Each outcome is "ok", "throttle", "error", or an int: how many of the requests to
    leave unprocessed.
    """

    def __init__(self, script):
        self.script = list(script)
        self.sizes = []

    def batch_write_item(self, RequestItems):
        [(table, requests)] = RequestItems.items()
        self.sizes.append(len(requests))
        outcome = self.script.pop(0) if self.script else "ok"
        if outcome == "throttle":
            raise throttle()
        if outcome == "error":
            raise ClientError(
                {"Error": {"Code": "ValidationException", "Message": "bad"}},
                "BatchWriteItem",
            )
        if outcome == "ok" or outcome == 0:
            return {"UnprocessedItems": {}}
        return {"UnprocessedItems": {table: requests[-outcome:]}}


def requests(n):
    return [{"PutRequest": {"Item": {"pk": {"S": f"p{i}"}}}} for i in range(n)]


def test_batch_write_uses_at_most_25_per_call(seed):
    client = FakeBatchClient([])
    assert seed.batch_write(client, TABLE, requests(85), sleep=no_sleep) == 85
    assert client.sizes == [25, 25, 25, 10]


def test_batch_write_retries_unprocessed_items(seed):
    client = FakeBatchClient([10, 3])
    sleeps = []
    assert seed.batch_write(client, TABLE, requests(25), sleep=sleeps.append) == 25
    assert client.sizes == [25, 10, 3]
    assert len(sleeps) == 2


def test_batch_write_retries_throttled_calls(seed):
    client = FakeBatchClient(["throttle", "throttle", "ok"])
    assert seed.batch_write(client, TABLE, requests(5), sleep=no_sleep) == 5
    assert client.sizes == [5, 5, 5]


def test_batch_write_gives_up_after_retries_without_progress(seed):
    client = FakeBatchClient(["throttle"] * 100)
    with pytest.raises(seed.SeedError) as exc:
        seed.batch_write(
            client, TABLE, requests(5), sleep=no_sleep, max_attempts_without_progress=4
        )
    assert len(client.sizes) == 4
    assert "5" in str(exc.value)


def test_batch_write_default_gives_up_only_after_several_retries(seed):
    client = FakeBatchClient(["throttle"] * 100)
    with pytest.raises(seed.SeedError):
        seed.batch_write(client, TABLE, requests(1), sleep=no_sleep)
    assert len(client.sizes) >= 5


def test_batch_write_progress_resets_the_give_up_count(seed):
    # 24 unprocessed then 23 ... each call makes progress: never gives up.
    client = FakeBatchClient(list(range(24, 0, -1)))
    assert (
        seed.batch_write(
            client, TABLE, requests(25), sleep=no_sleep, max_attempts_without_progress=2
        )
        == 25
    )
    assert len(client.sizes) == 25


def test_batch_write_stall_counter_resets_after_progress(seed):
    script = ["throttle", "throttle", 2, "throttle", "throttle", "ok"]
    client = FakeBatchClient(script)
    assert (
        seed.batch_write(
            client, TABLE, requests(5), sleep=no_sleep, max_attempts_without_progress=3
        )
        == 5
    )


def test_batch_write_raises_non_throttle_errors_at_once(seed):
    client = FakeBatchClient(["error"])
    with pytest.raises(ClientError):
        seed.batch_write(client, TABLE, requests(3), sleep=no_sleep)
    assert client.sizes == [3]


def test_batch_write_empty_makes_no_call(seed):
    client = FakeBatchClient([])
    assert seed.batch_write(client, TABLE, [], sleep=no_sleep) == 0
    assert client.sizes == []


def test_batch_write_backoff_is_jittered_and_grows(seed):
    client = FakeBatchClient(["throttle"] * 5)
    sleeps = []
    seed.batch_write(client, TABLE, requests(1), sleep=sleeps.append, rng=lambda: 1.0)
    assert sleeps == sorted(sleeps)
    assert sleeps[0] < sleeps[-1]
    assert all(s <= seed.BACKOFF_CAP_S for s in sleeps)


def test_backoff_delay_is_full_jitter_and_capped(seed):
    assert seed.backoff_delay(1, rng=lambda: 0.0) == 0.0
    assert seed.backoff_delay(1, rng=lambda: 1.0) == seed.BACKOFF_BASE_S
    assert seed.backoff_delay(50, rng=lambda: 1.0) == seed.BACKOFF_CAP_S


class FakeGetClient:
    def __init__(self, script):
        self.script = list(script)
        self.sizes = []

    def batch_get_item(self, RequestItems):
        [(table, request)] = RequestItems.items()
        keys = request["Keys"]
        self.sizes.append(len(keys))
        outcome = self.script.pop(0) if self.script else 0
        if outcome == "throttle":
            raise throttle("BatchGetItem")
        done = keys[: len(keys) - outcome] if outcome else keys
        rest = keys[len(keys) - outcome :] if outcome else []
        response = {"Responses": {table: [dict(k) for k in done]}}
        if rest:
            response["UnprocessedKeys"] = {table: {"Keys": rest}}
        return response


def gkeys(n):
    return [
        {"pk": {"S": f"USER#{i}"}, "sk": {"S": "RESIDENT#res-0001"}} for i in range(n)
    ]


def test_batch_get_uses_at_most_100_keys_and_retries_unprocessed(seed):
    client = FakeGetClient([0, 30, "throttle", 0])
    found = seed.batch_get(client, TABLE, gkeys(150), ["pk", "sk"], sleep=no_sleep)
    assert len(found) == 150
    assert client.sizes == [100, 50, 30, 30]


def test_batch_get_gives_up_without_progress(seed):
    client = FakeGetClient(["throttle"] * 50)
    with pytest.raises(seed.SeedError):
        seed.batch_get(
            client,
            TABLE,
            gkeys(2),
            ["pk"],
            sleep=no_sleep,
            max_attempts_without_progress=3,
        )
