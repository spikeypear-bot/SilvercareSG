"""Smoke test one IoT device against AWS IoT Core (docs/features/iot-core.md).

Run with `make smoke-test DEVICE=<id> ENDPOINT=<iot_endpoint>` in the dev container,
after `make device-certs`. Authenticates as the device with its own key and certificate
(MQTT over TLS, port 8883, CA devices/AmazonRootCA1.pem). Uses no AWS credentials.

Each check runs on a fresh connection:
  1. connect with client ID = <id>                        -> connected
  2. publish a synthetic status message (QoS 1), own topic -> PUBACK
  3. publish to another device's status topic              -> broker closes connection
  4. publish a message type the device type may not send   -> broker closes connection
  5. connect with a client ID that isn't <id>              -> refused or closed

With --ingest (docs/features/ingest-live.md), after those checks pass it also
publishes, on one more connection as the device: one valid synthetic message of each
type the device may send, the first of them again (a duplicate), and one invalid
message (wearable: vitals with hr_bpm 999; bedroom: bed with occupied "yes"). It then
prints the message IDs and the scripts/ingest-check.py command that checks they were
stored, plus its --history variant that also checks the S3 history
(docs/features/ingest-history.md). Those steps need AWS credentials and run on the host.
A wearable's fall also raises a critical alert, emailed to caregivers and nurses at
once (docs/features/alert-routing.md); the script prints that expectation too.

With --alerts (wearables only; docs/features/vitals-alerts.md, criterion 27), after the
checks (and the --ingest messages, if given) pass it publishes, on one more connection
as the device, synthetic messages that exercise the vitals alerts, with all other
fields normal:

  1. vitals        hr_bpm 180                       -> past the warning limit, not yet 2
  2. vitals        hr_bpm 185                       -> hr_high warning
  3. vitals_alert  hr_bpm 140 above 130, held 10 s  -> hr_high critical
  4. vitals_alert  hr_bpm 0 below 45, held 10 s     -> hr_low critical (no pulse)
  5. vitals        hr_bpm 182                       -> hr_high warning, suppressed

The vitals are well above the default limit (120) so a resident's personal limit
(THRESHOLDS item) is unlikely to hide the warning. It pauses after the first message
so telemetry stores it before the second arrives (two at once run in parallel and
can't see each other), and before the last so the first warning is stored before the
repeat. Then it prints the message IDs and what to expect. Needs no AWS credentials.

With --cancel (wearables only; docs/features/alert-cancel.md, criterion 17), after the
checks (and the --ingest and --alerts messages, if given) pass it publishes, on one
more connection as the device:

  1. fall          a synthetic fall                 -> fall alert email
  2. alert_cancel  the "I'm OK" button, 5 s later   -> "cancelled" update email

then prints the message IDs and what to expect: the fall email to caregivers and nurses
at once, then the cancelled update email to both (docs/features/alert-routing.md,
criterion 18).
The cancel also cancels any other open alert of the wearable (e.g. from --alerts).
Needs no AWS credentials.

With --low-battery (any device type; docs/features/watchdog.md, criterion 18a), after
the checks (and the other flags' messages, if given) pass it publishes, on one more
connection as the device, one `status` with battery_pct 10, not charging (a wearable
also worn), then prints what to expect: within 5 minutes (the next watchdog run) a
"SilverCare device problem: Low battery" email to the ops team and the resident's
caregivers, none to the nurses; the plain smoke test (battery 100) resolves it quietly
at the next run; and about 15 minutes after the device's last status it is reported
offline. Use an assigned device the simulator isn't sending for. Needs no AWS
credentials.

With --possible-fall (bedroom units only; docs/features/possible-fall.md, criterion
15), after the checks (and the other flags' messages, if given) pass it prints the
test-mode item to create in the DynamoDB console (silvercare-registry, JSON view), with
sk set to the current UTC time, then publishes, on one more connection as the device,
`bed` occupied false and, 3 s later, `motion` false, and prints what to expect: about
2-7 minutes later a "SilverCare CRITICAL alert: Possible fall" email with "Test mode:
yes" to the caregivers and the nurses (without the item, the real rule applies: night
only, 20 minutes). With --back-in-bed (bedroom units only) it publishes `bed` occupied
true: within 5 minutes a "SilverCare update: Possible fall - <resident> resolved" email
to the same people; then delete the test-mode item (it also expires after 2 hours).
Both need no AWS credentials: the user creates and deletes the item.

Prints only check names, PASS / FAIL, message IDs, and the test-mode item (device ID
and time only), never payloads, keys, or certificates. Exits 0 only if every check
passes.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import ssl
import sys
import time
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TextIO

import paho.mqtt.client as mqtt

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CA_FILE = REPO_ROOT / "devices" / "AmazonRootCA1.pem"
DEFAULT_KEYS_DIR = REPO_ROOT / "secrets" / "device-keys"

PORT = 8883
TIMEOUT_S = 10.0
# AWS IoT Core allows one connect per second per client ID and drops faster reconnects.
# Every check opens a new connection with the same client ID, so wait between them.
RECONNECT_PAUSE_S = 1.5
SCHEMA_VERSION = 1
FW_VERSION = "1.0.0"

DEVICE_ID_RE = re.compile(r"^(wearable|bedroom)-([0-9]{4})$")
ENDPOINT_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
MESSAGE_RE = re.compile(r"^[a-z]+(_[a-z]+)*$")

# Per type: the messages --ingest sends (one valid message each), and one the IoT policy
# doesn't allow (docs/metrics.md). The wearable policy also allows vitals_alert, which
# --ingest leaves out so it raises no critical alert; --alerts sends it.
ALLOWED_MESSAGES = {
    "wearable": ("vitals", "fall", "status"),
    "bedroom": ("motion", "bed", "status"),
}
DISALLOWED_MESSAGE = {"wearable": "motion", "bedroom": "vitals"}

CHECK_CONNECT = "1 connect with own client ID"
CHECK_PUBLISH = "2 publish status to own topic (PUBACK)"
CHECK_OTHER_DEVICE = "3 publish to another device's topic is refused"
CHECK_DISALLOWED = "4 publish a disallowed message type is refused"
CHECK_OTHER_CLIENT_ID = "5 connect with another client ID is refused"
CHECK_INGEST = "6 publish ingest test messages (PUBACK for each)"
CHECK_ALERTS = "7 publish alert test messages (PUBACK for each)"
CHECK_CANCEL = "8 publish cancel test messages (PUBACK for each)"
CHECK_LOW_BATTERY = "9 publish low-battery status (PUBACK)"
CHECK_POSSIBLE_FALL = "10 publish possible-fall test messages (PUBACK for each)"
CHECK_BACK_IN_BED = "11 publish back-in-bed message (PUBACK)"

# --possible-fall (possible-fall criterion 15): seconds between the bed and the motion
# message, both in their ts and in real time.
POSSIBLE_FALL_PAUSE_S = 3.0
TEST_MODE_ENTITY = "TEST_MODE"

# --low-battery (watchdog criterion 18a): below the watchdog's limit of 20 %.
LOW_BATTERY_PCT = 10

# --alerts (vitals-alerts criterion 27). Seconds between the messages' ts, so the
# vitals pair is well within the cloud check's 3-minute pairing limit.
ALERT_TS_STEP_S = 5
# Pause after the first vitals, so telemetry has stored it before the second arrives:
# two published together run in parallel and neither sees the other (2 in a row).
ALERTS_PAIR_PAUSE_S = 3.0
# Pause before the last (repeat) message, so alert-processor has stored the first
# warning before the repeat arrives (the alerts queue is not ordered).
ALERTS_REPEAT_PAUSE_S = 10.0
# --alerts heart rates: well above the default limit (120), so a resident's personal
# limit (e.g. 130) doesn't turn the warning off.
ALERTS_HR_VALUES = (180, 185, 182)
# The wearable's fixed vitals_alert limits (docs/metrics.md).
DEVICE_HR_HIGH_LIMIT = 130
DEVICE_HR_LOW_LIMIT = 45
DEVICE_HELD_S = 10

# --cancel (alert-cancel criterion 17): seconds between the fall and the alert_cancel,
# both in their ts and in real time, so alert-processor has stored the fall before the
# cancel arrives (the alerts queue is not ordered).
CANCEL_PAUSE_S = 5.0

# Fall accel_window shape (docs/metrics.md): 25 Hz, 1 s before to 1 s after impact.
FALL_WINDOW_HZ = 25
FALL_WINDOW_START_OFFSET_MS = -1000
FALL_WINDOW_SAMPLES = 50

# The invalid message per device type: (message type, field, invalid value).
INVALID_INGEST = {
    "wearable": ("vitals", "hr_bpm", 999),
    "bedroom": ("bed", "occupied", "yes"),
}


# ---- pure helpers (unit tested, no network) ----


def device_type(device_id: str) -> str:
    """Return wearable or bedroom from a device ID. Raises ValueError if malformed."""
    match = DEVICE_ID_RE.fullmatch(device_id)
    if not match:
        raise ValueError("device ID must match ^(wearable|bedroom)-[0-9]{4}$")
    return match.group(1)


def build_topic(device_id: str, message: str) -> str:
    """Return silvercare/v1/<device_id>/<message>."""
    device_type(device_id)
    if not MESSAGE_RE.fullmatch(message):
        raise ValueError("message must be lowercase letters only")
    return f"silvercare/v1/{device_id}/{message}"


def other_device_id(device_id: str) -> str:
    """Return a different device ID of the same type (for the cross-device check)."""
    dtype = device_type(device_id)
    number = int(device_id.rsplit("-", 1)[1])
    other = 2 if number == 1 else 1
    return f"{dtype}-{other:04d}"


def other_client_id(device_id: str) -> str:
    """Return a client ID that isn't the device's thing name."""
    device_type(device_id)
    return f"{device_id}-not-me"


def format_ts(now: datetime) -> str:
    """ISO 8601 UTC with milliseconds and Z, e.g. 2026-10-18T03:15:00.000Z."""
    now = now.astimezone(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def build_envelope(now: datetime | None = None, message_id: str | None = None) -> dict:
    """The fields every message has (docs/metrics.md, Envelope)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "message_id": message_id or str(uuid.uuid4()),
        "ts": format_ts(now or datetime.now(UTC)),
    }


