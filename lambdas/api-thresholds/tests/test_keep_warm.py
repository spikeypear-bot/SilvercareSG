"""Keep-warm pings (docs/features/api-caching.md criteria 3-4)."""

from __future__ import annotations

import io

import handler
import pytest
from events import api_event


def normal_event(**top):
    return api_event(groups=[], **top)


PING = {"source": "silvercare.keep-warm"}  # the rule's target input, exactly


@pytest.fixture
def no_aws(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("AWS client requested")

    for name in (
        "dynamodb_client",
        "cognito_client",
        "iot_data_client",
        "athena_client",
    ):
        if hasattr(handler, name):
            monkeypatch.setattr(handler, name, boom)


@pytest.fixture
def log_lines():
    buf = io.StringIO()
    stream_handler = handler.logger.registered_handler
    old = stream_handler.setStream(buf)
    yield lambda: [line for line in buf.getvalue().splitlines() if line]
    stream_handler.setStream(old)


def test_ping_returns_warm_without_aws_or_logs(no_aws, log_lines):
    assert handler.lambda_handler(dict(PING), None) == {"warm": True}
    assert log_lines() == []


def test_source_in_body_or_headers_is_a_normal_request(no_aws):
    event = normal_event(body='{"source": "silvercare.keep-warm"}')
    event["headers"]["source"] = "silvercare.keep-warm"
    response = handler.lambda_handler(event, None)
    assert response != {"warm": True}
    assert response["statusCode"] in (401, 403)
