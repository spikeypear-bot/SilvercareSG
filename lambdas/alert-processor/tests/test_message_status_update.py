"""status_update validation (docs/features/alert-cancel.md, criteria 6 and 19; changed
by docs/features/alert-routing.md, criteria 9 and 19). Pure."""

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
    NOTE,
    NURSE_SUB,
    RESIDENT,
    cancel_body,
    cancel_message,
    caregiver_cancel_body,
    caregiver_cancel_message,
    nurse_ack_body,
    nurse_ack_message,
)
from message import Rejection, StatusUpdate, parse_message

ALERT_TARGET = {"alert_id": ALERT_ID, "event_ts": EVENT_TS}


def parse(body: dict):
    return parse_message(json.dumps(body))


# ---- valid: one per row of criterion 9 ----


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


def test_caregiver_cancel_with_alert_target_is_valid():
    result = parse_message(caregiver_cancel_body())

    assert result == StatusUpdate(
        resident_id=RESIDENT,
        new_status="cancelled",
        changed_at=CHANGED_AT,
        changed_dt=message.parse_ts(CHANGED_AT),
        by_role="caregiver",
        by=CAREGIVER_SUB,
        by_name=CAREGIVER_NAME,
        alert_id=ALERT_ID,
        event_ts=EVENT_TS,
    )


def test_nurse_acknowledge_without_note_is_valid():
    result = parse_message(nurse_ack_body())

    assert result == StatusUpdate(
        resident_id=RESIDENT,
        new_status="acknowledged",
        changed_at=CHANGED_AT,
        changed_dt=message.parse_ts(CHANGED_AT),
        by_role="nurse",
        by=NURSE_SUB,
        by_name=None,
        alert_id=ALERT_ID,
        event_ts=EVENT_TS,
        note=None,
    )


def test_nurse_acknowledge_with_note_keeps_the_note():
    result = parse_message(nurse_ack_body(note=NOTE))

    assert isinstance(result, StatusUpdate)
    assert result.note == NOTE


def test_nurse_acknowledge_with_null_note_means_no_note():
    result = parse(nurse_ack_message(note=None))

    assert isinstance(result, StatusUpdate)
    assert result.note is None


def test_unassigned_resident_cancel_is_valid():
    assert isinstance(parse(cancel_message(resident_id="unassigned")), StatusUpdate)


def test_unassigned_nurse_acknowledge_is_valid():
    assert isinstance(parse(nurse_ack_message(resident_id="unassigned")), StatusUpdate)


@pytest.mark.parametrize("name", ["T", "x" * 100, "陈美玲", "Zoë Åström", " Test A "])
def test_caregiver_by_name_of_1_to_100_printable_characters_is_valid(name):
    assert isinstance(parse(caregiver_cancel_message(by_name=name)), StatusUpdate)


# ---- structure ----


@pytest.mark.parametrize("field", message.STATUS_UPDATE_FIELDS[1:])
def test_missing_field_is_rejected(field):
    assert parse(cancel_message(**{field: DELETE})) == Rejection(f"missing:{field}")


def test_unknown_top_level_field_is_rejected():
    body = cancel_message()
    body["details"] = {"note": "synthetic"}

    assert parse(body) == Rejection("unknown_field")


def test_misspelled_note_field_is_an_unknown_field():
    body = nurse_ack_message()
    body["notes"] = NOTE

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
        ({"changed_at": 1792293300}, "wrong_type:changed_at"),
        ({"changed_at": "2026-10-18 03:15:00"}, "invalid:changed_at"),
        ({"changed_at": "2026-10-18T03:15:00Z"}, "invalid:changed_at"),
        ({"changed_at": "2026-13-18T03:15:00.000Z"}, "invalid:changed_at"),
    ],
)
def test_invalid_top_level_field_is_rejected_with_its_reason(overrides, reason):
    assert parse(cancel_message(**overrides)) == Rejection(reason)


# ---- by_role (criterion 9: staff, family, and anything else are invalid) ----


@pytest.mark.parametrize(
    "by_role", ["staff", "family", "doctor", "Nurse", "CAREGIVER", "", "admin"]
)
@pytest.mark.parametrize(
    "build",
    [cancel_message, caregiver_cancel_message, nurse_ack_message],
    ids=["resident-shape", "caregiver-shape", "nurse-shape"],
)
def test_other_by_role_is_rejected(by_role, build):
    assert parse(build(by_role=by_role)) == Rejection("invalid:by_role")