def build_status_payload(
    dtype: str, now: datetime | None = None, message_id: str | None = None
) -> dict:
    """A valid synthetic status message for the device type (docs/metrics.md)."""
    payload = build_envelope(now, message_id)
    if dtype == "wearable":
        payload.update(
            {
                "battery_pct": 100,
                "worn": False,
                "charging": True,
                "fw_version": FW_VERSION,
            }
        )
    elif dtype == "bedroom":
        payload.update({"battery_pct": 100, "fw_version": FW_VERSION})
    else:
        raise ValueError("device type must be wearable or bedroom")
    return payload


def build_message_payload(
    dtype: str, message: str, now: datetime | None = None, message_id: str | None = None
) -> dict:
    """A valid synthetic message of any type the device type may send (docs/metrics.md)."""
    if message not in ALLOWED_MESSAGES.get(dtype, ()):
        raise ValueError(f"{dtype} devices don't send {message}")
    if message == "status":
        return build_status_payload(dtype, now, message_id)
    payload = build_envelope(now, message_id)
    if message == "vitals":
        payload.update(
            {
                "hr_bpm": 72,
                "spo2_pct": 97,
                "skin_temp_c": 33.4,
                "activity_pct": 12,
                "accel_g": {"x": 0.02, "y": -0.11, "z": 0.99},
            }
        )
    elif message == "fall":
        payload.update(
            {
                "confidence": 0.91,
                "peak_g": 3.8,
                "still_s": 20,
                "accel_window": {
                    "hz": FALL_WINDOW_HZ,
                    "start_offset_ms": FALL_WINDOW_START_OFFSET_MS,
                    "samples": [[0.01, -0.1, 0.99]] * FALL_WINDOW_SAMPLES,
                },
            }
        )
    elif message == "motion":
        payload["motion"] = True
    elif message == "bed":
        payload["occupied"] = False
    return payload


