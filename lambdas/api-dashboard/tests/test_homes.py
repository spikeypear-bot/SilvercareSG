"""`homes` on GET /dashboard for nurses (docs/features/alerts-map.md, criteria 2, 4-9),
through the handler against moto."""

from __future__ import annotations

import functools
import json
import re
from decimal import Decimal

import handler
import pytest
from botocore.exceptions import ClientError
from events import (
    ALERT_1,
    ALERT_2,
    ALERT_3,
    HOME_A,
    HOME_ADDRESS,
    HOME_B,
    HOME_C,
    HOME_LAT,
    HOME_LNG,
    NAME_B,
    NOW,
    RESIDENT_A,
    RESIDENT_B,
    RESIDENT_C,
    RESIDENT_NO_PROFILE,
    api_event,
    put_alert,
    put_home,
    put_resident,
)

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
NURSE = ("nurse",)
DOCTOR = ("doctor",)
HOME_A_VIEW = {
    "home_id": HOME_A,
    "address": HOME_ADDRESS,
    "lat": float(HOME_LAT),
    "lng": float(HOME_LNG),
}


def call(groups=NURSE):
    resp = handler.handle(api_event(groups=groups), NOW)
    assert resp["statusCode"] == 200
    return json.loads(resp["body"])


def home_reads(aws) -> list[str]:
    """pk of every HOME# key the handler asked BatchGetItem for."""
    return [
        key["pk"]["S"]
        for request in aws.batch_gets()
        for spec in request["RequestItems"].values()
        for key in spec["Keys"]
        if key["pk"]["S"].startswith("HOME#")
    ]


def throttled() -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "Synthetic throttle text qz-8812 for HOME#home-0001",
            }
        },
        "BatchGetItem",
    )


def resident_with_alert(aws, resident_id=RESIDENT_A, home_id=HOME_A, **alert):
    put_resident(aws.ddb, resident_id, f"Test Resident {resident_id[-1]}", home_id)
    put_alert(aws.ddb, resident_id=resident_id, **alert)


def at_time(monkeypatch, start: float = 1000.0) -> list[float]:
    """Make the handler's load_homes use a fake monotonic clock; set [0] to move it."""
    now = [start]
    monkeypatch.setattr(
        handler,
        "load_homes",
        functools.partial(handler.load_homes, clock=lambda: now[0]),
    )
    return now


# ---- criterion 4/5: who gets homes ----


def test_nurse_gets_the_home_of_a_resident_with_an_open_alert(aws):
    resident_with_alert(aws)
    put_home(aws.ddb)

    body = call()

    assert body["homes"] == [HOME_A_VIEW]
    assert "homes_error" not in body


def test_doctor_gets_no_homes_key_and_no_home_read(aws):
    resident_with_alert(aws)
    put_home(aws.ddb)

    body = call(DOCTOR)

    assert set(body) == {"residents", "alerts"}
    assert home_reads(aws) == []


def test_nurse_who_is_also_a_doctor_gets_homes(aws):
    resident_with_alert(aws)
    put_home(aws.ddb)

    body = call(("doctor", "nurse"))

    assert body["homes"] == [HOME_A_VIEW]


def test_nurse_with_no_open_alerts_gets_an_empty_list_and_no_read(aws):
    put_resident(aws.ddb)
    put_home(aws.ddb)

    body = call()

    assert body["homes"] == []
    assert home_reads(aws) == []


# ---- criterion 4: which homes ----


def test_acknowledged_alert_counts_as_open_list(aws):
    resident_with_alert(aws, status="acknowledged")
    put_home(aws.ddb)

    assert call()["homes"] == [HOME_A_VIEW]


def test_home_of_a_resident_without_open_alerts_is_left_out(aws):
    resident_with_alert(aws)
    put_resident(aws.ddb, RESIDENT_B, NAME_B, HOME_B)
    put_home(aws.ddb)
    put_home(aws.ddb, HOME_B)

    assert [h["home_id"] for h in call()["homes"]] == [HOME_A]
    assert home_reads(aws) == [f"HOME#{HOME_A}"]


@pytest.mark.parametrize("status", ["resolved", "cancelled", "closed"])
def test_alert_not_open_or_acknowledged_adds_no_home(aws, status):
    # Still on the open index (a stale open_pk), but its status is not open-list.
    resident_with_alert(aws, status=status)
    put_home(aws.ddb)

    assert call()["homes"] == []


