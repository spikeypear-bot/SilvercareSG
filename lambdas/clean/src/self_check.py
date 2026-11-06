"""Compare the self-check's query results with cases.json (pure, no AWS)."""

from __future__ import annotations

import json
from typing import Any

import sql

_ACCEL_COLUMNS = frozenset({"accel_g", "accel_window"})
_REJECTED_COLUMNS = ("message", "reason", "raw_line")


def _same(column: str, expected: Any, actual: str | None) -> bool:
    """Athena returns every value as text (NULL as None); compare by meaning."""
    if expected is None or actual is None:
        return expected is None and actual is None
    if column in _ACCEL_COLUMNS:
        return json.loads(actual) == expected
    if isinstance(expected, bool):
        return actual == str(expected).lower()
    if isinstance(expected, int | float):
        return float(actual) == float(expected)
    return actual == expected


def _clean_failures(
    table: str, cases: list[dict[str, Any]], rows: list[dict[str, str | None]]
) -> list[str]:
    expected = [
        case
        for case in cases
        if case.get("expected_table") == table and "expected_values" in case
    ]
    by_id: dict[str | None, list[dict[str, str | None]]] = {}
    for row in rows:
        by_id.setdefault(row.get("message_id"), []).append(row)
    failures = []
    for case in expected:
        values = case["expected_values"]
        found = by_id.pop(values["message_id"], [])
        if len(found) != 1 or not all(
            _same(column, value, found[0].get(column))
            for column, value in values.items()
        ):
            failures.append(case["id"])
    if by_id:
        failures.append(f"{table}:unexpected_rows")
    return failures


def _rejected_failures(
    cases: list[dict[str, Any]], rows: list[dict[str, str | None]]
) -> list[str]:
    seen = [tuple(row.get(column) for column in _REJECTED_COLUMNS) for row in rows]
    expected = {
        (case["raw_message"], case["expected_reason"], case["line"]): case["id"]
        for case in cases
        if case["expected_clean"] == "rejected" and not case.get("dropped_as_duplicate")
    }
    failures = [case_id for row, case_id in expected.items() if seen.count(row) != 1]
    if set(seen) - set(expected):
        failures.append(f"{sql.REJECTED_TABLE}:unexpected_rows")
    return failures


def failures(
    table: str, cases: list[dict[str, Any]], rows: list[dict[str, str | None]]
) -> list[str]:
    """Case IDs whose outcome differs in this table's rows. Empty means pass."""
    if table == sql.REJECTED_TABLE:
        return _rejected_failures(cases, rows)
    return _clean_failures(table, cases, rows)
