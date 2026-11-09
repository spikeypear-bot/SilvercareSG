"""Subscribe check (docs/features/live-alerts.md, criteria 7-12)."""

from __future__ import annotations

import io
import json

import handler
import pytest
from conftest import CAREGIVER_SUB, STAFF_SUB, subscribe_event

REFUSED = {"error": "Unauthorized"}


def run(event):
    return handler.lambda_handler(event, None)


@pytest.fixture
def logs():
    buf = io.StringIO()
    stream_handler = handler.logger.registered_handler
    old = stream_handler.setStream(buf)
    yield lambda: [json.loads(line) for line in buf.getvalue().splitlines() if line]
    stream_handler.setStream(old)


# ---- staff (criterion 7) ----


@pytest.mark.parametrize("group", ["doctor", "nurse"])
@pytest.mark.parametrize(
    "path", ["/health/*", "/device/*", "/health/res-0002", "/device/res-0099"]
)
def test_staff_may_subscribe_to_any_resident_without_reads(aws, group, path):
    assert run(subscribe_event(path, groups=(group,), sub=STAFF_SUB)) is None
    assert aws.calls == []


def test_nurse_who_is_also_caregiver_is_staff(aws):
    event = subscribe_event("/health/*", groups=("caregiver", "nurse"), sub=STAFF_SUB)
    assert run(event) is None
    assert aws.calls == []


# ---- caregivers (criterion 8) ----


@pytest.mark.parametrize("path", ["/health/res-0001", "/device/res-0001"])
def test_caregiver_may_subscribe_to_a_linked_resident(aws, path):
    assert run(subscribe_event(path)) is None
    assert aws.calls == ["get_item"]


def test_caregiver_is_refused_an_unlinked_resident(aws):
    assert run(subscribe_event("/health/res-0002")) == REFUSED
    assert aws.calls == ["get_item"]


@pytest.mark.parametrize("path", ["/health/*", "/device/*"])
def test_caregiver_wildcard_is_refused_without_reads(aws, path):
    assert run(subscribe_event(path)) == REFUSED
    assert aws.calls == []


def test_caregiver_with_a_bad_sub_is_refused_without_reads(aws):
    assert run(subscribe_event("/health/res-0001", sub="not-a-uuid")) == REFUSED
    assert aws.calls == []


# ---- other groups (criterion 9) ----


@pytest.mark.parametrize("groups", [("ops",), ("family",), (), None, "caregiver"])
def test_other_groups_are_refused_without_reads(aws, groups):
    assert run(subscribe_event("/device/res-0001", groups=groups)) == REFUSED
    assert aws.calls == []


# ---- channel shapes (criterion 10) ----


@pytest.mark.parametrize(
    "path",
    [
        "/alerts/res-0001",
        "/health/res-0001/extra",
        "/health/res-1",
        "/health/RES-0001",
        "/health/",
        "//res-0001",
        "health/res-0001",
        "/health/res-0001*",
        None,
        42,
    ],
)
@pytest.mark.parametrize("groups", [("caregiver",), ("nurse",)])
def test_bad_channels_are_refused_before_any_read(aws, path, groups):
    assert run(subscribe_event(path, groups=groups)) == REFUSED
    assert aws.calls == []


def test_missing_identity_or_info_is_refused(aws):
    assert run({}) == REFUSED
    assert run({"identity": None, "info": None}) == REFUSED
    assert aws.calls == []


# ---- refusals look the same; logs (criterion 11) ----


def test_unlinked_and_unknown_residents_get_the_same_refusal(aws):
    assert run(subscribe_event("/health/res-0002")) == run(
        subscribe_event("/health/res-9999")
    )


def test_logs_ids_and_outcome_but_never_the_token(aws, logs):
    run(subscribe_event("/health/res-0001"))
    (record,) = logs()
    assert record["outcome"] == "allowed_linked"
    assert record["sub"] == CAREGIVER_SUB
    assert record["resident_id"] == "res-0001"
    assert record["namespace"] == "health"
    assert record["groups"] == ["caregiver"]
    text = json.dumps(record)
    assert "synthetic-token" not in text and "claims" not in text


def test_an_invalid_sub_is_logged_as_invalid(aws, logs):
    run(subscribe_event("/health/res-0001", groups=("ops",), sub="Bob <bob@x>"))
    (record,) = logs()
    assert record["sub"] == "invalid"


# ---- fail closed (criterion 12) ----


def test_dynamodb_error_refuses_and_logs_only_the_code(aws, monkeypatch, logs):
    from botocore.exceptions import ClientError

    def boom(**_kwargs):
        raise ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "x",
                }
            },
            "GetItem",
        )

    monkeypatch.setattr(aws, "get_item", boom, raising=False)
    assert run(subscribe_event("/health/res-0001")) == REFUSED
    (record,) = logs()
    assert record["outcome"] == "error"
    assert record["error"] == "ProvisionedThroughputExceededException"
