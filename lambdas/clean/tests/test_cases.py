"""cases.json against ingest's real validate_event (criteria 11, 12, 15, 16)."""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import handler
import pytest
import sql
from helpers import (
    CASES,
    VALIDATION_PATH,
    case_by_id,
    ingest_outcome,
    ingest_result,
    load_validation,
)

CASE_IDS = [case["id"] for case in CASES]
BY_PARTITION_ONLY = {sql.UNKNOWN_MESSAGE, sql.UNPARSEABLE_MESSAGE}
KEPT = [case for case in CASES if "expected_values" in case]
ACCEL_ONLY_REASONS = {
    "missing:accel_g",
    "invalid:accel_g",
    "missing:accel_window",
    "invalid:accel_window",
}
ACCEL_KNOWN_FIELDS = {
    "accel_g": ("x", "y", "z"),
    "accel_window": ("hz", "start_offset_ms", "samples"),
}


def parsed(case: dict) -> dict:
    return json.loads(case["line"])


def is_object_line(case: dict) -> bool:
    try:
        return isinstance(json.loads(case["line"]), dict)
    except json.JSONDecodeError:
        return False


def names_contract_type(case: dict) -> bool:
    """A line under `unknown` recovered by criterion 6b."""
    return (
        case["raw_message"] == sql.UNKNOWN_MESSAGE
        and is_object_line(case)
        and parsed(case).get("message") in sql.CONTRACT_MESSAGES
    )


def skew_ms(line: dict) -> int:
    ts = datetime.strptime(line["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    return abs(line["received_at"] - int(ts.timestamp() * 1000))


# ---- criterion 16: the link to ingest ----


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_case_expected_ingest_matches_validate_event(case):
    assert ingest_outcome(case) == case["expected_ingest"]


def test_validate_event_comes_from_the_ingest_source_file():
    validation = load_validation()
    assert Path(validation.__file__).resolve() == VALIDATION_PATH
    assert VALIDATION_PATH.parts[-3:] == ("ingest", "src", "validation.py")


def test_loading_ingest_validation_leaves_clean_handler_in_place():
    load_validation()
    assert Path(handler.__file__).resolve().parent.parent.name == "clean"
    assert str(VALIDATION_PATH.parent) not in sys.path
    assert "validation" not in sys.modules


def test_ingest_rule_change_breaks_the_comparison(monkeypatch):
    """The point of criterion 16: tightening a rule in ingest shows up as a mismatch."""
    validation = load_validation()
    field = validation.Field("int", nullable=True, minimum=20, maximum=71)
    monkeypatch.setitem(
        validation.STORED_FIELDS[("wearable", "vitals")], "hr_bpm", field
    )
    assert ingest_outcome(case_by_id("vitals_valid")) != "valid"


# ---- criterion 11: clean accepts what ingest accepts, ignoring accelerometer checks ----


@pytest.fixture
def ingest_without_accel_checks(monkeypatch):
    extra_checks = load_validation().EXTRA_CHECKS
    monkeypatch.delitem(extra_checks, ("wearable", "vitals"))
    monkeypatch.delitem(extra_checks, ("wearable", "fall"))


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_expected_clean_is_ingest_without_accel_checks(
    case, ingest_without_accel_checks
):
    if case["raw_message"] in BY_PARTITION_ONLY and not names_contract_type(case):
        assert case["expected_clean"] == "rejected"
    else:
        assert case["expected_clean"] == ingest_outcome(case)


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_outcomes_differ_only_for_accelerometer_failures(case):
    if case["expected_ingest"] == case["expected_clean"]:
        return
    assert (case["expected_ingest"], case["expected_clean"]) == ("rejected", "valid")
    assert ingest_result(case).reason in ACCEL_ONLY_REASONS


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c["expected_clean"] == "rejected"],
    ids=lambda c: c["id"],
)
def test_expected_reason_equals_ingest_or_the_partition(case):
    if names_contract_type(case):
        assert case["expected_reason"] == ingest_result(case).reason
    elif case["raw_message"] == sql.UNKNOWN_MESSAGE:
        assert case["expected_reason"] == "unknown_message"
    elif case["raw_message"] == sql.UNPARSEABLE_MESSAGE:
        assert case["expected_reason"] == "unparseable"
    else:
        assert case["expected_reason"] == ingest_result(case).reason


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_outcome_has_exactly_the_fields_it_needs(case):
    if case["expected_clean"] == "rejected":
        assert "expected_reason" in case
        assert "expected_table" not in case
        return
    assert case["expected_table"] in sql.TABLES
    assert ("expected_values" in case) != case.get("dropped_as_duplicate", False)


