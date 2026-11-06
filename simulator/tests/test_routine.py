"""The routine table, per-resident offsets, --start-at, and the charging window
(criteria 7, 8, 10, 11, 12, 13, 15)."""

from __future__ import annotations

import random
from collections import Counter

import engine as engine_mod
import pytest
import residents as residents_mod
import routine
import routine_ref as ref
from conftest import DAY_MS, HOUR_MS, MINUTE_MS, START_EPOCH_MS, message_of, run_dry

H = HOUR_MS
M = MINUTE_MS


# ---- routine table ----


@pytest.mark.parametrize(
    ("time_ms", "phase"),
    [
        (0, "asleep"),
        (7 * H - 1, "asleep"),
        (7 * H, "bedroom"),
        (8 * H - 1, "bedroom"),
        (8 * H, "out"),
        (19 * H - 1, "out"),
        (19 * H, "charging"),
        (20 * H - 1, "charging"),
        (20 * H, "out"),
        (21 * H - 1, "out"),
        (21 * H, "bedroom"),
        (22 * H - 1, "bedroom"),
        (22 * H, "asleep"),
        (DAY_MS - 1, "asleep"),
        (DAY_MS, "asleep"),
    ],
)
def test_phase_at_each_boundary(time_ms, phase):
    assert routine.phase_at(time_ms, 0) == phase


@pytest.mark.parametrize("offset_min", [-30, -17, -1, 0, 1, 29, 30])
@pytest.mark.parametrize("boundary_h", [7, 8, 19, 20, 21, 22])
def test_offset_shifts_every_boundary(offset_min, boundary_h):
    offset = offset_min * M
    at = boundary_h * H + offset
    assert routine.phase_at(at - 1, offset) == ref.phase(boundary_h * H - 1, 0)
    assert routine.phase_at(at, offset) == ref.phase(boundary_h * H, 0)


def test_negative_offset_wakes_up_before_7():
    assert routine.phase_at(6 * H + 30 * M, -30 * M) == "bedroom"
    assert routine.phase_at(6 * H + 30 * M - 1, -30 * M) == "asleep"


def test_positive_offset_goes_to_bed_after_22():
    assert routine.phase_at(22 * H + 29 * M, 30 * M) == "bedroom"
    assert routine.phase_at(22 * H + 30 * M, 30 * M) == "asleep"


@pytest.mark.parametrize("end_h", [8, 22])
def test_motion_clears_2_minutes_after_bedroom_ends(end_h):
    offset = 12 * M
    end = end_h * H + offset
    assert routine.motion_on(end + 2 * M - 1, offset) is True
    assert routine.motion_on(end + 2 * M, offset) is False


@pytest.mark.parametrize("start_h", [7, 21])
def test_motion_on_when_bedroom_starts(start_h):
    assert routine.motion_on(start_h * H - 1, 0) is False
    assert routine.motion_on(start_h * H, 0) is True


@pytest.mark.parametrize("seed", range(50))
def test_offsets_are_whole_seconds_within_30_minutes(seed):
    ids = [f"res-{n:04d}" for n in range(1, 21)]
    offsets = routine.draw_offsets(ids, engine_mod.offsets_rng(seed))
    assert sorted(offsets) == ids
    for value in offsets.values():
        assert -30 * M <= value <= 30 * M
        assert value % 1000 == 0