def test_homes_are_distinct_and_ordered_by_home_id(aws):
    resident_with_alert(aws, RESIDENT_C, HOME_C, alert_id=ALERT_1)
    resident_with_alert(aws, RESIDENT_A, HOME_A, alert_id=ALERT_2)
    put_alert(aws.ddb, resident_id=RESIDENT_A, alert_id=ALERT_3, alert_type="fall")
    # Resident B shares resident C's home.
    resident_with_alert(
        aws, RESIDENT_B, HOME_C, alert_id="6f1c2e0a-5b3d-5e8f-9a47-0000000000b1"
    )
    put_home(aws.ddb, HOME_A)
    put_home(aws.ddb, HOME_C)

    homes = call()["homes"]

    assert [h["home_id"] for h in homes] == [HOME_A, HOME_C]
    assert sorted(home_reads(aws)) == [f"HOME#{HOME_A}", f"HOME#{HOME_C}"]


def test_home_entry_has_exactly_the_four_documented_keys(aws):
    resident_with_alert(aws)
    put_home(aws.ddb)

    (home,) = call()["homes"]

    assert set(home) == {"home_id", "address", "lat", "lng"}
    assert isinstance(home["lat"], float) and isinstance(home["lng"], float)


# ---- criterion 8: nothing from unassigned alerts or residents without a home ----


def test_unassigned_alert_adds_no_home_and_no_read(aws):
    put_alert(aws.ddb, resident_id="unassigned", device_id="wearable-0042")
    put_home(aws.ddb)

    body = call()

    assert body["homes"] == []
    assert home_reads(aws) == []


@pytest.mark.parametrize("home_id", [None, "", "../home-0001", Decimal(1), "HOME-0001"])
def test_resident_without_a_valid_home_id_adds_nothing(aws, home_id):
    resident_with_alert(aws, home_id=home_id)
    put_home(aws.ddb)

    assert call()["homes"] == []
    assert home_reads(aws) == []


def test_alert_of_a_resident_without_a_profile_adds_nothing(aws):
    put_alert(aws.ddb, resident_id=RESIDENT_NO_PROFILE)
    put_home(aws.ddb)

    assert call()["homes"] == []
    assert home_reads(aws) == []


def test_home_with_no_item_is_left_out(aws):
    resident_with_alert(aws)

    assert call()["homes"] == []
    assert home_reads(aws) == [f"HOME#{HOME_A}"]


# ---- criterion 2: valid items only ----


@pytest.mark.parametrize(
    ("fields", "bad"),
    [
        ({"address": ""}, ["address"]),
        ({"address": "x" * 201}, ["address"]),
        ({"address": None}, ["address"]),
        ({"address": Decimal(5)}, ["address"]),
        ({"address": ["Test address 1"]}, ["address"]),
        ({"lat": Decimal("1.1599")}, ["lat"]),
        ({"lat": Decimal("1.4801")}, ["lat"]),
        ({"lng": Decimal("103.5999")}, ["lng"]),
        ({"lng": Decimal("104.1001")}, ["lng"]),
        ({"lat": None}, ["lat"]),
        ({"lng": None}, ["lng"]),
        ({"lat": "1.3612"}, ["lat"]),
        ({"lat": True}, ["lat"]),
        ({"lng": False}, ["lng"]),
        ({"lat": Decimal("-1.3612"), "lng": Decimal("-103.8031")}, ["lat", "lng"]),
        ({"address": None, "lat": None, "lng": None}, ["address", "lat", "lng"]),
    ],
)
def test_invalid_home_item_is_left_out_and_its_field_names_logged(
    aws, logs, fields, bad
):
    resident_with_alert(aws)
    put_home(
        aws.ddb, **{"address": HOME_ADDRESS, "lat": HOME_LAT, "lng": HOME_LNG, **fields}
    )

    body = call()

    assert body["homes"] == []
    assert "homes_error" not in body
    (warning,) = [r for r in logs() if r["message"] == "invalid home"]
    assert warning["home_id"] == HOME_A
    assert warning["fields"] == bad


@pytest.mark.parametrize(
    "fields",
    [
        {"address": "x"},
        {"address": "x" * 200},
        {"lat": Decimal("1.16"), "lng": Decimal("103.6")},
        {"lat": Decimal("1.48"), "lng": Decimal("104.1")},
        {"lng": 104},  # an integer number
    ],
)
def test_home_at_each_boundary_is_valid(aws, fields):
    resident_with_alert(aws)
    put_home(aws.ddb, **fields)

    (home,) = call()["homes"]

    expected = {"address": HOME_ADDRESS, "lat": HOME_LAT, "lng": HOME_LNG, **fields}
    assert home == {
        "home_id": HOME_A,
        "address": expected["address"],
        "lat": float(expected["lat"]),
        "lng": float(expected["lng"]),
    }


