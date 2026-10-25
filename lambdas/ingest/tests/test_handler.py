"""The three entry points end to end against moto (criteria 11, 14-22, 24)."""

from __future__ import annotations

from decimal import Decimal

import handler
import pytest
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
from conftest import TABLE_NAME
from events import (
    BEDROOM,
    DELETE,
    STORED_FIELDS,
    WEARABLE,
    iso_to_epoch_ms,
    rule_event,
    ts_at,
)
from latest_store import WriteConflictError

HANDLERS = {
    "status": handler.device_health_handler,
    "vitals": handler.telemetry_handler,
    "motion": handler.telemetry_handler,
    "bed": handler.telemetry_handler,
    "fall": handler.fall_alert_handler,
}

CASES = [
    ("vitals", WEARABLE),
    ("fall", WEARABLE),
    ("status", WEARABLE),
    ("motion", BEDROOM),
    ("bed", BEDROOM),
    ("status", BEDROOM),
]

# Keys Powertools adds to every record; anything else must be from our allow-list.
POWERTOOLS_KEYS = {"level", "location", "message", "timestamp", "service"}
OUR_KEYS = {"device_id", "message_id", "outcome", "reason", "error"}

_d = TypeDeserializer()


def invoke(event):
    return HANDLERS[event["message"]](event, None)


def read_item(client, device_id, message):
    resp = client.get_item(
        TableName=TABLE_NAME,
        Key={"pk": {"S": f"DEVICE#{device_id}"}, "sk": {"S": f"MSG#{message}"}},
        ConsistentRead=True,
    )
    if "Item" not in resp:
        return None
    return {k: _d.deserialize(v) for k, v in resp["Item"].items()}


def raw_item(client, device_id, message):
    return client.get_item(
        TableName=TABLE_NAME,
        Key={"pk": {"S": f"DEVICE#{device_id}"}, "sk": {"S": f"MSG#{message}"}},
        ConsistentRead=True,
    ).get("Item")


def item_count(client) -> int:
    return client.scan(TableName=TABLE_NAME)["Count"]


# ---- valid messages: stored ----


@pytest.mark.parametrize(("message", "device_id"), CASES)
def test_valid_message_is_stored_with_only_allow_listed_fields(
    latest_table, message, device_id
):
    event = rule_event(message, device_id, extra_unknown="ignored")

    assert invoke(event) == {"outcome": "stored"}

    item = read_item(latest_table, device_id, message)
    (stored,) = item["entries"]
    assert (
        set(stored)
        == {
            "message_id",
            "ts",
            "received_at",
            "clock_skew",
        }
        | STORED_FIELDS[(device_id.split("-")[0], message)]
    )
    assert stored["message_id"] == event["message_id"]
    assert stored["received_at"] == "2026-10-18T03:12:00.410Z"
    assert stored["clock_skew"] is False


@pytest.mark.parametrize(("message", "device_id"), CASES)
def test_item_has_data_model_attributes(latest_table, message, device_id):
    event = rule_event(message, device_id)

    invoke(event)

    item = read_item(latest_table, device_id, message)
    expected_keys = {
        "pk",
        "sk",
        "device_type",
        "entries",
        "latest",
        "latest_ts",
        "version",
    }
    if message != "fall":
        expected_keys.add("watch_type")
    assert set(item) == expected_keys
    assert item["pk"] == f"DEVICE#{device_id}"
    assert item["sk"] == f"MSG#{message}"
    assert item["device_type"] == device_id.split("-")[0]
    assert item["latest"] == item["entries"][0]
    assert item["latest_ts"] == event["ts"]
    assert item["version"] == 1


@pytest.mark.parametrize("message", ["vitals", "status", "motion", "bed"])
def test_watch_type_equals_message_type(latest_table, message):
    invoke(rule_event(message))

    device_id = BEDROOM if message in ("motion", "bed") else WEARABLE
    assert read_item(latest_table, device_id, message)["watch_type"] == message


def test_fall_item_has_no_watch_type(latest_table):
    invoke(rule_event("fall"))

    assert "watch_type" not in read_item(latest_table, WEARABLE, "fall")


def test_accel_fields_are_never_stored(latest_table):
    invoke(rule_event("vitals"))
    invoke(rule_event("fall"))

    for message, name in (("vitals", "accel_g"), ("fall", "accel_window")):
        item = read_item(latest_table, WEARABLE, message)
        assert name not in item["entries"][0]
        assert name not in item["latest"]


