"""Synthetic home items written by seed.py (docs/features/alerts-map.md, criterion 21).

Item building and validation are pure logic; writes and --delete run against moto
(conftest's ddb fixture). No real AWS.
"""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

TABLE = "silvercare-registry"
NOW = datetime(2026, 11, 1, 3, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 2, 4, 5, 6, tzinfo=UTC)
TS = "2026-11-01T03:00:00Z"
HOME_IDS = [f"home-{n:04d}" for n in range(1, 21)]
# Criterion 2: what api-dashboard accepts as a valid home.
VALID_LAT = (Decimal("1.16"), Decimal("1.48"))
VALID_LNG = (Decimal("103.6"), Decimal("104.1"))
HAND_ADDRESS = "Test address hand-edited zq (synthetic)"


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


def homes_of(items):
    return {i["home_id"]["S"]: i for i in items if i["pk"]["S"].startswith("HOME#")}


@pytest.fixture
def data(seed):
    return seed.load_seed()


@pytest.fixture
def homes(seed, data):
    return homes_of(seed.build_registry_items(data, seed.local_subs(data), NOW))


@pytest.fixture
def seeded(seed, ddb, data):
    seed.seed_local(ddb, table=TABLE, seed=data, now=NOW, sleep=no_sleep)
    return ddb


# ---- item building ----


def test_one_home_item_per_seeded_home(homes, data):
    assert sorted(homes) == HOME_IDS == [r["home_id"] for r in data["residents"]]


def test_home_item_has_exactly_the_spec_fields(homes):
    item = homes["home-0003"]
    assert set(item) == {
        "pk",
        "sk",
        "home_id",
        "address",
        "lat",
        "lng",
        "created_at",
        "updated_at",
    }
    assert item["pk"] == {"S": "HOME#home-0003"}
    assert item["sk"] == {"S": "PROFILE"}
    assert item["created_at"] == item["updated_at"] == {"S": TS}


def test_home_item_has_no_entity_type_so_it_is_in_no_index(homes):
    for item in homes.values():
        assert not {"entity_type", "gsi1_pk", "gsi1_sk"} & set(item)


def test_home_address_is_the_obviously_synthetic_one(homes):
    for n, home_id in enumerate(HOME_IDS, start=1):
        assert homes[home_id]["address"] == {"S": f"Test address {n} (synthetic)"}


def test_home_coordinates_are_numbers_not_strings_or_floats(homes):
    for item in homes.values():
        assert set(item["lat"]) == {"N"} and set(item["lng"]) == {"N"}


def test_every_home_position_is_its_table_entry(seed, homes):
    for n, home_id in enumerate(HOME_IDS, start=1):
        item = homes[home_id]
        assert (Decimal(item["lat"]["N"]), Decimal(item["lng"]["N"])) == (
            seed.HOME_POSITIONS[n]
        )


def test_home_bounds_are_the_dashboard_validity_bounds(seed):
    assert seed.HOME_LAT_BOUNDS == VALID_LAT
    assert seed.HOME_LNG_BOUNDS == VALID_LNG


def test_every_home_passes_the_dashboard_validity_rule(homes):
    for item in homes.values():
        assert 1 <= len(item["address"]["S"]) <= 200
        assert VALID_LAT[0] <= Decimal(item["lat"]["N"]) <= VALID_LAT[1]
        assert VALID_LNG[0] <= Decimal(item["lng"]["N"]) <= VALID_LNG[1]


def test_every_home_has_its_own_position(homes):
    positions = {(i["lat"]["N"], i["lng"]["N"]) for i in homes.values()}
    assert len(positions) == 20


def test_position_table_has_twenty_distinct_points_for_homes_1_to_20(seed):
    assert sorted(seed.HOME_POSITIONS) == list(range(1, 21))
    assert len(set(seed.HOME_POSITIONS.values())) == 20


def test_every_table_position_is_inside_the_singapore_bounds(seed):
    for lat, lng in seed.HOME_POSITIONS.values():
        assert isinstance(lat, Decimal) and isinstance(lng, Decimal)
        assert VALID_LAT[0] <= lat <= VALID_LAT[1]
        assert VALID_LNG[0] <= lng <= VALID_LNG[1]


@pytest.mark.parametrize("home_id", ["home-1", "HOME-0001", "home-0001 ", "res-0001"])
def test_home_values_refuses_a_malformed_home_id(seed, home_id):
    with pytest.raises(ValueError):
        seed.home_values(home_id)


def test_seed_file_has_no_home_position_problem(seed, data):
    problems = seed.validate_seed(data, seed.load_devices())
    assert not any("HOME_POSITIONS" in p or "Singapore bounds" in p for p in problems)


def test_home_missing_from_the_table_is_a_validation_problem(seed, data, monkeypatch):
    positions = {n: p for n, p in seed.HOME_POSITIONS.items() if n != 20}
    monkeypatch.setattr(seed, "HOME_POSITIONS", positions)
    problems = seed.validate_seed(data, seed.load_devices())
    assert any(
        "res-0020" in p and "home has no position in HOME_POSITIONS" in p
        for p in problems
    )
    assert not any("res-0001" in p for p in problems)


