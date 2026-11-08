"""GET /dashboard through the handler, against moto (docs/data-model.md, Dashboard and
Resident status; patterns 9, 8, 5, 4)."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from decimal import Decimal

import handler
import pytest
from botocore.exceptions import ClientError
from conftest import ALERTS_TABLE
from events import (
    ACK_NOTE,
    ACTIVITY,
    ALERT_1,
    ALERT_2,
    ALERT_3,
    ALERT_4,
    ALERT_CREATED_AT,
    ALERT_RECEIVED_AT,
    BED_SENTINEL,
    BEDROOM,
    BEDROOM_2,
    CAREGIVER_2_EMAIL,
    CAREGIVER_EMAIL,
    CAREGIVER_NAME,
    CLOCK_SKEW_SENTINEL,
    DETAILS_SENTINEL,
    EMAIL,
    ESCALATES_AT,
    HOME_A,
    HOME_B,
    HOME_C,
    LATEST_RECEIVED_AT,
    MESSAGE_ID,
    MOTION_SENTINEL,
    NAME,
    NAME_A,
    NAME_B,
    NAME_C,
    NOTIFIED_AT,
    NOW,
    NURSE_SUB,
    OPS_SUB,
    PHOTO_A,
    PROFILE_NOTE,
    REQUEST_ID,
    RESIDENT_A,
    RESIDENT_B,
    RESIDENT_C,
    RESIDENT_NO_PROFILE,
    SOURCE_MESSAGE_ID,
    SPARE,
    SUB,
    UPDATED_BY_NAME,
    UPDATED_BY_SUB,
    WEARABLE,
    WEARABLE_2,
    WEARABLE_3,
    ago,
    alert_item,
    api_event,
    iso,
    put_alert,
    put_bed_and_motion,
    put_device,
    put_raw,
    put_resident,
    put_status,
    put_thresholds,
    put_vitals,
)

# Literal expected key sets (docs/data-model.md, Dashboard; frontend ApiDashboard*
# types). Deliberately not imported from dashboard.py.
TOP_KEYS = {"residents", "alerts"}
RESIDENT_KEYS = {
    "resident_id",
    "display_name",
    "home_id",
    "status",
    "status_text",
    "wearable",
    "bedroom",
    "vitals",
}
WEARABLE_KEYS = {"device_id", "ts", "battery_pct", "worn", "charging", "online"}
BEDROOM_KEYS = {"device_id", "ts", "online"}
# No activity_pct: the staff dashboards don't show it (data minimisation).
VITALS_KEYS = {"ts", "hr_bpm", "spo2_pct", "skin_temp_c"}
ALERT_KEYS = {
    "alert_id",
    "resident_id",
    "device_id",
    "alert_type",
    "severity",
    "status",
    "event_ts",
    "acknowledged_at",
    "acknowledged_by_role",
    "acknowledged_by",
    "ack_note",
    "escalated_at",
    "escalates_at",
}

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
POWERTOOLS_KEYS = {"level", "message", "service", "sampling_rate", "xray_trace_id"}
UNASSIGNED_DEVICE = "wearable-0042"
ESCALATED_AT = "2026-11-04T03:58:00.250Z"

URGENT = ("urgent", "Urgent: check now")
ATTENTION = ("attention", "Needs attention")
OK = ("ok", "All readings look normal")


def call(event=None, now=NOW):
    resp = handler.handle(api_event() if event is None else event, now)
    return resp["statusCode"], json.loads(resp["body"]), resp


def residents_by_id(body):
    return {r["resident_id"]: r for r in body["residents"]}


def alerts_by_id(body):
    return {a["alert_id"]: a for a in body["alerts"]}


def status_pair(body, resident_id=RESIDENT_A):
    resident = residents_by_id(body)[resident_id]
    return resident["status"], resident["status_text"]


def throttled(operation: str = "Query") -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "Synthetic throttle text qz-7781 for RESIDENT#res-0001",
            }
        },
        operation,
    )


def assert_exact_keys(body):
    assert set(body) == TOP_KEYS
    for resident in body["residents"]:
        assert set(resident) == RESIDENT_KEYS
        if resident["wearable"] is not None:
            assert set(resident["wearable"]) == WEARABLE_KEYS
        if resident["bedroom"] is not None:
            assert set(resident["bedroom"]) == BEDROOM_KEYS
        assert isinstance(resident["vitals"], list)
        assert len(resident["vitals"]) <= 1
        for entry in resident["vitals"]:
            assert set(entry) == VITALS_KEYS
    for alert in body["alerts"]:
        assert set(alert) == ALERT_KEYS


def seed_resident_a(aws) -> str:
    """Resident A with a reporting wearable (status + vitals) and bedroom unit, an open
    hr_high warning, and a battery_low a caregiver acknowledged with a note. Returns
    the caregiver's sub."""
    put_resident(aws.ddb)
    put_device(aws.ddb, WEARABLE)
    put_device(aws.ddb, BEDROOM)
    put_status(aws.ddb, WEARABLE)
    put_status(aws.ddb, BEDROOM)
    put_vitals(aws.ddb, WEARABLE)
    caregiver = aws.add_user(CAREGIVER_EMAIL, CAREGIVER_NAME)
    put_alert(aws.ddb, alert_id=ALERT_1, event_ts=ago(minutes=10))
    put_alert(
        aws.ddb,
        alert_id=ALERT_2,
        alert_type="battery_low",
        status="acknowledged",
        event_ts=ago(minutes=30),
        notified_at="2026-11-04T03:30:01.120Z",
        acknowledged_at=ago(minutes=20),
        acknowledged_by=caregiver,
        acknowledged_by_role="caregiver",
        ack_note=ACK_NOTE,
    )
    return caregiver


def seed_nulls(aws) -> None:
    """Resident B with no devices, resident C whose wearable never reported, and an
    unassigned critical fall already escalated."""
    put_resident(aws.ddb, RESIDENT_B, NAME_B, HOME_B)
    put_resident(aws.ddb, RESIDENT_C, NAME_C, HOME_C)
    put_device(aws.ddb, WEARABLE_3, RESIDENT_C, HOME_C)
    put_alert(
        aws.ddb,
        resident_id="unassigned",
        alert_id=ALERT_3,
        alert_type="fall",
        severity="critical",
        device_id=UNASSIGNED_DEVICE,
        event_ts=ago(minutes=2),
        notified_at=None,
        escalated_at=ESCALATED_AT,
    )


# ---- 1. exact response shape ----


def test_fully_populated_response_has_exactly_the_documented_keys(aws):
    seed_resident_a(aws)

    status, body, _ = call()

    assert status == 200
    assert_exact_keys(body)
    (resident,) = body["residents"]
    assert resident["wearable"] is not None
    assert resident["bedroom"] is not None
    assert len(resident["vitals"]) == 1
    assert all(v is not None for v in resident["wearable"].values())
    assert all(v is not None for v in resident["vitals"][0].values())
    assert len(body["alerts"]) == 2
    acknowledged = alerts_by_id(body)[ALERT_2]
    for key in ("acknowledged_at", "acknowledged_by_role", "acknowledged_by"):
        assert acknowledged[key] is not None
    assert acknowledged["ack_note"] is not None
    assert alerts_by_id(body)[ALERT_1]["escalates_at"] is not None


def test_response_with_nulls_has_exactly_the_documented_keys(aws):
    seed_nulls(aws)

    status, body, _ = call()

    assert status == 200
    assert_exact_keys(body)
    residents = residents_by_id(body)
    assert residents[RESIDENT_B]["wearable"] is None
    assert residents[RESIDENT_B]["bedroom"] is None
    assert residents[RESIDENT_B]["vitals"] == []
    assert residents[RESIDENT_C]["wearable"]["ts"] is None
    (alert,) = body["alerts"]
    assert alert["resident_id"] == "unassigned"
    assert alert["acknowledged_by"] is None
    assert alert["escalates_at"] is None


