"""Device alert limits in the simulator (docs/features/device-alert-limits.md,
"Device limits", "Valid limits", criteria 19 to 24).

Pure rules are tested directly; the engine is driven like the run loop
(scenario_helpers.play); live runs use the fake MQTT client and fake shadow service in
fakes.py. Nothing opens a network connection or reads a key: key files are empty
placeholders in tmp_path, and the dry-run and backfill tests block sockets and key
reads. Backfill writes only under tmp_path.
"""

from __future__ import annotations

import copy
import io
import json
import re

import backfill
import fakes
import pytest
import scenarios
from conftest import DEVICES_FILE, SEED_FILE, load_doc, run_dry
from engine import Engine
from fakes import World
from residents import (
    FIXED_LIMITS,
    DeviceLimits,
    Limits,
    Resident,
    build_residents,
    device_limits,
    parse_device_limits,
    reading_limits,
)
from scenario_helpers import MINUTE_MS, SECOND_MS, make_engine, play
from test_backfill import Run, guard, read_output, small_devices_file
from test_cli import guard_network_and_keys  # noqa: F401  (fixture)
from test_live import keys, run_live  # noqa: F401  (keys is a fixture)

W = "wearable-0005"  # res-0005: thresholds 45 / 130 / 88, worn at 12:00
B = "bedroom-0005"
T0 = 2 * MINUTE_MS
ALERT_MS = 10 * SECOND_MS
REPEAT_MS = 5 * MINUTE_MS


def thresholds(hr_min=45, hr_max=130, spo2=88) -> dict:
    return {
        "hr_bpm": {"min": hr_min, "max": hr_max},
        "spo2_pct": {"min": spo2},
        "skin_temp_c": {"min": 30.0, "max": 38.0},
    }


def limits_doc(hr_min=45, hr_max=130, spo2=88) -> dict:
    return {"hr_bpm": {"min": hr_min, "max": hr_max}, "spo2_pct": {"min": spo2}}


# ======== Device limits: computing a wearable's limits ========


def test_limits_take_the_stricter_of_thresholds_and_fixed():
    assert device_limits(thresholds(55, 110, 92)) == DeviceLimits(55, 110, 92)


def test_looser_thresholds_are_capped_at_the_fixed_limits():
    assert device_limits(thresholds(40, 140, 85)) == FIXED_LIMITS


@pytest.mark.parametrize("raw", [None, {}, [], "x", {"hr_bpm": None}])
def test_missing_thresholds_use_the_ingest_defaults(raw):
    assert device_limits(raw) == DeviceLimits(50, 120, 92)


@pytest.mark.parametrize(
    "hr",
    [
        {"min": "60", "max": 100},  # string
        {"min": True, "max": 100},  # bool is not a number
        {"min": 60.5, "max": 100},  # not an integer
        {"min": 19, "max": 100},  # below the contract range
        {"max": 100},  # missing
    ],
)
def test_invalid_hr_min_uses_its_default_only(hr):
    raw = {"hr_bpm": hr, "spo2_pct": {"min": 95}}
    assert device_limits(raw) == DeviceLimits(50, 100, 95)


def test_invalid_hr_max_and_spo2_use_their_defaults():
    raw = {"hr_bpm": {"min": 60, "max": 251}, "spo2_pct": {"min": 49}}
    assert device_limits(raw) == DeviceLimits(60, 120, 92)


def test_whole_number_floats_count_as_integers_like_ingest():
    assert device_limits(thresholds(60.0, 100.0, 95.0)) == DeviceLimits(60, 100, 95)


def test_threshold_min_not_below_max_uses_both_hr_defaults():
    # Ingest's rule: an inverted pair resolves to 50 / 120.
    assert device_limits(thresholds(100, 100, 95)) == DeviceLimits(50, 120, 95)


def test_hr_min_not_below_max_after_capping_falls_back_to_fixed_hr_only():
    # 135 / 140 is a valid pair, but capping max at 130 leaves min >= max.
    assert device_limits(thresholds(135, 140, 95)) == DeviceLimits(45, 130, 95)


def test_hr_min_just_below_max_after_capping_is_kept():
    assert device_limits(thresholds(129, 140, 95)) == DeviceLimits(129, 130, 95)