def test_decimal_is_stored_as_exact_dynamodb_number(latest_table):
    invoke(rule_event("vitals", skin_temp_c=33.4))
    invoke(rule_event("fall", confidence=0.91, peak_g=3.8))

    vitals = raw_item(latest_table, WEARABLE, "vitals")
    fall = raw_item(latest_table, WEARABLE, "fall")
    assert vitals["entries"]["L"][0]["M"]["skin_temp_c"] == {"N": "33.4"}
    assert fall["entries"]["L"][0]["M"]["confidence"] == {"N": "0.91"}
    assert fall["entries"]["L"][0]["M"]["peak_g"] == {"N": "3.8"}
    assert read_item(latest_table, WEARABLE, "vitals")["latest"]["skin_temp_c"] == (
        Decimal("33.4")
    )


def test_null_field_is_stored_as_null(latest_table):
    invoke(rule_event("status", BEDROOM, battery_pct=None))
    invoke(rule_event("vitals", hr_bpm=None))

    status = raw_item(latest_table, BEDROOM, "status")
    vitals = raw_item(latest_table, WEARABLE, "vitals")
    assert status["entries"]["L"][0]["M"]["battery_pct"] == {"NULL": True}
    assert vitals["entries"]["L"][0]["M"]["hr_bpm"] == {"NULL": True}


def test_booleans_are_stored_as_booleans(latest_table):
    invoke(rule_event("status", WEARABLE, worn=True, charging=False))

    entry = raw_item(latest_table, WEARABLE, "status")["entries"]["L"][0]["M"]
    assert entry["worn"] == {"BOOL": True}
    assert entry["charging"] == {"BOOL": False}


# ---- received_at and clock skew ----


def test_received_at_is_stored_as_iso_utc(latest_table):
    invoke(rule_event("vitals", received_at=1792293120007))

    entry = read_item(latest_table, WEARABLE, "vitals")["entries"][0]
    assert entry["received_at"] == "2026-10-18T03:12:00.007Z"


@pytest.mark.parametrize(
    ("offset_ms", "skewed"),
    [(300_001, True), (-300_001, True), (300_000, False), (-300_000, False)],
)
def test_clock_skew_flag_is_stored(latest_table, offset_ms, skewed):
    ts = ts_at(30)
    invoke(rule_event("vitals", ts=ts, received_at=iso_to_epoch_ms(ts) + offset_ms))

    entry = read_item(latest_table, WEARABLE, "vitals")["entries"][0]
    assert entry["clock_skew"] is skewed


# ---- duplicates, order, the 5 most recent ----


def test_duplicate_delivery_writes_nothing_new(latest_table, logs):
    event = rule_event("vitals")
    invoke(event)
    before = read_item(latest_table, WEARABLE, "vitals")

    assert invoke(dict(event)) == {"outcome": "duplicate"}

    after = read_item(latest_table, WEARABLE, "vitals")
    assert after == before
    assert after["version"] == 1
    assert logs()[-1]["outcome"] == "duplicate"


def test_sixth_message_drops_the_oldest(latest_table):
    events = [rule_event("motion", ts=ts_at(m)) for m in range(1, 7)]
    for event in events:
        invoke(event)

    item = read_item(latest_table, BEDROOM, "motion")
    assert [e["message_id"] for e in item["entries"]] == [
        e["message_id"] for e in reversed(events[1:])
    ]
    assert item["latest_ts"] == ts_at(6)
    assert item["version"] == 6


def test_out_of_order_newer_message_becomes_latest(latest_table):
    invoke(rule_event("bed", ts=ts_at(10)))
    newer = rule_event("bed", ts=ts_at(20))
    invoke(newer)

    item = read_item(latest_table, BEDROOM, "bed")
    assert item["latest"]["message_id"] == newer["message_id"]
    assert item["latest_ts"] == ts_at(20)


def test_late_message_is_inserted_in_ts_order(latest_table):
    for m in (50, 40, 30, 10):
        invoke(rule_event("bed", ts=ts_at(m)))
    late = rule_event("bed", ts=ts_at(20))

    assert invoke(late) == {"outcome": "stored"}

    item = read_item(latest_table, BEDROOM, "bed")
    assert [e["ts"] for e in item["entries"]] == [
        ts_at(m) for m in (50, 40, 30, 20, 10)
    ]
    assert item["latest_ts"] == ts_at(50)


def test_message_older_than_stored_five_is_too_old(latest_table, logs):
    for m in (50, 40, 30, 20, 10):
        invoke(rule_event("bed", ts=ts_at(m)))
    before = read_item(latest_table, BEDROOM, "bed")
    old = rule_event("bed", ts=ts_at(5))

    assert invoke(old) == {"outcome": "too_old"}

    assert read_item(latest_table, BEDROOM, "bed") == before
    assert logs()[-1]["outcome"] == "too_old"


def test_message_types_are_stored_in_separate_items(latest_table):
    invoke(rule_event("vitals"))
    invoke(rule_event("status", WEARABLE))
    invoke(rule_event("fall"))

    assert item_count(latest_table) == 3


