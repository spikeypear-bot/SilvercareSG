"""silvercare-shadow-sync runs against moto: registry reads, shadow reads and writes,
failures, and logs (docs/features/device-alert-limits.md, criteria 6-11)."""

from __future__ import annotations

import json
import re

import handler
import pytest
from conftest import client_error
from events import (
    BEDROOM,
    DEFAULTS,
    FIXED,
    RESIDENT_A,
    RESIDENT_B,
    WEARABLE_1,
    WEARABLE_2,
    WEARABLE_3,
    limits,
    put_device,
    put_raw,
    put_thresholds,
)

POWERTOOLS_KEYS = {"level", "message", "service", "sampling_rate", "xray_trace_id"}
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Distinctive thresholds, so "never in the logs" checks mean something.
TIGHT = limits(57, 113, 93)


def desired(document) -> dict | None:
    return (document or {}).get("state", {}).get("desired", {}).get("limits")


def seed(aws) -> None:
    """Resident A (tight thresholds) wears wearable-0001 and has bedroom-0001;
    wearable-0002 is a spare."""
    put_thresholds(aws.ddb, RESIDENT_A, hr=(57, 113), spo2=93)
    put_device(aws.ddb, WEARABLE_1, RESIDENT_A)
    put_device(aws.ddb, BEDROOM, RESIDENT_A)
    put_device(aws.ddb, WEARABLE_2, None)


def run(event=None):
    return handler.lambda_handler({} if event is None else event, None)


# ---- valid runs ----


def test_wearable_without_shadow_gets_its_desired_limits(aws):
    seed(aws)

    counts = run()

    assert desired(aws.shadow(WEARABLE_1)) == TIGHT
    assert desired(aws.shadow(WEARABLE_2)) == FIXED
    assert counts == {
        "wearables": 2,
        "updated": 2,
        "unchanged": 0,
        "failed": 0,
        "skipped": 0,
    }


def test_write_is_desired_limits_only(aws):
    seed(aws)

    run()

    payloads = [
        json.loads(kw["payload"]) for kw in aws.iot.named("update_thing_shadow")
    ]
    assert payloads == [
        {"state": {"desired": {"limits": TIGHT}}},
        {"state": {"desired": {"limits": FIXED}}},
    ]


def test_equal_desired_limits_are_not_rewritten(aws):
    seed(aws)
    aws.put_shadow(WEARABLE_1, {"desired": {"limits": TIGHT}})
    aws.put_shadow(WEARABLE_2, {"desired": {"limits": FIXED}})

    counts = run()

    assert aws.iot.named("update_thing_shadow") == []
    assert counts["unchanged"] == 2
    assert counts["updated"] == 0


def test_second_run_writes_nothing(aws):
    seed(aws)
    run()
    aws.iot.calls.clear()

    counts = run()

    assert aws.iot.named("update_thing_shadow") == []
    assert counts["unchanged"] == 2


def test_different_desired_limits_are_replaced_and_reported_is_kept(aws):
    seed(aws)
    aws.put_shadow(
        WEARABLE_1, {"desired": {"limits": FIXED}, "reported": {"limits": FIXED}}
    )

    run()

    document = aws.shadow(WEARABLE_1)
    assert desired(document) == TIGHT
    assert document["state"]["reported"] == {"limits": FIXED}


@pytest.mark.parametrize(
    "bad",
    [
        {**TIGHT, "skin_temp_c": {"min": 30}},
        limits(57, 113, 87),
        {"hr_bpm": {"min": "57", "max": 113}, "spo2_pct": {"min": 93}},
    ],
)
def test_invalid_desired_limits_are_replaced(aws, bad):
    seed(aws)
    aws.put_shadow(WEARABLE_1, {"desired": {"limits": bad}})

    run()

    assert aws.iot.things("update_thing_shadow")[0] == WEARABLE_1
    # moto merges desired maps like AWS, so an extra key may stay; the limits are new.
    assert desired(aws.shadow(WEARABLE_1))["hr_bpm"] == TIGHT["hr_bpm"]


