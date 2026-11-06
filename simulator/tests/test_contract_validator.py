"""The test validator itself rejects what the contract rejects (so a passing
contract test means something)."""

from __future__ import annotations

import copy

import contract
import pytest

VITALS = {
    "schema_version": 1,
    "message_id": "5f0c9a52-3c1e-4c8e-9f57-0d6f2b7a1e11",
    "ts": "2026-10-18T03:12:00.000Z",
    "hr_bpm": 72,
    "spo2_pct": 97,
    "skin_temp_c": 33.4,
    "activity_pct": 12,
    "accel_g": {"x": 0.02, "y": -0.11, "z": 0.99},
}
TOPIC = "silvercare/v1/wearable-0007/vitals"


def test_contract_example_is_valid():
    assert contract.problems(TOPIC, VITALS) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hr_bpm", 19),
        ("hr_bpm", 251),
        ("hr_bpm", 72.0),
        ("hr_bpm", True),
        ("spo2_pct", 101),
        ("skin_temp_c", 33.45),
        ("skin_temp_c", 45.1),
        ("activity_pct", None),
        ("schema_version", 2),
        ("schema_version", True),
        ("message_id", "5f0c9a52-3c1e-1c8e-9f57-0d6f2b7a1e11"),
        ("ts", "2026-10-18T03:12:00Z"),
        ("ts", "2026-10-18T11:12:00.000+08:00"),
        ("ts", "2026-02-30T03:12:00.000Z"),
        ("accel_g", {"x": 0, "y": 0}),
        ("accel_g", {"x": 0, "y": 0, "z": 16.5}),
    ],
)
def test_validator_rejects_bad_field(field, value):
    payload = {**VITALS, field: value}
    assert contract.problems(TOPIC, payload) != []


@pytest.mark.parametrize("field", ["device_id", "message", "received_at", "home_id"])
def test_validator_rejects_forbidden_field(field):
    assert contract.problems(TOPIC, {**VITALS, field: "x"}) != []


def test_validator_rejects_missing_field():
    payload = copy.deepcopy(VITALS)
    del payload["activity_pct"]
    assert contract.problems(TOPIC, payload) != []


@pytest.mark.parametrize(
    "topic",
    [
        "silvercare/v1/bedroom-0001/vitals",
        "silvercare/v2/wearable-0001/vitals",
        "silvercare/v1/wearable-01/vitals",
        "silvercare/v1/wearable-0001/vitals/extra",
    ],
)
def test_validator_rejects_bad_topic(topic):
    assert contract.problems(topic, VITALS) != []


def test_validator_rejects_payload_of_8kb():
    payload = {**VITALS, "pad": "x" * 8192}
    assert "payload: 8 KB or more" in contract.problems(TOPIC, payload)


# ---- vitals_alert, fall, alert_cancel (docs/features/simulator-alert-scenarios.md) ----

ALERT_TOPIC = "silvercare/v1/wearable-0007/vitals_alert"
FALL_TOPIC = "silvercare/v1/wearable-0007/fall"
CANCEL_TOPIC = "silvercare/v1/wearable-0007/alert_cancel"
ENVELOPE = {
    "schema_version": 1,
    "message_id": "3b9e2f1a-7c4d-4e8b-9a6f-1d2c3b4a5e60",
    "ts": "2026-10-18T03:15:10.000Z",
}
# The contract's examples (docs/metrics.md).
HR_HIGH = {
    **ENVELOPE,
    "metric": "hr_bpm",
    "value": 140,
    "limit": 130,
    "direction": "above",
    "held_s": 10,
}
NO_PULSE = {**HR_HIGH, "value": 0, "limit": 45, "direction": "below"}
SPO2_LOW = {**HR_HIGH, "metric": "spo2_pct", "value": 84, "limit": 88}
SPO2_LOW["direction"] = "below"


def fall_payload(**window) -> dict:
    samples = [[0.01, -0.1, 0.99]] * 25 + [[2.0, -2.5, 1.9]] + [[0.98, 0.1, 0.05]] * 24
    return {
        **ENVELOPE,
        "confidence": 0.91,
        "peak_g": 3.8,
        "still_s": 20,
        "accel_window": {
            "hz": 25,
            "start_offset_ms": -1000,
            "samples": samples,
            **window,
        },
    }


@pytest.mark.parametrize("payload", [HR_HIGH, NO_PULSE, SPO2_LOW])
def test_vitals_alert_contract_examples_are_valid(payload):
    assert contract.problems(ALERT_TOPIC, payload) == []


@pytest.mark.parametrize(
    "value", [{"value": 20}, {"value": 250, "limit": 130}, {"value": 44, "limit": 45}]
)
def test_vitals_alert_hr_values_at_the_range_edges_are_valid(value):
    payload = {**HR_HIGH, **value}
    if payload["limit"] == 45:
        payload["direction"] = "below"
    assert contract.problems(ALERT_TOPIC, payload) == []


