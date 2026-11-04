"""The watchdog's decisions (docs/features/watchdog.md, criteria 3-10 and 20). Pure:
checks.py is called directly, without moto."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import checks
import pytest
from checks import BATTERY_LOW, DEVICE_OFFLINE, NOT_WORN
from events import (
    ALERT_ID,
    ALERT_ID_2,
    BEDROOM,
    NOW,
    NOW_ISO,
    RESIDENT,
    RESIDENT_2,
    STATUS_ID,
    WEARABLE,
    WEARABLE_2,
    ago,
    device,
    iso,
    open_alert,
    status,
)

MS = timedelta(milliseconds=1)


def sgt(hour: int, minute: int = 0) -> datetime:
    """NOW's day (2026-11-04) at hour:minute SGT, as UTC."""
    return datetime(2026, 11, 4, hour, minute, tzinfo=checks.SGT).astimezone(UTC)


def types(problems) -> list[str]:
    return [p.alert_type for p in problems]


def problems(dev=None, st=None, since=None, now=NOW):
    return checks.problems_for(dev or device(), st or status(), since, now)


def plan(devices, statuses, worn_since=None, open_alerts=None, now=NOW):
    return checks.plan(devices, statuses, worn_since or {}, open_alerts or [], now)


# ======== device_offline (criterion 4) ========


def test_exactly_15_minutes_without_a_heartbeat_is_not_offline():
    st = status(ts=iso(NOW - timedelta(minutes=15)))

    assert checks.is_offline(st, NOW) is False


def test_one_millisecond_over_15_minutes_is_offline():
    st = status(ts=iso(NOW - timedelta(minutes=15) - MS))

    assert checks.is_offline(st, NOW) is True


def test_just_under_15_minutes_is_not_offline():
    st = status(ts=iso(NOW - timedelta(minutes=15) + MS))

    assert checks.is_offline(st, NOW) is False


def test_offline_problem_has_last_heartbeat_as_event_ts_and_last_seen():
    last = ago(minutes=20)

    (problem,) = problems(st=status(ts=last))

    assert problem.alert_type == DEVICE_OFFLINE
    assert problem.event_ts == last
    assert problem.details == {"last_seen": last}


def test_offline_device_is_not_checked_for_battery_or_worn():
    st = status(ts=ago(minutes=20), battery_pct=5, worn=False)

    assert types(problems(st=st, since=ago(hours=5))) == [DEVICE_OFFLINE]


# ======== battery_low (criterion 5) ========


@pytest.mark.parametrize(
    ("battery", "low"), [(0, True), (19, True), (20, False), (21, False)]
)
def test_battery_is_low_only_below_20(battery, low):
    result = checks.low_battery_pct(status(battery_pct=battery))

    assert result == (battery if low else None)


def test_battery_below_20_while_charging_is_not_low():
    assert checks.low_battery_pct(status(battery_pct=5, charging=True)) is None


@pytest.mark.parametrize("charging", [False, None, "true", 1])
def test_battery_below_20_with_charging_not_true_is_low(charging):
    assert checks.low_battery_pct(status(battery_pct=5, charging=charging)) == 5


def test_bedroom_unit_without_charging_field_is_low_below_20():
    assert checks.low_battery_pct(status(BEDROOM, battery_pct=12)) == 12


def test_null_battery_is_skipped():
    assert checks.low_battery_pct(status(BEDROOM, battery_pct=None)) is None


def test_missing_battery_is_skipped():
    st = status(BEDROOM)
    del st.latest["battery_pct"]

    assert checks.low_battery_pct(st) is None


def test_battery_read_back_as_decimal_is_compared_as_a_number():
    assert checks.low_battery_pct(status(battery_pct=Decimal(19))) == 19
    assert checks.low_battery_pct(status(battery_pct=Decimal(20))) is None


@pytest.mark.parametrize("battery", ["5", True, Decimal("5.5"), 5.0])
def test_battery_that_is_not_an_integer_is_skipped(battery):
    assert checks.low_battery_pct(status(battery_pct=battery)) is None


