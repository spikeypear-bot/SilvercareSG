"""Builders for results inputs: summary objects (curated-layer.md criterion 11) and the
two event forms (nightly-results.md criterion 4). All values are synthetic and fixed:
keys use fixed UUIDs, and the statistics are distinctive numbers (1312, 71.4, 460) so
log checks can search for them without meeting an ID by chance.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

DATE = "2026-11-18"
NEXT_DATE = "2026-11-19"
RESIDENT_A = "res-0003"
RESIDENT_B = "res-0004"
RESIDENT_C = "res-0005"
GENERATED_AT = "2026-11-19T00:40:58.004Z"
# When results writes the item (the spec's example).
LOADED_AT = datetime(2026, 11, 19, 0, 41, 7, 512000, tzinfo=UTC)
LOADED_AT_TEXT = "2026-11-19T00:41:07.512Z"


def key(n: int, date: str = DATE) -> str:
    """summaries/date=<D>/<uuid4>.json, as summaries writes it, with a fixed UUID."""
    return f"summaries/date={date}/00000000-0000-4000-8000-{n:012d}.json"


_SUMMARY: dict[str, Any] = {
    "resident_id": RESIDENT_A,
    "date": DATE,
    "hr_bpm_min": 52,
    "hr_bpm_avg": 71.4,
    "hr_bpm_max": 128,
    "spo2_pct_min": 90,
    "spo2_pct_avg": 96.1,
    "spo2_pct_max": 99,
    "skin_temp_c_min": 31.2,
    "skin_temp_c_avg": 33.8,
    "skin_temp_c_max": 35.0,
    "activity_pct_avg": 14.0,
    "vitals_readings": 1312,
    "hr_bpm_below": 0,
    "hr_bpm_above": 12,
    "spo2_pct_below": 3,
    "skin_temp_c_below": 0,
    "skin_temp_c_above": 0,
    "thresholds": {
        "hr_bpm": {"min": 50, "max": 120},
        "spo2_pct": {"min": 92},
        "skin_temp_c": {"min": 30.0, "max": 38.0},
    },
    "bed_minutes": 460,
    "motion_events": 23,
    "falls": 0,
    "vitals_alert_episodes": 1,
    "cancels": 0,
    "generated_at": GENERATED_AT,
    "schema_version": 1,
}

# Values the logs must never contain (criterion 11).
LOG_SENTINELS = ("1312", "71.4", "96.1", "33.8", "31.2", "460", "128", "thresholds")


def summary(**overrides: Any) -> dict[str, Any]:
    """The spec's example summary for res-0003 on 2026-11-18, with overrides."""
    out = copy.deepcopy(_SUMMARY)
    out.update(overrides)
    return out


def empty_summary(**overrides: Any) -> dict[str, Any]:
    """A resident with an assigned device but no readings: statistics null, counts 0."""
    out = summary(
        resident_id=RESIDENT_B,
        hr_bpm_min=None,
        hr_bpm_avg=None,
        hr_bpm_max=None,
        spo2_pct_min=None,
        spo2_pct_avg=None,
        spo2_pct_max=None,
        skin_temp_c_min=None,
        skin_temp_c_avg=None,
        skin_temp_c_max=None,
        activity_pct_avg=None,
        vitals_readings=0,
        hr_bpm_above=0,
        spo2_pct_below=0,
        bed_minutes=None,
        motion_events=0,
        vitals_alert_episodes=0,
    )
    out.update(overrides)
    return out


def without(field: str, **overrides: Any) -> dict[str, Any]:
    out = summary(**overrides)
    del out[field]
    return out


def on_success(
    date: Any = DATE, summaries: Any = 2, condition: str = "Success", **payload: Any
) -> dict[str, Any]:
    """Lambda's on-success invocation record for an async silvercare-summaries run."""
    response_payload: dict[str, Any] = {"date": date, "summaries": summaries}
    response_payload.update(payload)
    return {
        "version": "1.0",
        "timestamp": "2026-11-19T00:41:05.871Z",
        "requestContext": {
            "requestId": "00000000-0000-4000-8000-0000000000e1",
            "functionArn": (
                "arn:aws:lambda:ap-southeast-1:000000000000:"
                "function:silvercare-summaries:$LATEST"
            ),
            "condition": condition,
            "approximateInvokeCount": 1,
        },
        "requestPayload": {"date": date},
        "responseContext": {"statusCode": 200, "executedVersion": "$LATEST"},
        "responsePayload": response_payload,
    }
