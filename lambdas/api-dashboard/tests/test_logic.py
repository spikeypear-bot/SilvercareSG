"""dashboard.py, thresholds.py, and actors.py without AWS."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import actors
import dashboard
import pytest
import thresholds
from botocore.exceptions import ClientError
from events import ALERT_1, NOTIFIED_AT, alert_item

CAREGIVER_SUB = "00000000-0000-4000-8000-000000000002"


def _alert(**overrides) -> dashboard.Alert:
    item = alert_item(**overrides)
    item.pop("open_pk")
    alert = dashboard.alert_from_item(item)
    assert alert is not None
    return alert


# ---- severity and escalates_at ----


@pytest.mark.parametrize(
    ("severity", "expected"),
    [(None, "critical"), ("bogus", "critical"), ("warning", "warning")],
)
def test_severity_is_critical_unless_valid(severity, expected):
    assert _alert(severity=severity).severity == expected


def test_escalates_at_is_notified_at_plus_300_s():
    assert dashboard.escalates_at(_alert()) == "2026-11-04T03:55:07.317Z"


@pytest.mark.parametrize(
    "overrides",
    [
        {"alert_type": "device_offline"},
        {"alert_type": "battery_low"},
        {"alert_type": "not_worn"},
        {"severity": "critical"},
        {"status": "acknowledged"},
        {"resident_id": "unassigned"},
        {"escalated_at": NOTIFIED_AT},
        {"notified_at": None},
    ],
)
def test_escalates_at_is_none_otherwise(overrides):
    assert dashboard.escalates_at(_alert(**overrides)) is None


# ---- thresholds ----


@pytest.mark.parametrize(
    ("value", "limits", "abnormal"),
    [
        (Decimal(50), thresholds.DEFAULTS["hr_bpm"], False),
        (Decimal(49), thresholds.DEFAULTS["hr_bpm"], True),
        (Decimal(120), thresholds.DEFAULTS["hr_bpm"], False),
        (Decimal(121), thresholds.DEFAULTS["hr_bpm"], True),
        (None, thresholds.DEFAULTS["hr_bpm"], False),
        (True, thresholds.DEFAULTS["hr_bpm"], False),
    ],
)
def test_is_abnormal_is_strict(value, limits, abnormal):
    assert thresholds.is_abnormal(value, limits) is abnormal


def test_resolve_reports_field_names_only():
    limits, invalid = thresholds.resolve(
        {"hr_bpm": {"min": Decimal(17)}, "spo2_pct": {"min": Decimal(88)}}
    )

    assert invalid == ["hr_bpm.min"]
    assert limits["hr_bpm"] == thresholds.DEFAULTS["hr_bpm"]
    assert limits["spo2_pct"].min == Decimal(88)


@pytest.mark.parametrize(
    ("limit", "reading"),
    [("max", "36.6"), ("min", "30.7")],
)
def test_vitals_entry_at_a_decimal_limit_is_not_abnormal(limit, reading):
    """The cause of the route failure: vitals_from_item turns skin_temp_c into a
    float, and float(36.6) > Decimal("36.6") is True in Python."""
    limits, _ = thresholds.resolve({"skin_temp_c": {limit: Decimal(reading)}})
    _, entry = dashboard.vitals_from_item(
        {
            "pk": "DEVICE#wearable-0001",
            "latest": {
                "ts": "2026-11-04T03:59:00.000Z",
                "skin_temp_c": Decimal(reading),
            },
        }
    )

    assert dashboard.resident_status([], [entry], limits) == "ok"


def test_alert_from_item_skips_an_unhashable_alert_type():
    item = alert_item(alert_type=["hr_high"])

    assert dashboard.alert_from_item(item) is None


# ---- actors ----


class FakeCognito:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.result = result
        self.error = error

    def admin_get_user(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def _user(name: str | None) -> dict:
    attributes = [
        {"Name": "sub", "Value": CAREGIVER_SUB},
        {"Name": "email", "Value": "test.caregiver.a@example.invalid"},
    ]
    if name is not None:
        attributes.append({"Name": "name", "Value": name})
    return {"Username": CAREGIVER_SUB, "UserAttributes": attributes}


@pytest.fixture(autouse=True)
def _clear():
    actors.clear_cache()
    yield
    actors.clear_cache()


def test_lookup_uses_admin_get_user_with_the_sub_as_username():
    client = FakeCognito(_user("Test Caregiver A"))

    names, failed = actors.caregiver_names(lambda: client, "pool-test", [CAREGIVER_SUB])

    assert names == {CAREGIVER_SUB: "Test Caregiver A"}
    assert failed == 0
    assert client.calls == [{"UserPoolId": "pool-test", "Username": CAREGIVER_SUB}]


def test_user_not_found_is_former_caregiver():
    error = ClientError(
        {"Error": {"Code": "UserNotFoundException", "Message": "x"}}, "AdminGetUser"
    )
    client = FakeCognito(error=error)

    names, failed = actors.caregiver_names(lambda: client, "pool-test", [CAREGIVER_SUB])

    assert names == {CAREGIVER_SUB: "Former caregiver"}
    assert failed == 0


@pytest.mark.parametrize("name", [None, "", "   ", "x" * 101, "Test\nCaregiver"])
def test_missing_or_invalid_name_is_caregiver(name):
    client = FakeCognito(_user(name))

    names, _ = actors.caregiver_names(lambda: client, "pool-test", [CAREGIVER_SUB])

    assert names == {CAREGIVER_SUB: "Caregiver"}


def test_other_errors_count_as_failures_and_are_not_cached():
    error = ClientError(
        {"Error": {"Code": "TooManyRequestsException", "Message": "x"}}, "AdminGetUser"
    )
    client = FakeCognito(error=error)

    first = actors.caregiver_names(lambda: client, "pool-test", [CAREGIVER_SUB])
    client.error = None
    client.result = _user("Test Caregiver A")
    second = actors.caregiver_names(lambda: client, "pool-test", [CAREGIVER_SUB])

    assert first == ({CAREGIVER_SUB: None}, 1)
    assert second == ({CAREGIVER_SUB: "Test Caregiver A"}, 0)


def test_cached_name_expires_after_5_minutes():
    client = FakeCognito(_user("Test Caregiver A"))

    actors.caregiver_names(lambda: client, "p", [CAREGIVER_SUB], clock=lambda: 0.0)
    actors.caregiver_names(lambda: client, "p", [CAREGIVER_SUB], clock=lambda: 299.0)
    actors.caregiver_names(lambda: client, "p", [CAREGIVER_SUB], clock=lambda: 300.0)

    assert len(client.calls) == 2


@pytest.mark.parametrize(
    "sub",
    [
        'x" or name ^= "',
        "00000000-0000-4000-8000-00000000000A",
        CAREGIVER_SUB + "\n",
        "",
        ALERT_1[:-1],
    ],
)
def test_malformed_sub_never_reaches_cognito(sub):
    created: list[int] = []

    names, failed = actors.caregiver_names(
        lambda: created.append(1) or FakeCognito(), "p", [sub]
    )

    assert (names, failed) == ({}, 0)
    assert created == []


def test_no_client_is_created_when_there_is_nothing_to_look_up():
    created: list[int] = []

    actors.caregiver_names(lambda: created.append(1), "p", [])

    assert created == []


def test_iso_ms_formats_milliseconds_and_z():
    value = datetime(2026, 11, 4, 3, 55, 7, 317000, tzinfo=UTC)

    assert dashboard.iso_ms(value) == "2026-11-04T03:55:07.317Z"
