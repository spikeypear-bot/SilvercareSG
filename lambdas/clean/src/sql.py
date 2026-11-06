"""Athena SQL for the clean job (docs/features/clean-data.md). Pure strings, no AWS.

`tables.json` lists each clean table's fields. Terraform reads the same file for the
Glue columns, so the Parquet output and the table definitions can't drift apart.

How a line is checked: every top-level JSON value becomes its serialized token
(`72`, `72.0`, `"72"`, `true`, `null`, `{...}`), so a regex on the token tells apart what
Python's types tell apart in `lambdas/ingest/src/validation.py`. A missing key has no
token (SQL NULL). The WHEN branches of `_reason` follow validate_event's order, so a line
with several problems normally gets the same reason as ingest. Accelerometer checks are
not reasons: they only set `accel_valid`, and accelerometer columns hold only known
fields rebuilt from a valid value, otherwise NULL.

A line under `unknown` whose `message` names a contract type is processed as that type
(`message_type`): history filed vitals_alert and alert_cancel there before it allowed
underscores. Each clean table reads its own partition and `unknown`, so de-duplication
covers both. `rejected` still shows the raw partition.

Queries nest subqueries rather than use WITH, because the UNLOAD syntax AWS documents is
`UNLOAD (SELECT ...)`. Regexes use \\A...\\z: in Trino, `$` also matches before a final
newline, which Python's \\Z does not. Every predicate goes through COALESCE(..., FALSE)
because a NULL WHEN condition would skip the branch and let a bad line through.

The only text that reaches the SQL is the date (strictly validated by the handler),
constants, and the synthetic lines of cases.json, which are quoted by `_quote`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TABLES: dict[str, dict[str, Any]] = json.loads(
    (Path(__file__).parent / "tables.json").read_text(encoding="utf-8")
)["tables"]
REJECTED_TABLE = "rejected"
OUTPUT_TABLES = (*TABLES, REJECTED_TABLE)
RAW_TABLE = "raw_telemetry"
UNKNOWN_MESSAGE = "unknown"
UNPARSEABLE_MESSAGE = "unparseable"
# The seven types the IoT rules pass.
CONTRACT_MESSAGES = tuple(
    dict.fromkeys(table["raw_message"] for table in TABLES.values())
)
# Plus the two the history Lambda produces.
RAW_MESSAGES = (*CONTRACT_MESSAGES, UNKNOWN_MESSAGE, UNPARSEABLE_MESSAGE)
TABLE_PREFIX = "tables"
ENVELOPE_COLUMNS = (
    "message_id",
    "device_id",
    "ts",
    "received_at",
    "schema_version",
    "clock_skew",
)

_INT = r"\A-?[0-9]+\z"
_NUMBER = r"\A-?[0-9]"
_STRING = r'\A"'
_DEVICE_ID = r'\A"(wearable|bedroom)-[0-9]{4}"\z'
_DEVICE_TYPE = r'\A"(wearable|bedroom)-'
_UUID_V4 = (
    r'\A"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}'
    r'-[0-9a-fA-F]{12}"\z'
)
_TS = r'\A"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z"\z'
_TS_FORMAT = "%Y-%m-%dT%H:%i:%s.%fZ"
_CLOCK_SKEW_MS = 300_000
_ACCEL_AXIS_LIMIT = 16
_ACCEL_AXES = ("x", "y", "z")
_FALL_WINDOW_HZ = 25
_FALL_WINDOW_START_OFFSET_MS = -1000
_FALL_WINDOW_SAMPLES = 50
_ENVELOPE_AND_RULE_FIELDS = (
    "schema_version",
    "message_id",
    "ts",
    "device_id",
    "message",
    "received_at",
)
_TYPE_BY_KIND = {"int": "integer", "number": "double"}


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _token(name: str) -> str:
    return f"element_at(tokens, {_quote(name)})"


def _string_value(name: str) -> str:
    return f"try_cast(element_at(fields, {_quote(name)}) AS varchar)"


def _when_not(condition: str, reason: str, kind: str | None = None) -> str:
    scope = f"kind = {_quote(kind)} AND " if kind else ""
    return f"WHEN {scope}NOT COALESCE({condition}, FALSE) THEN {_quote(reason)}"


def _range(token: str, low: float, high: float) -> str:
    return f"try_cast({token} AS double) BETWEEN {low} AND {high}"


def _field_checks(kind: str, field: dict[str, Any]) -> list[str]:
    """Mirror validation._check_field: missing, then type, then range or length."""
    name = field["name"]
    token = _token(name)
    # A nullable field's JSON null passes the type and range checks.
    allow_null = f"{token} = 'null' OR " if field.get("nullable") else ""
    type_check = {
        "int": f"regexp_like({token}, {_quote(_INT)})",
        "number": f"regexp_like({token}, {_quote(_NUMBER)})",
        "bool": f"{token} IN ('true', 'false')",
        "str": f"regexp_like({token}, {_quote(_STRING)})",
    }[field["kind"]]
    checks = [
        _when_not(f"{token} IS NOT NULL", f"missing:{name}", kind),
        _when_not(f"{allow_null}{type_check}", f"wrong_type:{name}", kind),
    ]
    if "min" in field:
        in_range = _range(token, field["min"], field["max"])
        checks.append(
            _when_not(f"{allow_null}{in_range}", f"out_of_range:{name}", kind)
        )
    if "max_len" in field:
        length = f"length({_string_value(name)})"
        checks.append(
            _when_not(
                f"{length} BETWEEN 1 AND {field['max_len']}",
                f"out_of_range:{name}",
                kind,
            )
        )
    return checks


def _vitals_alert_checks() -> list[str]:
    """Mirror validation._check_vitals_alert, in the same order."""
    metric, direction, limit = map(_token, ("metric", "direction", "limit"))
    value = f"try_cast({_token('value')} AS double)"
    is_hr = f"""{metric} = '"hr_bpm"'"""
    checks = [
        (f"""{metric} IN ('"hr_bpm"', '"spo2_pct"')""", "invalid:metric"),
        (f"""{direction} IN ('"above"', '"below"')""", "invalid:direction"),
        (
            (
                f"({is_hr} AND ({value} = 0 OR {value} BETWEEN 20 AND 250))"
                f" OR (NOT {is_hr} AND {value} BETWEEN 50 AND 100)"
            ),
            "out_of_range:value",
        ),
        (
            (
                f"({is_hr} AND {limit} IN ('45', '130'))"
                f" OR (NOT {is_hr} AND {limit} = '88')"
            ),
            "invalid:limit",
        ),
        (f"""{is_hr} OR {direction} = '"below"'""", "invalid:direction"),
        (f"""{value} <> 0 OR {direction} = '"below"'""", "invalid:direction"),
    ]
    return [_when_not(check, reason, "vitals_alert") for check, reason in checks]


def _alert_cancel_checks() -> list[str]:
    """Mirror validation._check_envelope_only."""
    allowed = ", ".join(map(_quote, _ENVELOPE_AND_RULE_FIELDS))
    extras = f"cardinality(array_except(map_keys(fields), ARRAY[{allowed}]))"
    return [_when_not(f"{extras} = 0", "unknown_field", "alert_cancel")]


_EXTRA_CHECKS = {
    "vitals_alert": _vitals_alert_checks,
    "alert_cancel": _alert_cancel_checks,
}


def _reason() -> str:
    """The rejection reason of one line, or NULL if it is valid for its table."""
    version, message_id = _token("schema_version"), _token("message_id")
    envelope = [
        f"WHEN raw_message = {_quote(UNPARSEABLE_MESSAGE)} THEN 'unparseable'",
        f"WHEN message_type = {_quote(UNKNOWN_MESSAGE)} THEN 'unknown_message'",
        "WHEN fields IS NULL THEN 'invalid:event'",
        _when_not(
            f"regexp_like({_token('device_id')}, {_quote(_DEVICE_ID)})",
            "invalid:device_id",
        ),
        # kind is NULL for a type this device can't send (validation.ALLOWED_MESSAGES).
        _when_not(
            f"""kind IS NOT NULL AND {_token("message")} = concat('"', message_type, '"')""",
            "invalid:message",
        ),
        _when_not(f"{version} IS NOT NULL", "missing:schema_version"),
        _when_not(f"{version} = '1'", "invalid:schema_version"),
        _when_not(f"{message_id} IS NOT NULL", "missing:message_id"),
        _when_not(
            f"regexp_like({message_id}, {_quote(_UUID_V4)})", "invalid:message_id"
        ),
        _when_not(f"{_token('ts')} IS NOT NULL", "missing:ts"),
        _when_not("ts_value IS NOT NULL", "invalid:ts"),
        _when_not("received_value IS NOT NULL", "invalid:received_at"),
    ]
    per_table = [
        check
        for name, table in TABLES.items()
        for field in table["fields"]
        for check in _field_checks(name, field)
    ]
    extras = [check for build in _EXTRA_CHECKS.values() for check in build()]
    return "CASE\n" + "\n".join((*envelope, *per_table, *extras)) + "\nELSE NULL END"


def _kind() -> str:
    """The clean table a line belongs to, from its message type and device type."""
    branches = [
        f"WHEN message_type = {_quote(table['raw_message'])}"
        f" AND device_type = {_quote(table['device'])} THEN {_quote(name)}"
        for name, table in TABLES.items()
    ]
    return "CASE " + " ".join(branches) + " END"


def _message_type() -> str:
    """The raw partition, except that a line under `unknown` can name a contract type."""
    contract = ", ".join(_quote(f'"{name}"') for name in CONTRACT_MESSAGES)
    return (
        f"CASE WHEN raw_message = {_quote(UNKNOWN_MESSAGE)}"
        f" AND {_token('message')} IN ({contract})"
        f" THEN {_string_value('message')} ELSE raw_message END"
    )


def _validated(source: str) -> str:
    """Every source line with fields, tokens, kind, parsed times, and reason."""
    ts = _token("ts")
    received = _token("received_at")
    ts_value = (
        f"CASE WHEN regexp_like({ts}, {_quote(_TS)}) AND substr({ts}, 2, 4) <> '0000'"
        f" THEN try(date_parse(substr({ts}, 2, 24), {_quote(_TS_FORMAT)})) END"
    )
    received_value = (
        f"CASE WHEN regexp_like({received}, {_quote(_INT)})"
        f" AND try_cast({received} AS bigint) >= 0"
        f" THEN try(date_add('millisecond', try_cast({received} AS bigint),"
        " TIMESTAMP '1970-01-01 00:00:00.000')) END"
    )
    parsed = (
        "SELECT line, raw_message,"
        " try(CAST(try(json_parse(line)) AS map(varchar, json))) AS fields"
        f" FROM ({source})"
    )
    tokenized = (
        "SELECT line, raw_message, fields,"
        " transform_values(fields, (k, v) -> COALESCE(json_format(v), 'null')) AS tokens"
        f" FROM ({parsed})"
    )
    typed = (
        "SELECT line, raw_message, fields, tokens,"
        f" regexp_extract({_token('device_id')}, {_quote(_DEVICE_TYPE)}, 1) AS device_type,"
        f" {ts_value} AS ts_value, {received_value} AS received_value,"
        f" {_message_type()} AS message_type"
        f" FROM ({tokenized})"
    )
    kinded = f"SELECT *, {_kind()} AS kind FROM ({typed})"
    return f"SELECT *, {_reason()} AS reason FROM ({kinded})"


def _accel_part(accel: str, key: str) -> str:
    """One key of the accelerometer object, as JSON. NULL if it isn't an object."""
    inner = f"try_cast(element_at(fields, {_quote(accel)}) AS map(varchar, json))"
    return f"element_at({inner}, {_quote(key)})"


