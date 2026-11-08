"""Registry writes, queries, re-runs, clean-up, delete, pacing (criteria 8, 15, 15a,
16, 19, 21).

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
# Fields set to the run time. Not assigned_at: it is fixed on every run
# (docs/features/seed-data.md, criterion 6), so a re-run must keep it unchanged.
TIMESTAMP_FIELDS = {"created_at", "updated_at", "linked_at"}
ASSIGNED_AT = "2026-01-31T16:00:00Z"
# The local sub the removed test-family user had before 2026-11-04.
OLD_FAMILY_SUB = "00000000-0000-4000-8000-000000000004"
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
    assert count == 103
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


def test_pattern_14_old_family_sub_has_no_links(seeded):
    assert query(seeded, None, "pk", f"USER#{OLD_FAMILY_SUB}", "RESIDENT#", "sk") == []


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
    assert [i["gsi1_sk"]["S"] for i in items] == [f"USER#{CAREGIVER_SUB}"]
    assert [i["relationship"]["S"] for i in items] == ["caregiver"]


def test_only_the_caregiver_is_linked(seeded):
    links = sorted(
        (i["pk"]["S"], i["sk"]["S"], i["relationship"]["S"]) for i in seed_links(seeded)
    )
    assert links == [
        (f"USER#{CAREGIVER_SUB}", f"RESIDENT#res-000{n}", "caregiver")
        for n in (1, 2, 3)
    ]


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
    assert len(second) == 103
    assert without_timestamps(first) == without_timestamps(second)
    profile = keyed(second)[("RESIDENT#res-0001", "PROFILE")]
    assert profile["updated_at"] == {"S": "2026-11-02T04:05:06Z"}


def test_rerun_keeps_the_fixed_assigned_at(seed, seeded, data):
    seed.seed_local(seeded, table=TABLE, seed=data, now=LATER, sleep=no_sleep)
    assignments = [i for i in scan(seeded) if i["sk"]["S"] == "ASSIGNMENT"]
    assert len(assignments) == 40
    assert {i["assigned_at"]["S"] for i in assignments} == {ASSIGNED_AT}


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
    assert len(stored) == 103 + len(FOREIGN_ITEMS)


# ---- criterion 15a: clean-up of seed links the seed file no longer has ----


def link_item(sub, resident_id, linked_by="seed", relationship="family"):
    item = {
        "pk": {"S": f"USER#{sub}"},
        "sk": {"S": f"RESIDENT#{resident_id}"},
        "relationship": {"S": relationship},
        "linked_at": {"S": "2026-10-30T00:00:00Z"},
        "gsi1_pk": {"S": f"RESIDENT#{resident_id}"},
        "gsi1_sk": {"S": f"USER#{sub}"},
    }
    if linked_by is not None:
        item["linked_by"] = {"S": linked_by}
    return item


# The links the seed wrote for test-family before 2026-11-04 (live subs differ from
# the local one, so one of each).
OLD_FAMILY_LINKS = [
    link_item(OLD_FAMILY_SUB, "res-0002"),
    link_item(OLD_FAMILY_SUB, "res-0005"),
    link_item("fa000000-0000-4000-8000-000000000000", "res-0005"),
]


def put_items(client, items):
    for item in items:
        client.put_item(TableName=TABLE, Item=item)


def link_set(client):
    return {
        (i["pk"]["S"], i["sk"]["S"])
        for i in scan(client)
        if i["pk"]["S"].startswith("USER#") and i["sk"]["S"].startswith("RESIDENT#")
    }


def key_of(item):
    return (item["pk"]["S"], item["sk"]["S"])


def test_seed_removes_old_family_seed_links(seed, ddb, data):
    seed.ensure_table(ddb, TABLE, sleep=no_sleep)
    put_items(ddb, OLD_FAMILY_LINKS)
    seed.seed_local(ddb, table=TABLE, seed=data, now=NOW, sleep=no_sleep)
    assert link_set(ddb) == {
        (f"USER#{CAREGIVER_SUB}", f"RESIDENT#res-000{n}") for n in (1, 2, 3)
    }
    assert len(scan(ddb)) == 103


def test_seed_local_count_does_not_include_removed_links(seed, ddb, data):
    seed.ensure_table(ddb, TABLE, sleep=no_sleep)
    put_items(ddb, OLD_FAMILY_LINKS)
    assert seed.seed_local(ddb, table=TABLE, seed=data, now=NOW, sleep=no_sleep) == 103


def test_prune_returns_the_number_of_old_seed_links_removed(seed, seeded, data):
    put_items(seeded, OLD_FAMILY_LINKS)
    items = seed.build_registry_items(data, seed.local_subs(data), LATER)
    seed.write_registry(seeded, items, table=TABLE, sleep=no_sleep)
    assert seed.prune_seed_links(seeded, data, items, table=TABLE, sleep=no_sleep) == 3
    assert not link_set(seeded) & {key_of(i) for i in OLD_FAMILY_LINKS}


def test_prune_keeps_links_with_another_linked_by(seed, seeded, data):
    kept = [
        link_item(OLD_FAMILY_SUB, "res-0006", linked_by="admin"),
        link_item("ab000000-0000-4000-8000-000000000000", "res-0002", linked_by=""),
        link_item("ac000000-0000-4000-8000-000000000000", "res-0003", linked_by="Seed"),
        link_item("ad000000-0000-4000-8000-000000000000", "res-0004", linked_by=None),
    ]
    put_items(seeded, kept + OLD_FAMILY_LINKS)
    seed.seed_local(seeded, table=TABLE, seed=data, now=LATER, sleep=no_sleep)
    stored = keyed(scan(seeded))
    for item in kept:
        assert stored[key_of(item)] == item
    for item in OLD_FAMILY_LINKS:
        assert key_of(item) not in stored


def test_prune_keeps_seed_links_to_residents_outside_the_seed(seed, seeded, data):
    outside = link_item(OLD_FAMILY_SUB, "res-9001")
    put_items(seeded, [outside])
    seed.seed_local(seeded, table=TABLE, seed=data, now=LATER, sleep=no_sleep)
    assert keyed(scan(seeded))[key_of(outside)] == outside


def test_prune_keeps_foreign_items(seed, seeded, data):
    put_foreign(seeded)
    put_items(seeded, OLD_FAMILY_LINKS)
    seed.seed_local(seeded, table=TABLE, seed=data, now=LATER, sleep=no_sleep)
    stored = keyed(scan(seeded))
    for item in FOREIGN_ITEMS:
        assert stored[key_of(item)] == item
    assert len(stored) == 103 + len(FOREIGN_ITEMS)


def test_rerun_prunes_nothing(seed, seeded, data):
    items = seed.build_registry_items(data, seed.local_subs(data), LATER)
    seed.write_registry(seeded, items, table=TABLE, sleep=no_sleep)
    assert seed.prune_seed_links(seeded, data, items, table=TABLE, sleep=no_sleep) == 0
    assert len(seed_links(seeded)) == 3


def test_link_removed_from_seed_file_is_pruned_exactly(seed, seeded, data):
    put_foreign(seeded)
    before = keyed(scan(seeded))
    data["links"] = [lk for lk in data["links"] if lk["resident_id"] != "res-0002"]
    seed.seed_local(seeded, table=TABLE, seed=data, now=NOW, sleep=no_sleep)
    after = keyed(scan(seeded))
    removed = (f"USER#{CAREGIVER_SUB}", "RESIDENT#res-0002")
    assert set(before) - set(after) == {removed}
    assert set(after) == set(before) - {removed}
    for key in after:
        assert after[key] == before[key]  # same NOW: everything else unchanged


def test_prune_with_no_stale_links_makes_no_write(seed, seeded, data):
    from conftest import Recorder

    recorder = Recorder(seeded)
    items = seed.build_registry_items(data, seed.local_subs(data), LATER)
    seed.prune_seed_links(recorder, data, items, table=TABLE, sleep=no_sleep)
    assert "batch_write_item" not in recorder.ops()
    assert "delete_item" not in recorder.ops()


def test_prune_runs_after_the_new_links_are_written(seed, ddb, data):
    from conftest import Recorder

    seed.ensure_table(ddb, TABLE, sleep=no_sleep)
    put_items(ddb, OLD_FAMILY_LINKS)
    recorder = Recorder(ddb)
    seed.seed_local(recorder, table=TABLE, seed=data, now=NOW, sleep=no_sleep)
    ops = recorder.ops()
    puts = [
        request
        for op, kw in recorder.calls
        if op == "batch_write_item"
        for request in kw["RequestItems"][TABLE]
    ]
    assert all(set(request) == {"PutRequest"} for request in puts)
    assert len(puts) == 103
    deletes = [i for i, op in enumerate(ops) if op == "delete_item"]
    assert len(deletes) == 3
    last_write = max(i for i, op in enumerate(ops) if op == "batch_write_item")
    assert last_write < min(deletes)


def test_each_prune_delete_is_conditional_on_linked_by_seed(seed, seeded, data):
    from conftest import Recorder

    put_items(seeded, OLD_FAMILY_LINKS)
    recorder = Recorder(seeded)
    items = seed.build_registry_items(data, seed.local_subs(data), LATER)
    seed.prune_seed_links(recorder, data, items, table=TABLE, sleep=no_sleep)
    deletes = [kw for op, kw in recorder.calls if op == "delete_item"]
    assert sorted((kw["Key"]["pk"]["S"], kw["Key"]["sk"]["S"]) for kw in deletes) == (
        sorted(key_of(i) for i in OLD_FAMILY_LINKS)
    )
    for kw in deletes:
        assert kw["TableName"] == TABLE
        assert kw["ConditionExpression"] == "linked_by = :seed"
        assert kw["ExpressionAttributeValues"] == {":seed": {"S": "seed"}}


def test_link_relinked_by_someone_else_before_delete_is_kept(
    seed, seeded, data, monkeypatch
):
    # Found as a seed link, then re-made by an admin before the conditional delete.
    old, relinked = OLD_FAMILY_LINKS[0], OLD_FAMILY_LINKS[1]
    put_items(seeded, [old, relinked])
    real_find = seed.find_seed_links

    def find_then_relink(client, resident_ids, **kwargs):
        found = real_find(client, resident_ids, **kwargs)
        assert {(k["pk"]["S"], k["sk"]["S"]) for k in found} >= {
            key_of(old),
            key_of(relinked),
        }
        put_items(seeded, [{**relinked, "linked_by": {"S": "admin"}}])
        return found

    monkeypatch.setattr(seed, "find_seed_links", find_then_relink)
    items = seed.build_registry_items(data, seed.local_subs(data), LATER)
    removed = seed.prune_seed_links(seeded, data, items, table=TABLE, sleep=no_sleep)
    assert removed == 1
    stored = keyed(scan(seeded))
    assert key_of(old) not in stored
    assert stored[key_of(relinked)]["linked_by"] == {"S": "admin"}


def test_link_gone_before_delete_is_not_counted(seed, seeded, data, monkeypatch):
    gone = OLD_FAMILY_LINKS[0]
    put_items(seeded, [gone])
    real_find = seed.find_seed_links

    def find_then_vanish(client, resident_ids, **kwargs):
        found = real_find(client, resident_ids, **kwargs)
        seeded.delete_item(TableName=TABLE, Key={"pk": gone["pk"], "sk": gone["sk"]})
        return found

    monkeypatch.setattr(seed, "find_seed_links", find_then_vanish)
    items = seed.build_registry_items(data, seed.local_subs(data), LATER)
    assert seed.prune_seed_links(seeded, data, items, table=TABLE, sleep=no_sleep) == 0
    assert key_of(gone) not in keyed(scan(seeded))  # nothing re-created


def test_prune_raises_other_delete_errors(seed, seeded, data):
    from conftest import Recorder

    put_items(seeded, OLD_FAMILY_LINKS)
    recorder = Recorder(
        seeded, fail={"delete_item": (1, "ProvisionedThroughputExceededException", "x")}
    )
    items = seed.build_registry_items(data, seed.local_subs(data), LATER)
    with pytest.raises(ClientError):
        seed.prune_seed_links(recorder, data, items, table=TABLE, sleep=no_sleep)


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
    assert seed.seed_local(ddb, table=TABLE, seed=data, now=NOW, sleep=no_sleep) == 103
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
    assert (links, others) == (3, 100)
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
        Key={"pk": {"S": f"USER#{CAREGIVER_SUB}"}, "sk": {"S": "RESIDENT#res-0002"}},
    )
    links, _ = seed.delete_seed(seeded, data, table=TABLE, sleep=no_sleep)
    assert links == 2
    assert scan(seeded) == []


def test_delete_twice_succeeds(seed, seeded, data):
    seed.delete_seed(seeded, data, table=TABLE, sleep=no_sleep)
    assert seed.delete_seed(seeded, data, table=TABLE, sleep=no_sleep) == (0, 100)
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
    assert links == 4
    assert seed_links(seeded) == []


def test_find_seed_links_reads_linked_by(seed, seeded, data):
    put_foreign(seeded)
    ids = [r["resident_id"] for r in data["residents"]]
    found = seed.find_seed_links(seeded, ids, table=TABLE, sleep=no_sleep)
    assert len(found) == 3
    assert {k["pk"]["S"] for k in found} == {f"USER#{CAREGIVER_SUB}"}


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
    assert len(scan(seeded)) == 103


def test_seed_local_creates_table_and_writes(seed, moto, data):
    from conftest import boto_client

    client = boto_client("dynamodb")
    assert (
        seed.seed_local(client, table="fresh", seed=data, now=NOW, sleep=no_sleep)
        == 103
    )
    assert len(scan(client, "fresh")) == 103


def test_seed_local_default_seed_is_the_committed_file(seed, moto):
    from conftest import boto_client

    client = boto_client("dynamodb")
    assert seed.seed_local(client, table="fresh", sleep=no_sleep) == 103


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
