"""Builders for synthetic summaries and events (no real resident data)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import features as f

DATE = "2026-11-18"


def summary(resident_id: str = "res-0001", date: str = DATE, **overrides: Any) -> dict:
    """A curated-layer summary (curated-layer.md criterion 11), obviously synthetic.
    Values wobble with the day of month so baselines have some spread."""
    wobble = int(date[-2:]) % 3
    base = {
        "resident_id": resident_id,
        "date": date,
        "hr_bpm_min": 50 + wobble,
        "hr_bpm_avg": 60.0 + wobble,
        "hr_bpm_max": 90 + wobble,
        "spo2_pct_min": 94,
        "spo2_pct_avg": 97.0,
        "spo2_pct_max": 99,
        "skin_temp_c_min": 31.0,
        "skin_temp_c_avg": 33.5,
        "skin_temp_c_max": 35.0,
        "activity_pct_avg": 12.0 + wobble,
        "vitals_readings": 1300,
        "hr_bpm_below": 0,
        "hr_bpm_above": 0,
        "spo2_pct_below": 0,
        "skin_temp_c_below": 0,
        "skin_temp_c_above": 0,
        "thresholds": {
            "hr_bpm": {"min": 45, "max": 130},
            "spo2_pct": {"min": 88},
            "skin_temp_c": {"min": 30.0, "max": 38.0},
        },
        "bed_minutes": 420 + 10 * wobble,
        "motion_events": 20 + wobble,
        "falls": 0,
        "vitals_alert_episodes": 0,
        "cancels": 0,
        "generated_at": "2026-11-19T00:30:00.000Z",
        "schema_version": 1,
    }
    return {**base, **overrides}


def night(**overrides: Any) -> f.Night:
    return f.parse_summary(
        json.dumps(summary(**overrides)).encode(), overrides.get("date", DATE)
    )


def put_summary(aws, s: dict) -> None:
    aws.put(
        "silvercare-history-test",
        f"summaries/date={s['date']}/{uuid.uuid4()}.json",
        json.dumps(s),
    )


def put_history(aws, resident_id: str, date: str, days: int, **overrides: Any) -> None:
    """Summaries for `days` nights ending the night before `date`."""
    for d in range(1, days + 1):
        put_summary(aws, summary(resident_id, f.shift(date, -d), **overrides))


def on_success(date: str = DATE, loaded: int = 3) -> dict:
    """Lambda's on-success record from silvercare-results."""
    return {
        "version": "1.0",
        "requestContext": {"condition": "Success", "functionArn": "arn:test"},
        "responsePayload": {"date": date, "loaded": loaded},
    }


def transform_event(name: str, status: str = "Completed") -> dict:
    return {
        "source": "aws.sagemaker",
        "detail-type": "SageMaker Transform Job State Change",
        "detail": {"TransformJobName": name, "TransformJobStatus": status},
    }


def training_event(name: str, status: str = "Completed") -> dict:
    return {
        "source": "aws.sagemaker",
        "detail-type": "SageMaker Training Job State Change",
        "detail": {"TrainingJobName": name, "TrainingJobStatus": status},
    }