# ---- rejections: nothing written, no raise ----


@pytest.mark.parametrize(
    ("entry_point", "event"),
    [
        (handler.telemetry_handler, None),
        (handler.telemetry_handler, []),
        (handler.telemetry_handler, "not json"),
        (handler.telemetry_handler, rule_event("vitals", hr_bpm="seventy")),
        (handler.telemetry_handler, rule_event("vitals", hr_bpm=True)),
        (handler.telemetry_handler, rule_event("vitals", hr_bpm=999)),
        (handler.telemetry_handler, rule_event("vitals", schema_version=2)),
        (handler.telemetry_handler, rule_event("vitals", activity_pct=DELETE)),
        (handler.telemetry_handler, rule_event("vitals", accel_g=DELETE)),
        (handler.fall_alert_handler, rule_event("fall", accel_window=[])),
        (handler.device_health_handler, rule_event("status", BEDROOM, fw_version="")),
        (handler.telemetry_handler, rule_event("bed", occupied="yes")),
    ],
)
def test_invalid_message_is_rejected_without_writing(latest_table, entry_point, event):
    result = entry_point(event, None)

    assert result["outcome"] == "rejected"
    assert item_count(latest_table) == 0


@pytest.mark.parametrize(
    ("entry_point", "message", "device_id"),
    [
        (handler.device_health_handler, "vitals", WEARABLE),
        (handler.device_health_handler, "fall", WEARABLE),
        (handler.device_health_handler, "motion", BEDROOM),
        (handler.device_health_handler, "bed", BEDROOM),
        (handler.telemetry_handler, "status", WEARABLE),
        (handler.telemetry_handler, "status", BEDROOM),
        (handler.telemetry_handler, "fall", WEARABLE),
        (handler.fall_alert_handler, "vitals", WEARABLE),
        (handler.fall_alert_handler, "status", WEARABLE),
        (handler.fall_alert_handler, "motion", BEDROOM),
        (handler.fall_alert_handler, "bed", BEDROOM),
    ],
)
def test_message_type_for_another_function_is_wrong_function(
    latest_table, entry_point, message, device_id
):
    result = entry_point(rule_event(message, device_id), None)

    assert result == {"outcome": "rejected", "reason": "wrong_function"}
    assert item_count(latest_table) == 0


@pytest.mark.parametrize(
    ("entry_point", "message", "device_id"),
    [
        (handler.device_health_handler, "status", WEARABLE),
        (handler.device_health_handler, "status", BEDROOM),
        (handler.telemetry_handler, "vitals", WEARABLE),
        (handler.telemetry_handler, "motion", BEDROOM),
        (handler.telemetry_handler, "bed", BEDROOM),
        (handler.fall_alert_handler, "fall", WEARABLE),
    ],
)
def test_each_function_stores_its_own_types(
    latest_table, entry_point, message, device_id
):
    assert entry_point(rule_event(message, device_id), None) == {"outcome": "stored"}


# ---- logging (criteria 14, 19) ----


def _assert_only_allowed_keys(record):
    assert set(record) - POWERTOOLS_KEYS <= OUR_KEYS, record


def test_stored_message_logs_ids_and_outcome_only(latest_table, logs):
    event = rule_event("vitals", hr_bpm=187, spo2_pct=83, skin_temp_c=41.7)

    invoke(event)

    (record,) = logs()
    _assert_only_allowed_keys(record)
    assert record["message"] == "vitals"
    assert record["device_id"] == WEARABLE
    assert record["message_id"] == event["message_id"]
    assert record["outcome"] == "stored"


def test_rejected_message_logs_reason_without_value(latest_table, logs):
    event = rule_event("vitals", hr_bpm=999)

    invoke(event)

    (record,) = logs()
    _assert_only_allowed_keys(record)
    assert record["level"] == "WARNING"
    assert record["reason"] == "out_of_range:hr_bpm"
    assert record["device_id"] == WEARABLE
    assert record["message_id"] == event["message_id"]
    assert record["message"] == "vitals"
    assert "999" not in "".join(str(v) for k, v in record.items() if k != "timestamp")


def test_rejected_message_with_non_string_message_id_omits_it(latest_table, logs):
    invoke(rule_event("vitals", message_id=12345))

    (record,) = logs()
    _assert_only_allowed_keys(record)
    assert "message_id" not in record
    assert record["reason"] == "invalid:message_id"


def test_rejected_message_with_non_uuid_message_id_logs_placeholder(latest_table, logs):
    invoke(rule_event("vitals", message_id="hr_bpm=150"))

    (record,) = logs()
    assert record["message_id"] == "invalid"
    assert record["reason"] == "invalid:message_id"
    assert "150" not in "".join(str(v) for k, v in record.items() if k != "timestamp")