def test_whole_dashboard_body(aws):
    seed_resident_a(aws)
    seed_nulls(aws)

    status, body, _ = call(api_event(groups=["nurse"]))

    assert status == 200
    assert body == {
        "residents": [
            {
                "resident_id": RESIDENT_A,
                "display_name": NAME_A,
                "home_id": HOME_A,
                "status": "attention",
                "status_text": "Needs attention",
                "wearable": {
                    "device_id": WEARABLE,
                    "ts": ago(minutes=1),
                    "battery_pct": 63,
                    "worn": True,
                    "charging": False,
                    "online": True,
                },
                "bedroom": {"device_id": BEDROOM, "ts": ago(minutes=1), "online": True},
                "vitals": [
                    {
                        "ts": ago(minutes=1),
                        "hr_bpm": 73,
                        "spo2_pct": 96,
                        "skin_temp_c": 36.4,
                    }
                ],
            },
            {
                "resident_id": RESIDENT_B,
                "display_name": NAME_B,
                "home_id": HOME_B,
                "status": "ok",
                "status_text": "All readings look normal",
                "wearable": None,
                "bedroom": None,
                "vitals": [],
            },
            {
                "resident_id": RESIDENT_C,
                "display_name": NAME_C,
                "home_id": HOME_C,
                "status": "ok",
                "status_text": "All readings look normal",
                "wearable": {
                    "device_id": WEARABLE_3,
                    "ts": None,
                    "battery_pct": None,
                    "worn": None,
                    "charging": False,
                    "online": False,
                },
                "bedroom": None,
                "vitals": [],
            },
        ],
        "alerts": [
            {
                "alert_id": ALERT_3,
                "resident_id": "unassigned",
                "device_id": UNASSIGNED_DEVICE,
                "alert_type": "fall",
                "severity": "critical",
                "status": "open",
                "event_ts": ago(minutes=2),
                "acknowledged_at": None,
                "acknowledged_by_role": None,
                "acknowledged_by": None,
                "ack_note": None,
                "escalated_at": ESCALATED_AT,
                "escalates_at": None,
            },
            {
                "alert_id": ALERT_1,
                "resident_id": RESIDENT_A,
                "device_id": WEARABLE,
                "alert_type": "hr_high",
                "severity": "warning",
                "status": "open",
                "event_ts": ago(minutes=10),
                "acknowledged_at": None,
                "acknowledged_by_role": None,
                "acknowledged_by": None,
                "ack_note": None,
                "escalated_at": None,
                "escalates_at": ESCALATES_AT,
            },
            {
                "alert_id": ALERT_2,
                "resident_id": RESIDENT_A,
                "device_id": WEARABLE,
                "alert_type": "battery_low",
                "severity": "warning",
                "status": "acknowledged",
                "event_ts": ago(minutes=30),
                "acknowledged_at": ago(minutes=20),
                "acknowledged_by_role": "caregiver",
                "acknowledged_by": CAREGIVER_NAME,
                "ack_note": ACK_NOTE,
                "escalated_at": None,
                "escalates_at": None,
            },
        ],
        # Nurse only (alerts-map): resident A has open alerts but no HOME item.
        "homes": [],
    }


def test_response_headers_are_json_and_not_cached(aws):
    _, _, resp = call()

    assert resp["headers"] == {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
    }


def test_empty_tables_return_empty_lists(aws):
    status, body, _ = call()

    assert (status, body) == (200, {"residents": [], "alerts": []})


def test_lambda_handler_returns_200_with_the_real_clock(aws):
    seed_resident_a(aws)

    resp = handler.lambda_handler(api_event(), None)

    assert resp["statusCode"] == 200
    assert len(json.loads(resp["body"])["residents"]) == 1


# ---- 2. projected-but-dropped fields never reach the body ----


def test_dropped_fields_and_their_values_never_reach_the_body(aws):
    caregiver = seed_resident_a(aws)
    put_bed_and_motion(aws.ddb, BEDROOM)
    # A resident whose thresholds are read (no alert, a wearable with vitals).
    put_resident(aws.ddb, "res-0004", "Test Resident D", "home-0004")
    put_device(aws.ddb, "wearable-0004", "res-0004", "home-0004")
    put_vitals(aws.ddb, "wearable-0004")
    put_thresholds(aws.ddb, "res-0004", spo2_pct={"min": Decimal(88)})
    # A nurse's acknowledge of a health alert: acknowledged_by is the nurse's sub.
    put_alert(
        aws.ddb,
        alert_id=ALERT_4,
        alert_type="fall",
        severity="critical",
        status="acknowledged",
        event_ts=ago(minutes=40),
        acknowledged_at=ago(minutes=39),
        acknowledged_by=NURSE_SUB,
    )

    status, _, resp = call()
    text = resp["body"]

    assert status == 200
    assert aws.batch_gets(), "the THRESHOLDS item should have been read"
    for key in (
        "photo_key",
        "notified_at",
        "received_at",
        "clock_skew",
        "message_id",
        "details",
        "source_message_id",
        "updated_by",
        "updated_at",
        "medical_notes",
        "created_at",
        "open_pk",
        "entries",
        "fw_version",
        '"sub"',
    ):
        assert key not in text, key
    for value in (
        PHOTO_A,
        PROFILE_NOTE,
        NOTIFIED_AT,
        "2026-11-04T03:30:01.120Z",
        ALERT_RECEIVED_AT,
        ALERT_CREATED_AT,
        LATEST_RECEIVED_AT,
        CLOCK_SKEW_SENTINEL,
        MESSAGE_ID,
        SOURCE_MESSAGE_ID,
        DETAILS_SENTINEL,
        caregiver,
        NURSE_SUB,
        UPDATED_BY_SUB,
        UPDATED_BY_NAME,
        BED_SENTINEL,
        MOTION_SENTINEL,
        "9.8.7-test",
    ):
        assert value not in text, value


# ---- 3. severity ----


def test_missing_severity_is_returned_as_critical(aws):
    put_alert(aws.ddb, alert_type="fall", severity=None)

    _, body, _ = call()

    assert body["alerts"][0]["severity"] == "critical"


@pytest.mark.parametrize("severity", ["high", "CRITICAL", "Warning", "", Decimal(1)])
def test_invalid_severity_is_returned_as_critical(aws, severity):
    put_alert(aws.ddb, severity=severity)

    _, body, _ = call()

    assert body["alerts"][0]["severity"] == "critical"


@pytest.mark.parametrize("severity", ["warning", "critical"])
def test_valid_severity_is_kept(aws, severity):
    put_alert(aws.ddb, severity=severity)

    _, body, _ = call()

    assert body["alerts"][0]["severity"] == severity


# ---- 4. escalates_at ----


@pytest.mark.parametrize(
    "alert_type",
    [
        "hr_high",
        "hr_low",
        "spo2_low",
        "skin_temp_high",
        "skin_temp_low",
        "fall",
        "possible_fall",
    ],
)
def test_escalates_at_is_notified_at_plus_300_s_on_open_health_warning(aws, alert_type):
    put_alert(aws.ddb, alert_type=alert_type)

    _, body, _ = call()

    assert body["alerts"][0]["escalates_at"] == ESCALATES_AT


def test_escalates_at_has_milliseconds_when_notified_at_has_none(aws):
    put_alert(aws.ddb, notified_at="2026-11-04T03:50:07Z")

    _, body, _ = call()

    assert body["alerts"][0]["escalates_at"] == "2026-11-04T03:55:07.000Z"


@pytest.mark.parametrize("alert_type", ["device_offline", "battery_low", "not_worn"])
def test_device_problem_never_has_escalates_at(aws, alert_type):
    put_alert(aws.ddb, alert_type=alert_type, severity="warning", status="open")

    _, body, _ = call()

    (alert,) = body["alerts"]
    assert alert["status"] == "open"
    assert alert["severity"] == "warning"
    assert alert["escalates_at"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"severity": "critical"},
        {"severity": None},
        {"status": "acknowledged", "acknowledged_at": ago(minutes=5)},
        {"resident_id": "unassigned"},
        {"escalated_at": ESCALATED_AT},
        {"notified_at": None},
        {"notified_at": "yesterday"},
    ],
    ids=[
        "critical",
        "no-severity",
        "acknowledged",
        "unassigned",
        "escalated",
        "no-notified_at",
        "bad-notified_at",
    ],
)
def test_escalates_at_is_null_unless_every_condition_holds(aws, overrides):
    put_alert(aws.ddb, **overrides)

    _, body, _ = call()

    assert body["alerts"][0]["escalates_at"] is None


