"""Live-alert pings (docs/features/live-alerts.md, criteria 13-17)."""

from __future__ import annotations

import json
import urllib.error

import handler
import pytest
import realtime
from events import (
    RESIDENT,
    alert_body,
    check_body,
    nurse_ack_body,
    record,
    sqs_event,
)

HOST = "example1234.appsync-api.ap-southeast-1.amazonaws.com"


@pytest.fixture(autouse=True)
def _fresh_batch(monkeypatch):
    monkeypatch.setattr(realtime, "_batch", {"deadline": None, "tripped": False})


@pytest.fixture
def pings(monkeypatch):
    """Record publish calls instead of sending them."""
    calls: list[tuple[str, str]] = []

    def fake(namespace: str, resident_id: str):
        calls.append((namespace, resident_id))

    monkeypatch.setattr(realtime, "publish", fake)
    return calls


# ---- the publisher (criteria 15-16) ----


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_publish_signs_and_posts_one_ping(monkeypatch):
    monkeypatch.setenv("EVENTS_HTTP_DNS", HOST)
    sent = {}

    def fake_urlopen(request, timeout):
        sent.update(
            url=request.full_url,
            method=request.get_method(),
            headers={k.lower(): v for k, v in request.header_items()},
            body=json.loads(request.data),
            timeout=timeout,
        )
        return FakeResponse(200)

    monkeypatch.setattr(realtime.urllib.request, "urlopen", fake_urlopen)
    assert realtime.publish("health", RESIDENT) is None
    assert sent["url"] == f"https://{HOST}/event"
    assert sent["method"] == "POST"
    assert sent["timeout"] == 1.0
    auth = sent["headers"]["authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 ")
    assert "/ap-southeast-1/appsync/aws4_request" in auth
    assert sent["body"]["channel"] == f"/health/{RESIDENT}"
    (event,) = sent["body"]["events"]
    assert json.loads(event) == {"type": "changed", "resident_id": RESIDENT}


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("down"),
        TimeoutError(),
        urllib.error.HTTPError("u", 403, "Forbidden", {}, None),
    ],
)
def test_publish_failures_return_a_code_and_never_raise(monkeypatch, error):
    monkeypatch.setenv("EVENTS_HTTP_DNS", HOST)

    def fail(request, timeout):
        raise error

    monkeypatch.setattr(realtime.urllib.request, "urlopen", fail)
    assert realtime.publish("device", RESIDENT) == type(error).__name__


def test_publish_without_an_endpoint_does_nothing(monkeypatch):
    monkeypatch.delenv("EVENTS_HTTP_DNS", raising=False)

    def never(request, timeout):
        raise AssertionError("sent")

    monkeypatch.setattr(realtime.urllib.request, "urlopen", never)
    assert realtime.publish("health", RESIDENT) is None


# ---- when pings go out (criteria 13-14, 17) ----


def test_new_alert_pings_its_resident_health_channel(aws, pings):
    handler.lambda_handler(sqs_event(record(alert_body())), None)
    assert pings == [("health", RESIDENT)]


def test_ping_comes_after_the_write(aws, monkeypatch):
    from conftest import ALERTS_TABLE

    seen = []

    def check(namespace, resident_id):
        items = aws.ddb.scan(TableName=ALERTS_TABLE)["Items"]
        seen.append(len(items))

    monkeypatch.setattr(realtime, "publish", check)
    handler.lambda_handler(sqs_event(record(alert_body())), None)
    assert seen == [1]


def test_duplicate_delivery_sends_no_second_ping(aws, pings):
    handler.lambda_handler(sqs_event(record(alert_body())), None)
    handler.lambda_handler(sqs_event(record(alert_body())), None)
    assert pings == [("health", RESIDENT)]


def test_unassigned_alert_sends_no_ping(aws, pings):
    handler.lambda_handler(
        sqs_event(record(alert_body(resident_id="unassigned"))), None
    )
    assert pings == []


@pytest.mark.parametrize("alert_type", sorted(handler.DEVICE_PROBLEM_TYPES))
def test_namespace_is_device_for_device_problems(alert_type):
    assert realtime.namespace_for(alert_type, handler.DEVICE_PROBLEM_TYPES) == "device"


def test_namespace_is_health_for_other_alerts():
    assert realtime.namespace_for("fall", handler.DEVICE_PROBLEM_TYPES) == "health"


