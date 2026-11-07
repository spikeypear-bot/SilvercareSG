"""Resolved vitals thresholds, for the resident status (docs/data-model.md, "Resident
thresholds" and "Resident status").

The resolution rules are the telemetry Lambda's (lambdas/ingest/src/thresholds.py),
copied because each function is zipped from its own src/ folder. Keep the two in step:

- Defaults (user's decision, 2026-11-02; synthetic): heart rate below 50 or above
  120 bpm, SpO2 below 92 %, skin temperature below 30.0 or above 38.0 degC.
- Each limit that is present and valid (a number in the metric's valid range from
  docs/metrics.md, an integer for hr_bpm and spo2_pct) replaces its default; a missing
  or invalid one keeps it. If a metric's resolved min is not below its max, both of its
  limits keep the defaults.
- A value is abnormal when it is strictly below min or strictly above max. None (no
  reading) is never abnormal.

Limits are Decimals so comparisons with stored values are exact: 38.0 is not above 38.0.
This module makes no AWS calls; the handler reads the items (BatchGetItem) and caches
the result.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

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


def _valid_limit(value: Any, rule: LimitRule) -> bool:
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        return False
    value = Decimal(value)
    if not value.is_finite():
        return False
    if rule.integer and value != value.to_integral_value():
        return False
    return rule.minimum <= value <= rule.maximum


def resolve(item: Mapping[str, Any] | None) -> tuple[dict[str, Limits], list[str]]:
    """(limits per metric, invalid field names) from a deserialized THRESHOLDS item, or
    the defaults for None. Field names are like `hr_bpm.max`, never values."""
    limits = dict(DEFAULTS)
    invalid: list[str] = []
    if not item:
        return limits, invalid
    for metric, rule in RULES.items():
        value = item.get(metric)
        if value is None:
            continue
        if not isinstance(value, Mapping):
            invalid.append(metric)
            continue
        resolved = {"min": DEFAULTS[metric].min, "max": DEFAULTS[metric].max}
        for name in rule.names:
            if name not in value:
                continue
            if _valid_limit(value[name], rule):
                resolved[name] = Decimal(value[name])
            else:
                invalid.append(f"{metric}.{name}")
        low, high = resolved["min"], resolved["max"]
        if low is not None and high is not None and not low < high:
            invalid.append(metric)
            continue
        limits[metric] = Limits(min=low, max=high)
    return limits, invalid


def is_abnormal(value: Any, limits: Limits) -> bool:
    """Strictly outside the limits. None (and anything that isn't a number) is never
    abnormal."""
    if isinstance(value, bool) or not isinstance(value, Decimal | int | float):
        return False
    if isinstance(value, float):
        # The response carries skin_temp_c as a float, and a float compares exactly with
        # a Decimal: float(36.6) > Decimal("36.6"). repr() gives the shortest decimal
        # that round-trips, so a one-decimal reading becomes exactly that Decimal again.
        value = Decimal(repr(value))
    if limits.min is not None and value < limits.min:
        return True
    return limits.max is not None and value > limits.max
