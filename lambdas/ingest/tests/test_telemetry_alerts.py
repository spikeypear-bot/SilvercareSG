"""The cloud vitals check in `telemetry` end to end against moto
(docs/features/vitals-alerts.md, criteria 5, 7-12, 29).

moto for DynamoDB (latest + registry) and SQS (the alerts queue). Every ID and value is
synthetic.
"""

from __future__ import annotations

import re

import alerts
import handler
import pytest
from botocore.exceptions import ClientError
from conftest import REGISTRY_TABLE, TABLE_NAME, assign_device
from events import BEDROOM, WEARABLE, rule_event, ts_at

RESIDENT = "res-0002"
UUID_ANY_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def vitals(minute: int, second: int = 0, **fields) -> dict:
    return rule_event("vitals", ts=ts_at(minute, second), **fields)


def send(*events) -> list[dict]:
    return [handler.telemetry_handler(e, None) for e in events]


def put_thresholds(client, resident_id: str, **metrics) -> None:
    item = {
        "pk": {"S": f"THRESHOLDS#{resident_id}"},
        "sk": {"S": "THRESHOLDS"},
        "updated_at": {"S": "2026-10-18T00:00:00Z"},
    }
    for metric, limits in metrics.items():
        item[metric] = {"M": {k: {"N": str(v)} for k, v in limits.items()}}
    client.put_item(TableName=REGISTRY_TABLE, Item=item)


class Recorder:
    """Wraps a client and records (operation, TableName, pk) for every call."""

    def __init__(self, client, calls, fail_on=None):
        self._client = client
        self._calls = calls
        self._fail_on = fail_on

    def __getattr__(self, name):
        method = getattr(self._client, name)

        def wrapper(**kwargs):
            key = kwargs.get("Key") or {}
            self._calls.append(
                (name, kwargs.get("TableName"), key.get("pk", {}).get("S"))
            )
            if name == self._fail_on:
                raise ClientError(
                    {"Error": {"Code": "ThrottlingException", "Message": "x"}}, name
                )
            return method(**kwargs)

        return wrapper


@pytest.fixture
def assigned(latest_table):
    assign_device(latest_table, WEARABLE, RESIDENT)
    return latest_table


def registry_calls(calls) -> list:
    return [c for c in calls if c[1] == REGISTRY_TABLE]


# ---- two abnormal readings in a row (criteria 9-10) ----


def test_two_high_heart_rates_queue_one_hr_high_warning(assigned, alerts_queue):
    first, second = vitals(13, hr_bpm=125), vitals(14, hr_bpm=128)

    assert send(first, second) == [{"outcome": "stored"}, {"outcome": "stored"}]

    (body,) = alerts_queue.bodies()
    assert body == {
        "schema_version": 1,
        "alert_id": alerts.alert_id_for(second["ts"], "hr_high", "warning", WEARABLE),
        "alert_type": "hr_high",
        "severity": "warning",
        "resident_id": RESIDENT,
        "device_id": WEARABLE,
        "event_ts": second["ts"],
        "received_at": "2026-10-18T03:14:00.410Z",
        "source_message_id": second["message_id"],
        "details": {
            "metric": "hr_bpm",
            "value": 128,
            "limit": 120,
            "direction": "above",
            "previous_value": 125,
        },
    }


def test_queued_warning_has_alert_type_attribute(assigned, alerts_queue):
    send(vitals(13, hr_bpm=125), vitals(14, hr_bpm=128))

    (message,) = alerts_queue.receive_all()
    assert message["MessageAttributes"] == {
        "alert_type": {"DataType": "String", "StringValue": "hr_high"}
    }


def test_one_abnormal_reading_queues_nothing(assigned, alerts_queue):
    send(vitals(13, hr_bpm=72), vitals(14, hr_bpm=128))

    assert alerts_queue.receive_all() == []


def test_first_reading_alone_queues_nothing(assigned, alerts_queue):
    send(vitals(14, hr_bpm=128))

    assert alerts_queue.receive_all() == []


