"""GET /residents/{resident_id}/vitals (docs/features/vitals-trends.md, criteria 1-14)."""

from __future__ import annotations

import json
import time

import handler
import pytest
import vitals_sql
from conftest import REGISTRY_TABLE
from events import (
    BEDROOM,
    NOW,
    RESIDENT,
    WEARABLE,
    YESTERDAY,
    event,
    put_device,
    put_thresholds,
)

FAR = time.monotonic() + 3600
DAY_COLUMNS = ["date", *vitals_sql.SUMMARY_COLUMNS, *vitals_sql.THRESHOLD_COLUMNS]
HOUR_COLUMNS = ["hour_utc", *vitals_sql.SUMMARY_COLUMNS]


def call(ev, deadline=FAR):
    result = handler.handle(ev, NOW, deadline)
    return result["statusCode"], json.loads(result["body"])


def day_row(date: str, **values) -> dict:
    row = dict.fromkeys(DAY_COLUMNS)
    row.update({c: "0" for c in vitals_sql.SUMMARY_COLUMNS})
    row.update(
        date=date,
        hr_bpm_avg="71.44",
        hr_bpm_max="104",
        vitals_readings="1302",
        thr_hr_bpm_min="50",
        thr_hr_bpm_max="120",
        thr_spo2_pct_min="88",
        thr_skin_temp_c_min="30.0",
        thr_skin_temp_c_max="38.0",
    )
    row.update(values)
    return row


# ---- access and validation (criteria 1-5) ----


@pytest.mark.parametrize(
    "groups", ["[nurse]", "[caregiver]", "[ops]", "", None, ["family"]]
)
def test_non_doctor_gets_403_before_any_read(aws, groups):
    _, athena = aws
    status, body = call(event(groups=groups, resident_id="not-valid"))
    assert status == 403
    assert body["error"]["code"] == "forbidden"
    assert athena.queries == []


def test_groups_as_a_list_are_accepted(aws):
    status, _ = call(event(groups=["nurse", "doctor"]))
    assert status == 200


@pytest.mark.parametrize(
    "params",
    [
        {"interval": "minute", "from": "2026-11-17", "to": YESTERDAY},
        {"interval": "day", "from": "2026-11-17"},
        {"interval": "day", "from": "2026-02-30", "to": YESTERDAY},
        {"interval": "day", "from": "17-11-2026", "to": YESTERDAY},
        {"interval": "day", "from": "2026-11-18", "to": "2026-11-17"},
        {"interval": "day", "from": "2026-11-17'; DROP", "to": YESTERDAY},
        None,
    ],
)
def test_invalid_parameters_are_400_without_aws_calls(aws, params):
    _, athena = aws
    ev = event(params=params)
    if params is None:
        ev["queryStringParameters"] = None
    status, body = call(ev)
    assert status == 400
    assert body["error"]["code"] == "invalid_request"
    assert athena.queries == []


@pytest.mark.parametrize("resident_id", ["res-3", "RES-0003", "res-0003 ", None, 3])
def test_invalid_resident_id_is_400(aws, resident_id):
    status, body = call(event(resident_id=resident_id))
    assert (status, body["error"]["code"]) == (400, "invalid_request")


def test_today_is_out_of_range_and_yesterday_is_allowed(aws):
    status, body = call(
        event(params={"interval": "day", "from": YESTERDAY, "to": "2026-11-19"})
    )
    assert (status, body["error"]["code"]) == (400, "out_of_range")
    status, _ = call(
        event(params={"interval": "day", "from": YESTERDAY, "to": YESTERDAY})
    )
    assert status == 200


@pytest.mark.parametrize(
    ("interval", "start", "ok"),
    [
        ("day", "2026-08-21", True),  # 90 days
        ("day", "2026-08-20", False),  # 91 days
        ("hour", "2026-11-12", True),  # 7 days
        ("hour", "2026-11-11", False),  # 8 days
    ],
)
def test_range_limits(aws, interval, start, ok):
    status, body = call(
        event(params={"interval": interval, "from": start, "to": YESTERDAY})
    )
    if ok:
        assert status == 200
    else:
        assert (status, body["error"]["code"]) == (400, "range_too_long")


