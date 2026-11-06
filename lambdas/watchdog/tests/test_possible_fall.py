"""The watchdog's possible-fall decisions (docs/features/possible-fall.md, criteria 2-7,
3a, and 17). Pure: checks.py is called directly, without moto.

"Night now" is 2026-11-05 02:30:00 SGT (2026-11-04T18:30:00Z); the default vacate is
02:10:00 SGT, exactly 20 minutes earlier.
"""

from __future__ import annotations

from datetime import timedelta

import checks
import pytest
from checks import DEVICE_OFFLINE, POSSIBLE_FALL
from events import (
    ALERT_ID,
    BED_ID,
    BEDROOM,
    BEDROOM_2,
    RESIDENT,
    RESIDENT_2,
    WEARABLE,
    WEARABLE_2,
    bed_entry,
    bedroom_latest,
    device,
    iso,
    motion_entry,
    open_alert,
    sgt,
    status,
)

MS = timedelta(milliseconds=1)
NIGHT_NOW = sgt(2, 30)
VACATED = sgt(2, 10)
VACATED_ISO = iso(VACATED)
# 12:00 SGT on the same day: outside the night window.
DAY_NOW = sgt(12, 0)


def st(device_id: str, at, **fields) -> checks.Status:
    """A newest status sent at `at`."""
    return status(device_id, ts=iso(at), received_at=iso(at), **fields)


def falls(
    beds=None,
    motions=None,
    devices=None,
    statuses=None,
    test_mode=frozenset(),
    open_alerts=None,
    now=NIGHT_NOW,
) -> list[checks.Problem]:
    """The possible falls checks.plan raises. Defaults: one assigned bedroom unit
    (online, no wearable), bed empty since VACATED, no motion entry."""
    devices = [device(BEDROOM)] if devices is None else devices
    if statuses is None:
        statuses = {BEDROOM: st(BEDROOM, now - timedelta(minutes=1))}
    if beds is None:
        beds = {BEDROOM: bed_entry(False, VACATED_ISO)}
    result = checks.plan(
        devices,
        statuses,
        {},
        open_alerts or [],
        now,
        beds=beds,
        motions=motions or {},
        test_mode=test_mode,
    )
    return [p for p in result.raise_alerts if p.alert_type == POSSIBLE_FALL]


def one_fall(**kw) -> checks.Problem:
    (problem,) = falls(**kw)
    return problem


def vacated_at(when) -> dict:
    return {BEDROOM: bed_entry(False, iso(when))}


# ======== event time (criterion 2) ========


def bed_item(**fields) -> dict:
    latest = bedroom_latest("occupied", False, VACATED_ISO, BED_ID)
    latest.update(fields)
    return {"pk": f"DEVICE#{BEDROOM}", "latest": latest}


def test_event_time_is_ts_without_clock_skew():
    entry = bed_entry(False, VACATED_ISO, received_at=iso(VACATED + timedelta(hours=1)))

    assert entry.event_ts == VACATED_ISO


def test_event_time_is_received_at_when_clock_skew_is_true():
    received = iso(VACATED + timedelta(hours=1))

    entry = bed_entry(False, VACATED_ISO, received_at=received, clock_skew=True)

    assert entry.event_ts == received


@pytest.mark.parametrize("skew", ["true", 1, None])
def test_clock_skew_that_is_not_true_uses_ts(skew):
    received = iso(VACATED + timedelta(hours=1))

    entry = bed_entry(False, VACATED_ISO, received_at=received, clock_skew=skew)

    assert entry.event_ts == VACATED_ISO


def test_skewed_bed_entry_uses_received_at_for_the_night_window():
    # The device clock says 12:00 SGT; AWS IoT received it at 02:10 SGT.
    beds = {
        BEDROOM: bed_entry(
            False, iso(sgt(12, 0)), received_at=VACATED_ISO, clock_skew=True
        )
    }

    problem = one_fall(beds=beds)

    assert problem.event_ts == VACATED_ISO
    assert problem.details["out_of_bed_since"] == VACATED_ISO


def test_skewed_bed_entry_received_in_the_day_does_not_alert():
    # The device clock says 02:10 SGT; it was received at 12:00 SGT.
    beds = {
        BEDROOM: bed_entry(
            False, VACATED_ISO, received_at=iso(sgt(12, 0)), clock_skew=True
        )
    }

    assert falls(beds=beds, now=sgt(13, 0)) == []


