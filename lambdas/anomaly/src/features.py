"""Pure feature logic for the nightly anomaly score (docs/features/nightly-anomaly.md,
criteria 1-4). No AWS calls: summaries in, z-score rows and explanations out."""

from __future__ import annotations

import json
import math
import re
from datetime import date as date_cls
from datetime import timedelta
from typing import Any

SCHEMA_VERSION = 1
RESIDENT_ID_RE = re.compile(r"res-[0-9]{4}")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# Model input order (Decisions); also the tie-break order for explanations.
FEATURES = (
    "hr_bpm_avg",
    "hr_bpm_min",
    "hr_bpm_max",
    "spo2_pct_avg",
    "spo2_pct_min",
    "skin_temp_c_avg",
    "activity_pct_avg",
    "bed_minutes",
    "motion_events",
)
# Smallest standard deviation used per feature (criterion 2), so a steady resident's
# tiny spread doesn't turn a normal night into a huge z-score.
SD_FLOOR = {
    "hr_bpm_avg": 2.0,
    "hr_bpm_min": 2.0,
    "hr_bpm_max": 2.0,
    "spo2_pct_avg": 0.5,
    "spo2_pct_min": 0.5,
    "skin_temp_c_avg": 0.2,
    "activity_pct_avg": 2.0,
    "bed_minutes": 20.0,
    "motion_events": 3.0,
}
MIN_READINGS = 720  # 50 % of 1,440 one-a-minute readings (low-wear rule)
BASELINE_FIRST = 37  # baseline = nights D-37 .. D-8
BASELINE_LAST = 8
MIN_BASELINE = 20  # usable baseline nights needed to score
EXPLAIN = 2


class InvalidSummary(ValueError):
    """An S3 object isn't a summary this job can use. Never carries a value."""


Night = dict[str, Any]  # resident_id, date, vitals_readings, the 9 features


def parse_date(value: Any) -> str:
    """A real date in exactly yyyy-mm-dd form, or raise without repeating it."""
    if type(value) is not str or not DATE_RE.fullmatch(value):
        raise ValueError("date must be yyyy-mm-dd")
    try:
        date_cls.fromisoformat(value)
    except ValueError:
        raise ValueError("date must be a real date") from None
    return value


def shift(date: str, days: int) -> str:
    return (date_cls.fromisoformat(date) + timedelta(days=days)).isoformat()


def baseline_dates(date: str) -> list[str]:
    """D-37 .. D-8 inclusive, oldest first (30 dates)."""
    return [shift(date, -d) for d in range(BASELINE_FIRST, BASELINE_LAST - 1, -1)]


def _is_number(value: Any) -> bool:
    return (type(value) is int or type(value) is float) and math.isfinite(value)


def _reject_constant(_: str) -> Any:
    raise InvalidSummary("not finite")


def parse_summary(body: bytes, date: str) -> Night:
    """The fields this job needs from one curated-layer summary object. Raises
    InvalidSummary (no value in the message) if it isn't a schema-1 summary for D."""
    try:
        obj = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise InvalidSummary("not JSON") from None
    if not isinstance(obj, dict):
        raise InvalidSummary("not an object")
    if type(obj.get("schema_version")) is not int or obj["schema_version"] != 1:
        raise InvalidSummary("schema_version")
    resident_id = obj.get("resident_id")
    if not isinstance(resident_id, str) or not RESIDENT_ID_RE.fullmatch(resident_id):
        raise InvalidSummary("resident_id")
    if obj.get("date") != date:
        raise InvalidSummary("date")
    readings = obj.get("vitals_readings")
    if type(readings) is not int or readings < 0:
        raise InvalidSummary("vitals_readings")
    night: Night = {
        "resident_id": resident_id,
        "date": date,
        "vitals_readings": readings,
    }
    for name in FEATURES:
        if name not in obj:
            raise InvalidSummary(name)
        value = obj[name]
        if value is not None and not _is_number(value):
            raise InvalidSummary(name)
        night[name] = None if value is None else float(value)
    return night


def is_usable(night: Night | None) -> bool:
    """Criterion 1: at least 720 readings and none of the 9 features null."""
    return (
        night is not None
        and night["vitals_readings"] >= MIN_READINGS
        and all(night[name] is not None for name in FEATURES)
    )


def baseline(nights: list[Night]) -> dict[str, tuple[float, float]]:
    """feature -> (mean, population SD floored), over usable nights (criterion 2)."""
    stats: dict[str, tuple[float, float]] = {}
    for name in FEATURES:
        values = [n[name] for n in nights]
        mean = sum(values) / len(values)
        sd = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
        stats[name] = (mean, max(sd, SD_FLOOR[name]))
    return stats


def zscores(night: Night, stats: dict[str, tuple[float, float]]) -> list[float]:
    """Criterion 3: one z per feature in FEATURES order, 4 decimals."""
    return [
        round((night[name] - stats[name][0]) / stats[name][1], 4) for name in FEATURES
    ]


def reasons(
    night: Night, stats: dict[str, tuple[float, float]], z: list[float]
) -> list[dict[str, Any]]:
    """Criterion 4: the 2 features with the largest |z|, ties in FEATURES order."""
    order = sorted(range(len(FEATURES)), key=lambda i: (-abs(z[i]), i))[:EXPLAIN]
    return [
        {
            "feature": FEATURES[i],
            "value": night[FEATURES[i]],
            "usual": round(stats[FEATURES[i]][0], 1),
        }
        for i in order
    ]


def assess(
    resident_id: str, date: str, nights: dict[tuple[str, str], Night]
) -> dict[str, Any]:
    """One resident-night: status `low_wear`, `not_enough_history`, or `scoreable`
    (with z and reasons). `nights` maps (resident_id, date) to parsed summaries."""
    night = nights.get((resident_id, date))
    usable = [
        n for d in baseline_dates(date) if is_usable(n := nights.get((resident_id, d)))
    ]
    result: dict[str, Any] = {"resident_id": resident_id, "usable_nights": len(usable)}
    if not is_usable(night):
        return {**result, "status": "low_wear"}
    if len(usable) < MIN_BASELINE:
        return {**result, "status": "not_enough_history"}
    stats = baseline(usable)
    z = zscores(night, stats)
    return {
        **result,
        "status": "scoreable",
        "z": z,
        "reasons": reasons(night, stats, z),
    }


def csv_rows(rows: list[list[float]]) -> bytes:
    """RCF input: one line of comma-separated numbers per row, no header."""
    return "".join(",".join(repr(v) for v in row) + "\n" for row in rows).encode()


def cutoff(scores: list[float]) -> dict[str, Any]:
    """Criterion 11: mean + 3 population SD of the training scores."""
    if not scores:
        raise ValueError("no training scores")
    mean = sum(scores) / len(scores)
    sd = math.sqrt(sum((s - mean) ** 2 for s in scores) / len(scores))
    return {
        "mean": round(mean, 4),
        "sd": round(sd, 4),
        "cutoff": round(mean + 3 * sd, 4),
        "rows": len(scores),
    }


def parse_scores(body: bytes) -> list[float]:
    """RCF batch output: one `{"score": x}` JSON line per input row."""
    scores = []
    for line in body.decode("utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line, parse_constant=_reject_constant)
        value = obj.get("score") if isinstance(obj, dict) else None
        if not _is_number(value):
            raise ValueError("score is not a number")
        scores.append(float(value))
    return scores