# ---- expected values, worked out from the line itself ----


def _ms_text(ms: int) -> str:
    moment = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=ms)
    return moment.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _is_axis(value) -> bool:
    return type(value) in (int, float) and -16 <= value <= 16


def _accel_g_valid(accel) -> bool:
    return isinstance(accel, dict) and all(
        _is_axis(accel.get(axis)) for axis in ACCEL_KNOWN_FIELDS["accel_g"]
    )


def _accel_window_valid(window) -> bool:
    return (
        isinstance(window, dict)
        and type(window.get("hz")) is int
        and window["hz"] == 25
        and type(window.get("start_offset_ms")) is int
        and window["start_offset_ms"] == -1000
        and isinstance(window.get("samples"), list)
        and len(window["samples"]) == 50
        and all(
            isinstance(sample, list)
            and len(sample) == 3
            and all(_is_axis(value) for value in sample)
            for sample in window["samples"]
        )
    )


@pytest.mark.parametrize("case", KEPT, ids=lambda c: c["id"])
def test_expected_values_have_every_column_of_the_table(case):
    assert set(case["expected_values"]) == set(sql.columns(case["expected_table"]))


@pytest.mark.parametrize("case", KEPT, ids=lambda c: c["id"])
def test_expected_values_follow_the_line(case):
    line, values = parsed(case), case["expected_values"]
    assert values["message_id"] == line["message_id"]
    assert values["device_id"] == line["device_id"]
    assert values["schema_version"] == line["schema_version"]
    assert values["ts"] == line["ts"].replace("T", " ").removesuffix("Z")
    assert values["received_at"] == _ms_text(line["received_at"])
    for field in sql.TABLES[case["expected_table"]]["fields"]:
        assert values[field["name"]] == line[field["name"]]


@pytest.mark.parametrize("case", KEPT, ids=lambda c: c["id"])
def test_expected_clock_skew_is_over_five_minutes(case):
    skewed = skew_ms(parsed(case)) > 300_000
    assert case["expected_values"]["clock_skew"] is skewed


@pytest.mark.parametrize("case", KEPT, ids=lambda c: c["id"])
def test_expected_accel_columns_follow_criterion_9(case):
    accel_column = sql.TABLES[case["expected_table"]]["accel"]
    if not accel_column:
        return
    value = parsed(case).get(accel_column)
    is_valid = _accel_g_valid if accel_column == "accel_g" else _accel_window_valid
    valid = is_valid(value)
    rebuilt = (
        {key: value[key] for key in ACCEL_KNOWN_FIELDS[accel_column]} if valid else None
    )
    assert case["expected_values"][accel_column] == rebuilt
    assert case["expected_values"]["accel_valid"] is valid


@pytest.mark.parametrize(
    "case",
    [c for c in KEPT if sql.TABLES[c["expected_table"]]["accel"]],
    ids=lambda c: c["id"],
)
def test_expected_accel_valid_agrees_with_ingests_accel_check(case):
    validation = load_validation()
    check = validation.EXTRA_CHECKS[("wearable", parsed(case)["message"])]
    assert case["expected_values"]["accel_valid"] is (check(parsed(case)) is None)


@pytest.mark.parametrize(
    "case", [c for c in CASES if "expected_table" in c], ids=lambda c: c["id"]
)
def test_expected_table_follows_partition_and_device_type(case):
    spec = sql.TABLES[case["expected_table"]]
    line = parsed(case)
    recovered = case["raw_message"] == sql.UNKNOWN_MESSAGE
    assert spec["raw_message"] == (
        line["message"] if recovered else case["raw_message"]
    )
    assert line["device_id"].startswith(spec["device"] + "-")


# ---- criterion 12: which copy of a duplicate is kept ----


def test_kept_copy_of_each_duplicate_has_the_earliest_received_at():
    groups = defaultdict(list)
    for case in CASES:
        if "expected_table" in case:
            key = (case["expected_table"], parsed(case)["message_id"])
            groups[key].append(case)
    duplicated = [group for group in groups.values() if len(group) > 1]
    assert duplicated
    for group in duplicated:
        kept = [case for case in group if "expected_values" in case]
        assert len(kept) == 1
        earliest = min(parsed(case)["received_at"] for case in group)
        assert parsed(kept[0])["received_at"] == earliest