@pytest.mark.parametrize(
    "fields",
    [
        {"hr_bpm": 120},
        {"hr_bpm": 50},
        {"spo2_pct": 92},
        {"skin_temp_c": 30.0},
        {"skin_temp_c": 38.0},
    ],
)
def test_two_readings_at_a_default_limit_queue_nothing(assigned, alerts_queue, fields):
    send(vitals(13, **fields), vitals(14, **fields))

    assert alerts_queue.receive_all() == []


def test_opposite_directions_queue_nothing(assigned, alerts_queue):
    send(vitals(13, hr_bpm=40), vitals(14, hr_bpm=130))

    assert alerts_queue.receive_all() == []


def test_null_reading_breaks_a_streak(assigned, alerts_queue):
    send(
        vitals(12, hr_bpm=130),
        vitals(13, hr_bpm=None),
        vitals(14, hr_bpm=130),
    )

    assert alerts_queue.receive_all() == []


def test_readings_more_than_3_minutes_apart_queue_nothing(assigned, alerts_queue):
    send(vitals(10, hr_bpm=130), vitals(13, 1, hr_bpm=130))

    assert alerts_queue.receive_all() == []


def test_readings_exactly_3_minutes_apart_pair(assigned, alerts_queue):
    send(vitals(10, hr_bpm=130), vitals(13, hr_bpm=130))

    assert [b["alert_type"] for b in alerts_queue.bodies()] == ["hr_high"]


def test_one_message_can_queue_several_warnings(assigned, alerts_queue):
    second = vitals(14, hr_bpm=130, spo2_pct=85, skin_temp_c=38.6)
    send(vitals(13, hr_bpm=125, spo2_pct=88, skin_temp_c=38.4), second)

    bodies = alerts_queue.bodies()
    assert sorted(b["alert_type"] for b in bodies) == [
        "hr_high",
        "skin_temp_high",
        "spo2_low",
    ]
    assert len({b["alert_id"] for b in bodies}) == 3
    assert all(b["event_ts"] == second["ts"] for b in bodies)
    skin = next(b for b in bodies if b["alert_type"] == "skin_temp_high")
    assert skin["details"]["value"] == 38.6
    assert skin["details"]["previous_value"] == 38.4
    assert skin["details"]["limit"] == 38


def test_a_streak_keeps_raising_with_new_alert_ids(assigned, alerts_queue):
    # Repeats are held back by alert-processor, not here.
    send(vitals(12, hr_bpm=125), vitals(13, hr_bpm=128), vitals(14, hr_bpm=126))

    bodies = alerts_queue.bodies()
    assert [b["details"]["value"] for b in bodies] == [128, 126]
    assert bodies[0]["alert_id"] != bodies[1]["alert_id"]


# ---- duplicates, too old, not the newest (criterion 7) ----


def test_duplicate_reruns_the_check_with_the_same_alert_id(
    assigned, alerts_queue, logs
):
    first, second = vitals(13, hr_bpm=125), vitals(14, hr_bpm=128)
    send(first, second)

    assert handler.telemetry_handler(dict(second), None) == {"outcome": "duplicate"}

    a, b = alerts_queue.bodies()
    assert a["alert_id"] == b["alert_id"]
    assert logs()[-1]["alert_ids"] == [a["alert_id"]]


def test_too_old_message_is_not_checked(assigned, alerts_queue, monkeypatch, logs):
    send(*(vitals(m, hr_bpm=130 if m == 14 else 72) for m in range(10, 15)))
    alerts_queue.receive_all()
    calls: list = []
    monkeypatch.setattr(handler, "dynamodb_client", lambda: Recorder(assigned, calls))

    assert handler.telemetry_handler(vitals(5, hr_bpm=130), None) == {
        "outcome": "too_old"
    }

    assert registry_calls(calls) == []
    assert alerts_queue.receive_all() == []
    assert logs()[-1]["alert_ids"] == []


def test_late_message_that_is_not_the_newest_is_not_checked(assigned, alerts_queue):
    send(vitals(14, hr_bpm=130))

    # Older than the stored newest, so it lands at entries[1].
    assert send(vitals(13, hr_bpm=130)) == [{"outcome": "stored"}]

    assert alerts_queue.receive_all() == []


