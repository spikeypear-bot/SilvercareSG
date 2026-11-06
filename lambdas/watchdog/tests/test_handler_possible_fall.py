"""Possible-fall runs against moto (docs/features/possible-fall.md, criteria 1, 3a,
4-7, and 17): the bed, motion, and TEST_MODE reads, the alert and back-in-bed resolve
sent to the alerts queue, the summary counts, and logs without times, worn state, or
details.

"Night now" is 2026-11-05 02:30:00 SGT; the bed was vacated at 02:10:00 SGT.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import checks
import handler
from checks import POSSIBLE_FALL
from events import (
    ALERT_ID,
    ALERT_ID_2,
    BED_ID,
    BEDROOM,
    BEDROOM_2,
    MOTION_ID,
    RESIDENT,
    RESIDENT_2,
    STATUS_ID,
    WEARABLE,
    iso,
    put_alert,
    put_bed,
    put_device,
    put_motion,
    put_status,
    put_test_mode,
    sgt,
)

NIGHT_NOW = sgt(2, 30)
VACATED = iso(sgt(2, 10))
MOTION_AT = iso(sgt(2, 11))
BACK_AT = iso(sgt(2, 25))
DAY_NOW = sgt(12, 0)
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ZERO = {t: 0 for t in checks.WATCHDOG_TYPES}


def counts(**kw) -> dict[str, int]:
    return {**ZERO, **kw}


def unit(aws, device_id=BEDROOM, resident_id=RESIDENT, now=NIGHT_NOW):
    """An assigned bedroom unit with a fresh heartbeat."""
    put_device(aws.ddb, device_id, resident_id)
    at = iso(now - timedelta(minutes=1))
    put_status(aws.ddb, device_id, ts=at, received_at=at)


def put_pf_alert(aws, status="open", device_id=BEDROOM, resident_id=RESIDENT, **kw):
    return put_alert(
        aws.ddb,
        POSSIBLE_FALL,
        device_id,
        resident_id,
        alert_id=kw.pop("alert_id", ALERT_ID),
        event_ts=kw.pop("event_ts", VACATED),
        status=status,
        **kw,
    )


def run_handler(monkeypatch, now: datetime = NIGHT_NOW):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(handler, "datetime", FixedDatetime)
    return handler.lambda_handler({"source": "aws.events"}, None)


# ======== raising (criteria 3-4) ========


def test_night_possible_fall_sends_one_critical_alert(aws):
    unit(aws)
    put_bed(aws.ddb, BEDROOM, False, VACATED)
    put_motion(aws.ddb, BEDROOM, False, MOTION_AT)

    summary = handler.run(NIGHT_NOW)

    (send,) = aws.sends()
    assert send["MessageAttributes"] == {
        "alert_type": {"DataType": "String", "StringValue": POSSIBLE_FALL}
    }
    assert json.loads(send["MessageBody"]) == {
        "schema_version": 1,
        "alert_id": checks.alert_id_for(VACATED, POSSIBLE_FALL, "critical", BEDROOM),
        "alert_type": POSSIBLE_FALL,
        "severity": "critical",
        "resident_id": RESIDENT,
        "device_id": BEDROOM,
        "event_ts": VACATED,
        "received_at": iso(NIGHT_NOW),
        "source_message_id": BED_ID,
        "details": {"out_of_bed_since": VACATED, "wearable": "no_wearable"},
    }
    assert summary["raised"] == counts(possible_fall=1)
    assert summary["test_mode_units"] == 0


def test_bedroom_unit_without_any_status_is_checked(aws):
    put_device(aws.ddb, BEDROOM, RESIDENT)
    put_bed(aws.ddb, BEDROOM, False, VACATED)

    handler.run(NIGHT_NOW)

    assert [m["alert_type"] for m in aws.alerts()] == [POSSIBLE_FALL]


def test_motion_true_sends_nothing(aws):
    unit(aws)
    put_bed(aws.ddb, BEDROOM, False, VACATED)
    put_motion(aws.ddb, BEDROOM, True, MOTION_AT)

    handler.run(NIGHT_NOW)

    assert aws.sends() == []


def test_worn_wearable_of_the_resident_sends_nothing(aws):
    unit(aws)
    put_device(aws.ddb, WEARABLE, RESIDENT)
    at = iso(NIGHT_NOW - timedelta(minutes=2))
    put_status(aws.ddb, WEARABLE, ts=at, received_at=at, worn=True)
    put_bed(aws.ddb, BEDROOM, False, VACATED)

    handler.run(NIGHT_NOW)

    assert aws.sends() == []


def test_recent_off_wrist_wearable_alerts_with_not_worn(aws):
    unit(aws)
    put_device(aws.ddb, WEARABLE, RESIDENT)
    at = iso(NIGHT_NOW - timedelta(minutes=2))
    put_status(aws.ddb, WEARABLE, ts=at, received_at=at, worn=False)
    put_bed(aws.ddb, BEDROOM, False, VACATED)

    handler.run(NIGHT_NOW)

    (message,) = aws.alerts()
    assert message["details"]["wearable"] == "not_worn"


def test_unassigned_bedroom_unit_sends_nothing(aws):
    put_device(aws.ddb, BEDROOM, resident_id=None)
    put_status(aws.ddb, BEDROOM, ts=iso(NIGHT_NOW), received_at=iso(NIGHT_NOW))
    put_bed(aws.ddb, BEDROOM, False, VACATED)

    handler.run(NIGHT_NOW)

    assert aws.sends() == []


def test_offline_bedroom_unit_gets_only_its_offline_alert(aws):
    put_device(aws.ddb, BEDROOM, RESIDENT)
    old = iso(NIGHT_NOW - timedelta(minutes=30))
    put_status(aws.ddb, BEDROOM, ts=old, received_at=old)
    put_bed(aws.ddb, BEDROOM, False, VACATED)

    handler.run(NIGHT_NOW)

    assert [m["alert_type"] for m in aws.alerts()] == ["device_offline"]


def test_open_possible_fall_of_the_unit_blocks_a_second(aws):
    unit(aws)
    put_bed(aws.ddb, BEDROOM, False, VACATED)
    put_pf_alert(aws, event_ts=iso(sgt(1, 0)))

    handler.run(NIGHT_NOW)

    assert aws.sends() == []


def test_acknowledged_possible_fall_also_blocks_a_second(aws):
    unit(aws)
    put_bed(aws.ddb, BEDROOM, False, VACATED)
    put_pf_alert(aws, status="acknowledged", event_ts=iso(sgt(1, 0)))

    handler.run(NIGHT_NOW)

    assert aws.sends() == []


def test_two_runs_send_the_same_alert_id(aws):
    unit(aws)
    put_bed(aws.ddb, BEDROOM, False, VACATED)

    handler.run(NIGHT_NOW)
    handler.run(NIGHT_NOW + timedelta(minutes=5))

    first, second = aws.alerts()
    assert first["alert_id"] == second["alert_id"]


def test_skewed_bed_entry_uses_received_at(aws):
    unit(aws)
    put_bed(
        aws.ddb,
        BEDROOM,
        False,
        iso(sgt(12, 0)),
        received_at=VACATED,
        clock_skew=True,
    )

    handler.run(NIGHT_NOW)

    (message,) = aws.alerts()
    assert message["event_ts"] == VACATED


def test_possible_fall_is_not_grouped_with_five_offline_devices(aws):
    for i in range(1, 6):
        put_device(aws.ddb, f"wearable-{i:04d}", f"res-{i:04d}")
        old = iso(NIGHT_NOW - timedelta(minutes=30))
        put_status(aws.ddb, f"wearable-{i:04d}", ts=old, received_at=old)
    unit(aws, BEDROOM, "res-0009")
    put_bed(aws.ddb, BEDROOM, False, VACATED)

    summary = handler.run(NIGHT_NOW)

    (fall,) = [m for m in aws.alerts() if m["alert_type"] == POSSIBLE_FALL]
    assert "grouped" not in fall["details"]
    assert summary["grouped"] is True
    assert "bedroom" not in aws.publishes()[0]["Message"]


# ======== test mode (criterion 3a) ========


def test_test_mode_item_allows_a_day_alert_after_2_minutes(aws):
    unit(aws, now=DAY_NOW)
    put_bed(aws.ddb, BEDROOM, False, iso(DAY_NOW - timedelta(minutes=2)))
    put_test_mode(aws.ddb, BEDROOM, iso(DAY_NOW - timedelta(minutes=10)))

    summary = handler.run(DAY_NOW)

    (message,) = aws.alerts()
    assert message["details"]["test_mode"] is True
    assert summary["test_mode_units"] == 1


def test_test_mode_item_exactly_2_hours_old_still_counts(aws):
    unit(aws, now=DAY_NOW)
    put_bed(aws.ddb, BEDROOM, False, iso(DAY_NOW - timedelta(minutes=3)))
    put_test_mode(aws.ddb, BEDROOM, iso(DAY_NOW - timedelta(hours=2)))

    handler.run(DAY_NOW)

    assert len(aws.alerts()) == 1


def test_expired_test_mode_item_is_ignored(aws):
    unit(aws, now=DAY_NOW)
    put_bed(aws.ddb, BEDROOM, False, iso(DAY_NOW - timedelta(minutes=3)))
    put_test_mode(aws.ddb, BEDROOM, iso(DAY_NOW - timedelta(hours=2, milliseconds=1)))

    summary = handler.run(DAY_NOW)

    assert aws.sends() == []
    assert summary["test_mode_units"] == 0


def test_future_test_mode_item_is_ignored(aws):
    unit(aws, now=DAY_NOW)
    put_bed(aws.ddb, BEDROOM, False, iso(DAY_NOW - timedelta(minutes=3)))
    put_test_mode(aws.ddb, BEDROOM, iso(DAY_NOW + timedelta(minutes=1)))

    assert handler.run(DAY_NOW)["test_mode_units"] == 0
    assert aws.sends() == []


def test_test_mode_item_for_a_wearable_is_ignored(aws):
    put_test_mode(aws.ddb, WEARABLE, iso(DAY_NOW - timedelta(minutes=1)))

    assert handler.run(DAY_NOW)["test_mode_units"] == 0


def test_test_mode_affects_only_the_named_unit(aws):
    unit(aws, BEDROOM, RESIDENT, now=DAY_NOW)
    unit(aws, BEDROOM_2, RESIDENT_2, now=DAY_NOW)
    vacated = iso(DAY_NOW - timedelta(minutes=5))
    put_bed(aws.ddb, BEDROOM, False, vacated)
    put_bed(aws.ddb, BEDROOM_2, False, vacated)
    put_test_mode(aws.ddb, BEDROOM, iso(DAY_NOW - timedelta(minutes=10)))

    handler.run(DAY_NOW)

    assert [m["device_id"] for m in aws.alerts()] == [BEDROOM]


def test_test_mode_item_is_read_from_the_registry_by_type_index(aws, monkeypatch):
    real = handler.dynamodb_client()
    calls: list[dict] = []

    class Spy:
        def __getattr__(self, op):
            method = getattr(real, op)

            def wrapper(**kw):
                calls.append((op, kw))
                return method(**kw)

            return wrapper

    monkeypatch.setattr(handler, "dynamodb_client", lambda: Spy())

    handler.run(DAY_NOW)

    (query,) = [
        kw
        for op, kw in calls
        if op == "query" and kw["ExpressionAttributeValues"].get(":test_mode")
    ]
    assert query["IndexName"] == "by-type"
    assert query["ExpressionAttributeValues"] == {":test_mode": {"S": "TEST_MODE"}}
    assert "ConsistentRead" not in query


# ======== back in bed (criterion 7) ========


def test_back_in_bed_sends_a_system_resolve_with_reason(aws):
    unit(aws)
    put_pf_alert(aws)
    put_bed(aws.ddb, BEDROOM, True, BACK_AT)

    summary = handler.run(NIGHT_NOW)

    (send,) = aws.sends()
    assert set(send) == {"QueueUrl", "MessageBody"}
    assert json.loads(send["MessageBody"]) == {
        "kind": "status_update",
        "schema_version": 1,
        "resident_id": RESIDENT,
        "new_status": "resolved",
        "changed_at": iso(NIGHT_NOW),
        "by_role": "system",
        "by": None,
        "by_name": None,
        "target": {"alert_id": ALERT_ID, "event_ts": VACATED},
        "reason": "back_in_bed",
    }
    assert summary["resolved"] == counts(possible_fall=1)


def test_back_in_bed_resolves_an_acknowledged_possible_fall(aws):
    unit(aws)
    put_pf_alert(aws, status="acknowledged")
    put_bed(aws.ddb, BEDROOM, True, BACK_AT)

    handler.run(NIGHT_NOW)

    (resolve,) = aws.resolves()
    assert resolve["reason"] == "back_in_bed"


def test_back_in_bed_before_out_of_bed_since_does_not_resolve(aws):
    unit(aws)
    put_pf_alert(aws)
    put_bed(aws.ddb, BEDROOM, True, iso(sgt(2, 9)))

    handler.run(NIGHT_NOW)

    assert aws.sends() == []


def test_back_in_bed_of_a_unit_reassigned_to_another_resident_sends_nothing(aws):
    unit(aws, BEDROOM, RESIDENT_2)
    put_pf_alert(aws, resident_id=RESIDENT)
    put_bed(aws.ddb, BEDROOM, True, BACK_AT)

    handler.run(NIGHT_NOW)

    assert aws.sends() == []


def test_back_in_bed_of_an_unassigned_unit_sends_nothing(aws):
    put_device(aws.ddb, BEDROOM, resident_id=None)
    put_pf_alert(aws)
    put_bed(aws.ddb, BEDROOM, True, BACK_AT)

    handler.run(NIGHT_NOW)

    assert aws.sends() == []


def test_possible_fall_of_an_offline_unit_stays_open(aws):
    put_device(aws.ddb, BEDROOM, RESIDENT)
    old = iso(NIGHT_NOW - timedelta(minutes=40))
    put_status(aws.ddb, BEDROOM, ts=old, received_at=old)
    put_pf_alert(aws)
    put_bed(aws.ddb, BEDROOM, False, VACATED)

    handler.run(NIGHT_NOW)

    assert aws.resolves() == []


def test_possible_fall_of_a_spare_unit_stays_open(aws):
    put_device(aws.ddb, BEDROOM, resident_id=None)
    put_pf_alert(aws)

    handler.run(NIGHT_NOW)

    assert aws.resolves() == []


# ======== logs (criterion 6) ========


def strip(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in ("timestamp", "location")}


def test_summary_log_counts_possible_falls_and_test_mode_units(aws, logs, monkeypatch):
    unit(aws)
    put_bed(aws.ddb, BEDROOM, False, VACATED)
    unit(aws, BEDROOM_2, RESIDENT_2)
    put_pf_alert(aws, device_id=BEDROOM_2, resident_id=RESIDENT_2, alert_id=ALERT_ID_2)
    put_bed(aws.ddb, BEDROOM_2, True, BACK_AT)
    put_test_mode(aws.ddb, BEDROOM, iso(NIGHT_NOW - timedelta(minutes=1)))

    run_handler(monkeypatch)

    (line,) = [r for r in logs() if r.get("outcome") == "done"]
    assert line["raised"] == counts(possible_fall=1)
    assert line["resolved"] == counts(possible_fall=1)
    assert line["test_mode_units"] == 1


def test_possible_fall_log_lines_have_ids_only(aws, logs, monkeypatch):
    unit(aws)
    put_bed(aws.ddb, BEDROOM, False, VACATED)

    run_handler(monkeypatch)

    (line,) = [r for r in logs() if r.get("action") == "raised"]
    assert set(strip(line)) - {"level", "message", "service"} == {
        "action",
        "alert_id",
        "alert_type",
        "device_id",
        "resident_id",
    }
    assert (line["alert_type"], line["device_id"]) == (POSSIBLE_FALL, BEDROOM)


def test_logs_never_contain_bed_motion_times_worn_state_or_details(
    aws, logs, monkeypatch
):
    # Raised (with a recent off-wrist wearable and test mode) and resolved.
    unit(aws)
    put_device(aws.ddb, WEARABLE, RESIDENT)
    at = iso(NIGHT_NOW - timedelta(minutes=2))
    put_status(aws.ddb, WEARABLE, ts=at, received_at=at, worn=False)
    put_bed(aws.ddb, BEDROOM, False, iso(NIGHT_NOW - timedelta(minutes=4)))
    put_motion(aws.ddb, BEDROOM, False, MOTION_AT)
    put_test_mode(aws.ddb, BEDROOM, iso(NIGHT_NOW - timedelta(minutes=10)))
    unit(aws, BEDROOM_2, RESIDENT_2)
    put_pf_alert(aws, device_id=BEDROOM_2, resident_id=RESIDENT_2, alert_id=ALERT_ID_2)
    put_bed(aws.ddb, BEDROOM_2, True, BACK_AT)

    run_handler(monkeypatch)

    assert len(aws.alerts()) == 1
    assert len(aws.resolves()) == 1
    records = [strip(r) for r in logs()]
    assert records
    text = json.dumps(records)
    ids = {ALERT_ID_2, STATUS_ID, BED_ID, MOTION_ID, aws.alerts()[0]["alert_id"]}
    for value in ids:
        text = text.replace(value, "<id>")
    assert not UUID.search(text)
    for value in (
        "out_of_bed_since",
        "details",
        '"wearable"',
        "no_wearable",
        "no_recent_status",
        '"worn"',
        "occupied",
        '"motion"',
        '"test_mode"',
        "reason",
        "back_in_bed",
        "2026",
        VACATED,
        MOTION_AT,
        BACK_AT,
        "18:",
        "02:",
    ):
        assert value not in text, value