def test_skewed_bed_entry_uses_received_at_for_the_20_minutes():
    # ts says 40 minutes ago, received_at only 19 minutes ago.
    beds = {
        BEDROOM: bed_entry(
            False,
            iso(NIGHT_NOW - timedelta(minutes=40)),
            received_at=iso(NIGHT_NOW - timedelta(minutes=19)),
            clock_skew=True,
        )
    }

    assert falls(beds=beds) == []


@pytest.mark.parametrize("value", ["false", 0, None, "true", 1])
def test_bed_entry_with_a_non_boolean_occupied_is_skipped(value):
    assert checks.bedroom_entry_from_item(bed_item(occupied=value), "occupied") is None


def test_bed_entry_without_occupied_is_skipped():
    item = bed_item()
    del item["latest"]["occupied"]

    assert checks.bedroom_entry_from_item(item, "occupied") is None


@pytest.mark.parametrize("value", ["false", 0, None])
def test_motion_entry_with_a_non_boolean_motion_is_skipped(value):
    item = {
        "pk": f"DEVICE#{BEDROOM}",
        "latest": bedroom_latest("motion", value, VACATED_ISO, BED_ID),
    }

    assert checks.bedroom_entry_from_item(item, "motion") is None


@pytest.mark.parametrize(
    "fields",
    [
        {"ts": "2026-11-04T18:10:00Z"},
        {"ts": "yesterday"},
        {"ts": None},
        {"ts": "2026-13-04T18:10:00.000Z"},
        {"clock_skew": True, "received_at": "not-a-time"},
        {"clock_skew": True, "received_at": 1793671200120},
    ],
)
def test_bed_entry_with_a_bad_event_time_is_skipped(fields):
    assert checks.bedroom_entry_from_item(bed_item(**fields), "occupied") is None


@pytest.mark.parametrize(
    "pk", [f"DEVICE#{WEARABLE}", "DEVICE#bedroom-1", "bedroom-0001", None]
)
def test_bed_entry_of_a_non_bedroom_pk_is_skipped(pk):
    item = bed_item()
    item["pk"] = pk

    assert checks.bedroom_entry_from_item(item, "occupied") is None


def test_bed_entry_keeps_its_message_id():
    entry = checks.bedroom_entry_from_item(bed_item(), "occupied")

    assert entry == (
        BEDROOM,
        checks.BedroomEntry(False, VACATED_ISO, VACATED, BED_ID),
    )


# ======== night window (criterion 3) ========


@pytest.mark.parametrize(
    ("vacated", "alerts"),
    [
        (sgt(21, 59, 59, day=4), False),
        (sgt(21, 59, 59, 999, day=4), False),
        (sgt(22, 0, day=4), True),
        (sgt(23, 30, day=4), True),
        (sgt(0, 0), True),
        (sgt(6, 59, 59), True),
        (sgt(6, 59, 59, 999), True),
        (sgt(7, 0), False),
        (sgt(12, 0), False),
    ],
)
def test_night_window_is_22_00_to_before_07_00_sgt_on_the_vacate_time(vacated, alerts):
    now = vacated + timedelta(minutes=30)

    assert bool(falls(beds=vacated_at(vacated), now=now)) is alerts


def test_alert_may_fire_after_07_00_for_a_06_50_vacate():
    problem = one_fall(beds=vacated_at(sgt(6, 50)), now=sgt(7, 10))

    assert problem.details["out_of_bed_since"] == iso(sgt(6, 50))


def test_night_window_on_another_date_also_counts():
    vacated = sgt(23, 0, day=1)

    assert falls(beds=vacated_at(vacated), now=vacated + timedelta(hours=3))


# ======== 20 minutes (criterion 3) ========


def test_exactly_20_minutes_out_of_bed_alerts():
    assert NIGHT_NOW - VACATED == timedelta(minutes=20)

    assert len(falls()) == 1


def test_19_minutes_59_seconds_out_of_bed_does_not_alert():
    assert falls(now=VACATED + timedelta(minutes=19, seconds=59)) == []


def test_one_millisecond_under_20_minutes_does_not_alert():
    assert falls(now=VACATED + timedelta(minutes=20) - MS) == []


# ======== bed and motion (criterion 3) ========


def test_occupied_bed_does_not_alert():
    assert falls(beds={BEDROOM: bed_entry(True, VACATED_ISO)}) == []