def test_duplicate_of_a_message_that_is_no_longer_newest_is_not_checked(
    assigned, alerts_queue
):
    first, second = vitals(12, hr_bpm=125), vitals(13, hr_bpm=128)
    send(first, second, vitals(14, hr_bpm=72))
    alerts_queue.receive_all()

    send(dict(second))

    assert alerts_queue.receive_all() == []


# ---- unassigned devices (criterion 8) ----


def test_unassigned_device_uses_defaults_and_unassigned_resident(
    latest_table, alerts_queue, monkeypatch
):
    calls: list = []
    monkeypatch.setattr(
        handler, "dynamodb_client", lambda: Recorder(latest_table, calls)
    )

    send(vitals(13, hr_bpm=125), vitals(14, hr_bpm=128))

    (body,) = alerts_queue.bodies()
    assert body["resident_id"] == "unassigned"
    assert body["details"]["limit"] == 120
    # Only the assignment is read; there are no THRESHOLDS for "unassigned".
    assert registry_calls(calls) == [("get_item", REGISTRY_TABLE, f"DEVICE#{WEARABLE}")]


# ---- per-resident thresholds (criterion 5) ----


def test_resident_thresholds_replace_the_defaults(assigned, alerts_queue):
    put_thresholds(assigned, RESIDENT, spo2_pct={"min": 88})

    send(vitals(13, spo2_pct=90), vitals(14, spo2_pct=89))

    assert alerts_queue.receive_all() == []


def test_resident_thresholds_raise_at_their_own_limit(assigned, alerts_queue):
    put_thresholds(assigned, RESIDENT, spo2_pct={"min": 88})

    send(vitals(13, spo2_pct=87), vitals(14, spo2_pct=86))

    (body,) = alerts_queue.bodies()
    assert body["alert_type"] == "spo2_low"
    assert body["details"]["limit"] == 88


def test_partial_thresholds_keep_other_defaults(assigned, alerts_queue):
    put_thresholds(assigned, RESIDENT, spo2_pct={"min": 88})

    send(vitals(13, hr_bpm=125), vitals(14, hr_bpm=128))

    (body,) = alerts_queue.bodies()
    assert body["details"]["limit"] == 120


def test_out_of_range_threshold_falls_back_and_logs_field_name(
    assigned, alerts_queue, logs
):
    put_thresholds(assigned, RESIDENT, hr_bpm={"max": 300})

    send(vitals(13, hr_bpm=125), vitals(14, hr_bpm=128))

    (body,) = alerts_queue.bodies()
    assert body["details"]["limit"] == 120
    (warning,) = [r for r in logs() if r.get("reason") == "invalid_limit"]
    assert warning["resident_id"] == RESIDENT
    assert warning["fields"] == ["hr_bpm.max"]
    text = " ".join(f"{k}={v}" for k, v in warning.items() if k != "timestamp")
    assert "300" not in UUID_ANY_RE.sub("<id>", text)


def test_min_not_below_max_falls_back_to_defaults(assigned, alerts_queue, logs):
    put_thresholds(assigned, RESIDENT, hr_bpm={"min": 110, "max": 100})

    send(vitals(13, hr_bpm=125), vitals(14, hr_bpm=128))

    (body,) = alerts_queue.bodies()
    assert body["details"]["limit"] == 120
    (warning,) = [r for r in logs() if r.get("reason") == "invalid_limit"]
    assert warning["fields"] == ["hr_bpm"]


# ---- reads and sends only when needed (criteria 7, 10) ----


def test_no_registry_read_and_no_send_without_a_pair(
    assigned, alerts_queue, monkeypatch
):
    calls: list = []
    monkeypatch.setattr(handler, "dynamodb_client", lambda: Recorder(assigned, calls))

    send(vitals(14, hr_bpm=130))

    assert registry_calls(calls) == []
    assert handler.sqs_client.cache_info().currsize == 0