def test_battery_problem_has_status_ts_as_event_ts_and_battery_pct():
    st = status(battery_pct=15)

    (problem,) = problems(st=st)

    assert problem.alert_type == BATTERY_LOW
    assert problem.event_ts == st.latest_ts
    assert problem.details == {"battery_pct": 15}


# ======== not_worn (criterion 6) ========


def off_wrist(**fields):
    return status(worn=False, **fields)


def test_off_wrist_for_exactly_2_hours_is_not_worn():
    since = iso(NOW - timedelta(hours=2))

    (problem,) = problems(st=off_wrist(), since=since)

    assert problem.alert_type == NOT_WORN
    assert problem.event_ts == since
    assert problem.details == {"since": since}


def test_off_wrist_for_just_under_2_hours_is_not_raised():
    since = iso(NOW - timedelta(hours=2) + MS)

    assert problems(st=off_wrist(), since=since) == []


@pytest.mark.parametrize(
    ("now", "raised"),
    [
        (sgt(7, 59), False),
        (sgt(8, 0), True),
        (sgt(21, 59), True),
        (sgt(22, 0), False),
    ],
    ids=["07:59", "08:00", "21:59", "22:00"],
)
def test_not_worn_is_checked_from_08_00_to_before_22_00_sgt(now, raised):
    st = checks.Status(
        {"message_id": STATUS_ID, "worn": False, "charging": False, "battery_pct": 60},
        iso(now - timedelta(minutes=1)),
        now - timedelta(minutes=1),
    )
    since = iso(now - timedelta(hours=3))

    assert types(problems(st=st, since=since, now=now)) == (
        [NOT_WORN] if raised else []
    )


def test_off_wrist_while_charging_is_not_raised():
    assert problems(st=off_wrist(charging=True), since=ago(hours=3)) == []


def test_worn_wearable_is_not_raised_even_with_an_old_since():
    assert problems(st=status(worn=True), since=ago(hours=3)) == []


def test_off_wrist_without_worn_false_since_is_not_raised():
    assert problems(st=off_wrist(), since=None) == []


def test_bedroom_unit_is_never_not_worn():
    st = status(BEDROOM, worn=False)

    assert problems(dev=device(BEDROOM), st=st, since=ago(hours=3)) == []


def test_low_battery_and_not_worn_are_both_raised_for_one_device():
    result = problems(st=off_wrist(battery_pct=10), since=ago(hours=3))

    assert types(result) == [BATTERY_LOW, NOT_WORN]


# ======== plan: which devices (criteria 2, 4, 8) ========


def test_device_that_never_sent_a_status_is_not_checked():
    result = plan([device()], {})

    assert result.raise_alerts == []
    assert result.checked == 1


@pytest.mark.parametrize(
    "item",
    [
        {"pk": f"DEVICE#{WEARABLE}", "device_type": "wearable"},
        {"pk": f"DEVICE#{WEARABLE}", "device_type": "wearable", "resident_id": None},
        {
            "pk": f"DEVICE#{WEARABLE}",
            "device_type": "wearable",
            "resident_id": "unassigned",
        },
    ],
    ids=["no-resident", "null-resident", "unassigned"],
)
def test_spare_device_item_is_not_a_checked_device(item):
    assert checks.device_from_item(item) is None


def test_assigned_device_item_is_a_checked_device():
    item = {
        "pk": f"DEVICE#{WEARABLE}",
        "device_type": "wearable",
        "resident_id": RESIDENT,
    }

    assert checks.device_from_item(item) == device()


def test_one_open_alert_per_device_and_type_blocks_a_second():
    st = status(ts=ago(minutes=30))

    result = plan([device()], {WEARABLE: st}, open_alerts=[open_alert(DEVICE_OFFLINE)])

    assert result.raise_alerts == []
    assert result.resolve == []


def test_open_alert_of_another_type_does_not_block():
    result = plan(
        [device()],
        {WEARABLE: status(battery_pct=10)},
        open_alerts=[open_alert(NOT_WORN)],
        worn_since={},
    )

    assert types(result.raise_alerts) == [BATTERY_LOW]