def test_unassigned_wearable_default_is_the_fixed_limits():
    # The simulator refuses unassigned devices at setup, so only the default applies.
    assert Resident.__dataclass_fields__["device_limits"].default == FIXED_LIMITS
    assert FIXED_LIMITS == DeviceLimits(45, 130, 88)


def test_limits_shape_has_no_skin_temperature():
    assert DeviceLimits(50, 110, 92).to_dict() == limits_doc(50, 110, 92)


def test_seed_residents_get_their_computed_start_limits():
    residents, _ = build_residents(load_doc(SEED_FILE), load_doc(DEVICES_FILE))
    by_id = {r.resident_id: r.device_limits for r in residents}
    assert by_id["res-0004"] == FIXED_LIMITS  # SpO2 85 -> 88
    assert by_id["res-0007"] == FIXED_LIMITS  # HR max 140 -> 130
    assert by_id["res-0011"] == FIXED_LIMITS  # HR min 40 -> 45
    assert set(by_id.values()) == {FIXED_LIMITS}


def test_a_tightened_seed_threshold_becomes_the_start_limits():
    seed = load_doc(SEED_FILE)
    seed["residents"][4]["thresholds"] = thresholds(55, 110, 92)
    residents, _ = build_residents(seed, load_doc(DEVICES_FILE))
    (res,) = [r for r in residents if r.wearable_id == W]
    assert res.device_limits == DeviceLimits(55, 110, 92)


# ======== Valid limits ========


@pytest.mark.parametrize(
    "doc",
    [
        limits_doc(45, 130, 88),
        limits_doc(129, 130, 100),
        limits_doc(45, 46, 88),
    ],
)
def test_valid_limits_are_accepted(doc):
    h, s = doc["hr_bpm"], doc["spo2_pct"]
    assert parse_device_limits(doc) == DeviceLimits(h["min"], h["max"], s["min"])


@pytest.mark.parametrize(
    "doc",
    [
        limits_doc(44, 130, 88),  # hr min below 45
        limits_doc(45, 131, 88),  # hr max above 130
        limits_doc(45, 19, 88),  # hr max below 20
        limits_doc(100, 100, 88),  # min == max
        limits_doc(120, 110, 88),  # min > max
        limits_doc(45, 130, 87),  # SpO2 below 88
        limits_doc(45, 130, 101),  # SpO2 above 100
        limits_doc(45.0, 130, 88),  # float
        limits_doc(45, 130.0, 88),
        limits_doc(45, 130, 88.0),
        limits_doc(True, 130, 88),  # bool
        limits_doc("45", 130, 88),  # string
        limits_doc(None, 130, 88),
        {**limits_doc(), "skin_temp_c": {"min": 30}},  # extra top-level key
        {"hr_bpm": {"min": 45, "max": 130, "x": 1}, "spo2_pct": {"min": 88}},
        {"hr_bpm": {"min": 45, "max": 130}, "spo2_pct": {"min": 88, "max": 100}},
        {"hr_bpm": {"min": 45}, "spo2_pct": {"min": 88}},  # missing max
        {"hr_bpm": {"min": 45, "max": 130}},  # missing spo2_pct
        {"hr_bpm": [45, 130], "spo2_pct": {"min": 88}},
        {"hr_bpm": {"min": 45, "max": 130}, "spo2_pct": 88},
        None,
        [],
        "limits",
        {},
    ],
)
def test_invalid_limits_are_refused(doc):
    assert parse_device_limits(doc) is None


# ======== criterion 21: normal readings inside the current limits ========


SEED_RANGE = Limits(53, 122, 91, 100, 310, 370)


def test_reading_range_is_limits_narrowed_by_the_margins():
    got = reading_limits(DeviceLimits(60, 100, 95), SEED_RANGE)
    assert (got.hr_min, got.hr_max, got.spo2_min) == (68, 92, 98)
    # SpO2 max and skin temperature keep the seed-based range.
    assert (got.spo2_max, got.temp_min_tenths, got.temp_max_tenths) == (100, 310, 370)


