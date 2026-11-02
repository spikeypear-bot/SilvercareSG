"""The cloud vitals check in `telemetry` (docs/features/vitals-alerts.md, criteria 6-10).

Pure functions, no AWS calls. After a wearable `vitals` message is stored, the device's
newest two entries are compared with the resident's limits (thresholds.py). For each of
hr_bpm, spo2_pct, and skin_temp_c separately, a finding is raised when both entries are
abnormal in the same direction and their `ts` are at most 3 minutes apart (so a
wearable taken off for hours doesn't pair old and new readings). A null reading is
never abnormal, so it breaks a streak. One message can raise several findings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from thresholds import ABOVE, BELOW, METRICS, Limits, direction

MAX_PAIR_GAP = timedelta(minutes=3)
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

# (metric, direction) -> alert_type (criterion 1).
ALERT_TYPES: dict[tuple[str, str], str] = {
    ("hr_bpm", ABOVE): "hr_high",
    ("hr_bpm", BELOW): "hr_low",
    ("spo2_pct", BELOW): "spo2_low",
    ("skin_temp_c", ABOVE): "skin_temp_high",
    ("skin_temp_c", BELOW): "skin_temp_low",
}


@dataclass(frozen=True)
class Finding:
    """One metric abnormal in two readings in a row, in the same direction."""

    alert_type: str
    metric: str
    direction: str
    value: Any  # newest entry's reading
    previous_value: Any  # the entry before it
    limit: Any  # the threshold crossed


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, TS_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def recent_pair(
    entries: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(newest, previous) if the two newest entries are at most 3 minutes apart."""
    if not entries or len(entries) < 2:
        return None
    newest, previous = entries[0], entries[1]
    newest_ts, previous_ts = _parse_ts(newest.get("ts")), _parse_ts(previous.get("ts"))
    if newest_ts is None or previous_ts is None:
        return None
    if abs(newest_ts - previous_ts) > MAX_PAIR_GAP:
        return None
    return newest, previous


def check_pair(
    newest: dict[str, Any], previous: dict[str, Any], limits: dict[str, Limits]
) -> list[Finding]:
    """Findings for a pair of entries (from recent_pair), in METRICS order."""
    findings = []
    for metric in METRICS:
        metric_limits = limits[metric]
        value, previous_value = newest.get(metric), previous.get(metric)
        now_dir = direction(value, metric_limits)
        if now_dir is None or now_dir != direction(previous_value, metric_limits):
            continue
        findings.append(
            Finding(
                alert_type=ALERT_TYPES[(metric, now_dir)],
                metric=metric,
                direction=now_dir,
                value=value,
                previous_value=previous_value,
                limit=metric_limits.min if now_dir == BELOW else metric_limits.max,
            )
        )
    return findings


def check(
    entries: list[dict[str, Any]] | None, limits: dict[str, Limits]
) -> list[Finding]:
    """Findings for the device's entries (newest first)."""
    pair = recent_pair(entries)
    if pair is None:
        return []
    return check_pair(pair[0], pair[1], limits)
