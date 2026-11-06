"""Test helpers: cases.json, ingest's validation module, and a fake Athena."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import sql

CLEAN_SRC = Path(__file__).resolve().parent.parent / "src"
VALIDATION_PATH = (
    Path(__file__).resolve().parents[2] / "ingest" / "src" / "validation.py"
)
UNLOAD_TARGET = re.compile(r" TO 's3://([^/']+)/([^']+)' WITH ")
# Stands for data Athena could quote in a failure reason: it must never be logged.
LEAKY_REASON = "wearable-0001 line hr_bpm 72 00000000-0000-4000-8000-000000000001"


def load_validation() -> ModuleType:
    """Ingest's real validation.py, loaded by path.

    Every Lambda has its own src/ folder and test run. Loading only this one file under
    its own name leaves ingest's folder off sys.path, so its handler.py can't shadow
    clean's. A missing file raises: this must never turn into a skipped test.
    """
    name = "ingest_validation"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, VALIDATION_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # dataclasses look their module up here
        spec.loader.exec_module(module)
    return sys.modules[name]


CASES: list[dict[str, Any]] = json.loads(
    (CLEAN_SRC / "cases.json").read_text(encoding="utf-8")
)["cases"]


def case_by_id(case_id: str) -> dict[str, Any]:
    return next(case for case in CASES if case["id"] == case_id)


def ingest_result(case: dict[str, Any]) -> Any:
    """What ingest's validate_event says about this case's line."""
    validation = load_validation()
    try:
        event = json.loads(case["line"])
    except json.JSONDecodeError:
        event = case["line"]  # not an object, so validate_event rejects it
    handled = frozenset().union(*validation.ALLOWED_MESSAGES.values())
    return validation.validate_event(event, handled)


def ingest_outcome(case: dict[str, Any]) -> str:
    rejected = isinstance(ingest_result(case), load_validation().Rejection)
    return "rejected" if rejected else "valid"


def _as_text(column: str, value: Any) -> str | None:
    """A value as Athena's results would show it."""
    if value is None:
        return None
    if column in {"accel_g", "accel_window"}:
        return json.dumps(value)
    return str(value).lower() if isinstance(value, bool) else str(value)


def correct_rows(table: str) -> list[dict[str, str | None]]:
    """The rows the real SQL must return for this table when run on every case."""
    if table == sql.REJECTED_TABLE:
        unique = {
            (case["raw_message"], case["expected_reason"], case["line"])
            for case in CASES
            if case["expected_clean"] == "rejected"
            and not case.get("dropped_as_duplicate")
        }
        return [
            {"message": message, "reason": reason, "raw_line": line}
            for message, reason, line in sorted(unique)
        ]
    return [
        {col: _as_text(col, value) for col, value in case["expected_values"].items()}
        for case in CASES
        if case.get("expected_table") == table and "expected_values" in case
    ]


class _Paginator:
    def __init__(self, athena: FakeAthena) -> None:
        self.athena = athena

    def paginate(self, QueryExecutionId: str, PaginationConfig: dict) -> list[dict]:
        rows = self.athena.results[self.athena.query_ids.index(QueryExecutionId)]
        if not rows:
            return [{"ResultSet": {"Rows": []}}]
        cells = [{"Data": [{"VarCharValue": name} for name in rows[0]]}]
        for row in rows:
            data = [
                {} if value is None else {"VarCharValue": value}
                for value in row.values()
            ]
            cells.append({"Data": data})
        size = self.athena.page_size or len(cells)
        return [
            {"ResultSet": {"Rows": cells[start : start + size]}}
            for start in range(0, len(cells), size)
        ]


class FakeAthena:
    """Records queries and acts like Athena where the job depends on it.

    - Refuses a new query while one is running: the job runs them one at a time.
    - With `s3` set, UNLOAD fails on a non-empty folder, as Athena's does, and otherwise
      writes one part file (none when `empty_day`).
    """

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.query_ids: list[str] = []
        self.requests: list[dict] = []
        self.s3: Any = None
        self.empty_day = False
        self.state_for = lambda query: "SUCCEEDED"
        self.running_polls = 0
        self.results: list[list[dict]] = []
        self.page_size: int | None = None
        self.start_error: Exception | None = None
        self.unfinished: set[str] = set()
        self._polls: dict[str, int] = {}

    def start_query_execution(self, **kwargs: Any) -> dict:
        assert not self.unfinished, "a query started before the last one finished"
        if self.start_error:
            raise self.start_error
        query = kwargs["QueryString"]
        query_id = f"query-test-{len(self.queries):04d}"
        self.requests.append(kwargs)
        self.queries.append(query)
        self.query_ids.append(query_id)
        self.unfinished.add(query_id)
        self._polls[query_id] = 0
        if query.startswith("UNLOAD") and self.s3:
            self._unload(query)
        return {"QueryExecutionId": query_id}

    def _unload(self, query: str) -> None:
        bucket, prefix = UNLOAD_TARGET.search(query).groups()
        found = self.s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        assert found["KeyCount"] == 0, f"UNLOAD to a non-empty folder: {prefix}"
        if not self.empty_day:
            key = f"{prefix}part-{len(self.queries):04d}.parquet"
            self.s3.put_object(Bucket=bucket, Key=key, Body=b"synthetic")

    def get_query_execution(self, QueryExecutionId: str) -> dict:
        self._polls[QueryExecutionId] += 1
        query = self.queries[self.query_ids.index(QueryExecutionId)]
        if self._polls[QueryExecutionId] <= self.running_polls:
            state = "RUNNING"
        else:
            state = self.state_for(query)
            self.unfinished.discard(QueryExecutionId)
        return {
            "QueryExecution": {
                "Status": {"State": state, "StateChangeReason": LEAKY_REASON},
                "Statistics": {"DataScannedInBytes": 1234},
            }
        }

    def get_paginator(self, name: str) -> _Paginator:
        assert name == "get_query_results"
        return _Paginator(self)