def test_one_invalid_home_does_not_drop_the_others(aws):
    resident_with_alert(aws, RESIDENT_A, HOME_A, alert_id=ALERT_1)
    resident_with_alert(aws, RESIDENT_B, HOME_B, alert_id=ALERT_2)
    put_home(aws.ddb, HOME_A, lat=Decimal(2))
    put_home(aws.ddb, HOME_B)

    homes = call()["homes"]

    assert [h["home_id"] for h in homes] == [HOME_B]


# ---- criterion 6: the read ----


def test_home_read_is_projected_and_eventually_consistent(aws):
    resident_with_alert(aws)
    put_home(aws.ddb)

    call()

    (request,) = aws.batch_gets()
    ((table, spec),) = request["RequestItems"].items()
    assert table == "silvercare-registry"
    assert spec["Keys"] == [{"pk": {"S": f"HOME#{HOME_A}"}, "sk": {"S": "PROFILE"}}]
    names = spec["ExpressionAttributeNames"]
    read = {names[p.strip()] for p in spec["ProjectionExpression"].split(",")}
    assert read == {"pk", "address", "lat", "lng"}
    assert spec.get("ConsistentRead", False) is False


def test_home_reads_are_chunked_by_100(aws):
    home_ids = [f"home-{n:04d}" for n in range(1, 151)]

    handler.batch_get_homes(home_ids)

    sizes = [
        len(spec["Keys"])
        for request in aws.batch_gets()
        for spec in request["RequestItems"].values()
    ]
    assert sizes == [100, 50]


def test_unprocessed_home_keys_are_retried(aws, monkeypatch):
    resident_with_alert(aws)
    put_home(aws.ddb)
    real = aws.client
    attempts = []

    class UnprocessedOnce:
        def __getattr__(self, name):
            return getattr(real, name)

        def batch_get_item(self, RequestItems):
            attempts.append(RequestItems)
            if len(attempts) == 1:
                return {"Responses": {}, "UnprocessedKeys": RequestItems}
            return real.batch_get_item(RequestItems=RequestItems)

    monkeypatch.setattr(handler, "dynamodb_client", lambda: UnprocessedOnce())
    monkeypatch.setattr(handler.time, "sleep", lambda _s: None)

    body = call()

    assert body["homes"] == [HOME_A_VIEW]
    assert len(attempts) == 2


# ---- criterion 6: the 5-minute cache ----


def test_second_call_inside_5_minutes_does_not_read_homes(aws, monkeypatch):
    clock = at_time(monkeypatch)
    resident_with_alert(aws)
    put_home(aws.ddb)

    call()
    clock[0] += 299.9
    body = call()

    assert body["homes"] == [HOME_A_VIEW]
    assert home_reads(aws) == [f"HOME#{HOME_A}"]


def test_cached_home_is_served_even_after_the_item_changes(aws, monkeypatch):
    clock = at_time(monkeypatch)
    resident_with_alert(aws)
    put_home(aws.ddb)

    call()
    put_home(aws.ddb, address="Test address changed (synthetic)")
    clock[0] += 60

    assert call()["homes"] == [HOME_A_VIEW]


def test_home_is_read_again_after_5_minutes(aws, monkeypatch):
    clock = at_time(monkeypatch)
    resident_with_alert(aws)
    put_home(aws.ddb)

    call()
    put_home(aws.ddb, address="Test address changed (synthetic)")
    clock[0] += 300
    body = call()

    assert body["homes"][0]["address"] == "Test address changed (synthetic)"
    assert home_reads(aws) == [f"HOME#{HOME_A}"] * 2


def test_no_item_result_is_cached_too(aws, monkeypatch):
    clock = at_time(monkeypatch)
    resident_with_alert(aws)

    call()
    put_home(aws.ddb)
    clock[0] += 120
    body = call()

    assert body["homes"] == []
    assert home_reads(aws) == [f"HOME#{HOME_A}"]


def test_invalid_item_result_is_cached_too(aws, monkeypatch):
    clock = at_time(monkeypatch)
    resident_with_alert(aws)
    put_home(aws.ddb, address="")

    call()
    clock[0] += 120
    call()

    assert home_reads(aws) == [f"HOME#{HOME_A}"]


