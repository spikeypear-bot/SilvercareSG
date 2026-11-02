"""Alert message builder (docs/features/alerts-core.md, criteria 1-3). Pure, no moto."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import alerts
import pytest
from events import WEARABLE, rule_event
from validation import ValidMessage, validate_event

ALERT_FIELDS = {
    "schema_version",
    "alert_id",
    "alert_type",
    "resident_id",
    "device_id",
    "event_ts",
    "received_at",
    "source_message_id",
    "details",
}


def valid_fall(**overrides) -> ValidMessage:
    result = validate_event(rule_event("fall", **overrides), frozenset({"fall"}))
    assert isinstance(result, ValidMessage), result
    return result


# ---- alert_id (criterion 2) ----


def test_alert_namespace_is_the_fixed_constant():
    # Changing it would give retried events a new alert_id and duplicate alerts.
    assert alerts.ALERT_NAMESPACE == uuid.UUID("8d3c5f2a-6b1e-4f7d-9a0c-2e4b6d8f1a35")


def test_alert_id_is_uuid5_of_ts_type_and_device():
    ts = "2026-10-18T03:14:07.360Z"

    alert_id = alerts.alert_id_for(ts, "fall", WEARABLE)

    expected = uuid.uuid5(alerts.ALERT_NAMESPACE, f"{ts}|fall|{WEARABLE}")
    assert alert_id == str(expected)
    assert uuid.UUID(alert_id).version == 5


def test_same_event_always_gives_the_same_alert_id():
    first = alerts.build_fall_alert(valid_fall(), "res-0001")
    # A redelivery: same ts and device, new message_id and receive time.
    again = alerts.build_fall_alert(
        valid_fall(message_id=str(uuid.uuid4()), received_at=1792293120999),
        "res-0001",
    )

    assert first["alert_id"] == again["alert_id"]


@pytest.mark.parametrize(
    ("ts", "alert_type", "device_id"),
    [
        ("2026-10-18T03:14:07.361Z", "fall", WEARABLE),
        ("2026-10-18T03:14:07.360Z", "vitals_alert", WEARABLE),
        ("2026-10-18T03:14:07.360Z", "fall", "wearable-0002"),
    ],
)
def test_alert_id_changes_with_each_input(ts, alert_type, device_id):
    base = alerts.alert_id_for("2026-10-18T03:14:07.360Z", "fall", WEARABLE)

    assert alerts.alert_id_for(ts, alert_type, device_id) != base


# ---- message shape (criterion 1) ----


def test_fall_alert_has_exactly_the_contract_fields():
    msg = valid_fall()

    alert = alerts.build_fall_alert(msg, "res-0003")

    assert set(alert) == ALERT_FIELDS
    assert alert["schema_version"] == 1
    assert alert["alert_type"] == "fall"
    assert alert["resident_id"] == "res-0003"
    assert alert["device_id"] == WEARABLE
    assert alert["event_ts"] == msg.ts
    assert alert["received_at"] == "2026-10-18T03:12:00.410Z"
    assert alert["source_message_id"] == msg.message_id
    assert alert["alert_id"] == alerts.alert_id_for(msg.ts, "fall", WEARABLE)


def test_fall_details_are_the_fall_fields_only_without_accel_window():
    alert = alerts.build_fall_alert(valid_fall(), "res-0001")

    assert alert["details"] == {"confidence": 0.91, "peak_g": 3.8, "still_s": 20}
    assert "accel_window" not in alerts.encode(alert)
    assert "samples" not in alerts.encode(alert)


def test_unassigned_resident_is_carried_as_is():
    alert = alerts.build_fall_alert(valid_fall(), "unassigned")

    assert alert["resident_id"] == "unassigned"


# ---- encoding (criterion 3) ----


def test_encoded_floats_keep_their_short_form():
    alert = alerts.build_fall_alert(valid_fall(confidence=0.91, peak_g=3.8), "res-0001")

    text = alerts.encode(alert)

    assert '"confidence":0.91' in text
    assert '"peak_g":3.8' in text
    decoded = json.loads(text, parse_float=Decimal)
    assert decoded["details"]["confidence"] == Decimal("0.91")


def test_encode_is_compact_json():
    alert = alerts.build_fall_alert(valid_fall(), "res-0001")

    assert ", " not in alerts.encode(alert)
    assert json.loads(alerts.encode(alert)) == alert


def test_encode_refuses_nan():
    with pytest.raises(ValueError):
        alerts.encode({"details": {"confidence": float("nan")}})


# ---- send_alert ----


class RecordingSqs:
    def __init__(self):
        self.calls: list[dict] = []

    def send_message(self, **kwargs):
        self.calls.append(kwargs)
        return {"MessageId": "synthetic"}


def test_send_alert_uses_alert_type_attribute_and_no_group_or_delay():
    sqs = RecordingSqs()
    alert = alerts.build_fall_alert(valid_fall(), "res-0001")

    alerts.send_alert(sqs, "https://example.invalid/queue", alert)

    (call,) = sqs.calls
    assert set(call) == {"QueueUrl", "MessageBody", "MessageAttributes"}
    assert call["QueueUrl"] == "https://example.invalid/queue"
    assert call["MessageAttributes"] == {
        "alert_type": {"DataType": "String", "StringValue": "fall"}
    }
    assert json.loads(call["MessageBody"]) == alert