def build_ingest_messages(
    dtype: str,
    now: datetime | None = None,
    new_id: Callable[[], str] | None = None,
) -> list[tuple[str, str, dict]]:
    """The --ingest messages in send order, as (kind, message type, payload).

    kind is valid, duplicate, or invalid: one valid message per allowed type, the
    first valid one again, then one message that fails validation.
    """
    if dtype not in ALLOWED_MESSAGES:
        raise ValueError("device type must be wearable or bedroom")
    new_id = new_id or (lambda: str(uuid.uuid4()))
    now = now or datetime.now(UTC)
    valid = [
        ("valid", message, build_message_payload(dtype, message, now, new_id()))
        for message in ALLOWED_MESSAGES[dtype]
    ]
    _, first_message, first_payload = valid[0]
    duplicate = ("duplicate", first_message, dict(first_payload))
    invalid_message, field, value = INVALID_INGEST[dtype]
    invalid_payload = build_message_payload(dtype, invalid_message, now, new_id())
    invalid_payload[field] = value
    return [*valid, duplicate, ("invalid", invalid_message, invalid_payload)]


def build_vitals_payload(
    hr_bpm: int, now: datetime | None = None, message_id: str | None = None
) -> dict:
    """A valid synthetic wearable vitals message with the given heart rate."""
    payload = build_message_payload("wearable", "vitals", now, message_id)
    payload["hr_bpm"] = hr_bpm
    return payload


def build_vitals_alert_payload(
    value: int,
    limit: int,
    direction: str,
    now: datetime | None = None,
    message_id: str | None = None,
) -> dict:
    """A valid synthetic wearable vitals_alert for heart rate (docs/metrics.md)."""
    payload = build_envelope(now, message_id)
    payload.update(
        {
            "metric": "hr_bpm",
            "value": value,
            "limit": limit,
            "direction": direction,
            "held_s": DEVICE_HELD_S,
        }
    )
    return payload


def build_alert_messages(
    now: datetime | None = None,
    new_id: Callable[[], str] | None = None,
) -> list[tuple[str, str, dict]]:
    """The --alerts messages in send order, as (expected result, message type, payload).

    Each message's ts is ALERT_TS_STEP_S after the one before, starting at `now`.
    """
    new_id = new_id or (lambda: str(uuid.uuid4()))
    now = now or datetime.now(UTC)

    def at(step: int) -> datetime:
        return now + timedelta(seconds=step * ALERT_TS_STEP_S)

    return [
        (
            "no alert yet (1 of 2)",
            "vitals",
            build_vitals_payload(ALERTS_HR_VALUES[0], at(0), new_id()),
        ),
        (
            "hr_high warning",
            "vitals",
            build_vitals_payload(ALERTS_HR_VALUES[1], at(1), new_id()),
        ),
        (
            "hr_high critical",
            "vitals_alert",
            build_vitals_alert_payload(
                140, DEVICE_HR_HIGH_LIMIT, "above", at(2), new_id()
            ),
        ),
        (
            "hr_low critical (no pulse)",
            "vitals_alert",
            build_vitals_alert_payload(
                0, DEVICE_HR_LOW_LIMIT, "below", at(3), new_id()
            ),
        ),
        (
            "hr_high warning, suppressed",
            "vitals",
            build_vitals_payload(ALERTS_HR_VALUES[2], at(4), new_id()),
        ),
    ]