# ---- 5. resident status ----


def _resident_with_vitals(aws, resident_id=RESIDENT_A, device_id=WEARABLE, **values):
    put_resident(aws.ddb, resident_id, f"Test Resident {resident_id[-1]}")
    put_device(aws.ddb, device_id, resident_id)
    put_status(aws.ddb, device_id)
    put_vitals(aws.ddb, device_id, **values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"severity": "critical"},
        {"severity": "critical", "status": "acknowledged"},
        {"severity": None, "alert_type": "fall"},
        {"severity": "bogus"},
    ],
    ids=["open-critical", "acknowledged-critical", "no-severity", "bad-severity"],
)
def test_critical_alert_makes_the_resident_urgent(aws, overrides):
    put_resident(aws.ddb)
    put_alert(aws.ddb, **overrides)

    _, body, _ = call()

    assert status_pair(body) == URGENT


def test_urgent_beats_attention(aws):
    _resident_with_vitals(aws, hr_bpm=Decimal(140))
    put_alert(aws.ddb, alert_id=ALERT_1, severity="warning")
    put_alert(aws.ddb, alert_id=ALERT_2, severity="critical", event_ts=ago(minutes=11))

    _, body, _ = call()

    assert status_pair(body) == URGENT


@pytest.mark.parametrize(
    "overrides",
    [
        {"severity": "warning"},
        {"severity": "warning", "status": "acknowledged"},
        {"alert_type": "device_offline", "severity": "warning"},
        {"alert_type": "battery_low", "severity": "warning", "status": "acknowledged"},
        {"alert_type": "not_worn", "severity": "warning"},
    ],
    ids=["open-warning", "acknowledged-warning", "offline", "battery-ack", "not-worn"],
)
def test_warning_or_device_problem_makes_the_resident_need_attention(aws, overrides):
    put_resident(aws.ddb)
    put_alert(aws.ddb, **overrides)

    _, body, _ = call()

    assert status_pair(body) == ATTENTION


def test_resident_with_no_alerts_and_no_devices_is_ok(aws):
    put_resident(aws.ddb)

    _, body, _ = call()

    assert status_pair(body) == OK


def test_another_residents_alert_does_not_change_the_status(aws):
    put_resident(aws.ddb)
    put_resident(aws.ddb, RESIDENT_B, NAME_B, HOME_B)
    put_alert(aws.ddb, resident_id=RESIDENT_B, severity="critical")

    _, body, _ = call()

    assert status_pair(body, RESIDENT_A) == OK
    assert status_pair(body, RESIDENT_B) == URGENT


def test_skipped_alert_does_not_change_the_status(aws):
    put_resident(aws.ddb)
    put_alert(aws.ddb, severity="critical", status="resolved")

    _, body, _ = call()

    assert status_pair(body) == OK
    assert body["alerts"] == []


@pytest.mark.parametrize(
    ("metric", "value", "expected"),
    [
        ("hr_bpm", Decimal(49), ATTENTION),
        ("hr_bpm", Decimal(50), OK),
        ("hr_bpm", Decimal(51), OK),
        ("hr_bpm", Decimal(119), OK),
        ("hr_bpm", Decimal(120), OK),
        ("hr_bpm", Decimal(121), ATTENTION),
        ("spo2_pct", Decimal(93), OK),
        ("spo2_pct", Decimal(92), OK),
        ("spo2_pct", Decimal(91), ATTENTION),
        ("spo2_pct", Decimal(100), OK),
        ("skin_temp_c", Decimal("29.9"), ATTENTION),
        ("skin_temp_c", Decimal("30.0"), OK),
        ("skin_temp_c", Decimal("30.1"), OK),
        ("skin_temp_c", Decimal("37.9"), OK),
        ("skin_temp_c", Decimal("38.0"), OK),
        ("skin_temp_c", Decimal("38.1"), ATTENTION),
    ],
)
def test_newest_vitals_against_default_thresholds(aws, metric, value, expected):
    _resident_with_vitals(aws, **{metric: value})

    _, body, _ = call()

    assert status_pair(body) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal(91), OK),
        (Decimal(89), OK),
        (Decimal(88), OK),
        (Decimal(87), ATTENTION),
    ],
)
def test_resident_threshold_spo2_min_88_replaces_the_default(aws, value, expected):
    _resident_with_vitals(aws, spo2_pct=value)
    put_thresholds(aws.ddb, RESIDENT_A, spo2_pct={"min": Decimal(88)})

    _, body, _ = call()

    assert status_pair(body) == expected


@pytest.mark.parametrize(
    ("limits", "metric", "value"),
    [
        ({"skin_temp_c": {"max": Decimal("36.6")}}, "skin_temp_c", Decimal("36.6")),
        ({"skin_temp_c": {"min": Decimal("30.7")}}, "skin_temp_c", Decimal("30.7")),
        ({"skin_temp_c": {"max": Decimal("37.5")}}, "skin_temp_c", Decimal("37.5")),
    ],
    ids=["max-36.6", "min-30.7", "max-37.5"],
)
def test_skin_temp_exactly_at_a_custom_decimal_limit_is_normal(
    aws, limits, metric, value
):
    """Strictly outside (data model, Resident thresholds): a reading equal to its
    limit is normal, whatever the decimal."""
    _resident_with_vitals(aws, **{metric: value})
    put_thresholds(aws.ddb, RESIDENT_A, **limits)

    _, body, _ = call()

    assert status_pair(body) == OK


def test_custom_heart_rate_min_makes_a_lower_reading_normal(aws):
    _resident_with_vitals(aws, hr_bpm=Decimal(46))
    put_thresholds(
        aws.ddb, RESIDENT_A, hr_bpm={"min": Decimal(45), "max": Decimal(120)}
    )

    _, body, _ = call()

    assert status_pair(body) == OK


def test_null_readings_are_never_abnormal(aws):
    _resident_with_vitals(
        aws, hr_bpm=None, spo2_pct=None, skin_temp_c=None, activity_pct=None
    )

    _, body, _ = call()

    assert status_pair(body) == OK
    assert body["residents"][0]["vitals"][0]["hr_bpm"] is None


@pytest.mark.parametrize(
    ("limits", "metric", "value"),
    [
        ({"hr_bpm": {"min": Decimal(17)}}, "hr_bpm", Decimal(49)),
        ({"hr_bpm": {"min": Decimal("45.5")}}, "hr_bpm", Decimal(49)),
        ({"hr_bpm": {"min": "45"}}, "hr_bpm", Decimal(49)),
        ({"hr_bpm": {"min": True}}, "hr_bpm", Decimal(49)),
        ({"hr_bpm": {"min": Decimal(110), "max": Decimal(100)}}, "hr_bpm", Decimal(49)),
        ({"hr_bpm": "45-130"}, "hr_bpm", Decimal(49)),
        ({"spo2_pct": {"min": Decimal("88.5")}}, "spo2_pct", Decimal(90)),
        ({"spo2_pct": {"min": Decimal(40)}}, "spo2_pct", Decimal(91)),
        ({"skin_temp_c": {"max": Decimal("45.5")}}, "skin_temp_c", Decimal("38.5")),
    ],
    ids=[
        "hr-min-below-range",
        "hr-min-decimal",
        "hr-min-string",
        "hr-min-bool",
        "hr-min-not-below-max",
        "hr-not-a-map",
        "spo2-min-decimal",
        "spo2-min-below-range",
        "skin-max-above-range",
    ],
)
def test_invalid_threshold_limits_fall_back_to_defaults(aws, limits, metric, value):
    _resident_with_vitals(aws, **{metric: value})
    put_thresholds(aws.ddb, RESIDENT_A, **limits)

    _, body, _ = call()

    assert status_pair(body) == ATTENTION