def test_narrowed_hr_range_of_one_value_is_kept():
    got = reading_limits(DeviceLimits(45, 61, 88), SEED_RANGE)
    assert (got.hr_min, got.hr_max) == (53, 53)


def test_empty_narrowed_hr_range_uses_the_midpoint():
    got = reading_limits(DeviceLimits(45, 60, 88), SEED_RANGE)
    assert (got.hr_min, got.hr_max) == (52, 52)  # (45 + 60) // 2


def test_spo2_floor_is_capped_at_100():
    got = reading_limits(DeviceLimits(45, 130, 99), SEED_RANGE)
    assert got.spo2_min == 100


def vitals_of(played, device_id=W, after_ms=0):
    return [
        m.payload
        for m in played.of(device_id, "vitals")
        if m.elapsed_ms >= after_ms and m.payload["hr_bpm"] is not None
    ]


def test_readings_follow_limits_applied_during_the_run():
    engine = make_engine(devices=[W])
    before = vitals_of(play(engine, 10 * MINUTE_MS))
    assert {p["hr_bpm"] for p in before} != {67}  # seed range: 53 to 122
    # 60 + 8 > 75 - 8: the narrowed range is empty, so (60 + 75) // 2.
    assert engine.set_limits(W, DeviceLimits(60, 75, 96), 10 * MINUTE_MS)
    after = vitals_of(play(engine, 40 * MINUTE_MS), after_ms=10 * MINUTE_MS)
    assert len(after) >= 25
    assert {p["hr_bpm"] for p in after} == {67}
    assert all(p["spo2_pct"] is None or p["spo2_pct"] >= 99 for p in after)


def test_readings_stay_in_a_narrowed_range_and_never_alert():
    engine = make_engine(devices=[W])
    engine.set_limits(W, DeviceLimits(60, 100, 95), 0)
    played = play(engine, 60 * MINUTE_MS)
    vitals = vitals_of(played)
    assert len(vitals) >= 50
    for p in vitals:
        assert 68 <= p["hr_bpm"] <= 92
        assert p["spo2_pct"] is None or p["spo2_pct"] >= 98
    assert played.of(W, "vitals_alert") == []


def test_set_limits_reports_whether_the_limits_changed():
    engine = make_engine(devices=[W])
    assert engine.set_limits(W, FIXED_LIMITS, 0) is False
    assert engine.set_limits(W, DeviceLimits(45, 110, 88), 0) is True
    assert engine.limits(W) == DeviceLimits(45, 110, 88)


# ======== criteria 22 and 23: vitals_alert from readings ========


def alerts(played, device_id=W):
    return [(m.elapsed_ms, m.payload) for m in played.of(device_id, "vitals_alert")]


def fields(payload):
    return {k: payload[k] for k in ("metric", "limit", "direction", "held_s")}


@pytest.mark.parametrize(
    ("name", "limits", "expected"),
    [
        ("hr-spike", FIXED_LIMITS, ("hr_bpm", 130, "above")),
        ("hr-spike", DeviceLimits(45, 110, 88), ("hr_bpm", 110, "above")),
        ("spo2-drop", FIXED_LIMITS, ("spo2_pct", 88, "below")),
        ("spo2-drop", DeviceLimits(45, 130, 95), ("spo2_pct", 95, "below")),
        ("no-pulse", FIXED_LIMITS, ("hr_bpm", 45, "below")),
        ("no-pulse", DeviceLimits(60, 130, 88), ("hr_bpm", 60, "below")),
    ],
)
def test_alert_after_10_s_with_the_current_limit(name, limits, expected):
    engine = make_engine([(name, W, T0)], devices=[W])
    engine.set_limits(W, limits, 0)
    ((at, payload),) = alerts(play(engine, T0 + 3 * MINUTE_MS))
    assert at == T0 + ALERT_MS
    metric, limit, direction = expected
    assert fields(payload) == {
        "metric": metric,
        "limit": limit,
        "direction": direction,
        "held_s": 10,
    }
    if name == "no-pulse":
        assert payload["value"] == 0


def test_no_alert_just_before_10_s():
    engine = make_engine([("hr-spike", W, T0)], devices=[W])
    assert alerts(play(engine, T0 + ALERT_MS)) == []
    assert len(alerts(play(engine, T0 + ALERT_MS + 1))) == 1


