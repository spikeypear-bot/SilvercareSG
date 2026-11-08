"""Device shadow writes after a thresholds save (docs/features/device-alert-limits.md,
criteria 5a-5c), against moto's DynamoDB and IoT data."""

from __future__ import annotations

import json
import re

import handler
import pytest
from conftest import client_error
from events import (
    BEDROOM,
    NOW,
    RESIDENT_A,
    RESIDENT_B,
    WEARABLE_1,
    WEARABLE_2,
    WEARABLE_3,
    api_event,
    body,
    put_device,
    put_thresholds,
)

FIXED = {"hr_bpm": {"min": 45, "max": 130}, "spo2_pct": {"min": 88}}
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def limits(hr_min, hr_max, spo2):
    return {"hr_bpm": {"min": hr_min, "max": hr_max}, "spo2_pct": {"min": spo2}}


def desired(document):
    return (document or {}).get("state", {}).get("desired", {}).get("limits")


def seed(aws) -> None:
    """Resident A wears wearable-0001 and -0003 and has bedroom-0001; resident B wears
    wearable-0002."""
    put_thresholds(aws.ddb, RESIDENT_A)
    put_thresholds(aws.ddb, RESIDENT_B)
    for device_id, resident in (
        (WEARABLE_1, RESIDENT_A),
        (WEARABLE_3, RESIDENT_A),
        (BEDROOM, RESIDENT_A),
        (WEARABLE_2, RESIDENT_B),
    ):
        put_device(aws.ddb, device_id, resident)


def save(hr=(57, 113), spo2=93, **kw):
    resp = handler.handle(api_event(payload=body(hr=hr, spo2=spo2), **kw), NOW)
    return resp["statusCode"], json.loads(resp["body"])


# ---- 5a ----


def test_changed_save_writes_desired_limits_to_each_of_the_residents_wearables(aws):
    seed(aws)

    status, out = save()

    assert status == 200
    assert out["changed"] is True
    assert sorted(aws.iot.things("update_thing_shadow")) == [WEARABLE_1, WEARABLE_3]
    assert desired(aws.shadow(WEARABLE_1)) == limits(57, 113, 93)
    assert desired(aws.shadow(WEARABLE_3)) == limits(57, 113, 93)


def test_bedroom_and_other_residents_wearables_are_not_written(aws):
    seed(aws)

    save()

    assert aws.shadow(BEDROOM) is None
    assert aws.shadow(WEARABLE_2) is None


def test_write_is_only_the_desired_limits_on_the_classic_shadow(aws):
    seed(aws)

    save()

    for kwargs in aws.iot.named("update_thing_shadow"):
        assert "shadowName" not in kwargs
        assert json.loads(kwargs["payload"]) == {
            "state": {"desired": {"limits": limits(57, 113, 93)}}
        }
    assert {name for name, _ in aws.iot.calls} == {"update_thing_shadow"}


@pytest.mark.parametrize(
    ("hr", "spo2", "expected"),
    [
        ((40, 140), 85, FIXED),
        ((45, 130), 88, FIXED),
        ((46, 129), 89, limits(46, 129, 89)),
        ((50, 110), 92, limits(50, 110, 92)),
        ((135, 140), 95, limits(45, 130, 95)),
    ],
)
def test_limits_are_the_stricter_of_the_saved_thresholds_and_fixed(
    aws, hr, spo2, expected
):
    seed(aws)

    save(hr=hr, spo2=spo2)

    assert desired(aws.shadow(WEARABLE_1)) == expected


def test_unchanged_save_writes_no_shadow_and_queries_no_devices(aws):
    seed(aws)

    status, out = save(hr=(50, 120), spo2=92)

    assert status == 200
    assert out["changed"] is False
    assert aws.iot.calls == []
    assert aws.db.named("query") == []


def test_skin_temperature_only_change_still_writes_the_shadows(aws):
    seed(aws)

    resp = handler.handle(
        api_event(payload=body(skin=(31.0, 37.5))), NOW
    )  # hr/spo2 unchanged

    assert resp["statusCode"] == 200
    assert desired(aws.shadow(WEARABLE_1)) == limits(50, 120, 92)