def test_invalid_thresholds_are_logged_with_field_names_never_values(aws, logs):
    _resident_with_vitals(aws)
    put_thresholds(
        aws.ddb,
        RESIDENT_A,
        hr_bpm={"min": Decimal(17), "max": Decimal(253)},
        spo2_pct="eighty-eight-qx",
    )

    call()

    warnings = [r for r in logs() if r["message"] == "invalid thresholds"]
    (record,) = warnings
    assert record["level"] == "WARNING"
    assert record["resident_id"] == RESIDENT_A
    assert sorted(record["fields"]) == ["hr_bpm.max", "hr_bpm.min", "spo2_pct"]
    text = json.dumps(record)
    for value in ("17", "253", "eighty-eight-qx", UPDATED_BY_NAME):
        assert value not in text


def test_thresholds_are_not_read_for_a_resident_with_an_open_alert(aws):
    _resident_with_vitals(aws, hr_bpm=Decimal(140))
    put_alert(aws.ddb, alert_type="battery_low", severity="warning")

    call()

    assert "batch_get_item" not in aws.ops()


def test_thresholds_are_not_read_for_a_resident_without_vitals(aws):
    put_resident(aws.ddb)
    put_device(aws.ddb, WEARABLE)
    put_status(aws.ddb, WEARABLE)

    call()

    assert "batch_get_item" not in aws.ops()


def test_thresholds_are_read_only_for_residents_that_need_them(aws):
    _resident_with_vitals(aws, RESIDENT_A, WEARABLE)
    _resident_with_vitals(aws, RESIDENT_B, WEARABLE_2)
    put_resident(aws.ddb, RESIDENT_C, NAME_C, HOME_C)
    put_alert(aws.ddb, resident_id=RESIDENT_B)

    call()

    (request,) = aws.batch_gets()
    ((table, spec),) = request["RequestItems"].items()
    assert table == "silvercare-registry"
    assert spec["Keys"] == [
        {"pk": {"S": f"THRESHOLDS#{RESIDENT_A}"}, "sk": {"S": "THRESHOLDS"}}
    ]


def test_thresholds_are_cached_between_calls(aws):
    _resident_with_vitals(aws)
    put_thresholds(aws.ddb, RESIDENT_A, spo2_pct={"min": Decimal(88)})

    call()
    call()

    assert len(aws.batch_gets()) == 1


def test_no_thresholds_item_is_cached_too(aws):
    _resident_with_vitals(aws)

    call()
    call()

    assert len(aws.batch_gets()) == 1


def test_vitals_of_a_second_wearable_count_for_the_status(aws):
    """Both wearables' newest entries count; the card shows the newest status's."""
    put_resident(aws.ddb)
    put_device(aws.ddb, WEARABLE)
    put_device(aws.ddb, WEARABLE_2)
    put_status(aws.ddb, WEARABLE, ts=ago(minutes=10))
    put_status(aws.ddb, WEARABLE_2, ts=ago(minutes=2))
    put_vitals(aws.ddb, WEARABLE, hr_bpm=Decimal(140))
    put_vitals(aws.ddb, WEARABLE_2)

    _, body, _ = call()

    assert status_pair(body) == ATTENTION
    assert body["residents"][0]["vitals"][0]["hr_bpm"] == 73


# ---- 6. actor names ----


def test_caregiver_acknowledge_of_a_device_problem_returns_the_cognito_name(aws):
    caregiver = aws.add_user(CAREGIVER_EMAIL, CAREGIVER_NAME)
    put_alert(
        aws.ddb,
        alert_type="not_worn",
        status="acknowledged",
        acknowledged_at=ago(minutes=3),
        acknowledged_by=caregiver,
        acknowledged_by_role="caregiver",
    )

    _, body, resp = call()

    (alert,) = body["alerts"]
    assert alert["acknowledged_by"] == CAREGIVER_NAME
    assert alert["acknowledged_by_role"] == "caregiver"
    assert caregiver not in resp["body"]
    (request,) = aws.lookups()
    assert request == {"UserPoolId": aws.user_pool_id, "Username": caregiver}
    assert aws.cognito_ops() == ["admin_get_user"]


def test_caregiver_email_never_reaches_the_body(aws):
    """AdminGetUser returns every attribute; only `name` may be used."""
    caregiver = aws.add_user(CAREGIVER_EMAIL, CAREGIVER_NAME)
    put_alert(
        aws.ddb,
        alert_type="not_worn",
        status="acknowledged",
        acknowledged_by=caregiver,
        acknowledged_by_role="caregiver",
    )

    _, _, resp = call()

    assert CAREGIVER_EMAIL not in resp["body"]
    assert "example.invalid" not in resp["body"]


def test_deleted_caregiver_account_returns_former_caregiver(aws):
    caregiver = aws.add_user(CAREGIVER_EMAIL, CAREGIVER_NAME)
    aws.delete_user(caregiver)
    put_alert(
        aws.ddb,
        alert_type="battery_low",
        status="acknowledged",
        acknowledged_by=caregiver,
        acknowledged_by_role="caregiver",
    )

    _, body, _ = call()

    assert body["alerts"][0]["acknowledged_by"] == "Former caregiver"


def test_caregiver_account_without_a_name_returns_caregiver(aws):
    caregiver = aws.add_user(CAREGIVER_2_EMAIL, None)
    put_alert(
        aws.ddb,
        alert_type="battery_low",
        status="acknowledged",
        acknowledged_by=caregiver,
        acknowledged_by_role="caregiver",
    )

    _, body, _ = call()

    assert body["alerts"][0]["acknowledged_by"] == "Caregiver"


def test_nurse_acknowledge_of_a_health_alert_returns_no_acknowledger(aws):
    put_alert(
        aws.ddb,
        alert_type="fall",
        severity="critical",
        status="acknowledged",
        acknowledged_at=ago(minutes=4),
        acknowledged_by=NURSE_SUB,
        ack_note=ACK_NOTE,
    )

    _, body, _ = call()

    (alert,) = body["alerts"]
    assert alert["acknowledged_by"] is None
    assert alert["acknowledged_by_role"] is None
    assert alert["acknowledged_at"] == ago(minutes=4)
    assert alert["ack_note"] == ACK_NOTE
    assert aws.cognito_requests == []


def test_role_on_a_health_alert_is_never_returned_or_looked_up(aws):
    caregiver = aws.add_user(CAREGIVER_EMAIL, CAREGIVER_NAME)
    put_alert(
        aws.ddb,
        status="acknowledged",
        acknowledged_by=caregiver,
        acknowledged_by_role="caregiver",
    )

    _, body, _ = call()

    assert body["alerts"][0]["acknowledged_by_role"] is None
    assert body["alerts"][0]["acknowledged_by"] is None
    assert aws.cognito_requests == []


def test_ops_acknowledge_returns_role_ops_and_no_name(aws):
    put_alert(
        aws.ddb,
        alert_type="device_offline",
        status="acknowledged",
        acknowledged_by=OPS_SUB,
        acknowledged_by_role="ops",
    )

    _, body, resp = call()

    (alert,) = body["alerts"]
    assert alert["acknowledged_by_role"] == "ops"
    assert alert["acknowledged_by"] is None
    assert OPS_SUB not in resp["body"]
    assert aws.cognito_requests == []


@pytest.mark.parametrize("role", ["nurse", "doctor", "family", ""])
def test_unknown_acknowledger_role_on_a_device_problem_is_null(aws, role):
    put_alert(
        aws.ddb,
        alert_type="device_offline",
        status="acknowledged",
        acknowledged_by=NURSE_SUB,
        acknowledged_by_role=role,
    )

    _, body, _ = call()

    assert body["alerts"][0]["acknowledged_by_role"] is None
    assert body["alerts"][0]["acknowledged_by"] is None
    assert aws.cognito_requests == []


