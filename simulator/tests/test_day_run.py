"""A simulated 24 h --instant dry-run for all 40 devices (criteria 7 to 15, 20, 21).

One shared run (conftest.day_run): random seed 7, routine 03:00, real start
2026-11-03T04:00:00.250Z (12:00 SGT).
"""

from __future__ import annotations

import itertools
import re
from collections import Counter

import contract
import pytest
import routine_ref as ref
from conftest import (
    DAY_MS,
    DAY_START_AT,
    HOUR_MS,
    MINUTE_MS,
    START_EPOCH_MS,
    message_of,
    ts_to_epoch_ms,
)

START_ROUTINE_MS = ref.hhmm_ms(DAY_START_AT)
WEARABLES = [f"wearable-{n:04d}" for n in range(1, 21)]
BEDROOMS = [f"bedroom-{n:04d}" for n in range(1, 21)]


def number(device_id: str) -> int:
    return int(device_id.split("-")[1])


@pytest.fixture(scope="session")
def residents_by_number(seed_doc) -> dict[int, dict]:
    """Device number -> resident (the seed assigns wearable-N and bedroom-N to res-N;
    checked by test_seed_assigns_device_n_to_resident_n)."""
    by_id = {r["resident_id"]: r for r in seed_doc["residents"]}
    return {
        number(d["device_id"]): by_id[d["resident_id"]] for d in seed_doc["devices"]
    }


@pytest.fixture(scope="session")
def offsets(by_device) -> dict[int, int]:
    """Each resident's offset, recovered from when their bed becomes occupied."""
    out = {}
    for n in range(1, 21):
        beds = [
            (e, line)
            for e, line in by_device[f"bedroom-{n:04d}"]
            if message_of(line) == "bed" and line["payload"]["occupied"] and e > 4000
        ]
        assert len(beds) == 1
        out[n] = ref.signed(START_ROUTINE_MS + beds[0][0] - 22 * HOUR_MS)
    return out


def of_type(entries, message):
    return [(e, line) for e, line in entries if message_of(line) == message]


def test_seed_assigns_device_n_to_resident_n(seed_doc):
    for d in seed_doc["devices"]:
        assert d["resident_id"] == f"res-{number(d['device_id']):04d}"


# ---- contract (criterion 9) ----


def test_every_message_follows_the_contract(day_run):
    bad = Counter()
    for line in day_run.lines:
        for problem in contract.problems(line["topic"], line["payload"]):
            bad[(message_of(line), problem)] += 1
    assert dict(bad) == {}


def test_dry_run_lines_have_only_topic_and_payload(day_run):
    assert all(set(line) == {"topic", "payload"} for line in day_run.lines)


def test_message_ids_are_unique(day_run):
    ids = [line["payload"]["message_id"] for line in day_run.lines]
    assert len(ids) == len(set(ids))


def test_all_40_devices_publish_only_their_own_message_types(by_device):
    assert sorted(by_device) == sorted(WEARABLES + BEDROOMS)
    for device_id, entries in by_device.items():
        kinds = {message_of(line) for _, line in entries}
        if device_id.startswith("wearable-"):
            assert kinds == {"vitals", "status"}
        else:
            assert kinds == {"status", "bed", "motion"}


def test_messages_are_printed_in_time_order(day_run):
    times = [ts_to_epoch_ms(line["payload"]["ts"]) for line in day_run.lines]
    assert times == sorted(times)


def test_ts_is_real_time_from_the_start_not_the_routine_clock(day_run):
    times = [ts_to_epoch_ms(line["payload"]["ts"]) for line in day_run.lines]
    assert min(times) == START_EPOCH_MS
    assert max(times) < START_EPOCH_MS + DAY_MS
    # Real start 04:00 UTC; the routine clock (03:00 SGT = 19:00 UTC the day before)
    # must not leak into ts.
    assert day_run.lines[0]["payload"]["ts"] == "2026-11-03T04:00:00.250Z"
    assert not any(
        line["payload"]["ts"].startswith("2026-11-02T19:") for line in day_run.lines
    )


def test_no_message_at_or_after_the_end_of_the_duration(day_run):
    assert max(day_run.elapsed_ms(line) for line in day_run.lines) < DAY_MS


