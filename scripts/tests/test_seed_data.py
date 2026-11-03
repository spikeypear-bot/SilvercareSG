"""Seed file, validation, and registry item builders (docs/features/seed-data.md, 1-7).

Pure logic, no AWS. The committed seed file and infra/devices/devices.json are only read.
"""

from __future__ import annotations

import copy
import string
from datetime import UTC, datetime
from decimal import Decimal

import pytest

NOW = datetime(2026, 11, 1, 3, 0, 0, tzinfo=UTC)
TS = "2026-11-01T03:00:00Z"
LETTERS = string.ascii_uppercase[:20]


@pytest.fixture
def data(seed):
    return seed.load_seed()


@pytest.fixture
def devices(seed):
    return seed.load_devices()


def items_by_key(items):
    return {(i["pk"]["S"], i["sk"]["S"]): i for i in items}


# ---- criterion 1-2: the committed seed file ----


def test_committed_seed_file_is_valid(seed, data, devices):
    assert seed.validate_seed(data, devices) == []


def test_seed_file_has_20_residents_with_fixed_ids_and_homes(data):
    ids = [r["resident_id"] for r in data["residents"]]
    assert ids == [f"res-{n:04d}" for n in range(1, 21)]
    for n, resident in enumerate(data["residents"], start=1):
        assert resident["home_id"] == f"home-{n:04d}"


def test_each_resident_has_its_own_wearable_and_bedroom(data):
    assignments = {d["device_id"]: d["resident_id"] for d in data["devices"]}
    expected = {}
    for n in range(1, 21):
        expected[f"wearable-{n:04d}"] = f"res-{n:04d}"
        expected[f"bedroom-{n:04d}"] = f"res-{n:04d}"
    assert assignments == expected
    assert len(data["devices"]) == 40


def test_seed_devices_match_devices_json_exactly(data, devices):
    seeded = {(d["device_id"], d["device_type"]) for d in data["devices"]}
    registry = {(d["id"], d["type"]) for d in devices}
    assert seeded == registry


def test_seed_file_has_four_users_one_per_group_with_fixed_local_subs(data):
    users = {u["user_id"]: u for u in data["users"]}
    assert set(users) == {"test-doctor", "test-nurse", "test-caregiver", "test-family"}
    expected = {
        "test-doctor": ("test-doctor@example.com", "Test Doctor", "doctor", 1),
        "test-nurse": ("test-nurse@example.com", "Test Nurse", "nurse", 2),
        "test-caregiver": (
            "test-caregiver@example.com",
            "Test Caregiver",
            "caregiver",
            3,
        ),
        "test-family": ("test-family@example.com", "Test Family", "family", 4),
    }
    for user_id, (email, name, group, n) in expected.items():
        user = users[user_id]
        assert (user["email"], user["name"], user["group"]) == (email, name, group)
        assert user["local_sub"] == f"00000000-0000-4000-8000-{n:012d}"


def test_seed_file_has_no_password_key_anywhere(data):
    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    assert not [k for k in keys(data) if "password" in k.lower()]


def test_seed_file_values_are_obviously_synthetic(data):
    for n, resident in enumerate(data["residents"], start=1):
        letter = LETTERS[n - 1]
        assert resident["display_name"] == f"Test Resident {letter}"
        assert resident["medical_notes"].startswith("Synthetic note: ")
        assert len(resident["medical_notes"]) <= 2000
        assert 1935 <= resident["year_of_birth"] <= 1955
        assert type(resident["year_of_birth"]) is int
        [contact] = resident["emergency_contacts"]  # exactly 1
        assert contact["name"] == f"Test Contact {letter}1"
        assert contact["phone"] == f"+65 0000 00{n:02d}"
        assert isinstance(contact["relationship"], str) and contact["relationship"]
        assert "photo_key" not in resident
    for user in data["users"]:
        assert user["email"].endswith("@example.com")


def test_seed_file_links_match_the_spec(data):
    links = {(lk["user_id"], lk["resident_id"]) for lk in data["links"]}
    assert links == {
        ("test-family", "res-0002"),
        ("test-family", "res-0005"),
        ("test-caregiver", "res-0001"),
        ("test-caregiver", "res-0002"),
        ("test-caregiver", "res-0003"),
    }
    assert len(data["links"]) == 5


# ---- criterion 5: thresholds ----


