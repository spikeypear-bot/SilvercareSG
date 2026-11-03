"""Alert message builder (docs/features/alerts-core.md, criteria 1-3;
docs/features/vitals-alerts.md, criteria 1-3, 10). Pure, no moto."""

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
    "severity",
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


def test_alert_id_is_uuid5_of_ts_type_severity_and_device():
    ts = "2026-10-18T03:14:07.360Z"

    alert_id = alerts.alert_id_for(ts, "fall", "critical", WEARABLE)

    expected = uuid.uuid5(alerts.ALERT_NAMESPACE, f"{ts}|fall|critical|{WEARABLE}")
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
    ("ts", "alert_type", "severity", "device_id"),
    [
        ("2026-10-18T03:14:07.361Z", "hr_high", "critical", WEARABLE),
        ("2026-10-18T03:14:07.360Z", "hr_low", "critical", WEARABLE),
        ("2026-10-18T03:14:07.360Z", "hr_high", "warning", WEARABLE),
        ("2026-10-18T03:14:07.360Z", "hr_high", "critical", "wearable-0002"),
    ],
)
def test_alert_id_changes_with_each_input(ts, alert_type, severity, device_id):
    base = alerts.alert_id_for(
        "2026-10-18T03:14:07.360Z", "hr_high", "critical", WEARABLE
    )

    assert alerts.alert_id_for(ts, alert_type, severity, device_id) != base


def test_warning_and_critical_with_the_same_ts_get_different_alert_ids():
    ts = "2026-10-18T03:14:07.360Z"

    warning = alerts.alert_id_for(ts, "hr_high", "warning", WEARABLE)
    critical = alerts.alert_id_for(ts, "hr_high", "critical", WEARABLE)

    assert warning != critical


# ---- message shape (criterion 1) ----


def test_fall_alert_has_exactly_the_contract_fields():
    msg = valid_fall()

    alert = alerts.build_fall_alert(msg, "res-0003")

    assert set(alert) == ALERT_FIELDS
    assert alert["schema_version"] == 1
    assert alert["alert_type"] == "fall"
    assert alert["severity"] == "critical"
    assert alert["resident_id"] == "res-0003"
    assert alert["device_id"] == WEARABLE
    assert alert["event_ts"] == msg.ts
    assert alert["received_at"] == "2026-10-18T03:12:00.410Z"
    assert alert["source_message_id"] == msg.message_id
    assert alert["alert_id"] == alerts.alert_id_for(
        msg.ts, "fall", "critical", WEARABLE
    )


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


# ---- device vitals_alert (vitals-alerts criteria 1-3) ----


def valid_vitals_alert(**overrides) -> ValidMessage:
    result = validate_event(
        rule_event("vitals_alert", **overrides), frozenset({"vitals_alert"})
    )
    assert isinstance(result, ValidMessage), result
    return result


@pytest.mark.parametrize(
    ("overrides", "alert_type"),
    [
        ({}, "hr_high"),
        ({"value": 40, "limit": 45, "direction": "below"}, "hr_low"),
        ({"value": 0, "limit": 45, "direction": "below"}, "hr_low"),
        (
            {"metric": "spo2_pct", "value": 85, "limit": 88, "direction": "below"},
            "spo2_low",
        ),
    ],
    ids=["hr_high", "hr_low", "no_pulse", "spo2_low"],
)
def test_device_vitals_alert_maps_metric_and_direction_to_alert_type(
    overrides, alert_type
):
    alert = alerts.build_critical_alert(valid_vitals_alert(**overrides), "res-0002")

    assert alert["alert_type"] == alert_type
    assert alert["severity"] == "critical"


def test_device_vitals_alert_has_exactly_the_contract_fields():
    msg = valid_vitals_alert()

    alert = alerts.build_critical_alert(msg, "res-0002")

    assert set(alert) == ALERT_FIELDS
    assert alert["alert_id"] == alerts.alert_id_for(
        msg.ts, "hr_high", "critical", WEARABLE
    )
    assert alert["event_ts"] == msg.ts
    assert alert["source_message_id"] == msg.message_id
    assert alert["details"] == {
        "metric": "hr_bpm",
        "value": 140,
        "limit": 130,
        "direction": "above",
        "held_s": 10,
    }


def test_build_critical_alert_still_builds_falls():
    alert = alerts.build_critical_alert(valid_fall(), "res-0001")

    assert alert == alerts.build_fall_alert(valid_fall(), "res-0001") | {
        "source_message_id": alert["source_message_id"]
    }
    assert alert["alert_type"] == "fall"
    assert alert["severity"] == "critical"


# ---- cloud warning (vitals-alerts criteria 3, 10) ----


def newest_entry() -> dict:
    return {
        "message_id": "00000000-0000-4000-8000-000000000002",
        "ts": "2026-10-18T03:14:00.000Z",
        "received_at": "2026-10-18T03:14:00.410Z",
        "hr_bpm": Decimal(128),
    }


def finding(**overrides):
    from vitals_check import Finding

    base = {
        "alert_type": "hr_high",
        "metric": "hr_bpm",
        "direction": "above",
        "value": Decimal(128),
        "previous_value": Decimal(125),
        "limit": Decimal(120),
    }
    return Finding(**{**base, **overrides})


def test_vitals_warning_has_exactly_the_contract_fields():
    entry = newest_entry()

    alert = alerts.build_vitals_warning(WEARABLE, "res-0002", entry, finding())

    assert set(alert) == ALERT_FIELDS
    assert alert["alert_type"] == "hr_high"
    assert alert["severity"] == "warning"
    assert alert["resident_id"] == "res-0002"
    assert alert["event_ts"] == entry["ts"]
    assert alert["received_at"] == entry["received_at"]
    assert alert["source_message_id"] == entry["message_id"]
    assert alert["alert_id"] == alerts.alert_id_for(
        entry["ts"], "hr_high", "warning", WEARABLE
    )


def test_vitals_warning_details_are_plain_numbers():
    alert = alerts.build_vitals_warning(WEARABLE, "res-0002", newest_entry(), finding())

    assert alert["details"] == {
        "metric": "hr_bpm",
        "value": 128,
        "limit": 120,
        "direction": "above",
        "previous_value": 125,
    }
    assert type(alert["details"]["value"]) is int


def test_skin_temperature_warning_keeps_exact_decimals_in_json():
    f = finding(
        alert_type="skin_temp_high",
        metric="skin_temp_c",
        value=Decimal("38.4"),
        previous_value=Decimal("38.2"),
        limit=Decimal("38.0"),
    )

    text = alerts.encode(
        alerts.build_vitals_warning(WEARABLE, "res-0002", newest_entry(), f)
    )

    assert '"value":38.4' in text
    assert '"previous_value":38.2' in text
    assert '"limit":38' in text


def test_warning_and_device_alert_for_the_same_ts_never_collide():
    msg = valid_vitals_alert(ts="2026-10-18T03:14:00.000Z")
    critical = alerts.build_critical_alert(msg, "res-0002")
    warning = alerts.build_vitals_warning(
        WEARABLE, "res-0002", newest_entry(), finding()
    )

    assert critical["event_ts"] == warning["event_ts"]
    assert critical["alert_type"] == warning["alert_type"]
    assert critical["alert_id"] != warning["alert_id"]
