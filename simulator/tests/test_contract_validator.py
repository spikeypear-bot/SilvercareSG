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