# ---- summaries (criteria 18, 20) ----

SUMMARY_RE = re.compile(
    r"^(?P<sgt>\d\d:\d\d) SGT routine (?P<routine>\d\d:\d\d) \| connected (\d+)/(\d+)"
    r" \| sent vitals (?P<vitals>\d+) status (?P<status>\d+) bed (?P<bed>\d+)"
    r" motion (?P<motion>\d+) fall (?P<fall>\d+) vitals_alert (?P<vitals_alert>\d+)"
    r" alert_cancel (?P<alert_cancel>\d+) \| dropped (\d+) \| failed (\d+)$"
)


KINDS = ("vitals", "status", "bed", "motion", "fall", "vitals_alert", "alert_cancel")


def test_one_summary_line_per_minute_on_stderr(day_run):
    lines = day_run.stderr.splitlines()
    summaries = [SUMMARY_RE.fullmatch(line) for line in lines[1:-1]]
    assert all(summaries)
    assert len(summaries) == 24 * 60
    assert summaries[0]["sgt"] == "12:01" and summaries[0]["routine"] == "03:01"
    assert summaries[-1]["sgt"] == "12:00" and summaries[-1]["routine"] == "03:00"


def test_summary_counts_add_up_to_the_printed_messages(day_run):
    printed = Counter(message_of(line) for line in day_run.lines)
    summed = Counter()
    for line in day_run.stderr.splitlines()[1:-1]:
        match = SUMMARY_RE.fullmatch(line)
        for kind in KINDS:
            summed[kind] += int(match[kind])
    assert +summed == printed
    for kind in ("fall", "vitals_alert", "alert_cancel"):
        assert printed[kind] == 0  # no scenarios in this run
    total = day_run.stderr.splitlines()[-1]
    assert total == (
        f"total 24:00:00 | connected 40/40 | sent vitals {printed['vitals']}"
        f" status {printed['status']} bed {printed['bed']}"
        f" motion {printed['motion']} fall 0 vitals_alert 0 alert_cancel 0"
        " | dropped 0 | failed 0"
    )


def test_summaries_hold_no_payload_values(day_run):
    assert "{" not in day_run.stderr
    assert "hr_bpm" not in day_run.stderr


# ---- start-up and spreading (criterion 15) ----