def test_bedroom_shadow_is_never_read_or_written(aws):
    seed(aws)
    aws.put_shadow(BEDROOM, {"desired": {"note": "synthetic"}})

    run()

    for name in ("get_thing_shadow", "update_thing_shadow"):
        assert BEDROOM not in aws.iot.things(name)
    assert aws.shadow(BEDROOM)["state"]["desired"] == {"note": "synthetic"}


def test_only_get_and_update_shadow_are_called(aws):
    seed(aws)

    run()

    assert {name for name, _ in aws.iot.calls} <= {
        "get_thing_shadow",
        "update_thing_shadow",
    }
    for _, kwargs in aws.iot.calls:
        assert "shadowName" not in kwargs


def test_wearable_whose_resident_has_no_thresholds_gets_the_defaults(aws):
    put_device(aws.ddb, WEARABLE_1, RESIDENT_B)

    run()

    assert desired(aws.shadow(WEARABLE_1)) == DEFAULTS


def test_tightened_hr_max_reaches_the_shadow_in_one_run(aws):
    # criterion 9
    put_thresholds(aws.ddb, RESIDENT_A)
    put_device(aws.ddb, WEARABLE_1, RESIDENT_A)
    run()
    assert desired(aws.shadow(WEARABLE_1))["hr_bpm"]["max"] == 120

    put_thresholds(aws.ddb, RESIDENT_A, hr=(50, 110))
    run()

    assert desired(aws.shadow(WEARABLE_1))["hr_bpm"]["max"] == 110


def test_moved_wearable_gets_the_new_residents_limits(aws):
    put_thresholds(aws.ddb, RESIDENT_A, hr=(57, 113), spo2=93)
    put_thresholds(aws.ddb, RESIDENT_B, hr=(60, 100), spo2=95)
    put_device(aws.ddb, WEARABLE_1, RESIDENT_A)
    run()

    put_device(aws.ddb, WEARABLE_1, RESIDENT_B)
    run()

    assert desired(aws.shadow(WEARABLE_1)) == limits(60, 100, 95)


def test_unassigned_wearable_goes_back_to_the_fixed_limits(aws):
    seed(aws)
    run()

    put_device(aws.ddb, WEARABLE_1, None)
    run()

    assert desired(aws.shadow(WEARABLE_1)) == FIXED


def test_two_wearables_of_one_resident_read_one_thresholds_key(aws):
    seed(aws)
    put_device(aws.ddb, WEARABLE_3, RESIDENT_A)

    run()

    (batch,) = aws.db.named("batch_get_item")
    keys = batch["RequestItems"]["silvercare-registry"]["Keys"]
    assert keys == [
        {"pk": {"S": f"THRESHOLDS#{RESIDENT_A}"}, "sk": {"S": "THRESHOLDS"}}
    ]
    assert desired(aws.shadow(WEARABLE_3)) == TIGHT


def test_no_assigned_wearables_reads_no_thresholds(aws):
    put_device(aws.ddb, WEARABLE_2, None)
    put_device(aws.ddb, BEDROOM, RESIDENT_A)

    run()

    assert aws.db.named("batch_get_item") == []


def test_no_devices_is_an_empty_run(aws):
    assert run() == {
        "wearables": 0,
        "updated": 0,
        "unchanged": 0,
        "failed": 0,
        "skipped": 0,
    }
    assert aws.iot.calls == []


@pytest.mark.parametrize("event", [{}, None, {"unexpected": "x"}, "text"])
def test_event_content_is_ignored(aws, event):
    seed(aws)

    assert handler.lambda_handler(event, None)["updated"] == 2


# ---- registry reads ----


