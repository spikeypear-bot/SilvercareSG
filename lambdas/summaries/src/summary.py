"""Summary objects from the query's rows (docs/features/curated-layer.md, criteria 11
and 15). Pure functions, no AWS calls.

Each summary is one JSON object, in the field order of criterion 11. Statistics are
null when there was no non-null reading; counts are integers. `thresholds` holds the
resolved limits the query used, so the record keeps the limits that applied.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

import sql
import thresholds
from registry import is_resident_id
from thresholds import Limits

SCHEMA_VERSION = 1

# Fields that sit after the statistics and counts, in criterion 11's order.
_TAIL = ("bed_minutes", "motion_events", "falls", "vitals_alert_episodes", "cancels")


class BadResultError(Exception):
    """The query returned something other than one well-formed row per resident."""


def _value(text: str | None, kind: str) -> Any:
    if text is None:
        return None
    if kind == "str":
        return text
    if kind == "int":
        try:
            return int(text)
        except ValueError:
            number = float(text)
            if not number.is_integer():
                raise
            return int(number)
    number = float(text)
    if not math.isfinite(number):
        raise ValueError("not a finite number")
    return round(number, 1)


def parse_row(row: Mapping[str, str | None]) -> dict[str, Any]:
    """One result row as typed values, by column name. Raises BadResultError without
    repeating any value."""
    try:
        return {name: _value(row.get(name), kind) for name, kind in sql.RESULT_COLUMNS}
    except (TypeError, ValueError, OverflowError):
        raise BadResultError("result row has a value of the wrong type") from None


def build(
    row: Mapping[str, Any],
    date: str,
    limits: Mapping[str, Limits],
    generated_at: str,
) -> dict[str, Any]:
    """One resident's summary (criterion 11)."""
    resident_id = row["resident_id"]
    summary: dict[str, Any] = {"resident_id": resident_id, "date": date}
    for name, _ in sql.RESULT_COLUMNS:
        if name == "resident_id" or name in _TAIL:
            continue
        summary[name] = row[name]
    summary["thresholds"] = thresholds.as_json(dict(limits))
    for name in _TAIL:
        summary[name] = row[name]
    summary["generated_at"] = generated_at
    summary["schema_version"] = SCHEMA_VERSION
    return summary


def build_all(
    rows: list[Mapping[str, str | None]],
    date: str,
    limits: Mapping[str, Mapping[str, Limits]],
    generated_at: str,
) -> list[dict[str, Any]]:
    """Every resident's summary. Raises BadResultError unless the rows are exactly one
    per resident in `limits` (every mapped resident), so nothing is replaced on a bad
    result."""
    parsed = [parse_row(row) for row in rows]
    ids = [row["resident_id"] for row in parsed]
    if not all(is_resident_id(rid) for rid in ids):
        raise BadResultError("result has an invalid resident_id")
    if len(set(ids)) != len(ids) or set(ids) != set(limits):
        raise BadResultError("result does not have one row per resident")
    return [
        build(row, date, limits[row["resident_id"]], generated_at) for row in parsed
    ]


def body(summary: Mapping[str, Any]) -> bytes:
    """The object body: one line of compact JSON, UTF-8 (OpenX JSON SerDe reads one
    object per line)."""
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
