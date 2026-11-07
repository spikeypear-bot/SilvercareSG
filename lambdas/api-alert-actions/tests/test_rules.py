"""Pure rules for the resolve route (rules.py), no AWS, checked against
alert-processor's message.py where the two must agree."""

from __future__ import annotations

import json

import pytest
import rules
from events import ALERT_ID, EVENT_TS, NOTE, NOW_ISO, RESIDENT, SUB

PATH = {"resident_id": RESIDENT, "alert_id": ALERT_ID}


def parse(body) -> rules.Request:
    return rules.parse_request(rules.RESOLVE, PATH, json.dumps(body))


def test_parse_request_normalises_the_resolve_note():
    request = parse({"event_ts": EVENT_TS, "note": "  a\r\nb\r\n  "})

    assert request.note == "a\nb"
    assert request.action == rules.RESOLVE


@pytest.mark.parametrize(("length", "ok"), [(499, True), (500, True), (501, False)])
def test_valid_note_length_boundary(length, ok):
    assert rules.valid_note("n" * length) is ok


def test_valid_note_counts_code_points():
    assert rules.valid_note("\U0001f600" * 500) is True
    assert rules.valid_note("\U0001f600" * 501) is False


def test_valid_note_allows_newlines_inside():
    assert rules.valid_note("Synthetic\nnote") is True


@pytest.mark.parametrize(
    "groups",
    [{"nurse"}, {"nurse", "doctor"}],
)
def test_resolve_role_is_nurse_when_in_nurse(groups):
    assert rules.resolve_role(frozenset(groups)) == "nurse"


def test_resolve_role_is_doctor_otherwise():
    assert rules.resolve_role(frozenset({"doctor"})) == "doctor"


@pytest.mark.parametrize(
    ("action", "role", "allowed"),
    [
        (rules.RESOLVE, "nurse", True),
        (rules.RESOLVE, "doctor", True),
        (rules.ACKNOWLEDGE, "nurse", True),
        (rules.ACKNOWLEDGE, "ops", False),
        (rules.ACKNOWLEDGE, "caregiver", False),
        (rules.CANCEL, "caregiver", False),
    ],
)
def test_note_allowed_matches_alert_processor(action, role, allowed):
    assert rules.note_allowed(action, role) is allowed


@pytest.mark.parametrize("role", ["nurse", "doctor"])
def test_status_update_for_a_resolve_matches_alert_processor(ap_message, role):
    body = rules.status_update(
        resident_id=RESIDENT,
        alert_id=ALERT_ID,
        event_ts=EVENT_TS,
        action=rules.RESOLVE,
        by_role=role,
        by=SUB,
        by_name=None,
        note=NOTE,
        changed_at=NOW_ISO,
    )

    parsed = ap_message.parse_message(json.dumps(body))

    assert isinstance(parsed, ap_message.StatusUpdate), parsed
    assert "reason" not in body


@pytest.mark.parametrize("role", ["nurse", "doctor"])
def test_resolve_status_is_allowed_for_the_role_in_alert_processor(ap_message, role):
    assert "resolved" in ap_message.ROLE_STATUSES[role]


def test_the_shared_constants_match_alert_processor(ap_message):
    assert rules.NOTE_MAX_LEN == ap_message.NOTE_MAX_LEN
    assert rules.SCHEMA_VERSION == ap_message.SCHEMA_VERSION
    assert rules.ALERT_ID_RE.pattern == ap_message.UUID_RE.pattern
    assert rules.SUB_RE.pattern == ap_message.SUB_RE.pattern
    assert rules.RESIDENT_ID_RE.pattern == ap_message.RESIDENT_ID_RE.pattern