def test_old_staff_acknowledge_is_rejected():
    """A3b's staff acknowledge shape (sub and full name) is no longer accepted."""
    body = nurse_ack_message(by_role="staff", by_name="Test Nurse Alpha")

    assert parse(body) == Rejection("invalid:by_role")


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
@pytest.mark.parametrize(
    "build", [caregiver_cancel_message, nurse_ack_message], ids=["cancel", "ack"]
)
def test_target_alert_id_with_bad_format_is_rejected(alert_id, build):
    target = {"alert_id": alert_id, "event_ts": EVENT_TS}

    assert parse(build(target=target)) == Rejection("invalid:target.alert_id")


@pytest.mark.parametrize("event_ts", ["2026-10-18", "", 1, None])
@pytest.mark.parametrize(
    "build", [caregiver_cancel_message, nurse_ack_message], ids=["cancel", "ack"]
)
def test_target_event_ts_with_bad_format_is_rejected(event_ts, build):
    target = {"alert_id": ALERT_ID, "event_ts": event_ts}

    assert parse(build(target=target)) == Rejection("invalid:target.event_ts")


# ---- resident row ----


def test_resident_acknowledge_is_rejected_as_invalid_new_status():
    body = cancel_message(new_status="acknowledged")

    assert parse(body) == Rejection("invalid:new_status")


def test_resident_cancel_with_alert_target_is_rejected():
    assert parse(cancel_message(target=ALERT_TARGET)) == Rejection("invalid:target")


@pytest.mark.parametrize("by", [NURSE_SUB, "", "resident"])
def test_resident_cancel_with_a_by_is_rejected(by):
    assert parse(cancel_message(by=by)) == Rejection("invalid:by")


@pytest.mark.parametrize("by_name", ["Test Resident A", ""])
def test_resident_cancel_with_a_by_name_is_rejected(by_name):
    assert parse(cancel_message(by_name=by_name)) == Rejection("invalid:by_name")


@pytest.mark.parametrize("note", [None, NOTE, ""])
def test_resident_cancel_with_a_note_key_is_rejected_even_null(note):
    assert parse(cancel_message(note=note)) == Rejection("invalid:note")


# ---- caregiver row ----


# ---- watchdog rows (docs/features/watchdog.md, criteria 10 and 14a) ----


def test_caregiver_acknowledge_with_alert_target_and_name_is_valid():
    result = parse(caregiver_cancel_message(new_status="acknowledged"))

    assert result == StatusUpdate(
        resident_id=RESIDENT,
        new_status="acknowledged",
        changed_at=CHANGED_AT,
        changed_dt=message.parse_ts(CHANGED_AT),
        by_role="caregiver",
        by=CAREGIVER_SUB,
        by_name=CAREGIVER_NAME,
        alert_id=ALERT_ID,
        event_ts=EVENT_TS,
    )


@pytest.mark.parametrize("by_name", [None, "", " ", "x" * 101, 42])
def test_caregiver_acknowledge_without_a_valid_by_name_is_rejected(by_name):
    body = caregiver_cancel_message(new_status="acknowledged", by_name=by_name)

    assert parse(body) == Rejection("invalid:by_name")


def test_caregiver_acknowledge_with_device_target_is_rejected():
    body = caregiver_cancel_message(
        new_status="acknowledged", target={"device_id": DEVICE}
    )

    assert parse(body) == Rejection("invalid:target")


@pytest.mark.parametrize("note", [None, NOTE])
def test_caregiver_acknowledge_with_a_note_key_is_rejected(note):
    body = caregiver_cancel_message(new_status="acknowledged")
    body["note"] = note

    assert parse(body) == Rejection("invalid:note")


def test_caregiver_acknowledge_for_unassigned_is_rejected():
    body = caregiver_cancel_message(new_status="acknowledged", resident_id="unassigned")

    assert parse(body) == Rejection("invalid:resident_id")


def test_caregiver_resolve_is_rejected_as_invalid_new_status():
    body = caregiver_cancel_message(new_status="resolved")

    assert parse(body) == Rejection("invalid:new_status")


