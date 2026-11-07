"""Vitals thresholds for the daily summaries (docs/features/curated-layer.md,
criterion 8).

A copy of the rules in `lambdas/ingest/src/thresholds.py` `limits_from_item` (each
function is packaged separately). Keep the two in step: change both, and their tests,
together.

Defaults (user's decision, 2026-11-02; synthetic, to be checked with a clinician before
any real use): heart rate below 50 or above 120 bpm, SpO2 below 92 %, skin temperature
below 30.0 or above 38.0 degC.

A resident's own limits are in the registry item `pk = THRESHOLDS#<resident_id>`,
`sk = THRESHOLDS`:

    hr_bpm       {"min": <int>, "max": <int>}
    spo2_pct     {"min": <int>}
    skin_temp_c  {"min": <number>, "max": <number>}

Each limit that is present and valid replaces that default; a missing or invalid one
keeps the default. A limit is valid when it is a number in the metric's valid range
from docs/metrics.md (an integer for hr_bpm and spo2_pct). If a metric's resolved min
is not below its max, both of that metric's limits fall back to the defaults (the
metric is reported as invalid).

Every limit is a Decimal (or None for "no limit"), so they reach the SQL exactly.
Unlike ingest, nothing is cached: the summary job reads them once a day.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import TypeDeserializer

METRICS = ("hr_bpm", "spo2_pct", "skin_temp_c")


@dataclass(frozen=True)
class Limits:
    """One metric's limits. None means no limit in that direction."""

    min: Decimal | None
    max: Decimal | None


DEFAULTS: dict[str, Limits] = {
    "hr_bpm": Limits(min=Decimal(50), max=Decimal(120)),
    "spo2_pct": Limits(min=Decimal(92), max=None),
    "skin_temp_c": Limits(min=Decimal("30.0"), max=Decimal("38.0")),
}


@dataclass(frozen=True)
class LimitRule:
    """Which limits a metric may have, and the valid range of each (docs/metrics.md)."""

    names: tuple[str, ...]
    minimum: Decimal
    maximum: Decimal
    integer: bool


RULES: dict[str, LimitRule] = {
    "hr_bpm": LimitRule(("min", "max"), Decimal(20), Decimal(250), integer=True),
    "spo2_pct": LimitRule(("min",), Decimal(50), Decimal(100), integer=True),
    "skin_temp_c": LimitRule(
        ("min", "max"), Decimal("20.0"), Decimal("45.0"), integer=False
    ),
}

_deserializer = TypeDeserializer()


def _valid_limit(value: Any, rule: LimitRule) -> bool:
    if not isinstance(value, Decimal) or not value.is_finite():
        return False
    if rule.integer and value != value.to_integral_value():
        return False
    return rule.minimum <= value <= rule.maximum


def limits_from_item(
    item: dict[str, Any] | None,
) -> tuple[dict[str, Limits], list[str]]:
    """(limits per metric, invalid field names) from a low-level item.

    Field names are like `hr_bpm.max`, or `hr_bpm` when its min is not below its max.
    Never the values.
    """
    limits = dict(DEFAULTS)
    invalid: list[str] = []
    if not item:
        return limits, invalid
    for metric, rule in RULES.items():
        raw = item.get(metric)
        if raw is None:
            continue
        try:
            value = _deserializer.deserialize(raw)
        except (TypeError, ValueError, KeyError):
            invalid.append(metric)
            continue
        if not isinstance(value, dict):
            invalid.append(metric)
            continue
        resolved = {"min": DEFAULTS[metric].min, "max": DEFAULTS[metric].max}
        for name in rule.names:
            if name not in value:
                continue
            if _valid_limit(value[name], rule):
                resolved[name] = value[name]
            else:
                invalid.append(f"{metric}.{name}")
        low, high = resolved["min"], resolved["max"]
        if low is not None and high is not None and not low < high:
            invalid.append(metric)
            continue
        limits[metric] = Limits(min=low, max=high)
    return limits, invalid


def as_json(limits: dict[str, Limits]) -> dict[str, dict[str, int | float]]:
    """The `thresholds` object of a summary (criterion 11): integers for heart rate and
    SpO2, numbers for skin temperature, and no key for "no limit" (SpO2 max)."""
    out: dict[str, dict[str, int | float]] = {}
    for metric in METRICS:
        rule = RULES[metric]
        entry: dict[str, int | float] = {}
        for name in ("min", "max"):
            value = getattr(limits[metric], name)
            if value is None:
                continue
            entry[name] = int(value) if rule.integer else float(value)
        out[metric] = entry
    return out
