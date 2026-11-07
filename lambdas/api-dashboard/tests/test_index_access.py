"""GET /dashboard reads only what its role may read (infra/api.tf, policy document
`api_dashboard`) and only what each index projects (infra/dynamodb.tf). moto doesn't
enforce IAM, so these tests keep the code, the policy, and the projections in step:

- AlertsOpenIndex: Select = SPECIFIC_ATTRIBUTES and only the alert-row attributes,
  never `details` (not projected either).
- RegistryByTypeIndex: never `photo_key`, which the index does project.
- LatestByMessageIndex: no attribute condition (IAM can't see inside `latest`), so the
  code alone queries only `status` and `vitals` and drops `received_at`, `clock_skew`,
  `message_id`, and `activity_pct`.
- RegistryThresholds: BatchGetItem of THRESHOLDS# partitions and the three limits only,
  never `updated_by`.
- CognitoNames: AdminGetUser only.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import handler
import pytest
from conftest import ALERTS_TABLE, LATEST_TABLE, REGISTRY_TABLE, tf_projection
from events import (
    CAREGIVER_EMAIL,
    CAREGIVER_NAME,
    RESIDENT_A,
    RESIDENT_B,
    WEARABLE,
    WEARABLE_2,
    ago,
    api_event,
    put_alert,
    put_bed_and_motion,
    put_device,
    put_resident,
    put_status,
    put_thresholds,
    put_vitals,
)

API_TF = Path(__file__).resolve().parents[3] / "infra" / "api.tf"
DROPPED_FROM_LATEST = {"received_at", "clock_skew", "message_id", "activity_pct"}
# Key attributes every index projects.
OPEN_KEYS = {"pk", "sk", "open_pk"}
BY_TYPE_KEYS = {"pk", "sk", "entity_type"}
BY_MESSAGE_KEYS = {"pk", "sk", "watch_type"}


def _policy() -> str:
    """Only the api_dashboard policy document: other routes reuse the same sids."""
    text = API_TF.read_text(encoding="utf-8")
    match = re.search(
        r'data "aws_iam_policy_document" "api_dashboard" \{(.*?)\n\}\n', text, re.DOTALL
    )
    assert match is not None, "api_dashboard policy document not found in api.tf"
    return match.group(1)


def _statement(sid: str) -> str:
    match = re.search(
        # The last statement ends the document, so it has no newline after its `}`.
        r'sid\s*=\s*"' + re.escape(sid) + r'"(.*?)\n  }(?:\n|\Z)',
        _policy(),
        re.DOTALL,
    )
    assert match is not None, f"{sid} statement not found in the api_dashboard policy"
    return match.group(1)


def _list(text: str, name: str) -> set[str]:
    match = re.search(name + r"\s*=\s*\[(.*?)\]", text, re.DOTALL)
    assert match is not None, f"no {name} in statement"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


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


def _top_level(expression: str, names: dict[str, str]) -> set[str]:
    """The top-level attribute of each path in a ProjectionExpression."""
    return {
        names.get(path.strip().split(".")[0], path.strip().split(".")[0])
        for path in expression.split(",")
    }


def _paths(expression: str, names: dict[str, str]) -> list[str]:
    return sorted(
        ".".join(names.get(part, part) for part in path.strip().split("."))
        for path in expression.split(",")
    )


def _requests(monkeypatch, *readers) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    monkeypatch.setattr(handler, "query_all", lambda r: requests.append(r) or [])
    for read in readers:
        read()
    return requests


def _read(request: dict[str, Any]) -> set[str]:
    names = request["ExpressionAttributeNames"]
    return _names(request["ProjectionExpression"], names) | _names(
        request["KeyConditionExpression"], names
    )


@pytest.fixture
def alerts_request(monkeypatch):
    (request,) = _requests(monkeypatch, handler.read_alerts)
    return request


@pytest.fixture
def by_type_requests(monkeypatch):
    return _requests(monkeypatch, handler.read_residents, handler.read_devices)


@pytest.fixture
def by_message_requests(monkeypatch):
    return _requests(monkeypatch, handler.read_statuses, handler.read_vitals)


@pytest.fixture
def batch_request(monkeypatch):
    seen: list[dict[str, Any]] = []

    class Client:
        def batch_get_item(self, RequestItems):
            seen.append(RequestItems)
            return {"Responses": {}}

    monkeypatch.setattr(handler, "dynamodb_client", lambda: Client())
    handler.batch_get_thresholds([RESIDENT_A, RESIDENT_B])
    (request,) = seen
    return request


# ---- alerts open (pattern 9) ----


def test_alerts_query_sends_select_specific_attributes(alerts_request):
    assert alerts_request["TableName"] == ALERTS_TABLE
    assert alerts_request["IndexName"] == "open"
    assert alerts_request["Select"] == "SPECIFIC_ATTRIBUTES"
    assert _condition_values("AlertsOpenIndex", "dynamodb:Select") == {
        "SPECIFIC_ATTRIBUTES"
    }


def test_alerts_query_reads_only_attributes_the_role_allows(alerts_request):
    allowed = _condition_values("AlertsOpenIndex", "dynamodb:Attributes")

    assert _read(alerts_request) <= allowed


def test_alerts_query_reads_only_attributes_the_index_projects(alerts_request):
    projected = set(tf_projection("open")["NonKeyAttributes"]) | OPEN_KEYS
    names = alerts_request["ExpressionAttributeNames"]

    assert _names(alerts_request["ProjectionExpression"], names) <= projected


def test_alerts_query_never_names_details(alerts_request):
    assert "details" not in alerts_request["ExpressionAttributeNames"].values()
    assert "details" not in _condition_values("AlertsOpenIndex", "dynamodb:Attributes")
    assert "details" not in tf_projection("open")["NonKeyAttributes"]


def test_alerts_statement_is_query_on_the_open_index_only():
    statement = _statement("AlertsOpenIndex")

    assert _list(statement, "actions") == {"dynamodb:Query"}
    assert "/index/open" in statement


def test_alerts_fixture_projects_the_terraform_list(aws):
    table = aws.ddb.describe_table(TableName=ALERTS_TABLE)["Table"]
    (index,) = table["GlobalSecondaryIndexes"]

    assert index["Projection"] == tf_projection("open")
    assert "notified_at" in index["Projection"]["NonKeyAttributes"]


def test_alerts_items_hold_only_the_requested_attributes(aws):
    put_alert(aws.ddb)

    (item,) = handler.read_alerts()

    assert set(item) <= {
        "pk",
        "sk",
        "alert_type",
        "device_id",
        "status",
        "event_ts",
        "severity",
        "acknowledged_at",
        "ack_note",
        "acknowledged_by_role",
        "acknowledged_by",
        "escalated_at",
        "notified_at",
    }
    for dropped in ("details", "received_at", "source_message_id", "open_pk"):
        assert dropped not in item


# ---- registry by-type (patterns 8 and 4) ----


def test_by_type_queries_send_select_specific_attributes(by_type_requests):
    assert len(by_type_requests) == 2
    for request in by_type_requests:
        assert request["TableName"] == REGISTRY_TABLE
        assert request["IndexName"] == "by-type"
        assert request["Select"] == "SPECIFIC_ATTRIBUTES"
    assert _condition_values("RegistryByTypeIndex", "dynamodb:Select") == {
        "SPECIFIC_ATTRIBUTES"
    }


def test_by_type_queries_read_only_attributes_the_role_allows(by_type_requests):
    allowed = _condition_values("RegistryByTypeIndex", "dynamodb:Attributes")

    for request in by_type_requests:
        assert _read(request) <= allowed, request["KeyConditionExpression"]


def test_by_type_queries_read_only_attributes_the_index_projects(by_type_requests):
    projected = set(tf_projection("by-type")["NonKeyAttributes"]) | BY_TYPE_KEYS

    for request in by_type_requests:
        names = request["ExpressionAttributeNames"]
        assert _names(request["ProjectionExpression"], names) <= projected


def test_by_type_queries_never_name_photo_key(by_type_requests):
    for request in by_type_requests:
        assert "photo_key" not in request["ExpressionAttributeNames"].values()
    assert "photo_key" not in _condition_values(
        "RegistryByTypeIndex", "dynamodb:Attributes"
    )
    assert "photo_key" in tf_projection("by-type")["NonKeyAttributes"]


def test_by_type_items_hold_only_the_requested_attributes(aws):
    put_resident(aws.ddb)
    put_device(aws.ddb, WEARABLE)

    residents = handler.read_residents()
    devices = handler.read_devices()

    assert [set(i) for i in residents] == [{"pk", "display_name", "home_id"}]
    assert [set(i) for i in devices] == [{"pk", "resident_id"}]


# ---- latest by-message (pattern 5) ----


def test_by_message_queries_only_status_and_vitals(aws):
    put_resident(aws.ddb)
    put_device(aws.ddb, WEARABLE)
    put_status(aws.ddb, WEARABLE)
    put_vitals(aws.ddb, WEARABLE)
    put_bed_and_motion(aws.ddb)

    handler.handle(api_event(), handler.datetime.now(handler.UTC))

    watch_types = [
        next(iter(q["ExpressionAttributeValues"].values()))["S"]
        for q in aws.queries()
        if q["IndexName"] == "by-message"
    ]
    assert sorted(watch_types) == ["status", "vitals"]


def test_by_message_queries_send_select_specific_attributes(by_message_requests):
    for request in by_message_requests:
        assert request["TableName"] == LATEST_TABLE
        assert request["IndexName"] == "by-message"
        assert request["Select"] == "SPECIFIC_ATTRIBUTES"


def test_by_message_queries_read_only_what_the_index_projects(by_message_requests):
    projected = set(tf_projection("by-message")["NonKeyAttributes"]) | BY_MESSAGE_KEYS

    for request in by_message_requests:
        names = request["ExpressionAttributeNames"]
        assert _top_level(request["ProjectionExpression"], names) == {"pk", "latest"}
        assert {"pk", "latest"} <= projected


def test_by_message_queries_never_name_the_dropped_fields(by_message_requests):
    for request in by_message_requests:
        names = request["ExpressionAttributeNames"]
        assert set(names.values()).isdisjoint(DROPPED_FROM_LATEST | {"entries"})


def test_status_projection_is_exactly_the_card_fields(monkeypatch):
    (request,) = _requests(monkeypatch, handler.read_statuses)

    assert _paths(
        request["ProjectionExpression"], request["ExpressionAttributeNames"]
    ) == sorted(
        ["pk", "latest.ts", "latest.battery_pct", "latest.worn", "latest.charging"]
    )


def test_vitals_projection_is_exactly_the_card_fields(monkeypatch):
    (request,) = _requests(monkeypatch, handler.read_vitals)

    assert _paths(
        request["ProjectionExpression"], request["ExpressionAttributeNames"]
    ) == sorted(
        ["pk", "latest.ts", "latest.hr_bpm", "latest.spo2_pct", "latest.skin_temp_c"]
    )
    assert "activity_pct" not in request["ExpressionAttributeNames"].values()


def test_by_message_items_hold_only_the_projected_latest_fields(aws):
    put_status(aws.ddb, WEARABLE)
    put_vitals(aws.ddb, WEARABLE_2)

    statuses = handler.read_statuses()
    vitals = handler.read_vitals()

    for item in statuses + vitals:
        assert set(item) == {"pk", "latest"}
        assert set(item["latest"]).isdisjoint(DROPPED_FROM_LATEST)
    assert set(statuses[0]["latest"]) == {"ts", "battery_pct", "worn", "charging"}
    assert set(vitals[0]["latest"]) == {"ts", "hr_bpm", "spo2_pct", "skin_temp_c"}


# ---- registry THRESHOLDS# (BatchGetItem) ----


def test_thresholds_batch_get_reads_only_attributes_the_role_allows(batch_request):
    allowed = _condition_values("RegistryThresholds", "dynamodb:Attributes")
    ((_, spec),) = batch_request.items()

    read = _names(spec["ProjectionExpression"], spec["ExpressionAttributeNames"])
    assert read <= allowed
    assert read == {"pk", "hr_bpm", "spo2_pct", "skin_temp_c"}
    assert "updated_by" not in allowed


def test_thresholds_batch_get_reads_only_thresholds_partitions(batch_request):
    assert _condition_values("RegistryThresholds", "dynamodb:LeadingKeys") == {
        "THRESHOLDS#*"
    }
    ((table, spec),) = batch_request.items()

    assert table == REGISTRY_TABLE
    assert [k["pk"]["S"] for k in spec["Keys"]] == [
        f"THRESHOLDS#{RESIDENT_A}",
        f"THRESHOLDS#{RESIDENT_B}",
    ]
    assert all(k["sk"]["S"] == "THRESHOLDS" for k in spec["Keys"])


def test_thresholds_statement_allows_select_only_as_specific_attributes(batch_request):
    """BatchGetItem has no Select, so StringEqualsIfExists lets it through; the
    condition still blocks any request that would ask for ALL_ATTRIBUTES."""
    assert "StringEqualsIfExists" in _statement("RegistryThresholds")
    assert _condition_values("RegistryThresholds", "dynamodb:Select") == {
        "SPECIFIC_ATTRIBUTES"
    }
    ((_, spec),) = batch_request.items()
    assert spec.get("Select", "SPECIFIC_ATTRIBUTES") == "SPECIFIC_ATTRIBUTES"
    assert _list(_statement("RegistryThresholds"), "actions") == {
        "dynamodb:BatchGetItem"
    }


def test_thresholds_items_hold_only_the_limits(aws):
    put_thresholds(aws.ddb, RESIDENT_A, spo2_pct={"min": Decimal(88)})

    found = handler.batch_get_thresholds([RESIDENT_A])

    assert set(found[RESIDENT_A]) == {"pk", "spo2_pct"}


# ---- Cognito ----


def test_cognito_statement_allows_admin_get_user_only():
    assert _list(_statement("CognitoNames"), "actions") == {"cognito-idp:AdminGetUser"}
    assert "ListUsers" not in _policy()


def test_handler_cognito_calls_are_admin_get_user_only(aws):
    caregiver = aws.add_user(CAREGIVER_EMAIL, CAREGIVER_NAME)
    put_alert(
        aws.ddb,
        alert_type="battery_low",
        status="acknowledged",
        event_ts=ago(minutes=3),
        acknowledged_by=caregiver,
        acknowledged_by_role="caregiver",
    )

    handler.handle(api_event(), handler.datetime.now(handler.UTC))

    assert aws.cognito_ops() == ["admin_get_user"]


# ---- the whole policy ----


def test_policy_has_no_write_actions():
    policy = _policy()

    for action in (
        "PutItem",
        "UpdateItem",
        "DeleteItem",
        "BatchWriteItem",
        "TransactWriteItems",
        "Scan",
        '"dynamodb:GetItem"',
        "AdminUpdateUserAttributes",
    ):
        assert action not in policy, action