def test_unit_without_a_bed_entry_is_not_checked():
    assert falls(beds={}) == []


def test_motion_true_blocks_the_alert():
    motions = {BEDROOM: motion_entry(True, iso(VACATED + timedelta(minutes=1)))}

    assert falls(motions=motions) == []


def test_motion_false_alerts():
    motions = {BEDROOM: motion_entry(False, iso(VACATED + timedelta(minutes=1)))}

    assert len(falls(motions=motions)) == 1


def test_no_motion_entry_alerts():
    assert len(falls(motions={})) == 1


def test_motion_of_another_unit_does_not_block():
    motions = {BEDROOM_2: motion_entry(True, VACATED_ISO)}

    assert len(falls(motions=motions)) == 1


# ======== wearable (criteria 3-4) ========


def with_wearables(*wearables: tuple[str, str, dict | None]):
    """devices and statuses for the bedroom unit plus wearables given as (device_id,
    resident_id, status fields with `at`, or None for no status)."""
    devices = [device(BEDROOM)]
    statuses = {BEDROOM: st(BEDROOM, NIGHT_NOW - timedelta(minutes=1))}
    for device_id, resident_id, fields in wearables:
        devices.append(device(device_id, resident_id))
        if fields is not None:
            fields = dict(fields)
            statuses[device_id] = st(device_id, fields.pop("at"), **fields)
    return {"devices": devices, "statuses": statuses}


def worn(minutes_ago: float, value=True, **extra) -> dict:
    return {"at": NIGHT_NOW - timedelta(minutes=minutes_ago), "worn": value, **extra}


def test_no_wearable_assigned_alerts_with_no_wearable():
    assert one_fall().details == {
        "out_of_bed_since": VACATED_ISO,
        "wearable": "no_wearable",
    }


def test_worn_wearable_within_15_minutes_blocks_the_alert():
    assert falls(**with_wearables((WEARABLE, RESIDENT, worn(14)))) == []


def test_worn_wearable_one_millisecond_under_15_minutes_blocks_the_alert():
    fields = {"at": NIGHT_NOW - timedelta(minutes=15) + MS, "worn": True}

    assert falls(**with_wearables((WEARABLE, RESIDENT, fields))) == []


def test_worn_status_exactly_15_minutes_old_does_not_block():
    problem = one_fall(**with_wearables((WEARABLE, RESIDENT, worn(15))))

    assert problem.details["wearable"] == "no_recent_status"


def test_worn_status_older_than_15_minutes_does_not_block():
    problem = one_fall(**with_wearables((WEARABLE, RESIDENT, worn(40))))

    assert problem.details["wearable"] == "no_recent_status"


def test_recent_worn_false_alerts_with_not_worn():
    problem = one_fall(**with_wearables((WEARABLE, RESIDENT, worn(2, False))))

    assert problem.details["wearable"] == "not_worn"


def test_wearable_without_any_status_alerts_with_no_recent_status():
    problem = one_fall(**with_wearables((WEARABLE, RESIDENT, None)))

    assert problem.details["wearable"] == "no_recent_status"


def test_several_wearables_one_worn_blocks_the_alert():
    setup = with_wearables(
        (WEARABLE, RESIDENT, worn(1, False)),
        (WEARABLE_2, RESIDENT, worn(3)),
    )

    assert falls(**setup) == []


def test_several_wearables_one_off_wrist_one_stale_is_not_worn():
    setup = with_wearables(
        (WEARABLE, RESIDENT, worn(1, False)),
        (WEARABLE_2, RESIDENT, worn(30)),
    )

    assert one_fall(**setup).details["wearable"] == "not_worn"


def test_worn_wearable_of_another_resident_does_not_block():
    setup = with_wearables((WEARABLE, RESIDENT_2, worn(1)))

    assert one_fall(**setup).details["wearable"] == "no_wearable"


@pytest.mark.parametrize("value", [None, "true", 1])
def test_recent_status_with_worn_not_a_boolean_does_not_block(value):
    setup = with_wearables((WEARABLE, RESIDENT, worn(1, value)))

    assert one_fall(**setup).details["wearable"] == "no_recent_status"


def test_charging_worn_false_counts_as_not_worn():
    setup = with_wearables((WEARABLE, RESIDENT, worn(1, False, charging=True)))

    assert one_fall(**setup).details["wearable"] == "not_worn"


# ======== assigned and online (criterion 3) ========