def test_open_alert_of_another_device_does_not_block():
    result = plan(
        [device()],
        {WEARABLE: status(battery_pct=10)},
        open_alerts=[open_alert(BATTERY_LOW, device_id=WEARABLE_2, alert_id=ALERT_ID)],
    )

    assert types(result.raise_alerts) == [BATTERY_LOW]


# ======== plan: resolve (criterion 10) ========


def resolved(alert, devices=None, statuses=None, now=NOW):
    devices = [device()] if devices is None else devices
    statuses = {WEARABLE: status()} if statuses is None else statuses
    return plan(devices, statuses, open_alerts=[alert], now=now).resolve == [alert]


def test_offline_alert_is_resolved_when_the_device_is_back_online():
    assert resolved(open_alert(DEVICE_OFFLINE))


def test_offline_alert_stays_open_while_still_offline():
    assert not resolved(
        open_alert(DEVICE_OFFLINE), statuses={WEARABLE: status(ts=ago(minutes=20))}
    )


def test_battery_alert_is_resolved_at_20():
    assert resolved(
        open_alert(BATTERY_LOW), statuses={WEARABLE: status(battery_pct=20)}
    )


def test_battery_alert_stays_open_at_19():
    assert not resolved(
        open_alert(BATTERY_LOW), statuses={WEARABLE: status(battery_pct=19)}
    )


def test_battery_alert_is_resolved_when_charging():
    st = status(battery_pct=5, charging=True)

    assert resolved(open_alert(BATTERY_LOW), statuses={WEARABLE: st})


def test_not_worn_alert_is_resolved_when_worn_again():
    assert resolved(open_alert(NOT_WORN), statuses={WEARABLE: status(worn=True)})


def test_not_worn_alert_is_resolved_when_put_on_charge():
    st = status(worn=False, charging=True)

    assert resolved(open_alert(NOT_WORN), statuses={WEARABLE: st})


def test_not_worn_alert_stays_open_overnight_while_still_off_wrist():
    night = sgt(23, 30)
    st = checks.Status(
        {"message_id": STATUS_ID, "worn": False, "charging": False, "battery_pct": 60},
        iso(night - timedelta(minutes=1)),
        night - timedelta(minutes=1),
    )

    assert not resolved(open_alert(NOT_WORN), statuses={WEARABLE: st}, now=night)


@pytest.mark.parametrize("alert_type", [BATTERY_LOW, NOT_WORN])
def test_battery_and_not_worn_alerts_stay_open_while_offline(alert_type):
    stale = status(ts=ago(minutes=20), battery_pct=80, worn=True)

    assert not resolved(open_alert(alert_type), statuses={WEARABLE: stale})


@pytest.mark.parametrize("alert_type", [DEVICE_OFFLINE, BATTERY_LOW, NOT_WORN])
def test_alert_is_resolved_when_the_device_is_no_longer_assigned(alert_type):
    stale = status(ts=ago(minutes=20), battery_pct=5, worn=False)

    assert resolved(open_alert(alert_type), devices=[], statuses={WEARABLE: stale})


@pytest.mark.parametrize("alert_type", [DEVICE_OFFLINE, BATTERY_LOW, NOT_WORN])
def test_alert_is_resolved_when_the_device_moved_to_another_resident(alert_type):
    stale = status(ts=ago(minutes=20), battery_pct=5, worn=False)

    assert resolved(
        open_alert(alert_type),
        devices=[device(WEARABLE, RESIDENT_2)],
        statuses={WEARABLE: stale},
    )


def test_reassigned_device_resolves_the_old_alert_before_raising_a_new_one():
    stale = status(ts=ago(minutes=20))

    result = plan(
        [device(WEARABLE, RESIDENT_2)],
        {WEARABLE: stale},
        open_alerts=[open_alert(DEVICE_OFFLINE, resident_id=RESIDENT)],
    )

    # The old resident's alert blocks by device and type, so no second alert yet;
    # it is resolved in this run and the next run raises the new one.
    assert result.resolve == [open_alert(DEVICE_OFFLINE, resident_id=RESIDENT)]


