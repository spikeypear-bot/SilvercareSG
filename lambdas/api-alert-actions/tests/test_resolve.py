"""POST /residents/{resident_id}/alerts/{alert_id}/resolve (docs/data-model.md, Track
B route: resolve; Status updates). Every success is checked against alert-processor's
own validator, and every refusal sends nothing."""

from __future__ import annotations

import json
import re

import handler
import pytest
from events import (
    ALERT_ID,
    DEVICE_PROBLEM_TYPES,
    EVENT_TS,
    HEALTH_TYPES,
    NOTE,
    NOW_ISO,
    OTHER_RESIDENT,
    RESIDENT,
    SUB,
    UNASSIGNED,
    api_event,
    body_of,
    put_alert,
    put_link,
    status_of,
)
from helpers import (
    assert_error,
    assert_nothing_sent,
    call,
    client_error,
    sent_bodies,
    sent_texts,
)

# ---- success ----


@pytest.mark.parametrize(
    ("groups", "role"),
    [
        (("nurse",), "nurse"),
        (("doctor",), "doctor"),
        (("nurse", "doctor"), "nurse"),
        (("doctor", "nurse"), "nurse"),
        ("[doctor nurse]", "nurse"),
        (("doctor", "caregiver"), "doctor"),
    ],
)
def test_resolve_by_role_comes_from_the_group(aws, groups, role):
    put_alert(aws.ddb)

    result = call(api_event(groups=groups))

    assert status_of(result) == 202
    (body,) = sent_bodies(aws)
    assert body["by_role"] == role


@pytest.mark.parametrize("groups", [("nurse",), ("doctor",)])
def test_resolve_answers_202_with_only_alert_id_and_requested_status(aws, groups):
    put_alert(aws.ddb)

    result = call(api_event(groups=groups))

    assert status_of(result) == 202
    assert body_of(result) == {"alert_id": ALERT_ID, "requested_status": "resolved"}
    assert result["headers"]["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    ("groups", "role"), [(("nurse",), "nurse"), (("doctor",), "doctor")]
)
def test_resolve_sends_exactly_the_documented_message(aws, frozen_now, groups, role):
    put_alert(aws.ddb, status="acknowledged")

    call(api_event(groups=groups))

    assert sent_bodies(aws) == [
        {
            "kind": "status_update",
            "schema_version": 1,
            "resident_id": RESIDENT,
            "new_status": "resolved",
            "changed_at": NOW_ISO,
            "by_role": role,
            "by": SUB,
            "by_name": None,
            "note": NOTE,
            "target": {"alert_id": ALERT_ID, "event_ts": EVENT_TS},
        }
    ]


def test_resolve_message_has_no_reason_key(aws):
    put_alert(aws.ddb)

    call(api_event())

    (body,) = sent_bodies(aws)
    assert "reason" not in body


def test_resolve_puts_exactly_one_message_on_the_queue(aws):
    put_alert(aws.ddb)

    call(api_event())

    queued = aws.queued()
    assert len(queued) == 1
    assert json.loads(queued[0])["new_status"] == "resolved"


def test_resolve_changed_at_is_iso_ms_z(aws):
    put_alert(aws.ddb)

    call(api_event())

    (body,) = sent_bodies(aws)
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", body["changed_at"]
    )


@pytest.mark.parametrize("groups", [("nurse",), ("doctor",), ("nurse", "doctor")])
@pytest.mark.parametrize("status", ["open", "acknowledged"])
def test_resolve_message_is_accepted_by_alert_processor(
    aws, ap_message, groups, status
):
    put_alert(aws.ddb, status=status)

    call(api_event(groups=groups))

    (text,) = sent_texts(aws)
    parsed = ap_message.parse_message(text)
    assert isinstance(parsed, ap_message.StatusUpdate), parsed
    assert parsed.new_status == "resolved"
    assert parsed.note == NOTE
    assert parsed.alert_id == ALERT_ID
    assert parsed.event_ts == EVENT_TS


def test_resolve_with_normalised_note_is_accepted_by_alert_processor(aws, ap_message):
    put_alert(aws.ddb)
    raw = "  Synthetic note: line one.\r\nSynthetic note: line two.\n\n  "

    call(api_event(body={"event_ts": EVENT_TS, "note": raw}))

    (text,) = sent_texts(aws)
    parsed = ap_message.parse_message(text)
    assert isinstance(parsed, ap_message.StatusUpdate), parsed
    assert parsed.note == "Synthetic note: line one.\nSynthetic note: line two."