def test_resident_without_wearables_writes_no_shadow(aws):
    put_thresholds(aws.ddb, RESIDENT_A)
    put_device(aws.ddb, BEDROOM, RESIDENT_A)

    assert save()[0] == 200
    assert aws.iot.calls == []


def test_query_is_pattern_10_naming_only_gsi1_sk_and_device_type(aws):
    seed(aws)

    save()

    (query,) = aws.db.named("query")
    assert query["IndexName"] == "by-resident"
    assert query["Select"] == "SPECIFIC_ATTRIBUTES"
    assert query["ExpressionAttributeValues"] == {
        ":pk": {"S": f"RESIDENT#{RESIDENT_A}"},
        ":device": {"S": "DEVICE#"},
    }
    names = query["ExpressionAttributeNames"]
    projected = {names[n.strip()] for n in query["ProjectionExpression"].split(",")}
    assert projected == {"gsi1_sk", "device_type"}
    # IAM (infra/api.tf, RegistryByResidentIndex) allows only these names.
    assert set(names.values()) <= {"gsi1_pk", "gsi1_sk", "pk", "sk", "device_type"}


# ---- 5b: failures never change the answer ----


def test_failed_query_still_returns_200_and_logs_the_error_code(aws, logs):
    seed(aws)
    aws.db.fail["query"] = client_error(
        "ProvisionedThroughputExceededException", "Query"
    )

    status, out = save()

    assert status == 200
    assert out["changed"] is True
    assert aws.iot.calls == []
    (record,) = [r for r in logs() if r["message"] == "shadows"]
    assert record["outcome"] == "query_failed"
    assert record["error"] == "ProvisionedThroughputExceededException"


@pytest.mark.parametrize(
    "exc",
    [
        client_error("ThrottlingException", "UpdateThingShadow"),
        client_error("ServiceUnavailableException", "UpdateThingShadow"),
        TimeoutError("synthetic"),
    ],
)
def test_failed_shadow_write_still_returns_200_and_others_are_written(aws, logs, exc):
    seed(aws)
    aws.iot.fail_things[WEARABLE_1] = exc

    status, out = save()

    assert status == 200
    assert out["changed"] is True
    assert desired(aws.shadow(WEARABLE_3)) == limits(57, 113, 93)
    (record,) = [r for r in logs() if r["message"] == "shadows"]
    assert record["outcome"] == "failed"
    assert record["written"] == 1
    assert record["failed"] == 1
    assert record["failures"] == [
        {"device_id": WEARABLE_1, "error": _code(exc)},
    ]


def _code(exc):
    response = getattr(exc, "response", None)
    return response["Error"]["Code"] if response else type(exc).__name__


def test_iot_client_that_cannot_be_created_still_returns_200(aws, monkeypatch):
    seed(aws)

    def broken():
        raise KeyError("IOT_DATA_ENDPOINT")

    monkeypatch.setattr(handler, "iot_data_client", broken)

    status, out = save()

    assert status == 200
    assert out["changed"] is True


# ---- 5c: logs ----


def test_ok_shadow_log_has_counts(aws, logs):
    seed(aws)

    save()

    (record,) = [r for r in logs() if r["message"] == "shadows"]
    assert record["outcome"] == "ok"
    assert record["written"] == 2
    assert record["failed"] == 0
    assert "failures" not in record


def test_logs_never_carry_limit_or_threshold_values(aws, logs):
    seed(aws)
    aws.iot.fail_things[WEARABLE_1] = client_error(
        "ThrottlingException", "UpdateThingShadow"
    )

    save(hr=(57, 113), spo2=93)

    text = UUID_RE.sub("<uuid>", json.dumps(logs()))
    for value in ("57", "113", "93", "limits", 'hr_bpm"', "qz-6631", "Test Doctor"):
        assert value not in text


def test_iot_client_has_one_attempt_and_a_two_second_timeout():
    handler.iot_data_client.cache_clear()
    try:
        client = handler.iot_data_client()
    finally:
        handler.iot_data_client.cache_clear()
    assert client.meta.config.connect_timeout == 2
    assert client.meta.config.read_timeout == 2
    assert client.meta.config.retries["total_max_attempts"] == 1
