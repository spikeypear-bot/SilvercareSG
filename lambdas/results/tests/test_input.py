"""Input forms (nightly-results.md criterion 4): the on-success invocation record or
{"date": ...}. Anything else raises before any AWS call, without repeating the value.
With `summaries: 0` the handler returns without reading S3.
"""

from __future__ import annotations

import handler
import pytest
from events import DATE, on_success


def test_on_success_record_gives_its_date_and_count():
    assert handler.parse_event(on_success(summaries=21)) == (DATE, 21)


def test_manual_date_gives_the_date_and_no_count():
    assert handler.parse_event({"date": DATE}) == (DATE, None)


def test_summaries_zero_returns_without_reading_s3(no_aws, logs):
    result = handler.lambda_handler(on_success(summaries=0), None)
    assert result == {"date": DATE, "loaded": 0}
    assert any(r.get("date") == DATE for r in logs())


@pytest.mark.parametrize(
    "event",
    [
        None,
        [],
        "2026-11-18",
        {},
        {"date": DATE, "extra": 1},
        {"Date": DATE},
        {"responsePayload": {"date": DATE, "summaries": 1}},
        on_success(condition="RetriesExhausted"),
        {**on_success(), "requestContext": "Success"},
        {**on_success(), "responsePayload": None},
        {**on_success(), "responsePayload": {"summaries": 1}},
        on_success(summaries=None),
        on_success(summaries="2"),
        on_success(summaries=True),
        on_success(summaries=-1),
        on_success(summaries=2.0),
    ],
    ids=[
        "none",
        "list",
        "bare-string",
        "empty",
        "extra-field",
        "wrong-case",
        "record-without-context",
        "failure-record",
        "context-not-object",
        "payload-not-object",
        "payload-without-date",
        "count-missing",
        "count-string",
        "count-bool",
        "count-negative",
        "count-float",
    ],
)
def test_other_events_raise_before_any_aws_call(no_aws, event):
    with pytest.raises((ValueError, TypeError)):
        handler.lambda_handler(event, None)


@pytest.mark.parametrize(
    "value",
    [
        "2026-02-30",
        "2026-13-01",
        "2026-11-31",
        "2026-1-18",
        "18-11-2026",
        "2026-11-18T00:00",
        " 2026-11-18",
        "2026-11-18\n",
        "".join(chr(0xFF10 + d) for d in (2, 0, 2, 6)) + "-11-18",  # full-width
        20261118,
        None,
        ["2026-11-18"],
    ],
)
def test_bad_dates_raise_before_any_aws_call(no_aws, value):
    with pytest.raises(ValueError):
        handler.lambda_handler({"date": value}, None)


@pytest.mark.parametrize("value", ["2026-02-30", "res-0003-qx-note-7781"])
def test_errors_never_repeat_the_value(no_aws, value):
    for event in ({"date": value}, on_success(date=value)):
        with pytest.raises(ValueError) as caught:
            handler.lambda_handler(event, None)
        assert value not in str(caught.value)
        assert "7781" not in str(caught.value)


def test_record_with_bad_date_raises_before_any_aws_call(no_aws):
    with pytest.raises(ValueError):
        handler.lambda_handler(on_success(date="2026-02-30"), None)


def test_leap_day_is_a_real_date():
    assert handler.parse_date("2028-02-29") == "2028-02-29"