def test_resolve_sends_the_note_crlf_to_lf_and_trimmed(aws):
    put_alert(aws.ddb)

    call(api_event(body={"event_ts": EVENT_TS, "note": "\t Synthetic note: a\r\nb \n"}))

    (body,) = sent_bodies(aws)
    assert body["note"] == "Synthetic note: a\nb"


@pytest.mark.parametrize("alert_type", HEALTH_TYPES)
def test_resolve_accepts_every_health_alert_type(aws, ap_message, alert_type):
    put_alert(aws.ddb, alert_type=alert_type)

    result = call(api_event())

    assert status_of(result) == 202
    (text,) = sent_texts(aws)
    assert isinstance(ap_message.parse_message(text), ap_message.StatusUpdate)


@pytest.mark.parametrize("resident_id", [OTHER_RESIDENT, UNASSIGNED])
def test_resolve_works_for_any_resident_without_a_link(aws, ap_message, resident_id):
    put_alert(aws.ddb, resident_id=resident_id)

    result = call(api_event(groups=("doctor",), resident_id=resident_id))

    assert status_of(result) == 202
    (body,) = sent_bodies(aws)
    assert body["resident_id"] == resident_id
    parsed = ap_message.parse_message(json.dumps(body))
    assert isinstance(parsed, ap_message.StatusUpdate), parsed


def test_resolve_never_reads_the_caregiver_link(aws):
    put_alert(aws.ddb)

    call(api_event(groups=("nurse", "caregiver")))

    tables = [kw["TableName"] for kw in aws.get_items()]
    assert tables == ["silvercare-alerts"]


def test_resolve_reads_the_alert_strongly_consistent_with_a_narrow_projection(aws):
    put_alert(aws.ddb)

    call(api_event())

    (kwargs,) = aws.get_items()
    assert kwargs["ConsistentRead"] is True
    assert kwargs["Key"] == {
        "pk": {"S": f"RESIDENT#{RESIDENT}"},
        "sk": {"S": f"ALERT#{EVENT_TS}#{ALERT_ID}"},
    }
    names = kwargs["ExpressionAttributeNames"]
    projected = {names[p.strip()] for p in kwargs["ProjectionExpression"].split(",")}
    assert projected == {"pk", "sk", "alert_type", "status"}


def test_resolve_accepts_a_base64_body(aws):
    put_alert(aws.ddb)

    result = call(api_event(base64_body=True))

    assert status_of(result) == 202
    (body,) = sent_bodies(aws)
    assert body["note"] == NOTE


def test_resolve_takes_resident_and_target_from_the_stored_key(aws, monkeypatch):
    """A stored key that doesn't match the request (it can't in DynamoDB, so this
    replaces the read) is a 404, never a message built from the path."""
    put_alert(aws.ddb)
    real = handler.get_alert

    def mismatched(request):
        item = real(request)
        item["pk"] = f"RESIDENT#{OTHER_RESIDENT}"
        return item

    monkeypatch.setattr(handler, "get_alert", mismatched)

    result = call(api_event())

    assert_error(result, 404, "not_found")
    assert_nothing_sent(aws)


# ---- note ----


@pytest.mark.parametrize("length", [1, 499, 500])
def test_resolve_note_up_to_500_characters_is_accepted(aws, ap_message, length):
    put_alert(aws.ddb)
    note = "n" * length

    result = call(api_event(body={"event_ts": EVENT_TS, "note": note}))

    assert status_of(result) == 202
    (text,) = sent_texts(aws)
    parsed = ap_message.parse_message(text)
    assert isinstance(parsed, ap_message.StatusUpdate), parsed
    assert parsed.note == note


def test_resolve_note_of_500_code_points_outside_the_bmp_is_accepted(aws, ap_message):
    put_alert(aws.ddb)
    note = "\U0001f600" * 500  # 500 code points, 1000 UTF-16 units

    result = call(
        api_event(
            body=json.dumps({"event_ts": EVENT_TS, "note": note}, ensure_ascii=False)
        )
    )

    assert status_of(result) == 202
    (text,) = sent_texts(aws)
    assert isinstance(ap_message.parse_message(text), ap_message.StatusUpdate)