def test_home_values_refuses_a_home_missing_from_the_table(seed, monkeypatch):
    positions = {n: p for n, p in seed.HOME_POSITIONS.items() if n != 20}
    monkeypatch.setattr(seed, "HOME_POSITIONS", positions)
    with pytest.raises(ValueError):
        seed.home_values("home-0020")


@pytest.mark.parametrize(
    "position",
    [
        (Decimal("1.15"), Decimal("103.8")),  # south of the lat bound
        (Decimal("1.49"), Decimal("103.8")),  # north of the lat bound
        (Decimal("1.35"), Decimal("103.59")),  # west of the lng bound
        (Decimal("1.35"), Decimal("104.11")),  # east of the lng bound
    ],
)
def test_home_outside_the_singapore_bounds_is_a_validation_problem(
    seed, data, monkeypatch, position
):
    monkeypatch.setattr(seed, "HOME_POSITIONS", {**seed.HOME_POSITIONS, 20: position})
    problems = seed.validate_seed(data, seed.load_devices())
    assert any(
        "res-0020" in p and "home position is outside the Singapore bounds" in p
        for p in problems
    )
    assert not any("res-0001" in p for p in problems)
    assert not any(re.search(r"1\.\d|10[34]\.\d", p) for p in problems)


def test_home_on_the_singapore_bounds_is_valid(seed, data, monkeypatch):
    corner = (VALID_LAT[1], VALID_LNG[1])
    monkeypatch.setattr(seed, "HOME_POSITIONS", {**seed.HOME_POSITIONS, 20: corner})
    problems = seed.validate_seed(data, seed.load_devices())
    assert not any("Singapore bounds" in p for p in problems)


# ---- writes and re-runs ----


def test_seed_writes_every_home_item(seeded):
    assert sorted(homes_of(scan(seeded))) == HOME_IDS


def test_rerun_leaves_home_items_identical_except_timestamps(seed, seeded, data):
    def stripped(items):
        return {
            h: {k: v for k, v in i.items() if k not in {"created_at", "updated_at"}}
            for h, i in homes_of(items).items()
        }

    first = stripped(scan(seeded))
    seed.seed_local(seeded, table=TABLE, seed=data, now=LATER, sleep=no_sleep)
    assert stripped(scan(seeded)) == first


# ---- --delete ----


def edit_home(client, home_id, address=HAND_ADDRESS):
    client.update_item(
        TableName=TABLE,
        Key={"pk": {"S": f"HOME#{home_id}"}, "sk": {"S": "PROFILE"}},
        UpdateExpression="SET address = :a",
        ExpressionAttributeValues={":a": {"S": address}},
    )


def test_delete_removes_every_untouched_home(seed, seeded, data):
    kept = []
    seed.delete_seed(seeded, data, table=TABLE, sleep=no_sleep, kept_homes=kept)
    assert kept == []
    assert homes_of(scan(seeded)) == {}


def test_delete_keeps_a_hand_edited_home(seed, seeded, data):
    edit_home(seeded, "home-0003")
    kept = []
    links, others = seed.delete_seed(
        seeded, data, table=TABLE, sleep=no_sleep, kept_homes=kept
    )
    assert kept == ["home-0003"]
    assert (links, others) == (3, 99)
    (item,) = scan(seeded)
    assert item["pk"] == {"S": "HOME#home-0003"}
    assert item["address"] == {"S": HAND_ADDRESS}


def test_delete_without_kept_homes_list_still_keeps_a_hand_edited_home(
    seed, seeded, data
):
    edit_home(seeded, "home-0005")
    seed.delete_seed(seeded, data, table=TABLE, sleep=no_sleep)
    assert sorted(homes_of(scan(seeded))) == ["home-0005"]


def test_delete_home_counts_an_already_gone_home_as_deleted(seed, ddb):
    assert seed.delete_home(ddb, "home-0007", table=TABLE) is True


def test_delete_home_raises_errors_other_than_a_failed_condition(seed):
    class Throttled:
        def delete_item(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "ProvisionedThroughputExceededException"}},
                "DeleteItem",
            )

    with pytest.raises(ClientError):
        seed.delete_home(Throttled(), "home-0001", table=TABLE)


def test_delete_home_is_conditional_on_the_synthetic_address(seed):
    calls = []

    class Recording:
        def delete_item(self, **kwargs):
            calls.append(kwargs)

    seed.delete_home(Recording(), "home-0004", table=TABLE)
    (call,) = calls
    assert call["Key"] == {"pk": {"S": "HOME#home-0004"}, "sk": {"S": "PROFILE"}}
    assert "address = :address" in call["ConditionExpression"]
    assert call["ExpressionAttributeValues"] == {
        ":address": {"S": "Test address 4 (synthetic)"}
    }


def test_delete_output_names_kept_home_ids_never_the_address(seed, seeded):
    edit_home(seeded, "home-0002")
    out, err = io.StringIO(), io.StringIO()
    code = seed.main(
        ["--local", "--delete"],
        client_factory=lambda service, local=False: seeded,
        out=out,
        err=err,
        sleep=no_sleep,
        now=NOW,
    )
    assert code == 0, err.getvalue()
    text = out.getvalue() + err.getvalue()
    assert "registry: 1 homes kept" in text and "home-0002" in text
    assert HAND_ADDRESS not in text and "hand-edited" not in text