def test_hourly_only_covers_the_last_365_days(aws):
    params = {"interval": "hour", "from": "2025-11-18", "to": "2025-11-18"}
    status, body = call(event(params=params))
    assert (status, body["error"]["code"]) == (400, "out_of_range")
    params = {"interval": "hour", "from": "2025-11-19", "to": "2025-11-19"}
    assert call(event(params=params))[0] == 200


def test_daily_has_no_age_limit(aws):
    params = {"interval": "day", "from": "2024-01-01", "to": "2024-01-02"}
    assert call(event(params=params))[0] == 200


# ---- daily rows (criteria 7-8) ----


def test_daily_rows_one_per_date_with_gaps(aws):
    _, athena = aws
    athena.columns = DAY_COLUMNS
    athena.rows = [day_row("2026-11-16")]
    params = {"interval": "day", "from": "2026-11-16", "to": YESTERDAY}
    status, body = call(event(params=params))
    assert status == 200
    assert [r["date"] for r in body["rows"]] == ["2026-11-16", "2026-11-17", YESTERDAY]
    first, gap = body["rows"][0], body["rows"][1]
    assert first["has_data"] is True
    assert first["hr_bpm_avg"] == 71.4
    assert first["hr_bpm_max"] == 104
    assert first["thresholds"] == {
        "hr_bpm": {"min": 50, "max": 120},
        "spo2_pct": {"min": 88},
        "skin_temp_c": {"min": 30.0, "max": 38.0},
    }
    assert gap["has_data"] is False
    assert gap["hr_bpm_avg"] is None and gap["falls"] is None
    assert body["thresholds"] is None
    assert "resident_id" not in first and "generated_at" not in first


def test_daily_query_names_the_resident_and_dates(aws):
    _, athena = aws
    call(event(params={"interval": "day", "from": "2026-11-16", "to": YESTERDAY}))
    (query,) = athena.queries
    assert "resident_id = 'res-0003'" in query
    assert "BETWEEN '2026-11-16' AND '2026-11-18'" in query
    assert athena.requests[0]["WorkGroup"] == "silvercare-vitals"


def test_newest_summary_wins_for_a_date(aws):
    _, athena = aws
    athena.columns = DAY_COLUMNS
    athena.rows = [day_row(YESTERDAY, falls="1"), day_row(YESTERDAY, falls="2")]
    _, body = call(
        event(params={"interval": "day", "from": YESTERDAY, "to": YESTERDAY})
    )
    assert body["rows"][0]["falls"] == 2


def test_bad_row_is_skipped_not_fatal(aws):
    _, athena = aws
    athena.columns = DAY_COLUMNS
    athena.rows = [day_row(YESTERDAY, falls="1.5"), day_row("2026-11-17")]
    status, body = call(event())
    assert status == 200
    assert [r["has_data"] for r in body["rows"]] == [True, False]


def test_unknown_resident_returns_empty_rows(aws):
    status, body = call(event(resident_id="res-0999"))
    assert status == 200
    assert all(r["has_data"] is False for r in body["rows"])


# ---- hourly rows (criteria 9-13) ----


def hourly_params(day: str = YESTERDAY) -> dict:
    return {"interval": "hour", "from": day, "to": day}


def test_hourly_without_devices_runs_no_query(aws):
    _, athena = aws
    status, body = call(event(params=hourly_params()))
    assert status == 200
    assert athena.queries == []
    assert len(body["rows"]) == 24
    assert all(r["has_data"] is False for r in body["rows"])
    assert body["thresholds"]["spo2_pct"] == {"min": 92}  # the defaults