def _is_axis(value: str) -> str:
    """A JSON value is a number from -16 to 16 (the regex rules out booleans, strings)."""
    token = f"json_format({value})"
    return f"regexp_like({token}, {_quote(_NUMBER)}) AND " + _range(
        token, -_ACCEL_AXIS_LIMIT, _ACCEL_AXIS_LIMIT
    )


def _accel_valid(accel: str) -> str:
    """accel_g: x, y, z in range. accel_window: hz, offset, 50 samples of 3 in range."""
    if accel == "accel_g":
        axes = [_is_axis(_accel_part(accel, axis)) for axis in _ACCEL_AXES]
        return "COALESCE(" + " AND ".join(axes) + ", FALSE)"
    samples = f"try_cast({_accel_part(accel, 'samples')} AS array(json))"
    sample = "try_cast(s AS array(json))"
    # all_match gives NULL, not FALSE, on a JSON null: COALESCE turns that into FALSE.
    return (
        f"COALESCE(json_format({_accel_part(accel, 'hz')}) = '{_FALL_WINDOW_HZ}'"
        f" AND json_format({_accel_part(accel, 'start_offset_ms')})"
        f" = '{_FALL_WINDOW_START_OFFSET_MS}'"
        f" AND cardinality({samples}) = {_FALL_WINDOW_SAMPLES}"
        f" AND all_match({samples}, s -> cardinality({sample}) = 3"
        f" AND all_match({sample}, v -> {_is_axis('v')})), FALSE)"
    )


