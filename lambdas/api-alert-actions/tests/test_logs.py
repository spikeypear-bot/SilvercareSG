"""The log lines never carry the note, the `name` or `email` claim, headers, or the
request body (docs/data-model.md, Status updates: "A route test checks the log lines
for this"). Records are parsed without `timestamp` and `location`; IDs are fixed."""

from __future__ import annotations

import json

import pytest
from events import (
    CAREGIVER_NAME,
    EMAIL,
    EVENT_TS,
    NAME,
    api_event,
    put_alert,
    put_link,
    status_of,
)
from helpers import call, client_error, log_text

# Distinctive words, so a leak can't be mistaken for anything else in a record.
SECRET_NOTE = "Synthetic note: zebra quokka marmalade."
SECRET_WORDS = ("zebra", "quokka", "marmalade")


def assert_clean(records: list[dict], *extra: str) -> None:
    text = json.dumps(records, ensure_ascii=False)
    for word in (*SECRET_WORDS, NAME, CAREGIVER_NAME, EMAIL, "Bearer", *extra):
        assert word not in text, word
    # The raw body text never appears either, with UUIDs taken out first.
    assert "Synthetic" not in log_text(records)
    assert "synthetic-token" not in text


def outcome_records(records: list[dict]) -> list[dict]:
    return [r for r in records if r.get("message") == "alert action"]


@pytest.mark.parametrize("groups", [("nurse",), ("doctor",)])
def test_resolve_success_logs_has_note_but_never_the_note(aws, logs, groups):
    put_alert(aws.ddb)
    body = {"event_ts": EVENT_TS, "note": SECRET_NOTE}

    result = call(api_event(groups=groups, body=body))

    assert status_of(result) == 202
    records = logs()
    (sent,) = [r for r in outcome_records(records) if r.get("outcome") == "sent"]
    assert sent["has_note"] is True
    assert sent["by_role"] == groups[0]
    assert sent["action"] == "resolve"
    assert_clean(records, json.dumps(body))


@pytest.mark.parametrize(
    "note",
    [
        SECRET_NOTE + "\u202e",
        SECRET_NOTE + " " + "x" * 500,
        "  ",
    ],
    ids=["bidi", "too_long", "blank"],
)
def test_resolve_400_logs_a_fixed_reason_and_never_the_note(aws, logs, note):
    put_alert(aws.ddb)
    body = {"event_ts": EVENT_TS, "note": note}

    result = call(api_event(body=body))

    assert status_of(result) == 400
    records = logs()
    (refused,) = outcome_records(records)
    assert refused["outcome"] == "refused_400"
    assert refused["reason"] == "note"
    assert_clean(records, json.dumps(body))


def test_resolve_400_on_an_unknown_key_never_logs_the_body(aws, logs):
    put_alert(aws.ddb)
    body = {"event_ts": EVENT_TS, "note": SECRET_NOTE, "zebra_key": "quokka"}

    call(api_event(body=body))

    records = logs()
    (refused,) = outcome_records(records)
    assert refused["reason"] == "body_keys"
    assert_clean(records, json.dumps(body))


def test_resolve_403_logs_groups_but_never_the_note_or_name(aws, logs):
    put_alert(aws.ddb)

    call(
        api_event(
            groups=("caregiver",),
            claims={"name": CAREGIVER_NAME},
            body={"event_ts": EVENT_TS, "note": SECRET_NOTE},
        )
    )

    records = logs()
    (refused,) = outcome_records(records)
    assert refused["outcome"] == "forbidden"
    assert refused["groups"] == ["caregiver"]
    assert_clean(records)


def test_resolve_500_logs_the_error_code_and_never_the_note(aws, logs):
    put_alert(aws.ddb)
    aws.sqs.fail["send_message"] = client_error("ServiceUnavailable", "SendMessage")

    call(api_event(body={"event_ts": EVENT_TS, "note": SECRET_NOTE}))

    records = logs()
    (failed,) = outcome_records(records)
    assert failed["outcome"] == "error"
    assert failed["error"] == "ServiceUnavailable"
    assert_clean(records)


def test_nurse_acknowledge_with_note_logs_has_note_but_never_the_note(aws, logs):
    put_alert(aws.ddb)

    call(api_event("acknowledge", body={"event_ts": EVENT_TS, "note": SECRET_NOTE}))

    records = logs()
    (sent,) = [r for r in outcome_records(records) if r.get("outcome") == "sent"]
    assert sent["has_note"] is True
    assert_clean(records)


def test_nurse_acknowledge_without_note_logs_has_note_false(aws, logs):
    put_alert(aws.ddb)

    call(api_event("acknowledge"))

    records = logs()
    (sent,) = [r for r in outcome_records(records) if r.get("outcome") == "sent"]
    assert sent["has_note"] is False


def test_caregiver_cancel_never_logs_the_name_claim(aws, logs):
    put_alert(aws.ddb)
    put_link(aws.ddb)

    result = call(
        api_event("cancel", groups=("caregiver",), claims={"name": CAREGIVER_NAME})
    )

    assert status_of(result) == 202
    assert_clean(logs())


def test_ops_acknowledge_with_note_400_never_logs_the_note(aws, logs):
    put_alert(aws.ddb, alert_type="battery_low")

    call(
        api_event(
            "acknowledge",
            groups=("ops",),
            body={"event_ts": EVENT_TS, "note": SECRET_NOTE},
        )
    )

    records = logs()
    (refused,) = outcome_records(records)
    assert refused["reason"] == "note_not_allowed"
    assert_clean(records)


def test_log_keys_do_not_carry_over_between_invocations(aws, logs):
    put_alert(aws.ddb)
    call(api_event())

    call(api_event(routeKey="POST /nowhere"))

    last = outcome_records(logs())[-1]
    assert last["outcome"] == "no_claims"
    for key in ("sub", "action", "resident_id", "alert_id"):
        assert key not in last
