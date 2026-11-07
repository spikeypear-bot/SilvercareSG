"""Staff resolve validation (docs/features/staff-resolve.md, criteria 1, 2 and 10):
the nurse and doctor `resolved` rows, the required closing note, and the rows that
stay unchanged. Pure."""

from __future__ import annotations

import json

import message
import pytest
from events import (
    ALERT_ID,
    CHANGED_AT,
    DELETE,
    DEVICE,
    DOCTOR_SUB,
    EVENT_TS,
    NOTE,
    NURSE_SUB,
    RESIDENT,
    RESOLVE_NOTE,
    back_in_bed_message,
    cancel_message,
    caregiver_cancel_message,
    nurse_ack_message,
    resolve_message,
    staff_resolve_body,
    staff_resolve_message,
)
from message import Rejection, StatusUpdate, parse_message

STAFF = pytest.mark.parametrize("by_role", ["nurse", "doctor"])


def parse(body: dict):
    return parse_message(json.dumps(body))


# ---- valid rows (criterion 1) ----


@pytest.mark.parametrize(
    ("by_role", "sub"), [("nurse", NURSE_SUB), ("doctor", DOCTOR_SUB)]
)
def test_staff_resolve_with_a_note_is_valid(by_role, sub):
    result = parse_message(staff_resolve_body(by_role))

    assert result == StatusUpdate(
        resident_id=RESIDENT,
        new_status="resolved",
        changed_at=CHANGED_AT,
        changed_dt=message.parse_ts(CHANGED_AT),
        by_role=by_role,
        by=sub,
        by_name=None,
        alert_id=ALERT_ID,
        event_ts=EVENT_TS,
        note=RESOLVE_NOTE,
    )
    assert result.reason is None


@STAFF
def test_unassigned_staff_resolve_is_valid(by_role):
    body = staff_resolve_message(by_role, resident_id="unassigned")

    assert isinstance(parse(body), StatusUpdate)


# ---- the required note (criterion 1) ----


@STAFF
def test_staff_resolve_without_a_note_key_is_rejected(by_role):
    assert parse(staff_resolve_message(by_role, note=DELETE)) == Rejection(
        "invalid:note"
    )


@STAFF
def test_staff_resolve_with_a_null_note_is_rejected(by_role):
    assert parse(staff_resolve_message(by_role, note=None)) == Rejection("invalid:note")


@STAFF
@pytest.mark.parametrize("note", ["", " ", "\n", " \n \n ", "　", " "])
def test_staff_resolve_with_a_blank_note_is_rejected(by_role, note):
    assert parse(staff_resolve_message(by_role, note=note)) == Rejection("invalid:note")


@STAFF
def test_staff_resolve_note_of_500_characters_is_valid(by_role):
    result = parse(staff_resolve_message(by_role, note="x" * 500))

    assert isinstance(result, StatusUpdate)
    assert result.note == "x" * 500


@STAFF
def test_staff_resolve_note_of_501_characters_is_rejected(by_role):
    assert parse(staff_resolve_message(by_role, note="x" * 501)) == Rejection(
        "invalid:note"
    )


@STAFF
def test_staff_resolve_note_of_one_character_is_valid(by_role):
    assert isinstance(parse(staff_resolve_message(by_role, note="x")), StatusUpdate)


@STAFF
@pytest.mark.parametrize(
    "note",
    [
        "Synthetic\tnote",
        "Synthetic\rnote",
        "Synthetic\r\nnote",
        "Synthetic\x00note",
        "Synthetic\x1bnote",
        "Synthetic\x7fnote",
        "Synthetic\x85note",  # next line (C1 control)
        "Synthetic\u202enote",  # right-to-left override (Cf)
        "Synthetic\u200bnote",  # zero-width space (Cf)
        "Synthetic\u2028note",  # line separator (Zl)
        "Synthetic\u2029note",  # paragraph separator (Zp)
    ],
    ids=[
        "tab",
        "cr",
        "crlf",
        "nul",
        "esc",
        "del",
        "nel",
        "U+202E",
        "U+200B",
        "U+2028",
        "U+2029",
    ],
)
def test_staff_resolve_note_with_a_control_or_separator_character_is_rejected(
    by_role, note
):
    assert parse(staff_resolve_message(by_role, note=note)) == Rejection("invalid:note")