@pytest.mark.parametrize("seed", range(50))
def test_no_two_residents_change_state_in_the_same_second(seed):
    ids = [f"res-{n:04d}" for n in range(1, 21)]
    offsets = routine.draw_offsets(ids, engine_mod.offsets_rng(seed))
    points = Counter()
    for offset in offsets.values():
        for tod in (7, 8, 19, 20, 21, 22):
            points[(tod * H + offset) // 1000 % 86_400] += 1
        for tod in (8 * H + 2 * M, 22 * H + 2 * M):
            points[(tod + offset) // 1000 % 86_400] += 1
    assert max(points.values()) == 1


def test_offsets_spread_over_the_whole_range():
    ids = [f"res-{n:04d}" for n in range(1, 21)]
    values = []
    for seed in range(100):
        values.extend(routine.draw_offsets(ids, engine_mod.offsets_rng(seed)).values())
    assert min(values) < -27 * M and max(values) > 27 * M
    assert abs(sum(values) / len(values)) < 2 * M
    # Roughly uniform: each 10-minute bucket holds about 1/6 of the draws.
    buckets = Counter((v + 30 * M) // (10 * M) for v in values if v < 30 * M)
    for count in buckets.values():
        assert abs(count / len(values) - 1 / 6) < 0.03


def test_same_seed_gives_the_same_offsets_and_another_seed_others():
    ids = [f"res-{n:04d}" for n in range(1, 21)]
    a = routine.draw_offsets(ids, engine_mod.offsets_rng(5))
    b = routine.draw_offsets(ids, engine_mod.offsets_rng(5))
    c = routine.draw_offsets(ids, engine_mod.offsets_rng(6))
    assert a == b
    assert a != c


@pytest.mark.parametrize("text", ["00:00", "07:00", "23:59", "12:34"])
def test_parse_hhmm_accepts_valid_times(text):
    hours, minutes = map(int, text.split(":"))
    assert routine.parse_hhmm(text) == hours * H + minutes * M


@pytest.mark.parametrize("text", ["24:00", "7:00", "12:60", "1200", "12:00:00", ""])
def test_parse_hhmm_rejects_invalid_times(text):
    with pytest.raises(ValueError):
        routine.parse_hhmm(text)


# ---- engine with a fixed offset (edge cases at the start) ----


@pytest.fixture
def one_resident(seed_doc, devices_doc, monkeypatch):
    """An engine for res-0001's two devices with offset 0."""
    residents, order = residents_mod.build_residents(seed_doc, devices_doc)

    def build(start_routine_ms: int, offset_ms: int = 0) -> engine_mod.Engine:
        monkeypatch.setattr(
            routine,
            "draw_offsets",
            lambda ids, rng: {rid: offset_ms for rid in ids},
        )
        return engine_mod.Engine(
            residents,
            order,
            ["wearable-0001", "bedroom-0001"],
            seed=1,
            start_epoch_ms=START_EPOCH_MS,
            start_routine_ms=start_routine_ms,
        )

    return build


def messages(eng, until_ms):
    return [
        (m.elapsed_ms, m.device_id, m.message, m.payload) for m in eng.pop_due(until_ms)
    ]


def test_start_exactly_at_bedtime_starts_in_bed_and_sends_no_extra_bed(one_resident):
    eng = one_resident(22 * H)
    out = messages(eng, 2 * H)
    beds = [(e, p["occupied"]) for e, d, kind, p in out if kind == "bed"]
    assert beds == [(100, True)]  # the start-up state only (bedroom is device 2)


def test_start_inside_motion_hold_clears_motion_on_time(one_resident):
    eng = one_resident(22 * H + 1 * M)
    out = messages(eng, 10 * M)
    motions = [(e, p["motion"]) for e, d, kind, p in out if kind == "motion"]
    assert motions == [(100, True), (1 * M, False)]


def test_start_exactly_at_charging_start(one_resident):
    eng = one_resident(19 * H)
    out = messages(eng, 2 * H)
    statuses = [
        (e, p["worn"], p["charging"])
        for e, d, k, p in out
        if d == "wearable-0001" and k == "status"
    ]
    assert statuses[0] == (0, False, True)
    assert (H, True, False) in statuses
    vitals = [e for e, d, k, p in out if k == "vitals"]
    assert vitals and min(vitals) >= H


def test_battery_charges_to_at_most_100(one_resident):
    eng = one_resident(19 * H)
    out = messages(eng, 2 * H)
    levels = [
        (e, p["battery_pct"])
        for e, d, k, p in out
        if d == "wearable-0001" and k == "status"
    ]
    start = levels[0][1]
    for e, level in levels:
        if e <= H:
            expected = min(100, start + 40 * e / H)
            assert abs(level - expected) <= 1
    assert max(level for _, level in levels) == 100


def test_battery_model_units():
    battery = engine_mod.Battery(pct=80.0, charging=False)
    battery.advance(2 * H)
    assert battery.published() == 77
    battery.charging = True
    battery.advance(2 * H + 30 * M)
    assert battery.published() == 97
    battery.advance(4 * H)
    assert battery.published() == 100


def test_battery_published_is_an_integer():
    battery = engine_mod.Battery(pct=63.7, charging=False)
    assert isinstance(battery.published(), int)


# ---- --start-at through the CLI ----


def dry(argv):
    result = run_dry(["--dry-run", "--instant", "--random-seed", "11", *argv])
    assert result.code == 0, result.stderr
    return result


def test_start_at_sets_the_routine_clock_and_wraps_past_midnight():
    result = dry(["--duration", "2h", "--start-at", "23:30"])
    lines = result.stderr.splitlines()
    assert lines[0].endswith("routine 23:30")
    routines = [line.split(" routine ")[1][:5] for line in lines[1:-1]]
    assert routines[0] == "23:31"
    assert routines[29] == "00:00"
    assert routines[-1] == "01:30"


def test_routine_past_midnight_is_asleep_for_everyone():
    result = dry(["--duration", "2h", "--start-at", "23:30"])
    beds = [line for line in result.lines if message_of(line) == "bed"]
    motions = [line for line in result.lines if message_of(line) == "motion"]
    assert len(beds) == 20 and all(b["payload"]["occupied"] for b in beds)
    assert len(motions) == 20 and not any(m["payload"]["motion"] for m in motions)
    assert all(
        line["payload"]["worn"]
        for line in result.lines
        if line["topic"].startswith("silvercare/v1/wearable-")
        and message_of(line) == "status"
    )


def test_start_at_does_not_change_ts():
    a = dry(["--duration", "10m", "--start-at", "03:00"])
    b = dry(["--duration", "10m", "--start-at", "15:00"])
    ts_a = [line["payload"]["ts"] for line in a.lines]
    ts_b = [line["payload"]["ts"] for line in b.lines]
    assert ts_a[0] == ts_b[0] == "2026-11-03T04:00:00.250Z"


def test_default_routine_is_the_real_singapore_time():
    # 04:00:00.250 UTC is 12:00 SGT.
    result = dry(["--duration", "2m"])
    assert result.stderr.splitlines()[0].endswith("routine 12:00")
    assert " SGT routine 12:01 " in result.stderr.splitlines()[1]


def test_default_routine_crosses_the_utc_date_line():
    # 2026-11-03T17:30Z is 01:30 SGT on 4 November.
    epoch = START_EPOCH_MS - 250 + (13 * H + 30 * M)
    result = run_dry(
        ["--dry-run", "--instant", "--duration", "1m", "--random-seed", "1"],
        epoch_ms=lambda: epoch,
    )
    assert result.stderr.splitlines()[0].endswith("routine 01:30")
    assert result.lines[0]["payload"]["ts"] == "2026-11-03T17:30:00.000Z"


@pytest.mark.parametrize("start_at", ["06:40", "07:20"])
def test_start_up_state_follows_each_residents_offset(start_at):
    result = dry(["--duration", "1m", "--start-at", start_at])
    offsets = routine.draw_offsets(
        [f"res-{n:04d}" for n in range(1, 21)], engine_mod.offsets_rng(11)
    )
    start = ref.hhmm_ms(start_at)
    beds = {
        line["topic"].split("/")[2]: line["payload"]["occupied"]
        for line in result.lines
        if message_of(line) == "bed"
    }
    motions = {
        line["topic"].split("/")[2]: line["payload"]["motion"]
        for line in result.lines
        if message_of(line) == "motion"
    }
    for n in range(1, 21):
        phase = ref.phase(start, offsets[f"res-{n:04d}"])
        assert beds[f"bedroom-{n:04d}"] is (phase == "asleep")
        assert motions[f"bedroom-{n:04d}"] is (phase == "bedroom")
    assert len(set(beds.values())) == 2  # some up already, some still asleep


def test_charging_window_from_18_to_21():
    result = dry(["--duration", "3h", "--start-at", "18:00"])
    offsets = routine.draw_offsets(
        [f"res-{n:04d}" for n in range(1, 21)], engine_mod.offsets_rng(11)
    )
    start = 18 * H
    for n in range(1, 21):
        device = f"wearable-{n:04d}"
        offset = offsets[f"res-{n:04d}"]
        lines = [line for line in result.lines if line["topic"].split("/")[2] == device]
        elapsed = [result.elapsed_ms(line) for line in lines]
        begin = ref.elapsed_of(19 * H, offset, start)
        end = ref.elapsed_of(20 * H, offset, start)
        vitals = [e for e, line in zip(elapsed, lines) if message_of(line) == "vitals"]
        assert not [e for e in vitals if begin <= e < end]
        assert len(vitals) in (179 - 60, 180 - 60)
        statuses = [
            (e, line["payload"]["worn"], line["payload"]["charging"])
            for e, line in zip(elapsed, lines)
            if message_of(line) == "status"
        ]
        assert (begin, False, True) in statuses
        assert (end, True, False) in statuses
        for e, worn, charging in statuses:
            assert charging is (begin <= e < end)
            assert worn is not charging
        levels = [
            (e, line["payload"]["battery_pct"])
            for e, line in zip(elapsed, lines)
            if message_of(line) == "status"
        ]
        during = [level for e, level in levels if begin <= e <= end]
        assert during[-1] >= min(100, during[0] + 39)


# ---- spreading and determinism ----


@pytest.mark.parametrize(
    ("seed", "start_at", "epoch_shift_ms"),
    [
        (1, "00:00", 0),
        (2, "06:45", 999),
        (3, "18:59", 1),
        (42, "21:30", 500),
        (123456, "07:30", 250),
        (987654321, "12:00", 750),
    ],
)
def test_no_second_after_the_first_minute_has_more_than_4_publishes(
    seed_doc, devices_doc, seed, start_at, epoch_shift_ms
):
    residents, order = residents_mod.build_residents(seed_doc, devices_doc)
    start_epoch = START_EPOCH_MS - 250 + epoch_shift_ms
    eng = engine_mod.Engine(
        residents,
        order,
        order,
        seed=seed,
        start_epoch_ms=start_epoch,
        start_routine_ms=ref.hhmm_ms(start_at),
        new_id=lambda: "id",
    )
    per_second = Counter(
        (start_epoch + m.elapsed_ms) // 1000
        for m in eng.pop_due(DAY_MS - 1)
        if m.elapsed_ms >= M
    )
    assert max(per_second.values()) <= 4


def test_default_start_time_with_odd_milliseconds_still_spreads(seed_doc, devices_doc):
    # Without --start-at the routine clock starts at the real time, mid-second.
    residents, order = residents_mod.build_residents(seed_doc, devices_doc)
    start_routine = 21 * H + 59 * M + 59_999
    eng = engine_mod.Engine(
        residents,
        order,
        order,
        seed=77,
        start_epoch_ms=START_EPOCH_MS,
        start_routine_ms=start_routine,
        new_id=lambda: "id",
    )
    per_second = Counter(
        (START_EPOCH_MS + m.elapsed_ms) // 1000
        for m in eng.pop_due(DAY_MS - 1)
        if m.elapsed_ms >= M
    )
    assert max(per_second.values()) <= 4


def counter_ids():
    count = iter(range(10**9))
    return lambda: f"00000000-0000-4000-8000-{next(count):012d}"


def test_same_random_seed_and_start_at_give_identical_output():
    argv = ["--duration", "3h", "--start-at", "06:30"]
    a = run_dry(
        ["--dry-run", "--instant", "--random-seed", "9", *argv], new_id=counter_ids()
    )
    b = run_dry(
        ["--dry-run", "--instant", "--random-seed", "9", *argv], new_id=counter_ids()
    )
    assert a.stdout == b.stdout
    assert a.stderr == b.stderr


def test_same_seed_with_default_ids_differs_only_in_message_id():
    argv = ["--dry-run", "--instant", "--random-seed", "9", "--duration", "30m"]
    a = run_dry(argv)
    b = run_dry(argv)

    def strip(lines):
        return [{**line["payload"], "message_id": None} for line in lines]

    assert strip(a.lines) == strip(b.lines)
    assert a.lines[0]["payload"]["message_id"] != b.lines[0]["payload"]["message_id"]


def test_another_random_seed_gives_other_values():
    argv = ["--dry-run", "--instant", "--duration", "30m", "--start-at", "10:00"]
    a = run_dry([*argv, "--random-seed", "1"], new_id=counter_ids())
    b = run_dry([*argv, "--random-seed", "2"], new_id=counter_ids())
    assert a.stdout != b.stdout


def test_device_values_do_not_depend_on_the_other_selected_devices():
    argv = [
        "--dry-run",
        "--instant",
        "--duration",
        "2h",
        "--start-at",
        "02:00",
        "--random-seed",
        "4",
    ]
    full = run_dry(argv)
    one = run_dry([*argv, "--devices", "wearable-0003"])

    def values(result):
        return [
            {k: v for k, v in line["payload"].items() if k not in ("message_id", "ts")}
            for line in result.lines
            if line["topic"] == "silvercare/v1/wearable-0003/vitals"
        ]

    # Alone, wearable-0003's vitals second is 0, its start-up time, so its first
    # reading moves to 60 s: compare the readings both runs have.
    alone = values(one)
    assert len(alone) >= 119
    assert values(full)[: len(alone)] == alone


def test_random_seed_is_printed_when_not_given():
    result = run_dry(["--dry-run", "--instant", "--duration", "1m"])
    first = result.stderr.splitlines()[0]
    seed = int(first.split("random seed ")[1].split(",")[0])
    again = run_dry(
        ["--dry-run", "--instant", "--duration", "1m", "--random-seed", str(seed)]
    )
    strip = [{**line["payload"], "message_id": None} for line in result.lines]
    strip_again = [{**line["payload"], "message_id": None} for line in again.lines]
    assert strip == strip_again


def test_random_module_state_is_not_used(monkeypatch):
    # Reseeding the global random module must not change a seeded run.
    argv = ["--dry-run", "--instant", "--random-seed", "3", "--duration", "10m"]
    random.seed(1)
    a = run_dry(argv, new_id=counter_ids())
    random.seed(2)
    b = run_dry(argv, new_id=counter_ids())
    assert a.stdout == b.stdout


def test_a_48_h_run_repeats_the_day_without_drift(seed_doc, devices_doc):
    residents, order = residents_mod.build_residents(seed_doc, devices_doc)
    start_routine = ref.hhmm_ms("16:20")
    eng = engine_mod.Engine(
        residents,
        order,
        order,
        seed=31,
        start_epoch_ms=START_EPOCH_MS,
        start_routine_ms=start_routine,
        new_id=lambda: "id",
    )
    out = eng.pop_due(2 * DAY_MS - 1)
    offsets = routine.draw_offsets(
        [r.resident_id for r in residents], engine_mod.offsets_rng(31)
    )
    per_second = Counter((START_EPOCH_MS + m.elapsed_ms) // 1000 for m in out)
    assert (
        max(c for s, c in per_second.items() if s >= START_EPOCH_MS // 1000 + 60) <= 4
    )
    for n in range(1, 21):
        offset = offsets[f"res-{n:04d}"]
        beds = {
            (m.elapsed_ms, m.payload["occupied"])
            for m in out
            if m.device_id == f"bedroom-{n:04d}"
            and m.message == "bed"
            and m.elapsed_ms > 5000
        }
        assert beds == ref.expected_changes(
            ref.BED_CHANGES, offset, start_routine, 2 * DAY_MS
        )
        vitals = [
            m
            for m in out
            if m.device_id == f"wearable-{n:04d}" and m.message == "vitals"
        ]
        assert len(vitals) in (2 * 1380 - 1, 2 * 1380)
        levels = [
            m.payload["battery_pct"]
            for m in out
            if m.device_id == f"wearable-{n:04d}" and m.message == "status"
        ]
        assert 0 < min(levels) and max(levels) <= 100