def test_cognito_error_degrades_to_null_name_with_200(aws, logs):
    caregiver = aws.add_user(CAREGIVER_EMAIL, CAREGIVER_NAME)
    put_alert(
        aws.ddb,
        alert_type="battery_low",
        status="acknowledged",
        acknowledged_by=caregiver,
        acknowledged_by_role="caregiver",
    )
    aws.cognito_client.fail["admin_get_user"] = ClientError(
        {"Error": {"Code": "TooManyRequestsException", "Message": "qz-1203"}},
        "AdminGetUser",
    )

    status, body, _ = call()

    assert status == 200
    assert body["alerts"][0]["acknowledged_by"] is None
    assert body["alerts"][0]["acknowledged_by_role"] == "caregiver"
    (record,) = logs()
    assert record["outcome"] == "ok"
    assert record["name_lookup_failures"] == 1
    assert "qz-1203" not in json.dumps(record)


def test_failed_name_lookup_is_retried_on_the_next_call(aws):
    caregiver = aws.add_user(CAREGIVER_EMAIL, CAREGIVER_NAME)
    put_alert(
        aws.ddb,
        alert_type="battery_low",
        status="acknowledged",
        acknowledged_by=caregiver,
        acknowledged_by_role="caregiver",
    )
    aws.cognito_client.fail["admin_get_user"] = ClientError(
        {"Error": {"Code": "TooManyRequestsException", "Message": "x"}},
        "AdminGetUser",
    )
    call()
    del aws.cognito_client.fail["admin_get_user"]

    _, body, _ = call()

    assert body["alerts"][0]["acknowledged_by"] == CAREGIVER_NAME
    assert len(aws.lookups()) == 2


@pytest.mark.parametrize(
    "error",
    [
        ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "x"}},
            "AdminGetUser",
        ),
        ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "x"}},
            "AdminGetUser",
        ),
        TimeoutError("synthetic network timeout"),
    ],
    ids=["access-denied", "no-such-pool", "timeout"],
)
def test_other_cognito_errors_are_failures_not_former_caregiver(aws, logs, error):
    """Only UserNotFoundException means the account is gone."""
    caregiver = aws.add_user(CAREGIVER_EMAIL, CAREGIVER_NAME)
    put_alert(
        aws.ddb,
        alert_type="battery_low",
        status="acknowledged",
        acknowledged_by=caregiver,
        acknowledged_by_role="caregiver",
    )
    aws.cognito_client.fail["admin_get_user"] = error

    status, body, _ = call()

    assert status == 200
    assert body["alerts"][0]["acknowledged_by"] is None
    (record,) = logs()
    assert record["name_lookup_failures"] == 1


def test_names_are_cached_between_calls(aws):
    caregiver = aws.add_user(CAREGIVER_EMAIL, CAREGIVER_NAME)
    put_alert(
        aws.ddb,
        alert_type="battery_low",
        status="acknowledged",
        acknowledged_by=caregiver,
        acknowledged_by_role="caregiver",
    )

    call()
    _, body, _ = call()

    assert body["alerts"][0]["acknowledged_by"] == CAREGIVER_NAME
    assert len(aws.lookups()) == 1


def test_former_caregiver_is_cached_too(aws):
    caregiver = aws.add_user(CAREGIVER_EMAIL, CAREGIVER_NAME)
    aws.delete_user(caregiver)
    put_alert(
        aws.ddb,
        alert_type="battery_low",
        status="acknowledged",
        acknowledged_by=caregiver,
        acknowledged_by_role="caregiver",
    )

    call()
    _, body, _ = call()

    assert body["alerts"][0]["acknowledged_by"] == "Former caregiver"
    assert len(aws.lookups()) == 1


def test_one_lookup_per_caregiver_however_many_alerts(aws):
    caregiver = aws.add_user(CAREGIVER_EMAIL, CAREGIVER_NAME)
    for alert_id, alert_type in ((ALERT_1, "battery_low"), (ALERT_2, "not_worn")):
        put_alert(
            aws.ddb,
            alert_id=alert_id,
            alert_type=alert_type,
            status="acknowledged",
            acknowledged_by=caregiver,
            acknowledged_by_role="caregiver",
        )

    _, body, _ = call()

    assert [a["acknowledged_by"] for a in body["alerts"]] == [CAREGIVER_NAME] * 2
    assert len(aws.lookups()) == 1


def test_list_users_is_never_called(aws):
    """The role may only AdminGetUser (infra/api.tf, CognitoNames): ListUsers would
    let it enumerate the pool."""
    first = aws.add_user(CAREGIVER_EMAIL, CAREGIVER_NAME)
    second = aws.add_user(CAREGIVER_2_EMAIL, None)
    gone = aws.add_user("test.caregiver.c@example.invalid", "Test Caregiver C")
    aws.delete_user(gone)
    for alert_id, sub in ((ALERT_1, first), (ALERT_2, second), (ALERT_3, gone)):
        put_alert(
            aws.ddb,
            alert_id=alert_id,
            alert_type="battery_low",
            status="acknowledged",
            acknowledged_by=sub,
            acknowledged_by_role="caregiver",
        )

    call()

    assert set(aws.cognito_ops()) == {"admin_get_user"}
    assert sorted(r["Username"] for r in aws.lookups()) == sorted([first, second, gone])


def test_no_cognito_call_when_nothing_needs_a_name(aws):
    seed_nulls(aws)
    put_alert(aws.ddb, alert_id=ALERT_1)

    status, _, _ = call()

    assert status == 200
    assert aws.cognito_requests == []
    assert aws.cognito_calls == []


@pytest.mark.parametrize(
    "sub",
    [
        'x" or name ^= "',
        "00000000-0000-4000-8000-00000000000A",
        "00000000-0000-4000-8000-000000000002 ",
        "00000000000040008000000000000002",
        "",
        CAREGIVER_EMAIL,
    ],
    ids=["quote", "uppercase", "trailing-space", "no-dashes", "empty", "email"],
)
def test_malformed_sub_is_never_sent_to_cognito(aws, sub):
    put_alert(
        aws.ddb,
        alert_type="battery_low",
        status="acknowledged",
        acknowledged_by=sub,
        acknowledged_by_role="caregiver",
    )

    status, body, resp = call()

    assert status == 200
    assert body["alerts"][0]["acknowledged_by"] is None
    assert aws.cognito_calls == []
    if sub:
        assert sub not in resp["body"]


# ---- 7. devices ----


def test_resident_without_devices_has_null_wearable_and_bedroom(aws):
    put_resident(aws.ddb)

    _, body, _ = call()

    resident = body["residents"][0]
    assert resident["wearable"] is None
    assert resident["bedroom"] is None
    assert resident["vitals"] == []


def test_never_reported_devices_have_null_readings_and_are_offline(aws):
    put_resident(aws.ddb)
    put_device(aws.ddb, WEARABLE)
    put_device(aws.ddb, BEDROOM)

    _, body, _ = call()

    resident = body["residents"][0]
    assert resident["wearable"] == {
        "device_id": WEARABLE,
        "ts": None,
        "battery_pct": None,
        "worn": None,
        "charging": False,
        "online": False,
    }
    assert resident["bedroom"] == {"device_id": BEDROOM, "ts": None, "online": False}


@pytest.mark.parametrize("device_id", [WEARABLE, BEDROOM])
def test_status_exactly_15_minutes_old_is_online(aws, device_id):
    put_resident(aws.ddb)
    put_device(aws.ddb, device_id)
    put_status(aws.ddb, device_id, ts=iso(NOW - timedelta(minutes=15)))

    _, body, _ = call()

    key = "wearable" if device_id == WEARABLE else "bedroom"
    assert body["residents"][0][key]["online"] is True


