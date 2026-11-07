"""Test helpers: synthetic registry items, clean partitions, and a fake Athena.

All data is synthetic: devices `wearable-000n` / `bedroom-000n`, residents `res-000n`.
"""

from __future__ import annotations

import re
from typing import Any

import sql
from botocore.exceptions import ClientError

# A Singapore date: 2026-10-17 16:00 UTC to 2026-10-18 16:00 UTC.
DATE = "2026-10-18"
ASSIGNED_AT = "2026-10-01T03:00:00Z"

CLEAN_TABLES = ("vitals", "fall", "vitals_alert", "alert_cancel", "bed", "motion")
# The clean partitions DATE reads (criterion 10), written out rather than taken from
# sql.py, so a change there is caught.
PARTITIONS_FOR_DATE = {
    ("vitals", "2026-10-17"),
    ("vitals", "2026-10-18"),
    ("fall", "2026-10-17"),
    ("fall", "2026-10-18"),
    ("alert_cancel", "2026-10-17"),
    ("alert_cancel", "2026-10-18"),
    ("motion", "2026-10-17"),
    ("motion", "2026-10-18"),
    ("bed", "2026-10-16"),
    ("bed", "2026-10-17"),
    ("bed", "2026-10-18"),
    ("vitals_alert", "2026-10-16"),
    ("vitals_alert", "2026-10-17"),
    ("vitals_alert", "2026-10-18"),
}

# Distinctive synthetic values. Tests check none of them reaches a log or an error:
# none is a substring of the date, a device or resident ID, or a fake query ID.
CUSTOM_LIMITS = {
    "hr_bpm": {"min": 47, "max": 113},
    "spo2_pct": {"min": 89},
    "skin_temp_c": {"min": "31.7", "max": "37.3"},
}
LIMIT_TEXTS = ("47", "113", "89", "31.7", "37.3")
READING_ROW = {
    "hr_bpm_min": "61",
    "hr_bpm_avg": "76.5",
    "hr_bpm_max": "143",
    "spo2_pct_min": "93",
    "spo2_pct_avg": "96.2",
    "spo2_pct_max": "99",
    "skin_temp_c_min": "33.4",
    "skin_temp_c_avg": "35.6",
    "skin_temp_c_max": "36.9",
    "activity_pct_avg": "27.3",
    "vitals_readings": "1388",
    "hr_bpm_below": "0",
    "hr_bpm_above": "52",
    "spo2_pct_below": "0",
    "skin_temp_c_below": "0",
    "skin_temp_c_above": "0",
    "bed_minutes": "417",
    "motion_events": "263",
    "falls": "0",
    "vitals_alert_episodes": "0",
    "cancels": "0",
}
READING_TEXTS = (
    "143",
    "76.5",
    "96.2",
    "33.4",
    "35.6",
    "36.9",
    "27.3",
    "1388",
    "417",
    "263",
)
# Null for a resident with no readings (criterion 11); every other column is a count.
NULL_WITHOUT_READINGS = {
    "hr_bpm_min",
    "hr_bpm_avg",
    "hr_bpm_max",
    "spo2_pct_min",
    "spo2_pct_avg",
    "spo2_pct_max",
    "skin_temp_c_min",
    "skin_temp_c_avg",
    "skin_temp_c_max",
    "activity_pct_avg",
    "bed_minutes",
}
# Stands for data Athena could quote in a failure reason: it must never be logged.
LEAKY_REASON = "wearable-0001 hr_bpm 143 res-0001 limit 113"


# ---- registry items (low-level, as DynamoDB stores them) ----


def device_item(
    device_id: str,
    resident_id: str | None = "res-0001",
    assigned_at: str | None = ASSIGNED_AT,
    device_type: str | None = None,
) -> dict[str, Any]:
    """A DEVICE#<id> / ASSIGNMENT item as docs/data-model.md shows it. Without a
    resident_id it is a spare (no resident, home, or by-resident keys)."""
    item: dict[str, Any] = {
        "pk": {"S": f"DEVICE#{device_id}"},
        "sk": {"S": "ASSIGNMENT"},
        "entity_type": {"S": "DEVICE"},
        "device_type": {"S": device_type or device_id.split("-", 1)[0]},
    }
    if resident_id is not None:
        item["resident_id"] = {"S": resident_id}
        item["home_id"] = {"S": "home-0001"}
        item["gsi1_pk"] = {"S": f"RESIDENT#{resident_id}"}
        item["gsi1_sk"] = {"S": f"DEVICE#{device_id}"}
    if assigned_at is not None:
        item["assigned_at"] = {"S": assigned_at}
    return item