def test_alert_repeats_every_5_minutes_while_the_reading_stays_past(monkeypatch):
    monkeypatch.setattr(scenarios, "VITALS_DURATION_MS", 12 * MINUTE_MS)
    engine = make_engine([("hr-spike", W, T0)], devices=[W])
    times = [t for t, _ in alerts(play(engine, T0 + 30 * MINUTE_MS))]
    assert times == [T0 + ALERT_MS + k * REPEAT_MS for k in range(3)]


def test_episode_ends_when_the_reading_comes_back_inside(monkeypatch):
    monkeypatch.setattr(scenarios, "VITALS_DURATION_MS", 6 * MINUTE_MS)
    engine = make_engine([("spo2-drop", W, T0)], devices=[W])
    times = [t for t, _ in alerts(play(engine, T0 + 30 * MINUTE_MS))]
    # Third would be at 10 m 10 s; the scenario ended at 6 m.
    assert times == [T0 + ALERT_MS, T0 + ALERT_MS + REPEAT_MS]


def test_a_new_crossing_starts_a_new_10_s_timer(monkeypatch):
    monkeypatch.setattr(scenarios, "VITALS_DURATION_MS", 6 * MINUTE_MS)
    second = T0 + 7 * MINUTE_MS
    engine = make_engine([("hr-spike", W, T0), ("hr-spike", W, second)], devices=[W])
    times = [t for t, _ in alerts(play(engine, T0 + 30 * MINUTE_MS))]
    assert times == [
        T0 + ALERT_MS,
        T0 + ALERT_MS + REPEAT_MS,
        second + ALERT_MS,
        second + ALERT_MS + REPEAT_MS,
    ]


def test_limit_change_during_an_episode_is_used_by_the_next_message(monkeypatch):
    monkeypatch.setattr(scenarios, "VITALS_DURATION_MS", 12 * MINUTE_MS)
    engine = make_engine([("hr-spike", W, T0)], devices=[W])
    sent = alerts(play(engine, T0 + MINUTE_MS))
    engine.set_limits(W, DeviceLimits(45, 110, 88), T0 + MINUTE_MS)
    sent += alerts(play(engine, T0 + 12 * MINUTE_MS))
    assert [(t, p["limit"]) for t, p in sent] == [
        (T0 + ALERT_MS, 130),
        (T0 + ALERT_MS + REPEAT_MS, 110),
        (T0 + ALERT_MS + 2 * REPEAT_MS, 110),
    ]


def test_limit_change_before_the_first_alert_keeps_the_timer():
    engine = make_engine([("hr-spike", W, T0)], devices=[W])
    play(engine, T0 + 5 * SECOND_MS)
    engine.set_limits(W, DeviceLimits(45, 110, 88), T0 + 5 * SECOND_MS)
    ((at, payload),) = alerts(play(engine, T0 + 3 * MINUTE_MS))
    assert (at, payload["limit"]) == (T0 + ALERT_MS, 110)


def _patch_hr_crossing(monkeypatch, from_ms):
    """During spo2-drop, heart rate also reads 150 from from_ms (no scenario does
    both, so the tie can only be reached this way)."""
    original = Engine._reading

    def reading(self, device_id, metric, now_ms):
        value = original(self, device_id, metric, now_ms)
        active = self._active(device_id, now_ms)
        if (
            metric == "hr_bpm"
            and active is not None
            and active.name == "spo2-drop"
            and now_ms >= from_ms
        ):
            return 150
        return value

    monkeypatch.setattr(Engine, "_reading", reading)


def test_both_metrics_due_at_once_send_hr_first(monkeypatch):
    _patch_hr_crossing(monkeypatch, T0)
    engine = make_engine([("spo2-drop", W, T0)], devices=[W])
    sent = alerts(play(engine, T0 + 3 * MINUTE_MS))
    assert [(t, p["metric"]) for t, p in sent] == [
        (T0 + ALERT_MS, "hr_bpm"),
        (T0 + ALERT_MS, "spo2_pct"),
    ]


