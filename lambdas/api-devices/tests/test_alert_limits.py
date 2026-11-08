"""Each wearable's `alert_limits` from its device shadow (docs/features/
device-alert-limits.md, criteria 13-15): fleet.alert_limits_view without AWS, then
GET /devices against moto's IoT data shadows."""

from __future__ import annotations

import json
import re

import fleet
import handler
import pytest
from botocore.exceptions import ClientError
from conftest import put_shadow
from events import BEDROOM, NOW, SUB, WEARABLE, WEARABLE_2, api_event, seed_fleet

FIXED = {"hr_bpm": {"min": 45, "max": 130}, "spo2_pct": {"min": 88}}
# Distinctive numbers, so a check that they never reach the logs means something.
TIGHT = {"hr_bpm": {"min": 57, "max": 113}, "spo2_pct": {"min": 93}}
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def doc(desired=None, reported=None) -> dict:
    state = {}
    if desired is not None:
        state["desired"] = {"limits": desired}
    if reported is not None:
        state["reported"] = {"limits": reported}
    return {"state": state, "version": 3}


def call(event):
    resp = handler.handle(event, NOW)
    return resp["statusCode"], json.loads(resp["body"]), resp


def by_id(body) -> dict:
    return {d["device_id"]: d for d in body["devices"]}


def throttled() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Synthetic qz-4417"}},
        "GetThingShadow",
    )


# ---- status rules (criterion 14), pure ----


def test_equal_valid_desired_and_reported_is_in_sync():
    view = fleet.alert_limits_view(doc(TIGHT, TIGHT), staff=True)
    assert view == {"status": "in_sync", "desired": TIGHT, "reported": TIGHT}


def test_reported_missing_is_pending():
    view = fleet.alert_limits_view(doc(TIGHT), staff=True)
    assert view == {"status": "pending", "desired": TIGHT, "reported": None}


def test_reported_different_is_pending():
    view = fleet.alert_limits_view(doc(TIGHT, FIXED), staff=True)
    assert view == {"status": "pending", "desired": TIGHT, "reported": FIXED}


def test_invalid_reported_is_pending_and_returned_as_null():
    bad = {"hr_bpm": {"min": 45, "max": 131}, "spo2_pct": {"min": 88}}
    view = fleet.alert_limits_view(doc(FIXED, bad), staff=True)
    assert view == {"status": "pending", "desired": FIXED, "reported": None}


def test_no_shadow_is_unknown():
    view = fleet.alert_limits_view(None, staff=True)
    assert view == {"status": "unknown", "desired": None, "reported": None}


def test_desired_missing_is_unknown_even_with_reported():
    view = fleet.alert_limits_view(doc(reported=FIXED), staff=True)
    assert view == {"status": "unknown", "desired": None, "reported": FIXED}


@pytest.mark.parametrize(
    "desired",
    [
        {"hr_bpm": {"min": 44, "max": 130}, "spo2_pct": {"min": 88}},
        {"hr_bpm": {"min": 45, "max": 131}, "spo2_pct": {"min": 88}},
        {"hr_bpm": {"min": 45, "max": 130}, "spo2_pct": {"min": 87}},
        {"hr_bpm": {"min": 45, "max": 130}, "spo2_pct": {"min": 101}},
        {"hr_bpm": {"min": 100, "max": 100}, "spo2_pct": {"min": 88}},
        {"hr_bpm": {"min": 45.0, "max": 130}, "spo2_pct": {"min": 88}},
        {"hr_bpm": {"min": True, "max": 130}, "spo2_pct": {"min": 88}},
        {"hr_bpm": {"min": "45", "max": 130}, "spo2_pct": {"min": 88}},
        {"hr_bpm": {"min": 45, "max": 130}, "spo2_pct": {"min": 88, "max": 100}},
        {"hr_bpm": {"min": 45, "max": 130}},
        {**FIXED, "skin_temp_c": {"min": 30, "max": 38}},
        [45, 130, 88],
        "45-130",
    ],
)
def test_invalid_desired_is_unknown_and_returned_as_null(desired):
    view = fleet.alert_limits_view(doc(desired, FIXED), staff=True)
    assert view == {"status": "unknown", "desired": None, "reported": FIXED}


@pytest.mark.parametrize(
    "document",
    [{}, {"state": None}, {"state": []}, {"state": {"desired": "x"}}, [], "x", 7],
)
def test_malformed_document_is_unknown(document):
    view = fleet.alert_limits_view(document, staff=True)
    assert view == {"status": "unknown", "desired": None, "reported": None}


@pytest.mark.parametrize(
    ("document", "status"),
    [(doc(TIGHT, TIGHT), "in_sync"), (doc(TIGHT, FIXED), "pending"), (None, "unknown")],
)
def test_ops_view_has_the_status_only(document, status):
    assert fleet.alert_limits_view(document, staff=False) == {"status": status}


def test_valid_limits_at_their_bounds_are_accepted():
    edge = {"hr_bpm": {"min": 129, "max": 130}, "spo2_pct": {"min": 100}}
    assert fleet.alert_limits_view(doc(edge, edge), staff=True)["status"] == "in_sync"


# ---- GET /devices (criteria 13-15) ----


def test_staff_get_desired_reported_and_status_per_wearable(aws):
    seed_fleet(aws.ddb)
    put_shadow(WEARABLE, {"desired": {"limits": TIGHT}, "reported": {"limits": FIXED}})
    put_shadow(
        WEARABLE_2, {"desired": {"limits": FIXED}, "reported": {"limits": FIXED}}
    )

    status, body, _ = call(api_event(groups=["doctor"]))

    assert status == 200
    devices = by_id(body)
    assert devices[WEARABLE]["alert_limits"] == {
        "status": "pending",
        "desired": TIGHT,
        "reported": FIXED,
    }
    assert devices[WEARABLE_2]["alert_limits"] == {
        "status": "in_sync",
        "desired": FIXED,
        "reported": FIXED,
    }