def put_device(ddb, device_id: str, **kwargs: Any) -> None:
    ddb.put_item(TableName="silvercare-registry", Item=device_item(device_id, **kwargs))


ATTRIBUTE_TYPES = {"M", "N", "S", "BOOL", "NULL", "L"}


def _number_map(limits: dict[str, Any]) -> dict[str, Any]:
    return {"M": {name: {"N": str(value)} for name, value in limits.items()}}


def thresholds_item(resident_id: str, **metrics: Any) -> dict[str, Any]:
    """A THRESHOLDS#<id> / THRESHOLDS item; each metric is {name: number} or a raw
    attribute value."""
    item: dict[str, Any] = {
        "pk": {"S": f"THRESHOLDS#{resident_id}"},
        "sk": {"S": "THRESHOLDS"},
    }
    for metric, value in metrics.items():
        is_attribute = isinstance(value, dict) and set(value) <= ATTRIBUTE_TYPES
        item[metric] = value if is_attribute else _number_map(value)
    return item


def put_thresholds(ddb, resident_id: str, **metrics: Any) -> None:
    ddb.put_item(
        TableName="silvercare-registry", Item=thresholds_item(resident_id, **metrics)
    )


def put_resident(ddb, resident_id: str) -> None:
    """A resident profile on by-type (entity_type RESIDENT): never read by this job."""
    ddb.put_item(
        TableName="silvercare-registry",
        Item={
            "pk": {"S": f"RESIDENT#{resident_id}"},
            "sk": {"S": "PROFILE"},
            "entity_type": {"S": "RESIDENT"},
            "display_name": {"S": "Test Resident A"},
            "home_id": {"S": "home-0001"},
        },
    )


def seed(ddb) -> None:
    """res-0001: wearable-0001 and bedroom-0001, default thresholds (no item).
    res-0002: wearable-0002, CUSTOM_LIMITS. wearable-0003: a spare."""
    put_resident(ddb, "res-0001")
    put_device(ddb, "wearable-0001", resident_id="res-0001")
    put_device(ddb, "bedroom-0001", resident_id="res-0001")
    put_device(ddb, "wearable-0002", resident_id="res-0002")
    put_device(ddb, "wearable-0003", resident_id=None)
    put_thresholds(ddb, "res-0002", **CUSTOM_LIMITS)


# ---- Glue ----


class _EntityNotFoundError(ClientError):
    """What boto3's glue client raises as `exceptions.EntityNotFoundException`."""


class FakeGlue:
    """Glue as the handler uses it: GetPartition only (moto's Glue needs pyparsing,
    which the dev image doesn't have). Holds (database, table, partition date) and
    records each call in the shared `calls`."""

    class exceptions:
        EntityNotFoundException = _EntityNotFoundError

    def __init__(self, calls: list) -> None:
        self.calls = calls
        self.partitions: set[tuple[str, str, str]] = set()

    def get_partition(
        self, DatabaseName: str, TableName: str, PartitionValues: list[str]
    ) -> dict:
        self.calls.append(
            (
                "glue",
                "GetPartition",
                {
                    "DatabaseName": DatabaseName,
                    "TableName": TableName,
                    "PartitionValues": list(PartitionValues),
                },
            )
        )
        (value,) = PartitionValues
        if (DatabaseName, TableName, value) not in self.partitions:
            raise _EntityNotFoundError(
                {
                    "Error": {
                        "Code": "EntityNotFoundException",
                        "Message": "Cannot find partition.",
                    }
                },
                "GetPartition",
            )
        return {"Partition": {"Values": [value], "TableName": TableName}}