def test_each_metric_has_its_own_timer(monkeypatch):
    _patch_hr_crossing(monkeypatch, T0 + 4 * SECOND_MS)
    engine = make_engine([("spo2-drop", W, T0)], devices=[W])
    sent = alerts(play(engine, T0 + 4 * SECOND_MS))
    engine.set_limits(W, FIXED_LIMITS, T0 + 4 * SECOND_MS)  # re-checks the readings
    sent += alerts(play(engine, T0 + 3 * MINUTE_MS))
    assert [(t, p["metric"]) for t, p in sent] == [
        (T0 + ALERT_MS, "spo2_pct"),
        (T0 + 4 * SECOND_MS + ALERT_MS, "hr_bpm"),
    ]


def test_timer_under_10_s_at_run_end_sends_nothing_and_is_cut_off():
    engine = make_engine([("hr-spike", W, T0)], devices=[W])
    played = play(engine, T0 + 9 * SECOND_MS)
    assert alerts(played) == []
    assert engine.cut_off(T0 + 9 * SECOND_MS - 1) == [
        f"scenario hr-spike {W} {scenarios.CUT_OFF}"
    ]


def test_seed_limit_45_130_88_scenarios_keep_their_times_and_values():
    # Criterion 23: the three vitals scenarios on a 45 / 130 / 88 wearable.
    for name, metric, limit in (
        ("hr-spike", "hr_bpm", 130),
        ("spo2-drop", "spo2_pct", 88),
        ("no-pulse", "hr_bpm", 45),
    ):
        played = play(make_engine([(name, W, T0)], devices=[W]), T0 + 10 * MINUTE_MS)
        ((at, payload),) = alerts(played)
        assert at == T0 + ALERT_MS
        assert (payload["metric"], payload["limit"]) == (metric, limit)


def test_limits_only_change_the_limit_field():
    def run(limits):
        engine = make_engine([("hr-spike", W, T0)], devices=[W])
        engine.set_limits(W, limits, 0)
        return alerts(play(engine, T0 + 3 * MINUTE_MS))

    ((t1, a),) = run(FIXED_LIMITS)
    ((t2, b),) = run(DeviceLimits(45, 125, 88))  # readings unchanged: 53..117
    strip = {"message_id", "limit"}
    assert t1 == t2
    assert {k: v for k, v in a.items() if k not in strip} == {
        k: v for k, v in b.items() if k not in strip
    }
    assert (a["limit"], b["limit"]) == (130, 125)


# ======== criteria 19 and 20: live run, the shadow ========

ENDPOINT_TOPIC = "$aws/things/{}/shadow/{}"
LIMIT_WORDS = re.compile(r"\b(4[5-9]|[5-9]\d|1[0-3]\d)\b")


def shadow_service(desired=None, rejected_code=404):
    """A fake classic shadow: get -> get/accepted with desired limits, or
    get/rejected when desired is None."""

    def reply(device_id, suffix, payload):
        if suffix != "get":
            return []
        if desired is None:
            body = {"code": rejected_code, "message": "No shadow exists"}
            return [("get/rejected", json.dumps(body).encode())]
        doc = {"state": {"desired": {"limits": desired}}, "version": 1}
        return [("get/accepted", json.dumps(doc).encode())]

    return reply


def reports(world, device_id=W):
    return [
        json.loads(p["payload"])
        for p in world.shadow_publishes(device_id)
        if p["topic"] == ENDPOINT_TOPIC.format(device_id, "update")
    ]


def gets(world, device_id=W):
    return [
        p
        for p in world.shadow_publishes(device_id)
        if p["topic"] == ENDPOINT_TOPIC.format(device_id, "get")
    ]


def reported(doc):
    return {"state": {"reported": {"limits": doc}}}


def live(keys, world, argv=(), devices=f"{W},{B}", duration="40s"):  # noqa: F811
    return run_live(
        keys,
        world,
        ["--duration", duration, "--devices", devices, *argv],
        start_at="12:00",
    )


def device_lines(result):
    return [x for x in result.out_lines if re.match(r"^(wearable|bedroom)-\d{4}: ", x)]


