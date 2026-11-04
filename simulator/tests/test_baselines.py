"""The seed file's baselines and the simulator's checks of the seed and device files
(criteria 4, 5, 6)."""

from __future__ import annotations

import importlib.util
import io
import json
import sys

import main
import pytest
import residents as residents_mod
from conftest import REPO_ROOT

ENDPOINT = "example-ats.iot.ap-southeast-1.amazonaws.com"


def resident(doc: dict, rid: str) -> dict:
    return next(r for r in doc["residents"] if r["resident_id"] == rid)


# ---- the committed seed file (criterion 4) ----


def test_every_resident_has_a_baseline_with_exactly_three_fields(seed_doc):
    for r in seed_doc["residents"]:
        assert set(r["baseline"]) == {"hr_bpm", "spo2_pct", "skin_temp_c"}


def test_baseline_types(seed_doc):
    for r in seed_doc["residents"]:
        b = r["baseline"]
        assert type(b["hr_bpm"]) is int
        assert type(b["spo2_pct"]) is int
        assert type(b["skin_temp_c"]) in (int, float)
        assert abs(b["skin_temp_c"] * 10 - round(b["skin_temp_c"] * 10)) < 1e-9


def test_baseline_exceptions(seed_doc):
    assert resident(seed_doc, "res-0004")["baseline"]["spo2_pct"] == 91
    assert resident(seed_doc, "res-0007")["baseline"]["hr_bpm"] == 105
    assert resident(seed_doc, "res-0011")["baseline"]["hr_bpm"] == 50


def test_other_baselines_are_in_the_normal_ranges(seed_doc):
    for r in seed_doc["residents"]:
        b = r["baseline"]
        rid = r["resident_id"]
        if rid not in ("res-0007", "res-0011"):
            assert 62 <= b["hr_bpm"] <= 82, rid
        if rid != "res-0004":
            assert 95 <= b["spo2_pct"] <= 98, rid
        assert 32.8 <= b["skin_temp_c"] <= 34.2, rid


def test_baselines_vary_between_residents(seed_doc):
    baselines = [r["baseline"] for r in seed_doc["residents"]]
    assert len({b["hr_bpm"] for b in baselines}) >= 15
    assert len({b["spo2_pct"] for b in baselines}) >= 4
    assert len({b["skin_temp_c"] for b in baselines}) >= 12
    assert len({json.dumps(b, sort_keys=True) for b in baselines}) == 20


def test_committed_seed_and_devices_files_pass_simulator_validation(
    seed_doc, devices_doc
):
    residents, order = residents_mod.build_residents(seed_doc, devices_doc)
    assert len(residents) == 20
    assert order == [d["id"] for d in devices_doc["devices"]]
    for r in residents:
        n = r.resident_id.split("-")[1]
        assert (r.wearable_id, r.bedroom_id) == (f"wearable-{n}", f"bedroom-{n}")


