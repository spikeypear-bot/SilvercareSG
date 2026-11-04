"""status_update validation (docs/features/alert-cancel.md, criteria 6 and 19). Pure."""

from __future__ import annotations

import json

import message
import pytest
from events import (
    ALERT_ID,
    CAREGIVER_NAME,
    CAREGIVER_SUB,
    CHANGED_AT,
    DELETE,
    DEVICE,
    EVENT_TS,
    RESIDENT,
    STAFF_NAME,
    STAFF_SUB,
    ack_body,
    ack_message,
    cancel_body,
    cancel_message,
)
from message import Rejection, StatusUpdate, parse_message


def parse(body: dict):
    return parse_message(json.dumps(body))


# ---- valid ----


def test_resident_cancel_with_device_target_is_valid():
    result = parse_message(cancel_body())

    assert result == StatusUpdate(
        resident_id=RESIDENT,
        new_status="cancelled",
        changed_at=CHANGED_AT,
        changed_dt=message.parse_ts(CHANGED_AT),
        by_role="resident",
        by=None,
        by_name=None,
        device_id=DEVICE,
    )


def test_staff_acknowledge_with_alert_target_is_valid():
    result = parse_message(ack_body("staff"))

    assert isinstance(result, StatusUpdate)
    assert result.alert_id == ALERT_ID
    assert result.event_ts == EVENT_TS
    assert result.device_id is None
    assert result.by == STAFF_SUB
    assert result.by_name == STAFF_NAME


def test_caregiver_acknowledge_is_valid():
    result = parse_message(ack_body("caregiver"))

    assert isinstance(result, StatusUpdate)
    assert result.by_role == "caregiver"
    assert result.by == CAREGIVER_SUB
    assert result.by_name == CAREGIVER_NAME


def test_unassigned_resident_cancel_is_valid():
    assert isinstance(parse(cancel_message(resident_id="unassigned")), StatusUpdate)


@pytest.mark.parametrize("name", ["T", "x" * 100, "陈美玲", "Zoë Åström", " Test A "])
def test_by_name_of_1_to_100_printable_characters_is_valid(name):
    assert isinstance(parse(ack_message(by_name=name)), StatusUpdate)


# ---- structure ----


@pytest.mark.parametrize("field", message.STATUS_UPDATE_FIELDS[1:])
def test_missing_field_is_rejected(field):
    assert parse(cancel_message(**{field: DELETE})) == Rejection(f"missing:{field}")


def test_unknown_top_level_field_is_rejected():
    body = cancel_message()
    body["details"] = {"note": "synthetic"}

    assert parse(body) == Rejection("unknown_field")


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"schema_version": "1"}, "wrong_type:schema_version"),
        ({"schema_version": 2}, "invalid:schema_version"),
        ({"resident_id": 3}, "wrong_type:resident_id"),
        ({"resident_id": "resident-3"}, "invalid:resident_id"),
        ({"new_status": 1}, "wrong_type:new_status"),
        ({"new_status": "resolved"}, "invalid:new_status"),
        ({"new_status": "open"}, "invalid:new_status"),
        ({"by_role": None}, "wrong_type:by_role"),
        ({"by_role": "doctor"}, "invalid:by_role"),
        ({"by_role": "nurse"}, "invalid:by_role"),
        ({"changed_at": 1792293300}, "wrong_type:changed_at"),
        ({"changed_at": "2026-10-18 03:15:00"}, "invalid:changed_at"),
        ({"changed_at": "2026-10-18T03:15:00Z"}, "invalid:changed_at"),
        ({"changed_at": "2026-13-18T03:15:00.000Z"}, "invalid:changed_at"),
    ],
)
def test_invalid_top_level_field_is_rejected_with_its_reason(overrides, reason):
    assert parse(cancel_message(**overrides)) == Rejection(reason)


# ---- target ----


@pytest.mark.parametrize("target", ["wearable-0007", ["wearable-0007"], None, 7])
def test_target_that_is_not_an_object_is_wrong_type(target):
    assert parse(cancel_message(target=target)) == Rejection("wrong_type:target")


@pytest.mark.parametrize(
    "target",
    [
        {},
        {"device_id": DEVICE, "alert_id": ALERT_ID},
        {"alert_id": ALERT_ID},
        {"event_ts": EVENT_TS},
        {"device_id": DEVICE, "extra": "x"},
        {"alert_id": ALERT_ID, "event_ts": EVENT_TS, "device_id": DEVICE},
    ],
)
def test_target_with_wrong_field_set_is_invalid(target):
    assert parse(cancel_message(target=target)) == Rejection("invalid:target")