def test_resolve_note_of_500_characters_after_trimming_is_accepted(aws):
    put_alert(aws.ddb)

    result = call(
        api_event(body={"event_ts": EVENT_TS, "note": "  " + "n" * 500 + "  "})
    )

    assert status_of(result) == 202


def test_resolve_note_of_501_characters_is_400(aws):
    put_alert(aws.ddb)

    result = call(api_event(body={"event_ts": EVENT_TS, "note": "n" * 501}))

    assert_error(result, 400, "bad_request")
    assert_nothing_sent(aws)


BAD_NOTES = {
    "empty": "",
    "spaces": "   ",
    "newlines": "\n\n",
    "crlf_only": "\r\n\r\n",
    "tab_inside": "Synthetic\tnote",
    "lone_cr_inside": "Synthetic\rnote",
    "nul": "Synthetic\x00note",
    "del": "Synthetic\x7fnote",
    "c1_control": "Synthetic\x9bnote",
    "rlo_bidi_override": "Synthetic \u202enote",
    "lri_bidi_isolate": "Synthetic \u2066note",
    "zero_width_space": "Synthetic\u200bnote",
    "zero_width_joiner_emoji": "Synthetic \U0001f468\u200d\U0001f469 note",
    "bom_inside": "Synthetic\ufeffnote",
    "line_separator": "Synthetic\u2028note",
    "paragraph_separator": "Synthetic\u2029note",
    "private_use": "Synthetic\ue000note",
    "unassigned_code_point": "Synthetic\u0378note",
}


@pytest.mark.parametrize("note", BAD_NOTES.values(), ids=BAD_NOTES.keys())
def test_resolve_invalid_note_is_400_and_never_echoed(aws, note):
    put_alert(aws.ddb)

    result = call(api_event(body={"event_ts": EVENT_TS, "note": note}))

    assert_error(result, 400, "bad_request")
    assert "Synthetic" not in result["body"]
    assert_nothing_sent(aws)


@pytest.mark.parametrize("note", BAD_NOTES.values(), ids=BAD_NOTES.keys())
def test_every_note_the_api_refuses_alert_processor_refuses_too(ap_message, note):
    """The API's note rule is never stricter than alert-processor's on the normalised
    note (otherwise a portal-accepted note would be refused here for no reason)."""
    normalised = note.replace("\r\n", "\n").strip()
    assert not ap_message._valid_note(normalised)


@pytest.mark.parametrize(
    "body",
    [
        {"event_ts": EVENT_TS},
        {"event_ts": EVENT_TS, "note": None},
        {"event_ts": EVENT_TS, "note": 42},
        {"event_ts": EVENT_TS, "note": ["Synthetic note: list"]},
        {"event_ts": EVENT_TS, "note": {"text": "Synthetic note: map"}},
        {"event_ts": EVENT_TS, "note": True},
    ],
    ids=["missing", "null", "number", "list", "map", "bool"],
)
def test_resolve_without_a_string_note_is_400(aws, body):
    put_alert(aws.ddb)

    result = call(api_event(body=body))

    assert_error(result, 400, "bad_request")
    assert "Synthetic" not in result["body"]
    assert_nothing_sent(aws)


# ---- body and path ----


@pytest.mark.parametrize(
    "extra",
    [
        {"reason": "back_in_bed"},
        {"by": SUB},
        {"by_role": "nurse"},
        {"by_name": "Test Name"},
        {"new_status": "resolved"},
        {"resident_id": RESIDENT},
        {"alert_id": ALERT_ID},
    ],
    ids=lambda d: next(iter(d)),
)
def test_resolve_body_with_any_other_key_is_400(aws, extra):
    put_alert(aws.ddb)

    result = call(api_event(body={"event_ts": EVENT_TS, "note": NOTE, **extra}))

    assert_error(result, 400, "bad_request")
    assert_nothing_sent(aws)