def test_unassigned_bedroom_unit_is_not_checked():
    assert falls(devices=[]) == []


def test_bedroom_unit_assigned_to_another_resident_alerts_for_that_resident():
    problem = one_fall(devices=[device(BEDROOM, RESIDENT_2)])

    assert problem.device.resident_id == RESIDENT_2


def test_offline_bedroom_unit_is_skipped():
    statuses = {BEDROOM: st(BEDROOM, NIGHT_NOW - timedelta(minutes=15) - MS)}

    assert falls(statuses=statuses) == []


def test_bedroom_unit_with_a_heartbeat_exactly_15_minutes_old_is_checked():
    statuses = {BEDROOM: st(BEDROOM, NIGHT_NOW - timedelta(minutes=15))}

    assert len(falls(statuses=statuses)) == 1


def test_bedroom_unit_without_any_status_is_checked():
    assert len(falls(statuses={})) == 1


def test_offline_bedroom_unit_still_gets_its_offline_alert():
    statuses = {BEDROOM: st(BEDROOM, NIGHT_NOW - timedelta(minutes=30))}

    result = checks.plan(
        [device(BEDROOM)],
        statuses,
        {},
        [],
        NIGHT_NOW,
        beds={BEDROOM: bed_entry(False, VACATED_ISO)},
    )

    assert [p.alert_type for p in result.raise_alerts] == [DEVICE_OFFLINE]


# ======== one open per unit, alert message (criteria 4-5) ========


def test_open_possible_fall_of_the_unit_blocks_a_second():
    alert = open_alert(
        POSSIBLE_FALL, BEDROOM, event_ts=iso(VACATED - timedelta(hours=1))
    )

    assert falls(open_alerts=[alert]) == []


def test_open_possible_fall_of_another_unit_does_not_block():
    alert = open_alert(POSSIBLE_FALL, BEDROOM_2, RESIDENT_2)

    assert len(falls(open_alerts=[alert])) == 1


def test_open_device_problem_of_the_unit_does_not_block():
    alert = open_alert("battery_low", BEDROOM)

    assert len(falls(open_alerts=[alert])) == 1


def test_possible_fall_message_has_the_a1_fields():
    problem = one_fall(**with_wearables((WEARABLE, RESIDENT, worn(1, False))))

    message = checks.alert_message(problem, NIGHT_NOW)

    assert message == {
        "schema_version": 1,
        "alert_id": checks.alert_id_for(
            VACATED_ISO, POSSIBLE_FALL, "critical", BEDROOM
        ),
        "alert_type": POSSIBLE_FALL,
        "severity": "critical",
        "resident_id": RESIDENT,
        "device_id": BEDROOM,
        "event_ts": VACATED_ISO,
        "received_at": iso(NIGHT_NOW),
        "source_message_id": BED_ID,
        "details": {"out_of_bed_since": VACATED_ISO, "wearable": "not_worn"},
    }


def test_possible_fall_has_the_same_alert_id_in_every_run():
    first = one_fall()
    later_now = NIGHT_NOW + timedelta(minutes=5)
    later = one_fall(now=later_now)

    assert (
        checks.alert_message(first, NIGHT_NOW)["alert_id"]
        == checks.alert_message(later, later_now)["alert_id"]
    )


def test_possible_fall_source_falls_back_to_the_alert_id_without_a_bed_message_id():
    beds = {BEDROOM: bed_entry(False, VACATED_ISO, message_id="not-a-uuid")}

    message = checks.alert_message(one_fall(beds=beds), NIGHT_NOW)

    assert message["source_message_id"] == message["alert_id"]


def offline_wearables(n: int):
    devices, statuses = [], {}
    for i in range(1, n + 1):
        device_id = f"wearable-{i:04d}"
        devices.append(device(device_id, f"res-{i:04d}"))
        statuses[device_id] = st(device_id, NIGHT_NOW - timedelta(minutes=30))
    return devices, statuses


def test_possible_falls_are_never_grouped():
    devices, statuses = offline_wearables(5)
    devices.append(device(BEDROOM, "res-0009"))
    statuses[BEDROOM] = st(BEDROOM, NIGHT_NOW - timedelta(minutes=1))

    result = checks.plan(
        devices,
        statuses,
        {},
        [],
        NIGHT_NOW,
        beds={BEDROOM: bed_entry(False, VACATED_ISO)},
    )

    assert result.grouped is True
    (fall,) = [p for p in result.raise_alerts if p.alert_type == POSSIBLE_FALL]
    assert "grouped" not in fall.details