def ops_ack(**overrides):
    return (
        caregiver_cancel_message(
            new_status="acknowledged", by_role="ops", by=NURSE_SUB, by_name=None
        )
        | overrides
    )


def test_ops_acknowledge_with_alert_target_is_valid():
    result = parse(ops_ack())

    assert isinstance(result, StatusUpdate)
    assert (result.by_role, result.new_status, result.by) == (
        "ops",
        "acknowledged",
        NURSE_SUB,
    )
    assert result.by_name is None
    assert result.note is None


@pytest.mark.parametrize("new_status", ["cancelled", "resolved"])
def test_ops_only_acknowledges(new_status):
    assert parse(ops_ack(new_status=new_status)) == Rejection("invalid:new_status")


@pytest.mark.parametrize("by", [None, "", "not-a-sub", NURSE_SUB.upper()])
def test_ops_acknowledge_without_a_sub_is_rejected(by):
    assert parse(ops_ack(by=by)) == Rejection("invalid:by")


@pytest.mark.parametrize("by_name", ["Test Ops Gamma", ""])
def test_ops_acknowledge_with_a_by_name_is_rejected(by_name):
    assert parse(ops_ack(by_name=by_name)) == Rejection("invalid:by_name")


@pytest.mark.parametrize("note", [None, NOTE])
def test_ops_acknowledge_with_a_note_key_is_rejected(note):
    assert parse(ops_ack(note=note)) == Rejection("invalid:note")


def test_ops_acknowledge_with_device_target_is_rejected():
    assert parse(ops_ack(target={"device_id": DEVICE})) == Rejection("invalid:target")


def test_unassigned_ops_acknowledge_is_valid():
    assert isinstance(parse(ops_ack(resident_id="unassigned")), StatusUpdate)


def system_resolve(**overrides):
    return (
        caregiver_cancel_message(
            new_status="resolved", by_role="system", by=None, by_name=None
        )
        | overrides
    )


def test_system_resolve_with_alert_target_is_valid():
    result = parse(system_resolve())

    assert result == StatusUpdate(
        resident_id=RESIDENT,
        new_status="resolved",
        changed_at=CHANGED_AT,
        changed_dt=message.parse_ts(CHANGED_AT),
        by_role="system",
        by=None,
        by_name=None,
        alert_id=ALERT_ID,
        event_ts=EVENT_TS,
    )


@pytest.mark.parametrize("new_status", ["cancelled", "acknowledged"])
def test_system_only_resolves(new_status):
    body = system_resolve(new_status=new_status)

    assert parse(body) == Rejection("invalid:new_status")


@pytest.mark.parametrize(
    "build",
    [cancel_message, caregiver_cancel_message, nurse_ack_message, ops_ack],
    ids=["resident", "caregiver", "nurse", "ops"],
)
def test_resolved_comes_only_from_system(build):
    assert parse(build(new_status="resolved")) == Rejection("invalid:new_status")


@pytest.mark.parametrize("by", [NURSE_SUB, "", "system"])
def test_system_resolve_with_a_by_is_rejected(by):
    assert parse(system_resolve(by=by)) == Rejection("invalid:by")


@pytest.mark.parametrize("by_name", ["Test System", ""])
def test_system_resolve_with_a_by_name_is_rejected(by_name):
    assert parse(system_resolve(by_name=by_name)) == Rejection("invalid:by_name")


@pytest.mark.parametrize("note", [None, NOTE])
def test_system_resolve_with_a_note_key_is_rejected(note):
    assert parse(system_resolve(note=note)) == Rejection("invalid:note")


def test_system_resolve_with_device_target_is_rejected():
    body = system_resolve(target={"device_id": DEVICE})

    assert parse(body) == Rejection("invalid:target")


def test_caregiver_cancel_with_device_target_is_rejected():
    body = caregiver_cancel_message(target={"device_id": DEVICE})

    assert parse(body) == Rejection("invalid:target")


@pytest.mark.parametrize(
    "by",
    [
        None,
        "",
        "not-a-sub",
        CAREGIVER_SUB.upper(),
        CAREGIVER_SUB + "0",
        123,
        CAREGIVER_NAME,
    ],
)
def test_caregiver_cancel_with_a_by_that_is_not_a_sub_is_rejected(by):
    assert parse(caregiver_cancel_message(by=by)) == Rejection("invalid:by")


