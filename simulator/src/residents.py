"""Residents, their devices, and their baselines from the seed and device files
(docs/features/simulator-core.md, criteria 4 to 6), and each wearable's device alert
limits (docs/features/device-alert-limits.md, "Device limits" and "Valid limits").

Problem messages name IDs only, never values from the files: a malformed "ID" or
value could hold anything.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-[0-9]{4}$")
RESIDENT_ID_RE = re.compile(r"^res-[0-9]{4}$")
DEVICE_TYPES = ("wearable", "bedroom")

# How far inside the resident's thresholds the baseline (and every normal reading)
# must stay, so normal data never looks abnormal (criterion 5).
HR_MARGIN = 8
SPO2_MARGIN = 3
TEMP_MARGIN_TENTHS = 10  # 1.0 degC

# The contract's valid ranges (docs/metrics.md, wearable vitals).
HR_RANGE = (20, 250)
SPO2_RANGE = (50, 100)
TEMP_RANGE_TENTHS = (200, 450)


class SetupError(Exception):
    """A problem with the flags or input files. The message names IDs only."""


@dataclass(frozen=True)
class Limits:
    """The range every normal reading stays in: the resident's thresholds narrowed by
    the margins, and inside the contract's valid range. Skin temperature in tenths."""

    hr_min: int
    hr_max: int
    spo2_min: int
    spo2_max: int
    temp_min_tenths: int
    temp_max_tenths: int


@dataclass(frozen=True)
class DeviceLimits:
    """A wearable's on-device vitals_alert limits (the shadow's `limits`)."""

    hr_min: int
    hr_max: int
    spo2_min: int

    def to_dict(self) -> dict:
        return {
            "hr_bpm": {"min": self.hr_min, "max": self.hr_max},
            "spo2_pct": {"min": self.spo2_min},
        }


# The loosest limits a device may use (docs/metrics.md, vitals_alert), also the limits
# of an unassigned wearable.
FIXED_LIMITS = DeviceLimits(hr_min=45, hr_max=130, spo2_min=88)
# The cloud's defaults for a missing or invalid threshold, as
# lambdas/ingest/src/thresholds.py resolves them.
DEFAULT_HR = (50, 120)
DEFAULT_SPO2_MIN = 92


def _threshold(raw: Any, key: str, valid: tuple[int, int]) -> int | None:
    """One threshold limit if present and valid (an integer in the metric's range,
    like ingest's Decimal check: 45.0 counts as 45), else None."""
    value = raw.get(key) if isinstance(raw, dict) else None
    if not _is_number(value) or not float(value).is_integer():
        return None
    value = int(value)
    return value if valid[0] <= value <= valid[1] else None


def device_limits(thresholds: Any) -> DeviceLimits:
    """A wearable's limits from its resident's thresholds: resolved like the cloud's
    check (missing or invalid limit -> that default; min not below max -> both
    defaults), then the stricter of each and FIXED_LIMITS. If heart rate min is not
    below max, heart rate falls back to FIXED_LIMITS'."""
    raw = thresholds if isinstance(thresholds, dict) else {}
    hr_raw = raw.get("hr_bpm")
    hr_min = _threshold(hr_raw, "min", HR_RANGE)
    hr_max = _threshold(hr_raw, "max", HR_RANGE)
    hr_min = DEFAULT_HR[0] if hr_min is None else hr_min
    hr_max = DEFAULT_HR[1] if hr_max is None else hr_max
    if hr_min >= hr_max:
        hr_min, hr_max = DEFAULT_HR
    spo2 = _threshold(raw.get("spo2_pct"), "min", SPO2_RANGE)
    spo2 = DEFAULT_SPO2_MIN if spo2 is None else spo2
    limits = DeviceLimits(
        hr_min=max(hr_min, FIXED_LIMITS.hr_min),
        hr_max=min(hr_max, FIXED_LIMITS.hr_max),
        spo2_min=max(spo2, FIXED_LIMITS.spo2_min),
    )
    if limits.hr_min >= limits.hr_max:
        limits = DeviceLimits(FIXED_LIMITS.hr_min, FIXED_LIMITS.hr_max, limits.spo2_min)
    return limits


def parse_device_limits(value: Any) -> DeviceLimits | None:
    """Valid limits (spec "Valid limits") -> DeviceLimits, anything else -> None:
    integers, hr_bpm.min 45-250, hr_bpm.max 20-130, min < max, spo2_pct.min 88-100,
    no other keys."""
    if not isinstance(value, dict) or set(value) != {"hr_bpm", "spo2_pct"}:
        return None
    hr, spo2 = value["hr_bpm"], value["spo2_pct"]
    if not isinstance(hr, dict) or set(hr) != {"min", "max"}:
        return None
    if not isinstance(spo2, dict) or set(spo2) != {"min"}:
        return None
    hr_min, hr_max, spo2_min = hr["min"], hr["max"], spo2["min"]
    if not all(_is_int(v) for v in (hr_min, hr_max, spo2_min)):
        return None
    if not (
        45 <= hr_min <= 250
        and 20 <= hr_max <= 130
        and hr_min < hr_max
        and 88 <= spo2_min <= 100
    ):
        return None
    return DeviceLimits(hr_min, hr_max, spo2_min)


