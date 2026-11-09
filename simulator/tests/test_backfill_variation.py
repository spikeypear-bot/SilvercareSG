"""Unusual residents in the backfill (docs/features/backfill-variation.md, criterion 12).

Short ranges, few devices, output only under tmp_path, sockets and key files blocked.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import backfill
import pytest
import routine
import variation
from conftest import DEVICES_FILE, SEED_FILE, ts_to_epoch_ms
from engine import Engine
from residents import FIXED_LIMITS, build_residents
from test_backfill import (
    ALL_MESSAGES,
    TODAY,
    guard,
    ingest_validation,
    read_output,
    run,
    sgt_midnight_ms,
)

FROM, TO = "2026-03-10", "2026-03-12"
# Resident 1 is ordinary; 3 and 8 are unusual.
DEVICES = [f"{k}-000{i}" for k in ("wearable", "bedroom") for i in (1, 3, 8)]
UNUSUAL_WEARABLES = {"wearable-0003", "wearable-0008"}
SECOND_MS, MINUTE_MS, HOUR_MS, DAY_MS = 1000, 60_000, 3_600_000, 86_400_000
START_MS = sgt_midnight_ms(date(2026, 3, 8))  # FROM minus the 2-day lead-in


def devices_file(folder: Path, devices=DEVICES) -> Path:
    doc = json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
    doc["devices"] = [d for d in doc["devices"] if d["id"] in devices]
    path = folder / "devices.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def argv(folder: Path, *extra: str) -> list[str]:
    return [
        "--from", FROM, "--to", TO, "--falls", "2", "--vitals-episodes", "20",
        "--devices-file", str(devices_file(folder)), *extra,
    ]  # fmt: skip


def guarded(tmp_path: Path, name: str, *extra: str):
    with pytest.MonkeyPatch.context() as m:
        guard(m)
        result = run(argv(tmp_path, *extra), tmp_path / name)
    return result


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    folder = tmp_path_factory.mktemp("variation")
    result = guarded(folder, "out")
    assert result.code == 0, result.stderr
    return read_output(result)


def residents():
    seed = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    devices = json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
    return build_residents(seed, devices)


# ======== the profile ========


@pytest.mark.parametrize("offset_min", [-30, -7, 0, 13, 30])
def test_profile_without_shifts_is_the_routine(offset_min):
    offset = offset_min * MINUTE_MS
    profile = variation.Profile(0, DAY_MS, frozenset(), {}, {})
    for t in range(DAY_MS, 3 * DAY_MS, 30 * SECOND_MS):
        assert profile.phase(t, offset) == routine.phase_at(t, offset)
        assert profile.next_change_ms(t, offset) - t == routine.next_change_delay_ms(
            t, offset
        )


def test_sleep_shift_moves_bedtime_and_wake():
    # Day 1: wake 45 min late, bedtime 60 min early (the evening out phase vanishes).
    profile = variation.Profile(
        0, 9 * DAY_MS, frozenset(), {1: -60 * MINUTE_MS}, {1: 45 * MINUTE_MS}
    )
    day = DAY_MS
    assert profile.phase(day + 7 * HOUR_MS + 44 * MINUTE_MS, 0) == "asleep"
    assert profile.phase(day + 7 * HOUR_MS + 45 * MINUTE_MS, 0) == "bedroom"
    assert profile.phase(day + 8 * HOUR_MS + 45 * MINUTE_MS, 0) == "out"
    assert profile.phase(day + 20 * HOUR_MS, 0) == "bedroom"
    assert profile.phase(day + 21 * HOUR_MS, 0) == "asleep"
    assert profile.phase(2 * day + 7 * HOUR_MS, 0) == "bedroom"  # day 2 unshifted


def test_decline_runs_from_zero_to_full_over_the_range():
    profile = variation.Profile(DAY_MS, 11 * DAY_MS, frozenset(), {}, {})
    assert profile.vitals_shift(0) == (0, 0)  # lead-in
    assert profile.vitals_shift(DAY_MS) == (0, 0)
    assert profile.vitals_shift(6 * DAY_MS) == (4, -1)  # halfway
    assert profile.vitals_shift(11 * DAY_MS) == (8, -2)


def test_bad_day_adds_15_bpm_and_minus_3_pct_for_that_day_only():
    profile = variation.Profile(DAY_MS, 11 * DAY_MS, frozenset({3}), {}, {})
    base = profile.vitals_shift(3 * DAY_MS - 1)
    assert profile.vitals_shift(3 * DAY_MS)[0] - base[0] in (15, 16)
    assert profile.vitals_shift(4 * DAY_MS - 1)[1] - base[1] in (-3, -4)
    assert profile.vitals_shift(4 * DAY_MS) == (2, -1)  # the next day isn't bad


def test_build_profiles_shift_only_range_days_within_an_hour():
    window = backfill.make_window(date(2026, 3, 10), date(2026, 3, 12), TODAY)
    profiles = backfill.build_profiles(window, 1)
    assert set(profiles) == set(variation.UNUSUAL_RESIDENTS)
    for p in profiles.values():
        assert set(p.bedtime_shift_ms) == set(p.wake_shift_ms) == {2, 3, 4}
        for shift in [*p.bedtime_shift_ms.values(), *p.wake_shift_ms.values()]:
            assert -HOUR_MS <= shift <= HOUR_MS and shift % MINUTE_MS == 0
        assert p.bad_days <= {2, 3, 4}


# ======== readings and alerts in the engine ========


def engine_run(profile, hours=30, device="wearable-0003", resident="res-0003"):
    rs, order = residents()
    engine = Engine(
        rs,
        order,
        [device],
        seed=1,
        start_epoch_ms=START_MS,
        start_routine_ms=0,
        profiles={resident: profile},
    )
    return engine, engine.pop_due(hours * HOUR_MS)


# Full decline from the start, every day bad.
SICK = variation.Profile(-2, -1, frozenset(range(10)), {}, {})


@pytest.fixture
def strong(monkeypatch):
    """A stronger decline than the default, so SICK readings cross the limits."""
    monkeypatch.setattr(variation, "DECLINE_HR", 20)
    monkeypatch.setattr(variation, "DECLINE_SPO2", -5)


def test_unusual_noise_is_wider_and_unclamped(strong):
    calm = variation.Profile(10 * DAY_MS, 11 * DAY_MS, frozenset(), {}, {})
    _, msgs = engine_run(calm)
    hr = [m.payload["hr_bpm"] for m in msgs if m.message == "vitals"]
    hr = [v for v in hr if v is not None]
    # res-0003: baseline 76; deltas -8/0/+10; noise +-12 instead of +-4.
    assert min(hr) >= 76 - 8 - 12 and max(hr) <= 76 + 10 + 12
    assert max(hr) > 76 + 10 + 4 and min(hr) < 76 - 8 - 4
    _, sick = engine_run(SICK)
    spo2 = [m.payload["spo2_pct"] for m in sick if m.message == "vitals"]
    assert min(v for v in spo2 if v is not None) < FIXED_LIMITS.spo2_min  # unclamped


def expected_alerts(msgs, engine, device):
    """An independent model of criterion 6: per metric, an episode starts at a
    reading past the limit, alerts 10 s later and every 5 min while worn, and ends
    at the next reading back inside; a null reading changes nothing."""
    limits = engine.limits(device)
    out = set()

    def close(metric, start, until):
        t = start + 10 * SECOND_MS
        while t < until:
            if engine.phase(device, t) != "charging":
                out.add((metric, t))
            t += 5 * MINUTE_MS

    readings = [m for m in msgs if m.message == "vitals"]
    stop = msgs[-1].elapsed_ms
    for metric in ("hr_bpm", "spo2_pct"):
        start, last = None, None
        for m in readings:
            v = m.payload[metric]
            if v is None:
                continue
            past = (
                v < limits.hr_min or v > limits.hr_max
                if metric == "hr_bpm"
                else v < limits.spo2_min
            )
            if past and start is None:
                start = m.elapsed_ms
            elif not past and start is not None:
                close(metric, start, m.elapsed_ms)
                start = None
            last = m.elapsed_ms
        if start is not None:
            close(metric, start, stop + 1)
        assert last is not None
    return out


def test_readings_past_a_limit_alert_like_a_device(strong):
    engine, msgs = engine_run(SICK)
    actual = {
        (m.payload["metric"], m.elapsed_ms) for m in msgs if m.message == "vitals_alert"
    }
    assert actual  # SICK crosses the limits
    assert actual == expected_alerts(msgs, engine, "wearable-0003")


def test_no_profile_means_no_alert_and_clamped_readings():
    rs, order = residents()
    engine = Engine(rs, order, ["wearable-0003"], seed=1, start_epoch_ms=START_MS,
                    start_routine_ms=0)  # fmt: skip
    msgs = engine.pop_due(30 * HOUR_MS)
    assert not [m for m in msgs if m.message == "vitals_alert"]


# ======== the backfill run ========


def test_ordinary_resident_vitals_match_todays_backfill(tmp_path, out, monkeypatch):
    monkeypatch.setattr(variation, "UNUSUAL_RESIDENTS", ())
    plain = guarded(tmp_path, "plain")
    assert plain.code == 0, plain.stderr

    def values(output, device):
        return [
            {k: v for k, v in line.items() if k not in ("message_id", "received_at")}
            for line in output.by_message("vitals")
            if line["device_id"] == device
        ]

    before = read_output(plain)
    assert values(out, "wearable-0001") == values(before, "wearable-0001")
    assert values(out, "wearable-0003") != values(before, "wearable-0003")


def test_only_unusual_wearables_send_vitals_alert(out):
    alerts = out.by_message("vitals_alert")
    assert alerts
    assert {line["device_id"] for line in alerts} <= UNUSUAL_WEARABLES


def test_every_line_passes_ingest_validation(out):
    for line in out.all_lines:
        result = ingest_validation.validate_event(line, ALL_MESSAGES)
        assert isinstance(result, ingest_validation.ValidMessage), (
            line["message"],
            result.reason,
        )


def episode_lines(output):
    out = []
    for line in output.run.stderr.splitlines():
        name, *rest = line.split(" ")
        if name in ("hr-spike", "spo2-drop"):
            out.append((name, rest[0], f"{rest[1]} {rest[2]}"))
    return out


def test_20_episodes_10_of_each_on_unusual_wearables(out):
    episodes = episode_lines(out)
    assert len(episodes) == 20
    assert sum(name == "hr-spike" for name, _, _ in episodes) == 10
    assert {wearable for _, wearable, _ in episodes} <= UNUSUAL_WEARABLES


def test_default_episodes_ramp_from_about_1_to_about_10_a_month():
    assert backfill.DEFAULT_VITALS_EPISODES == 176
    window = backfill.make_window(date(2026, 3, 1), date(2026, 10, 31), TODAY)
    wearables = [f"wearable-{i:04d}" for i in (3, 8, 14, 19)]
    episodes = backfill.place_episodes(
        176, window, wearables, lambda w, t: "out", [], backfill.rng_for(1, "e")
    )
    per_month = Counter(
        (window.lead_in + timedelta(milliseconds=e.at_ms)).month for e in episodes
    )
    # Expected per resident: March ~1.6, October ~9.4 (x4 residents).
    assert per_month[3] <= 12 and 28 <= per_month[10] <= 48
    assert per_month[3] < per_month[6] < per_month[10]


def test_ramp_fraction_is_an_inverse_cdf():
    assert backfill.ramp_fraction(0) == 0
    assert backfill.ramp_fraction(1) == pytest.approx(1)
    # The density at the end is EPISODE_RAMP times the density at the start.
    lo = backfill.ramp_fraction(1e-7) - backfill.ramp_fraction(0)
    hi = backfill.ramp_fraction(1) - backfill.ramp_fraction(1 - 1e-7)
    assert lo / hi == pytest.approx(backfill.EPISODE_RAMP, rel=0.01)


def test_episodes_show_their_values_in_the_range_and_away_from_falls(out):
    spikes = defaultdict(list)
    for line in out.by_message("vitals"):
        if line["device_id"] in UNUSUAL_WEARABLES:
            t = ts_to_epoch_ms(line["ts"])
            if (line["hr_bpm"] or 0) >= 150:
                spikes[line["device_id"]].append(t)
    assert sum(len(v) for v in spikes.values()) >= 10  # every spike is worn 3 min
    range_start = sgt_midnight_ms(date(2026, 3, 10))
    assert all(t >= range_start for v in spikes.values() for t in v)
    falls = [
        (line["device_id"], ts_to_epoch_ms(line["ts"]))
        for line in out.by_message("fall")
    ]
    for device, fall in falls:
        for t in spikes.get(device, []):
            assert not fall - 13 * MINUTE_MS < t < fall + 20 * MINUTE_MS


def test_summary_names_unusual_residents(out):
    lines = out.run.stderr.splitlines()
    unusual = [line for line in lines if line.startswith("unusual ")]
    assert [line.split(" ")[1] for line in unusual] == ["res-0003", "res-0008"]
    alerts = defaultdict(int)
    for line in out.by_message("vitals_alert"):
        alerts[line["device_id"]] += 1
    for line in unusual:
        wearable = line.split(" ")[2].rstrip(":")
        assert line.endswith(f"{alerts[wearable]} vitals_alert")


def test_same_seed_gives_byte_identical_files(tmp_path, out):
    again = guarded(tmp_path, "again")
    assert again.code == 0
    assert read_output(again).files == out.files


@pytest.mark.parametrize("value", ["401", "-1", "x", "2.5"])
def test_bad_vitals_episodes_exit_2_and_write_nothing(tmp_path, value):
    result = guarded(tmp_path, "o", "--vitals-episodes", value)
    assert result.code == 2
    assert result.stderr.startswith("error: --vitals-episodes")
    assert not (tmp_path / "o").exists()


def test_episodes_that_dont_fit_exit_2_and_write_nothing(tmp_path):
    with pytest.MonkeyPatch.context() as m:
        guard(m)
        result = run(
            ["--from", TO, "--to", TO, "--falls", "0", "--vitals-episodes", "100",
             "--devices-file", str(devices_file(tmp_path, ["wearable-0003"]))],
            tmp_path / "o",
        )  # fmt: skip
    assert result.code == 2
    assert result.stderr.startswith("error: can't place 100 vitals episodes")
    assert not (tmp_path / "o").exists()