@pytest.mark.parametrize("device_id", [WEARABLE, BEDROOM])
def test_status_15_minutes_and_1_ms_old_is_offline(aws, device_id):
    put_resident(aws.ddb)
    put_device(aws.ddb, device_id)
    ts = iso(NOW - timedelta(minutes=15, milliseconds=1))
    put_status(aws.ddb, device_id, ts=ts)

    _, body, _ = call()

    key = "wearable" if device_id == WEARABLE else "bedroom"
    assert body["residents"][0][key]["online"] is False
    assert body["residents"][0][key]["ts"] == ts


def test_wearable_with_the_newest_status_is_shown(aws):
    put_resident(aws.ddb)
    put_device(aws.ddb, WEARABLE)
    put_device(aws.ddb, WEARABLE_2)
    put_status(aws.ddb, WEARABLE, ts=ago(minutes=10))
    put_status(aws.ddb, WEARABLE_2, ts=ago(minutes=2), battery_pct=Decimal(41))
    put_vitals(aws.ddb, WEARABLE, hr_bpm=Decimal(66))
    put_vitals(aws.ddb, WEARABLE_2, hr_bpm=Decimal(81))

    _, body, _ = call()

    resident = body["residents"][0]
    assert resident["wearable"]["device_id"] == WEARABLE_2
    assert resident["wearable"]["battery_pct"] == 41
    assert [v["hr_bpm"] for v in resident["vitals"]] == [81]


def test_wearable_with_a_status_beats_one_without(aws):
    put_resident(aws.ddb)
    put_device(aws.ddb, WEARABLE)
    put_device(aws.ddb, WEARABLE_2)
    put_status(aws.ddb, WEARABLE_2, ts=ago(minutes=40))

    _, body, _ = call()

    assert body["residents"][0]["wearable"]["device_id"] == WEARABLE_2


def test_bedroom_unit_with_the_newest_status_is_shown(aws):
    put_resident(aws.ddb)
    put_device(aws.ddb, BEDROOM)
    put_device(aws.ddb, BEDROOM_2)
    put_status(aws.ddb, BEDROOM, ts=ago(minutes=1))
    put_status(aws.ddb, BEDROOM_2, ts=ago(minutes=9))

    _, body, _ = call()

    assert body["residents"][0]["bedroom"]["device_id"] == BEDROOM


def test_spare_devices_never_reach_the_response(aws):
    put_resident(aws.ddb)
    put_device(aws.ddb, SPARE, resident_id=None)
    put_status(aws.ddb, SPARE)
    put_vitals(aws.ddb, SPARE, hr_bpm=Decimal(199))

    _, body, resp = call()

    assert SPARE not in resp["body"]
    assert body["residents"][0]["wearable"] is None
    assert body["residents"][0]["vitals"] == []


def test_each_resident_shows_only_their_own_devices(aws):
    put_resident(aws.ddb)
    put_resident(aws.ddb, RESIDENT_B, NAME_B, HOME_B)
    put_device(aws.ddb, WEARABLE, RESIDENT_A)
    put_device(aws.ddb, WEARABLE_2, RESIDENT_B, HOME_B)
    put_status(aws.ddb, WEARABLE)
    put_status(aws.ddb, WEARABLE_2)
    put_vitals(aws.ddb, WEARABLE, hr_bpm=Decimal(66))
    put_vitals(aws.ddb, WEARABLE_2, hr_bpm=Decimal(81))

    _, body, _ = call()

    residents = residents_by_id(body)
    assert residents[RESIDENT_A]["wearable"]["device_id"] == WEARABLE
    assert [v["hr_bpm"] for v in residents[RESIDENT_A]["vitals"]] == [66]
    assert residents[RESIDENT_B]["wearable"]["device_id"] == WEARABLE_2
    assert [v["hr_bpm"] for v in residents[RESIDENT_B]["vitals"]] == [81]
    assert residents[RESIDENT_B]["bedroom"] is None


def test_vitals_has_only_the_newest_entry(aws):
    _resident_with_vitals(aws, ts=ago(seconds=30))

    _, body, _ = call()

    assert body["residents"][0]["vitals"] == [
        {
            "ts": ago(seconds=30),
            "hr_bpm": 73,
            "spo2_pct": 96,
            "skin_temp_c": 36.4,
        }
    ]


def test_activity_pct_never_reaches_the_body(aws):
    _resident_with_vitals(aws, activity_pct=ACTIVITY)

    _, body, resp = call()

    assert "activity_pct" not in resp["body"]
    assert "activity" not in resp["body"]
    assert set(body["residents"][0]["vitals"][0]) == VITALS_KEYS


@pytest.mark.parametrize(
    ("metric", "value"),
    [
        ("hr_bpm", Decimal(251)),
        ("hr_bpm", Decimal("72.5")),
        ("spo2_pct", Decimal(49)),
        ("skin_temp_c", Decimal("45.1")),
        ("hr_bpm", "72"),
        ("hr_bpm", True),
    ],
)
def test_out_of_range_reading_is_returned_as_null(aws, metric, value):
    _resident_with_vitals(aws, **{metric: value})

    _, body, _ = call()

    assert body["residents"][0]["vitals"][0][metric] is None


def test_vitals_with_an_invalid_ts_are_not_shown(aws):
    _resident_with_vitals(aws, ts="2026-11-04 03:59:00")

    _, body, _ = call()

    assert body["residents"][0]["vitals"] == []


def test_worn_false_and_charging_are_returned(aws):
    put_resident(aws.ddb)
    put_device(aws.ddb, WEARABLE)
    put_status(aws.ddb, WEARABLE, worn=False, charging=True)

    _, body, _ = call()

    assert body["residents"][0]["wearable"]["worn"] is False
    assert body["residents"][0]["wearable"]["charging"] is True


# ---- 8. alerts ----


def test_alerts_are_returned_newest_first(aws):
    put_resident(aws.ddb)
    put_alert(aws.ddb, alert_id=ALERT_1, event_ts=ago(minutes=30))
    put_alert(aws.ddb, alert_id=ALERT_2, event_ts=ago(minutes=1))
    put_alert(
        aws.ddb, resident_id=RESIDENT_B, alert_id=ALERT_3, event_ts=ago(minutes=12)
    )

    _, body, _ = call()

    assert [a["alert_id"] for a in body["alerts"]] == [ALERT_2, ALERT_3, ALERT_1]


def test_malformed_alerts_are_skipped_and_counted(aws, logs):
    put_alert(aws.ddb, alert_id=ALERT_1)
    put_alert(aws.ddb, pk="RESIDENT#resident-9", alert_id=ALERT_2)
    put_alert(aws.ddb, pk="USER#res-0001", alert_id=ALERT_2)
    put_alert(aws.ddb, sk=f"ALERT#{ago(minutes=3)}#not-a-uuid")
    put_alert(aws.ddb, sk=f"EVENT#{ago(minutes=4)}#{ALERT_3}")
    put_alert(aws.ddb, alert_id=ALERT_3, alert_type="inactivity")
    put_alert(aws.ddb, alert_id=ALERT_4, status="resolved")
    put_alert(
        aws.ddb,
        sk=f"ALERT#{ago(minutes=5)}#6f1c2e0a-5b3d-5e8f-9a47-0000000000b1",
        status="cancelled",
    )
    put_alert(
        aws.ddb,
        sk=f"ALERT#{ago(minutes=6)}#6f1c2e0a-5b3d-5e8f-9a47-0000000000b2",
        device_id="sensor-0001",
    )
    put_alert(
        aws.ddb,
        sk="ALERT#not-a-time#6f1c2e0a-5b3d-5e8f-9a47-0000000000b3",
        event_ts="yesterday",
    )

    status, body, _ = call()

    assert status == 200
    assert [a["alert_id"] for a in body["alerts"]] == [ALERT_1]
    (record,) = logs()
    assert record["skipped"] == 9
    assert record["alerts"] == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"alert_type": ["hr_high"]},
        {"status": ["open"]},
        {"severity": {"level": "warning"}},
        {
            "alert_type": "battery_low",
            "status": "acknowledged",
            "acknowledged_by_role": ["caregiver"],
        },
    ],
    ids=["alert_type-list", "status-list", "severity-map", "role-list"],
)
def test_alert_with_a_non_string_field_is_skipped_not_a_500(aws, overrides):
    """A malformed item must be skipped (like any other bad value), not fail the
    whole dashboard for every staff member."""
    put_alert(aws.ddb, alert_id=ALERT_1, event_ts=ago(minutes=1))
    put_alert(aws.ddb, alert_id=ALERT_2, event_ts=ago(minutes=2), **overrides)

    resp = handler.lambda_handler(api_event(), None)

    assert resp["statusCode"] == 200
    ids = [a["alert_id"] for a in json.loads(resp["body"])["alerts"]]
    assert ALERT_1 in ids


