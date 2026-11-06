"""Alert scenarios driven through the Engine (docs/features/simulator-alert-scenarios.md,
criteria 4 to 12 and 16). Synthetic residents only; no network, no keys."""

from __future__ import annotations

import contract
import payloads
import pytest
from conftest import START_EPOCH_MS
from scenario_helpers import (
    MINUTE_MS,
    SECOND_MS,
    accel_list,
    keyed,
    magnitude,
    run_engine,
    strip_id,
)

W = "wearable-0005"
OTHER = "wearable-0006"
T0 = 30 * SECOND_MS
WINDOW_MS = 3 * MINUTE_MS
UNTIL_MS = 15 * MINUTE_MS
ENVELOPE = {"schema_version", "message_id", "ts"}


@pytest.fixture(scope="module")
def baseline():
    return run_engine()


def ts(elapsed_ms: int) -> str:
    return payloads.format_ts(START_EPOCH_MS + elapsed_ms)


def baseline_vitals(baseline, device_id=W) -> dict[int, dict]:
    return {m.elapsed_ms: m.payload for m in baseline.of(device_id, "vitals")}


def assert_valid(played):
    for m in played.messages:
        assert contract.problems(m.topic, m.payload) == [], (m.message, m.elapsed_ms)


# ---- hr-spike (criterion 5) ----


def test_hr_spike_vitals_are_150_to_155_and_never_null_for_3_minutes(baseline):
    played = run_engine([("hr-spike", W, T0)])
    inside = [m for m in played.of(W, "vitals") if T0 <= m.elapsed_ms < T0 + WINDOW_MS]
    assert len(inside) == 3
    for m in inside:
        assert m.payload["hr_bpm"] is not None
        assert 150 <= m.payload["hr_bpm"] <= 155


def test_hr_spike_keeps_every_other_vitals_field_normal(baseline):
    played = run_engine([("hr-spike", W, T0)])
    normal = baseline_vitals(baseline)
    for m in played.of(W, "vitals"):
        expected = strip_id(normal[m.elapsed_ms])
        got = strip_id(m.payload)
        if T0 <= m.elapsed_ms < T0 + WINDOW_MS:
            expected.pop("hr_bpm"), got.pop("hr_bpm")
        assert got == expected, m.elapsed_ms


def test_hr_spike_readings_are_normal_again_from_t0_plus_3_minutes(baseline):
    played = run_engine([("hr-spike", W, T0)])
    normal = baseline_vitals(baseline)
    after = [m for m in played.of(W, "vitals") if m.elapsed_ms >= T0 + WINDOW_MS]
    assert len(after) >= 5
    for m in after:
        assert strip_id(m.payload) == strip_id(normal[m.elapsed_ms])


def test_hr_spike_sends_one_vitals_alert_at_t0_plus_10_s(baseline):
    played = run_engine([("hr-spike", W, T0)])
    alerts = played.of(W, "vitals_alert")
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.elapsed_ms == T0 + 10 * SECOND_MS
    assert alert.payload["ts"] == ts(T0 + 10 * SECOND_MS)
    assert strip_id(alert.payload) == {
        "schema_version": 1,
        "ts": ts(T0 + 10 * SECOND_MS),
        "metric": "hr_bpm",
        "value": alert.payload["value"],
        "limit": 130,
        "direction": "above",
        "held_s": 10,
    }
    assert 150 <= alert.payload["value"] <= 155


def test_hr_spike_alert_value_is_the_reading_sent_just_before_it(baseline):
    # Start exactly on one of W's vitals slots: that reading is the current value.
    slot = min(t for t in baseline_vitals(baseline) if t >= T0)
    played = run_engine([("hr-spike", W, slot)])
    first = next(m for m in played.of(W, "vitals") if m.elapsed_ms == slot)
    (alert,) = played.of(W, "vitals_alert")
    assert alert.elapsed_ms == slot + 10 * SECOND_MS
    assert 150 <= first.payload["hr_bpm"] <= 155
    assert alert.payload["value"] == first.payload["hr_bpm"]


def test_reading_due_exactly_at_t0_plus_3_minutes_is_normal(baseline):
    slot = min(t for t in baseline_vitals(baseline) if t >= T0)
    played = run_engine([("hr-spike", W, slot)])
    at_end = next(m for m in played.of(W, "vitals") if m.elapsed_ms == slot + WINDOW_MS)
    normal = baseline_vitals(baseline)
    assert strip_id(at_end.payload) == strip_id(normal[slot + WINDOW_MS])


