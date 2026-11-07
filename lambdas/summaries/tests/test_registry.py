"""The device -> resident mapping (docs/features/curated-layer.md, criteria 6-7): the
pure validation in registry.py, and the handler's by-type Query and BatchGetItem
against moto."""

from __future__ import annotations

from datetime import UTC, datetime

import handler
import pytest
import registry
from conftest import REGISTRY_TABLE
from helpers import ASSIGNED_AT, DATE, put_device, put_resident, seed

# ---- registry.device_from_item (pure) ----


def item(**overrides) -> dict:
    """A deserialized ASSIGNMENT item, as handler.read_devices passes it."""
    base = {
        "pk": "DEVICE#wearable-0001",
        "resident_id": "res-0001",
        "device_type": "wearable",
        "assigned_at": ASSIGNED_AT,
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def test_valid_item_becomes_a_device():
    assert registry.device_from_item(item()) == registry.Device(
        device_id="wearable-0001",
        device_type="wearable",
        resident_id="res-0001",
        assigned_at=datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )


def test_bedroom_sensor_gets_the_bedroom_type():
    device = registry.device_from_item(
        item(pk="DEVICE#bedroom-0001", device_type="bedroom")
    )
    assert device.device_type == "bedroom"


def test_item_without_device_type_takes_the_id_prefix():
    device = registry.device_from_item(item(device_type=None))
    assert device.device_type == "wearable"


def test_assigned_at_with_microseconds_is_kept():
    device = registry.device_from_item(item(assigned_at="2026-10-01T03:00:00.123456Z"))
    assert device.assigned_at == datetime(2026, 10, 1, 3, 0, 0, 123456, tzinfo=UTC)


@pytest.mark.parametrize(
    "pk",
    [
        "DEVICE#wearable-001",
        "DEVICE#wearable-00001",
        "DEVICE#Wearable-0001",
        "DEVICE#sensor-0001",
        "DEVICE#wearable_0001",
        "DEVICE# wearable-0001",
        "DEVICE#wearable-0001 ",
        "DEVICE#wearable-0001\n",
        "DEVICE#wearable-\uff10\uff10\uff10\uff11",
        "DEVICE#wearable-0001'; DROP TABLE vitals --",
        "wearable-0001",
        "RESIDENT#res-0001",
        "",
        None,
        7,
    ],
    ids=repr,
)
def test_invalid_device_id_is_skipped(pk):
    # item() drops a None override, so None means no pk at all.
    assert registry.device_from_item(item(pk=pk)) is None


@pytest.mark.parametrize(
    "resident_id",
    [
        "res-001",
        "res-00001",
        "RES-0001",
        "res_0001",
        "res-0001 ",
        " res-0001",
        "res-0001\n",
        "unassigned",
        "Test Resident A",
        "res-0001' OR '1'='1",
        "res-\u0661\u0662\u0663\u0664",
        "",
        1,
    ],
    ids=repr,
)
def test_invalid_resident_id_is_skipped(resident_id):
    assert registry.device_from_item(item(resident_id=resident_id)) is None


def test_item_without_resident_id_is_skipped():
    # Unassigned since the index was read.
    record = item()
    del record["resident_id"]
    assert registry.device_from_item(record) is None


@pytest.mark.parametrize(
    "assigned_at",
    [
        "2026-10-01",
        "2026-10-01T03:00:00",
        "2026-10-01T03:00Z",
        "2026-10-01T03:00:00+08:00",
        "2026-10-01T03:00:00+00:00",
        "2026-10-01 03:00:00Z",
        "2026-10-01T03:00:00z",
        "2026-13-01T03:00:00Z",
        "2026-02-30T03:00:00Z",
        "2026-10-01T24:00:00Z",
        "2026-10-01T03:60:00Z",
        "2026-10-01T03:00:00.1234567Z",
        "2026-10-01T03:00:00.Z",
        "2026-10-01T03:00:00Z ",
        "2026-10-01T03:00:00Z' --",
        "",
        1759287600,
    ],
    ids=repr,
)
def test_invalid_assigned_at_is_skipped(assigned_at):
    assert registry.device_from_item(item(assigned_at=assigned_at)) is None


def test_missing_assigned_at_is_skipped():
    record = item()
    del record["assigned_at"]
    assert registry.device_from_item(record) is None


@pytest.mark.parametrize(
    "pk,device_type",
    [
        ("DEVICE#wearable-0001", "bedroom"),
        ("DEVICE#bedroom-0001", "wearable"),
        ("DEVICE#wearable-0001", "Wearable"),
        ("DEVICE#wearable-0001", ""),
    ],
)
def test_device_type_that_contradicts_the_id_is_skipped(pk, device_type):
    assert registry.device_from_item(item(pk=pk, device_type=device_type)) is None


# ---- registry.assigned_from_index (pure) ----


def test_index_keeps_assigned_devices_and_counts_spares():
    scan = registry.assigned_from_index(
        [
            {"pk": "DEVICE#wearable-0001", "resident_id": "res-0001"},
            {"pk": "DEVICE#wearable-0003"},
            {"pk": "DEVICE#bedroom-0001", "resident_id": "res-0001"},
        ]
    )
    assert scan.assigned == ["wearable-0001", "bedroom-0001"]
    assert scan.unassigned == 1
    assert scan.invalid == []


def test_index_item_with_invalid_device_id_is_logged_as_invalid_only():
    scan = registry.assigned_from_index(
        [
            {"pk": "DEVICE#hr 143 for res-0001", "resident_id": "res-0001"},
            {"pk": "RESIDENT#res-0001"},
            {},
        ]
    )
    assert scan.assigned == []
    assert scan.invalid == ["invalid", "invalid", "invalid"]


def test_index_lists_a_device_once():
    scan = registry.assigned_from_index(
        [
            {"pk": "DEVICE#wearable-0001", "resident_id": "res-0001"},
            {"pk": "DEVICE#wearable-0001", "resident_id": "res-0001"},
        ]
    )
    assert scan.assigned == ["wearable-0001"]


@pytest.mark.parametrize(
    "device_id,expected",
    [
        ("wearable-0001", "wearable-0001"),
        ("bedroom-0042", "bedroom-0042"),
        ("hr 143", "invalid"),
        (None, "invalid"),
    ],
)
def test_loggable_id_hides_malformed_ids(device_id, expected):
    assert registry.loggable_id(device_id) == expected


# ---- handler: the by-type Query (criterion 6) ----


def test_index_query_reads_devices_on_by_type(seeded_registry):
    handler.read_index()
    (query,) = [kw for op, kw in _dynamodb(seeded_registry) if op == "query"]
    assert query["TableName"] == REGISTRY_TABLE
    assert query["IndexName"] == "by-type"
    names = query["ExpressionAttributeNames"]
    condition = query["KeyConditionExpression"]
    for alias, name in names.items():
        condition = condition.replace(alias, name)
    assert condition == "entity_type = :device"
    assert query["ExpressionAttributeValues"] == {":device": {"S": "DEVICE"}}


def test_index_query_selects_and_projects_only_pk_and_resident_id(seeded_registry):
    # The role's dynamodb:Select and dynamodb:Attributes conditions (summaries.tf).
    handler.read_index()
    (query,) = [kw for op, kw in _dynamodb(seeded_registry) if op == "query"]
    assert query["Select"] == "SPECIFIC_ATTRIBUTES"
    names = query["ExpressionAttributeNames"]
    projected = {names[a.strip()] for a in query["ProjectionExpression"].split(",")}
    assert projected == {"pk", "resident_id"}


def test_index_query_is_eventually_consistent(seeded_registry):
    handler.read_index()
    for op, kw in _dynamodb(seeded_registry):
        if op == "query":
            assert not kw.get("ConsistentRead")


def test_index_query_follows_every_page(aws):
    for number in range(1, 8):
        put_device(aws.ddb, f"wearable-{number:04d}", resident_id="res-0001")
    aws.dynamodb.page_limit = 2
    scan = handler.read_index()
    queries = [kw for op, kw in _dynamodb(aws) if op == "query"]
    assert len(queries) >= 4
    assert "ExclusiveStartKey" not in queries[0]
    assert all("ExclusiveStartKey" in q for q in queries[1:])
    assert sorted(scan.assigned) == [f"wearable-{n:04d}" for n in range(1, 8)]


def test_index_query_skips_spares_and_ignores_residents(aws):
    seed(aws.ddb)
    put_resident(aws.ddb, "res-0002")
    scan = handler.read_index()
    assert sorted(scan.assigned) == ["bedroom-0001", "wearable-0001", "wearable-0002"]
    assert scan.unassigned == 1


# ---- handler: BatchGetItem of the ASSIGNMENT items (criteria 6-7) ----


def test_assignment_lookup_keys_and_projection(seeded_registry):
    handler.read_devices(["wearable-0001", "bedroom-0001"])
    (call,) = [kw for op, kw in _dynamodb(seeded_registry) if op == "batch_get_item"]
    request = call["RequestItems"][REGISTRY_TABLE]
    assert request["Keys"] == [
        {"pk": {"S": "DEVICE#wearable-0001"}, "sk": {"S": "ASSIGNMENT"}},
        {"pk": {"S": "DEVICE#bedroom-0001"}, "sk": {"S": "ASSIGNMENT"}},
    ]
    names = request["ExpressionAttributeNames"]
    projected = {names[a.strip()] for a in request["ProjectionExpression"].split(",")}
    assert projected == {"pk", "resident_id", "device_type", "assigned_at"}
    assert not request.get("ConsistentRead")


def test_assignment_lookup_returns_validated_devices_sorted(seeded_registry):
    devices = handler.read_devices(["wearable-0002", "wearable-0001", "bedroom-0001"])
    assert [(d.device_id, d.resident_id) for d in devices] == [
        ("bedroom-0001", "res-0001"),
        ("wearable-0001", "res-0001"),
        ("wearable-0002", "res-0002"),
    ]


def test_more_than_100_keys_are_split_into_calls_of_at_most_100(aws):
    keys = [
        {"pk": {"S": f"DEVICE#wearable-{n:04d}"}, "sk": {"S": "ASSIGNMENT"}}
        for n in range(250)
    ]
    handler.batch_get(keys, ["pk"])
    calls = [kw for op, kw in _dynamodb(aws) if op == "batch_get_item"]
    sizes = [len(c["RequestItems"][REGISTRY_TABLE]["Keys"]) for c in calls]
    assert sizes == [100, 100, 50]


def test_no_keys_make_no_call(aws):
    assert handler.batch_get([], ["pk"]) == []
    assert _dynamodb(aws) == []


def test_unprocessed_keys_are_retried_with_backoff(seeded_registry, sleeps):
    seeded_registry.dynamodb.unprocessed_rounds = 2
    devices = handler.read_devices(["wearable-0001", "wearable-0002"])
    assert [d.device_id for d in devices] == ["wearable-0001", "wearable-0002"]
    calls = [kw for op, kw in _dynamodb(seeded_registry) if op == "batch_get_item"]
    assert len(calls) == 3
    assert sleeps == [0.2, 0.4]
    # The retry asks again for the same keys, with the same projection.
    assert calls[1]["RequestItems"] == calls[0]["RequestItems"]


def test_unprocessed_keys_after_every_attempt_fail_the_run(seeded_registry, sleeps):
    seeded_registry.dynamodb.unprocessed_rounds = 99
    with pytest.raises(handler.UnprocessedKeysError) as error:
        handler.read_devices(["wearable-0001"])
    calls = [kw for op, kw in _dynamodb(seeded_registry) if op == "batch_get_item"]
    assert len(calls) == handler.BATCH_GET_ATTEMPTS == 6
    assert sleeps == [0.2, 0.4, 0.8, 1.6, 3.2]
    assert "wearable-0001" not in str(error.value)


def test_unprocessed_keys_that_never_clear_change_no_summaries(
    seeded_registry, athena, sleeps
):
    seeded_registry.s3.put_object(
        Bucket="silvercare-history-test",
        Key=f"summaries/date={DATE}/old.json",
        Body=b"{}",
    )
    seeded_registry.dynamodb.unprocessed_rounds = 99
    with pytest.raises(handler.UnprocessedKeysError):
        handler.lambda_handler({"date": DATE}, None)
    assert athena.queries == []
    assert seeded_registry.object_keys() == [f"summaries/date={DATE}/old.json"]


def test_unprocessed_keys_on_the_thresholds_read_are_retried(seeded_registry, sleeps):
    devices = handler.read_devices(["wearable-0002"])
    seeded_registry.dynamodb.unprocessed_rounds = 1
    limits = handler.read_limits(sorted({d.resident_id for d in devices}))
    assert int(limits["res-0002"]["hr_bpm"].max) == 113
    assert sleeps == [0.2]


# ---- handler: skipping and logging (criterion 7) ----


def test_device_listed_on_the_index_but_gone_is_skipped_and_logged(aws, logs):
    devices = handler.read_devices(["wearable-0009"])
    assert devices == []
    skipped = [r for r in logs() if r["message"] == "device skipped"]
    assert [r["device_id"] for r in skipped] == ["wearable-0009"]


def test_invalid_assignment_is_skipped_and_logged_by_device_id_only(aws, logs):
    put_device(aws.ddb, "wearable-0001", resident_id="Test Resident A")
    put_device(aws.ddb, "wearable-0002", resident_id="res-0002", assigned_at="soon")
    put_device(aws.ddb, "wearable-0004", resident_id="res-0004")
    devices = handler.read_devices(["wearable-0001", "wearable-0002", "wearable-0004"])
    assert [d.device_id for d in devices] == ["wearable-0004"]
    skipped = [r for r in logs() if r["message"] == "device skipped"]
    assert sorted(r["device_id"] for r in skipped) == ["wearable-0001", "wearable-0002"]
    for record in skipped:
        assert set(record) <= {"level", "message", "service", "device_id"}
    text = str(logs())
    assert "Test Resident A" not in text
    assert "soon" not in text
    assert "res-0002" not in text


def test_invalid_index_item_is_logged_as_invalid(aws, athena, logs):
    aws.ddb.put_item(
        TableName=REGISTRY_TABLE,
        Item={
            "pk": {"S": "DEVICE#hr 143 res-0001"},
            "sk": {"S": "ASSIGNMENT"},
            "entity_type": {"S": "DEVICE"},
            "resident_id": {"S": "res-0001"},
        },
    )
    put_device(aws.ddb, "wearable-0001", resident_id="res-0001")
    handler.lambda_handler({"date": DATE}, None)
    skipped = [r for r in logs() if r["message"] == "device skipped"]
    assert [r["device_id"] for r in skipped] == ["invalid"]
    assert "hr 143" not in str(logs())
    assert "'res-0001'" in athena.queries[0]
    assert "hr 143" not in athena.queries[0]


def test_skipped_device_is_not_in_the_query(aws, athena):
    put_device(aws.ddb, "wearable-0001", resident_id="res-0001")
    put_device(aws.ddb, "wearable-0002", resident_id="res-0002", assigned_at="soon")
    handler.lambda_handler({"date": DATE}, None)
    assert "'wearable-0002'" not in athena.queries[0]
    assert "'res-0002'" not in athena.queries[0]


def test_spare_device_is_not_looked_up(seeded_registry, athena):
    handler.lambda_handler({"date": DATE}, None)
    looked_up = [
        key["pk"]["S"]
        for op, kw in _dynamodb(seeded_registry)
        if op == "batch_get_item"
        for key in kw["RequestItems"][REGISTRY_TABLE]["Keys"]
    ]
    assert "DEVICE#wearable-0003" not in looked_up
    assert "'wearable-0003'" not in athena.queries[0]


def test_registry_reads_only_device_and_threshold_partitions(seeded_registry, athena):
    # What the role's dynamodb:LeadingKeys condition allows (infra/summaries.tf).
    handler.lambda_handler({"date": DATE}, None)
    pks = [
        key["pk"]["S"]
        for op, kw in _dynamodb(seeded_registry)
        if op == "batch_get_item"
        for key in kw["RequestItems"][REGISTRY_TABLE]["Keys"]
    ]
    assert pks
    assert all(pk.startswith(("DEVICE#", "THRESHOLDS#")) for pk in pks)
    ops = {op for op, _ in _dynamodb(seeded_registry)}
    assert ops == {"query", "batch_get_item"}


# ---- helpers ----


@pytest.fixture
def seeded_registry(aws):
    seed(aws.ddb)
    return aws


def _dynamodb(aws) -> list[tuple[str, dict]]:
    return [(op, kw) for svc, op, kw in aws.calls if svc == "dynamodb"]