def report_alerts(
    device_id: str, messages: list[tuple[str, str, dict]], out: TextIO | None = None
) -> None:
    """Print the sent message IDs (no other fields) and what to expect."""
    out = out or sys.stdout
    print("Alert messages sent (expected result):", file=out)
    for expected, message, payload in messages:
        print(f"  {message:<12}  {payload['message_id']}  {expected}", file=out)
    print(
        f"Expect within a minute, if {device_id} is assigned to a resident (seed"
        " script) and you are subscribed to that resident:",
        file=out,
    )
    print(
        "  3 caregiver emails: 'SilverCare alert: High heart rate', 'SilverCare"
        " CRITICAL alert: High heart rate', and 'SilverCare CRITICAL alert: Low heart"
        " rate' (no pulse); the 2 critical ones say 'The care centre nurse has also"
        " been alerted.';",
        file=out,
    )
    print(
        "  at the same time, the 2 'SilverCare CRITICAL alert' emails to the nurse"
        " subscribers, with the line 'Sent to the care centre at once: critical"
        " alert.';",
        file=out,
    )
    print(
        "  3 new items under the resident in silvercare-alerts (hr_high warning,"
        " hr_high critical, hr_low critical) and 3 objects under alerts/ in the"
        " history bucket;",
        file=out,
    )
    print(
        "  an alert-processor log line with outcome 'suppressed' for the last"
        " vitals; both DLQs empty;",
        file=out,
    )
    print(
        "  about 5 minutes later, 1 'SilverCare ESCALATED alert: High heart rate'"
        " email (the warning) to the nurse subscribers (nothing acknowledges it).",
        file=out,
    )
    print(
        "No warning email? Check the resident's THRESHOLDS item in silvercare-registry:"
        " a personal heart-rate max above the readings makes them normal.",
        file=out,
    )
    print(
        "An unassigned device stores the 3 alerts under RESIDENT#unassigned and"
        " emails only the nurses, all 3 at once. Within 30 minutes of a run, a second run's alerts are all"
        " suppressed (same type and severity): wait 30 minutes to see the emails again.",
        file=out,
    )


def build_cancel_payload(
    now: datetime | None = None, message_id: str | None = None
) -> dict:
    """A valid wearable alert_cancel: the envelope only (docs/metrics.md)."""
    return build_envelope(now, message_id)


def build_cancel_messages(
    now: datetime | None = None,
    new_id: Callable[[], str] | None = None,
) -> list[tuple[str, str, dict]]:
    """The --cancel messages in send order, as (expected result, message type,
    payload): a fall at `now`, then an alert_cancel CANCEL_PAUSE_S later."""
    new_id = new_id or (lambda: str(uuid.uuid4()))
    now = now or datetime.now(UTC)
    return [
        (
            "fall alert",
            "fall",
            build_message_payload("wearable", "fall", now, new_id()),
        ),
        (
            "cancels the open alerts",
            "alert_cancel",
            build_cancel_payload(now + timedelta(seconds=CANCEL_PAUSE_S), new_id()),
        ),
    ]


def report_cancel(
    device_id: str, messages: list[tuple[str, str, dict]], out: TextIO | None = None
) -> None:
    """Print the sent message IDs (no other fields) and what to expect."""
    out = out or sys.stdout
    print("Cancel messages sent (expected result):", file=out)
    for expected, message, payload in messages:
        print(f"  {message:<12}  {payload['message_id']}  {expected}", file=out)
    print(
        f"Expect within a minute, if {device_id} is assigned to a resident (seed"
        " script) and you are subscribed to that resident:",
        file=out,
    )
    print(
        "  to caregivers and to the nurse subscribers: 'SilverCare CRITICAL alert:"
        " Fall' at once, then 'SilverCare update: Fall - <resident> cancelled'"
        " (cancelled by the resident; the nurses get it because they were emailed);",
        file=out,
    )
    print(
        "  no 'SilverCare ESCALATED alert' email: a fall goes to the nurses at once,"
        " with no escalation check;",
        file=out,
    )
    print(
        "  the fall item in silvercare-alerts has status 'cancelled', escalated_at,"
        " cancelled_at, expires_at, and status_notified_at, and no open_pk; an"
        " alert-processor log"
        " line with kind 'status_update' and outcome 'updated'; both DLQs empty.",
        file=out,
    )
    print(
        "Every other open alert of the wearable up to the press (e.g. from --alerts)"
        " (open or acknowledged) is cancelled too, with its own update email. An"
        " unassigned device cancels under RESIDENT#unassigned and emails only the"
        " nurses. If the outcome is 'no_open_alerts', the cancel was handled before"
        " the fall was stored: the fall stays open.",
        file=out,
    )


def build_low_battery_payload(
    dtype: str, now: datetime | None = None, message_id: str | None = None
) -> dict:
    """A valid synthetic status with battery_pct 10, not charging (a wearable is also
    worn, so it raises only the low-battery alert)."""
    payload = build_status_payload(dtype, now, message_id)
    payload["battery_pct"] = LOW_BATTERY_PCT
    if dtype == "wearable":
        payload["worn"] = True
        payload["charging"] = False
    return payload


def build_low_battery_messages(
    dtype: str,
    now: datetime | None = None,
    new_id: Callable[[], str] | None = None,
) -> list[tuple[str, str, dict]]:
    """The --low-battery messages, as (expected result, message type, payload)."""
    new_id = new_id or (lambda: str(uuid.uuid4()))
    return [
        (
            "battery_low at the next watchdog run",
            "status",
            build_low_battery_payload(dtype, now, new_id()),
        )
    ]