# ---- spo2-drop (criterion 6) ----


def test_spo2_drop_vitals_are_82_to_84_never_null_other_fields_normal(baseline):
    played = run_engine([("spo2-drop", W, T0)])
    normal = baseline_vitals(baseline)
    inside = 0
    for m in played.of(W, "vitals"):
        got, expected = strip_id(m.payload), strip_id(normal[m.elapsed_ms])
        if T0 <= m.elapsed_ms < T0 + WINDOW_MS:
            inside += 1
            assert got["spo2_pct"] is not None and 82 <= got["spo2_pct"] <= 84
            got.pop("spo2_pct"), expected.pop("spo2_pct")
        assert got == expected, m.elapsed_ms
    assert inside == 3


def test_spo2_drop_sends_one_vitals_alert_limit_88_below(baseline):
    slot = min(t for t in baseline_vitals(baseline) if t >= T0)
    played = run_engine([("spo2-drop", W, slot)])
    first = next(m for m in played.of(W, "vitals") if m.elapsed_ms == slot)
    (alert,) = played.of(W, "vitals_alert")
    assert alert.elapsed_ms == slot + 10 * SECOND_MS
    assert strip_id(alert.payload) == {
        "schema_version": 1,
        "ts": ts(slot + 10 * SECOND_MS),
        "metric": "spo2_pct",
        "value": first.payload["spo2_pct"],
        "limit": 88,
        "direction": "below",
        "held_s": 10,
    }


def test_spo2_drop_alert_value_is_in_range_before_any_reading():
    played = run_engine([("spo2-drop", W, T0)])
    (alert,) = played.of(W, "vitals_alert")
    assert alert.elapsed_ms == T0 + 10 * SECOND_MS
    assert 82 <= alert.payload["value"] <= 84


# ---- no-pulse (criterion 7) ----


def test_no_pulse_vitals_have_null_hr_and_spo2_and_activity_0(baseline):
    played = run_engine([("no-pulse", W, T0)])
    normal = baseline_vitals(baseline)
    inside = 0
    for m in played.of(W, "vitals"):
        got, expected = strip_id(m.payload), strip_id(normal[m.elapsed_ms])
        if T0 <= m.elapsed_ms < T0 + WINDOW_MS:
            inside += 1
            assert got["hr_bpm"] is None
            assert got["spo2_pct"] is None
            assert got["activity_pct"] == 0
            for name in ("hr_bpm", "spo2_pct", "activity_pct"):
                got.pop(name), expected.pop(name)
        assert got == expected, m.elapsed_ms
    assert inside == 3


def test_no_pulse_sends_one_vitals_alert_value_0_limit_45_below():
    played = run_engine([("no-pulse", W, T0)])
    (alert,) = played.of(W, "vitals_alert")
    assert alert.elapsed_ms == T0 + 10 * SECOND_MS
    assert strip_id(alert.payload) == {
        "schema_version": 1,
        "ts": ts(T0 + 10 * SECOND_MS),
        "metric": "hr_bpm",
        "value": 0,
        "limit": 45,
        "direction": "below",
        "held_s": 10,
    }


def test_no_vitals_message_ever_has_hr_bpm_0():
    played = run_engine([("no-pulse", W, T0), ("no-pulse", OTHER, T0)])
    for m in played.messages:
        if m.message == "vitals":
            assert m.payload["hr_bpm"] != 0


# ---- vitals_alert repeats and wear (criterion 8) ----


@pytest.mark.parametrize("name", ["hr-spike", "spo2-drop", "no-pulse"])
def test_exactly_one_vitals_alert_per_3_minute_scenario(name):
    played = run_engine([(name, W, T0)], until_ms=20 * MINUTE_MS)
    assert len(played.of(W, "vitals_alert")) == 1
    assert [m for m in played.messages if m.message == "vitals_alert"] == played.of(
        W, "vitals_alert"
    )


def charging_start(device_id: str, start_at: str) -> int:
    """Elapsed time the wearable goes on its charger (status worn false)."""
    played = run_engine(start_at=start_at, until_ms=60 * MINUTE_MS)
    worn = None
    for m in played.of(device_id, "status"):
        if worn is True and m.payload["worn"] is False:
            return m.elapsed_ms
        worn = m.payload["worn"]
    raise AssertionError("no charging start in the hour")


