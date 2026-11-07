"""Pure rules for the acknowledge, cancel, and resolve routes (docs/data-model.md, Status
updates; Track B route: resolve): no AWS calls.

`alert-processor` trusts every status_update and fails silently on one it can't apply
(it logs and drops it, or it ends in the DLQ), so the API checks everything first and
answers 403 / 404 / 409. The checks match `lambdas/alert-processor/src/message.py`;
keep the two in step.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any

SCHEMA_VERSION = 1
UNASSIGNED = "unassigned"

RESIDENT_ID_RE = re.compile(r"^(res-[0-9]{4}|unassigned)\Z")
# As alert-processor: alert IDs are UUID v5 (variant 8, 9, a, or b).
ALERT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")
SUB_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")

MAX_BODY_BYTES = 4096
NOTE_MAX_LEN = 500
BY_NAME_MAX_LEN = 100

HEALTH_ALERT_TYPES = frozenset(
    {
        "fall",
        "hr_high",
        "hr_low",
        "spo2_low",
        "skin_temp_high",
        "skin_temp_low",
        "possible_fall",
    }
)
DEVICE_PROBLEM_TYPES = frozenset({"device_offline", "battery_low", "not_worn"})

ACKNOWLEDGE = "acknowledge"
CANCEL = "cancel"
RESOLVE = "resolve"
REQUESTED_STATUS = {
    ACKNOWLEDGE: "acknowledged",
    CANCEL: "cancelled",
    RESOLVE: "resolved",
}
# The statuses each action may start from (pattern 25's conditions).
FROM_STATUSES = {
    ACKNOWLEDGE: frozenset({"open"}),
    CANCEL: frozenset({"open", "acknowledged"}),
    RESOLVE: frozenset({"open", "acknowledged"}),
}
BODY_KEYS = {
    ACKNOWLEDGE: frozenset({"event_ts", "note"}),
    CANCEL: frozenset({"event_ts"}),
    RESOLVE: frozenset({"event_ts", "note"}),
}
# A staff resolve's closing note is required (staff-resolve criterion 1).
NOTE_REQUIRED = frozenset({RESOLVE})


class Refused(Exception):
    """An answer other than 202: status, error code, message (never echoes input)."""

    def __init__(self, status: int, code: str, message: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.code = code
        self.message = message
        # For the log only: a fixed word, never a value from the request.
        self.reason = reason


def bad_request(reason: str) -> Refused:
    return Refused(400, "bad_request", "The request is not valid.", reason)


def not_found(reason: str) -> Refused:
    return Refused(404, "not_found", "No such alert.", reason)


def conflict(reason: str, message: str) -> Refused:
    return Refused(409, "conflict", message, reason)


# ---- request ----


@dataclass(frozen=True)
class Request:
    action: str
    resident_id: str
    alert_id: str
    event_ts: str
    # None: no note. A nurse's acknowledge may carry one; a resolve always does.
    note: str | None


def valid_note(value: str) -> bool:
    """alert-processor's `_valid_note`: 1-500 characters, not only whitespace, no
    Unicode category C (controls, format characters such as bidi overrides and
    zero-width joiners) and no U+2028/U+2029, except newline."""
    if not 1 <= len(value) <= NOTE_MAX_LEN or not value.strip():
        return False
    return not any(
        ch != "\n"
        and (
            unicodedata.category(ch).startswith("C")
            or unicodedata.category(ch) in ("Zl", "Zp")
        )
        for ch in value
    )


def valid_by_name(value: Any) -> bool:
    """alert-processor's `_valid_by_name` (a caregiver's `name` claim)."""
    if type(value) is not str or not 1 <= len(value) <= BY_NAME_MAX_LEN:
        return False
    if not value.strip():
        return False
    return not any(unicodedata.category(ch).startswith("C") for ch in value)


def parse_request(action: str, path: Any, body_text: Any) -> Request:
    """The validated path and body, or Refused(400). Only the documented keys are
    accepted."""
    if not isinstance(path, dict):
        raise bad_request("path")
    resident_id, alert_id = path.get("resident_id"), path.get("alert_id")
    if not isinstance(resident_id, str) or not RESIDENT_ID_RE.match(resident_id):
        raise bad_request("resident_id")
    if not isinstance(alert_id, str) or not ALERT_ID_RE.match(alert_id):
        raise bad_request("alert_id")

    if (
        not isinstance(body_text, str)
        or len(body_text.encode("utf-8")) > MAX_BODY_BYTES
    ):
        raise bad_request("body")
    try:
        body = json.loads(body_text)
    except ValueError:
        raise bad_request("body_json") from None
    if not isinstance(body, dict) or set(body) - BODY_KEYS[action]:
        raise bad_request("body_keys")

    event_ts = body.get("event_ts")
    if type(event_ts) is not str or not TS_RE.match(event_ts):
        raise bad_request("event_ts")
    try:
        datetime.strptime(event_ts, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        raise bad_request("event_ts") from None

    note = body.get("note")
    if note is None:
        if action in NOTE_REQUIRED:
            raise bad_request("note_required")
    else:
        if type(note) is not str:
            raise bad_request("note")
        # As the portal's checkAckNote: CRLF to LF, then trim.
        note = note.replace("\r\n", "\n").strip()
        if not valid_note(note):
            raise bad_request("note")
    return Request(action, resident_id, alert_id, event_ts, note)


# ---- decision ----


def acknowledge_role(groups: frozenset[str], alert_type: str, linked: Any) -> str:
    """by_role for an acknowledge, or Refused. `linked()` runs the pattern 13 check
    (only called when the caregiver rule needs it).

    - health alert: `nurse`. A linked caregiver gets 409 (they cancel instead); ops and
      unlinked caregivers get 404, so they can't learn that a health alert exists.
    - device problem: `ops` (any resident), else a linked `caregiver`; a nurse gets 409
      (nurses don't acknowledge device problems, user's decisions 2026-11-05).
    """
    if alert_type in HEALTH_ALERT_TYPES:
        if "nurse" in groups:
            return "nurse"
        if "caregiver" in groups and linked():
            raise conflict(
                "type", "Only the care centre nurse acknowledges this alert."
            )
        raise not_found("hidden_health_alert")
    if alert_type in DEVICE_PROBLEM_TYPES:
        if "ops" in groups:
            return "ops"
        if "caregiver" in groups:
            if linked():
                return "caregiver"
            raise not_found("link")
        raise conflict(
            "type", "Device problems are acknowledged by ops or a caregiver."
        )
    raise not_found("alert_type")


def check_cancel(alert_type: str) -> None:
    """A caregiver cancels health alerts only (device problems have no cancel)."""
    if alert_type in DEVICE_PROBLEM_TYPES:
        raise conflict("type", "Device problems can't be cancelled.")
    if alert_type not in HEALTH_ALERT_TYPES:
        raise not_found("alert_type")


def resolve_role(groups: frozenset[str]) -> str:
    """by_role for a staff resolve: `nurse` if the caller is in `nurse` (also when in
    both), else `doctor`. The caller's groups were already narrowed to these two."""
    return "nurse" if "nurse" in groups else "doctor"