def test_open_alert_of_a_device_without_a_status_item_is_resolved():
    assert resolved(open_alert(BATTERY_LOW), statuses={})


# ======== plan: grouping (criterion 8a) ========


def offline_fleet(n: int):
    ids = [f"wearable-{i:04d}" for i in range(1, n + 1)]
    devices = [device(d, f"res-{i:04d}") for i, d in enumerate(ids, start=1)]
    statuses = {d: status(d, ts=ago(minutes=20)) for d in ids}
    return devices, statuses


def test_four_offline_devices_are_not_grouped():
    result = plan(*offline_fleet(4))

    assert result.grouped is False
    assert len(result.raise_alerts) == 4
    assert all("grouped" not in p.details for p in result.raise_alerts)


def test_five_offline_devices_are_grouped():
    result = plan(*offline_fleet(5))

    assert result.grouped is True
    assert len(result.raise_alerts) == 5
    assert all(p.details["grouped"] is True for p in result.raise_alerts)


def test_already_open_offline_alerts_do_not_count_towards_grouping():
    devices, statuses = offline_fleet(5)
    already = open_alert(DEVICE_OFFLINE, "wearable-0001", "res-0001")

    result = plan(devices, statuses, open_alerts=[already])

    assert len(result.raise_alerts) == 4
    assert result.grouped is False


def test_other_types_do_not_count_towards_grouping():
    devices, statuses = offline_fleet(4)
    devices.append(device("wearable-0099", "res-0099"))
    statuses["wearable-0099"] = status("wearable-0099", battery_pct=5)

    result = plan(devices, statuses)

    assert sorted(types(result.raise_alerts)) == [BATTERY_LOW] + [DEVICE_OFFLINE] * 4
    assert result.grouped is False
    assert all("grouped" not in p.details for p in result.raise_alerts)


def test_grouping_marks_only_offline_alerts():
    devices, statuses = offline_fleet(5)
    devices.append(device("wearable-0099", "res-0099"))
    statuses["wearable-0099"] = status("wearable-0099", battery_pct=5)

    result = plan(devices, statuses)

    battery = [p for p in result.raise_alerts if p.alert_type == BATTERY_LOW]
    assert battery[0].details == {"battery_pct": 5}


# ======== messages (criteria 8, 10) ========


def ingest_namespace() -> uuid.UUID:
    """ALERT_NAMESPACE as written in lambdas/ingest/src/alerts.py."""
    path = Path(checks.__file__).resolve().parents[2] / "ingest" / "src" / "alerts.py"
    match = re.search(
        r'ALERT_NAMESPACE = uuid\.UUID\("([0-9a-f-]+)"\)', path.read_text("utf-8")
    )
    assert match
    return uuid.UUID(match.group(1))


def test_alert_namespace_is_the_same_as_ingest():
    assert ingest_namespace() == checks.ALERT_NAMESPACE


def test_alert_id_is_uuid5_of_event_ts_type_severity_device():
    event_ts = "2026-11-04T03:40:00.000Z"

    result = checks.alert_id_for(event_ts, DEVICE_OFFLINE, "warning", WEARABLE)

    expected = uuid.uuid5(
        ingest_namespace(), f"{event_ts}|device_offline|warning|{WEARABLE}"
    )
    assert result == str(expected)


def test_alert_message_has_the_a1_fields():
    st = status(battery_pct=15)
    (problem,) = problems(st=st)

    message = checks.alert_message(problem, NOW)

    assert message == {
        "schema_version": 1,
        "alert_id": checks.alert_id_for(st.latest_ts, BATTERY_LOW, "warning", WEARABLE),
        "alert_type": BATTERY_LOW,
        "severity": "warning",
        "resident_id": RESIDENT,
        "device_id": WEARABLE,
        "event_ts": st.latest_ts,
        "received_at": NOW_ISO,
        "source_message_id": STATUS_ID,
        "details": {"battery_pct": 15},
    }