def test_acknowledge_pings_once(aws, pings):
    handler.lambda_handler(sqs_event(record(alert_body())), None)
    pings.clear()
    handler.lambda_handler(sqs_event(record(nurse_ack_body(), "sqs-msg-0002")), None)
    assert pings == [("health", RESIDENT)]
    pings.clear()
    # A second acknowledge changes nothing: no ping.
    handler.lambda_handler(sqs_event(record(nurse_ack_body(), "sqs-msg-0003")), None)
    assert pings == []


def test_ping_changes_sends_one_per_resident_and_namespace(pings):
    handler.ping_changes(
        [
            ("fall", RESIDENT),
            ("vitals_alert", RESIDENT),
            ("device_offline", RESIDENT),
            ("fall", "res-0004"),
            ("fall", "unassigned"),
            ("fall", None),
        ]
    )
    assert sorted(pings) == [
        ("device", RESIDENT),
        ("health", RESIDENT),
        ("health", "res-0004"),
    ]


def test_publish_failure_never_fails_the_record(aws, monkeypatch, logs):
    monkeypatch.setattr(realtime, "publish", lambda ns, rid: "URLError")
    result = handler.lambda_handler(sqs_event(record(alert_body())), None)
    assert result == {"batchItemFailures": []}
    warning = [r for r in logs() if r.get("message") == "realtime publish failed"]
    assert warning and warning[0]["error"] == "URLError"
    assert set(warning[0]) >= {"namespace", "resident_id", "error"}


def test_escalation_check_that_does_not_escalate_sends_no_ping(aws, pings):
    # No stored alert: the check finds nothing to escalate.
    handler.lambda_handler(sqs_event(record(check_body(), "sqs-msg-0009")), None)
    assert pings == []


# ---- the budget (criterion 16a) ----


def _failing_urlopen(monkeypatch):
    calls = []

    def fail(request, timeout):
        calls.append(timeout)
        raise TimeoutError

    monkeypatch.setattr(realtime.urllib.request, "urlopen", fail)
    return calls


def test_after_a_failure_the_rest_of_the_batch_is_skipped(monkeypatch):
    monkeypatch.setenv("EVENTS_HTTP_DNS", HOST)
    calls = _failing_urlopen(monkeypatch)
    realtime.start_batch()
    assert realtime.publish("health", "res-0001") == "TimeoutError"
    assert realtime.publish("health", "res-0002") == "skipped"
    assert realtime.publish("device", "res-0003") == "skipped"
    assert len(calls) == 1


def test_an_http_error_status_also_stops_the_batch(monkeypatch):
    monkeypatch.setenv("EVENTS_HTTP_DNS", HOST)
    monkeypatch.setattr(realtime, "_post", lambda url, body, headers, timeout: 503)
    realtime.start_batch()
    assert realtime.publish("health", "res-0001") == "http_503"
    assert realtime.publish("health", "res-0002") == "skipped"


def test_a_new_batch_tries_again(monkeypatch):
    monkeypatch.setenv("EVENTS_HTTP_DNS", HOST)
    calls = _failing_urlopen(monkeypatch)
    realtime.start_batch()
    realtime.publish("health", "res-0001")
    realtime.start_batch()
    realtime.publish("health", "res-0001")
    assert len(calls) == 2


def test_the_budget_caps_total_time_and_each_timeout(monkeypatch):
    monkeypatch.setenv("EVENTS_HTTP_DNS", HOST)
    now = {"t": 100.0}
    monkeypatch.setattr(realtime.time, "monotonic", lambda: now["t"])
    timeouts = []
    monkeypatch.setattr(
        realtime,
        "_post",
        lambda url, body, headers, timeout: timeouts.append(timeout) or 200,
    )
    realtime.start_batch()  # 1.5 s budget
    assert realtime.publish("health", "res-0001") is None
    now["t"] = 101.0  # 0.5 s left
    assert realtime.publish("health", "res-0002") is None
    now["t"] = 101.5  # spent
    assert realtime.publish("health", "res-0003") == "skipped"
    assert timeouts == [1.0, pytest.approx(0.5)]


def test_an_outage_costs_one_attempt_per_batch_and_fails_no_record(aws, monkeypatch):
    monkeypatch.setenv("EVENTS_HTTP_DNS", HOST)
    calls = _failing_urlopen(monkeypatch)
    records = [
        record(alert_body(resident_id=f"res-000{n}"), f"sqs-msg-000{n}")
        for n in (1, 2, 4)
    ]
    result = handler.lambda_handler(sqs_event(*records), None)
    assert result == {"batchItemFailures": []}
    assert len(calls) == 1