def test_four_offline_plus_a_possible_fall_are_not_grouped():
    devices, statuses = offline_wearables(4)
    devices.append(device(BEDROOM, "res-0009"))
    statuses[BEDROOM] = st(BEDROOM, NIGHT_NOW - timedelta(minutes=1))

    result = checks.plan(
        devices,
        statuses,
        {},
        [],
        NIGHT_NOW,
        beds={BEDROOM: bed_entry(False, VACATED_ISO)},
    )

    assert result.grouped is False


# ======== test mode (criterion 3a) ========


def tm_item(device_id=BEDROOM, set_at=None, **overrides) -> dict:
    item = {
        "pk": f"DEVICE#{device_id}",
        "sk": f"TEST_MODE#{iso(set_at or DAY_NOW - timedelta(minutes=10))}",
    }
    item.update(overrides)
    return item


def test_test_mode_item_puts_its_unit_in_test_mode():
    assert checks.units_in_test_mode([tm_item()], DAY_NOW) == frozenset({BEDROOM})


def test_test_mode_item_set_exactly_now_counts():
    assert checks.unit_in_test_mode(tm_item(set_at=DAY_NOW), DAY_NOW) == BEDROOM


def test_test_mode_item_exactly_2_hours_old_still_counts():
    item = tm_item(set_at=DAY_NOW - timedelta(hours=2))

    assert checks.unit_in_test_mode(item, DAY_NOW) == BEDROOM


def test_test_mode_item_one_millisecond_over_2_hours_is_expired():
    item = tm_item(set_at=DAY_NOW - timedelta(hours=2) - MS)

    assert checks.unit_in_test_mode(item, DAY_NOW) is None


def test_test_mode_item_in_the_future_is_ignored():
    item = tm_item(set_at=DAY_NOW + MS)

    assert checks.unit_in_test_mode(item, DAY_NOW) is None


@pytest.mark.parametrize(
    "sk",
    [
        "TEST_MODE#yesterday",
        "TEST_MODE#2026-11-05T03:50:00Z",
        "TEST_MODE#2026-11-05 03:50:00.000Z",
        "TEST_MODE#2026-02-30T03:50:00.000Z",
        "TEST_MODE#",
        "TEST_MODE",
        "ASSIGNMENT",
        "test_mode#2026-11-05T03:50:00.000Z",
        None,
        42,
    ],
)
def test_test_mode_item_with_an_unreadable_time_is_ignored(sk):
    assert checks.unit_in_test_mode(tm_item(sk=sk), DAY_NOW) is None


@pytest.mark.parametrize(
    "pk",
    [
        f"DEVICE#{WEARABLE}",
        "DEVICE#bedroom-01",
        "DEVICE#bedroom-0001x",
        "RESIDENT#res-0001",
        BEDROOM,
        None,
    ],
)
def test_test_mode_item_for_a_non_bedroom_pk_is_ignored(pk):
    assert checks.unit_in_test_mode(tm_item(pk=pk), DAY_NOW) is None


def test_test_mode_ignores_invalid_items_and_keeps_valid_ones():
    items = [
        tm_item(),
        tm_item(BEDROOM_2, set_at=DAY_NOW - timedelta(hours=3)),
        tm_item(pk=f"DEVICE#{WEARABLE}"),
    ]

    assert checks.units_in_test_mode(items, DAY_NOW) == frozenset({BEDROOM})


TEST_MODE = frozenset({BEDROOM})


def day_vacate(minutes_ago: float, ms: int = 0):
    return vacated_at(DAY_NOW - timedelta(minutes=minutes_ago, milliseconds=ms))


def test_test_mode_counts_any_time_of_day():
    problem = one_fall(beds=day_vacate(2), test_mode=TEST_MODE, now=DAY_NOW)

    assert problem.details == {
        "out_of_bed_since": iso(DAY_NOW - timedelta(minutes=2)),
        "wearable": "no_wearable",
        "test_mode": True,
    }
    assert problem.severity == "critical"


def test_test_mode_exactly_2_minutes_alerts():
    assert len(falls(beds=day_vacate(2), test_mode=TEST_MODE, now=DAY_NOW)) == 1


def test_test_mode_1_minute_59_seconds_does_not_alert():
    beds = vacated_at(DAY_NOW - timedelta(minutes=1, seconds=59))

    assert falls(beds=beds, test_mode=TEST_MODE, now=DAY_NOW) == []