def test_rejected_non_dict_event_logs_without_ids(latest_table, logs):
    handler.telemetry_handler(["synthetic"], None)

    (record,) = logs()
    _assert_only_allowed_keys(record)
    assert record["reason"] == "invalid:event"
    assert "device_id" not in record


def test_long_ids_in_logs_are_truncated(latest_table, logs):
    long_id = "x" * 500

    invoke(rule_event("vitals", message_id=long_id))

    (record,) = logs()
    assert len(record["message_id"]) <= handler.LOG_ID_MAX_LEN


@pytest.mark.parametrize(("message", "device_id"), CASES)
def test_logs_never_contain_field_values_or_field_names(
    latest_table, logs, message, device_id
):
    overrides = {"fw_version": "9.8.7-synthetic"} if message == "status" else {}
    event = rule_event(message, device_id, **overrides)

    invoke(event)
    invoke(dict(event))  # duplicate

    hidden = STORED_FIELDS[(device_id.split("-")[0], message)] | {
        "accel_g",
        "accel_window",
        "received_at",
        "clock_skew",
        "ts",
        "schema_version",
    }
    records = logs()
    assert [r["outcome"] for r in records] == ["stored", "duplicate"]
    for record in records:
        _assert_only_allowed_keys(record)
        assert not hidden & set(record)
    assert "9.8.7-synthetic" not in logs.raw.getvalue()
    assert event["ts"] not in logs.raw.getvalue()


# ---- AWS errors are raised (criterion 20) ----


class FailingClient:
    def __init__(self, code, operation="get_item"):
        self.code = code
        self.operation = operation

    def _fail(self, op):
        raise ClientError({"Error": {"Code": self.code, "Message": "synthetic"}}, op)

    def get_item(self, **kwargs):
        if self.operation == "get_item":
            self._fail("GetItem")
        return {}

    def put_item(self, **kwargs):
        self._fail("PutItem")


@pytest.mark.parametrize(
    ("code", "operation"),
    [
        ("ProvisionedThroughputExceededException", "get_item"),
        ("ThrottlingException", "get_item"),
        ("ProvisionedThroughputExceededException", "put_item"),
        ("InternalServerError", "put_item"),
        ("AccessDeniedException", "get_item"),
    ],
)
def test_aws_error_is_raised_for_retry(monkeypatch, logs, code, operation):
    monkeypatch.setattr(
        handler, "dynamodb_client", lambda: FailingClient(code, operation)
    )

    with pytest.raises(ClientError):
        invoke(rule_event("vitals"))

    (record,) = logs()
    _assert_only_allowed_keys(record)
    assert record["outcome"] == "error"
    assert record["error"] == "ClientError"


def test_write_conflict_after_three_attempts_is_raised(monkeypatch, logs):
    monkeypatch.setattr(
        handler,
        "dynamodb_client",
        lambda: FailingClient("ConditionalCheckFailedException", "put_item"),
    )

    with pytest.raises(WriteConflictError):
        invoke(rule_event("vitals"))

    assert logs()[-1]["error"] == "WriteConflictError"


# ---- client config and scope (criteria 11, 22) ----


def test_dynamodb_client_uses_adaptive_retry_mode(latest_table):
    client = handler.dynamodb_client()

    assert client.meta.config.retries["mode"] == "adaptive"


def test_dynamodb_client_is_reused(latest_table):
    assert handler.dynamodb_client() is handler.dynamodb_client()


class CallRecorder:
    def __init__(self, client):
        self._client = client
        self.calls: list[tuple[str, str]] = []

    def __getattr__(self, name):
        method = getattr(self._client, name)

        def wrapper(**kwargs):
            self.calls.append((name, kwargs.get("TableName")))
            return method(**kwargs)

        return wrapper


@pytest.mark.parametrize(("message", "device_id"), CASES)
def test_only_get_and_put_on_the_latest_table(
    latest_table, monkeypatch, message, device_id
):
    recorder = CallRecorder(latest_table)
    monkeypatch.setattr(handler, "dynamodb_client", lambda: recorder)

    invoke(rule_event(message, device_id))

    assert recorder.calls == [("get_item", TABLE_NAME), ("put_item", TABLE_NAME)]


def test_table_name_comes_from_environment(latest_table, monkeypatch):
    recorder = CallRecorder(latest_table)
    monkeypatch.setattr(handler, "dynamodb_client", lambda: recorder)
    monkeypatch.setenv("LATEST_TABLE", "synthetic-other-table")

    with pytest.raises(ClientError):
        invoke(rule_event("vitals"))

    assert recorder.calls[0] == ("get_item", "synthetic-other-table")