def test_vitals_alert_is_not_sent_once_the_wearable_is_off_the_wrist():
    off = charging_start(W, "18:40")
    assert off > 20 * SECOND_MS
    played = run_engine(
        [("hr-spike", W, off - 5 * SECOND_MS)],
        start_at="18:40",
        until_ms=off + WINDOW_MS,
    )
    assert f"scenario hr-spike {W} started" in played.lines()
    assert played.of(W, "vitals_alert") == []
    assert [m for m in played.of(W, "vitals") if m.elapsed_ms >= off] == []


# ---- fall (criterion 9) ----


def test_fall_message_is_sent_at_t0_plus_20_s_with_ts_of_the_impact():
    played = run_engine([("fall", W, T0)])
    (fall,) = played.of(W, "fall")
    assert fall.elapsed_ms == T0 + 20 * SECOND_MS
    assert fall.payload["ts"] == ts(T0)
    assert fall.topic == f"silvercare/v1/{W}/fall"


def test_fall_message_values_are_in_their_ranges():
    (fall,) = run_engine([("fall", W, T0)]).of(W, "fall")
    p = fall.payload
    assert set(p) == ENVELOPE | {"confidence", "peak_g", "still_s", "accel_window"}
    assert 0.85 <= p["confidence"] <= 0.95
    assert 3.0 <= p["peak_g"] <= 4.5
    assert p["still_s"] == 20
    assert p["accel_window"]["hz"] == 25
    assert p["accel_window"]["start_offset_ms"] == -1000
    assert len(p["accel_window"]["samples"]) == 50


def test_fall_window_upright_then_impact_at_index_25_then_lying():
    (fall,) = run_engine([("fall", W, T0)]).of(W, "fall")
    samples = fall.payload["accel_window"]["samples"]
    peak_g = fall.payload["peak_g"]
    for sample in samples:
        assert len(sample) == 3
        for v in sample:
            assert -16 <= v <= 16
            assert round(v, 3) == v
    magnitudes = [magnitude(s) for s in samples]
    assert max(range(50), key=magnitudes.__getitem__) == 25
    assert abs(magnitudes[25] - peak_g) <= 0.002
    for s in samples[:25]:
        assert 0.9 <= magnitude(s) <= 1.1  # about 1 g before the impact
    signs = set()
    for s in samples[26:]:
        assert 0.9 <= magnitude(s) <= 1.1
        assert abs(s[0]) == max(abs(v) for v in s)  # lying: mostly x
        signs.add(s[0] > 0)
    assert len(signs) == 1  # one lying orientation


def test_while_down_vitals_have_activity_0_to_3_lying_accel_and_normal_vitals(
    baseline,
):
    played = run_engine([("fall", W, T0)])
    (fall,) = played.of(W, "fall")
    lying_positive = fall.payload["accel_window"]["samples"][-1][0] > 0
    normal = baseline_vitals(baseline)
    down = [
        m for m in played.of(W, "vitals") if T0 <= m.elapsed_ms < T0 + 10 * MINUTE_MS
    ]
    assert len(down) == 10
    for m in down:
        p = m.payload
        assert 0 <= p["activity_pct"] <= 3
        accel = accel_list(p)
        assert abs(accel[0]) == max(abs(v) for v in accel)
        assert (accel[0] > 0) == lying_positive
        assert 0.9 <= magnitude(accel) <= 1.1
        for name in ("hr_bpm", "spo2_pct", "skin_temp_c"):
            assert p[name] == normal[m.elapsed_ms][name], name


def test_resident_gets_up_after_10_minutes_and_the_fall_ends(baseline):
    played = run_engine([("fall", W, T0)])
    normal = baseline_vitals(baseline)
    after = [m for m in played.of(W, "vitals") if m.elapsed_ms >= T0 + 10 * MINUTE_MS]
    assert after
    for m in after:
        assert strip_id(m.payload) == strip_id(normal[m.elapsed_ms])
    assert played.notices == [
        (T0, f"scenario fall {W} started"),
        (T0 + 10 * MINUTE_MS, f"scenario fall {W} ended"),
    ]


# ---- im-ok (criterion 10) ----