def _accel_json(accel: str) -> str:
    """Known fields only. Used once _accel_valid has checked them, so samples are safe."""
    if accel == "accel_g":
        x, y, z = (f"json_format({_accel_part(accel, axis)})" for axis in _ACCEL_AXES)
        return f"""concat('{{"x":', {x}, ',"y":', {y}, ',"z":', {z}, '}}')"""
    samples = f"json_format({_accel_part(accel, 'samples')})"
    return (
        f"""concat('{{"hz":{_FALL_WINDOW_HZ},"""
        f""""start_offset_ms":{_FALL_WINDOW_START_OFFSET_MS},"samples":', {samples}, '}}')"""
    )


def _field_column(field: dict[str, Any]) -> str:
    name = field["name"]
    token = _token(name)
    if field["kind"] == "bool":
        value = f"{token} = 'true'"
    elif field["kind"] == "str":
        value = _string_value(name)
    else:
        value = f"try_cast({token} AS {_TYPE_BY_KIND[field['kind']]})"
    return f'{value} AS "{name}"'


def columns(table: str) -> list[str]:
    """Output column names of one clean table, in order."""
    spec = TABLES[table]
    accel = [spec["accel"], "accel_valid"] if spec["accel"] else []
    return [*ENVELOPE_COLUMNS, *(f["name"] for f in spec["fields"]), *accel]