@pytest.mark.parametrize(
    "event_ts",
    [
        None,
        "",
        1762311000000,
        "2026-11-05T02:50:00Z",
        "2026-11-05T02:50:00.000",
        "2026-11-05 02:50:00.000Z",
        "2026-13-05T02:50:00.000Z",
        "2026-02-30T02:50:00.000Z",
        "2026-11-05T02:50:00.000+08:00",
    ],
)
def test_resolve_bad_event_ts_is_400(aws, event_ts):
    put_alert(aws.ddb)

    result = call(api_event(body={"event_ts": event_ts, "note": NOTE}))

    assert_error(result, 400, "bad_request")
    assert_nothing_sent(aws)


def test_resolve_missing_event_ts_is_400(aws):
    put_alert(aws.ddb)

    result = call(api_event(body={"note": NOTE}))

    assert_error(result, 400, "bad_request")
    assert_nothing_sent(aws)


@pytest.mark.parametrize(
    "body",
    [None, "", "not json", "[]", '"text"', "null", "{", json.dumps([NOTE])],
    ids=[
        "none",
        "empty",
        "not_json",
        "list",
        "string",
        "null",
        "truncated",
        "note_list",
    ],
)
def test_resolve_malformed_body_is_400(aws, body):
    put_alert(aws.ddb)

    result = call(api_event(body=body))

    assert_error(result, 400, "bad_request")
    assert_nothing_sent(aws)


def test_resolve_body_over_4096_bytes_is_400(aws):
    put_alert(aws.ddb)
    body = json.dumps({"event_ts": EVENT_TS, "note": NOTE}) + " " * 4096

    result = call(api_event(body=body))

    assert_error(result, 400, "bad_request")
    assert_nothing_sent(aws)


def test_resolve_bad_base64_body_is_400(aws):
    put_alert(aws.ddb)
    event = api_event()
    event["isBase64Encoded"] = True
    event["body"] = "!!not base64!!"

    result = call(event)

    assert_error(result, 400, "bad_request")
    assert_nothing_sent(aws)


@pytest.mark.parametrize(
    ("resident_id", "alert_id"),
    [
        ("resident-0001", ALERT_ID),
        ("res-1", ALERT_ID),
        (RESIDENT, "not-a-uuid"),
        (RESIDENT, ALERT_ID.upper()),
        (RESIDENT, "6f1c2e0a-5b3d-5e8f-1a47-000000000001"),
    ],
)
def test_resolve_bad_path_is_400(aws, resident_id, alert_id):
    put_alert(aws.ddb)

    result = call(api_event(resident_id=resident_id, alert_id=alert_id))

    assert_error(result, 400, "bad_request")
    assert_nothing_sent(aws)


def test_resolve_without_path_parameters_is_400(aws):
    put_alert(aws.ddb)

    result = call(api_event(pathParameters=None))

    assert_error(result, 400, "bad_request")
    assert_nothing_sent(aws)


# ---- groups ----


@pytest.mark.parametrize(
    "groups",
    [("caregiver",), ("ops",), ("family",), (), None, "", ("staff",), ("admin",)],
    ids=[
        "caregiver",
        "ops",
        "family",
        "empty_list",
        "no_claim",
        "empty_string",
        "staff",
        "admin",
    ],
)
def test_resolve_without_nurse_or_doctor_is_403(aws, groups):
    put_alert(aws.ddb)
    put_link(aws.ddb)

    result = call(api_event(groups=groups))

    assert_error(result, 403, "forbidden")
    assert_nothing_sent(aws)
    assert aws.get_items() == []


def test_resolve_403_comes_before_the_body_check(aws):
    result = call(api_event(groups=("caregiver",), body="not json"))

    assert_error(result, 403, "forbidden")
    assert_nothing_sent(aws)


def test_resolve_400_comes_before_the_alert_read(aws):
    result = call(api_event(body={"event_ts": EVENT_TS}))

    assert_error(result, 400, "bad_request")
    assert aws.get_items() == []


# ---- alert lookup, type, status ----


def test_resolve_missing_alert_is_404(aws):
    result = call(api_event())

    assert_error(result, 404, "not_found")
    assert_nothing_sent(aws)


def test_resolve_of_an_alert_under_another_event_ts_is_404(aws):
    put_alert(aws.ddb)

    result = call(
        api_event(body={"event_ts": "2026-11-05T02:50:00.001Z", "note": NOTE})
    )

    assert_error(result, 404, "not_found")
    assert_nothing_sent(aws)