def reading_limits(device: DeviceLimits, limits: Limits) -> Limits:
    """The range normal readings stay in under the wearable's current device limits
    (criterion 21): heart rate [min + 8, max - 8], or (min + max) // 2 if that is
    empty; SpO2 at least min + 3 (at most 100); skin temperature from the seed."""
    hr_low, hr_high = device.hr_min + HR_MARGIN, device.hr_max - HR_MARGIN
    if hr_low > hr_high:
        hr_low = hr_high = (device.hr_min + device.hr_max) // 2
    return replace(
        limits,
        hr_min=hr_low,
        hr_max=hr_high,
        spo2_min=min(device.spo2_min + SPO2_MARGIN, SPO2_RANGE[1]),
    )


@dataclass(frozen=True)
class Baseline:
    hr_bpm: int
    spo2_pct: int
    skin_temp_tenths: int  # 33.4 degC -> 334


@dataclass(frozen=True)
class Resident:
    resident_id: str
    wearable_id: str | None
    bedroom_id: str | None
    baseline: Baseline
    limits: Limits
    device_limits: DeviceLimits = FIXED_LIMITS  # the wearable's start limits


def _id_or(value: Any, pattern: re.Pattern[str], fallback: str) -> str:
    if isinstance(value, str) and pattern.fullmatch(value):
        return value
    return fallback


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _tenths(value: float) -> int:
    return round(value * 10)


def load_json(path: Path, label: str) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise SetupError(f"{label} not found") from None
    except (OSError, ValueError):
        raise SetupError(f"{label} is not readable JSON") from None


def _limits(thresholds: Any) -> Limits | None:
    """Limits from a resident's thresholds, or None if they are malformed."""
    try:
        hr = thresholds["hr_bpm"]
        spo2 = thresholds["spo2_pct"]
        temp = thresholds["skin_temp_c"]
        values = (hr["min"], hr["max"], spo2["min"], temp["min"], temp["max"])
    except (KeyError, TypeError):
        return None
    if not all(_is_number(v) for v in values):
        return None
    return Limits(
        hr_min=max(HR_RANGE[0], int(hr["min"]) + HR_MARGIN),
        hr_max=min(HR_RANGE[1], int(hr["max"]) - HR_MARGIN),
        spo2_min=max(SPO2_RANGE[0], int(spo2["min"]) + SPO2_MARGIN),
        spo2_max=SPO2_RANGE[1],
        temp_min_tenths=max(
            TEMP_RANGE_TENTHS[0], _tenths(temp["min"]) + TEMP_MARGIN_TENTHS
        ),
        temp_max_tenths=min(
            TEMP_RANGE_TENTHS[1], _tenths(temp["max"]) - TEMP_MARGIN_TENTHS
        ),
    )


def _baseline(value: Any, label: str, problems: list[str]) -> Baseline | None:
    if not isinstance(value, dict) or set(value) != {
        "hr_bpm",
        "spo2_pct",
        "skin_temp_c",
    }:
        problems.append(
            f"resident {label}: baseline needs exactly hr_bpm, spo2_pct, skin_temp_c"
        )
        return None
    hr, spo2, temp = value["hr_bpm"], value["spo2_pct"], value["skin_temp_c"]
    if not _is_int(hr) or not _is_int(spo2):
        problems.append(
            f"resident {label}: baseline hr_bpm and spo2_pct must be integers"
        )
        return None
    if not _is_number(temp) or abs(temp * 10 - round(temp * 10)) > 1e-6:
        problems.append(
            f"resident {label}: baseline skin_temp_c must be a number with one decimal"
        )
        return None
    return Baseline(hr_bpm=hr, spo2_pct=spo2, skin_temp_tenths=_tenths(temp))


def _check_margins(
    baseline: Baseline, limits: Limits, label: str, problems: list[str]
) -> None:
    if not limits.hr_min <= baseline.hr_bpm <= limits.hr_max:
        problems.append(
            f"resident {label}: baseline hr_bpm must be at least {HR_MARGIN} bpm"
            " inside the hr_bpm thresholds"
        )
    if not limits.spo2_min <= baseline.spo2_pct <= limits.spo2_max:
        problems.append(
            f"resident {label}: baseline spo2_pct must be at least {SPO2_MARGIN}"
            " above the spo2_pct threshold and at most 100"
        )
    if (
        not limits.temp_min_tenths
        <= baseline.skin_temp_tenths
        <= limits.temp_max_tenths
    ):
        problems.append(
            f"resident {label}: baseline skin_temp_c must be at least 1.0 degC"
            " inside the skin_temp_c thresholds"
        )