def test_subscribes_qos_1_to_three_topics_after_the_connack(keys):  # noqa: F811
    world = World(fakes.FakeTime())
    live(keys, world)
    for device_id in (W, B):
        connack = min(t for t, d, ok in world.connacks if d == device_id and ok)
        ((at, _, topics),) = [s for s in world.subscribes if s[1] == device_id]
        assert at == connack
        assert topics == [
            (ENDPOINT_TOPIC.format(device_id, s), 1)
            for s in ("get/accepted", "get/rejected", "update/delta")
        ]


def test_gets_the_shadow_with_an_empty_message_after_the_suback(keys):  # noqa: F811
    world = World(fakes.FakeTime())
    live(keys, world)
    for device_id in (W, B):
        ((sub_at, *_),) = [s for s in world.subscribes if s[1] == device_id]
        (get,) = gets(world, device_id)
        assert get["at"] > sub_at  # on the SUBACK, a later loop
        assert get["payload"] == b""
        assert (get["qos"], get["retain"]) == (1, False)


def test_refused_subscribe_sends_no_get(keys):  # noqa: F811
    world = World(fakes.FakeTime(), suback_ok=lambda d: d != W)
    result = live(keys, world)
    assert gets(world, W) == []
    assert len(gets(world, B)) == 1
    assert f"{W}: shadow subscribe refused" in result.out_lines


def test_get_accepted_applies_and_reports_the_desired_limits(keys):  # noqa: F811
    desired = limits_doc(45, 117, 93)
    world = World(fakes.FakeTime(), shadow=shadow_service(desired))
    result = live(keys, world)
    assert reports(world) == [reported(desired)]
    assert device_lines(result) == [f"{W}: limits updated"]


def test_report_payload_is_compact_qos_1(keys):  # noqa: F811
    world = World(fakes.FakeTime(), shadow=shadow_service(limits_doc(45, 117, 93)))
    live(keys, world)
    (report,) = [
        p
        for p in world.shadow_publishes(W)
        if p["topic"] == ENDPOINT_TOPIC.format(W, "update")
    ]
    assert (report["qos"], report["retain"]) == (1, False)
    assert b" " not in report["payload"]


def test_applied_limits_drive_the_alert(keys):  # noqa: F811
    world = World(fakes.FakeTime(), shadow=shadow_service(limits_doc(45, 117, 93)))
    live(keys, world, ["--scenario", f"hr-spike:{W}@20s"], duration="2m")
    (alert,) = [
        json.loads(p["payload"])
        for p in world.publishes(W)
        if p["topic"].endswith("/vitals_alert")
    ]
    assert (alert["metric"], alert["limit"]) == ("hr_bpm", 117)


def test_applied_limits_narrow_the_live_readings(keys):  # noqa: F811
    world = World(fakes.FakeTime(), shadow=shadow_service(limits_doc(70, 90, 96)))
    live(keys, world, duration="20m")
    vitals = [
        json.loads(p["payload"])
        for p in world.publishes(W)
        if p["topic"].endswith("/vitals") and p["at"] > 2000
    ]
    assert len(vitals) >= 15
    for v in vitals:
        assert v["hr_bpm"] is None or 78 <= v["hr_bpm"] <= 82
        assert v["spo2_pct"] is None or v["spo2_pct"] >= 99


def test_desired_equal_to_the_seed_limits_is_reported_without_a_change_line(
    keys,  # noqa: F811
):
    world = World(fakes.FakeTime(), shadow=shadow_service(limits_doc()))
    result = live(keys, world)
    assert reports(world) == [reported(limits_doc())]
    assert device_lines(result) == []


def test_no_shadow_404_keeps_and_reports_the_seed_limits(keys):  # noqa: F811
    world = World(fakes.FakeTime(), shadow=shadow_service(None))
    result = live(keys, world)
    assert reports(world) == [reported(limits_doc())]
    assert device_lines(result) == []


def test_get_rejected_with_another_code_reports_nothing(keys):  # noqa: F811
    world = World(fakes.FakeTime(), shadow=shadow_service(None, rejected_code=500))
    result = live(keys, world)
    assert reports(world) == []
    assert device_lines(result) == []


