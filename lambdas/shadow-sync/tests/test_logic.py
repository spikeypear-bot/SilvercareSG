"""shadow-sync's pure logic without AWS: a wearable's limits (device-alert-limits.md,
"Device limits"), which items are wearables, and when a shadow needs a write. Also
checks the copied modules stay in step with their other copies."""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import device_limits
import handler
import pytest
import thresholds
from boto3.dynamodb.types import TypeSerializer
from events import DEFAULTS, FIXED, RESIDENT_A, limits

LAMBDAS = Path(__file__).resolve().parents[2]


def limits_with(**metrics) -> dict:
    """limits_for for a resident whose THRESHOLDS item holds `metrics`."""
    return handler.limits_for(RESIDENT_A, {RESIDENT_A: metrics})


# ---- computing limits ----


def test_unassigned_wearable_gets_the_fixed_limits():
    assert handler.limits_for(None, {}) == FIXED


def test_assigned_wearable_without_thresholds_item_gets_the_defaults():
    assert handler.limits_for(RESIDENT_A, {}) == DEFAULTS


def test_looser_thresholds_give_the_fixed_limits():
    result = limits_with(hr_bpm={"min": 30, "max": 180}, spo2_pct={"min": 60})
    assert result == FIXED


def test_stricter_thresholds_are_used():
    result = limits_with(hr_bpm={"min": 55, "max": 110}, spo2_pct={"min": 93})
    assert result == limits(55, 110, 93)


@pytest.mark.parametrize(
    ("hr_min", "expected"), [(44, 45), (45, 45), (46, 46), (20, 45), (100, 100)]
)
def test_hr_min_is_the_higher_of_threshold_and_45(hr_min, expected):
    result = limits_with(hr_bpm={"min": hr_min, "max": 120})
    assert result["hr_bpm"]["min"] == expected


@pytest.mark.parametrize(
    ("hr_max", "expected"), [(129, 129), (130, 130), (131, 130), (250, 130), (60, 60)]
)
def test_hr_max_is_the_lower_of_threshold_and_130(hr_max, expected):
    result = limits_with(hr_bpm={"min": 50, "max": hr_max})
    assert result["hr_bpm"]["max"] == expected


@pytest.mark.parametrize(
    ("spo2", "expected"), [(87, 88), (88, 88), (89, 89), (50, 88), (100, 100)]
)
def test_spo2_min_is_the_higher_of_threshold_and_88(spo2, expected):
    assert limits_with(spo2_pct={"min": spo2})["spo2_pct"]["min"] == expected


@pytest.mark.parametrize(
    "value", ["110", Decimal("110.5"), Decimal(300), Decimal(10), True, None, [1]]
)
def test_invalid_hr_max_uses_the_default(value):
    result = limits_with(hr_bpm={"min": 55, "max": value}, spo2_pct={"min": 93})
    assert result == limits(55, 120, 93)


@pytest.mark.parametrize("value", ["93", Decimal("93.5"), Decimal(49), False])
def test_invalid_spo2_uses_the_default(value):
    assert limits_with(spo2_pct={"min": value})["spo2_pct"]["min"] == 92


def test_metric_that_is_not_a_map_uses_its_defaults():
    result = limits_with(hr_bpm="55-110", spo2_pct=[93])
    assert result == DEFAULTS


def test_thresholds_with_min_not_below_max_use_the_defaults():
    result = limits_with(hr_bpm={"min": 100, "max": 90}, spo2_pct={"min": 95})
    assert result == limits(50, 120, 95)


@pytest.mark.parametrize(("hr_min", "hr_max"), [(135, 140), (130, 131), (200, 250)])
def test_hr_min_not_below_max_after_clamping_gives_fixed_hr_and_keeps_spo2(
    hr_min, hr_max
):
    result = limits_with(hr_bpm={"min": hr_min, "max": hr_max}, spo2_pct={"min": 95})
    assert result == limits(45, 130, 95)


def test_hr_129_to_130_stays_as_computed():
    result = limits_with(hr_bpm={"min": 129, "max": 135})
    assert result["hr_bpm"] == {"min": 129, "max": 130}


def test_skin_temperature_is_never_a_device_limit():
    result = limits_with(skin_temp_c={"min": Decimal("31.0"), "max": Decimal("37.0")})
    assert set(result) == {"hr_bpm", "spo2_pct"}


@pytest.mark.parametrize(
    "item",
    [
        None,
        {},
        {"hr_bpm": {"min": 20, "max": 250}, "spo2_pct": {"min": 50}},
        {"hr_bpm": {"min": 249, "max": 250}, "spo2_pct": {"min": 100}},
        {"hr_bpm": {"min": 20, "max": 21}},
        {"hr_bpm": {"min": 129, "max": 131}},
    ],
)
def test_computed_limits_are_always_valid_integers(item):
    result = handler.limits_for(RESIDENT_A, {RESIDENT_A: item} if item else {})
    assert device_limits.is_valid(result)