def test_thresholds_use_defaults_except_the_three_exceptions(data):
    default = {
        "hr_bpm": {"min": 45, "max": 130},
        "spo2_pct": {"min": 88},
        "skin_temp_c": {"min": Decimal("30.0"), "max": Decimal("38.0")},
    }
    exceptions = {
        "res-0004": ("spo2_pct", "min", 85, "low normal SpO2"),
        "res-0007": ("hr_bpm", "max", 140, "high resting heart rate"),
        "res-0011": ("hr_bpm", "min", 40, "low resting heart rate"),
    }
    for resident in data["residents"]:
        expected = copy.deepcopy(default)
        rid = resident["resident_id"]
        if rid in exceptions:
            name, bound, value, reason = exceptions[rid]
            expected[name][bound] = value
            assert reason in resident["medical_notes"]
        assert resident["thresholds"] == expected, rid


def test_fractional_thresholds_load_as_decimal_not_float(data):
    temp = data["residents"][0]["thresholds"]["skin_temp_c"]
    assert type(temp["min"]) is Decimal and type(temp["max"]) is Decimal


# ---- criterion 3: validation ----


def problems_for(seed, data, devices):
    return seed.validate_seed(data, devices)


def test_missing_device_is_named(seed, data, devices):
    data["devices"] = [d for d in data["devices"] if d["device_id"] != "bedroom-0007"]
    problems = problems_for(seed, data, devices)
    assert any("bedroom-0007" in p and "not seeded" in p for p in problems)


def test_extra_device_is_named(seed, data, devices):
    data["devices"].append(
        {
            "device_id": "wearable-0099",
            "device_type": "wearable",
            "resident_id": "res-0001",
        }
    )
    problems = problems_for(seed, data, devices)
    assert any("wearable-0099" in p for p in problems)


def test_wrong_device_type_is_named(seed, data, devices):
    data["devices"][0]["device_type"] = "bedroom"
    problems = problems_for(seed, data, devices)
    assert any("wearable-0001" in p and "device_type" in p for p in problems)


def test_device_type_changed_in_devices_json_is_named(seed, data, devices):
    for entry in devices:
        if entry["id"] == "bedroom-0003":
            entry["type"] = "wearable"
    problems = problems_for(seed, data, devices)
    assert any("bedroom-0003" in p for p in problems)


def test_device_listed_twice_is_named(seed, data, devices):
    data["devices"].append(copy.deepcopy(data["devices"][0]))
    problems = problems_for(seed, data, devices)
    assert any("wearable-0001" in p and "more than once" in p for p in problems)


def test_duplicate_resident_id_is_named(seed, data, devices):
    data["residents"][4]["resident_id"] = "res-0004"
    problems = problems_for(seed, data, devices)
    assert any("res-0004" in p and "duplicate" in p for p in problems)


def test_link_to_unknown_resident_is_named(seed, data, devices):
    data["links"].append({"user_id": "test-family", "resident_id": "res-0999"})
    problems = problems_for(seed, data, devices)
    assert any("res-0999" in p and "unknown resident" in p for p in problems)


def test_link_to_unknown_user_is_named(seed, data, devices):
    data["links"].append({"user_id": "test-stranger", "resident_id": "res-0001"})
    problems = problems_for(seed, data, devices)
    assert any("test-stranger" in p and "unknown user" in p for p in problems)


def test_link_to_staff_user_is_refused(seed, data, devices):
    data["links"].append({"user_id": "test-doctor", "resident_id": "res-0001"})
    problems = problems_for(seed, data, devices)
    assert any("test-doctor" in p for p in problems)


def test_device_assigned_to_unknown_resident_is_named(seed, data, devices):
    data["devices"][0]["resident_id"] = "res-0999"
    problems = problems_for(seed, data, devices)
    assert any("wearable-0001" in p and "unknown resident" in p for p in problems)


@pytest.mark.parametrize(
    "place",
    ["top", "user", "resident", "contact", "case"],
)
def test_password_field_anywhere_is_refused(seed, data, devices, place):
    if place == "top":
        data["password"] = "x"
    elif place == "user":
        data["users"][1]["password"] = "x"
    elif place == "resident":
        data["residents"][2]["thresholds"]["hr_bpm"]["password"] = "x"
    elif place == "contact":
        data["residents"][3]["emergency_contacts"][0]["temp_password"] = "x"
    else:
        data["users"][0]["Password"] = "x"
    problems = problems_for(seed, data, devices)
    assert any("password" in p.lower() for p in problems)