def add_clean_partitions(glue: FakeGlue, database: str, date: str) -> None:
    """Every clean partition the summary of Singapore date `date` reads."""
    for table, partition in sql.required_partitions(date):
        glue.partitions.add((database, table, partition))


# ---- Athena ----


def empty_row(resident_id: str) -> dict[str, str | None]:
    """A result row for a resident with no readings: statistics and bed_minutes null,
    counts 0 (what the query returns, as Athena's text)."""
    row: dict[str, str | None] = {}
    for name, _ in sql.RESULT_COLUMNS:
        if name == "resident_id":
            row[name] = resident_id
        elif name in NULL_WITHOUT_READINGS:
            row[name] = None
        else:
            row[name] = "0"
    return row


def reading_row(resident_id: str) -> dict[str, str | None]:
    return {"resident_id": resident_id, **READING_ROW}


def residents_in(query: str) -> list[str]:
    return sorted(set(re.findall(r"'(res-[0-9]{4})'", query)))


def _default_rows(query: str) -> list[dict[str, str | None]]:
    """One row per resident in the query, as the real query returns."""
    if not query.startswith("WITH devices AS"):
        return []
    return [empty_row(resident_id) for resident_id in residents_in(query)]


def step_of(query: str) -> str:
    if query.startswith("WITH devices AS"):
        return "summary"
    if query.startswith(f"ALTER TABLE {sql.SUMMARY_TABLE} "):
        return "add_partition"
    return "other"


class _Paginator:
    def __init__(self, athena: FakeAthena) -> None:
        self.athena = athena

    def paginate(self, QueryExecutionId: str, PaginationConfig: dict) -> list[dict]:
        self.athena.result_requests.append(QueryExecutionId)
        rows = self.athena.rows[self.athena.query_ids.index(QueryExecutionId)]
        header = list(rows[0]) if rows else [name for name, _ in sql.RESULT_COLUMNS]
        cells = [{"Data": [{"VarCharValue": name} for name in header]}]
        for row in rows:
            data = [
                {} if row[name] is None else {"VarCharValue": row[name]}
                for name in header
            ]
            cells.append({"Data": data})
        size = self.athena.page_size or len(cells)
        return [
            {"ResultSet": {"Rows": cells[start : start + size]}}
            for start in range(0, len(cells), size)
        ]


class FakeAthena:
    """Records queries (and each start in the shared `calls`), returns a state per
    query, and serves result rows. By default the summary query returns one empty row
    per resident it names; set `rows_for` to change that."""

    def __init__(self, calls: list) -> None:
        self.calls = calls
        self.queries: list[str] = []
        self.query_ids: list[str] = []
        self.requests: list[dict] = []
        self.rows: list[list[dict]] = []
        self.result_requests: list[str] = []
        self.state_for = lambda query: "SUCCEEDED"
        self.rows_for = _default_rows
        self.running_polls = 0
        self.page_size: int | None = None
        self.start_error: Exception | None = None
        self._polls: dict[str, int] = {}

    def start_query_execution(self, **kwargs: Any) -> dict:
        query = kwargs["QueryString"]
        self.calls.append(("athena", "StartQueryExecution", {"step": step_of(query)}))
        if self.start_error:
            raise self.start_error
        query_id = f"query-test-{len(self.queries):04d}"
        self.requests.append(kwargs)
        self.queries.append(query)
        self.query_ids.append(query_id)
        self.rows.append(self.rows_for(query))
        self._polls[query_id] = 0
        return {"QueryExecutionId": query_id}

    def get_query_execution(self, QueryExecutionId: str) -> dict:
        self._polls[QueryExecutionId] += 1
        query = self.queries[self.query_ids.index(QueryExecutionId)]
        if self._polls[QueryExecutionId] <= self.running_polls:
            state = "RUNNING"
        else:
            state = self.state_for(query)
        return {
            "QueryExecution": {
                "Status": {"State": state, "StateChangeReason": LEAKY_REASON},
                "Statistics": {"DataScannedInBytes": 2048},
            }
        }

    def get_paginator(self, name: str) -> _Paginator:
        assert name == "get_query_results"
        return _Paginator(self)

    def steps(self) -> list[str]:
        return [step_of(query) for query in self.queries]