def test_device_query_is_by_type_eventually_consistent_and_projected(aws):
    seed(aws)

    run()

    (query,) = aws.db.named("query")
    assert query["IndexName"] == "by-type"
    assert query["Select"] == "SPECIFIC_ATTRIBUTES"
    assert "ConsistentRead" not in query
    assert query["ExpressionAttributeValues"] == {":device": {"S": "DEVICE"}}
    names = query["ExpressionAttributeNames"]
    projected = {names[n.strip()] for n in query["ProjectionExpression"].split(",")}
    assert projected == {"pk", "device_type", "resident_id"}
    # IAM (infra/shadow_sync.tf) allows only these attribute names.
    assert set(names.values()) <= {
        "pk",
        "sk",
        "entity_type",
        "device_type",
        "resident_id",
    }


def test_thresholds_batch_get_is_projected_and_eventually_consistent(aws):
    seed(aws)

    run()

    (batch,) = aws.db.named("batch_get_item")
    request = batch["RequestItems"]["silvercare-registry"]
    assert "ConsistentRead" not in request
    names = request["ExpressionAttributeNames"]
    assert {names[n.strip()] for n in request["ProjectionExpression"].split(",")} == {
        "pk",
        "hr_bpm",
        "spo2_pct",
    }
    for key in request["Keys"]:
        assert key["pk"]["S"].startswith("THRESHOLDS#")


def test_device_query_follows_every_page(aws):
    for n in range(1, 6):
        put_device(aws.ddb, f"wearable-000{n}", None)
    aws.db.page_limit = 2

    counts = run()

    assert counts["wearables"] == 5
    assert len(aws.db.named("query")) >= 3
    assert sorted(aws.iot.things("update_thing_shadow")) == [
        f"wearable-000{n}" for n in range(1, 6)
    ]


def test_unprocessed_keys_are_retried_with_back_off(aws):
    seed(aws)
    aws.db.unprocessed = 2

    run()

    assert len(aws.db.named("batch_get_item")) == 3
    assert aws.sleeps == [0.2, 0.4]
    assert desired(aws.shadow(WEARABLE_1)) == TIGHT


def test_unprocessed_keys_after_every_retry_fail_the_run_and_write_nothing(aws):
    seed(aws)
    aws.db.unprocessed = 99

    with pytest.raises(handler.UnprocessedKeysError):
        run()

    assert len(aws.db.named("batch_get_item")) == handler.BATCH_GET_ATTEMPTS
    assert aws.iot.named("update_thing_shadow") == []


def test_batch_get_sends_at_most_100_keys_per_call(aws):
    residents = [f"res-{n:04d}" for n in range(1, 151)]

    handler.read_thresholds(residents)

    sizes = [
        len(kw["RequestItems"]["silvercare-registry"]["Keys"])
        for kw in aws.db.named("batch_get_item")
    ]
    assert sizes == [100, 50]


def test_throttled_device_query_fails_the_run_and_writes_nothing(aws):
    seed(aws)
    aws.db.fail["query"] = client_error("ProvisionedThroughputExceededException")

    with pytest.raises(Exception, match="ProvisionedThroughputExceeded"):
        run()

    assert aws.iot.calls == []


# ---- malformed registry items (skipped, shadows untouched) ----


@pytest.mark.parametrize("resident_id", ["resident-one", ["res-0001"], 7])
def test_wearable_with_malformed_resident_is_skipped(aws, resident_id):
    seed(aws)
    put_raw(
        aws.ddb,
        {
            "pk": f"DEVICE#{WEARABLE_3}",
            "sk": "ASSIGNMENT",
            "entity_type": "DEVICE",
            "device_type": "wearable",
            "resident_id": resident_id,
        },
    )

    counts = run()

    assert counts["skipped"] == 1
    assert counts["wearables"] == 2
    assert WEARABLE_3 not in aws.iot.things("get_thing_shadow")
    assert aws.shadow(WEARABLE_3) is None


def test_device_with_malformed_id_is_skipped(aws):
    seed(aws)
    put_device(aws.ddb, "wearable-x", None, pk="DEVICE#wearable-x")

    counts = run()

    assert counts["skipped"] == 1
    assert "wearable-x" not in aws.iot.things("get_thing_shadow")


