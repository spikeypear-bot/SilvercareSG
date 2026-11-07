"""GET /devices reads only what its role may read (infra/api.tf, policy
`api_devices`; docs/data-model.md, Devices view). The registry `by-type` index also
projects `photo_key`, which IAM keeps from this route: the Query is allowed only with
Select = SPECIFIC_ATTRIBUTES and only for the listed attributes. The latest
`by-message` index has no attribute condition (it can't see inside `latest`), so the
code alone keeps `worn`, `received_at`, `clock_skew`, and `message_id` out. These tests
keep the code and the policy in step, because moto doesn't enforce IAM."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import handler
import pytest
from conftest import LATEST_TABLE, REGISTRY_TABLE, tf_projection
from events import seed_fleet

API_TF = Path(__file__).resolve().parents[3] / "infra" / "api.tf"
DROPPED_FROM_LATEST = {"worn", "received_at", "clock_skew", "message_id"}


def _statement(sid: str) -> str:
    text = API_TF.read_text(encoding="utf-8")
    match = re.search(
        r'sid\s*=\s*"' + re.escape(sid) + r'"(.*?)\n  }\n', text, re.DOTALL
    )
    assert match is not None, f"{sid} statement not found in api.tf"
    return match.group(1)


def _condition_values(sid: str, variable: str) -> set[str]:
    match = re.search(
        r'variable\s*=\s*"' + re.escape(variable) + r'"\s*values\s*=\s*\[(.*?)\]',
        _statement(sid),
        re.DOTALL,
    )
    assert match is not None, f"no {variable} condition on {sid}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _names(expression: str, names: dict[str, str]) -> set[str]:
    """Attribute names in an expression: #placeholders resolved, :values skipped.
    A nested path `#a.#b` yields both `a` and `b`."""
    tokens = re.findall(r"[#:]?[A-Za-z_]\w*", expression)
    return {names.get(t, t) for t in tokens if not t.startswith(":")}


def _requests(monkeypatch, *readers) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    monkeypatch.setattr(handler, "query_all", lambda r: requests.append(r) or [])
    for read in readers:
        read()
    return requests


@pytest.fixture
def by_type_requests(monkeypatch):
    return _requests(monkeypatch, handler.read_devices, handler.read_residents)


@pytest.fixture
def by_message_request(monkeypatch):
    (request,) = _requests(monkeypatch, handler.read_statuses)
    return request


# ---- registry by-type ----


def test_by_type_queries_send_select_specific_attributes(by_type_requests):
    assert len(by_type_requests) == 2
    for request in by_type_requests:
        assert request["IndexName"] == "by-type"
        assert request["Select"] == "SPECIFIC_ATTRIBUTES"
    assert _condition_values("RegistryByTypeIndex", "dynamodb:Select") == {
        "SPECIFIC_ATTRIBUTES"
    }


def test_by_type_queries_read_only_attributes_the_role_allows(by_type_requests):
    allowed = _condition_values("RegistryByTypeIndex", "dynamodb:Attributes")
    for request in by_type_requests:
        names = request["ExpressionAttributeNames"]
        read = _names(request["ProjectionExpression"], names) | _names(
            request["KeyConditionExpression"], names
        )

        assert read <= allowed, request["KeyConditionExpression"]


def test_by_type_queries_never_name_photo_key(by_type_requests):
    for request in by_type_requests:
        assert "photo_key" not in request["ExpressionAttributeNames"].values()
        assert "photo_key" not in request["ProjectionExpression"]


def test_role_may_not_read_photo_key_from_by_type():
    allowed = _condition_values("RegistryByTypeIndex", "dynamodb:Attributes")

    assert "photo_key" not in allowed
    assert "entity_type" in allowed


def test_by_type_fixture_projects_photo_key(aws):
    """So the checks above matter: the deployed index really holds photo_key."""
    table = aws.ddb.describe_table(TableName=REGISTRY_TABLE)["Table"]
    index = next(
        i for i in table["GlobalSecondaryIndexes"] if i["IndexName"] == "by-type"
    )

    assert index["Projection"] == tf_projection("by-type")
    assert "photo_key" in index["Projection"]["NonKeyAttributes"]


def test_by_type_items_hold_only_the_requested_attributes(aws, monkeypatch):
    seed_fleet(aws.ddb)
    seen: list[dict[str, Any]] = []
    real_query_all = handler.query_all

    def recording(request):
        items = real_query_all(request)
        seen.extend(items)
        return items

    monkeypatch.setattr(handler, "query_all", recording)

    devices = handler.read_devices()
    residents = handler.read_residents()

    assert len(devices) == 3
    assert len(residents) == 2
    for item in devices:
        assert set(item) <= {"pk", "device_type", "resident_id", "home_id"}
    for item in residents:
        assert set(item) == {"pk", "display_name"}
    assert all("photo_key" not in item for item in seen)


# ---- latest by-message ----


def test_by_message_query_never_names_the_dropped_latest_fields(by_message_request):
    names = by_message_request["ExpressionAttributeNames"]
    read = _names(by_message_request["ProjectionExpression"], names)

    assert read.isdisjoint(DROPPED_FROM_LATEST)
    assert set(names.values()).isdisjoint(DROPPED_FROM_LATEST)
    assert "entries" not in read
    assert by_message_request["Select"] == "SPECIFIC_ATTRIBUTES"


def test_by_message_projection_reads_only_four_latest_fields(by_message_request):
    names = by_message_request["ExpressionAttributeNames"]
    paths = [
        ".".join(names.get(part, part) for part in path.strip().split("."))
        for path in by_message_request["ProjectionExpression"].split(",")
    ]

    assert sorted(paths) == sorted(
        [
            "pk",
            "latest.ts",
            "latest.battery_pct",
            "latest.charging",
            "latest.fw_version",
        ]
    )


def test_by_message_fixture_projects_latest(aws):
    table = aws.ddb.describe_table(TableName=LATEST_TABLE)["Table"]
    (index,) = table["GlobalSecondaryIndexes"]

    assert index["Projection"] == tf_projection("by-message")
    assert "latest" in index["Projection"]["NonKeyAttributes"]


def test_by_message_items_hold_only_the_four_latest_fields(aws):
    """The nested ProjectionExpression really cuts `latest` down (moto)."""
    seed_fleet(aws.ddb)

    items = handler.read_statuses()

    assert len(items) == 2
    for item in items:
        assert set(item) == {"pk", "latest"}
        assert set(item["latest"]) <= {"ts", "battery_pct", "charging", "fw_version"}
        assert set(item["latest"]).isdisjoint(DROPPED_FROM_LATEST)