def test_event_ts_falls_back_to_the_sort_key(aws):
    put_alert(
        aws.ddb,
        sk=f"ALERT#{ago(minutes=7)}#{ALERT_1}",
        event_ts=ago(minutes=7),
    )
    aws.ddb.update_item(
        TableName=ALERTS_TABLE,
        Key={
            "pk": {"S": f"RESIDENT#{RESIDENT_A}"},
            "sk": {"S": f"ALERT#{ago(minutes=7)}#{ALERT_1}"},
        },
        UpdateExpression="REMOVE event_ts",
    )

    _, body, _ = call()

    assert body["alerts"][0]["event_ts"] == ago(minutes=7)


def test_resident_without_a_profile_is_not_listed_but_their_alerts_are(aws):
    put_resident(aws.ddb)
    put_device(aws.ddb, WEARABLE_2, RESIDENT_NO_PROFILE)
    put_status(aws.ddb, WEARABLE_2)
    put_alert(aws.ddb, resident_id=RESIDENT_NO_PROFILE, device_id=WEARABLE_2)

    _, body, _ = call()

    assert [r["resident_id"] for r in body["residents"]] == [RESIDENT_A]
    (alert,) = body["alerts"]
    assert alert["resident_id"] == RESIDENT_NO_PROFILE


@pytest.mark.parametrize(
    "display_name",
    ["", "   ", "x" * 101, "Test" + chr(0x202E) + "Resident", Decimal(7)],
)
def test_resident_with_an_invalid_display_name_is_not_listed(aws, display_name):
    put_resident(aws.ddb, display_name=display_name)

    _, body, _ = call()

    assert body["residents"] == []


def test_invalid_home_id_is_returned_as_null(aws):
    put_resident(aws.ddb, home_id="12 Synthetic Street")

    _, body, resp = call()

    assert body["residents"][0]["home_id"] is None
    assert "Synthetic Street" not in resp["body"]


@pytest.mark.parametrize("note", ["", "x" * 501, Decimal(5)])
def test_invalid_ack_note_is_returned_as_null(aws, note):
    put_alert(aws.ddb, status="acknowledged", acknowledged_by=NURSE_SUB, ack_note=note)

    _, body, _ = call()

    assert body["alerts"][0]["ack_note"] is None


def test_ack_note_of_500_characters_is_kept(aws):
    put_alert(
        aws.ddb, status="acknowledged", acknowledged_by=NURSE_SUB, ack_note="n" * 500
    )

    _, body, _ = call()

    assert body["alerts"][0]["ack_note"] == "n" * 500


# ---- 9. groups, claims, and input ----


@pytest.mark.parametrize("group", ["doctor", "nurse"])
def test_staff_get_200(aws, group):
    seed_resident_a(aws)

    status, body, _ = call(api_event(groups=[group]))

    assert status == 200
    assert len(body["residents"]) == 1


@pytest.mark.parametrize(
    "groups",
    [
        ["doctor", "nurse"],
        "[doctor nurse]",
        "nurse",
        "doctor,nurse",
        "[caregiver nurse]",
    ],
)
def test_staff_in_any_group_claim_shape_get_200(aws, groups):
    status, _, _ = call(api_event(groups=groups))

    assert status == 200


@pytest.mark.parametrize(
    "groups",
    [
        ["caregiver"],
        ["ops"],
        ["family"],
        [],
        "",
        "caregiver",
        "[ops family]",
        ["Doctor", "nurses"],
        None,
        7,
    ],
)
def test_non_staff_callers_get_403(aws, groups):
    seed_resident_a(aws)

    status, body, _ = call(api_event(groups=groups))

    assert status == 403
    assert body == {"error": {"code": "forbidden", "message": body["error"]["message"]}}
    assert aws.calls == []
    assert aws.cognito_requests == []


def test_missing_groups_claim_gets_403(aws):
    status, _, _ = call(api_event(drop_groups=True))

    assert status == 403
    assert aws.calls == []


def test_groups_claim_in_a_header_is_ignored(aws):
    event = api_event(groups=["caregiver"])
    event["headers"]["cognito:groups"] = "doctor"

    status, _, _ = call(event)

    assert status == 403


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"requestContext": {}},
        {"requestContext": {"authorizer": {"jwt": {}}}},
        {"requestContext": {"authorizer": {"jwt": {"claims": None}}}},
        {"requestContext": {"authorizer": {"jwt": {"claims": "sub=x"}}}},
        None,
    ],
    ids=["empty", "no-authorizer", "no-claims", "claims-null", "claims-str", "none"],
)
def test_missing_claims_get_401(aws, event):
    resp = handler.handle(event, NOW)
    status, body = resp["statusCode"], json.loads(resp["body"])

    assert status == 401
    assert body["error"]["code"] == "unauthorized"
    assert aws.calls == []


@pytest.mark.parametrize("query", [{"resident_id": "res-0001"}, {"": ""}])
def test_any_query_string_gets_400(aws, query):
    status, body, _ = call(api_event(queryStringParameters=query))

    assert status == 400
    assert body["error"]["code"] == "bad_request"
    assert aws.calls == []
    assert aws.cognito_requests == []


@pytest.mark.parametrize("body", ['{"resident_id":"res-0001"}', "{}", "x"])
def test_any_body_gets_400(aws, body):
    status, out, _ = call(api_event(body=body))

    assert status == 400
    assert out["error"]["code"] == "bad_request"
    assert aws.calls == []


def test_non_staff_with_a_body_gets_403_not_400(aws):
    status, _, _ = call(
        api_event(groups=["caregiver"], body="{}", queryStringParameters={"a": "b"})
    )

    assert status == 403


# ---- 10. pagination ----


def _seed_two_of_everything(aws):
    put_resident(aws.ddb)
    put_resident(aws.ddb, RESIDENT_B, NAME_B, HOME_B)
    put_device(aws.ddb, WEARABLE)
    put_device(aws.ddb, WEARABLE_2, RESIDENT_B, HOME_B)
    put_status(aws.ddb, WEARABLE)
    put_status(aws.ddb, WEARABLE_2)
    put_vitals(aws.ddb, WEARABLE)
    put_vitals(aws.ddb, WEARABLE_2)
    put_alert(aws.ddb, alert_id=ALERT_1, event_ts=ago(minutes=5))
    put_alert(aws.ddb, alert_id=ALERT_2, event_ts=ago(minutes=6))


def test_every_query_follows_last_evaluated_key(aws):
    _seed_two_of_everything(aws)
    _, expected, _ = call()
    aws.calls.clear()
    handler.clear_caches()
    aws.client.page_limit = 1

    status, body, _ = call()

    assert status == 200
    assert body == expected
    by_key: dict[str, list[dict]] = {}
    for request in aws.queries():
        value = next(iter(request["ExpressionAttributeValues"].values()))["S"]
        by_key.setdefault(value, []).append(request)
    assert set(by_key) == {"OPEN", "RESIDENT", "DEVICE", "status", "vitals"}
    for value, requests in by_key.items():
        assert len(requests) >= 2, value
        assert "ExclusiveStartKey" not in requests[0]
        assert all("ExclusiveStartKey" in r for r in requests[1:]), value


