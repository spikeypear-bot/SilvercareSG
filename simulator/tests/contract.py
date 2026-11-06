"""A validator for device messages, written from docs/metrics.md only.

It deliberately shares no code with simulator/src, so a mistake in the simulator's
payload module can't hide itself here. `problems(topic, payload)` returns a list of
reasons (empty if the message follows the contract).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

TOPIC_RE = re.compile(
    r"^silvercare/v1/(?P<device>(?P<type>wearable|bedroom)-[0-9]{4})/(?P<message>[a-z_]+)$"
)
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
MAX_PAYLOAD_BYTES = 8 * 1024

# Messages each device type may send (Messages, Summary).
ALLOWED = {
    "wearable": {"vitals", "vitals_alert", "fall", "alert_cancel", "status"},
    "bedroom": {"motion", "bed", "status"},
}
# The IoT rules add these; a payload that has any of them is dropped.
FORBIDDEN = {"device_id", "message", "received_at"}
# Never personal identifiers in a payload.
PERSONAL = {"resident_id", "name", "display_name", "nric", "address", "home_id"}
ENVELOPE = {"schema_version", "message_id", "ts"}

FIELDS = {
    ("wearable", "vitals"): {
        "hr_bpm",
        "spo2_pct",
        "skin_temp_c",
        "activity_pct",
        "accel_g",
    },
    ("wearable", "vitals_alert"): {"metric", "value", "limit", "direction", "held_s"},
    ("wearable", "fall"): {"confidence", "peak_g", "still_s", "accel_window"},
    # Envelope only: any other field rejects the message.
    ("wearable", "alert_cancel"): set(),
    ("wearable", "status"): {"battery_pct", "worn", "charging", "fw_version"},
    ("bedroom", "status"): {"battery_pct", "fw_version"},
    ("bedroom", "motion"): {"motion"},
    ("bedroom", "bed"): {"occupied"},
}


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _int_in(
    payload: dict, name: str, low: int, high: int, nullable: bool, out: list[str]
) -> None:
    value = payload.get(name)
    if value is None and nullable:
        return
    if not is_int(value):
        out.append(f"{name}: not an integer")
    elif not low <= value <= high:
        out.append(f"{name}: out of range")


def _bool(payload: dict, name: str, out: list[str]) -> None:
    if not isinstance(payload.get(name), bool):
        out.append(f"{name}: not a boolean")


def _fw(payload: dict, out: list[str]) -> None:
    value = payload.get("fw_version")
    if not isinstance(value, str) or not value or len(value) > 32:
        out.append("fw_version: not a string of at most 32 chars")


def _envelope(payload: dict, out: list[str]) -> None:
    version = payload.get("schema_version")
    if not is_int(version) or version != 1:
        out.append("schema_version: not integer 1")
    message_id = payload.get("message_id")
    if not isinstance(message_id, str) or not UUID4_RE.fullmatch(message_id):
        out.append("message_id: not a UUID v4")
    ts = payload.get("ts")
    if not isinstance(ts, str) or not TS_RE.fullmatch(ts):
        out.append("ts: not ISO 8601 UTC with ms and Z")
    else:
        try:
            datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            out.append("ts: not a real date/time")


def _vitals(payload: dict, out: list[str]) -> None:
    _int_in(payload, "hr_bpm", 20, 250, True, out)
    _int_in(payload, "spo2_pct", 50, 100, True, out)
    temp = payload.get("skin_temp_c")
    if temp is not None:
        if not is_number(temp):
            out.append("skin_temp_c: not a number")
        else:
            if not 20.0 <= temp <= 45.0:
                out.append("skin_temp_c: out of range")
            if abs(temp * 10 - round(temp * 10)) > 1e-9:
                out.append("skin_temp_c: more than 1 decimal")
    _int_in(payload, "activity_pct", 0, 100, False, out)
    accel = payload.get("accel_g")
    if not isinstance(accel, dict) or set(accel) != {"x", "y", "z"}:
        out.append("accel_g: not an object with exactly x, y, z")
        return
    for axis in ("x", "y", "z"):
        if not is_number(accel[axis]):
            out.append(f"accel_g.{axis}: not a number")
        elif not -16 <= accel[axis] <= 16:
            out.append(f"accel_g.{axis}: out of range")


# vitals_alert (docs/metrics.md, wearable -> vitals_alert): the fixed device limits.
ALERT_LIMITS = {"hr_bpm": {45, 130}, "spo2_pct": {88}}
# hr_bpm 0 = no pulse while worn; otherwise the vitals ranges.
VALUE_OK = {
    "hr_bpm": lambda v: v == 0 or 20 <= v <= 250,
    "spo2_pct": lambda v: 50 <= v <= 100,
}
FALL_WINDOW_FIELDS = {"hz", "start_offset_ms", "samples"}
FALL_SAMPLES = 50


def _vitals_alert(payload: dict, out: list[str]) -> None:
    metric = payload.get("metric")
    if metric not in ALERT_LIMITS:
        out.append("metric: not hr_bpm or spo2_pct")
        return
    value = payload.get("value")
    if not is_int(value):
        out.append("value: not an integer")
    elif not VALUE_OK[metric](value):
        out.append("value: out of range")
    limit = payload.get("limit")
    if not is_int(limit):
        out.append("limit: not an integer")
    elif limit not in ALERT_LIMITS[metric]:
        out.append("limit: not a device limit of the metric")
    direction = payload.get("direction")
    if direction not in ("above", "below"):
        out.append("direction: not above or below")
    elif direction == "above" and metric == "spo2_pct":
        out.append("direction: spo2_pct is only below")
    elif direction == "above" and is_int(value) and value == 0:
        out.append("direction: value 0 is only below")
    _int_in(payload, "held_s", 10, 600, False, out)


def _number_in(payload: dict, name: str, low: float, high: float, out: list[str]):
    value = payload.get(name)
    if not is_number(value):
        out.append(f"{name}: not a number")
    elif not low <= value <= high:
        out.append(f"{name}: out of range")


def _fall(payload: dict, out: list[str]) -> None:
    _number_in(payload, "confidence", 0.0, 1.0, out)
    _number_in(payload, "peak_g", 0.0, 16.0, out)
    _int_in(payload, "still_s", 0, 600, False, out)
    window = payload.get("accel_window")
    if not isinstance(window, dict) or set(window) != FALL_WINDOW_FIELDS:
        out.append(
            "accel_window: not an object with exactly hz, start_offset_ms, samples"
        )
        return
    if not is_int(window["hz"]) or window["hz"] != 25:
        out.append("accel_window.hz: not integer 25")
    offset = window["start_offset_ms"]
    if not is_int(offset) or offset != -1000:
        out.append("accel_window.start_offset_ms: not integer -1000")
    samples = window["samples"]
    if not isinstance(samples, list) or len(samples) != FALL_SAMPLES:
        out.append("accel_window.samples: not a list of 50 samples")
        return
    for sample in samples:
        if (
            not isinstance(sample, list)
            or len(sample) != 3
            or not all(is_number(v) for v in sample)
        ):
            out.append("accel_window.samples: a sample is not [x, y, z] numbers")
            return
        if not all(-16 <= v <= 16 for v in sample):
            out.append("accel_window.samples: an axis is out of range")
            return


def problems(topic: str, payload: Any) -> list[str]:
    out: list[str] = []
    match = TOPIC_RE.fullmatch(topic) if isinstance(topic, str) else None
    if not match:
        return ["topic: not silvercare/v1/<device_id>/<message>"]
    dtype, message = match["type"], match["message"]
    if message not in ALLOWED[dtype]:
        return [f"topic: {dtype} can't send {message}"]
    if not isinstance(payload, dict):
        return ["payload: not a JSON object"]
    size = len(json.dumps(payload).encode("utf-8"))
    if size >= MAX_PAYLOAD_BYTES:
        out.append("payload: 8 KB or more")
    for name in sorted(FORBIDDEN & set(payload)):
        out.append(f"{name}: forbidden in the payload")
    for name in sorted(PERSONAL & set(payload)):
        out.append(f"{name}: personal identifier in the payload")
    expected = FIELDS.get((dtype, message))
    if expected is None:
        return [*out, f"{message}: not sent by the simulator"]
    extra = set(payload) - ENVELOPE - expected - FORBIDDEN - PERSONAL
    missing = (ENVELOPE | expected) - set(payload)
    out.extend(f"{name}: missing" for name in sorted(missing))
    out.extend(f"{name}: not in the contract" for name in sorted(extra))
    _envelope(payload, out)
    if (dtype, message) == ("wearable", "vitals"):
        _vitals(payload, out)
    elif (dtype, message) == ("wearable", "vitals_alert"):
        _vitals_alert(payload, out)
    elif (dtype, message) == ("wearable", "fall"):
        _fall(payload, out)
    elif (dtype, message) == ("wearable", "status"):
        _int_in(payload, "battery_pct", 0, 100, False, out)
        _bool(payload, "worn", out)
        _bool(payload, "charging", out)
        _fw(payload, out)
    elif (dtype, message) == ("bedroom", "status"):
        _int_in(payload, "battery_pct", 0, 100, True, out)
        _fw(payload, out)
    elif message == "motion":
        _bool(payload, "motion", out)
    elif message == "bed":
        _bool(payload, "occupied", out)
    return out
