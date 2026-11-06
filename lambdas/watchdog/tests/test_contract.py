"""The watchdog's messages pass alert-processor's own validator
(lambdas/alert-processor/src/message.py, pure, loaded by path under another name so
it never clashes with this function's modules). docs/features/watchdog.md, criteria
8, 8a, 10, 12."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import checks
import pytest
from checks import BATTERY_LOW, DEVICE_OFFLINE, NOT_WORN
from events import NOW, ago, device, open_alert, status

MESSAGE_PY = (
    Path(__file__).resolve().parents[2] / "alert-processor" / "src" / "message.py"
)


@pytest.fixture(scope="module")
def ap_message():
    name = "alert_processor_message_for_watchdog"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, MESSAGE_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def problem_of(alert_type: str) -> checks.Problem:
    st = {
        DEVICE_OFFLINE: status(ts=ago(minutes=20)),
        BATTERY_LOW: status(battery_pct=15),
        NOT_WORN: status(worn=False),
    }[alert_type]
    (problem,) = checks.problems_for(device(), st, ago(hours=3), NOW)
    assert problem.alert_type == alert_type
    return problem


@pytest.mark.parametrize("alert_type", [DEVICE_OFFLINE, BATTERY_LOW, NOT_WORN])
def test_alert_message_is_accepted_by_alert_processor(ap_message, alert_type):
    body = checks.encode(checks.alert_message(problem_of(alert_type), NOW))

    result = ap_message.parse_message(body)

    assert isinstance(result, ap_message.Alert), result
    assert result.alert_type == alert_type


def test_grouped_offline_message_is_accepted_by_alert_processor(ap_message):
    problem = problem_of(DEVICE_OFFLINE)
    problem.details["grouped"] = True

    result = ap_message.parse_message(checks.encode(checks.alert_message(problem, NOW)))

    assert isinstance(result, ap_message.Alert), result
    assert result.details["grouped"] is True


def test_resolve_message_is_accepted_by_alert_processor(ap_message):
    body = checks.encode(checks.resolve_message(open_alert(BATTERY_LOW), NOW))

    result = ap_message.parse_message(body)

    assert isinstance(result, ap_message.StatusUpdate), result
    assert (result.new_status, result.by_role) == ("resolved", "system")


# ---- possible fall (docs/features/possible-fall.md, criteria 4, 7, 9) ----


def possible_fall_problem(details_extra: dict | None = None) -> checks.Problem:
    vacated = ago(minutes=25)
    details = {"out_of_bed_since": vacated, "wearable": "no_recent_status"}
    details.update(details_extra or {})
    return checks.Problem(
        checks.POSSIBLE_FALL,
        device("bedroom-0001"),
        vacated,
        details,
        None,
        checks.SEVERITY_CRITICAL,
    )


@pytest.mark.parametrize("extra", [{}, {"test_mode": True}])
def test_possible_fall_message_is_accepted_by_alert_processor(ap_message, extra):
    body = checks.encode(checks.alert_message(possible_fall_problem(extra), NOW))

    result = ap_message.parse_message(body)

    assert isinstance(result, ap_message.Alert), result
    assert (result.alert_type, result.severity) == ("possible_fall", "critical")


@pytest.mark.parametrize("wearable", ["not_worn", "no_recent_status", "no_wearable"])
def test_each_wearable_state_is_accepted_by_alert_processor(ap_message, wearable):
    problem = possible_fall_problem({"wearable": wearable})

    result = ap_message.parse_message(checks.encode(checks.alert_message(problem, NOW)))

    assert isinstance(result, ap_message.Alert), result


def test_back_in_bed_resolve_is_accepted_by_alert_processor(ap_message):
    alert = open_alert(checks.POSSIBLE_FALL, "bedroom-0001")

    result = ap_message.parse_message(checks.encode(checks.resolve_message(alert, NOW)))

    assert isinstance(result, ap_message.StatusUpdate), result
    assert result.reason == "back_in_bed"