def test_im_ok_sends_one_envelope_only_alert_cancel_at_once():
    played = run_engine([("im-ok", W, T0)])
    (cancel,) = played.of(W, "alert_cancel")
    assert cancel.elapsed_ms == T0
    assert set(cancel.payload) == ENVELOPE
    assert cancel.payload["ts"] == ts(T0)
    assert cancel.payload["schema_version"] == 1
    assert played.lines() == [
        f"scenario im-ok {W} started",
        f"scenario im-ok {W} ended",
    ]


def test_im_ok_after_the_fall_message_ends_the_fall_at_once(baseline):
    at = T0 + 25 * SECOND_MS  # 5 s after the fall message
    played = run_engine([("fall", W, T0), ("im-ok", W, at)])
    (cancel,) = played.of(W, "alert_cancel")
    assert cancel.elapsed_ms == at and cancel.payload["ts"] == ts(at)
    assert played.notices == [
        (T0, f"scenario fall {W} started"),
        (at, f"scenario im-ok {W} started"),
        (at, f"scenario fall {W} ended"),
        (at, f"scenario im-ok {W} ended"),
    ]
    normal = baseline_vitals(baseline)
    for m in played.of(W, "vitals"):
        if m.elapsed_ms >= at:
            assert strip_id(m.payload) == strip_id(normal[m.elapsed_ms])


@pytest.mark.parametrize("press_s", [0, 5, 19, 20, 24])
def test_early_im_ok_is_deferred_to_5_s_after_the_fall_message(press_s):
    at = T0 + press_s * SECOND_MS
    played = run_engine([("fall", W, T0), ("im-ok", W, at)])
    (fall,) = played.of(W, "fall")
    (cancel,) = played.of(W, "alert_cancel")
    assert fall.elapsed_ms == T0 + 20 * SECOND_MS
    assert cancel.elapsed_ms == fall.elapsed_ms + 5 * SECOND_MS
    assert cancel.payload["ts"] == ts(cancel.elapsed_ms)
    assert played.messages.index(fall) < played.messages.index(cancel)
    assert played.notices == [
        (T0, f"scenario fall {W} started"),
        (at, f"scenario im-ok {W} started (after the fall message)"),
        (cancel.elapsed_ms, f"scenario fall {W} ended"),
        (cancel.elapsed_ms, f"scenario im-ok {W} ended"),
    ]


def test_deferred_im_ok_also_gets_the_resident_up(baseline):
    played = run_engine([("fall", W, T0), ("im-ok", W, T0 + 5 * SECOND_MS)])
    up = T0 + 25 * SECOND_MS
    normal = baseline_vitals(baseline)
    later = [m for m in played.of(W, "vitals") if m.elapsed_ms >= up]
    assert later
    for m in later:
        assert strip_id(m.payload) == strip_id(normal[m.elapsed_ms])


def test_im_ok_during_hr_spike_cancels_but_the_spike_goes_on():
    played = run_engine([("hr-spike", W, T0), ("im-ok", W, T0 + 60 * SECOND_MS)])
    assert [m.elapsed_ms for m in played.of(W, "alert_cancel")] == [T0 + MINUTE_MS]
    inside = [m for m in played.of(W, "vitals") if T0 <= m.elapsed_ms < T0 + WINDOW_MS]
    assert all(150 <= m.payload["hr_bpm"] <= 155 for m in inside)
    assert played.lines() == [
        f"scenario hr-spike {W} started",
        f"scenario im-ok {W} started",
        f"scenario im-ok {W} ended",
        f"scenario hr-spike {W} ended",
    ]


# ---- skip reasons (criteria 4, 11) ----


def charging_wearable(start_at: str) -> str:
    """A wearable on its charger from the start until at least 1 minute in."""
    played = run_engine(start_at=start_at, until_ms=MINUTE_MS + 1)
    for n in range(1, 21):
        device_id = f"wearable-{n:04d}"
        statuses = played.of(device_id, "status")
        if all(m.payload["charging"] for m in statuses):
            return device_id
    raise AssertionError("no wearable charging")


@pytest.mark.parametrize("name", ["fall", "hr-spike", "no-pulse", "spo2-drop"])
def test_scenario_on_a_wearable_off_the_wrist_is_skipped_not_worn(name):
    target = charging_wearable("19:30")
    played = run_engine([(name, target, T0)], start_at="19:30", until_ms=5 * MINUTE_MS)
    assert played.lines() == [f"scenario {name} {target} skipped: not worn"]
    assert [m.message for m in played.of(target)] == ["status"]


