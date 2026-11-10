"""The anomaly fields on both nightly routes (docs/features/nightly-anomaly.md, criteria
15-18)."""

from __future__ import annotations

import json
from decimal import Decimal

import handler
import pytest
from events import (
    CLEAR_COUNTS,
    NOW,
    RESIDENT_A,
    YESTERDAY,
    api_event,
    night_item,
    overview_event,
    put_raw,
    put_resident,
)

VERSION = "anomaly-train-20261116-a1b2c3d4"


def scored(score: str = "2.8412", unusual: bool = True, **overrides) -> dict:
    value = {
        "status": "scored",
        "score": Decimal(score),
        "cutoff": Decimal("2.3071"),
        "unusual": unusual,
        "reasons": [
            {
                "feature": "hr_bpm_avg",
                "value": Decimal("78.0"),
                "usual": Decimal("58.2"),
            },
            {
                "feature": "bed_minutes",
                "value": Decimal(240),
                "usual": Decimal("426.0"),
            },
        ],
        "usable_nights": Decimal(30),
        "model_version": VERSION,
        "schema_version": Decimal(1),
    }
    return {**value, **overrides}


def status_only(status: str) -> dict:
    return {
        "status": status,
        "score": None,
        "cutoff": None,
        "unusual": None,
        "reasons": [],
        "usable_nights": Decimal(12),
        "model_version": None,
        "schema_version": Decimal(1),
    }


def put_anomaly(aws, resident_id: str, anomaly, **counts) -> None:
    values = {k: Decimal(v) for k, v in counts.items()}
    item = night_item(resident_id, YESTERDAY, **{**CLEAR_COUNTS, **values})
    put_raw(aws.ddb, {**item, "anomaly": anomaly})


def night(aws) -> dict:
    resp = handler.handle(api_event(), NOW)
    (n,) = json.loads(resp["body"])["nights"]
    return n


def overview(aws) -> dict:
    return json.loads(handler.handle_overview(overview_event(), NOW)["body"])


# ---- resident page route ----


def test_scored_night_returns_the_anomaly(aws):
    put_anomaly(aws, RESIDENT_A, scored())
    assert night(aws)["anomaly"] == {
        "status": "scored",
        "score": 2.84,
        "cutoff": 2.31,
        "unusual": True,
        "reasons": [
            {"feature": "hr_bpm_avg", "value": 78.0, "usual": 58.2},
            {"feature": "bed_minutes", "value": 240.0, "usual": 426.0},
        ],
        "usable_nights": 30,
        "model_version": VERSION,
    }


@pytest.mark.parametrize("status", ["low_wear", "not_enough_history", "no_model"])
def test_unscored_status_returns_nulls(aws, status):
    put_anomaly(aws, RESIDENT_A, status_only(status))
    got = night(aws)["anomaly"]
    assert got["status"] == status and got["score"] is None and got["reasons"] == []


def test_night_without_anomaly_returns_null(aws):
    put_raw(aws.ddb, night_item())
    assert night(aws)["anomaly"] is None


@pytest.mark.parametrize(
    "bad",
    [
        "not a map",
        scored(status="maybe"),
        scored(unusual="yes"),
        {**scored(), "score": "2.84"},
        scored(model_version="../x"),
        scored(reasons=[{"feature": "display_name", "value": 1, "usual": 1}]),
        {**status_only("low_wear"), "score": Decimal(1)},
    ],
)
def test_malformed_anomaly_is_null_and_keeps_the_night(aws, bad):
    put_anomaly(aws, RESIDENT_A, bad)
    n = night(aws)
    assert n["anomaly"] is None and n["date"] == YESTERDAY


# ---- overview route ----


def test_unusual_only_night_is_flagged_last(aws):
    for r in ("res-0003", "res-0004", "res-0005", "res-0006"):
        put_resident(aws.ddb, r, "Test Resident A")
    put_anomaly(aws, "res-0003", scored("2.5"))
    put_anomaly(aws, "res-0004", scored("3.9"))
    put_anomaly(aws, "res-0005", scored("1.0", unusual=False), hr_bpm_below=2)
    put_anomaly(aws, "res-0006", scored("1.0", unusual=False))
    body = overview(aws)
    assert [e["resident_id"] for e in body["flagged"]] == [
        "res-0005",
        "res-0004",
        "res-0003",
    ]
    assert body["flagged"][1]["unusual"] is True
    assert body["flagged"][1]["anomaly_reasons"][0]["feature"] == "hr_bpm_avg"
    assert body["flagged"][0]["unusual"] is False
    assert body["clear_count"] == 1


def test_overview_never_returns_the_score(aws):
    put_resident(aws.ddb, "res-0003", "Test Resident A")
    put_anomaly(aws, "res-0003", scored("2.5"))
    (entry,) = overview(aws)["flagged"]
    assert "_score" not in entry and "score" not in json.dumps(entry)


def test_malformed_anomaly_doesnt_hide_a_fall(aws, logs):
    put_resident(aws.ddb, "res-0003", "Test Resident A")
    put_anomaly(aws, "res-0003", "not a map", falls=1)
    (entry,) = overview(aws)["flagged"]
    assert entry["falls"] == 1 and entry["unusual"] is False
    # Logged with the ID, never the value (criteria 18, 25).
    (warning,) = [r for r in logs() if r.get("outcome") == "invalid_anomaly"]
    assert warning["resident_id"] == "res-0003"
    assert "not a map" not in json.dumps(logs())