def test_every_problem_is_reported_not_just_the_first(seed, data, devices):
    data["residents"][4]["resident_id"] = "res-0004"
    data["links"].append({"user_id": "test-stranger", "resident_id": "res-0999"})
    data["devices"] = data["devices"][1:]
    problems = problems_for(seed, data, devices)
    text = "\n".join(problems)
    for needle in ("res-0004", "test-stranger", "res-0999", "wearable-0001"):
        assert needle in text


def test_problem_messages_contain_ids_not_values(seed, data, devices):
    data["residents"][0]["medical_notes"] = "Not synthetic: secret value 123"
    data["residents"][0]["display_name"] = ""
    problems = problems_for(seed, data, devices)
    assert problems
    text = "\n".join(problems)
    assert "secret value" not in text
    assert "res-0001" in text


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["links"].append(
            {"user_id": ["test-family"], "resident_id": "res-0001"}
        ),
        lambda d: d["links"].append(
            {"user_id": "test-family", "resident_id": ["res-0001"]}
        ),
        lambda d: d["devices"][0].__setitem__("resident_id", {"id": "res-0001"}),
    ],
    ids=["link-user-list", "link-resident-list", "device-resident-dict"],
)
def test_unhashable_ids_are_validation_problems_not_crashes(
    seed, data, devices, mutate
):
    mutate(data)
    problems = problems_for(seed, data, devices)
    assert problems


def test_non_numeric_threshold_is_refused(seed, data, devices):
    data["residents"][0]["thresholds"]["hr_bpm"]["min"] = "45"
    assert any("res-0001" in p for p in problems_for(seed, data, devices))


def test_min_not_below_max_is_refused(seed, data, devices):
    data["residents"][0]["thresholds"]["hr_bpm"] = {"min": 130, "max": 130}
    assert any("res-0001" in p for p in problems_for(seed, data, devices))


def test_year_of_birth_outside_range_is_refused(seed, data, devices):
    data["residents"][0]["year_of_birth"] = 1956
    assert any("res-0001" in p for p in problems_for(seed, data, devices))


def test_notes_without_synthetic_prefix_are_refused(seed, data, devices):
    data["residents"][0]["medical_notes"] = "uses a walking frame"
    assert any("res-0001" in p for p in problems_for(seed, data, devices))


def test_notes_over_2000_characters_are_refused(seed, data, devices):
    data["residents"][0]["medical_notes"] = "Synthetic note: " + "x" * 1985
    assert any("res-0001" in p for p in problems_for(seed, data, devices))


def test_non_example_email_is_refused(seed, data, devices):
    data["users"][0]["email"] = "test-doctor@gmail.com"
    assert any("test-doctor" in p for p in problems_for(seed, data, devices))


def test_non_object_seed_is_refused(seed, devices):
    assert seed.validate_seed([], devices)


def test_load_seed_rejects_non_object(seed, tmp_path):
    path = tmp_path / "seed.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(TypeError):
        seed.load_seed(path)


# ---- criteria 4-7: registry items ----


@pytest.fixture
def items(seed, data):
    return items_by_key(seed.build_registry_items(data, seed.local_subs(data), NOW))


def test_builds_85_items(seed, data):
    assert len(seed.build_registry_items(data, seed.local_subs(data), NOW)) == 85


def test_profile_item_shape(items):
    item = items[("RESIDENT#res-0001", "PROFILE")]
    assert set(item) == {
        "pk",
        "sk",
        "entity_type",
        "resident_id",
        "display_name",
        "home_id",
        "year_of_birth",
        "medical_notes",
        "emergency_contacts",
        "created_at",
        "updated_at",
    }
    assert item["entity_type"] == {"S": "RESIDENT"}
    assert item["resident_id"] == {"S": "res-0001"}
    assert item["display_name"] == {"S": "Test Resident A"}
    assert item["home_id"] == {"S": "home-0001"}
    assert "N" in item["year_of_birth"]
    assert item["created_at"] == {"S": TS} and item["updated_at"] == {"S": TS}
    [contact] = item["emergency_contacts"]["L"]
    assert contact["M"]["name"] == {"S": "Test Contact A1"}
    assert contact["M"]["phone"] == {"S": "+65 0000 0001"}
    assert "photo_key" not in item


def test_last_profile_is_resident_t(items):
    item = items[("RESIDENT#res-0020", "PROFILE")]
    assert item["display_name"] == {"S": "Test Resident T"}
    assert item["emergency_contacts"]["L"][0]["M"]["phone"] == {"S": "+65 0000 0020"}