@pytest.fixture
def seed_module(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "seed_for_simulator_tests", REPO_ROOT / "scripts" / "seed.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "seed_for_simulator_tests", module)
    spec.loader.exec_module(module)
    return module


def test_seed_script_validation_accepts_the_baselines(seed_module):
    data = seed_module.load_seed()
    assert seed_module.validate_seed(data, seed_module.load_devices()) == []


def test_seed_script_does_not_write_baselines_to_the_registry(seed_module):
    data = seed_module.load_seed()
    items = seed_module.build_registry_items(
        data, seed_module.local_subs(data), "2026-11-03T04:00:00Z"
    )
    text = json.dumps(items)
    assert "baseline" not in text


# ---- validation in the simulator (criterion 5) ----


def write(tmp_path, seed_doc, devices_doc):
    seed_path = tmp_path / "seed.json"
    devices_path = tmp_path / "devices.json"
    seed_path.write_text(json.dumps(seed_doc), encoding="utf-8")
    devices_path.write_text(json.dumps(devices_doc), encoding="utf-8")
    return ["--seed-file", str(seed_path), "--devices-file", str(devices_path)]


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    code = main.main(
        argv,
        stdout=out,
        stderr=err,
        client_factory=lambda device_id: pytest.fail("connected"),
    )
    return code, out.getvalue(), err.getvalue()


def dry(tmp_path, seed_doc, devices_doc):
    return run(
        ["--dry-run", "--instant", "--duration", "1m"]
        + write(tmp_path, seed_doc, devices_doc)
    )


def ids_in(text: str) -> set[str]:
    import re

    return set(re.findall(r"(?:res|wearable|bedroom)-\d{4}", text))


def test_missing_baseline_exits_2_naming_the_resident(
    tmp_path, seed_copy, devices_copy
):
    del resident(seed_copy, "res-0003")["baseline"]
    code, out, err = dry(tmp_path, seed_copy, devices_copy)
    assert code == 2
    assert out == ""
    assert "res-0003" in err


def test_missing_baseline_is_checked_before_a_live_run_connects(
    tmp_path, seed_copy, devices_copy
):
    del resident(seed_copy, "res-0003")["baseline"]
    keys = tmp_path / "keys"
    keys.mkdir()
    code, _out, err = run(
        ["--endpoint", ENDPOINT, "--keys-dir", str(keys), "--ca-file", str(keys / "x")]
        + write(tmp_path, seed_copy, devices_copy)
    )
    assert code == 2
    assert "res-0003" in err


@pytest.mark.parametrize(
    ("field", "value", "ok"),
    [
        ("hr_bpm", 52, False),  # min 45 + 7
        ("hr_bpm", 53, True),  # min 45 + 8
        ("hr_bpm", 122, True),  # max 130 - 8
        ("hr_bpm", 123, False),  # max 130 - 7
        ("spo2_pct", 90, False),  # min 88 + 2
        ("spo2_pct", 91, True),  # min 88 + 3
        ("spo2_pct", 100, True),
        ("spo2_pct", 101, False),
        ("skin_temp_c", 30.9, False),  # min 30.0 + 0.9
        ("skin_temp_c", 31.0, True),  # min 30.0 + 1.0
        ("skin_temp_c", 37.0, True),  # max 38.0 - 1.0
        ("skin_temp_c", 37.1, False),  # max 38.0 - 0.9
    ],
)
def test_baseline_margin_boundaries(
    tmp_path, seed_copy, devices_copy, field, value, ok
):
    resident(seed_copy, "res-0005")["baseline"][field] = value
    code, out, err = dry(tmp_path, seed_copy, devices_copy)
    if ok:
        assert code == 0, err
    else:
        assert code == 2
        assert out == ""
        assert ids_in(err) == {"res-0005"}
        assert str(value) not in err.replace("res-0005", "")


@pytest.mark.parametrize(
    "baseline",
    [
        {"hr_bpm": 72, "spo2_pct": 97},
        {"hr_bpm": 72, "spo2_pct": 97, "skin_temp_c": 33.4, "note": "x"},
        {"hr_bpm": 72.0, "spo2_pct": 97, "skin_temp_c": 33.4},
        {"hr_bpm": True, "spo2_pct": 97, "skin_temp_c": 33.4},
        {"hr_bpm": 72, "spo2_pct": "97", "skin_temp_c": 33.4},
        {"hr_bpm": 72, "spo2_pct": 97, "skin_temp_c": 33.45},
        {"hr_bpm": 72, "spo2_pct": 97, "skin_temp_c": None},
        "Synthetic secret text",
        None,
        [72, 97, 33.4],
    ],
)
def test_malformed_baseline_exits_2_naming_only_the_resident(
    tmp_path, seed_copy, devices_copy, baseline
):
    resident(seed_copy, "res-0009")["baseline"] = baseline
    code, _out, err = dry(tmp_path, seed_copy, devices_copy)
    assert code == 2
    assert ids_in(err) == {"res-0009"}
    assert "Synthetic secret text" not in err
    assert "33.45" not in err


def test_every_problem_is_listed_not_only_the_first(tmp_path, seed_copy, devices_copy):
    del resident(seed_copy, "res-0002")["baseline"]
    resident(seed_copy, "res-0017")["baseline"]["hr_bpm"] = 200
    code, _, err = dry(tmp_path, seed_copy, devices_copy)
    assert code == 2
    assert ids_in(err) == {"res-0002", "res-0017"}


def test_baseline_checked_against_the_residents_own_thresholds(
    tmp_path, seed_copy, devices_copy
):
    # res-0011 has hr min 40: 48 is allowed for them, not for others (min 45).
    resident(seed_copy, "res-0011")["baseline"]["hr_bpm"] = 48
    code, _, err = dry(tmp_path, seed_copy, devices_copy)
    assert code == 0, err
    resident(seed_copy, "res-0011")["baseline"]["hr_bpm"] = 47
    code, _, err = dry(tmp_path, seed_copy, devices_copy)
    assert code == 2


def test_device_in_devices_file_but_not_in_seed_exits_2(
    tmp_path, seed_copy, devices_copy
):
    seed_copy["devices"] = [
        d for d in seed_copy["devices"] if d["device_id"] != "bedroom-0013"
    ]
    code, _out, err = dry(tmp_path, seed_copy, devices_copy)
    assert code == 2
    assert ids_in(err) == {"bedroom-0013"}


def test_device_type_differs_between_files_exits_2(tmp_path, seed_copy, devices_copy):
    entry = next(d for d in seed_copy["devices"] if d["device_id"] == "wearable-0006")
    entry["device_type"] = "bedroom"
    code, _, err = dry(tmp_path, seed_copy, devices_copy)
    assert code == 2
    assert "wearable-0006" in err


def test_device_assigned_to_unknown_resident_exits_2(tmp_path, seed_copy, devices_copy):
    entry = next(d for d in seed_copy["devices"] if d["device_id"] == "wearable-0006")
    entry["resident_id"] = "res-0099"
    code, _, err = dry(tmp_path, seed_copy, devices_copy)
    assert code == 2
    assert "wearable-0006" in err


def test_two_wearables_for_one_resident_exits_2(tmp_path, seed_copy, devices_copy):
    entry = next(d for d in seed_copy["devices"] if d["device_id"] == "wearable-0006")
    entry["resident_id"] = "res-0005"
    code, _, err = dry(tmp_path, seed_copy, devices_copy)
    assert code == 2
    assert "res-0005" in err


def test_device_id_with_odd_text_is_not_echoed(tmp_path, seed_copy, devices_copy):
    devices_copy["devices"].append({"id": "Synthetic secret text", "type": "wearable"})
    code, _, err = dry(tmp_path, seed_copy, devices_copy)
    assert code == 2
    assert "Synthetic secret text" not in err


def test_seed_values_never_appear_in_validation_errors(
    tmp_path, seed_copy, devices_copy
):
    r = resident(seed_copy, "res-0001")
    r["baseline"] = {"hr_bpm": 129, "spo2_pct": 87, "skin_temp_c": 39.7}
    code, _, err = dry(tmp_path, seed_copy, devices_copy)
    assert code == 2
    for text in ("129", "87", "39.7", "Test Resident A", "+65"):
        assert text not in err