def test_get_accepted_without_desired_limits_is_ignored(keys):  # noqa: F811
    def reply(device_id, suffix, payload):
        body = {"state": {"reported": {"limits": limits_doc()}}}
        return [("get/accepted", json.dumps(body).encode())] if suffix == "get" else []

    world = World(fakes.FakeTime(), shadow=reply)
    result = live(keys, world)
    assert reports(world) == []
    assert device_lines(result) == []


@pytest.mark.parametrize(
    "desired",
    [
        limits_doc(44, 130, 88),
        limits_doc(45, 131, 88),
        limits_doc(45, 130, 87.5),
        {**limits_doc(), "skin_temp_c": {"min": 31}},
        "not limits",
    ],
)
def test_invalid_desired_limits_are_rejected_not_applied(keys, desired):  # noqa: F811
    world = World(
        fakes.FakeTime(), shadow=shadow_service(desired), incoming=lambda d, t: []
    )
    result = live(keys, world, ["--scenario", f"hr-spike:{W}@20s"], duration="2m")
    assert reports(world) == []
    assert device_lines(result) == [f"{W}: limits rejected"]
    (alert,) = [
        json.loads(p["payload"])
        for p in world.publishes(W)
        if p["topic"].endswith("/vitals_alert")
    ]
    assert alert["limit"] == 130  # still the seed limits


def test_get_accepted_that_is_not_json_is_rejected(keys):  # noqa: F811
    def reply(device_id, suffix, payload):
        return [("get/accepted", b"{not json")] if suffix == "get" else []

    world = World(fakes.FakeTime(), shadow=reply)
    result = live(keys, world)
    assert reports(world) == []
    assert device_lines(result) == [f"{W}: limits rejected"]


def delta_at(at_ms, state):
    """update/delta pushed to W once, at the first loop from at_ms."""
    sent = []

    def incoming(device_id, now):
        if device_id == W and now >= at_ms and not sent:
            sent.append(now)
            body = {"state": state, "version": 2}
            return [("update/delta", json.dumps(body).encode())]
        return []

    return incoming


def test_full_delta_is_applied_and_reported(keys):  # noqa: F811
    world = World(
        fakes.FakeTime(),
        incoming=delta_at(10_000, {"limits": limits_doc(50, 110, 92)}),
    )
    result = live(keys, world)
    assert reports(world) == [reported(limits_doc(50, 110, 92))]
    assert device_lines(result) == [f"{W}: limits updated"]


def test_partial_delta_is_merged_into_the_current_limits(keys):  # noqa: F811
    world = World(
        fakes.FakeTime(),
        incoming=delta_at(10_000, {"limits": {"hr_bpm": {"max": 110}}}),
    )
    live(keys, world)
    assert reports(world) == [reported(limits_doc(45, 110, 88))]


def test_partial_delta_merges_into_limits_applied_earlier(keys):  # noqa: F811
    world = World(
        fakes.FakeTime(),
        shadow=shadow_service(limits_doc(50, 120, 92)),
        incoming=delta_at(10_000, {"limits": {"spo2_pct": {"min": 95}}}),
    )
    live(keys, world)
    assert reports(world) == [
        reported(limits_doc(50, 120, 92)),
        reported(limits_doc(50, 120, 95)),
    ]


def test_partial_delta_that_makes_the_limits_invalid_is_rejected(keys):  # noqa: F811
    world = World(
        fakes.FakeTime(),
        incoming=delta_at(10_000, {"limits": {"hr_bpm": {"max": 40}}}),
    )
    result = live(keys, world)
    assert reports(world) == []
    assert device_lines(result) == [f"{W}: limits rejected"]


def test_delta_without_limits_is_ignored(keys):  # noqa: F811
    world = World(fakes.FakeTime(), incoming=delta_at(10_000, {"other": 1}))
    result = live(keys, world)
    assert reports(world) == []
    assert device_lines(result) == []


def test_bedroom_subscribes_and_gets_but_ignores_the_content(keys):  # noqa: F811
    def incoming(device_id, now):
        if device_id == B and 10_000 <= now < 10_050:
            body = {"state": {"limits": limits_doc(50, 110, 92)}}
            return [("update/delta", json.dumps(body).encode())]
        return []

    world = World(
        fakes.FakeTime(),
        shadow=shadow_service(limits_doc(50, 110, 92)),
        incoming=incoming,
    )
    result = live(keys, world, devices=B)
    assert len(gets(world, B)) == 1
    assert [s for s in world.received if s[1] == B]  # it did receive them
    assert reports(world, B) == []
    assert device_lines(result) == []