def test_bedroom_sensor_alert_limits_is_null_and_its_shadow_is_never_read(aws):
    seed_fleet(aws.ddb)
    put_shadow(BEDROOM, {"desired": {"limits": FIXED}})

    _, body, _ = call(api_event(groups=["nurse"]))

    assert by_id(body)[BEDROOM]["alert_limits"] is None
    assert sorted(aws.iot.calls) == [
        ("get_thing_shadow", WEARABLE),
        ("get_thing_shadow", WEARABLE_2),
    ]


def test_shadow_reads_are_read_only(aws):
    seed_fleet(aws.ddb)

    call(api_event(groups=["doctor"]))

    assert {name for name, _ in aws.iot.calls} == {"get_thing_shadow"}


@pytest.mark.parametrize("groups", [["ops"], "[ops]"])
def test_ops_get_the_status_only_never_the_numbers(aws, groups):
    seed_fleet(aws.ddb)
    put_shadow(WEARABLE, {"desired": {"limits": TIGHT}, "reported": {"limits": TIGHT}})

    status, body, resp = call(api_event(groups=groups))

    assert status == 200
    devices = by_id(body)
    assert devices[WEARABLE]["alert_limits"] == {"status": "in_sync"}
    assert devices[WEARABLE_2]["alert_limits"] == {"status": "unknown"}
    assert devices[BEDROOM]["alert_limits"] is None
    for value in ("57", "113", "93", "hr_bpm", "spo2_pct", "desired", "reported"):
        assert value not in UUID_RE.sub("", resp["body"])


def test_shadow_read_failure_gives_unknown_and_still_200(aws, logs):
    seed_fleet(aws.ddb)
    put_shadow(WEARABLE, {"desired": {"limits": FIXED}, "reported": {"limits": FIXED}})
    put_shadow(
        WEARABLE_2, {"desired": {"limits": FIXED}, "reported": {"limits": FIXED}}
    )
    aws.iot.fail[WEARABLE] = throttled()

    status, body, _ = call(api_event(groups=["doctor"]))

    assert status == 200
    devices = by_id(body)
    assert devices[WEARABLE]["alert_limits"] == {
        "status": "unknown",
        "desired": None,
        "reported": None,
    }
    assert devices[WEARABLE_2]["alert_limits"]["status"] == "in_sync"
    (record,) = logs()
    assert record["outcome"] == "ok"
    assert record["shadow_read_failures"] == 1


def test_no_shadow_is_unknown_but_not_a_failed_read(aws, logs):
    seed_fleet(aws.ddb)

    call(api_event(groups=["doctor"]))

    (record,) = logs()
    assert record["shadow_read_failures"] == 0


def test_every_shadow_read_failing_still_returns_200(aws, logs):
    seed_fleet(aws.ddb)
    aws.iot.fail[WEARABLE] = throttled()
    aws.iot.fail[WEARABLE_2] = TimeoutError("synthetic")

    status, body, _ = call(api_event(groups=["ops"]))

    assert status == 200
    assert by_id(body)[WEARABLE]["alert_limits"] == {"status": "unknown"}
    assert by_id(body)[WEARABLE_2]["alert_limits"] == {"status": "unknown"}
    (record,) = logs()
    assert record["shadow_read_failures"] == 2


def test_client_that_cannot_be_created_gives_unknown_not_500(aws, monkeypatch):
    seed_fleet(aws.ddb)

    def broken():
        raise KeyError("IOT_DATA_ENDPOINT")

    monkeypatch.setattr(handler, "iot_data_client", broken)

    status, body, _ = call(api_event(groups=["doctor"]))

    assert status == 200
    assert by_id(body)[WEARABLE]["alert_limits"]["status"] == "unknown"


def test_unparseable_shadow_payload_gives_unknown(aws, monkeypatch):
    seed_fleet(aws.ddb)

    class Garbage:
        def get_thing_shadow(self, **kwargs):
            import io

            return {"payload": io.BytesIO(b"not json {")}

    monkeypatch.setattr(handler, "iot_data_client", Garbage)

    status, body, _ = call(api_event(groups=["doctor"]))

    assert status == 200
    assert by_id(body)[WEARABLE]["alert_limits"]["status"] == "unknown"


def test_logs_never_carry_limit_values_or_device_ids(aws, logs):
    seed_fleet(aws.ddb)
    put_shadow(WEARABLE, {"desired": {"limits": TIGHT}, "reported": {"limits": TIGHT}})
    aws.iot.fail[WEARABLE_2] = throttled()

    call(api_event(groups=["doctor"]))

    text = json.dumps(logs())
    assert SUB in text
    text = UUID_RE.sub("<uuid>", text)
    for value in ("57", "113", "93", "limits", "wearable", "qz-4417"):
        assert value not in text


def test_no_wearables_reads_no_shadow(aws):
    call(api_event(groups=["doctor"]))

    assert aws.iot.calls == []


def test_iot_client_has_one_attempt_and_a_two_second_timeout(monkeypatch):
    # Built against moto's host name; this only inspects the config, no call is made.
    handler.iot_data_client.cache_clear()
    try:
        config = handler.iot_data_client().meta.config
    finally:
        handler.iot_data_client.cache_clear()
    assert config.connect_timeout == 2
    assert config.read_timeout == 2
    assert config.retries["total_max_attempts"] == 1