def test_only_uncached_homes_are_read(aws, monkeypatch):
    clock = at_time(monkeypatch)
    resident_with_alert(aws, RESIDENT_A, HOME_A, alert_id=ALERT_1)
    put_home(aws.ddb, HOME_A)
    put_home(aws.ddb, HOME_B)

    call()
    resident_with_alert(aws, RESIDENT_B, HOME_B, alert_id=ALERT_2)
    clock[0] += 10
    body = call()

    assert [h["home_id"] for h in body["homes"]] == [HOME_A, HOME_B]
    assert home_reads(aws) == [f"HOME#{HOME_A}", f"HOME#{HOME_B}"]


# ---- criterion 7: a failed read ----


def test_failed_home_read_gives_homes_error_and_keeps_the_rest(aws):
    resident_with_alert(aws)
    put_home(aws.ddb)
    expected = call(DOCTOR)
    aws.client.fail["batch_get_item"] = throttled()

    body = call()

    assert body["homes"] == [] and body["homes_error"] is True
    assert {k: body[k] for k in ("residents", "alerts")} == expected
    assert expected["alerts"] and expected["residents"]


def test_failed_home_read_is_logged_with_outcome_and_code_only(aws, logs):
    resident_with_alert(aws)
    aws.client.fail["batch_get_item"] = throttled()

    call()

    records = logs()
    (warning,) = [r for r in records if r["message"] == "home read failed"]
    assert warning["error"] == "ProvisionedThroughputExceededException"
    (outcome,) = [r for r in records if "outcome" in r]
    assert outcome["outcome"] == "ok"
    assert outcome["homes_error"] is True
    assert outcome["homes"] == 0
    assert "qz-8812" not in json.dumps(records)


def test_persistently_unprocessed_home_keys_give_homes_error(aws, monkeypatch):
    resident_with_alert(aws)
    put_home(aws.ddb)
    real = aws.client

    class Unprocessed:
        def __getattr__(self, name):
            return getattr(real, name)

        def batch_get_item(self, RequestItems):
            return {"Responses": {}, "UnprocessedKeys": RequestItems}

    monkeypatch.setattr(handler, "dynamodb_client", lambda: Unprocessed())
    monkeypatch.setattr(handler.time, "sleep", lambda _s: None)

    body = call()

    assert body["homes"] == [] and body["homes_error"] is True
    assert body["alerts"]


def test_failed_home_read_is_not_cached(aws):
    resident_with_alert(aws)
    put_home(aws.ddb)
    aws.client.fail["batch_get_item"] = throttled()
    call()
    del aws.client.fail["batch_get_item"]

    body = call()

    assert body["homes"] == [HOME_A_VIEW]
    assert "homes_error" not in body


def test_doctor_never_gets_homes_error(aws):
    resident_with_alert(aws)
    aws.client.fail["batch_get_item"] = throttled()

    body = call(DOCTOR)

    assert "homes_error" not in body and "homes" not in body


# ---- criterion 9: logs ----


def test_ok_log_has_home_counts(aws, logs):
    resident_with_alert(aws, RESIDENT_A, HOME_A, alert_id=ALERT_1)
    resident_with_alert(aws, RESIDENT_B, HOME_B, alert_id=ALERT_2)
    resident_with_alert(aws, RESIDENT_C, HOME_C, alert_id=ALERT_3)
    put_home(aws.ddb, HOME_A)
    put_home(aws.ddb, HOME_B, lat=Decimal(9))

    call()

    (outcome,) = [r for r in logs() if "outcome" in r]
    assert (outcome["homes"], outcome["homes_missing"]) == (1, 2)
    assert "homes_error" not in outcome


def test_doctor_log_has_no_home_fields(aws, logs):
    resident_with_alert(aws)

    call(DOCTOR)

    (outcome,) = [r for r in logs() if "outcome" in r]
    assert not {"homes", "homes_missing", "homes_error"} & set(outcome)


def test_logs_never_carry_an_address_or_a_coordinate(aws, logs):
    resident_with_alert(aws, RESIDENT_A, HOME_A, alert_id=ALERT_1)
    resident_with_alert(aws, RESIDENT_B, HOME_B, alert_id=ALERT_2)
    put_home(aws.ddb, HOME_A)
    # Invalid: logged as a warning, but by field name only.
    put_home(
        aws.ddb,
        HOME_B,
        address="Test address qz-6640 (synthetic)",
        lat=Decimal("1.4917"),
    )

    call()

    records = logs()
    assert any(r["message"] == "invalid home" for r in records)
    text = UUID_RE.sub("<uuid>", json.dumps(records))
    for value in ("qz-5521", "qz-6640", "Test address", "1.3612", "103.8031", "1.4917"):
        assert value not in text, value