@pytest.mark.parametrize(
    "by_name",
    [
        None,
        "",
        " ",
        "   ",
        "\u3000",  # ideographic space only
        "x" * 101,
        "Test\nCaregiver",
        "Test\rCaregiver",
        "Test\tCaregiver",
        "Test\x00Caregiver",
        "Test\u200bCaregiver",  # zero-width space (format character)
        "Test\u202eCaregiver",  # right-to-left override
        42,
        ["Test", "Caregiver"],
    ],
)
def test_caregiver_cancel_with_invalid_by_name_is_rejected(by_name):
    body = caregiver_cancel_message(by_name=by_name)

    assert parse(body) == Rejection("invalid:by_name")


def test_caregiver_cancel_for_unassigned_is_rejected():
    body = caregiver_cancel_message(resident_id="unassigned")

    assert parse(body) == Rejection("invalid:resident_id")


@pytest.mark.parametrize("note", [None, NOTE, ""])
def test_caregiver_cancel_with_a_note_key_is_rejected_even_null(note):
    body = caregiver_cancel_message(note=note)

    assert parse(body) == Rejection("invalid:note")


# ---- nurse row ----


def test_nurse_cancel_is_rejected_as_invalid_new_status():
    assert parse(nurse_ack_message(new_status="cancelled")) == Rejection(
        "invalid:new_status"
    )


def test_nurse_acknowledge_with_device_target_is_rejected():
    body = nurse_ack_message(target={"device_id": DEVICE})

    assert parse(body) == Rejection("invalid:target")


@pytest.mark.parametrize(
    "by",
    [None, "", "not-a-sub", NURSE_SUB.upper(), NURSE_SUB + "0", 123, "Test Nurse"],
)
def test_nurse_acknowledge_with_a_by_that_is_not_a_sub_is_rejected(by):
    assert parse(nurse_ack_message(by=by)) == Rejection("invalid:by")


@pytest.mark.parametrize("by_name", ["Test Nurse Alpha", "", " ", 42])
def test_nurse_acknowledge_with_a_by_name_is_rejected(by_name):
    assert parse(nurse_ack_message(by_name=by_name)) == Rejection("invalid:by_name")


# ---- note (nurse only): 1-500 characters, not only whitespace, no control
# characters except newline ----


def test_note_of_500_characters_is_valid():
    result = parse(nurse_ack_message(note="x" * 500))

    assert isinstance(result, StatusUpdate)
    assert result.note == "x" * 500


def test_note_of_501_characters_is_rejected():
    assert parse(nurse_ack_message(note="x" * 501)) == Rejection("invalid:note")


def test_note_of_one_character_is_valid():
    assert isinstance(parse(nurse_ack_message(note="x")), StatusUpdate)


def test_empty_note_is_rejected():
    assert parse(nurse_ack_message(note="")) == Rejection("invalid:note")


@pytest.mark.parametrize("note", [" ", "     ", "\n", " \n \n ", "\u3000", "\u00a0"])
def test_whitespace_only_note_is_rejected(note):
    assert parse(nurse_ack_message(note=note)) == Rejection("invalid:note")


@pytest.mark.parametrize(
    "note",
    [
        "Synthetic\tnote",
        "Synthetic\rnote",
        "Synthetic\r\nnote",
        "Synthetic\x00note",
        "Synthetic\x07note",
        "Synthetic\x1bnote",  # escape
        "Synthetic\x7fnote",  # delete
        "Synthetic\x85note",  # next line (C1 control)
    ],
)
def test_note_with_a_control_character_is_rejected(note):
    assert parse(nurse_ack_message(note=note)) == Rejection("invalid:note")


@pytest.mark.parametrize(
    "note",
    [
        "Synthetic line one.\nSynthetic line two.",
        "Synthetic line.\n",
        "\nSynthetic line.",
        "Synthetic.\n\nAfter a blank line.",
    ],
)
def test_note_with_newlines_is_valid_and_kept_as_sent(note):
    result = parse(nurse_ack_message(note=note))

    assert isinstance(result, StatusUpdate)
    assert result.note == note


