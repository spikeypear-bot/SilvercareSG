"""fleet.py without AWS: parsing, the online rule, and building the response."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import fleet
import pytest
from events import (
    BEDROOM,
    HOME_A,
    NAME_A,
    NOW,
    RESIDENT_A,
    RESIDENT_B,
    WEARABLE,
    WEARABLE_2,
    ago,
    iso,
    status_entry,
)


def device_item(device_id: str = WEARABLE, **fields):
    item = {
        "pk": f"DEVICE#{device_id}",
        "device_type": device_id.split("-", 1)[0],
        "resident_id": RESIDENT_A,
        "home_id": HOME_A,
    }
    item.update(fields)
    return {k: v for k, v in item.items() if v is not None}


def status_item(device_id: str = WEARABLE, **fields):
    return {"pk": f"DEVICE#{device_id}", "latest": status_entry(device_id, **fields)}


def resident_item(resident_id: str = RESIDENT_A, name=NAME_A):
    return {"pk": f"RESIDENT#{resident_id}", "display_name": name}


def status_at(ts: datetime) -> fleet.Status:
    parsed = fleet.status_from_item(status_item(ts=iso(ts)))
    assert parsed is not None
    return parsed[1]


# ---- parse_ts ----


def test_parse_ts_reads_iso_ms_z():
    assert fleet.parse_ts("2026-11-04T03:59:00.123Z") == datetime(
        2026, 11, 4, 3, 59, 0, 123000, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "value",
    [
        "2026-11-04T03:59:00Z",
        "2026-11-04T03:59:00.12Z",
        "2026-11-04T03:59:00.1234Z",
        "2026-02-30T03:59:00.000Z",
        "2026-11-04T25:00:00.000Z",
        "2026-11-04T03:59:00.000Z\n",
        " 2026-11-04T03:59:00.000Z",
        "",
        None,
        1730692740,
    ],
)
def test_parse_ts_rejects_anything_else(value):
    assert fleet.parse_ts(value) is None


# ---- online ----


def test_exactly_15_minutes_is_online():
    assert fleet.is_online(status_at(NOW - timedelta(minutes=15)), NOW) is True


def test_15_minutes_and_1_ms_is_offline():
    status = status_at(NOW - timedelta(minutes=15, milliseconds=1))

    assert fleet.is_online(status, NOW) is False


def test_14_minutes_59_999_seconds_is_online():
    status = status_at(NOW - timedelta(minutes=14, seconds=59, milliseconds=999))

    assert fleet.is_online(status, NOW) is True


def test_no_status_is_offline():
    assert fleet.is_online(None, NOW) is False


# ---- battery and fw_version ----


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal(0), 0),
        (Decimal(100), 100),
        (Decimal(63), 63),
        (Decimal("63.0"), 63),
        (63, 63),
        (Decimal(-1), None),
        (Decimal(101), None),
        (Decimal("50.5"), None),
        (True, None),
        (False, None),
        ("63", None),
        (63.0, None),
        (None, None),
    ],
)
def test_battery_is_an_integer_from_0_to_100(value, expected):
    parsed = fleet.status_from_item(status_item(battery_pct=value))

    assert parsed is not None
    assert parsed[1].battery_pct == expected
    assert type(parsed[1].battery_pct) is type(expected)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.0.0", "1.0.0"),
        ("v" * 32, "v" * 32),
        ("v" * 33, None),
        ("1.0\u0000", None),
        ("1.0\u200b", None),
        ("1.0\u202e", None),
        ("1.0\t", None),
        ("", None),
        (Decimal(1), None),
        (None, None),
    ],
)
def test_fw_version_is_printable_and_at_most_32_characters(value, expected):
    parsed = fleet.status_from_item(status_item(fw_version=value))

    assert parsed is not None
    assert parsed[1].fw_version == expected


@pytest.mark.parametrize("value", [True, "true", Decimal(1), None])
def test_only_boolean_true_counts_as_charging(value):
    parsed = fleet.status_from_item(status_item(charging=value))

    assert parsed is not None
    assert parsed[1].charging is (value is True)


def test_status_without_latest_map_is_ignored():
    assert fleet.status_from_item({"pk": f"DEVICE#{WEARABLE}"}) is None
    assert fleet.status_from_item({"pk": f"DEVICE#{WEARABLE}", "latest": []}) is None


def test_status_with_malformed_pk_is_ignored():
    item = status_item()
    item["pk"] = "DEVICE#wearable-01"

    assert fleet.status_from_item(item) is None


# ---- devices ----


@pytest.mark.parametrize(
    "pk",
    [
        "DEVICE#sensor-0001",
        "DEVICE#wearable-1",
        "DEVICE#wearable-00001",
        "DEVICE#Wearable-0001",
        "DEVICE#wearable-0001\n",
        "wearable-0001",
        "RESIDENT#res-0001",
        None,
        Decimal(1),
    ],
)
def test_device_with_malformed_pk_is_none(pk):
    item = device_item()
    item["pk"] = pk

    assert fleet.device_from_item(item) is None


@pytest.mark.parametrize("resident_id", ["resident-1", "res-1", "RES-0001", Decimal(1)])
def test_device_with_malformed_resident_id_is_none(resident_id):
    assert fleet.device_from_item(device_item(resident_id=resident_id)) is None


def test_spare_device_has_no_resident_or_home():
    device = fleet.device_from_item(device_item(resident_id=None, home_id="home-0001"))

    assert device == fleet.Device(WEARABLE, "wearable", None, None)


def test_malformed_home_id_becomes_none():
    device = fleet.device_from_item(device_item(home_id="Blk 1 Synthetic Street"))

    assert device == fleet.Device(WEARABLE, "wearable", RESIDENT_A, None)


@pytest.mark.parametrize("device_type", [None, "phone", "", Decimal(1)])
def test_unknown_device_type_comes_from_the_id_prefix(device_type):
    item = device_item(BEDROOM)
    item["device_type"] = device_type

    device = fleet.device_from_item(item)

    assert device is not None
    assert device.device_type == "bedroom"


# ---- resident names ----


@pytest.mark.parametrize(
    "name", ["", "   ", "x" * 101, "Test\u202eResident", "Test\nResident", None, 7]
)
def test_invalid_display_name_is_dropped(name):
    assert fleet.resident_name_from_item(resident_item(name=name)) is None


def test_display_name_is_stripped():
    assert fleet.resident_name_from_item(resident_item(name="  Test Resident A ")) == (
        RESIDENT_A,
        NAME_A,
    )


def test_display_name_of_100_characters_is_kept():
    assert fleet.resident_name_from_item(resident_item(name="x" * 100)) == (
        RESIDENT_A,
        "x" * 100,
    )


@pytest.mark.parametrize("pk", ["RESIDENT#res-1", "DEVICE#res-0001", "res-0001", None])
def test_resident_with_malformed_pk_is_dropped(pk):
    assert fleet.resident_name_from_item({"pk": pk, "display_name": NAME_A}) is None


# ---- build_fleet ----


def test_build_fleet_sorts_by_device_id_and_counts_skipped():
    result = fleet.build_fleet(
        [
            device_item(WEARABLE_2),
            device_item(WEARABLE),
            device_item(BEDROOM),
            {"pk": "DEVICE#sensor-0001"},
        ],
        [],
        [],
        NOW,
    )

    assert [d["device_id"] for d in result.devices] == [BEDROOM, WEARABLE, WEARABLE_2]
    assert result.skipped == 1


def test_build_fleet_devices_have_exactly_the_data_model_keys():
    result = fleet.build_fleet(
        [device_item(WEARABLE), device_item(WEARABLE_2, resident_id=None)],
        [status_item(WEARABLE)],
        [resident_item()],
        NOW,
    )

    for device in result.devices:
        assert tuple(device) == fleet.DEVICE_KEYS
    assert tuple(result.devices[0]["assigned_to"]) == fleet.ASSIGNED_TO_KEYS
    assert result.devices[1]["assigned_to"] is None


def test_build_fleet_keeps_names_only_for_residents_on_a_device():
    result = fleet.build_fleet(
        [device_item(WEARABLE)],
        [],
        [resident_item(RESIDENT_A), resident_item(RESIDENT_B, "Test Resident B")],
        NOW,
    )

    assert "Test Resident B" not in repr(result)
    assert result.devices[0]["assigned_to"]["display_name"] == NAME_A


def test_build_fleet_ignores_statuses_of_unknown_devices():
    result = fleet.build_fleet(
        [device_item(WEARABLE)], [status_item(WEARABLE_2)], [], NOW
    )

    assert [d["device_id"] for d in result.devices] == [WEARABLE]
    assert result.devices[0]["online"] is False


def test_build_fleet_device_listed_twice_appears_once():
    result = fleet.build_fleet(
        [device_item(WEARABLE), device_item(WEARABLE)], [], [], NOW
    )

    assert len(result.devices) == 1


def test_build_fleet_bedroom_charging_is_false():
    result = fleet.build_fleet(
        [device_item(BEDROOM)], [status_item(BEDROOM, charging=True)], [], NOW
    )

    assert result.devices[0]["charging"] is False


def test_build_fleet_last_seen_is_the_status_ts_unchanged():
    ts = ago(minutes=3, milliseconds=7)

    result = fleet.build_fleet([device_item()], [status_item(ts=ts)], [], NOW)

    assert result.devices[0]["last_seen"] == ts