def test_malformed_thresholds_item_gives_the_defaults(aws):
    put_device(aws.ddb, WEARABLE_1, RESIDENT_A)
    put_thresholds(aws.ddb, RESIDENT_A, hr=("fast", "slow"), spo2="low")

    run()

    assert desired(aws.shadow(WEARABLE_1)) == DEFAULTS


# ---- failures (criterion 10) ----


@pytest.mark.parametrize(
    "exc",
    [
        client_error("ThrottlingException", "GetThingShadow"),
        client_error("InternalFailureException", "GetThingShadow"),
        TimeoutError("synthetic"),
    ],
)
def test_one_failed_read_does_not_stop_the_others_and_the_run_fails(aws, exc):
    seed(aws)
    aws.iot.fail_things[WEARABLE_1] = exc

    with pytest.raises(handler.SyncFailedError):
        run()

    assert desired(aws.shadow(WEARABLE_2)) == FIXED
    assert aws.shadow(WEARABLE_1) is None


def test_one_failed_write_does_not_stop_the_others_and_the_run_fails(aws):
    seed(aws)
    aws.iot.fail_updates[WEARABLE_1] = client_error("ThrottlingException")

    with pytest.raises(handler.SyncFailedError):
        run()

    assert aws.iot.things("update_thing_shadow") == [WEARABLE_1, WEARABLE_2]
    assert desired(aws.shadow(WEARABLE_2)) == FIXED


def test_wearable_without_a_thing_is_a_failure_not_a_crash(aws):
    # Registry lists a wearable that moto's IoT doesn't know: GetThingShadow is a 404
    # and UpdateThingShadow then fails (moto needs the thing); the others still sync.
    seed(aws)
    put_device(aws.ddb, "wearable-0099", None)

    with pytest.raises(handler.SyncFailedError):
        run()

    assert desired(aws.shadow(WEARABLE_1)) == TIGHT


# ---- logs (criterion 11) ----


def test_ok_log_has_counts_only(aws, logs):
    seed(aws)

    run()

    (record,) = logs()
    assert record["message"] == "shadow sync"
    assert record["level"] == "INFO"
    assert set(record) - POWERTOOLS_KEYS == {
        "wearables",
        "updated",
        "unchanged",
        "failed",
        "skipped",
    }


def test_failure_log_has_counts_and_failed_ids_with_error_codes(aws, logs):
    seed(aws)
    aws.iot.fail_updates[WEARABLE_2] = client_error("ThrottlingException")

    with pytest.raises(handler.SyncFailedError):
        run()

    (record,) = logs()
    assert record["level"] == "ERROR"
    assert record["failed"] == 1
    assert record["updated"] == 1
    assert record["failures"] == [
        {"device_id": WEARABLE_2, "error": "ThrottlingException"}
    ]


def test_logs_never_carry_limits_thresholds_or_resident_ids(aws, logs):
    seed(aws)
    aws.iot.fail_updates[WEARABLE_2] = client_error("ThrottlingException")

    with pytest.raises(handler.SyncFailedError):
        run()

    text = UUID_RE.sub("<uuid>", json.dumps(logs()))
    for value in (
        "57",
        "113",
        "93",
        "130",
        "88",
        "res-",
        "RESIDENT",
        "THRESHOLDS",
        "limits",
        "hr_bpm",
        "qz-5523",
    ):
        assert value not in text


def test_run_error_message_carries_no_ids_or_values(aws):
    seed(aws)
    aws.iot.fail_updates[WEARABLE_1] = client_error("ThrottlingException")

    with pytest.raises(handler.SyncFailedError) as info:
        run()

    assert str(info.value) == "1 wearable shadows failed"


# ---- client config ----


def test_iot_client_uses_the_endpoint_and_a_two_second_timeout():
    handler.iot_data_client.cache_clear()
    try:
        client = handler.iot_data_client()
    finally:
        handler.iot_data_client.cache_clear()
    assert (
        client.meta.endpoint_url == "https://data-ats.iot.ap-southeast-1.amazonaws.com"
    )
    assert client.meta.config.connect_timeout == 2
    assert client.meta.config.read_timeout == 2