def test_hourly_rows_are_singapore_hours_with_current_thresholds(aws):
    client, athena = aws
    put_device(client, REGISTRY_TABLE, WEARABLE)
    put_device(client, REGISTRY_TABLE, BEDROOM)
    put_thresholds(client, REGISTRY_TABLE)
    athena.columns = HOUR_COLUMNS
    # 2026-11-18 14:00 SGT is 06:00 UTC.
    row = dict.fromkeys(HOUR_COLUMNS)
    row.update(
        {c: "0" for c in vitals_sql.SUMMARY_COLUMNS}, hour_utc="2026-11-18 06:00:00"
    )
    row.update(vitals_readings="60", hr_bpm_avg="70.0", bed_minutes=None)
    athena.rows = [row]
    status, body = call(event(params=hourly_params()))
    assert status == 200
    rows = body["rows"]
    assert rows[0]["hour"] == "2026-11-18T00:00:00+08:00"
    assert rows[-1]["hour"] == "2026-11-18T23:00:00+08:00"
    assert rows[14]["hour"] == "2026-11-18T14:00:00+08:00"
    assert rows[14]["has_data"] is True and rows[14]["vitals_readings"] == 60
    assert rows[13]["has_data"] is False
    assert body["thresholds"]["spo2_pct"] == {"min": 88}
    (query,) = athena.queries
    assert "'wearable-0003'" in query and "'bedroom-0003'" in query


def test_hourly_uses_only_devices_still_assigned_to_the_resident(aws):
    client, athena = aws
    put_device(client, REGISTRY_TABLE, WEARABLE)
    # The index still lists the bedroom unit, but its base item now names another resident.
    put_device(client, REGISTRY_TABLE, BEDROOM)
    client.update_item(
        TableName=REGISTRY_TABLE,
        Key={"pk": {"S": f"DEVICE#{BEDROOM}"}, "sk": {"S": "ASSIGNMENT"}},
        UpdateExpression="SET resident_id = :r",
        ExpressionAttributeValues={":r": {"S": "res-0004"}},
    )
    call(event(params=hourly_params()))
    (query,) = athena.queries
    assert "'wearable-0003'" in query and "'bedroom-0003'" not in query


def test_hourly_bed_state_alone_counts_as_data(aws):
    values = dict.fromkeys(vitals_sql.SUMMARY_COLUMNS)
    values.update(
        vitals_readings=0,
        motion_events=0,
        falls=0,
        vitals_alert_episodes=0,
        cancels=0,
        bed_minutes=0,
    )
    assert handler._has_data(values) is True
    values["bed_minutes"] = None
    assert handler._has_data(values) is False


# ---- running the query (criterion 14) ----


def test_slow_query_is_stopped_and_returns_504(aws):
    _, athena = aws
    athena.running_polls = 10**6
    deadline = time.monotonic() - 1
    with pytest.raises(handler.QueryTimeoutError):
        handler.handle(event(), NOW, deadline)
    assert athena.stopped == ["query-test-0001"]


def test_lambda_handler_maps_timeout_to_504(aws, monkeypatch):
    _, athena = aws
    athena.running_polls = 10**6
    monkeypatch.setattr(handler, "QUERY_DEADLINE_S", -1)
    result = handler.lambda_handler(event(), None)
    assert result["statusCode"] == 504
    assert json.loads(result["body"])["error"]["code"] == "query_timeout"


def test_failed_query_returns_500_without_its_reason(aws, logs):
    _, athena = aws
    athena.state = "FAILED"
    result = handler.lambda_handler(event(), None)
    assert result["statusCode"] == 500
    assert "leaked" not in result["body"]
    assert "leaked" not in json.dumps(logs())


def test_logs_hold_no_values_or_sql(aws, logs):
    client, athena = aws
    put_device(client, REGISTRY_TABLE, WEARABLE)
    athena.columns = HOUR_COLUMNS
    row = dict.fromkeys(HOUR_COLUMNS)
    row.update(
        {c: "0" for c in vitals_sql.SUMMARY_COLUMNS}, hour_utc="2026-11-18 06:00:00"
    )
    row.update(hr_bpm_max="187")
    athena.rows = [row]
    handler.lambda_handler(event(params=hourly_params()), None)
    lines = logs()
    assert lines
    for line in lines:
        text = json.dumps(line)
        assert "187" not in text and "SELECT" not in text
    ok = [line for line in lines if line.get("outcome") == "ok"]
    assert ok and ok[0]["resident_id"] == RESIDENT and ok[0]["interval"] == "hour"