def test_alert_pages_keep_newest_first_order(aws):
    for n, alert_id in enumerate((ALERT_1, ALERT_2, ALERT_3, ALERT_4)):
        put_alert(aws.ddb, alert_id=alert_id, event_ts=ago(minutes=n + 1))
    aws.client.page_limit = 1

    _, body, _ = call()

    assert [a["alert_id"] for a in body["alerts"]] == [
        ALERT_1,
        ALERT_2,
        ALERT_3,
        ALERT_4,
    ]


# ---- 11. AWS errors ----


def test_throttled_query_returns_clean_500(aws):
    seed_resident_a(aws)
    aws.client.fail["query"] = throttled()

    resp = handler.lambda_handler(api_event(), None)

    assert resp["statusCode"] == 500
    body = json.loads(resp["body"])
    assert body == {
        "error": {"code": "internal_error", "message": body["error"]["message"]}
    }
    for text in ("qz-7781", "ProvisionedThroughput", "RESIDENT#", "Traceback"):
        assert text not in resp["body"]


def test_throttled_query_logs_only_outcome_and_error_code(aws, logs):
    aws.client.fail["query"] = throttled()

    handler.lambda_handler(api_event(), None)

    (record,) = logs()
    assert set(record) - POWERTOOLS_KEYS == {"outcome", "error", "request_id"}
    assert record["outcome"] == "error"
    assert record["error"] == "ProvisionedThroughputExceededException"
    assert record["level"] == "ERROR"
    assert "qz-7781" not in json.dumps(record)


def test_throttled_batch_get_returns_clean_500(aws, logs):
    _resident_with_vitals(aws)
    aws.client.fail["batch_get_item"] = throttled("BatchGetItem")

    resp = handler.lambda_handler(api_event(), None)

    assert resp["statusCode"] == 500
    (record,) = logs()
    assert record["error"] == "ProvisionedThroughputExceededException"


def test_persistently_unprocessed_thresholds_return_500(aws, logs, monkeypatch):
    _resident_with_vitals(aws)
    real = aws.client._client

    class Unprocessed:
        def __getattr__(self, name):
            return getattr(real, name)

        def batch_get_item(self, RequestItems):
            return {"Responses": {}, "UnprocessedKeys": RequestItems}

    monkeypatch.setattr(handler, "dynamodb_client", lambda: Unprocessed())
    monkeypatch.setattr(handler.time, "sleep", lambda _s: None)

    resp = handler.lambda_handler(api_event(), None)

    assert resp["statusCode"] == 500
    (record,) = logs()
    assert record["error"] == "RuntimeError"


def test_unexpected_exception_returns_500_with_its_type_name_logged(
    aws, logs, monkeypatch
):
    def boom():
        raise ValueError("synthetic failure text qz-9912")

    monkeypatch.setattr(handler, "read_vitals", boom)

    resp = handler.lambda_handler(api_event(), None)

    assert resp["statusCode"] == 500
    assert "qz-9912" not in resp["body"]
    (record,) = logs()
    assert record["error"] == "ValueError"
    assert "qz-9912" not in json.dumps(record)


# ---- 12. logs ----


def _log_text(records: list[dict]) -> str:
    """Records as text with UUIDs replaced, so digit strings inside them can't match a
    reading."""
    return UUID_RE.sub("<uuid>", json.dumps(records))


def test_ok_log_has_sub_and_counts_only(aws, logs):
    seed_resident_a(aws)
    seed_nulls(aws)

    call()

    (record,) = logs()
    assert record["message"] == "get dashboard"
    assert set(record) - POWERTOOLS_KEYS == {
        "outcome",
        "sub",
        "residents",
        "alerts",
        "skipped",
        "name_lookup_failures",
        "request_id",
    }
    assert record["outcome"] == "ok"
    assert record["sub"] == SUB
    assert record["request_id"] == REQUEST_ID
    assert (record["residents"], record["alerts"]) == (3, 3)
    assert (record["skipped"], record["name_lookup_failures"]) == (0, 0)


def test_logs_never_carry_names_notes_readings_or_the_body(aws, logs):
    caregiver = seed_resident_a(aws)
    seed_nulls(aws)
    _resident_with_vitals(aws, "res-0004", "wearable-0004", hr_bpm=Decimal(133))
    put_thresholds(aws.ddb, "res-0004", hr_bpm={"min": Decimal(17)})

    _, _, resp = call()
    call(api_event(groups=["caregiver"]))
    call(api_event(body='{"q": "synthetic body qz-5521"}'))
    aws.client.fail["query"] = throttled()
    handler.lambda_handler(api_event(), None)

    records = logs()
    raw = json.dumps(records)
    for value in (caregiver, NURSE_SUB):
        assert value not in raw
    text = _log_text(records)
    for value in (
        NAME_A,
        NAME_B,
        NAME_C,
        "Test Resident",
        CAREGIVER_NAME,
        "Test Caregiver",
        ACK_NOTE,
        "qx-3318",
        PROFILE_NOTE,
        "qz-5521",
        "qz-7781",
        EMAIL,
        NAME,
        "home-000",
        "wearable",
        "bedroom",
        "73",
        "96",
        "36.4",
        "133",
        "63",
        "photo",
    ):
        assert value not in text, value
    assert resp["body"] not in raw


def test_forbidden_log_has_sub_and_groups(aws, logs):
    call(api_event(groups=["caregiver", "ops"]))

    (record,) = logs()
    assert record["outcome"] == "forbidden"
    assert record["sub"] == SUB
    assert record["groups"] == ["caregiver", "ops"]


def test_unauthorized_log_has_only_the_outcome(aws, logs):
    call({"requestContext": {}})

    (record,) = logs()
    assert set(record) - POWERTOOLS_KEYS == {"outcome", "request_id"}
    assert record["outcome"] == "no_claims"


def test_sub_that_is_not_a_uuid_is_logged_as_invalid(aws, logs):
    call(api_event(claims={"sub": EMAIL}))

    (record,) = logs()
    assert record["sub"] == "invalid"
    assert EMAIL not in json.dumps(record)


# ---- 13. read-only ----


def test_handler_only_reads(aws):
    seed_resident_a(aws)
    _resident_with_vitals(aws, RESIDENT_B, WEARABLE_2)

    call()

    assert set(aws.ops()) == {"query", "batch_get_item"}
    assert len(aws.queries()) == 5
    for request in aws.queries():
        assert request.get("ConsistentRead", False) is False
        assert request["Select"] == "SPECIFIC_ATTRIBUTES"
    for request in aws.batch_gets():
        for spec in request["RequestItems"].values():
            assert spec.get("ConsistentRead", False) is False
    assert set(aws.cognito_ops()) == {"admin_get_user"}


def test_alerts_query_is_newest_first_on_the_open_index(aws):
    call()

    (request,) = [q for q in aws.queries() if q["TableName"] == ALERTS_TABLE]
    assert request["IndexName"] == "open"
    assert request["ScanIndexForward"] is False
    assert request["ExpressionAttributeValues"] == {":open": {"S": "OPEN"}}


def test_two_calls_return_the_same_body(aws):
    seed_resident_a(aws)
    seed_nulls(aws)

    first = handler.handle(api_event(), NOW)
    second = handler.handle(api_event(), NOW)

    assert first == second


def test_unassigned_alert_item_builder_has_no_notified_at():
    """Guard for the fixtures: unassigned alerts are stored without notified_at."""
    item = alert_item(resident_id="unassigned", notified_at=None)

    assert "notified_at" not in item
    assert item["pk"] == "RESIDENT#unassigned"


def test_raw_item_with_extra_attributes_is_built_field_by_field(aws):
    put_raw(
        aws.ddb,
        ALERTS_TABLE,
        {**alert_item(), "resolved_by": UPDATED_BY_SUB, "cancelled_by": OPS_SUB},
    )

    _, body, resp = call()

    assert set(body["alerts"][0]) == ALERT_KEYS
    assert UPDATED_BY_SUB not in resp["body"]
    assert OPS_SUB not in resp["body"]
