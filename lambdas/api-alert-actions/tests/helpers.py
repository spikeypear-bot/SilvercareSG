"""Small helpers shared by the route tests: call the handler, read what it sent, and
check error answers."""

from __future__ import annotations

import json
import re
from typing import Any

import handler
from botocore.exceptions import ClientError
from events import body_of, status_of

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def call(event: dict[str, Any]) -> dict[str, Any]:
    return handler.lambda_handler(event, None)


def sent_texts(aws) -> list[str]:
    return [kw["MessageBody"] for kw in aws.sends()]


def sent_bodies(aws) -> list[dict[str, Any]]:
    return [json.loads(text) for text in sent_texts(aws)]


def assert_nothing_sent(aws) -> None:
    assert aws.sends() == []
    assert aws.queued() == []


def assert_error(result: dict[str, Any], status: int, code: str) -> None:
    """The answer is `status` with exactly {"error": {"code", "message"}}."""
    assert status_of(result) == status
    body = body_of(result)
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code


def client_error(code: str, op: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "synthetic"}}, op)


def log_text(records: list[dict[str, Any]]) -> str:
    """Parsed log records as one JSON text, with UUIDs replaced (check real IDs are
    absent before calling this)."""
    return UUID_RE.sub("<uuid>", json.dumps(records, ensure_ascii=False))