def test_cases_include_a_duplicate_with_a_later_received_at():
    earliest = parsed(case_by_id("duplicate_earliest_copy"))
    later = parsed(case_by_id("duplicate_later_copy"))
    assert later["message_id"] == earliest["message_id"]
    assert later["received_at"] > earliest["received_at"]


def test_exact_duplicate_rejected_lines_are_both_in_the_cases():
    first = case_by_id("rejected_exact_duplicate_1")
    second = case_by_id("rejected_exact_duplicate_2")
    assert first["line"] == second["line"]
    assert first["expected_reason"] == second["expected_reason"]


@pytest.mark.parametrize(
    "earliest,later",
    [
        ("across_partitions_own_first_earliest", "across_partitions_own_first_later"),
        (
            "across_partitions_recovered_first_earliest",
            "across_partitions_recovered_first_later",
        ),
    ],
)
def test_duplicate_across_own_and_unknown_partitions_keeps_one_row(earliest, later):
    kept, dropped = case_by_id(earliest), case_by_id(later)
    assert parsed(kept)["message_id"] == parsed(dropped)["message_id"]
    assert kept["expected_table"] == dropped["expected_table"]
    assert {kept["raw_message"], dropped["raw_message"]} == {
        sql.UNKNOWN_MESSAGE,
        kept["expected_table"],
    }
    assert parsed(kept)["received_at"] < parsed(dropped)["received_at"]
    assert "expected_values" in kept
    assert dropped["dropped_as_duplicate"] is True


def test_rejected_line_in_own_and_unknown_partitions_is_one_case_kept():
    kept = case_by_id("across_partitions_rejected_kept")
    dropped = case_by_id("across_partitions_rejected_dropped")
    assert kept["line"] == dropped["line"]
    assert kept["expected_reason"] == dropped["expected_reason"]
    assert (kept["raw_message"], dropped["raw_message"]) == (
        "alert_cancel",
        sql.UNKNOWN_MESSAGE,
    )
    assert "dropped_as_duplicate" not in kept
    assert dropped["dropped_as_duplicate"] is True


# ---- criterion 6b: lines under `unknown` ----


@pytest.mark.parametrize("table", sql.TABLES)
def test_every_table_has_a_recovered_valid_case(table):
    assert any(
        names_contract_type(case) and case["expected_table"] == table for case in KEPT
    )


@pytest.mark.parametrize(
    "case_id,reason",
    [
        ("recovered_bad_ts", "invalid:ts"),
        ("recovered_wrong_device", "invalid:message"),
        ("recovered_alert_cancel_extra_field", "unknown_field"),
    ],
)
def test_recovered_invalid_line_is_rejected_for_its_own_problem(case_id, reason):
    case = case_by_id(case_id)
    assert names_contract_type(case)
    assert case["expected_clean"] == "rejected"
    assert case["expected_reason"] == reason


@pytest.mark.parametrize(
    "case_id",
    [
        "unknown_partition",
        "unknown_name_not_in_contract",
        "unknown_message_not_a_string",
        "unknown_message_missing",
    ],
)
def test_unknown_line_not_naming_a_contract_type_is_unknown_message(case_id):
    case = case_by_id(case_id)
    assert case["raw_message"] == sql.UNKNOWN_MESSAGE
    assert not names_contract_type(case)
    assert case["expected_reason"] == "unknown_message"


def test_line_in_own_partition_naming_another_type_is_still_rejected():
    case = case_by_id("own_partition_names_another_type")
    assert case["raw_message"] == "vitals"
    assert parsed(case)["message"] == "motion"
    assert case["expected_reason"] == "invalid:message"
    assert "expected_table" not in case


# ---- criterion 9: accelerometer columns ----


@pytest.mark.parametrize(
    "case_id,column",
    [
        ("accel_g_extra_nested_key", "accel_g"),
        ("accel_window_extra_nested_key", "accel_window"),
    ],
)
def test_extra_nested_accel_key_is_never_in_the_expected_value(case_id, column):
    case = case_by_id(case_id)
    assert "note" in parsed(case)[column]
    assert case["expected_values"]["accel_valid"] is True
    assert set(case["expected_values"][column]) == set(ACCEL_KNOWN_FIELDS[column])


def test_fall_with_bad_accel_stays_in_the_clean_table_with_null_accel():
    case = case_by_id("accel_window_wrong_hz")
    assert case["expected_ingest"] == "rejected"
    assert case["expected_table"] == "fall"
    assert case["expected_values"]["accel_window"] is None
    assert case["expected_values"]["accel_valid"] is False


# ---- criterion 15: what the cases must cover ----