def test_note_of_500_characters_with_newlines_is_valid():
    note = ("x" * 99 + "\n") * 5

    assert len(note) == 500
    assert isinstance(parse(nurse_ack_message(note=note)), StatusUpdate)


@pytest.mark.parametrize(
    "note",
    [
        "\U0001f691 Synthetic note: ambulance on the way.",  # ambulance emoji
        "Synthetic ❤\ufe0f note",  # heart + variation selector (Mn)
        "\U0001f44d",  # thumbs up only
        "陈美玲 synthetic note",
        "Zoë Åström synthetic note",
    ],
)
def test_note_with_emoji_or_non_latin_text_is_valid(note):
    result = parse(nurse_ack_message(note=note))

    assert isinstance(result, StatusUpdate)
    assert result.note == note


@pytest.mark.parametrize(
    "note",
    [
        "Synthetic\u202enote",  # right-to-left override (bidi, category Cf)
        "Synthetic\u200bnote",  # zero-width space (category Cf)
        "\u202eSynthetic note",
        "Synthetic note\u200b",
    ],
)
def test_note_with_bidi_or_invisible_format_character_is_rejected(note):
    """Pending the message.py fix: note rejects Unicode category Cf, like by_name
    (which rejects all of category C)."""
    assert parse(nurse_ack_message(note=note)) == Rejection("invalid:note")


@pytest.mark.parametrize(
    "note",
    [
        "Synthetic line one.\u2028Synthetic line two.",  # line separator (Zl)
        "Synthetic paragraph one.\u2029Synthetic two.",  # paragraph separator (Zp)
    ],
    ids=["U+2028", "U+2029"],
)
def test_note_with_a_unicode_line_or_paragraph_separator_is_rejected(note):
    assert parse(nurse_ack_message(note=note)) == Rejection("invalid:note")


@pytest.mark.parametrize(
    "note", ["\u200b", "\u200b" * 3, " \u200b "], ids=["one", "three", "spaced"]
)
def test_note_of_only_zero_width_spaces_is_rejected(note):
    assert parse(nurse_ack_message(note=note)) == Rejection("invalid:note")


def test_note_with_a_zero_width_joiner_emoji_sequence_is_rejected():
    """Health worker emoji: woman + ZWJ (U+200D, category Cf) + staff of
    Aesculapius + variation selector. Rejected since the review fix; single code
    point emoji are still accepted (test_note_with_emoji_or_non_latin_text_is_valid)."""
    note = "Synthetic note \U0001f469\u200d\u2695\ufe0f"

    assert parse(nurse_ack_message(note=note)) == Rejection("invalid:note")


@pytest.mark.parametrize("note", [5, 0, True, ["x"], {"text": "x"}])
def test_note_of_the_wrong_type_is_rejected(note):
    assert parse(nurse_ack_message(note=note)) == Rejection("invalid:note")


def test_rejection_reasons_never_contain_the_note():
    for note in ("Synthetic\tnote Alpha", "Synthetic " * 60, " "):
        result = parse(nurse_ack_message(note=note))
        assert isinstance(result, Rejection)
        assert result.reason == "invalid:note"


def test_rejection_reasons_never_contain_the_by_name():
    for name in ("Test\nCaregiver Beta", "x" * 101):
        result = parse(caregiver_cancel_message(by_name=name))
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


def test_log_ids_of_a_caregiver_cancel_never_have_by_or_by_name():
    ids = message.log_ids(caregiver_cancel_body())

    assert ids["alert_id"] == ALERT_ID
    assert "by" not in ids
    assert "by_name" not in ids
    assert CAREGIVER_SUB not in ids.values()
    assert CAREGIVER_NAME not in ids.values()


def test_log_ids_of_an_acknowledge_never_have_by_or_note():
    ids = message.log_ids(nurse_ack_body(note=NOTE))

    assert ids["alert_id"] == ALERT_ID
    assert "by" not in ids
    assert "note" not in ids
    assert NURSE_SUB not in ids.values()
    assert NOTE not in ids.values()


def test_log_ids_mark_a_free_text_target_as_invalid():
    ids = message.log_ids(cancel_body(target={"device_id": "Test Resident A"}))

    assert ids["device_id"] == "invalid"