@pytest.mark.parametrize("alert_type", DEVICE_PROBLEM_TYPES)
@pytest.mark.parametrize("groups", [("nurse",), ("doctor",)])
def test_resolve_of_a_device_problem_is_409(aws, alert_type, groups):
    put_alert(aws.ddb, alert_type=alert_type)

    result = call(api_event(groups=groups))

    assert_error(result, 409, "conflict")
    assert_nothing_sent(aws)


@pytest.mark.parametrize("alert_type", ["vitals_alert", "unknown_type", ""])
def test_resolve_of_an_unknown_alert_type_is_404(aws, alert_type):
    put_alert(aws.ddb, alert_type=alert_type)

    result = call(api_event())

    assert_error(result, 404, "not_found")
    assert_nothing_sent(aws)


@pytest.mark.parametrize("alert_type", [["fall"], {"t": "fall"}, 7])
def test_resolve_of_an_alert_type_that_is_not_a_string_is_404(aws, alert_type):
    put_alert(aws.ddb, alert_type=alert_type)

    result = call(api_event())

    assert_error(result, 404, "not_found")
    assert_nothing_sent(aws)


@pytest.mark.parametrize("status", ["cancelled", "resolved"])
def test_resolve_of_a_closed_alert_is_409(aws, status):
    put_alert(aws.ddb, status=status)

    result = call(api_event())

    assert_error(result, 409, "conflict")
    assert_nothing_sent(aws)


@pytest.mark.parametrize("status", [None, "OPEN", "unknown", ["open"], {"s": "open"}])
def test_resolve_of_an_alert_with_a_bad_status_is_409(aws, status):
    put_alert(aws.ddb, status=status)

    result = call(api_event())

    assert_error(result, 409, "conflict")
    assert_nothing_sent(aws)


# ---- AWS failures ----


def test_resolve_throttled_read_is_500_and_sends_nothing(aws):
    put_alert(aws.ddb)
    aws.dynamodb.fail["get_item"] = client_error(
        "ProvisionedThroughputExceededException", "GetItem"
    )

    result = call(api_event())

    assert_error(result, 500, "internal_error")
    assert_nothing_sent(aws)


def test_resolve_failed_send_is_500(aws):
    put_alert(aws.ddb)
    aws.sqs.fail["send_message"] = client_error("ServiceUnavailable", "SendMessage")

    result = call(api_event())

    assert_error(result, 500, "internal_error")
    assert aws.queued() == []


def test_resolve_failed_send_never_echoes_the_note_in_the_response(aws):
    put_alert(aws.ddb)
    aws.sqs.fail["send_message"] = client_error("ServiceUnavailable", "SendMessage")

    result = call(api_event())

    assert NOTE not in result["body"]
    assert "Synthetic" not in result["body"]


# ---- auth edge cases ----


@pytest.mark.parametrize(
    "route",
    [
        "POST /residents/{resident_id}/alerts/{alert_id}/close",
        "GET /residents/{resident_id}/alerts/{alert_id}/resolve",
        "",
        None,
    ],
)
def test_unknown_route_key_is_401(aws, route):
    put_alert(aws.ddb)

    result = call(api_event(routeKey=route))

    assert_error(result, 401, "unauthorized")
    assert_nothing_sent(aws)


def test_missing_claims_is_401(aws):
    put_alert(aws.ddb)
    event = api_event()
    event["requestContext"]["authorizer"] = {}

    result = call(event)

    assert_error(result, 401, "unauthorized")
    assert_nothing_sent(aws)


@pytest.mark.parametrize(
    "sub", [None, "", "not-a-uuid", "0000000A-0000-4000-8000-000000000001", 7]
)
def test_invalid_sub_is_401(aws, sub):
    put_alert(aws.ddb)

    result = call(api_event(claims={"sub": sub}))

    assert_error(result, 401, "unauthorized")
    assert_nothing_sent(aws)


def test_resolve_by_is_the_token_sub_and_by_name_null(aws):
    """`by` is always the token's sub; a body `by` is refused (see body keys)."""
    put_alert(aws.ddb)

    call(api_event(claims={"sub": "00000000-0000-4000-8000-00000000000f"}))

    (body,) = sent_bodies(aws)
    assert body["by"] == "00000000-0000-4000-8000-00000000000f"
    assert body["by_name"] is None