def test_normal_pair_reads_registry_but_sends_nothing(
    assigned, alerts_queue, monkeypatch
):
    calls: list = []
    monkeypatch.setattr(handler, "dynamodb_client", lambda: Recorder(assigned, calls))

    send(vitals(13), vitals(14))

    assert [c[2] for c in registry_calls(calls)] == [
        f"DEVICE#{WEARABLE}",
        f"THRESHOLDS#{RESIDENT}",
    ]
    assert handler.sqs_client.cache_info().currsize == 0
    assert alerts_queue.receive_all() == []


def test_registry_and_thresholds_are_cached_across_invocations(
    assigned, alerts_queue, monkeypatch
):
    calls: list = []
    monkeypatch.setattr(handler, "dynamodb_client", lambda: Recorder(assigned, calls))

    send(vitals(12), vitals(13), vitals(14))

    assert len(registry_calls(calls)) == 2


def test_check_runs_after_the_store(assigned, alerts_queue, monkeypatch):
    calls: list = []
    monkeypatch.setattr(handler, "dynamodb_client", lambda: Recorder(assigned, calls))
    send(vitals(13, hr_bpm=125))
    calls.clear()

    send(vitals(14, hr_bpm=128))

    assert [(c[0], c[1]) for c in calls] == [
        ("get_item", TABLE_NAME),
        ("put_item", TABLE_NAME),
        ("get_item", REGISTRY_TABLE),
        ("get_item", REGISTRY_TABLE),
    ]


# ---- failures (criterion 10) ----


def test_send_failure_raises_after_the_message_is_stored(
    assigned, alerts_queue, monkeypatch, logs
):
    send(vitals(13, hr_bpm=125))
    sqs = handler.sqs_client()
    monkeypatch.setattr(
        handler, "sqs_client", lambda: Recorder(sqs, [], fail_on="send_message")
    )

    with pytest.raises(ClientError):
        send(vitals(14, hr_bpm=128))

    record = logs()[-1]
    assert record["outcome"] == "error"
    assert record["error"] == "ClientError"
    # Stored first, so Lambda's retry finds a duplicate and re-runs the check.
    item = assigned.get_item(
        TableName=TABLE_NAME,
        Key={"pk": {"S": f"DEVICE#{WEARABLE}"}, "sk": {"S": "MSG#vitals"}},
        ConsistentRead=True,
    )["Item"]
    assert len(item["entries"]["L"]) == 2


def test_retry_after_send_failure_queues_the_warning(
    assigned, alerts_queue, monkeypatch
):
    second = vitals(14, hr_bpm=128)
    send(vitals(13, hr_bpm=125))
    sqs = handler.sqs_client()
    monkeypatch.setattr(
        handler, "sqs_client", lambda: Recorder(sqs, [], fail_on="send_message")
    )
    with pytest.raises(ClientError):
        send(second)
    monkeypatch.setattr(handler, "sqs_client", lambda: sqs)

    assert send(dict(second)) == [{"outcome": "duplicate"}]

    (body,) = alerts_queue.bodies()
    assert body["alert_id"] == alerts.alert_id_for(
        second["ts"], "hr_high", "warning", WEARABLE
    )


def test_registry_failure_raises(assigned, alerts_queue, monkeypatch):
    send(vitals(13, hr_bpm=125))
    calls: list = []

    class FailRegistry(Recorder):
        def __getattr__(self, name):
            inner = super().__getattr__(name)

            def wrapper(**kwargs):
                if kwargs.get("TableName") == REGISTRY_TABLE:
                    raise ClientError(
                        {"Error": {"Code": "ThrottlingException", "Message": "x"}},
                        name,
                    )
                return inner(**kwargs)

            return wrapper

    monkeypatch.setattr(
        handler, "dynamodb_client", lambda: FailRegistry(assigned, calls)
    )

    with pytest.raises(ClientError):
        send(vitals(14, hr_bpm=128))
    assert alerts_queue.receive_all() == []


# ---- logs (criterion 11) ----


def test_log_line_has_alert_ids(assigned, alerts_queue, logs):
    send(vitals(13, hr_bpm=125), vitals(14, hr_bpm=128))

    (body,) = alerts_queue.bodies()
    first, second = [r for r in logs() if r["message"] == "vitals"]
    assert first["alert_ids"] == []
    assert second["alert_ids"] == [body["alert_id"]]
    assert second["outcome"] == "stored"