def test_im_ok_works_while_the_wearable_is_off_the_wrist():
    target = charging_wearable("19:30")
    played = run_engine([("im-ok", target, T0)], start_at="19:30", until_ms=MINUTE_MS)
    assert [m.elapsed_ms for m in played.of(target, "alert_cancel")] == [T0]
    assert played.lines()[0] == f"scenario im-ok {target} started"


@pytest.mark.parametrize("second", ["fall", "hr-spike", "no-pulse", "spo2-drop"])
def test_second_scenario_on_a_busy_wearable_is_skipped(second):
    played = run_engine([("hr-spike", W, T0), (second, W, T0 + 30 * SECOND_MS)])
    assert played.lines() == [
        f"scenario hr-spike {W} started",
        f"scenario {second} {W} skipped: busy",
        f"scenario hr-spike {W} ended",
    ]
    assert played.of(W, "fall") == []
    assert len(played.of(W, "vitals_alert")) == 1


def test_a_fall_keeps_the_wearable_busy_until_it_ends():
    played = run_engine([("fall", W, T0), ("no-pulse", W, T0 + 9 * MINUTE_MS)])
    assert f"scenario no-pulse {W} skipped: busy" in played.lines()


def test_scenario_typed_at_the_end_of_the_previous_one_starts():
    end = T0 + WINDOW_MS
    played = run_engine([("hr-spike", W, T0)], typed=[(end, "spo2-drop", W)])
    assert f"scenario spo2-drop {W} started" in played.lines()


def test_scenario_flagged_at_the_end_of_the_previous_one_starts():
    # [t0, t0 + 3 min): at t0 + 3 min the first scenario is over.
    end = T0 + WINDOW_MS
    played = run_engine([("hr-spike", W, T0), ("spo2-drop", W, end)])
    assert f"scenario spo2-drop {W} started" in played.lines(), played.lines()


@pytest.mark.parametrize(("gap_s", "skipped"), [(1, True), (4, True), (5, False)])
def test_im_ok_within_5_s_of_the_previous_is_skipped_too_soon(gap_s, skipped):
    played = run_engine([("im-ok", W, T0), ("im-ok", W, T0 + gap_s * SECOND_MS)])
    second = played.notices[2:]
    if skipped:
        assert second == [
            (T0 + gap_s * SECOND_MS, f"scenario im-ok {W} skipped: too soon")
        ]
        assert len(played.of(W, "alert_cancel")) == 1
    else:
        assert second[0][1] == f"scenario im-ok {W} started"
        assert len(played.of(W, "alert_cancel")) == 2


def test_im_ok_is_never_busy_and_scenarios_on_other_wearables_are_independent(
    baseline,
):
    played = run_engine(
        [("fall", W, T0), ("hr-spike", OTHER, T0), ("im-ok", W, T0 + 40 * SECOND_MS)]
    )
    assert f"scenario hr-spike {OTHER} started" in played.lines()
    assert f"scenario im-ok {W} started" in played.lines()
    assert len(played.of(W, "fall")) == 1
    assert len(played.of(OTHER, "vitals_alert")) == 1
    assert played.of(OTHER, "fall") == [] and played.of(W, "vitals_alert") == []


# ---- every message follows the contract (criterion 17) ----


def test_every_message_of_every_scenario_follows_the_contract():
    flags = [
        ("fall", "wearable-0001", T0),
        ("hr-spike", "wearable-0002", T0),
        ("spo2-drop", "wearable-0003", T0),
        ("no-pulse", "wearable-0004", T0),
        ("im-ok", "wearable-0005", T0),
        ("fall", "wearable-0006", T0),
        ("im-ok", "wearable-0006", T0 + 3 * SECOND_MS),
        ("fall", "wearable-0007", T0),
        ("im-ok", "wearable-0007", T0 + 2 * MINUTE_MS),
    ]
    played = run_engine(flags, until_ms=20 * MINUTE_MS)
    kinds = {m.message for m in played.messages}
    assert {"fall", "vitals_alert", "alert_cancel"} <= kinds
    assert_valid(played)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_fall_messages_follow_the_contract_for_many_seeds(seed):
    flags = [("fall", f"wearable-{n:04d}", T0) for n in range(1, 21)]
    played = run_engine(flags, until_ms=MINUTE_MS, seed=seed)
    falls = [m for m in played.messages if m.message == "fall"]
    assert len(falls) == 20
    for m in falls:
        assert contract.problems(m.topic, m.payload) == []


