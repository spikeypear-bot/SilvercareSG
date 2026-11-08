"""Builders for GET /residents/{resident_id}/nightly inputs. All IDs and values are
synthetic and fixed, so log and body checks never meet a random UUID by chance.

"Now" defaults to 2026-11-19 09:30:00 SGT (01:30:00 UTC): yesterday's Singapore date,
the `expected_date`, is 2026-11-18.
"""

from __future__ import annotations

import copy
import os
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import TypeSerializer

SGT = timezone(timedelta(hours=8))


def sgt(*args: int) -> datetime:
    return datetime(*args, tzinfo=SGT).astimezone(UTC)


NOW = sgt(2026, 11, 19, 9, 30, 0)
BEFORE_READY = sgt(2026, 11, 19, 9, 29, 59)
YESTERDAY = "2026-11-18"

SUB = "00000000-0000-4000-8000-000000000001"
REQUEST_ID = "JpvNMgAbSQ0EJcQ="
EMAIL = "test.doctor.zq@example.invalid"
NAME = "Test Doctor Zq"

RESIDENT_A = "res-0003"
RESIDENT_B = "res-0004"
RESIDENT_UNKNOWN = "res-0999"
LOADED_AT = "2026-11-19T00:41:07.512Z"

# A night's summary map as results stores it (numbers as Decimal).
_SUMMARY: dict[str, Any] = {
    "hr_bpm_min": Decimal(52),
    "hr_bpm_avg": Decimal("71.4"),
    "hr_bpm_max": Decimal(128),
    "spo2_pct_min": Decimal(90),
    "spo2_pct_avg": Decimal("96.1"),
    "spo2_pct_max": Decimal(99),
    "skin_temp_c_min": Decimal("31.2"),
    "skin_temp_c_avg": Decimal("33.8"),
    "skin_temp_c_max": Decimal("35.0"),
    "activity_pct_avg": Decimal("14.0"),
    "vitals_readings": Decimal(1312),
    "hr_bpm_below": Decimal(0),
    "hr_bpm_above": Decimal(12),
    "spo2_pct_below": Decimal(3),
    "skin_temp_c_below": Decimal(0),
    "skin_temp_c_above": Decimal(0),
    "thresholds": {
        "hr_bpm": {"min": Decimal(50), "max": Decimal(120)},
        "spo2_pct": {"min": Decimal(92)},
        "skin_temp_c": {"min": Decimal("30.0"), "max": Decimal("38.0")},
    },
    "bed_minutes": Decimal(460),
    "motion_events": Decimal(23),
    "falls": Decimal(0),
    "vitals_alert_episodes": Decimal(1),
    "cancels": Decimal(0),
}

# The spec's response example night for 2026-11-18.
EXPECTED_NIGHT: dict[str, Any] = {
    "date": "2026-11-18",
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
}

# Values the logs must never contain (criterion 20).
LOG_SENTINELS = ("1312", "71.4", "96.1", "33.8", "31.2", "460", "128", "thresholds")

_serializer = TypeSerializer()


def summary(**overrides: Any) -> dict[str, Any]:
    out = copy.deepcopy(_SUMMARY)
    out.update(overrides)
    return out


def night_item(
    resident_id: str = RESIDENT_A, date: str = YESTERDAY, **summary_overrides: Any
) -> dict[str, Any]:
    """A Results item as results writes it (deserialized form)."""
    return {
        "pk": f"RESIDENT#{resident_id}",
        "sk": f"NIGHT#{date}",
        "resident_id": resident_id,
        "date": date,
        "summary": summary(**summary_overrides),
        "schema_version": 1,
        "loaded_at": LOADED_AT,
        "expires_at": 1797609600,
    }


def put_raw(ddb, item: dict[str, Any], table: str | None = None) -> None:
    ddb.put_item(
        TableName=table or os.environ["RESULTS_TABLE"],
        Item={k: _serializer.serialize(v) for k, v in item.items()},
    )


def put_night(ddb, resident_id: str = RESIDENT_A, date: str = YESTERDAY, **kw):
    put_raw(ddb, night_item(resident_id, date, **kw))


def api_event(
    resident_id: Any = RESIDENT_A,
    groups: Any = ("doctor",),
    claims: dict[str, Any] | None = None,
    drop_groups: bool = False,
    **top: Any,
) -> dict[str, Any]:
    """GET /residents/{resident_id}/nightly with a Cognito JWT (payload 2.0)."""
    jwt_claims: dict[str, Any] = {
        "sub": SUB,
        "email": EMAIL,
        "name": NAME,
        "cognito:groups": list(groups) if isinstance(groups, tuple) else groups,
        "token_use": "id",
    }
    if drop_groups:
        del jwt_claims["cognito:groups"]
    jwt_claims.update(claims or {})
    path = f"/residents/{resident_id}/nightly"
    event: dict[str, Any] = {
        "version": "2.0",
        "routeKey": "GET /residents/{resident_id}/nightly",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"authorization": "Bearer synthetic-token"},
        "pathParameters": {"resident_id": resident_id},
        "requestContext": {
            "http": {"method": "GET", "path": path},
            "requestId": REQUEST_ID,
            "routeKey": "GET /residents/{resident_id}/nightly",
            "stage": "$default",
            "authorizer": {"jwt": {"claims": jwt_claims, "scopes": None}},
        },
        "isBase64Encoded": False,
    }
    event.update(top)
    return event