def check_resolve(alert_type: str) -> None:
    """Staff resolve health alerts (possible falls included) only: the watchdog resolves
    device problems when they clear (pattern 16)."""
    if alert_type in DEVICE_PROBLEM_TYPES:
        raise conflict(
            "type", "Device problems are resolved automatically when they clear."
        )
    if alert_type not in HEALTH_ALERT_TYPES:
        raise not_found("alert_type")


def note_allowed(action: str, by_role: str) -> bool:
    """Who may send a note (message.py's `_check_note`): a nurse's acknowledge
    (optional) and a nurse's or doctor's resolve (required, checked in parse_request)."""
    if action == RESOLVE:
        return by_role in ("nurse", "doctor")
    return action == ACKNOWLEDGE and by_role == "nurse"


def check_status(action: str, status: Any) -> None:
    if not isinstance(status, str) or status not in FROM_STATUSES[action]:
        raise conflict("status", "This alert has already been dealt with.")


def status_update(
    *,
    resident_id: str,
    alert_id: str,
    event_ts: str,
    action: str,
    by_role: str,
    by: str,
    by_name: str | None,
    note: str | None,
    changed_at: str,
) -> dict[str, Any]:
    """The queue message, exactly as docs/data-model.md's table: `note` on a nurse's
    acknowledge with a note and on every staff resolve; `by_name` only for a
    caregiver."""
    body: dict[str, Any] = {
        "kind": "status_update",
        "schema_version": SCHEMA_VERSION,
        "resident_id": resident_id,
        "new_status": REQUESTED_STATUS[action],
        "changed_at": changed_at,
        "by_role": by_role,
        "by": by,
        "by_name": by_name if by_role == "caregiver" else None,
        "target": {"alert_id": alert_id, "event_ts": event_ts},
    }
    if note is not None and note_allowed(action, by_role):
        body["note"] = note
    return body