def report_low_battery(
    device_id: str, messages: list[tuple[str, str, dict]], out: TextIO | None = None
) -> None:
    """Print the sent message ID (no other fields) and what to expect."""
    out = out or sys.stdout
    print("Low-battery message sent (expected result):", file=out)
    for expected, message, payload in messages:
        print(f"  {message:<12}  {payload['message_id']}  {expected}", file=out)
    print(
        "Use an assigned device the simulator isn't sending for: a simulator"
        " heartbeat with a normal battery would overwrite this one and resolve or"
        " prevent the alert. Expect within 5 minutes (the next watchdog run), if"
        f" {device_id} is assigned to a resident:",
        file=out,
    )
    print(
        f"  'SilverCare device problem: Low battery - {device_id} (<resident>)' to the"
        " ops subscribers and to the resident's caregivers; no email to the nurses;",
        file=out,
    )
    print(
        "  a battery_low item (severity warning, open) under the resident in"
        " silvercare-alerts with notified_at and ops_notified_at; a watchdog log line"
        " with action 'raised'.",
        file=out,
    )
    print(
        "Then run the plain smoke test (it sends a normal battery): at the next"
        " watchdog run the alert is resolved quietly (no email; status 'resolved',"
        " resolved_by_role 'system').",
        file=out,
    )
    print(
        "About 15 minutes after the device's last status, the watchdog reports it"
        " offline ('SilverCare device problem: Device offline'), unless it sends"
        " another status first.",
        file=out,
    )


def build_bed_payload(
    occupied: bool, now: datetime | None = None, message_id: str | None = None
) -> dict:
    """A valid synthetic bedroom `bed` message (docs/metrics.md)."""
    payload = build_envelope(now, message_id)
    payload["occupied"] = occupied
    return payload


def build_motion_payload(
    motion: bool, now: datetime | None = None, message_id: str | None = None
) -> dict:
    """A valid synthetic bedroom `motion` message (docs/metrics.md)."""
    payload = build_envelope(now, message_id)
    payload["motion"] = motion
    return payload


def build_possible_fall_messages(
    now: datetime | None = None,
    new_id: Callable[[], str] | None = None,
) -> list[tuple[str, str, dict]]:
    """The --possible-fall messages in send order, as (expected result, message type,
    payload): bed occupied false at `now`, then motion false POSSIBLE_FALL_PAUSE_S
    later."""
    new_id = new_id or (lambda: str(uuid.uuid4()))
    now = now or datetime.now(UTC)
    return [
        ("bed empty", "bed", build_bed_payload(False, now, new_id())),
        (
            "no motion",
            "motion",
            build_motion_payload(
                False, now + timedelta(seconds=POSSIBLE_FALL_PAUSE_S), new_id()
            ),
        ),
    ]


def build_back_in_bed_messages(
    now: datetime | None = None,
    new_id: Callable[[], str] | None = None,
) -> list[tuple[str, str, dict]]:
    """The --back-in-bed message, as (expected result, message type, payload)."""
    new_id = new_id or (lambda: str(uuid.uuid4()))
    return [
        (
            "resolves the possible fall",
            "bed",
            build_bed_payload(True, now, new_id()),
        )
    ]


def build_test_mode_item(device_id: str, now: datetime | None = None) -> dict:
    """The registry test-mode item for a bedroom unit, in DynamoDB JSON (the console's
    JSON view), set at `now` (possible-fall criterion 3a)."""
    if device_type(device_id) != "bedroom":
        raise ValueError("test mode is for bedroom units only")
    return {
        "pk": {"S": f"DEVICE#{device_id}"},
        "sk": {"S": f"TEST_MODE#{format_ts(now or datetime.now(UTC))}"},
        "entity_type": {"S": TEST_MODE_ENTITY},
    }


def report_test_mode_item(
    device_id: str, now: datetime | None = None, out: TextIO | None = None
) -> None:
    """Print the test-mode item to create before the possible-fall messages."""
    out = out or sys.stdout
    print(
        "For a live check at any time of day, create this item now in the DynamoDB"
        " console (silvercare-registry -> Create item -> JSON view):",
        file=out,
    )
    print(f"  {json.dumps(build_test_mode_item(device_id, now))}", file=out)
    print(
        "It puts only this unit in test mode (any time counts as night, the wait is 2"
        " minutes) and expires by itself 2 hours after the time in sk. Without it,"
        " the real rule applies (vacated 22:00-07:00 SGT, 20 minutes).",
        file=out,
    )
    print(
        "Use a resident whose wearable the simulator isn't sending for (no status for"
        " 15 minutes counts as not worn) or who has none: a worn wearable stops the"
        " alert.",
        file=out,
    )


def report_possible_fall(
    device_id: str, messages: list[tuple[str, str, dict]], out: TextIO | None = None
) -> None:
    """Print the sent message IDs (no other fields) and what to expect."""
    out = out or sys.stdout
    print("Possible-fall messages sent (expected result):", file=out)
    for expected, message, payload in messages:
        print(f"  {message:<12}  {payload['message_id']}  {expected}", file=out)
    print(
        f"Expect, if {device_id} is assigned to a resident and in test mode, about 2-7"
        " minutes later (2 minutes plus the next watchdog run):",
        file=out,
    )
    print(
        "  'SilverCare CRITICAL alert: Possible fall - <resident>' with 'Test mode:"
        " yes' to the resident's caregivers and to the nurse subscribers at once;",
        file=out,
    )
    print(
        "  a possible_fall item (severity critical, open) under the resident in"
        " silvercare-alerts; a watchdog log line with action 'raised'.",
        file=out,
    )
    print(
        "Then run the smoke test with --back-in-bed (BACK_IN_BED=1) to resolve it.",
        file=out,
    )