def _registry_devices(devices_doc: Any, problems: list[str]) -> list[tuple[str, str]]:
    """(device_id, type) for every device in devices.json."""
    entries = devices_doc.get("devices") if isinstance(devices_doc, dict) else None
    if not isinstance(entries, list) or not entries:
        problems.append("devices file: devices must be a non-empty list")
        return []
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        device_id = entry.get("id") if isinstance(entry, dict) else None
        label = _id_or(device_id, DEVICE_ID_RE, f"#{index + 1}")
        if label.startswith("#"):
            problems.append(f"devices file: device {label} has an invalid ID")
            continue
        dtype = entry.get("type")
        if dtype not in DEVICE_TYPES or not device_id.startswith(f"{dtype}-"):
            problems.append(f"devices file: device {label} has a wrong type")
            continue
        if device_id in seen:
            problems.append(f"devices file: device {label} is listed twice")
            continue
        seen.add(device_id)
        result.append((device_id, dtype))
    return result


def build_residents(seed: Any, devices_doc: Any) -> tuple[list[Resident], list[str]]:
    """Validate the seed and devices files and return (residents, devices in
    devices.json order). Raises SetupError listing every problem (IDs only)."""
    problems: list[str] = []
    registry = _registry_devices(devices_doc, problems)

    residents_raw = seed.get("residents") if isinstance(seed, dict) else None
    seed_devices = seed.get("devices") if isinstance(seed, dict) else None
    if not isinstance(residents_raw, list) or not isinstance(seed_devices, list):
        raise SetupError("seed file: residents and devices must be lists")

    parsed: dict[str, tuple[Baseline, Limits, DeviceLimits]] = {}
    for index, resident in enumerate(residents_raw):
        rid = resident.get("resident_id") if isinstance(resident, dict) else None
        label = _id_or(rid, RESIDENT_ID_RE, f"#{index + 1}")
        if label.startswith("#"):
            problems.append(f"resident {label}: invalid resident_id")
            continue
        if rid in parsed:
            problems.append(f"resident {label}: listed twice")
            continue
        limits = _limits(resident.get("thresholds"))
        if limits is None:
            problems.append(f"resident {label}: thresholds are malformed")
            continue
        if "baseline" not in resident:
            problems.append(f"resident {label}: missing baseline")
            continue
        baseline = _baseline(resident["baseline"], label, problems)
        if baseline is None:
            continue
        _check_margins(baseline, limits, label, problems)
        parsed[rid] = (baseline, limits, device_limits(resident.get("thresholds")))

    # device_id -> (type, resident_id) from the seed file's assignments.
    assigned: dict[str, tuple[str, str]] = {}
    for entry in seed_devices:
        if not isinstance(entry, dict):
            continue
        device_id = entry.get("device_id")
        resident_id = entry.get("resident_id")
        if (
            isinstance(device_id, str)
            and DEVICE_ID_RE.fullmatch(device_id)
            and isinstance(resident_id, str)
            and RESIDENT_ID_RE.fullmatch(resident_id)
        ):
            assigned[device_id] = (str(entry.get("device_type")), resident_id)

    per_resident: dict[str, dict[str, str]] = {}
    for device_id, dtype in registry:
        if device_id not in assigned:
            problems.append(f"device {device_id}: not assigned to a resident")
            continue
        seed_type, resident_id = assigned[device_id]
        if seed_type != dtype:
            problems.append(f"device {device_id}: type differs from the seed file")
            continue
        if resident_id not in parsed and not any(
            p.startswith(f"resident {resident_id}:") for p in problems
        ):
            problems.append(f"device {device_id}: assigned to an unknown resident")
            continue
        slot = per_resident.setdefault(resident_id, {})
        if dtype in slot:
            problems.append(f"resident {resident_id}: more than one {dtype}")
            continue
        slot[dtype] = device_id

    if problems:
        raise SetupError("; ".join(problems))

    residents = [
        Resident(
            resident_id=rid,
            wearable_id=per_resident.get(rid, {}).get("wearable"),
            bedroom_id=per_resident.get(rid, {}).get("bedroom"),
            baseline=baseline,
            limits=limits,
            device_limits=wearable_limits,
        )
        for rid, (baseline, limits, wearable_limits) in sorted(parsed.items())
    ]
    return residents, [device_id for device_id, _ in registry]