@pytest.mark.parametrize("table", sql.TABLES)
def test_every_table_has_a_kept_valid_case(table):
    assert any(case["expected_table"] == table for case in KEPT)


@pytest.mark.parametrize("table", sql.TABLES)
def test_every_table_has_a_rejected_case(table):
    spec = sql.TABLES[table]
    assert any(
        case["expected_clean"] == "rejected"
        and case["raw_message"] == spec["raw_message"]
        and is_object_line(case)
        and str(parsed(case).get("device_id")).startswith(spec["device"])
        for case in CASES
    )


@pytest.mark.parametrize("message", sql.RAW_MESSAGES)
def test_every_raw_partition_has_a_case(message):
    assert any(case["raw_message"] == message for case in CASES)


@pytest.mark.parametrize(
    "reason",
    [
        "invalid:event",
        "invalid:device_id",
        "invalid:message",
        "missing:schema_version",
        "invalid:schema_version",
        "missing:message_id",
        "invalid:message_id",
        "missing:ts",
        "invalid:ts",
        "invalid:received_at",
        "invalid:metric",
        "invalid:direction",
        "invalid:limit",
        "out_of_range:value",
        "unknown_field",
    ],
)
def test_every_envelope_and_alert_rule_has_a_rejected_case(reason):
    assert any(case.get("expected_reason") == reason for case in CASES)


@pytest.mark.parametrize("family", ["missing:", "wrong_type:", "out_of_range:"])
def test_every_field_rule_family_has_a_rejected_case(family):
    assert any(case.get("expected_reason", "").startswith(family) for case in CASES)


@pytest.mark.parametrize("field", ["hr_bpm", "spo2_pct", "skin_temp_c"])
def test_null_reading_is_kept_in_vitals(field):
    assert any(
        case["expected_table"] == "vitals" and parsed(case)[field] is None
        for case in KEPT
    )


def test_null_battery_is_kept_in_bedroom_status():
    assert any(
        case["expected_table"] == "bedroom_status"
        and parsed(case)["battery_pct"] is None
        for case in KEPT
    )


def test_no_pulse_emergency_reading_is_kept():
    assert any(
        case["expected_table"] == "vitals_alert"
        and parsed(case)["metric"] == "hr_bpm"
        and parsed(case)["value"] == 0
        for case in KEPT
    )


def _wrong_type_fields(python_type: type) -> set[str]:
    """Fields rejected as wrong_type in a case where they hold this JSON type."""
    found = set()
    for case in CASES:
        reason = case.get("expected_reason", "")
        if reason.startswith("wrong_type:") and is_object_line(case):
            field = reason.split(":")[1]
            if type(parsed(case).get(field)) is python_type:
                found.add(field)
    return found


def test_cases_cover_a_number_sent_as_a_string():
    assert _wrong_type_fields(str) & {"hr_bpm", "value", "skin_temp_c", "activity_pct"}


def test_cases_cover_a_boolean_in_a_number_field():
    assert _wrong_type_fields(bool) & {"hr_bpm", "value", "skin_temp_c", "confidence"}


def test_cases_cover_an_integer_field_sent_as_a_float():
    assert _wrong_type_fields(float)


@pytest.mark.parametrize("accel_column", ["accel_g", "accel_window"])
def test_malformed_accelerometer_data_stays_marked_invalid(accel_column):
    assert any(
        accel_column in case["expected_values"]
        and case["expected_values"]["accel_valid"] is False
        for case in KEPT
    )


def test_alert_cancel_with_an_extra_field_is_rejected():
    assert any(
        case.get("expected_reason") == "unknown_field"
        and parsed(case)["message"] == "alert_cancel"
        for case in CASES
    )


@pytest.mark.parametrize("gap_ms", [300_000, 300_001])
def test_clock_skew_boundary_has_a_case(gap_ms):
    assert any(skew_ms(parsed(case)) == gap_ms for case in KEPT)


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_case_ids_in_the_line_are_obviously_synthetic(case):
    if not is_object_line(case):
        return
    line = parsed(case)
    device_id, message_id = line.get("device_id"), line.get("message_id")
    validation = load_validation()
    if isinstance(device_id, str) and validation.DEVICE_ID_RE.match(device_id):
        assert re.search(r"-000[0-9]\Z", device_id)
    if isinstance(message_id, str) and validation.UUID_V4_RE.match(message_id):
        assert re.search(r"-0000-4000-8000-0{6}", message_id)


def test_case_ids_are_unique():
    assert len(CASE_IDS) == len(set(CASE_IDS))