def report_back_in_bed(
    device_id: str, messages: list[tuple[str, str, dict]], out: TextIO | None = None
) -> None:
    """Print the sent message ID (no other fields) and what to expect."""
    out = out or sys.stdout
    print("Back-in-bed message sent (expected result):", file=out)
    for expected, message, payload in messages:
        print(f"  {message:<12}  {payload['message_id']}  {expected}", file=out)
    print(
        f"Expect within 5 minutes (the next watchdog run), if {device_id} has an open"
        " possible fall: 'SilverCare update: Possible fall - <resident> resolved' to"
        " the caregivers and the nurse subscribers; the item has status 'resolved',"
        " resolved_by_role 'system', and status_notified_at.",
        file=out,
    )
    print(
        "Then delete the test-mode item (DEVICE#<unit> / TEST_MODE#...) in"
        " silvercare-registry; it also expires by itself 2 hours after it was set.",
        file=out,
    )


def ingest_check_command(
    device_id: str,
    valid_ids: Iterable[str],
    invalid_ids: Iterable[str],
    history: bool = False,
) -> str:
    """The command that checks the --ingest messages were stored (run on the host).

    With history=True, the variant that also checks the S3 history.
    """
    return (
        f"python scripts/ingest-check.py --device {device_id}"
        f" --message-ids {','.join(valid_ids)}"
        f" --invalid-ids {','.join(invalid_ids)}"
        f"{' --history' if history else ''}"
    )


def report_ingest(
    device_id: str, messages: list[tuple[str, str, dict]], out: TextIO | None = None
) -> None:
    """Print the sent message IDs (no other fields) and the next step."""
    out = out or sys.stdout
    print("Ingest messages sent:", file=out)
    for kind, message, payload in messages:
        print(f"  {kind:<9}  {message:<6}  {payload['message_id']}", file=out)
    valid_ids = [p["message_id"] for k, _, p in messages if k == "valid"]
    invalid_ids = [p["message_id"] for k, _, p in messages if k == "invalid"]
    if any(message == "fall" for _, message, _ in messages):
        print(
            f"Expect within a minute, if {device_id} is assigned to a resident: a"
            " 'SilverCare CRITICAL alert: Fall' email to caregivers and to the nurse"
            " subscribers at once (unassigned: nurses only).",
            file=out,
        )
    # The plain command stays the last line (scripts and tests read it from there).
    print(
        "Next, on the host with your AWS credentials, check silvercare-latest and"
        " the S3 history (takes up to 7 minutes):",
        file=out,
    )
    print(
        f"  {ingest_check_command(device_id, valid_ids, invalid_ids, history=True)}",
        file=out,
    )
    print("or silvercare-latest only:", file=out)
    print(f"  {ingest_check_command(device_id, valid_ids, invalid_ids)}", file=out)