@pytest.mark.parametrize("device_id", ["phone-0001", "wearable-01", 7, None])
def test_target_device_id_with_bad_format_is_rejected(device_id):
    result = parse(cancel_message(target={"device_id": device_id}))

    assert result == Rejection("invalid:target.device_id")


@pytest.mark.parametrize(
    "alert_id", ["not-a-uuid", ALERT_ID.upper(), 1, None, ALERT_ID + "0"]
)
def test_target_alert_id_with_bad_format_is_rejected(alert_id):
    target = {"alert_id": alert_id, "event_ts": EVENT_TS}

    assert parse(ack_message(target=target)) == Rejection("invalid:target.alert_id")


@pytest.mark.parametrize("event_ts", ["2026-10-18", "", 1, None])
def test_target_event_ts_with_bad_format_is_rejected(event_ts):
    target = {"alert_id": ALERT_ID, "event_ts": event_ts}

    assert parse(ack_message(target=target)) == Rejection("invalid:target.event_ts")


# ---- role rules ----


def test_resident_acknowledge_is_rejected_as_invalid_new_status():
    body = cancel_message(new_status="acknowledged")

    assert parse(body) == Rejection("invalid:new_status")


def test_resident_cancel_with_alert_target_is_rejected():
    body = cancel_message(target={"alert_id": ALERT_ID, "event_ts": EVENT_TS})

    assert parse(body) == Rejection("invalid:target")


@pytest.mark.parametrize("by", [STAFF_SUB, "", "resident"])
def test_resident_cancel_with_a_by_is_rejected(by):
    assert parse(cancel_message(by=by)) == Rejection("invalid:by")


@pytest.mark.parametrize("by_name", ["Test Resident A", ""])
def test_resident_cancel_with_a_by_name_is_rejected(by_name):
    assert parse(cancel_message(by_name=by_name)) == Rejection("invalid:by_name")


@pytest.mark.parametrize("by_role", ["staff", "caregiver"])
def test_staff_or_caregiver_cancel_is_rejected_as_invalid_new_status(by_role):
    body = ack_message(by_role, new_status="cancelled")

    assert parse(body) == Rejection("invalid:new_status")


@pytest.mark.parametrize("by_role", ["staff", "caregiver"])
def test_staff_or_caregiver_with_device_target_is_rejected(by_role):
    body = ack_message(by_role, target={"device_id": DEVICE})

    assert parse(body) == Rejection("invalid:target")


@pytest.mark.parametrize(
    "by",
    [None, "", "not-a-sub", STAFF_SUB.upper(), STAFF_SUB + "0", 123, STAFF_NAME],
)
def test_acknowledge_with_a_by_that_is_not_a_sub_is_rejected(by):
    assert parse(ack_message(by=by)) == Rejection("invalid:by")


@pytest.mark.parametrize(
    "by_name",
    [
        None,
        "",
        " ",
        "   ",
        "　",
        "x" * 101,
        "Test\nNurse",
        "Test\rNurse",
        "Test\tNurse",
        "Test\x00Nurse",
        "Test\u200bNurse",  # zero-width space (format character)
        "Test\u202eNurse",  # right-to-left override
        42,
        ["Test", "Nurse"],
    ],
)
def test_acknowledge_with_invalid_by_name_is_rejected(by_name):
    assert parse(ack_message(by_name=by_name)) == Rejection("invalid:by_name")


def test_rejection_reasons_never_contain_the_by_name():
    for name in ("Test\nNurse Alpha", "x" * 101):
        result = parse(ack_message(by_name=name))
        assert isinstance(result, Rejection)
        assert "Test" not in result.reason
        assert "xxx" not in result.reason


# ---- kinds and log ids ----


def test_status_update_kind_is_not_parsed_as_an_escalation_check():
    assert isinstance(parse_message(cancel_body()), StatusUpdate)


def test_unknown_kind_is_still_rejected():
    assert parse(cancel_message(kind="status_change")) == Rejection("invalid:kind")


def test_log_ids_of_a_cancel_take_device_id_from_target():
    ids = message.log_ids(cancel_body())

    assert ids["device_id"] == DEVICE
    assert ids["resident_id"] == RESIDENT
    assert ids["kind"] == "status_update"


def test_log_ids_of_an_acknowledge_take_alert_id_from_target_and_never_by():
    ids = message.log_ids(ack_body())

    assert ids["alert_id"] == ALERT_ID
    assert "by" not in ids
    assert "by_name" not in ids
    assert STAFF_SUB not in ids.values()
    assert STAFF_NAME not in ids.values()


def test_log_ids_mark_a_free_text_target_as_invalid():
    ids = message.log_ids(cancel_body(target={"device_id": "Test Resident A"}))

    assert ids["device_id"] == "invalid"