def test_test_mode_one_millisecond_under_2_minutes_does_not_alert():
    beds = vacated_at(DAY_NOW - timedelta(minutes=2) + MS)

    assert falls(beds=beds, test_mode=TEST_MODE, now=DAY_NOW) == []


def test_without_test_mode_a_day_vacate_does_not_alert():
    assert falls(beds=day_vacate(60), now=DAY_NOW) == []


def test_test_mode_still_needs_motion_cleared():
    motions = {BEDROOM: motion_entry(True, iso(DAY_NOW - timedelta(minutes=1)))}

    assert (
        falls(beds=day_vacate(5), motions=motions, test_mode=TEST_MODE, now=DAY_NOW)
        == []
    )


def test_test_mode_still_needs_the_wearable_not_worn():
    devices = [device(BEDROOM), device(WEARABLE)]
    statuses = {
        BEDROOM: st(BEDROOM, DAY_NOW - timedelta(minutes=1)),
        WEARABLE: st(WEARABLE, DAY_NOW - timedelta(minutes=1), worn=True),
    }

    result = falls(
        beds=day_vacate(5),
        devices=devices,
        statuses=statuses,
        test_mode=TEST_MODE,
        now=DAY_NOW,
    )

    assert result == []


def test_test_mode_still_skips_an_offline_unit():
    statuses = {BEDROOM: st(BEDROOM, DAY_NOW - timedelta(minutes=16))}

    result = falls(
        beds=day_vacate(5), statuses=statuses, test_mode=TEST_MODE, now=DAY_NOW
    )

    assert result == []


def test_test_mode_still_needs_the_bed_empty():
    beds = {BEDROOM: bed_entry(True, iso(DAY_NOW - timedelta(minutes=5)))}

    assert falls(beds=beds, test_mode=TEST_MODE, now=DAY_NOW) == []


def test_test_mode_still_needs_the_unit_assigned():
    assert falls(beds=day_vacate(5), devices=[], test_mode=TEST_MODE, now=DAY_NOW) == []


def test_test_mode_still_allows_only_one_open_per_unit():
    alert = open_alert(POSSIBLE_FALL, BEDROOM)

    result = falls(
        beds=day_vacate(5), test_mode=TEST_MODE, open_alerts=[alert], now=DAY_NOW
    )

    assert result == []


def test_test_mode_affects_only_the_named_unit():
    devices = [device(BEDROOM), device(BEDROOM_2, RESIDENT_2)]
    statuses = {
        BEDROOM: st(BEDROOM, DAY_NOW - timedelta(minutes=1)),
        BEDROOM_2: st(BEDROOM_2, DAY_NOW - timedelta(minutes=1)),
    }
    empty = bed_entry(False, iso(DAY_NOW - timedelta(minutes=5)))

    result = falls(
        beds={BEDROOM: empty, BEDROOM_2: empty},
        devices=devices,
        statuses=statuses,
        test_mode=TEST_MODE,
        now=DAY_NOW,
    )

    assert [p.device.device_id for p in result] == [BEDROOM]


def test_real_night_alert_in_test_mode_has_no_test_mode_detail():
    problem = one_fall(test_mode=TEST_MODE)

    assert "test_mode" not in problem.details


def test_night_vacate_that_only_test_mode_allows_has_test_mode():
    beds = vacated_at(NIGHT_NOW - timedelta(minutes=5))

    problem = one_fall(beds=beds, test_mode=TEST_MODE)

    assert problem.details["test_mode"] is True


def test_test_mode_alert_has_the_usual_alert_id():
    vacated = iso(DAY_NOW - timedelta(minutes=5))

    message = checks.alert_message(
        one_fall(
            beds=vacated_at(DAY_NOW - timedelta(minutes=5)),
            test_mode=TEST_MODE,
            now=DAY_NOW,
        ),
        DAY_NOW,
    )

    assert message["alert_id"] == checks.alert_id_for(
        vacated, POSSIBLE_FALL, "critical", BEDROOM
    )


def test_plan_counts_the_units_in_test_mode():
    result = checks.plan(
        [], {}, {}, [], DAY_NOW, test_mode=frozenset({BEDROOM, BEDROOM_2})
    )

    assert result.test_mode_units == 2


# ======== back in bed (criterion 7) ========