def test_startup_status_first_then_bed_and_motion_at_most_10_devices_per_second(
    by_device,
):
    starts = {}
    for device_id, entries in by_device.items():
        first_e, first = entries[0]
        assert message_of(first) == "status"
        starts[device_id] = first_e
        if device_id.startswith("bedroom-"):
            assert [message_of(line) for _, line in entries[:3]] == [
                "status",
                "bed",
                "motion",
            ]
            assert {e for e, _ in entries[:3]} == {first_e}
    per_second = Counter((START_EPOCH_MS + e) // 1000 for e in starts.values())
    assert max(per_second.values()) <= 10
    assert max(starts.values()) < 5000


def test_startup_bed_and_motion_match_the_routine(by_device, offsets):
    for n in range(1, 21):
        entries = by_device[f"bedroom-{n:04d}"]
        _, bed = entries[1]
        _, motion = entries[2]
        phase = ref.phase(START_ROUTINE_MS, offsets[n])
        assert bed["payload"]["occupied"] == (phase == "asleep")
        assert motion["payload"]["motion"] is False  # 03:00 +-30 min: asleep


def test_wearables_have_distinct_vitals_seconds_3_s_apart(by_device):
    residues = []
    for device_id in WEARABLES:
        times = {e % MINUTE_MS for e, _ in of_type(by_device[device_id], "vitals")}
        assert len(times) == 1, device_id
        residues.append(times.pop())
    residues.sort()
    assert [b - a for a, b in itertools.pairwise(residues)] == [3000] * 19


def status_slot(entries) -> int:
    residues = Counter(e % (5 * MINUTE_MS) for e, _ in of_type(entries, "status"))
    return residues.most_common(1)[0][0]


def test_devices_have_distinct_status_offsets_7_5_s_apart(by_device):
    slots = sorted(status_slot(entries) for entries in by_device.values())
    assert [b - a for a, b in itertools.pairwise(slots)] == [7500] * 39


@pytest.mark.parametrize("device_id", WEARABLES + BEDROOMS)
def test_periodic_status_every_5_minutes(by_device, device_id):
    entries = by_device[device_id]
    slot = status_slot(entries)
    periodic = [e for e, _ in of_type(entries, "status") if e % 300_000 == slot]
    gaps = {b - a for a, b in itertools.pairwise(periodic)}
    assert gaps == {300_000}
    assert len(periodic) in (287, 288)


def test_no_second_after_the_first_minute_has_more_than_4_publishes(day_run):
    per_second = Counter(
        ts_to_epoch_ms(line["payload"]["ts"]) // 1000
        for line in day_run.lines
        if day_run.elapsed_ms(line) >= MINUTE_MS
    )
    assert max(per_second.values()) <= 4


# ---- counts (criterion 21) ----


@pytest.mark.parametrize("device_id", WEARABLES)
def test_vitals_about_1440_minus_60_per_wearable(by_device, device_id):
    count = len(of_type(by_device[device_id], "vitals"))
    assert 1379 <= count <= 1380


@pytest.mark.parametrize("device_id", WEARABLES)
def test_vitals_every_60_s_with_one_hour_gap_for_charging(by_device, device_id):
    times = [e for e, _ in of_type(by_device[device_id], "vitals")]
    gaps = Counter(b - a for a, b in itertools.pairwise(times))
    assert gaps == {MINUTE_MS: len(times) - 2, 61 * MINUTE_MS: 1}


@pytest.mark.parametrize("n", range(1, 21))
def test_no_vitals_while_charging(by_device, offsets, n):
    for e, _ in of_type(by_device[f"wearable-{n:04d}"], "vitals"):
        assert ref.phase(START_ROUTINE_MS + e, offsets[n]) != "charging"


@pytest.mark.parametrize("n", range(1, 21))
def test_wearable_status_on_each_worn_or_charging_change(by_device, offsets, n):
    entries = by_device[f"wearable-{n:04d}"]
    slot = status_slot(entries)
    statuses = of_type(entries, "status")
    changes = {
        (e, (line["payload"]["worn"], line["payload"]["charging"]))
        for e, line in statuses[1:]
        if e % 300_000 != slot
    }
    assert changes == ref.expected_changes(
        ref.WEAR_CHANGES, offsets[n], START_ROUTINE_MS, DAY_MS
    )


@pytest.mark.parametrize("n", range(1, 21))
def test_worn_false_and_charging_true_exactly_during_charging(by_device, offsets, n):
    for e, line in of_type(by_device[f"wearable-{n:04d}"], "status"):
        charging = ref.phase(START_ROUTINE_MS + e, offsets[n]) == "charging"
        assert line["payload"]["worn"] is (not charging)
        assert line["payload"]["charging"] is charging


@pytest.mark.parametrize("n", range(1, 21))
def test_exactly_the_expected_bed_transitions(by_device, offsets, n):
    beds = of_type(by_device[f"bedroom-{n:04d}"], "bed")[1:]  # after start-up
    got = {(e, line["payload"]["occupied"]) for e, line in beds}
    assert len(got) == len(beds)
    assert got == ref.expected_changes(
        ref.BED_CHANGES, offsets[n], START_ROUTINE_MS, DAY_MS
    )


@pytest.mark.parametrize("n", range(1, 21))
def test_exactly_the_expected_motion_transitions(by_device, offsets, n):
    motions = of_type(by_device[f"bedroom-{n:04d}"], "motion")[1:]
    got = {(e, line["payload"]["motion"]) for e, line in motions}
    assert len(got) == len(motions)
    assert got == ref.expected_changes(
        ref.MOTION_CHANGES, offsets[n], START_ROUTINE_MS, DAY_MS
    )


def test_bed_and_motion_alternate_never_repeating_a_state(by_device):
    for device_id in BEDROOMS:
        for kind, field in (("bed", "occupied"), ("motion", "motion")):
            values = [
                line["payload"][field]
                for _, line in of_type(by_device[device_id], kind)
            ]
            assert all(a != b for a, b in itertools.pairwise(values)), device_id


def test_offsets_are_within_30_minutes_and_differ_between_residents(offsets):
    assert all(-30 * MINUTE_MS <= o <= 30 * MINUTE_MS for o in offsets.values())
    assert all(o % 1000 == 0 for o in offsets.values())
    assert len(set(offsets.values())) == 20


def test_recovered_offsets_match_the_seeded_draw(offsets, residents_by_number):
    import engine
    import routine

    drawn = routine.draw_offsets(
        [r["resident_id"] for r in residents_by_number.values()],
        engine.offsets_rng(7),
    )
    for n, resident in residents_by_number.items():
        assert drawn[resident["resident_id"]] == offsets[n]


@pytest.mark.parametrize("device_id", BEDROOMS)
def test_bedroom_status_battery_null_and_firmware(by_device, device_id):
    for _, line in of_type(by_device[device_id], "status"):
        assert line["payload"]["battery_pct"] is None
        assert line["payload"]["fw_version"] == "1.0.0"


# ---- values (criterion 10) ----


def limits(resident: dict) -> dict:
    t = resident["thresholds"]
    return {
        "hr": (t["hr_bpm"]["min"] + 8, t["hr_bpm"]["max"] - 8),
        "spo2": (t["spo2_pct"]["min"] + 3, 100),
        "temp": (
            round(t["skin_temp_c"]["min"] + 1.0, 1),
            round(t["skin_temp_c"]["max"] - 1.0, 1),
        ),
    }


def clamp(value, low, high):
    return max(low, min(high, value))


HR_DELTA = {"asleep": -8, "bedroom": 0, "out": 10}
ACTIVITY = {"asleep": (0, 5), "bedroom": (5, 30), "out": (20, 70)}


@pytest.mark.parametrize("n", range(1, 21))
def test_vitals_stay_inside_thresholds_narrowed_by_the_margins(
    by_device, residents_by_number, n
):
    lim = limits(residents_by_number[n])
    for _, line in of_type(by_device[f"wearable-{n:04d}"], "vitals"):
        p = line["payload"]
        if p["hr_bpm"] is not None:
            assert lim["hr"][0] <= p["hr_bpm"] <= lim["hr"][1]
        if p["spo2_pct"] is not None:
            assert lim["spo2"][0] <= p["spo2_pct"] <= lim["spo2"][1]
        if p["skin_temp_c"] is not None:
            assert lim["temp"][0] <= p["skin_temp_c"] <= lim["temp"][1]


@pytest.mark.parametrize("n", range(1, 21))
def test_vitals_follow_the_baseline_and_phase_model(
    by_device, residents_by_number, offsets, n
):
    resident = residents_by_number[n]
    base = resident["baseline"]
    lim = limits(resident)
    for e, line in of_type(by_device[f"wearable-{n:04d}"], "vitals"):
        p = line["payload"]
        phase = ref.phase(START_ROUTINE_MS + e, offsets[n])
        centre = base["hr_bpm"] + HR_DELTA[phase]
        if p["hr_bpm"] is not None:
            low, high = (clamp(centre + d, *lim["hr"]) for d in (-4, 4))
            assert low <= p["hr_bpm"] <= high, (phase, p["hr_bpm"])
        if p["spo2_pct"] is not None:
            low, high = (clamp(base["spo2_pct"] + d, *lim["spo2"]) for d in (-1, 1))
            assert low <= p["spo2_pct"] <= min(high, 100)
        if p["skin_temp_c"] is not None:
            centre_t = base["skin_temp_c"] + (0.3 if phase == "asleep" else 0)
            low, high = (
                clamp(round(centre_t + d, 1), *lim["temp"]) for d in (-0.4, 0.4)
            )
            assert low - 1e-9 <= p["skin_temp_c"] <= high + 1e-9
        a_low, a_high = ACTIVITY[phase]
        assert a_low <= p["activity_pct"] <= a_high, phase
        accel = p["accel_g"]
        magnitude = (accel["x"] ** 2 + accel["y"] ** 2 + accel["z"] ** 2) ** 0.5
        assert 0.9 <= magnitude <= 1.1


def test_hr_noise_uses_the_whole_plus_minus_4_range(by_device, offsets):
    # res-0001 (baseline 72) is never clamped, so every offset -4..4 should appear.
    deltas = set()
    for e, line in of_type(by_device["wearable-0001"], "vitals"):
        hr = line["payload"]["hr_bpm"]
        if hr is not None:
            phase = ref.phase(START_ROUTINE_MS + e, offsets[1])
            deltas.add(hr - 72 - HR_DELTA[phase])
    assert deltas == set(range(-4, 5))


def test_res_0011_heart_rate_never_below_48(by_device):
    hrs = [
        line["payload"]["hr_bpm"]
        for _, line in of_type(by_device["wearable-0011"], "vitals")
        if line["payload"]["hr_bpm"] is not None
    ]
    assert min(hrs) == 48


def test_no_reading_reaches_the_planned_vitals_alert_limits(day_run):
    for line in day_run.lines:
        if message_of(line) != "vitals":
            continue
        p = line["payload"]
        assert p["hr_bpm"] is None or 45 <= p["hr_bpm"] <= 130
        assert p["spo2_pct"] is None or p["spo2_pct"] >= 88


def null_rate(entries, field, phases, offsets_n):
    rows = [
        line["payload"][field]
        for e, line in entries
        if ref.phase(START_ROUTINE_MS + e, offsets_n) in phases
    ]
    return sum(v is None for v in rows) / len(rows)


def all_vitals(by_device):
    for n in range(1, 21):
        yield n, of_type(by_device[f"wearable-{n:04d}"], "vitals")


@pytest.mark.parametrize(
    ("field", "phases", "rate", "tolerance"),
    [
        ("hr_bpm", {"asleep", "bedroom", "out"}, 0.02, 0.006),
        ("skin_temp_c", {"asleep", "bedroom", "out"}, 0.02, 0.006),
        ("spo2_pct", {"asleep", "bedroom"}, 0.02, 0.008),
        ("spo2_pct", {"out"}, 0.05, 0.01),
    ],
)
def test_null_rates_within_tolerance(
    by_device, offsets, field, phases, rate, tolerance
):
    nulls = total = 0
    for n, entries in all_vitals(by_device):
        for e, line in entries:
            if ref.phase(START_ROUTINE_MS + e, offsets[n]) in phases:
                total += 1
                nulls += line["payload"][field] is None
    assert abs(nulls / total - rate) <= tolerance, nulls / total


def test_activity_and_accel_are_never_null(by_device):
    for _, entries in all_vitals(by_device):
        for _, line in entries:
            assert line["payload"]["activity_pct"] is not None
            assert line["payload"]["accel_g"] is not None


# ---- battery (criterion 11) ----


@pytest.mark.parametrize("device_id", WEARABLES)
def test_battery_follows_drain_and_charge_rates(by_device, device_id):
    statuses = of_type(by_device[device_id], "status")
    first = statuses[0][1]["payload"]["battery_pct"]
    assert 60 <= first <= 95
    level = float(first)
    previous_e, charging = statuses[0][0], statuses[0][1]["payload"]["charging"]
    for e, line in statuses[1:]:
        rate = 40.0 if charging else -1.5
        level = clamp(level + rate * (e - previous_e) / HOUR_MS, 0.0, 100.0)
        published = line["payload"]["battery_pct"]
        assert abs(published - level) <= 1.0, (e, published, level)
        previous_e, charging = e, line["payload"]["charging"]


@pytest.mark.parametrize("device_id", WEARABLES)
def test_battery_never_rises_off_the_charger(by_device, device_id):
    statuses = of_type(by_device[device_id], "status")
    for (_, a), (_, b) in itertools.pairwise(statuses):
        if not a["payload"]["charging"] and not b["payload"]["charging"]:
            assert b["payload"]["battery_pct"] <= a["payload"]["battery_pct"]
        if b["payload"]["charging"] and a["payload"]["charging"]:
            assert b["payload"]["battery_pct"] >= a["payload"]["battery_pct"]


def test_wearable_firmware_version(by_device):
    for device_id in WEARABLES:
        for _, line in of_type(by_device[device_id], "status"):
            assert line["payload"]["fw_version"] == "1.0.0"
