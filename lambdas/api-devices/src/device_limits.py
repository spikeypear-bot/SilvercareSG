"""A wearable's device alert limits (docs/features/device-alert-limits.md, "Device
limits"; docs/metrics.md, "Device shadow"). Pure: no AWS calls.

Copied because each function is zipped from its own src/ folder: the same file is in
lambdas/shadow-sync/src/, lambdas/api-thresholds/src/, and lambdas/api-devices/src/.
Keep them identical.

`limits` shape, everywhere (shadow, API):
    {"hr_bpm": {"min": int, "max": int}, "spo2_pct": {"min": int}}
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# The loosest limits a device may use, and an unassigned wearable's limits.
FIXED_HR_MIN = 45
FIXED_HR_MAX = 130
FIXED_SPO2_MIN = 88


def fixed_limits() -> dict[str, dict[str, int]]:
    return {
        "hr_bpm": {"min": FIXED_HR_MIN, "max": FIXED_HR_MAX},
        "spo2_pct": {"min": FIXED_SPO2_MIN},
    }


def compute_limits(resolved: Mapping[str, Any] | None) -> dict[str, dict[str, int]]:
    """The device limits from a resident's resolved thresholds (thresholds.resolve:
    every hr_bpm and spo2_pct limit present, integers), or the fixed limits for an
    unassigned wearable (None). Each limit is the stricter of threshold and fixed; if
    that leaves hr_bpm.min >= hr_bpm.max, heart rate gets the fixed limits."""
    if resolved is None:
        return fixed_limits()
    hr, spo2 = resolved["hr_bpm"], resolved["spo2_pct"]
    hr_min = max(int(hr.min), FIXED_HR_MIN)
    hr_max = min(int(hr.max), FIXED_HR_MAX)
    if hr_min >= hr_max:
        hr_min, hr_max = FIXED_HR_MIN, FIXED_HR_MAX
    return {
        "hr_bpm": {"min": hr_min, "max": hr_max},
        "spo2_pct": {"min": max(int(spo2.min), FIXED_SPO2_MIN)},
    }


def _int_in(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def is_valid(limits: Any) -> bool:
    """Valid limits: integers, hr_bpm.min 45-250, hr_bpm.max 20-130, hr_bpm.min <
    hr_bpm.max, spo2_pct.min 88-100, no other keys."""
    if not isinstance(limits, dict) or set(limits) != {"hr_bpm", "spo2_pct"}:
        return False
    hr, spo2 = limits["hr_bpm"], limits["spo2_pct"]
    if not isinstance(hr, dict) or set(hr) != {"min", "max"}:
        return False
    if not isinstance(spo2, dict) or set(spo2) != {"min"}:
        return False
    return (
        _int_in(hr["min"], FIXED_HR_MIN, 250)
        and _int_in(hr["max"], 20, FIXED_HR_MAX)
        and hr["min"] < hr["max"]
        and _int_in(spo2["min"], FIXED_SPO2_MIN, 100)
    )