def pf_alert(status_event_ts: str = VACATED_ISO) -> checks.OpenAlert:
    return open_alert(
        POSSIBLE_FALL, BEDROOM, alert_id=ALERT_ID, event_ts=status_event_ts
    )


def resolves(beds, devices=None, statuses=None, now=NIGHT_NOW) -> list:
    result = checks.plan(
        [device(BEDROOM)] if devices is None else devices,
        {BEDROOM: st(BEDROOM, now - timedelta(minutes=1))}
        if statuses is None
        else statuses,
        {},
        [pf_alert()],
        now,
        beds=beds,
    )
    return result.resolve


def test_back_in_bed_after_the_vacate_resolves():
    beds = {BEDROOM: bed_entry(True, iso(VACATED + timedelta(minutes=25)))}

    assert resolves(beds) == [pf_alert()]


def test_back_in_bed_one_millisecond_after_the_vacate_resolves():
    beds = {BEDROOM: bed_entry(True, iso(VACATED + MS))}

    assert resolves(beds) == [pf_alert()]


def test_back_in_bed_at_the_vacate_time_does_not_resolve():
    assert resolves({BEDROOM: bed_entry(True, VACATED_ISO)}) == []


def test_back_in_bed_with_an_earlier_event_time_does_not_resolve():
    beds = {BEDROOM: bed_entry(True, iso(VACATED - timedelta(minutes=1)))}

    assert resolves(beds) == []


def test_back_in_bed_uses_received_at_of_a_skewed_entry():
    beds = {
        BEDROOM: bed_entry(
            True,
            iso(VACATED - timedelta(hours=5)),
            received_at=iso(VACATED + timedelta(minutes=30)),
            clock_skew=True,
        )
    }

    assert resolves(beds) == [pf_alert()]


def test_bed_still_empty_does_not_resolve():
    beds = {BEDROOM: bed_entry(False, iso(VACATED + timedelta(minutes=30)))}

    assert resolves(beds) == []


def test_no_bed_entry_does_not_resolve():
    assert resolves({}) == []


BACK_IN_BED = {BEDROOM: bed_entry(True, iso(VACATED + timedelta(minutes=30)))}


def test_back_in_bed_of_a_unit_still_assigned_to_the_resident_resolves():
    assert resolves(BACK_IN_BED, devices=[device(BEDROOM, RESIDENT)]) == [pf_alert()]


def test_back_in_bed_of_an_unassigned_unit_does_not_resolve():
    assert resolves(BACK_IN_BED, devices=[]) == []


def test_back_in_bed_of_a_unit_reassigned_to_another_resident_does_not_resolve():
    assert resolves(BACK_IN_BED, devices=[device(BEDROOM, RESIDENT_2)]) == []


def test_unassigned_unit_without_bed_data_does_not_resolve():
    assert resolves({}, devices=[]) == []


def test_offline_unit_does_not_resolve_its_possible_fall():
    statuses = {BEDROOM: st(BEDROOM, NIGHT_NOW - timedelta(hours=1))}

    assert resolves({}, statuses=statuses) == []


def test_back_in_bed_of_another_unit_does_not_resolve():
    beds = {BEDROOM_2: bed_entry(True, iso(VACATED + timedelta(minutes=30)))}

    assert resolves(beds) == []


def test_back_in_bed_resolve_message_has_reason_back_in_bed():
    message = checks.resolve_message(pf_alert(), NIGHT_NOW)

    assert message == {
        "kind": "status_update",
        "schema_version": 1,
        "resident_id": RESIDENT,
        "new_status": "resolved",
        "changed_at": iso(NIGHT_NOW),
        "by_role": "system",
        "by": None,
        "by_name": None,
        "target": {"alert_id": ALERT_ID, "event_ts": VACATED_ISO},
        "reason": "back_in_bed",
    }


@pytest.mark.parametrize("alert_type", checks.DEVICE_PROBLEM_TYPES)
def test_device_problem_resolve_message_has_no_reason(alert_type):
    message = checks.resolve_message(open_alert(alert_type), NIGHT_NOW)

    assert "reason" not in message


def test_open_possible_fall_item_is_parsed():
    item = {
        "pk": f"RESIDENT#{RESIDENT}",
        "sk": f"ALERT#{VACATED_ISO}#{ALERT_ID}",
        "alert_type": POSSIBLE_FALL,
        "device_id": BEDROOM,
    }

    assert checks.open_alert_from_item(item) == pf_alert()