def test_same_problem_in_a_later_run_has_the_same_alert_id():
    st = status(ts=ago(minutes=20))
    (first,) = problems(st=st)
    (later,) = problems(st=st, now=NOW + timedelta(minutes=5))

    assert (
        checks.alert_message(first, NOW)["alert_id"]
        == checks.alert_message(later, NOW + timedelta(minutes=5))["alert_id"]
    )


@pytest.mark.parametrize(
    "message_id", ["not-a-uuid", None, 42, "ABCDEF00-0000-4000-8000-000000000001"]
)
def test_source_message_id_falls_back_to_the_alert_id(message_id):
    (problem,) = problems(st=status(battery_pct=15, message_id=message_id))

    message = checks.alert_message(problem, NOW)

    assert message["source_message_id"] == message["alert_id"]


def test_resolve_message_is_a_system_status_update_without_note():
    alert = open_alert(BATTERY_LOW, event_ts="2026-11-04T02:00:00.000Z")

    assert checks.resolve_message(alert, NOW) == {
        "kind": "status_update",
        "schema_version": 1,
        "resident_id": RESIDENT,
        "new_status": "resolved",
        "changed_at": NOW_ISO,
        "by_role": "system",
        "by": None,
        "by_name": None,
        "target": {"alert_id": ALERT_ID, "event_ts": "2026-11-04T02:00:00.000Z"},
    }


# ======== open alert items (pattern 9) ========


def open_item(**overrides):
    item = {
        "pk": f"RESIDENT#{RESIDENT}",
        "sk": f"ALERT#2026-11-04T02:00:00.000Z#{ALERT_ID_2}",
        "alert_type": NOT_WORN,
        "device_id": WEARABLE,
    }
    item.update(overrides)
    return item


def test_open_device_problem_item_is_parsed():
    assert checks.open_alert_from_item(open_item()) == checks.OpenAlert(
        ALERT_ID_2, NOT_WORN, WEARABLE, RESIDENT, "2026-11-04T02:00:00.000Z"
    )


@pytest.mark.parametrize("alert_type", ["fall", "hr_high", "spo2_low", None])
def test_open_health_alert_item_is_ignored(alert_type):
    assert checks.open_alert_from_item(open_item(alert_type=alert_type)) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"sk": "ALERT#2026-11-04T02:00:00.000Z#not-a-uuid"},
        {"sk": f"ALERT#yesterday#{ALERT_ID_2}"},
        {"pk": "HOME#home-0001"},
        {"device_id": "phone-0001"},
    ],
)
def test_open_item_with_bad_keys_is_ignored(overrides):
    assert checks.open_alert_from_item(open_item(**overrides)) is None


# ======== mass offline summary (criterion 8a) ========


def test_summary_subject_counts_the_devices():
    assert checks.summary_subject(7) == "SilverCare device problem: 7 devices offline"


def test_summary_body_lists_devices_residents_and_sgt_heartbeats():
    result = plan(*offline_fleet(5))

    body = checks.summary_body(
        result.raise_alerts, "https://d0000example.cloudfront.net/"
    )

    last = (NOW - timedelta(minutes=20)).astimezone(checks.SGT)
    when = last.strftime("%Y-%m-%d %H:%M:%S") + " SGT"
    assert when == "2026-11-04 11:40:00 SGT"
    expected = [
        "SilverCare device problem",
        "",
        "5 devices stopped sending heartbeats (no status for 15 minutes):",
        "",
        *[
            f"  wearable-{i:04d} (res-{i:04d}): last heartbeat {when}"
            for i in range(1, 6)
        ],
        "",
        (
            "Each device has its own alert in the portal, and each resident's"
            " caregivers are emailed about their own device."
        ),
        "",
        "Open the SilverCare portal: https://d0000example.cloudfront.net/",
        "",
        "This is an automated message. Do not reply.",
    ]
    assert body == "\n".join(expected) + "\n"
