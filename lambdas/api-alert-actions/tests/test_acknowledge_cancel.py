"""Regression for POST .../acknowledge and .../cancel after the note rule changed for
the resolve route (docs/data-model.md, Status updates). Focused: the note rules per
role, doctor refused, and a few core group/link/type checks."""

from __future__ import annotations

import pytest
from events import (
    ALERT_ID,
    CAREGIVER_NAME,
    EVENT_TS,
    NOTE,
    SUB,
    UNASSIGNED,
    api_event,
    body_of,
    put_alert,
    put_link,
    status_of,
)
from helpers import assert_error, assert_nothing_sent, call, sent_bodies, sent_texts

CAREGIVER_CLAIMS = {"name": CAREGIVER_NAME}


# ---- nurse acknowledge and its note ----


def test_nurse_acknowledge_without_note_is_202_without_a_note_key(aws, ap_message):
    put_alert(aws.ddb)

    result = call(api_event("acknowledge", groups=("nurse",)))

    assert status_of(result) == 202
    assert body_of(result) == {"alert_id": ALERT_ID, "requested_status": "acknowledged"}
    (body,) = sent_bodies(aws)
    assert "note" not in body
    assert body["by_role"] == "nurse"
    assert body["new_status"] == "acknowledged"
    (text,) = sent_texts(aws)
    assert isinstance(ap_message.parse_message(text), ap_message.StatusUpdate)


def test_nurse_acknowledge_with_null_note_is_202_without_a_note_key(aws):
    put_alert(aws.ddb)

    result = call(api_event("acknowledge", body={"event_ts": EVENT_TS, "note": None}))

    assert status_of(result) == 202
    (body,) = sent_bodies(aws)
    assert "note" not in body


def test_nurse_acknowledge_with_note_is_202_with_the_note(aws, ap_message):
    put_alert(aws.ddb)

    result = call(
        api_event("acknowledge", body={"event_ts": EVENT_TS, "note": f" {NOTE}\r\n"})
    )

    assert status_of(result) == 202
    (body,) = sent_bodies(aws)
    assert body["note"] == NOTE
    (text,) = sent_texts(aws)
    parsed = ap_message.parse_message(text)
    assert isinstance(parsed, ap_message.StatusUpdate), parsed
    assert parsed.note == NOTE


def test_nurse_acknowledge_with_blank_note_is_400(aws):
    """Unlike the portal (where empty means no note), the API refuses a blank one."""
    put_alert(aws.ddb)

    result = call(api_event("acknowledge", body={"event_ts": EVENT_TS, "note": "  "}))

    assert_error(result, 400, "bad_request")
    assert_nothing_sent(aws)


def test_nurse_acknowledge_with_zero_width_joiner_note_is_400(aws):
    put_alert(aws.ddb)

    result = call(
        api_event(
            "acknowledge",
            body={"event_ts": EVENT_TS, "note": "Synthetic \U0001f468\u200d\U0001f469"},
        )
    )

    assert_error(result, 400, "bad_request")
    assert_nothing_sent(aws)


def test_nurse_acknowledge_of_an_acknowledged_alert_is_409(aws):
    put_alert(aws.ddb, status="acknowledged")

    result = call(api_event("acknowledge", groups=("nurse",)))

    assert_error(result, 409, "conflict")
    assert_nothing_sent(aws)


@pytest.mark.parametrize("alert_type", ["device_offline", "battery_low", "not_worn"])
def test_nurse_acknowledge_of_a_device_problem_is_409(aws, alert_type):
    put_alert(aws.ddb, alert_type=alert_type)

    result = call(api_event("acknowledge", groups=("nurse",)))

    assert_error(result, 409, "conflict")
    assert_nothing_sent(aws)


# ---- ops and caregiver acknowledge: no note ----


def test_ops_acknowledge_of_a_device_problem_is_202_without_a_note_key(aws, ap_message):
    put_alert(aws.ddb, alert_type="battery_low")

    result = call(api_event("acknowledge", groups=("ops",)))

    assert status_of(result) == 202
    (body,) = sent_bodies(aws)
    assert body["by_role"] == "ops"
    assert "note" not in body
    (text,) = sent_texts(aws)
    assert isinstance(ap_message.parse_message(text), ap_message.StatusUpdate)


def test_ops_acknowledge_with_a_note_is_400(aws):
    put_alert(aws.ddb, alert_type="battery_low")

    result = call(
        api_event(
            "acknowledge", groups=("ops",), body={"event_ts": EVENT_TS, "note": NOTE}
        )
    )

    assert_error(result, 400, "bad_request")
    assert "Synthetic" not in result["body"]
    assert_nothing_sent(aws)


def test_caregiver_acknowledge_with_a_note_is_400(aws):
    put_alert(aws.ddb, alert_type="not_worn")
    put_link(aws.ddb)

    result = call(
        api_event(
            "acknowledge",
            groups=("caregiver",),
            claims=CAREGIVER_CLAIMS,
            body={"event_ts": EVENT_TS, "note": NOTE},
        )
    )

    assert_error(result, 400, "bad_request")
    assert "Synthetic" not in result["body"]
    assert_nothing_sent(aws)


