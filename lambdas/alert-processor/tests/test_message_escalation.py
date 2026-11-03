"""Escalation check message validation (docs/features/escalation.md, criteria 1, 17).
Pure, no moto."""

from __future__ import annotations

import json

import pytest
from events import (
    ALERT_ID,
    DELETE,
    EVENT_TS,
    RESIDENT,
    alert_body,
    alert_message,
    check_body,
    check_message,
)
from message import Alert, EscalationCheck, Rejection, log_ids, parse_message

EXACT_BODY = (
    '{"kind":"escalation_check","schema_version":1,'
    f'"alert_id":"{ALERT_ID}","resident_id":"{RESIDENT}","event_ts":"{EVENT_TS}"}}'
)


def test_valid_check_parses():
    assert parse_message(check_body()) == EscalationCheck(
        alert_id=ALERT_ID, resident_id=RESIDENT, event_ts=EVENT_TS
    )


def test_check_for_an_unassigned_alert_parses():
    result = parse_message(check_body(resident_id="unassigned"))

    assert isinstance(result, EscalationCheck)
    assert result.resident_id == "unassigned"


def test_body_without_kind_is_an_alert():
    assert isinstance(parse_message(alert_body()), Alert)


def test_check_body_is_exact():
    import message

    assert message.check_body(ALERT_ID, RESIDENT, EVENT_TS) == EXACT_BODY


def test_check_body_round_trips():
    import message

    text = message.check_body(ALERT_ID, "unassigned", EVENT_TS)

    assert parse_message(text) == EscalationCheck(ALERT_ID, "unassigned", EVENT_TS)


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("escalation", "invalid:kind"),
        ("alert", "invalid:kind"),
        ("ESCALATION_CHECK", "invalid:kind"),
        ("", "invalid:kind"),
        (None, "wrong_type:kind"),
        (1, "wrong_type:kind"),
        (["escalation_check"], "wrong_type:kind"),
    ],
)
def test_unknown_kind_is_rejected(kind, reason):
    assert parse_message(check_body(kind=kind)) == Rejection(reason)


def test_alert_message_with_a_kind_field_is_rejected():
    body = json.dumps(alert_message(kind="alert"))

    assert parse_message(body) == Rejection("invalid:kind")


def test_alert_fields_in_a_check_are_rejected():
    body = json.dumps(check_message(alert_type="fall"))

    assert parse_message(body) == Rejection("unknown_field")


@pytest.mark.parametrize(
    "name", ["schema_version", "alert_id", "resident_id", "event_ts"]
)
def test_check_missing_a_field_is_rejected(name):
    assert parse_message(check_body(**{name: DELETE})) == Rejection(f"missing:{name}")


def test_check_with_an_unknown_field_is_rejected():
    assert parse_message(check_body(extra="x")) == Rejection("unknown_field")


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"schema_version": 2}, "invalid:schema_version"),
        ({"schema_version": 0}, "invalid:schema_version"),
        ({"schema_version": "1"}, "wrong_type:schema_version"),
        ({"schema_version": 1.0}, "wrong_type:schema_version"),
        ({"schema_version": True}, "wrong_type:schema_version"),
        ({"alert_id": "not-a-uuid"}, "invalid:alert_id"),
        ({"alert_id": ALERT_ID.upper()}, "invalid:alert_id"),
        ({"alert_id": 1}, "wrong_type:alert_id"),
        ({"resident_id": "Test Resident A"}, "invalid:resident_id"),
        ({"resident_id": "res-03"}, "invalid:resident_id"),
        ({"resident_id": None}, "wrong_type:resident_id"),
        ({"event_ts": "2026-10-18T03:14:07Z"}, "invalid:event_ts"),
        ({"event_ts": "2026-10-18 03:14:07.360Z"}, "invalid:event_ts"),
        ({"event_ts": "2026-13-40T03:14:07.360Z"}, "invalid:event_ts"),
        ({"event_ts": 1793686998}, "wrong_type:event_ts"),
    ],
)
def test_check_with_a_bad_format_is_rejected(overrides, reason):
    assert parse_message(check_body(**overrides)) == Rejection(reason)


@pytest.mark.parametrize("text", ["not json", None, "[]", '["kind"]', "NaN"])
def test_non_object_body_is_rejected(text):
    assert isinstance(parse_message(text), Rejection)


def test_rejection_reason_never_contains_the_value():
    result = parse_message(check_body(kind="Test Resident A hr 187"))

    assert isinstance(result, Rejection)
    assert "Test" not in result.reason
    assert "187" not in result.reason


def test_log_ids_of_a_check_include_kind():
    assert log_ids(check_body(schema_version=2)) == {
        "alert_id": ALERT_ID,
        "alert_type": None,
        "severity": None,
        "device_id": None,
        "resident_id": RESIDENT,
        "kind": "escalation_check",
    }


def test_log_ids_mask_an_unknown_kind_and_bad_ids():
    ids = log_ids(check_body(kind="hr 187", resident_id="Test Resident A"))

    assert ids["kind"] == "invalid"
    assert ids["resident_id"] == "invalid"


def test_log_ids_of_an_alert_have_no_kind():
    assert "kind" not in log_ids(alert_body())
