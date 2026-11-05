"""The watchdog reads only what its role may read from the alerts `open` index
(infra/watchdog.tf, statement AlertsOpenIndex; docs/features/watchdog.md, criterion
17; docs/data-model.md, alerts Index). The index also projects the dashboards'
fields, including the nurse's `ack_note`, which the watchdog must never read: IAM
allows the Query only with Select = SPECIFIC_ATTRIBUTES and only for the listed
attributes. These tests keep the code and the policy in step, because moto doesn't
enforce IAM."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import handler
from checks import BATTERY_LOW
from conftest import ALERTS_TABLE, tf_projection
from events import ago, put_alert

WATCHDOG_TF = Path(__file__).resolve().parents[3] / "infra" / "watchdog.tf"
DASHBOARD_FIELDS = {
    "severity",
    "acknowledged_at",
    "ack_note",
    "acknowledged_by_role",
    "acknowledged_by",
    "escalated_at",
    "notified_at",
}


def _statement() -> str:
    text = WATCHDOG_TF.read_text(encoding="utf-8")
    match = re.search(r'sid\s*=\s*"AlertsOpenIndex"(.*?)\n  }\n', text, re.DOTALL)
    assert match is not None, "AlertsOpenIndex statement not found in watchdog.tf"
    return match.group(1)


def _condition_values(variable: str) -> set[str]:
    match = re.search(
        r'variable\s*=\s*"' + re.escape(variable) + r'"\s*values\s*=\s*\[(.*?)\]',
        _statement(),
        re.DOTALL,
    )
    assert match is not None, f"no {variable} condition on AlertsOpenIndex"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _names(expression: str, names: dict[str, str]) -> set[str]:
    """Attribute names in an expression: #placeholders resolved, :values skipped."""
    tokens = re.findall(r"[#:]?[A-Za-z_]\w*", expression)
    return {names.get(t, t) for t in tokens if not t.startswith(":")}


def _open_index_request(monkeypatch) -> dict[str, Any]:
    requests: list[dict[str, Any]] = []
    monkeypatch.setattr(handler, "query_all", lambda r: requests.append(r) or [])
    handler.read_open_alerts()
    (request,) = requests
    return request


def test_open_index_query_sends_select_specific_attributes(monkeypatch):
    request = _open_index_request(monkeypatch)

    assert request["IndexName"] == "open"
    assert request["Select"] == "SPECIFIC_ATTRIBUTES"
    assert _condition_values("dynamodb:Select") == {"SPECIFIC_ATTRIBUTES"}


def test_open_index_query_reads_only_attributes_the_role_allows(monkeypatch):
    request = _open_index_request(monkeypatch)
    names = request["ExpressionAttributeNames"]

    read = _names(request["ProjectionExpression"], names) | _names(
        request["KeyConditionExpression"], names
    )

    assert read <= _condition_values("dynamodb:Attributes")


def test_role_may_not_read_ack_note_or_other_dashboard_fields():
    assert _condition_values("dynamodb:Attributes").isdisjoint(
        DASHBOARD_FIELDS | {"details"}
    )


def test_open_index_fixture_projects_the_dashboard_fields(aws):
    table = aws.ddb.describe_table(TableName=ALERTS_TABLE)["Table"]
    (index,) = table["GlobalSecondaryIndexes"]

    assert index["Projection"] == tf_projection("open")
    assert DASHBOARD_FIELDS <= set(index["Projection"]["NonKeyAttributes"])
    assert "details" not in index["Projection"]["NonKeyAttributes"]


def test_open_index_items_hold_only_the_requested_attributes(aws, monkeypatch):
    item = put_alert(aws.ddb, BATTERY_LOW)
    aws.ddb.update_item(
        TableName=ALERTS_TABLE,
        Key={"pk": {"S": item["pk"]}, "sk": {"S": item["sk"]}},
        UpdateExpression=(
            "SET ack_note = :note, acknowledged_at = :at, notified_at = :at, "
            "acknowledged_by_role = :role, acknowledged_by = :by"
        ),
        ExpressionAttributeValues={
            ":note": {"S": "Synthetic note: test only."},
            ":at": {"S": ago(minutes=30)},
            ":role": {"S": "caregiver"},
            ":by": {"S": "3f2b8c1e-0000-4000-8000-000000000001"},
        },
    )
    seen: list[dict[str, Any]] = []
    real_query_all = handler.query_all

    def recording(request: dict[str, Any]) -> list[dict[str, Any]]:
        items = real_query_all(request)
        seen.extend(items)
        return items

    monkeypatch.setattr(handler, "query_all", recording)

    alerts = handler.read_open_alerts()

    assert [a.alert_type for a in alerts] == [BATTERY_LOW]
    assert seen
    for got in seen:
        assert set(got) <= {"pk", "sk", "alert_type", "device_id"}
        assert "ack_note" not in got
