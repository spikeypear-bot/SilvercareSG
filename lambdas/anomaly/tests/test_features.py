"""Pure feature logic (nightly-anomaly.md criteria 1-4, 11)."""

from __future__ import annotations

import json

import features as f
import pytest
from events import DATE, night, summary


def nights_for(resident_id: str, count: int, **overrides) -> dict:
    """`count` usable baseline nights (D-8 backwards) plus night D."""
    out = {}
    for d in f.baseline_dates(DATE)[::-1][:count]:
        out[(resident_id, d)] = night(resident_id=resident_id, date=d, **overrides)
    out[(resident_id, DATE)] = night(resident_id=resident_id, date=DATE)
    return out


def test_night_with_720_readings_is_usable():
    assert f.is_usable(night(vitals_readings=720))


def test_night_with_719_readings_is_low_wear():
    assert not f.is_usable(night(vitals_readings=719))


def test_night_with_a_null_feature_is_not_usable():
    assert not f.is_usable(night(bed_minutes=None))


def test_baseline_is_nights_37_to_8_days_before():
    dates = f.baseline_dates(DATE)
    assert len(dates) == 30
    assert dates[0] == "2026-10-12" and dates[-1] == "2026-11-10"


def test_steady_resident_uses_the_sd_floor():
    stats = f.baseline([night(), night(), night()])
    assert stats["hr_bpm_avg"][1] == f.SD_FLOOR["hr_bpm_avg"]
    assert stats["bed_minutes"][1] == f.SD_FLOOR["bed_minutes"]


def test_zscore_is_distance_in_floored_sds():
    stats = f.baseline([night(hr_bpm_avg=60.0)])
    z = f.zscores(night(hr_bpm_avg=70.0), stats)
    assert z[f.FEATURES.index("hr_bpm_avg")] == 5.0  # (70 - 60) / floor 2


def test_reasons_are_the_two_furthest_features():
    stats = f.baseline([night()])
    n = night(hr_bpm_avg=80.0, bed_minutes=200)
    z = f.zscores(n, stats)
    got = f.reasons(n, stats, z)
    assert [r["feature"] for r in got] == ["bed_minutes", "hr_bpm_avg"]
    assert got[1] == {"feature": "hr_bpm_avg", "value": 80.0, "usual": 60.0}


def test_reason_ties_follow_feature_order():
    stats = f.baseline([night()])
    z = [1.0] * len(f.FEATURES)
    assert [r["feature"] for r in f.reasons(night(), stats, z)] == list(f.FEATURES[:2])


def test_resident_with_19_baseline_nights_has_not_enough_history():
    a = f.assess("res-0001", DATE, nights_for("res-0001", 19))
    assert a["status"] == "not_enough_history" and a["usable_nights"] == 19


def test_resident_with_20_baseline_nights_is_scoreable():
    a = f.assess("res-0001", DATE, nights_for("res-0001", 20))
    assert a["status"] == "scoreable" and len(a["z"]) == 9 and len(a["reasons"]) == 2


def test_low_wear_baseline_nights_dont_count():
    a = f.assess("res-0001", DATE, nights_for("res-0001", 30, vitals_readings=100))
    assert a["status"] == "not_enough_history" and a["usable_nights"] == 0


def test_low_wear_night_is_not_scored():
    nights = nights_for("res-0001", 30)
    nights[("res-0001", DATE)] = night(vitals_readings=10)
    assert f.assess("res-0001", DATE, nights)["status"] == "low_wear"


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 2},
        {"resident_id": "Test Resident A"},
        {"date": "2026-11-17"},
        {"hr_bpm_avg": "60"},
        {"hr_bpm_avg": True},
        {"vitals_readings": -1},
    ],
)
def test_invalid_summary_is_rejected(overrides):
    body = json.dumps(summary(**overrides)).encode()
    with pytest.raises(f.InvalidSummary):
        f.parse_summary(body, DATE)


def test_summary_missing_a_feature_is_rejected():
    s = summary()
    del s["motion_events"]
    with pytest.raises(f.InvalidSummary):
        f.parse_summary(json.dumps(s).encode(), DATE)


def test_non_finite_number_is_rejected():
    body = json.dumps(summary()).replace('"hr_bpm_avg": 60.0', '"hr_bpm_avg": NaN')
    with pytest.raises(f.InvalidSummary):
        f.parse_summary(body.encode(), DATE)


def test_cutoff_is_mean_plus_three_sd():
    got = f.cutoff([1.0, 2.0, 3.0])
    assert got == {"mean": 2.0, "sd": 0.8165, "cutoff": 4.4495, "rows": 3}


def test_scores_are_read_from_jsonlines():
    assert f.parse_scores(b'{"score": 1.5}\n{"score": 2}\n') == [1.5, 2.0]


@pytest.mark.parametrize("line", [b'{"score": "x"}', b"[1]", b'{"score": NaN}'])
def test_bad_score_line_is_rejected(line):
    with pytest.raises(ValueError):
        f.parse_scores(line)