@pytest.mark.parametrize(
    "change",
    [
        {"metric": "skin_temp_c"},
        {"metric": None},
        {"value": 19},
        {"value": 251},
        {"value": -1},
        {"value": 140.0},
        {"value": True},
        {"value": None},
        {"limit": 140},
        {"limit": 88},
        {"limit": 130.0},
        {"direction": "up"},
        {"direction": None},
        {"held_s": 9},
        {"held_s": 601},
        {"held_s": 10.0},
        # value 0 only with direction below
        {"value": 0, "direction": "above"},
    ],
)
def test_validator_rejects_bad_hr_vitals_alert(change):
    assert contract.problems(ALERT_TOPIC, {**HR_HIGH, **change}) != []


@pytest.mark.parametrize(
    "change",
    [
        {"direction": "above"},  # spo2_pct only below
        {"value": 49},
        {"value": 101},
        {"value": 0},
        {"limit": 45},
        {"limit": 130},
    ],
)
def test_validator_rejects_bad_spo2_vitals_alert(change):
    assert contract.problems(ALERT_TOPIC, {**SPO2_LOW, **change}) != []


@pytest.mark.parametrize("field", ["metric", "value", "limit", "direction", "held_s"])
def test_validator_rejects_vitals_alert_missing_field(field):
    payload = {k: v for k, v in HR_HIGH.items() if k != field}
    assert contract.problems(ALERT_TOPIC, payload) != []


def test_fall_contract_example_is_valid():
    assert contract.problems(FALL_TOPIC, fall_payload()) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", 1.01),
        ("confidence", -0.01),
        ("confidence", "0.9"),
        ("confidence", True),
        ("peak_g", 16.01),
        ("peak_g", -0.1),
        ("peak_g", None),
        ("still_s", 601),
        ("still_s", -1),
        ("still_s", 20.0),
        ("accel_window", None),
        ("accel_window", []),
    ],
)
def test_validator_rejects_bad_fall_field(field, value):
    assert contract.problems(FALL_TOPIC, {**fall_payload(), field: value}) != []


GOOD_SAMPLES = fall_payload()["accel_window"]["samples"]


@pytest.mark.parametrize(
    "window",
    [
        {"hz": 50},
        {"hz": 25.0},
        {"start_offset_ms": 0},
        {"start_offset_ms": -1000.0},
        {"samples": GOOD_SAMPLES[:49]},
        {"samples": [*GOOD_SAMPLES, [0.0, 0.0, 1.0]]},
        {"samples": [[0.0, 0.0]] + GOOD_SAMPLES[1:]},
        {"samples": [[0.0, 0.0, 1.0, 0.0]] + GOOD_SAMPLES[1:]},
        {"samples": [[0.0, "0", 1.0]] + GOOD_SAMPLES[1:]},
        {"samples": [[0.0, True, 1.0]] + GOOD_SAMPLES[1:]},
        {"samples": [{"x": 0, "y": 0, "z": 1}] + GOOD_SAMPLES[1:]},
        {"samples": GOOD_SAMPLES[:49] + [[16.5, 0.0, 0.0]]},
        {"samples": GOOD_SAMPLES[:49] + [[0.0, -16.01, 0.0]]},
        {"extra": 1},
    ],
)
def test_validator_rejects_bad_fall_accel_window(window):
    assert contract.problems(FALL_TOPIC, fall_payload(**window)) != []


def test_validator_accepts_fall_samples_at_plus_minus_16():
    samples = GOOD_SAMPLES[:48] + [[16, -16, 0], [-16.0, 16.0, 0.0]]
    assert contract.problems(FALL_TOPIC, fall_payload(samples=samples)) == []


def test_validator_rejects_fall_missing_accel_window():
    payload = {k: v for k, v in fall_payload().items() if k != "accel_window"}
    assert contract.problems(FALL_TOPIC, payload) != []


def test_alert_cancel_contract_example_is_valid():
    assert contract.problems(CANCEL_TOPIC, dict(ENVELOPE)) == []


@pytest.mark.parametrize(
    "extra", [{"reason": "ok"}, {"pressed": True}, {"hr_bpm": None}, {"x": {}}]
)
def test_validator_rejects_alert_cancel_with_any_other_field(extra):
    assert contract.problems(CANCEL_TOPIC, {**ENVELOPE, **extra}) != []


@pytest.mark.parametrize("field", ["schema_version", "message_id", "ts"])
def test_validator_rejects_alert_cancel_missing_envelope_field(field):
    payload = {k: v for k, v in ENVELOPE.items() if k != field}
    assert contract.problems(CANCEL_TOPIC, payload) != []


@pytest.mark.parametrize(
    ("message", "payload"),
    [
        ("fall", fall_payload()),
        ("vitals_alert", HR_HIGH),
        ("alert_cancel", ENVELOPE),
    ],
)
def test_validator_rejects_alert_messages_from_a_bedroom_sensor(message, payload):
    topic = f"silvercare/v1/bedroom-0007/{message}"
    assert contract.problems(topic, payload) != []