@STAFF
def test_staff_resolve_note_with_newlines_is_kept_as_sent(by_role):
    note = "Synthetic line one.\n\nSynthetic line two.\n"

    result = parse(staff_resolve_message(by_role, note=note))

    assert isinstance(result, StatusUpdate)
    assert result.note == note


@STAFF
@pytest.mark.parametrize("note", [5, True, ["x"], {"text": "x"}])
def test_staff_resolve_note_of_the_wrong_type_is_rejected(by_role, note):
    assert parse(staff_resolve_message(by_role, note=note)) == Rejection("invalid:note")


@STAFF
def test_staff_resolve_rejection_reason_never_contains_the_note(by_role):
    for note in ("Synthetic\tclosing note Alpha", "Synthetic " * 60, " "):
        result = parse(staff_resolve_message(by_role, note=note))
        assert result == Rejection("invalid:note")


# ---- by, by_name, reason, target (criterion 1) ----


@STAFF
@pytest.mark.parametrize(
    "by",
    [None, "", "not-a-sub", NURSE_SUB.upper(), NURSE_SUB + "0", 123, "Test Nurse"],
)
def test_staff_resolve_with_a_by_that_is_not_a_lowercase_uuid_is_rejected(by_role, by):
    assert parse(staff_resolve_message(by_role, by=by)) == Rejection("invalid:by")


@STAFF
@pytest.mark.parametrize("by_name", ["Test Doctor Delta", "", " ", 42])
def test_staff_resolve_with_a_by_name_is_rejected(by_role, by_name):
    assert parse(staff_resolve_message(by_role, by_name=by_name)) == Rejection(
        "invalid:by_name"
    )


@STAFF
@pytest.mark.parametrize("reason", ["back_in_bed", None, "other"])
def test_staff_resolve_with_a_reason_key_is_rejected(by_role, reason):
    assert parse(staff_resolve_message(by_role, reason=reason)) == Rejection(
        "invalid:reason"
    )


@STAFF
def test_staff_resolve_with_a_device_target_is_rejected(by_role):
    body = staff_resolve_message(by_role, target={"device_id": DEVICE})

    assert parse(body) == Rejection("invalid:target")


@STAFF
def test_staff_resolve_with_an_upper_case_alert_id_is_rejected(by_role):
    body = staff_resolve_message(
        by_role, target={"alert_id": ALERT_ID.upper(), "event_ts": EVENT_TS}
    )

    assert parse(body) == Rejection("invalid:target.alert_id")


# ---- unchanged rows (criterion 2) ----


@pytest.mark.parametrize("new_status", ["acknowledged", "cancelled"])
def test_doctor_other_than_resolve_is_invalid_new_status(new_status):
    body = staff_resolve_message("doctor", new_status=new_status)

    assert parse(body) == Rejection("invalid:new_status")


def test_doctor_acknowledge_without_a_note_is_still_rejected():
    body = staff_resolve_message("doctor", new_status="acknowledged", note=DELETE)

    assert parse(body) == Rejection("invalid:new_status")


def test_nurse_acknowledge_note_stays_optional():
    assert parse(nurse_ack_message()).note is None
    assert parse(nurse_ack_message(note=None)).note is None
    assert parse(nurse_ack_message(note=NOTE)).note == NOTE


@pytest.mark.parametrize("note", [None, RESOLVE_NOTE])
def test_system_resolve_with_a_note_key_is_still_rejected(note):
    assert parse(resolve_message(note=note)) == Rejection("invalid:note")


@pytest.mark.parametrize("note", [None, RESOLVE_NOTE])
def test_back_in_bed_resolve_with_a_note_key_is_still_rejected(note):
    body = back_in_bed_message()
    body["note"] = note

    assert parse(body) == Rejection("invalid:note")


@pytest.mark.parametrize(
    "build", [cancel_message, caregiver_cancel_message], ids=["resident", "caregiver"]
)
@pytest.mark.parametrize("note", [None, RESOLVE_NOTE])
def test_cancel_with_a_note_key_is_still_rejected(build, note):
    assert parse(build(note=note)) == Rejection("invalid:note")


# ---- log ids ----


@STAFF
def test_log_ids_of_a_staff_resolve_never_have_by_or_note(by_role):
    ids = message.log_ids(staff_resolve_body(by_role))

    assert ids["alert_id"] == ALERT_ID
    assert not {"by", "by_name", "note"} & set(ids)
    assert NURSE_SUB not in ids.values()
    assert DOCTOR_SUB not in ids.values()
    assert RESOLVE_NOTE not in ids.values()