# ---- determinism and no change without scenarios (criteria 12, 16) ----


def test_same_seed_start_and_scenarios_give_the_same_messages():
    flags = [("fall", W, T0), ("hr-spike", OTHER, T0), ("im-ok", W, 2 * MINUTE_MS)]
    first, second = run_engine(flags), run_engine(flags)
    assert [(m.elapsed_ms, m.topic, m.payload) for m in first.messages] == [
        (m.elapsed_ms, m.topic, m.payload) for m in second.messages
    ]
    assert first.notices == second.notices


def test_a_different_seed_gives_different_scenario_values():
    one = run_engine([("fall", W, T0)], seed=1).of(W, "fall")[0].payload
    two = run_engine([("fall", W, T0)], seed=2).of(W, "fall")[0].payload
    assert one["accel_window"] != two["accel_window"]


def test_a_scenario_changes_no_other_device_messages(baseline):
    played = run_engine(
        [
            ("fall", W, T0),
            ("im-ok", W, T0 + 2 * MINUTE_MS),
            ("hr-spike", W, 5 * MINUTE_MS),
        ]
    )
    others = {k: v for k, v in keyed(played).items() if k[1] != W}
    expected = {k: v for k, v in keyed(baseline).items() if k[1] != W}
    assert others == expected


def test_a_scenario_leaves_the_wearables_status_messages_unchanged(baseline):
    played = run_engine([("fall", W, T0), ("no-pulse", W, 11 * MINUTE_MS)])
    assert [(m.elapsed_ms, strip_id(m.payload)) for m in played.of(W, "status")] == [
        (m.elapsed_ms, strip_id(m.payload)) for m in baseline.of(W, "status")
    ]


def test_a_run_without_scenarios_has_no_scenario_messages_or_lines(baseline):
    assert baseline.notices == []
    assert {m.message for m in baseline.messages} <= {
        "vitals",
        "status",
        "bed",
        "motion",
    }


# ---- a scenario ending at t is over for everything at t (criterion 4) ----


def test_vitals_scenario_ends_before_the_next_one_starts_at_the_same_time(baseline):
    slot = min(t for t in baseline_vitals(baseline) if t >= T0)
    end = slot + WINDOW_MS
    played = run_engine([("hr-spike", W, slot), ("spo2-drop", W, end)])
    assert played.notices[:3] == [
        (slot, f"scenario hr-spike {W} started"),
        (end, f"scenario hr-spike {W} ended"),
        (end, f"scenario spo2-drop {W} started"),
    ]
    # The reading due at that moment belongs to the new scenario only.
    at_end = next(m for m in played.of(W, "vitals") if m.elapsed_ms == end)
    normal = baseline_vitals(baseline)[end]
    assert 82 <= at_end.payload["spo2_pct"] <= 84
    assert at_end.payload["hr_bpm"] == normal["hr_bpm"]
    assert [m.elapsed_ms for m in played.of(W, "vitals_alert")] == [
        slot + 10 * SECOND_MS,
        end + 10 * SECOND_MS,
    ]


def test_fall_ending_at_10_minutes_ends_before_a_new_fall_starts():
    end = T0 + 10 * MINUTE_MS
    played = run_engine([("fall", W, T0), ("fall", W, end)], until_ms=12 * MINUTE_MS)
    assert played.notices == [
        (T0, f"scenario fall {W} started"),
        (end, f"scenario fall {W} ended"),
        (end, f"scenario fall {W} started"),
    ]
    assert [m.elapsed_ms for m in played.of(W, "fall")] == [
        T0 + 20 * SECOND_MS,
        end + 20 * SECOND_MS,
    ]


def test_engine_cut_off_lists_only_what_did_not_finish():
    from scenario_helpers import make_engine, play

    engine = make_engine([("fall", W, T0), ("im-ok", W, T0 + 5 * SECOND_MS)])
    play(engine, T0 + 24 * SECOND_MS)  # the deferred cancel is due at t0 + 25 s
    assert engine.cut_off(T0 + 24 * SECOND_MS - 1) == [
        f"scenario im-ok {W} cut off: run ended",
        f"scenario fall {W} cut off: run ended",
    ]
    finished = make_engine([("hr-spike", W, T0), ("im-ok", OTHER, T0)])
    play(finished, T0 + WINDOW_MS + 1)
    assert finished.cut_off(T0 + WINDOW_MS) == []