def test_rejected_vitals_log_has_no_alert_ids(assigned, logs):
    send(vitals(14, hr_bpm=999))

    (record,) = logs()
    assert record["reason"] == "out_of_range:hr_bpm"
    assert "alert_ids" not in record


@pytest.mark.parametrize("message", ["motion", "bed"])
def test_bedroom_messages_log_empty_alert_ids_and_read_no_registry(
    latest_table, alerts_queue, monkeypatch, logs, message
):
    calls: list = []
    monkeypatch.setattr(
        handler, "dynamodb_client", lambda: Recorder(latest_table, calls)
    )

    handler.telemetry_handler(rule_event(message, BEDROOM, ts=ts_at(13)), None)
    handler.telemetry_handler(rule_event(message, BEDROOM, ts=ts_at(14)), None)

    assert registry_calls(calls) == []
    assert alerts_queue.receive_all() == []
    assert [r["alert_ids"] for r in logs()] == [[], []]


def test_logs_hold_no_values_limits_or_thresholds(assigned, alerts_queue, logs):
    put_thresholds(assigned, RESIDENT, spo2_pct={"min": 87}, hr_bpm={"max": 113})

    second = vitals(14, hr_bpm=186, spo2_pct=84, skin_temp_c=41.6)
    send(vitals(13, hr_bpm=187, spo2_pct=83, skin_temp_c=41.7), second)
    send(dict(second))  # duplicate

    # 3 warnings, then the same 3 again for the duplicate.
    assert len(alerts_queue.bodies()) == 6
    noisy = {"timestamp", "location"}
    text = " ".join(f"{k}={v}" for r in logs() for k, v in r.items() if k not in noisy)
    text = UUID_ANY_RE.sub("<id>", text)
    for hidden in (
        "187",
        "186",
        "83",
        "84",
        "41.7",
        "41.6",
        "87",
        "113",
        "details",
        "limit",
        "previous_value",
        "hr_bpm",
        "spo2_pct",
        "skin_temp_c",
        RESIDENT,
    ):
        assert hidden not in text, hidden


# ---- device-health is unchanged (criterion 12) ----


@pytest.mark.parametrize("device_id", [WEARABLE, BEDROOM])
def test_device_health_reads_no_registry_and_sends_nothing(
    latest_table, alerts_queue, monkeypatch, logs, device_id
):
    assign_device(latest_table, WEARABLE, RESIDENT)
    calls: list = []
    monkeypatch.setattr(
        handler, "dynamodb_client", lambda: Recorder(latest_table, calls)
    )

    for minute in (12, 13, 14):
        handler.device_health_handler(
            rule_event("status", device_id, ts=ts_at(minute)), None
        )

    assert registry_calls(calls) == []
    assert handler.sqs_client.cache_info().currsize == 0
    assert alerts_queue.receive_all() == []
    assert all("alert_ids" not in r for r in logs())


# ---- configuration (criterion 11) ----


def test_queue_url_comes_from_environment(assigned, alerts_queue, monkeypatch):
    sqs = handler.sqs_client()
    other = sqs.create_queue(QueueName="synthetic-other-queue")["QueueUrl"]
    monkeypatch.setenv("ALERTS_QUEUE_URL", other)

    send(vitals(13, hr_bpm=125), vitals(14, hr_bpm=128))

    assert alerts_queue.receive_all() == []
    got = sqs.receive_message(QueueUrl=other, MaxNumberOfMessages=10)
    assert len(got.get("Messages", [])) == 1


def test_registry_table_comes_from_environment(assigned, monkeypatch):
    calls: list = []
    monkeypatch.setattr(handler, "dynamodb_client", lambda: Recorder(assigned, calls))
    monkeypatch.setenv("REGISTRY_TABLE", "synthetic-other-registry")
    send(vitals(13, hr_bpm=125))

    with pytest.raises(ClientError):
        send(vitals(14, hr_bpm=128))

    assert ("get_item", "synthetic-other-registry", f"DEVICE#{WEARABLE}") in calls