def test_reconnect_subscribes_and_gets_again(keys):  # noqa: F811
    world = World(
        fakes.FakeTime(),
        drop=lambda d, t: d == W and 20_000 <= t < 20_050,
        shadow=shadow_service(limits_doc(45, 117, 93)),
    )
    result = live(keys, world, duration="1m")
    assert len(world.connect_times(W)) == 2
    assert len([s for s in world.subscribes if s[1] == W]) == 2
    assert len(gets(world, W)) == 2
    assert reports(world) == [reported(limits_doc(45, 117, 93))] * 2
    # Applied once: the second get finds them in use already.
    assert device_lines(result).count(f"{W}: limits updated") == 1


def test_output_never_includes_limit_values(keys):  # noqa: F811
    world = World(
        fakes.FakeTime(),
        shadow=shadow_service(limits_doc(57, 113, 93)),
        incoming=delta_at(10_000, {"limits": {"hr_bpm": {"max": 119}}}),
    )
    result = live(keys, world, ["--scenario", f"hr-spike:{W}@20s"], duration="2m")
    text = result.stdout + result.stderr
    assert "{" not in text and "limits" not in text.replace("limits updated", "")
    for value in ("57", "113", "93", "119"):
        assert not re.search(rf"\b{value}\b", text), value


# ======== criterion 24: dry-run and backfill ========


def tightened_seed(tmp_path, number=5, **kwargs):
    seed = copy.deepcopy(load_doc(SEED_FILE))
    seed["residents"][number - 1]["thresholds"] = thresholds(**kwargs)
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(seed), encoding="utf-8")
    return path


def test_dry_run_uses_the_seed_limits_and_opens_no_connection(
    tmp_path,
    guard_network_and_keys,  # noqa: F811
):
    seed = tightened_seed(tmp_path, hr_min=55, hr_max=110, spo2=92)
    result = run_dry(
        [
            "--dry-run",
            "--instant",
            "--duration",
            "10m",
            "--start-at",
            "12:00",
            "--devices",
            W,
            "--seed-file",
            str(seed),
            "--scenario",
            f"hr-spike:{W}@2m",
        ]
    )
    assert result.code == 0, result.stderr
    (alert,) = [x["payload"] for x in result.lines if x["topic"].endswith("_alert")]
    assert (alert["limit"], alert["direction"]) == (110, "above")
    assert result.elapsed_ms({"payload": alert}) == T0 + ALERT_MS
    assert not [x for x in result.lines if x["topic"].startswith("$aws/")]
    assert "limits" not in result.stderr


def run_backfill(out, seed_file, devices_file):
    err = io.StringIO()
    code = backfill.main(
        [
            "--from",
            "2026-03-10",
            "--to",
            "2026-03-10",
            "--falls",
            "0",
            "--random-seed",
            "1",
            "--seed-file",
            str(seed_file),
            "--devices-file",
            str(devices_file),
            "--out",
            str(out),
        ],
        stderr=err,
        today=backfill.date(2026, 11, 8),
    )
    return code, err.getvalue(), out


def test_backfill_readings_follow_seed_limits_and_stay_byte_identical(
    tmp_path, monkeypatch
):
    guard(monkeypatch)
    seed = tightened_seed(tmp_path, number=1, hr_min=60, hr_max=90, spo2=94)
    devices = small_devices_file(tmp_path)
    outputs = []
    for name in ("a", "b"):
        code, err, out = run_backfill(tmp_path / name, seed, devices)
        assert code == 0, err
        outputs.append(read_output(Run(code, "", err, out)))
    first, again = outputs
    assert first.files == again.files
    hrs = [
        line["hr_bpm"]
        for line in first.by_message("vitals")
        if line["device_id"] == "wearable-0001" and line["hr_bpm"] is not None
    ]
    assert hrs and min(hrs) >= 68 and max(hrs) <= 82
    assert not first.by_message("vitals_alert")