def test_caregiver_acknowledge_of_a_device_problem_is_202_with_by_name(aws, ap_message):
    put_alert(aws.ddb, alert_type="not_worn")
    put_link(aws.ddb)

    result = call(
        api_event("acknowledge", groups=("caregiver",), claims=CAREGIVER_CLAIMS)
    )

    assert status_of(result) == 202
    (body,) = sent_bodies(aws)
    assert body["by_role"] == "caregiver"
    assert body["by_name"] == CAREGIVER_NAME
    assert "note" not in body
    (text,) = sent_texts(aws)
    assert isinstance(ap_message.parse_message(text), ap_message.StatusUpdate)


def test_ops_acknowledge_of_a_health_alert_is_404(aws):
    put_alert(aws.ddb, alert_type="fall")

    result = call(api_event("acknowledge", groups=("ops",)))

    assert_error(result, 404, "not_found")
    assert_nothing_sent(aws)


# ---- caregiver cancel ----


def test_caregiver_cancel_is_202_and_accepted_by_alert_processor(aws, ap_message):
    put_alert(aws.ddb, status="acknowledged")
    put_link(aws.ddb)

    result = call(api_event("cancel", groups=("caregiver",), claims=CAREGIVER_CLAIMS))

    assert status_of(result) == 202
    assert body_of(result) == {"alert_id": ALERT_ID, "requested_status": "cancelled"}
    (body,) = sent_bodies(aws)
    assert body["by_role"] == "caregiver"
    assert body["by"] == SUB
    assert "note" not in body
    (text,) = sent_texts(aws)
    assert isinstance(ap_message.parse_message(text), ap_message.StatusUpdate)


@pytest.mark.parametrize("note", [NOTE, None, ""])
def test_cancel_with_a_note_key_is_400(aws, note):
    put_alert(aws.ddb)
    put_link(aws.ddb)

    result = call(
        api_event(
            "cancel",
            groups=("caregiver",),
            claims=CAREGIVER_CLAIMS,
            body={"event_ts": EVENT_TS, "note": note},
        )
    )

    assert_error(result, 400, "bad_request")
    assert "Synthetic" not in result["body"]
    assert_nothing_sent(aws)


def test_caregiver_cancel_without_a_link_is_404_and_reads_no_alert(aws):
    put_alert(aws.ddb)

    result = call(api_event("cancel", groups=("caregiver",), claims=CAREGIVER_CLAIMS))

    assert_error(result, 404, "not_found")
    assert_nothing_sent(aws)
    tables = [kw["TableName"] for kw in aws.get_items()]
    assert tables == ["silvercare-registry"]


def test_caregiver_cancel_of_unassigned_is_404_without_a_link_read(aws):
    put_alert(aws.ddb, resident_id=UNASSIGNED)

    result = call(
        api_event(
            "cancel",
            groups=("caregiver",),
            claims=CAREGIVER_CLAIMS,
            resident_id=UNASSIGNED,
        )
    )

    assert_error(result, 404, "not_found")
    assert_nothing_sent(aws)
    assert aws.get_items() == []


def test_caregiver_cancel_of_a_device_problem_is_409(aws):
    put_alert(aws.ddb, alert_type="device_offline")
    put_link(aws.ddb)

    result = call(api_event("cancel", groups=("caregiver",), claims=CAREGIVER_CLAIMS))

    assert_error(result, 409, "conflict")
    assert_nothing_sent(aws)


def test_caregiver_without_a_name_claim_is_403(aws):
    put_alert(aws.ddb)
    put_link(aws.ddb)

    result = call(api_event("cancel", groups=("caregiver",), claims={"name": None}))

    assert_error(result, 403, "account_incomplete")
    assert_nothing_sent(aws)


# ---- doctor and other groups ----


@pytest.mark.parametrize("action", ["acknowledge", "cancel"])
def test_doctor_on_acknowledge_or_cancel_is_403(aws, action):
    put_alert(aws.ddb)
    put_link(aws.ddb)

    result = call(api_event(action, groups=("doctor",)))

    assert_error(result, 403, "forbidden")
    assert_nothing_sent(aws)
    assert aws.get_items() == []


@pytest.mark.parametrize("action", ["acknowledge", "cancel"])
def test_family_on_acknowledge_or_cancel_is_403(aws, action):
    put_alert(aws.ddb)
    put_link(aws.ddb)

    result = call(api_event(action, groups=("family",)))

    assert_error(result, 403, "forbidden")
    assert_nothing_sent(aws)


@pytest.mark.parametrize("groups", [("nurse",), ("ops",)])
def test_nurse_or_ops_on_cancel_is_403(aws, groups):
    put_alert(aws.ddb)

    result = call(api_event("cancel", groups=groups))

    assert_error(result, 403, "forbidden")
    assert_nothing_sent(aws)


def test_acknowledge_body_with_an_unknown_key_is_400(aws):
    put_alert(aws.ddb)

    result = call(
        api_event("acknowledge", body={"event_ts": EVENT_TS, "by_role": "nurse"})
    )

    assert_error(result, 400, "bad_request")
    assert_nothing_sent(aws)