def encode_payload(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def report(results: Iterable[tuple[str, bool]], out: TextIO | None = None) -> int:
    """Print PASS / FAIL per check and return the exit code (0 only if all passed)."""
    out = out or sys.stdout
    results = list(results)
    for name, passed in results:
        print(f"{'PASS' if passed else 'FAIL'}  {name}", file=out)
    passed_count = sum(1 for _, passed in results if passed)
    print(f"{passed_count}/{len(results)} checks passed", file=out)
    return 0 if results and passed_count == len(results) else 1


# ---- MQTT (needs the network; not unit tested) ----


class Connection:
    """One MQTT/TLS connection, driven by a manual loop (no thread, no auto reconnect)."""

    def __init__(self, endpoint: str, client_id: str, tls: dict[str, str]) -> None:
        self.endpoint = endpoint
        self.connected = False
        self.refused = False
        self.closed = False
        self.acked: set[int] = set()
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        self.client.tls_set(
            ca_certs=tls["ca"],
            certfile=tls["cert"],
            keyfile=tls["key"],
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            self.refused = True
        else:
            self.connected = True

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.closed = True

    def _on_publish(self, client, userdata, mid, reason_code, properties) -> None:
        self.acked.add(mid)

    def _run_until(self, done: Callable[[], bool]) -> None:
        deadline = time.monotonic() + TIMEOUT_S
        while not done() and time.monotonic() < deadline:
            if self.client.loop(timeout=0.2) != mqtt.MQTT_ERR_SUCCESS:
                self.closed = True

    def connect(self) -> str:
        """Return connected, refused, closed, timeout, or error."""
        try:
            self.client.connect(self.endpoint, PORT, keepalive=30)
        except (OSError, ValueError):
            # DNS, TCP, or TLS failure (ssl errors are OSErrors): the check can't run.
            return "error"
        self._run_until(lambda: self.connected or self.refused or self.closed)
        if self.connected:
            return "connected"
        if self.refused:
            return "refused"
        return "closed" if self.closed else "timeout"

    def publish(self, topic: str, payload: bytes) -> str:
        """Publish at QoS 1. Return puback, closed, or timeout."""
        info = self.client.publish(topic, payload, qos=1, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            self.closed = True
        self._run_until(lambda: info.mid in self.acked or self.closed)
        if info.mid in self.acked:
            return "puback"
        return "closed" if self.closed else "timeout"

    def close(self) -> None:
        """Best-effort clean disconnect, so the next check starts fresh.

        After a successful connection, waits RECONNECT_PAUSE_S: the next check usually
        reconnects with the same client ID, and AWS IoT drops a reconnect within 1 s.
        """
        with contextlib.suppress(OSError, ValueError):
            if self.connected and not self.closed:
                self.client.disconnect()
                self.client.loop(timeout=0.2)
        if self.connected:
            time.sleep(RECONNECT_PAUSE_S)


def run_checks(
    device_id: str, endpoint: str, tls: dict[str, str]
) -> list[tuple[str, bool]]:
    dtype = device_type(device_id)

    def with_connection(client_id: str, action: Callable[[Connection], bool]) -> bool:
        conn = Connection(endpoint, client_id, tls)
        try:
            return action(conn)
        finally:
            conn.close()

    def check_connect(conn: Connection) -> bool:
        return conn.connect() == "connected"

    def check_publish(conn: Connection) -> bool:
        if conn.connect() != "connected":
            return False
        topic = build_topic(device_id, "status")
        return (
            conn.publish(topic, encode_payload(build_status_payload(dtype))) == "puback"
        )

    def expect_publish_refused(
        topic: str, payload: dict
    ) -> Callable[[Connection], bool]:
        def check(conn: Connection) -> bool:
            if conn.connect() != "connected":
                return False
            return conn.publish(topic, encode_payload(payload)) == "closed"

        return check

    def check_other_client_id(conn: Connection) -> bool:
        return conn.connect() in ("refused", "closed")

    other_topic = build_topic(other_device_id(device_id), "status")
    disallowed_topic = build_topic(device_id, DISALLOWED_MESSAGE[dtype])
    return [
        (CHECK_CONNECT, with_connection(device_id, check_connect)),
        (CHECK_PUBLISH, with_connection(device_id, check_publish)),
        (
            CHECK_OTHER_DEVICE,
            with_connection(
                device_id,
                expect_publish_refused(other_topic, build_status_payload(dtype)),
            ),
        ),
        (
            CHECK_DISALLOWED,
            with_connection(
                device_id, expect_publish_refused(disallowed_topic, build_envelope())
            ),
        ),
        (
            CHECK_OTHER_CLIENT_ID,
            with_connection(other_client_id(device_id), check_other_client_id),
        ),
    ]


def run_ingest(
    device_id: str,
    endpoint: str,
    tls: dict[str, str],
    messages: list[tuple[str, str, dict]],
    pause_before_last_s: float = 0.0,
    pause_after_first_s: float = 0.0,
) -> bool:
    """Publish messages in order on one connection. True if all got PUBACK.

    With pause_before_last_s, waits that long before the last message (--alerts,
    --cancel); with pause_after_first_s, that long after the first (--alerts).
    """
    conn = Connection(endpoint, device_id, tls)
    try:
        if conn.connect() != "connected":
            return False
        for i, (_, message, payload) in enumerate(messages):
            if pause_after_first_s and i == 1:
                time.sleep(pause_after_first_s)
            if pause_before_last_s and i == len(messages) - 1:
                time.sleep(pause_before_last_s)
            topic = build_topic(device_id, message)
            if conn.publish(topic, encode_payload(payload)) != "puback":
                return False
        return True
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test one device against AWS IoT Core."
    )
    parser.add_argument("--device", required=True, help="device ID, e.g. wearable-0001")
    parser.add_argument(
        "--endpoint", required=True, help="the iot_endpoint Terraform output"
    )
    parser.add_argument(
        "--job",
        action="store_true",
        help="reserved for the ota-updates jobs check (not available yet)",
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="after the checks, publish ingest test messages and print the ingest-check command",
    )
    parser.add_argument(
        "--alerts",
        action="store_true",
        help="wearables only: after the checks, publish vitals alert test messages and print what to expect",
    )
    parser.add_argument(
        "--cancel",
        action="store_true",
        help="wearables only: after the checks, publish a fall and an alert_cancel 5 s later and print what to expect",
    )
    parser.add_argument(
        "--low-battery",
        action="store_true",
        help="after the checks, publish a status with battery 10 %% (not charging) and print what to expect",
    )
    parser.add_argument(
        "--possible-fall",
        action="store_true",
        help="bedroom units only: print the test-mode item, then publish bed empty and no motion 3 s later",
    )
    parser.add_argument(
        "--back-in-bed",
        action="store_true",
        help="bedroom units only: publish bed occupied (resolves an open possible fall)",
    )
    parser.add_argument("--ca-file", type=Path, default=DEFAULT_CA_FILE)
    parser.add_argument("--keys-dir", type=Path, default=DEFAULT_KEYS_DIR)
    args = parser.parse_args(argv)

    if args.job:
        print(
            "error: the jobs check comes with the ota-updates feature", file=sys.stderr
        )
        return 2
    try:
        dtype = device_type(args.device)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.alerts and dtype != "wearable":
        print("error: --alerts is for wearables only", file=sys.stderr)
        return 2
    if args.cancel and dtype != "wearable":
        print("error: --cancel is for wearables only", file=sys.stderr)
        return 2
    if args.possible_fall and dtype != "bedroom":
        print("error: --possible-fall is for bedroom units only", file=sys.stderr)
        return 2
    if args.back_in_bed and dtype != "bedroom":
        print("error: --back-in-bed is for bedroom units only", file=sys.stderr)
        return 2
    if not ENDPOINT_RE.fullmatch(args.endpoint):
        print(
            "error: --endpoint must be a host name (the iot_endpoint output)",
            file=sys.stderr,
        )
        return 2

    tls = {
        "ca": args.ca_file,
        "cert": args.keys_dir / f"{args.device}.crt",
        "key": args.keys_dir / f"{args.device}.key",
    }
    missing = [str(p) for p in tls.values() if not p.is_file()]
    if missing:
        print(
            f"error: missing {', '.join(missing)} (see README: IoT devices)",
            file=sys.stderr,
        )
        return 2

    tls_paths = {k: str(v) for k, v in tls.items()}
    results = run_checks(args.device, args.endpoint, tls_paths)
    if not (
        args.ingest
        or args.alerts
        or args.cancel
        or args.low_battery
        or args.possible_fall
        or args.back_in_bed
    ):
        return report(results)

    ingest_messages: list[tuple[str, str, dict]] = []
    if args.ingest:
        if not all(passed for _, passed in results):
            print(
                "Skipping the ingest messages: an earlier check failed.",
                file=sys.stderr,
            )
            results = [*results, (CHECK_INGEST, False)]
        else:
            ingest_messages = build_ingest_messages(dtype)
            published = run_ingest(
                args.device, args.endpoint, tls_paths, ingest_messages
            )
            results = [*results, (CHECK_INGEST, published)]
            if not published:
                ingest_messages = []

    alert_messages: list[tuple[str, str, dict]] = []
    if args.alerts:
        if not all(passed for _, passed in results):
            print(
                "Skipping the alert messages: an earlier check failed.", file=sys.stderr
            )
            results = [*results, (CHECK_ALERTS, False)]
        else:
            alert_messages = build_alert_messages()
            published = run_ingest(
                args.device,
                args.endpoint,
                tls_paths,
                alert_messages,
                pause_before_last_s=ALERTS_REPEAT_PAUSE_S,
                pause_after_first_s=ALERTS_PAIR_PAUSE_S,
            )
            results = [*results, (CHECK_ALERTS, published)]
            if not published:
                alert_messages = []

    cancel_messages: list[tuple[str, str, dict]] = []
    if args.cancel:
        if not all(passed for _, passed in results):
            print(
                "Skipping the cancel messages: an earlier check failed.",
                file=sys.stderr,
            )
            results = [*results, (CHECK_CANCEL, False)]
        else:
            cancel_messages = build_cancel_messages()
            published = run_ingest(
                args.device,
                args.endpoint,
                tls_paths,
                cancel_messages,
                pause_before_last_s=CANCEL_PAUSE_S,
            )
            results = [*results, (CHECK_CANCEL, published)]
            if not published:
                cancel_messages = []

    low_battery_messages: list[tuple[str, str, dict]] = []
    if args.low_battery:
        if not all(passed for _, passed in results):
            print(
                "Skipping the low-battery message: an earlier check failed.",
                file=sys.stderr,
            )
            results = [*results, (CHECK_LOW_BATTERY, False)]
        else:
            low_battery_messages = build_low_battery_messages(dtype)
            published = run_ingest(
                args.device, args.endpoint, tls_paths, low_battery_messages
            )
            results = [*results, (CHECK_LOW_BATTERY, published)]
            if not published:
                low_battery_messages = []

    possible_fall_messages: list[tuple[str, str, dict]] = []
    if args.possible_fall:
        if not all(passed for _, passed in results):
            print(
                "Skipping the possible-fall messages: an earlier check failed.",
                file=sys.stderr,
            )
            results = [*results, (CHECK_POSSIBLE_FALL, False)]
        else:
            # Printed before publishing, with the current UTC time (criterion 15).
            report_test_mode_item(args.device)
            possible_fall_messages = build_possible_fall_messages()
            published = run_ingest(
                args.device,
                args.endpoint,
                tls_paths,
                possible_fall_messages,
                pause_before_last_s=POSSIBLE_FALL_PAUSE_S,
            )
            results = [*results, (CHECK_POSSIBLE_FALL, published)]
            if not published:
                possible_fall_messages = []

    back_in_bed_messages: list[tuple[str, str, dict]] = []
    if args.back_in_bed:
        if not all(passed for _, passed in results):
            print(
                "Skipping the back-in-bed message: an earlier check failed.",
                file=sys.stderr,
            )
            results = [*results, (CHECK_BACK_IN_BED, False)]
        else:
            back_in_bed_messages = build_back_in_bed_messages()
            published = run_ingest(
                args.device, args.endpoint, tls_paths, back_in_bed_messages
            )
            results = [*results, (CHECK_BACK_IN_BED, published)]
            if not published:
                back_in_bed_messages = []

    code = report(results)
    if possible_fall_messages:
        report_possible_fall(args.device, possible_fall_messages)
    if back_in_bed_messages:
        report_back_in_bed(args.device, back_in_bed_messages)
    if alert_messages:
        report_alerts(args.device, alert_messages)
    if cancel_messages:
        report_cancel(args.device, cancel_messages)
    if low_battery_messages:
        report_low_battery(args.device, low_battery_messages)
    # The ingest report stays last: its last line is the plain ingest-check command.
    if ingest_messages:
        report_ingest(args.device, ingest_messages)
    return code


if __name__ == "__main__":
    sys.exit(main())