def clean_query(table: str, source: str) -> str:
    """Valid lines of one table, keeping the earliest received per message_id."""
    spec = TABLES[table]
    typed = [
        f"{_string_value('message_id')} AS message_id",
        f"{_string_value('device_id')} AS device_id",
        "ts_value AS ts",
        "received_value AS received_at",
        f"try_cast({_token('schema_version')} AS integer) AS schema_version",
        (
            f"abs(date_diff('millisecond', ts_value, received_value)) > {_CLOCK_SKEW_MS}"
            " AS clock_skew"
        ),
        *map(_field_column, spec["fields"]),
    ]
    if accel := spec["accel"]:
        valid_accel = _accel_valid(accel)
        typed += [
            f"CASE WHEN {valid_accel} THEN {_accel_json(accel)} END AS {accel}",
            f"{valid_accel} AS accel_valid",
        ]
    names = ", ".join(f'"{name}"' for name in columns(table))
    select = ", ".join(typed)
    valid = (
        f"SELECT {select} FROM ({_validated(source)})"
        f" WHERE kind = {_quote(table)} AND reason IS NULL"
    )
    ranked = (
        f"SELECT {names}, row_number() OVER"
        " (PARTITION BY message_id ORDER BY received_at) AS row_num"
        f" FROM ({valid})"
    )
    return f"SELECT {names} FROM ({ranked}) WHERE row_num = 1"


def rejected_query(source: str) -> str:
    """Every line that isn't valid, with its reason. Exact duplicate lines once, even
    if one copy is under `unknown` and the other under the type's own partition."""
    return (
        'SELECT min(raw_message) AS "message", reason, line AS raw_line'
        f" FROM ({_validated(source)}) WHERE reason IS NOT NULL"
        " GROUP BY reason, line"
    )


def output_query(table: str, source: str) -> str:
    if table == REJECTED_TABLE:
        return rejected_query(source)
    return clean_query(table, source)


def raw_source(date: str, table: str) -> str:
    """Raw lines of one date, limited to the partitions the table needs so Athena scans
    less: a clean table reads its own and `unknown`."""
    where = f'"date" = {_quote(date)}'
    if table != REJECTED_TABLE:
        own = _quote(TABLES[table]["raw_message"])
        where += f" AND message IN ({own}, {_quote(UNKNOWN_MESSAGE)})"
    return f"SELECT line, message AS raw_message FROM {RAW_TABLE} WHERE {where}"


def cases_source(cases: list[dict[str, Any]]) -> str:
    """The self-check's cases as inline VALUES: reads no table and no bucket."""
    rows = ", ".join(
        f"({_quote(case['line'])}, {_quote(case['raw_message'])})" for case in cases
    )
    return f"SELECT line, raw_message FROM (VALUES {rows}) AS cases(line, raw_message)"


def output_prefix(table: str, date: str) -> str:
    """Key prefix of one table's output for one date, with the trailing slash."""
    return f"{TABLE_PREFIX}/{table}/date={date}/"


def unload(table: str, date: str, bucket: str) -> str:
    target = f"s3://{bucket}/{output_prefix(table, date)}"
    query = output_query(table, raw_source(date, table))
    return (
        f"UNLOAD ({query}) TO {_quote(target)}"
        " WITH (format = 'PARQUET', compression = 'SNAPPY')"
    )


def add_raw_partitions(date: str) -> str:
    partitions = " ".join(
        f"PARTITION (message = {_quote(message)}, `date` = {_quote(date)})"
        for message in RAW_MESSAGES
    )
    return f"ALTER TABLE {RAW_TABLE} ADD IF NOT EXISTS {partitions}"


def add_output_partition(table: str, date: str) -> str:
    return f"ALTER TABLE {table} ADD IF NOT EXISTS PARTITION (`date` = {_quote(date)})"