def test_thresholds_item_shape_and_numbers_are_n(items):
    item = items[("THRESHOLDS#res-0001", "THRESHOLDS")]
    assert set(item) == {
        "pk",
        "sk",
        "hr_bpm",
        "spo2_pct",
        "skin_temp_c",
        "updated_at",
        "updated_by",
    }
    assert item["hr_bpm"] == {"M": {"min": {"N": "45"}, "max": {"N": "130"}}}
    assert item["spo2_pct"] == {"M": {"min": {"N": "88"}}}
    assert item["skin_temp_c"] == {"M": {"min": {"N": "30.0"}, "max": {"N": "38.0"}}}
    assert item["updated_by"] == {"S": "seed"}
    assert "entity_type" not in item  # thresholds don't show up in by-type


def test_threshold_exceptions_are_in_items(items):
    assert items[("THRESHOLDS#res-0004", "THRESHOLDS")]["spo2_pct"]["M"]["min"] == {
        "N": "85"
    }
    assert items[("THRESHOLDS#res-0007", "THRESHOLDS")]["hr_bpm"]["M"]["max"] == {
        "N": "140"
    }
    assert items[("THRESHOLDS#res-0011", "THRESHOLDS")]["hr_bpm"]["M"]["min"] == {
        "N": "40"
    }


def test_device_item_shape(items):
    item = items[("DEVICE#bedroom-0002", "ASSIGNMENT")]
    assert item == {
        "pk": {"S": "DEVICE#bedroom-0002"},
        "sk": {"S": "ASSIGNMENT"},
        "entity_type": {"S": "DEVICE"},
        "device_type": {"S": "bedroom"},
        "resident_id": {"S": "res-0002"},
        "home_id": {"S": "home-0002"},
        "assigned_at": {"S": TS},
        "gsi1_pk": {"S": "RESIDENT#res-0002"},
        "gsi1_sk": {"S": "DEVICE#bedroom-0002"},
    }


def test_link_item_shape_uses_the_given_sub(seed, data):
    subs = {"test-family": "aaaaaaaa-0000-4000-8000-000000000000"}
    subs["test-caregiver"] = "bbbbbbbb-0000-4000-8000-000000000000"
    items = items_by_key(seed.build_registry_items(data, subs, NOW))
    item = items[("USER#aaaaaaaa-0000-4000-8000-000000000000", "RESIDENT#res-0005")]
    assert item == {
        "pk": {"S": "USER#aaaaaaaa-0000-4000-8000-000000000000"},
        "sk": {"S": "RESIDENT#res-0005"},
        "relationship": {"S": "family"},
        "linked_at": {"S": TS},
        "linked_by": {"S": "seed"},
        "gsi1_pk": {"S": "RESIDENT#res-0005"},
        "gsi1_sk": {"S": "USER#aaaaaaaa-0000-4000-8000-000000000000"},
    }
    caregiver = items[
        ("USER#bbbbbbbb-0000-4000-8000-000000000000", "RESIDENT#res-0003")
    ]
    assert caregiver["relationship"] == {"S": "caregiver"}


def test_local_subs_are_the_fixed_synthetic_subs(seed, data):
    assert seed.local_subs(data) == {
        "test-doctor": "00000000-0000-4000-8000-000000000001",
        "test-nurse": "00000000-0000-4000-8000-000000000002",
        "test-caregiver": "00000000-0000-4000-8000-000000000003",
        "test-family": "00000000-0000-4000-8000-000000000004",
    }


def test_no_item_contains_a_float(seed, data):
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                assert key != "N" or isinstance(child, str)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        else:
            assert not isinstance(value, float)

    walk(seed.build_registry_items(data, seed.local_subs(data), NOW))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("30.0"), {"N": "30.0"}),
        (Decimal("38.0"), {"N": "38.0"}),
        (45, {"N": "45"}),
        (True, {"BOOL": True}),
        ("x", {"S": "x"}),
    ],
)
def test_to_attribute(seed, value, expected):
    assert seed.to_attribute(value) == expected


def test_to_attribute_refuses_float(seed):
    with pytest.raises(TypeError):
        seed.to_attribute(30.0)


def test_timestamps_are_utc_iso_seconds_z(seed):
    sgt = datetime.fromisoformat("2026-11-01T11:00:00.123456+08:00")
    assert seed.format_ts(sgt) == TS
