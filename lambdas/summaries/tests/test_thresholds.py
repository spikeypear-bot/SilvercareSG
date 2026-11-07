"""Threshold resolution (docs/features/curated-layer.md, criterion 8).

The resolution cases are ingest's (lambdas/ingest/tests/test_thresholds.py, the
"resolution from the item" section), run against this function's copy of
`limits_from_item`. Ingest's cache, `direction`, and `limits_for_resident` aren't
copied (the summary compares in SQL; see test_sql.py for the strict boundaries). Every
case is also run through ingest's real `thresholds.py`, loaded by path, so the two
copies can't drift apart.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import handler
import pytest
import thresholds
from helpers import put_device, put_thresholds
from thresholds import DEFAULTS, Limits, limits_from_item

RESIDENT = "res-0002"
INGEST_THRESHOLDS = (
    Path(__file__).resolve().parents[2] / "ingest" / "src" / "thresholds.py"
)


def load_ingest_thresholds() -> ModuleType:
    """Ingest's real thresholds.py, loaded by path under its own name, so ingest's
    folder stays off sys.path. A missing file raises: never a skipped test."""
    name = "ingest_thresholds"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, INGEST_THRESHOLDS)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # dataclasses look their module up here
        spec.loader.exec_module(module)
    return sys.modules[name]


def n(value) -> dict:
    return {"N": str(value)}


def item(**metrics) -> dict:
    """A THRESHOLDS item in low-level format; values are dicts of name -> number."""
    out = {"pk": {"S": f"THRESHOLDS#{RESIDENT}"}, "sk": {"S": "THRESHOLDS"}}
    for metric, limits in metrics.items():
        if isinstance(limits, dict):
            out[metric] = {"M": {k: n(v) for k, v in limits.items()}}
        else:
            out[metric] = limits
    return out


def with_attribute(metric: str, name: str, attribute: dict) -> dict:
    built = item()
    built[metric] = {"M": {name: attribute}}
    return built


# ---- defaults (decisions table) ----


def test_defaults_are_the_decided_limits():
    assert DEFAULTS == {
        "hr_bpm": Limits(min=Decimal(50), max=Decimal(120)),
        "spo2_pct": Limits(min=Decimal(92), max=None),
        "skin_temp_c": Limits(min=Decimal("30.0"), max=Decimal("38.0")),
    }


# ---- resolution from the item (ingest's cases) ----


def test_no_item_gives_the_defaults():
    assert limits_from_item(None) == (DEFAULTS, [])


def test_empty_item_gives_the_defaults():
    assert limits_from_item({}) == (DEFAULTS, [])


def test_full_item_replaces_every_limit():
    limits, invalid = limits_from_item(
        item(
            hr_bpm={"min": 45, "max": 110},
            spo2_pct={"min": 88},
            skin_temp_c={"min": "31.5", "max": "37.5"},
        )
    )
    assert limits == {
        "hr_bpm": Limits(min=Decimal(45), max=Decimal(110)),
        "spo2_pct": Limits(min=Decimal(88), max=None),
        "skin_temp_c": Limits(min=Decimal("31.5"), max=Decimal("37.5")),
    }
    assert invalid == []


def test_partial_item_keeps_defaults_for_the_rest():
    # The seed script's res-0002: only a lower SpO2 min.
    limits, _ = limits_from_item(item(spo2_pct={"min": 88}))
    assert limits["spo2_pct"] == Limits(min=Decimal(88), max=None)
    assert limits["hr_bpm"] == DEFAULTS["hr_bpm"]
    assert limits["skin_temp_c"] == DEFAULTS["skin_temp_c"]


def test_partial_metric_keeps_the_default_for_its_other_limit():
    limits, _ = limits_from_item(item(hr_bpm={"max": 110}))
    assert limits["hr_bpm"] == Limits(min=Decimal(50), max=Decimal(110))


INVALID_LIMITS = [
    ("hr_bpm.max", n(300)),
    ("hr_bpm.min", n(19)),
    ("hr_bpm.max", n("110.5")),
    ("hr_bpm.max", {"S": "high"}),
    ("hr_bpm.max", {"BOOL": True}),
    ("hr_bpm.max", {"NULL": True}),
    ("spo2_pct.min", n(49)),
    ("spo2_pct.min", n(101)),
    ("skin_temp_c.max", n("45.1")),
    ("skin_temp_c.min", n("19.9")),
]


@pytest.mark.parametrize(("field", "attribute"), INVALID_LIMITS)
def test_invalid_limit_keeps_its_default_and_is_reported_by_field_name(
    field, attribute
):
    metric, name = field.split(".")
    limits, invalid = limits_from_item(with_attribute(metric, name, attribute))
    assert limits[metric] == DEFAULTS[metric]
    assert invalid == [field]


@pytest.mark.parametrize(
    ("field", "attribute"),
    [
        ("hr_bpm.min", n(20)),
        ("hr_bpm.max", n(250)),
        ("spo2_pct.min", n(50)),
        ("spo2_pct.min", n(100)),
        ("skin_temp_c.min", n("20.0")),
        ("skin_temp_c.max", n("45.0")),
    ],
)
def test_limit_at_the_edge_of_its_valid_range_is_accepted(field, attribute):
    # Each edge is still on the right side of the metric's other (default) limit.
    metric, name = field.split(".")
    limits, invalid = limits_from_item(with_attribute(metric, name, attribute))
    assert invalid == []
    assert getattr(limits[metric], name) == Decimal(attribute["N"])


def test_metric_that_is_not_a_map_is_invalid():
    built = item()
    built["hr_bpm"] = {"N": "110"}
    limits, invalid = limits_from_item(built)
    assert limits["hr_bpm"] == DEFAULTS["hr_bpm"]
    assert invalid == ["hr_bpm"]


def test_metric_that_is_not_an_attribute_value_is_invalid():
    built = item()
    built["hr_bpm"] = {"X": "110"}
    limits, invalid = limits_from_item(built)
    assert limits["hr_bpm"] == DEFAULTS["hr_bpm"]
    assert invalid == ["hr_bpm"]


def test_spo2_max_is_ignored():
    # SpO2 has no upper limit; an extra max is not a limit and not reported.
    limits, invalid = limits_from_item(item(spo2_pct={"min": 90, "max": 99}))
    assert limits["spo2_pct"] == Limits(min=Decimal(90), max=None)
    assert invalid == []


@pytest.mark.parametrize(
    "hr", [{"min": 110, "max": 110}, {"min": 115, "max": 100}, {"min": 125}]
)
def test_min_not_below_max_falls_back_to_both_defaults(hr):
    # {"min": 125} alone: 125 is not below the default max 120.
    limits, invalid = limits_from_item(item(hr_bpm=hr))
    assert limits["hr_bpm"] == DEFAULTS["hr_bpm"]
    assert invalid == ["hr_bpm"]


def test_skin_min_not_below_max_falls_back_to_both_defaults():
    limits, invalid = limits_from_item(item(skin_temp_c={"min": "37.0", "max": "37.0"}))
    assert limits["skin_temp_c"] == DEFAULTS["skin_temp_c"]
    assert invalid == ["skin_temp_c"]


def test_invalid_report_has_field_names_not_values():
    _, invalid = limits_from_item(item(hr_bpm={"max": 247.5}, spo2_pct={"min": 7}))
    assert invalid == ["hr_bpm.max", "spo2_pct.min"]
    assert "247" not in str(invalid)
    assert "7" not in "".join(invalid)


def test_valid_item_reports_nothing():
    assert limits_from_item(item(spo2_pct={"min": 88}))[1] == []


def test_resolved_limits_are_decimals():
    limits, _ = limits_from_item(item(skin_temp_c={"min": "31.5"}))
    for metric_limits in limits.values():
        for value in (metric_limits.min, metric_limits.max):
            assert value is None or isinstance(value, Decimal)


# ---- the same results as ingest's copy ----

PARITY_ITEMS = {
    "none": None,
    "empty": {},
    "full": item(
        hr_bpm={"min": 45, "max": 110},
        spo2_pct={"min": 88},
        skin_temp_c={"min": "31.5", "max": "37.5"},
    ),
    "partial": item(spo2_pct={"min": 88}),
    "partial-metric": item(hr_bpm={"max": 110}),
    "not-a-map": {**item(), "hr_bpm": {"N": "110"}},
    "spo2-max": item(spo2_pct={"min": 90, "max": 99}),
    "equal": item(hr_bpm={"min": 110, "max": 110}),
    "crossed": item(hr_bpm={"min": 115, "max": 100}),
    "min-over-default-max": item(hr_bpm={"min": 125}),
    "two-invalid": item(hr_bpm={"max": 247.5}, spo2_pct={"min": 7}),
    **{
        f"invalid-{field}-{i}": with_attribute(*field.split("."), attribute)
        for i, (field, attribute) in enumerate(INVALID_LIMITS)
    },
}


def _plain(limits: dict) -> dict:
    return {metric: (lim.min, lim.max) for metric, lim in limits.items()}


def _rules(rules: dict) -> dict:
    return {m: (r.names, r.minimum, r.maximum, r.integer) for m, r in rules.items()}


@pytest.mark.parametrize("case", PARITY_ITEMS)
def test_resolution_matches_ingests_limits_from_item(case):
    ingest = load_ingest_thresholds()
    built = PARITY_ITEMS[case]
    ours, our_invalid = limits_from_item(built)
    theirs, their_invalid = ingest.limits_from_item(built)
    assert _plain(ours) == _plain(theirs)
    assert our_invalid == their_invalid


def test_defaults_and_rules_match_ingests():
    ingest = load_ingest_thresholds()
    assert _plain(DEFAULTS) == _plain(ingest.DEFAULTS)
    assert _rules(thresholds.RULES) == _rules(ingest.RULES)


# ---- the thresholds object of a summary (criterion 11) ----


def test_default_thresholds_json_is_the_specs_example():
    assert thresholds.as_json(DEFAULTS) == {
        "hr_bpm": {"min": 50, "max": 120},
        "spo2_pct": {"min": 92},
        "skin_temp_c": {"min": 30.0, "max": 38.0},
    }


def test_thresholds_json_types():
    out = thresholds.as_json(DEFAULTS)
    assert type(out["hr_bpm"]["min"]) is int
    assert type(out["spo2_pct"]["min"]) is int
    assert type(out["skin_temp_c"]["max"]) is float
    assert "max" not in out["spo2_pct"]
    assert json.dumps(out["skin_temp_c"]) == '{"min": 30.0, "max": 38.0}'


def test_custom_thresholds_json():
    limits, _ = limits_from_item(item(skin_temp_c={"min": "31.5", "max": "37.5"}))
    assert thresholds.as_json(limits)["skin_temp_c"] == {"min": 31.5, "max": 37.5}


# ---- handler: reading each resident's thresholds (criterion 8) ----


def test_thresholds_lookup_keys_and_projection(aws):
    handler.read_limits(["res-0001", "res-0002"])
    (call,) = [kw for svc, op, kw in aws.calls if op == "batch_get_item"]
    request = call["RequestItems"]["silvercare-registry"]
    assert request["Keys"] == [
        {"pk": {"S": "THRESHOLDS#res-0001"}, "sk": {"S": "THRESHOLDS"}},
        {"pk": {"S": "THRESHOLDS#res-0002"}, "sk": {"S": "THRESHOLDS"}},
    ]
    names = request["ExpressionAttributeNames"]
    projected = {names[a.strip()] for a in request["ProjectionExpression"].split(",")}
    assert projected == {"pk", "hr_bpm", "spo2_pct", "skin_temp_c"}
    assert not request.get("ConsistentRead")


def test_resident_without_a_thresholds_item_gets_the_defaults(aws):
    put_thresholds(aws.ddb, "res-0002", spo2_pct={"min": 88})
    limits = handler.read_limits(["res-0001", "res-0002"])
    assert limits["res-0001"] == DEFAULTS
    assert limits["res-0002"]["spo2_pct"] == Limits(min=Decimal(88), max=None)
    assert limits["res-0002"]["hr_bpm"] == DEFAULTS["hr_bpm"]


def test_each_resident_gets_their_own_limits(aws):
    put_thresholds(aws.ddb, "res-0001", hr_bpm={"max": 110})
    put_thresholds(aws.ddb, "res-0002", hr_bpm={"max": 130})
    limits = handler.read_limits(["res-0001", "res-0002"])
    assert limits["res-0001"]["hr_bpm"].max == Decimal(110)
    assert limits["res-0002"]["hr_bpm"].max == Decimal(130)


def test_invalid_thresholds_are_logged_with_resident_and_field_only(aws, logs):
    put_thresholds(aws.ddb, "res-0002", hr_bpm={"max": 251}, spo2_pct={"min": 49})
    limits = handler.read_limits(["res-0002"])
    assert limits["res-0002"] == DEFAULTS
    (record,) = [r for r in logs() if r["level"] == "WARNING"]
    assert record["message"] == "invalid thresholds, defaults used"
    assert record["resident_id"] == "res-0002"
    assert record["fields"] == ["hr_bpm.max", "spo2_pct.min"]
    text = json.dumps(record)
    assert "251" not in text
    assert "49" not in text


def test_valid_thresholds_log_nothing(aws, logs):
    put_thresholds(aws.ddb, "res-0002", spo2_pct={"min": 88})
    handler.read_limits(["res-0002"])
    assert [r for r in logs() if r["level"] == "WARNING"] == []


def test_resolved_limits_reach_the_query(aws, athena):
    put_device(aws.ddb, "wearable-0002", resident_id="res-0002")
    put_thresholds(aws.ddb, "res-0002", hr_bpm={"min": 300, "max": 110})
    handler.lambda_handler({"date": "2026-10-18"}, None)
    # min 300 is invalid, so the default 50 stays; max 110 applies.
    assert "('res-0002', 50, 110, 92," in athena.queries[0]