# ---- valid limits ----


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        (FIXED, True),
        (limits(129, 130, 100), True),
        (limits(44, 130, 88), False),
        (limits(45, 131, 88), False),
        (limits(45, 130, 87), False),
        (limits(45, 130, 101), False),
        (limits(100, 100, 88), False),
        (limits(110, 100, 88), False),
        ({"hr_bpm": {"min": 45.0, "max": 130}, "spo2_pct": {"min": 88}}, False),
        ({"hr_bpm": {"min": True, "max": 130}, "spo2_pct": {"min": 88}}, False),
        ({"hr_bpm": {"min": 45, "max": 130}, "spo2_pct": {"min": 88, "x": 1}}, False),
        ({"hr_bpm": {"min": 45, "max": 130, "x": 1}, "spo2_pct": {"min": 88}}, False),
        ({**FIXED, "skin_temp_c": {"min": 30, "max": 38}}, False),
        ({"hr_bpm": {"min": 45, "max": 130}}, False),
        (None, False),
        ([], False),
    ],
)
def test_is_valid(value, ok):
    assert device_limits.is_valid(value) is ok


# ---- which items are wearables ----


def item(device_id="wearable-0001", resident_id="res-0001", **fields):
    out = {"pk": f"DEVICE#{device_id}", "device_type": device_id.split("-")[0]}
    if resident_id is not None:
        out["resident_id"] = resident_id
    out.update(fields)
    return out


def test_wearables_are_kept_with_their_resident_or_none():
    wearables, skipped = handler.wearables_from_items(
        [item(), item("wearable-0002", None), item("bedroom-0001")]
    )
    assert wearables == {"wearable-0001": "res-0001", "wearable-0002": None}
    assert skipped == 0


@pytest.mark.parametrize(
    "bad",
    [
        item(pk="wearable-0001"),
        item(pk="DEVICE#wearable-1"),
        item(pk="DEVICE#wearable-0001\n"),
        item(pk="RESIDENT#wearable-0001"),
        item(pk=7),
        item(resident_id="resident-1"),
        item(resident_id=["res-0001"]),
        item(resident_id={"S": "res-0001"}),
        item(resident_id=""),
    ],
)
def test_malformed_wearable_is_skipped_and_counted(bad):
    assert handler.wearables_from_items([bad, item("wearable-0003")]) == (
        {"wearable-0003": "res-0001"},
        1,
    )


def test_unknown_device_type_is_ignored_not_counted():
    assert handler.wearables_from_items([item(device_type=["wearable"])]) == ({}, 0)


# ---- when a shadow needs a write ----


def doc(desired):
    return {"state": {"desired": {"limits": desired}}, "version": 4}


def test_equal_desired_limits_need_no_write():
    assert handler.needs_update(doc(FIXED), FIXED) is False


@pytest.mark.parametrize(
    "document",
    [
        None,
        {},
        {"state": {}},
        {"state": {"reported": {"limits": FIXED}}},
        {"state": "x"},
        doc(limits(45, 120, 88)),
        doc({**FIXED, "x": 1}),
        doc(None),
        [],
    ],
)
def test_missing_different_or_invalid_desired_needs_a_write(document):
    assert handler.needs_update(document, FIXED) is True


# ---- copies stay in step ----


def test_device_limits_copies_are_byte_identical():
    copies = [
        LAMBDAS / name / "src" / "device_limits.py"
        for name in ("shadow-sync", "api-thresholds", "api-devices")
    ]
    first = copies[0].read_bytes()
    for copy in copies[1:]:
        assert copy.read_bytes() == first, copy


def _code(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text[text.index("from __future__") :]


def test_thresholds_code_matches_the_api_copies():
    mine = _code(LAMBDAS / "shadow-sync" / "src" / "thresholds.py")
    for name in ("api-thresholds", "api-residents", "api-dashboard"):
        assert _code(LAMBDAS / name / "src" / "thresholds.py") == mine, name


def _ingest_thresholds():
    name = "ingest_thresholds"
    if name not in sys.modules:
        path = LAMBDAS / "ingest" / "src" / "thresholds.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@pytest.mark.parametrize(
    "stored",
    [
        None,
        {},
        {"hr_bpm": {"min": Decimal(55), "max": Decimal(110)}},
        {"hr_bpm": {"min": Decimal(100), "max": Decimal(90)}},
        {"hr_bpm": {"min": "55", "max": Decimal("110.5")}},
        {"spo2_pct": {"min": Decimal(93)}},
        {"spo2_pct": {"min": Decimal(101)}},
        {"hr_bpm": "x", "spo2_pct": [1]},
        {"hr_bpm": {"min": True, "max": Decimal(250)}},
    ],
)
def test_resolution_matches_ingest(stored):
    # ingest reads a low-level GetItem item; shadow-sync a deserialized one.
    ingest = _ingest_thresholds()
    mine, _ = thresholds.resolve(stored)
    low_level = {k: TypeSerializer().serialize(v) for k, v in (stored or {}).items()}
    theirs, _ = ingest.limits_from_item(low_level or None)
    for metric in ("hr_bpm", "spo2_pct"):
        assert (mine[metric].min, mine[metric].max) == (
            theirs[metric].min,
            theirs[metric].max,
        ), metric
