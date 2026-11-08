"""Privacy: no health alert ever reaches an ops response (docs/data-model.md, the
**ops** paragraph). ops see only `device_offline`, `battery_low`, `not_worn`, filtered
server-side. A failure here is a privacy breach, not a display bug.

Three layers are tested separately, so each one would catch the leak on its own:

1. the `open` index Query carries the FilterExpression on every page;
2. the handler drops anything that isn't a device problem even if the Query returned
   it (the FilterExpression removed or broken), and never reads its base item;
3. problems.build drops an item if the base-table copy isn't a device problem.
"""

from __future__ import annotations

import json
from decimal import Decimal

import handler
import problems
import pytest
from events import (
    BATTERY,
    HEALTH_ACK_NOTE,
    HEALTH_DETAILS_SENTINEL,
    HEALTH_IDS,
    HEALTH_TYPES,
    NOT_WORN,
    OFFLINE,
    RESIDENT_A,
    WEARABLE,
    api_event,
    put_alert,
    put_device_problems,
    put_health_alerts,
    ts,
)

DEVICE_IDS = {OFFLINE, BATTERY, NOT_WORN}


def _call(event=None):
    result = handler.lambda_handler(event or api_event(), None)
    return result["statusCode"], json.loads(result["body"]), result["body"]


def _assert_no_health_data(raw_body: str) -> None:
    for alert_id in HEALTH_IDS:
        assert alert_id not in raw_body
    for alert_type in HEALTH_TYPES:
        assert f'"{alert_type}"' not in raw_body
    assert HEALTH_DETAILS_SENTINEL not in raw_body
    assert HEALTH_ACK_NOTE not in raw_body
    assert "ack_note" not in raw_body


# ---- end to end ----


def test_mixed_open_list_returns_only_device_problems(aws):
    put_health_alerts(aws.ddb, RESIDENT_A)
    put_health_alerts(aws.ddb, "res-0002")
    put_device_problems(aws.ddb)

    status, body, raw = _call()

    assert status == 200
    assert {a["alert_id"] for a in body["alerts"]} == DEVICE_IDS
    assert {a["alert_type"] for a in body["alerts"]} <= set(
        problems.DEVICE_PROBLEM_TYPES
    )
    _assert_no_health_data(raw)


def test_only_health_alerts_gives_an_empty_list(aws):
    put_health_alerts(aws.ddb)

    status, body, raw = _call()

    assert status == 200
    assert body == {"alerts": []}
    _assert_no_health_data(raw)
    # No device problem, so no base-table read at all.
    assert aws.batch_gets() == []


def test_unknown_alert_type_is_not_shown(aws):
    """A type added later (not a device problem) must not reach ops by default."""
    put_alert(aws.ddb, alert_id=OFFLINE, alert_type="some_future_type")

    status, body, _ = _call()

    assert status == 200
    assert body == {"alerts": []}


# ---- layer 1: the FilterExpression ----


def test_every_query_page_has_the_device_problem_filter(aws):
    put_health_alerts(aws.ddb)
    put_device_problems(aws.ddb)
    aws.client.page_limit = 2

    status, body, raw = _call()

    queries = aws.queries()
    assert status == 200
    assert len(queries) > 1  # several pages
    for query in queries:
        assert query["IndexName"] == "open"
        expression = query["FilterExpression"]
        names = query["ExpressionAttributeNames"]
        values = query["ExpressionAttributeValues"]
        assert expression == "#type IN (:offline, :battery, :not_worn)"
        assert names["#type"] == "alert_type"
        assert {values[k]["S"] for k in (":offline", ":battery", ":not_worn")} == set(
            problems.DEVICE_PROBLEM_TYPES
        )
    assert {a["alert_id"] for a in body["alerts"]} == DEVICE_IDS
    _assert_no_health_data(raw)


# ---- layer 2: the handler's own check, if the filter ever failed ----


def _without_filter(aws):
    """Make the Query behave as if it had no FilterExpression: return every open
    alert, health alerts included (projected as the index would)."""

    def everything(_kwargs, _result):
        scan = aws.ddb.scan(TableName="silvercare-alerts")
        items = [
            {
                k: v
                for k, v in item.items()
                if k not in ("details", "ack_note", "source_message_id")
            }
            for item in scan["Items"]
            if "open_pk" in item
        ]
        return {"Items": items, "Count": len(items)}

    aws.client.override["query"] = everything


def test_health_alerts_returned_by_a_broken_filter_are_dropped(aws, logs):
    put_health_alerts(aws.ddb)
    put_device_problems(aws.ddb)
    _without_filter(aws)

    status, body, raw = _call()

    assert status == 200
    assert {a["alert_id"] for a in body["alerts"]} == DEVICE_IDS
    _assert_no_health_data(raw)
    ok = [r for r in logs() if r.get("outcome") == "ok"]
    assert ok[0]["not_device_problems"] == len(HEALTH_IDS)


def test_health_alerts_are_never_read_from_the_base_table(aws):
    """Their `details` (vitals, fall data) must not even be fetched."""
    put_health_alerts(aws.ddb)
    put_device_problems(aws.ddb)
    _without_filter(aws)

    _call()

    requested = [
        key["sk"]["S"]
        for call in aws.batch_gets()
        for table in call["RequestItems"].values()
        for key in table["Keys"]
    ]
    assert requested
    assert all(sk.rsplit("#", 1)[1] in DEVICE_IDS for sk in requested)


# ---- layer 3: problems.build ----


def _index_item(alert_id: str, alert_type: str) -> dict:
    return {
        "pk": f"RESIDENT#{RESIDENT_A}",
        "sk": f"ALERT#{ts(10)}#{alert_id}",
        "alert_type": alert_type,
        "device_id": WEARABLE,
        "status": "open",
        "event_ts": ts(10),
        "severity": "warning",
    }


@pytest.mark.parametrize("alert_type", HEALTH_TYPES)
def test_build_drops_every_health_type(alert_type):
    built = problems.build([_index_item(HEALTH_IDS[0], alert_type)], {}, {})
    assert built.alerts == []
    assert built.not_device_problems == 1


@pytest.mark.parametrize(
    "alert_type", [None, 7, "", "DEVICE_OFFLINE", "device_offline "]
)
def test_build_drops_missing_or_odd_types(alert_type):
    built = problems.build([_index_item(OFFLINE, alert_type)], {}, {})
    assert built.alerts == []


def test_build_drops_an_alert_whose_base_item_is_a_health_alert():
    item = _index_item(OFFLINE, "device_offline")
    base = {
        (item["pk"], item["sk"]): {
            "alert_type": "fall",
            "details": {"text": HEALTH_DETAILS_SENTINEL, "peak_g": Decimal("3.8")},
        }
    }

    built = problems.build([item], base, {})

    assert built.alerts == []
    assert built.not_device_problems == 1
