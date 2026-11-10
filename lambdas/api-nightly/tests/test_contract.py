"""The item `results` writes is the item this route reads (nightly-results.md criteria
2, 17). results' handler is loaded from its own src/ under another module name (both
are called `handler`); nothing in it is changed.
"""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import handler
from boto3.dynamodb.types import TypeDeserializer
from events import BEFORE_READY, EXPECTED_NIGHT, RESIDENT_A, YESTERDAY

RESULTS_SRC = Path(__file__).resolve().parents[2] / "results" / "src" / "handler.py"


def _results_handler():
    # Its own Powertools service name, so it never shares this handler's logger.
    previous = os.environ.get("POWERTOOLS_SERVICE_NAME")
    os.environ["POWERTOOLS_SERVICE_NAME"] = "silvercare-results-contract-test"
    try:
        return _load()
    finally:
        if previous is None:
            del os.environ["POWERTOOLS_SERVICE_NAME"]
        else:
            os.environ["POWERTOOLS_SERVICE_NAME"] = previous


def _load():
    spec = importlib.util.spec_from_file_location("results_handler", RESULTS_SRC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_summary() -> dict:
    night = {k: v for k, v in EXPECTED_NIGHT.items() if k not in ("date", "anomaly")}
    return {
        "resident_id": RESIDENT_A,
        "date": YESTERDAY,
        **night,
        "generated_at": "2026-11-19T00:40:58.004Z",
        "schema_version": 1,
    }


def test_results_item_becomes_the_spec_night():
    results = _results_handler()
    body = json.dumps(_source_summary()).encode()
    summary = results.parse_summary(body, YESTERDAY)
    raw = results.build_item(summary, datetime(2026, 11, 19, 0, 41, 7, tzinfo=UTC))
    item = {k: TypeDeserializer().deserialize(v) for k, v in raw.items()}
    projected = {k: item[k] for k in ("pk", "sk", "summary")}
    nights, invalid = handler.build_nights([projected], BEFORE_READY)
    assert invalid == 0
    assert nights == [EXPECTED_NIGHT]


def test_results_item_key_matches_the_routes_query():
    results = _results_handler()
    summary = results.parse_summary(json.dumps(_source_summary()).encode(), YESTERDAY)
    raw = results.build_item(summary, datetime(2026, 11, 19, tzinfo=UTC))
    assert raw["pk"] == {"S": f"RESIDENT#{RESIDENT_A}"}
    assert raw["sk"]["S"].startswith(handler.NIGHT_PREFIX)
