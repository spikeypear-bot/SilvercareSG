"""Tests for `iot-smoke-test.py --possible-fall` and `--back-in-bed`
(docs/features/possible-fall.md, criteria 3a, 15, and 17).

No network: sockets are blocked (conftest) and the MQTT connection is the fake from
test_iot_smoke_cancel.py (bedroom units may publish bed, motion, status). Payloads are
checked with the ingest Lambda's own validator and against the watchdog's own parsing
of bed/motion entries and test-mode items (both pure, loaded by path).
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from datetime import UTC, datetime, timedelta

import pytest
from helpers import PRIVATE_KEY_TEXT, REPO_ROOT
from test_iot_smoke_cancel import (
    BEDROOM,
    UUID_ANY_RE,
    WEARABLE,
    FakeConnection,
    args,
    as_rule_event,
    counter_ids,
    parse_ts,
)

FIXED_NOW = datetime(2026, 11, 4, 6, 0, 0, 120_000, tzinfo=UTC)
BEDROOM_MESSAGES = frozenset({"bed", "motion", "status"})
ID_1 = "00000000-0000-4000-8000-000000000001"


def _load(name: str, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validation():
    path = REPO_ROOT / "lambdas" / "ingest" / "src" / "validation.py"
    return _load("ingest_lambda_validation", path)


@pytest.fixture(scope="module")
def watchdog_checks():
    path = REPO_ROOT / "lambdas" / "watchdog" / "src" / "checks.py"
    return _load("watchdog_checks_for_scripts", path)


@pytest.fixture
def fake_conn(smoke, monkeypatch):
    FakeConnection.instances = []
    FakeConnection.fail_topic = None
    FakeConnection.refuse_other_client = True
    FakeConnection.log = []
    monkeypatch.setattr(smoke, "Connection", FakeConnection)
    monkeypatch.setattr(
        smoke.time, "sleep", lambda s: FakeConnection.log.append(("sleep", s))
    )
    return FakeConnection


@pytest.fixture
def device_files(tmp_path):
    """Placeholder CA, certificates, and keys under tmp_path (no real PEM)."""
    keys = tmp_path / "keys"
    keys.mkdir()
    ca = tmp_path / "ca.pem"
    ca.write_text("synthetic ca placeholder\n")
    for device_id in (WEARABLE, BEDROOM):
        (keys / f"{device_id}.crt").write_text("synthetic cert placeholder\n")
        (keys / f"{device_id}.key").write_text("synthetic key placeholder\n")
    return {"ca": ca, "keys": keys}


def pf_messages(smoke):
    return smoke.build_possible_fall_messages(FIXED_NOW, counter_ids())


def bib_messages(smoke):
    return smoke.build_back_in_bed_messages(FIXED_NOW, counter_ids())


def as_latest_item(device_id: str, payload: dict) -> dict:
    """The by-message item the watchdog would read for this payload (no skew)."""
    entry = {**payload, "received_at": payload["ts"], "clock_skew": False}
    return {"pk": f"DEVICE#{device_id}", "latest": entry}


# ---- payloads ----


@pytest.mark.parametrize("occupied", [True, False])
def test_bed_payload_is_the_envelope_and_occupied(smoke, occupied):
    assert smoke.build_bed_payload(occupied, FIXED_NOW, ID_1) == {
        "schema_version": 1,
        "message_id": ID_1,
        "ts": "2026-11-04T06:00:00.120Z",
        "occupied": occupied,
    }


@pytest.mark.parametrize("motion", [True, False])
def test_motion_payload_is_the_envelope_and_motion(smoke, motion):
    assert smoke.build_motion_payload(motion, FIXED_NOW, ID_1) == {
        "schema_version": 1,
        "message_id": ID_1,
        "ts": "2026-11-04T06:00:00.120Z",
        "motion": motion,
    }


def test_possible_fall_messages_are_bed_empty_then_no_motion(smoke):
    messages = pf_messages(smoke)

    assert [(m, p.get("occupied"), p.get("motion")) for _, m, p in messages] == [
        ("bed", False, None),
        ("motion", None, False),
    ]
    assert "occupied" not in messages[1][2]
    assert "motion" not in messages[0][2]


def test_motion_ts_is_3_s_after_the_bed_ts(smoke):
    (_, _, bed), (_, _, motion) = pf_messages(smoke)

    assert parse_ts(bed["ts"]) == FIXED_NOW
    assert parse_ts(motion["ts"]) - parse_ts(bed["ts"]) == timedelta(
        seconds=smoke.POSSIBLE_FALL_PAUSE_S
    )
    assert smoke.POSSIBLE_FALL_PAUSE_S == 3.0


def test_possible_fall_message_ids_are_unique(smoke):
    ids = [p["message_id"] for _, _, p in smoke.build_possible_fall_messages(FIXED_NOW)]

    assert len(set(ids)) == 2


def test_back_in_bed_message_is_one_bed_occupied_true(smoke):
    ((expected, message, payload),) = bib_messages(smoke)

    assert (message, payload["occupied"]) == ("bed", True)
    assert payload["ts"] == "2026-11-04T06:00:00.120Z"
    assert "resolves" in expected


def test_payloads_never_contain_rule_added_fields(smoke):
    for _, _, payload in [*pf_messages(smoke), *bib_messages(smoke)]:
        assert not {"device_id", "message", "received_at", "clock_skew"} & set(payload)


def test_possible_fall_and_back_in_bed_payloads_pass_ingest_validation(
    smoke, validation
):
    for _, message, payload in [*pf_messages(smoke), *bib_messages(smoke)]:
        result = validation.validate_event(
            as_rule_event(BEDROOM, message, payload), BEDROOM_MESSAGES
        )
        assert isinstance(result, validation.ValidMessage), (message, result)


def test_watchdog_reads_the_bed_payload_as_vacated(smoke, watchdog_checks):
    (_, _, bed), _ = pf_messages(smoke)

    parsed = watchdog_checks.bedroom_entry_from_item(
        as_latest_item(BEDROOM, bed), "occupied"
    )

    assert parsed is not None
    assert (parsed[1].value, parsed[1].event_ts) == (False, bed["ts"])


def test_watchdog_reads_the_motion_payload_as_cleared(smoke, watchdog_checks):
    _, (_, _, motion) = pf_messages(smoke)

    parsed = watchdog_checks.bedroom_entry_from_item(
        as_latest_item(BEDROOM, motion), "motion"
    )

    assert parsed is not None
    assert parsed[1].value is False


def test_watchdog_resolves_on_the_back_in_bed_payload(smoke, watchdog_checks):
    (_, _, bed), _ = pf_messages(smoke)
    later = FIXED_NOW + timedelta(minutes=5)
    ((_, _, back),) = smoke.build_back_in_bed_messages(later, counter_ids())
    entry = watchdog_checks.bedroom_entry_from_item(
        as_latest_item(BEDROOM, back), "occupied"
    )[1]
    alert = watchdog_checks.OpenAlert(
        "6f1c2e0a-5b3d-5e8f-9a47-0000000000a1",
        "possible_fall",
        BEDROOM,
        "res-0001",
        bed["ts"],
    )

    assert watchdog_checks.back_in_bed(alert, entry) is True


# ---- the test-mode item ----


def test_test_mode_item_is_dynamodb_json_with_the_given_time(smoke):
    assert smoke.build_test_mode_item("bedroom-0003", FIXED_NOW) == {
        "pk": {"S": "DEVICE#bedroom-0003"},
        "sk": {"S": "TEST_MODE#2026-11-04T06:00:00.120Z"},
        "entity_type": {"S": "TEST_MODE"},
    }


def test_test_mode_item_defaults_to_the_current_utc_time(smoke):
    before = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=1)

    item = smoke.build_test_mode_item(BEDROOM)

    after = datetime.now(UTC) + timedelta(seconds=1)
    set_at = parse_ts(item["sk"]["S"].removeprefix("TEST_MODE#"))
    assert before <= set_at <= after


@pytest.mark.parametrize("device_id", [WEARABLE, "bedroom-1", "phone-0001"])
def test_test_mode_item_refuses_a_non_bedroom_device(smoke, device_id):
    with pytest.raises(ValueError):
        smoke.build_test_mode_item(device_id, FIXED_NOW)


def test_watchdog_accepts_the_test_mode_item(smoke, watchdog_checks):
    item = smoke.build_test_mode_item(BEDROOM, FIXED_NOW)
    plain = {name: value["S"] for name, value in item.items()}

    assert watchdog_checks.unit_in_test_mode(plain, FIXED_NOW) == BEDROOM
    later = FIXED_NOW + timedelta(hours=2)
    assert watchdog_checks.unit_in_test_mode(plain, later) == BEDROOM
    expired = later + timedelta(milliseconds=1)
    assert watchdog_checks.unit_in_test_mode(plain, expired) is None


def item_report(smoke, now=FIXED_NOW) -> str:
    out = io.StringIO()
    smoke.report_test_mode_item("bedroom-0003", now, out)
    return out.getvalue()


def printed_item(text: str) -> dict:
    (line,) = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("{")]
    return json.loads(line)


def test_report_prints_the_exact_test_mode_item(smoke):
    text = item_report(smoke)

    assert (
        '  {"pk": {"S": "DEVICE#bedroom-0003"}, "sk": {"S":'
        ' "TEST_MODE#2026-11-04T06:00:00.120Z"}, "entity_type": {"S": "TEST_MODE"}}'
    ) in text.splitlines()


def test_report_says_where_to_create_the_item(smoke):
    text = item_report(smoke)

    assert "silvercare-registry -> Create item -> JSON view" in text


def test_report_says_the_item_expires_after_2_hours_and_only_this_unit(smoke):
    text = item_report(smoke)

    assert "only this unit" in text
    assert "expires by itself 2 hours" in text
    assert "the wait is 2 minutes" in text
    assert "20 minutes" in text


def test_report_reminds_to_use_a_resident_without_a_sending_wearable(smoke):
    text = item_report(smoke)

    assert "whose wearable the simulator isn't sending for" in text
    assert "no status for 15 minutes counts as not worn" in text


def test_possible_fall_report_prints_ids_and_expectations(smoke):
    messages = pf_messages(smoke)
    out = io.StringIO()

    smoke.report_possible_fall(BEDROOM, messages, out)

    text = out.getvalue()
    for _, _, payload in messages:
        assert payload["message_id"] in text
    assert "'SilverCare CRITICAL alert: Possible fall - <resident>'" in text
    assert "'Test mode: yes'" in text
    assert "caregivers and to the nurse subscribers at once" in text
    assert "about 2-7 minutes later" in text
    assert "--back-in-bed" in text


def test_back_in_bed_report_prints_id_and_expectations(smoke):
    messages = bib_messages(smoke)
    out = io.StringIO()

    smoke.report_back_in_bed(BEDROOM, messages, out)

    text = out.getvalue()
    assert messages[0][2]["message_id"] in text
    assert "'SilverCare update: Possible fall - <resident> resolved'" in text
    assert "within 5 minutes" in text
    assert "resolved_by_role 'system'" in text
    assert "delete the test-mode item" in text


@pytest.mark.parametrize("which", ["possible_fall", "back_in_bed"])
def test_reports_print_no_payload_values(smoke, which):
    messages = pf_messages(smoke) if which == "possible_fall" else bib_messages(smoke)
    out = io.StringIO()

    getattr(smoke, f"report_{which}")(BEDROOM, messages, out)

    text = UUID_ANY_RE.sub("<id>", out.getvalue())
    for value in ("occupied", "schema_version", "{", "2026-11-04", "false", "true"):
        assert value not in text, value


# ---- main with a fake connection ----


def published(fake_conn) -> list[tuple[str, dict]]:
    conn = fake_conn.instances[-1]
    return [(topic, json.loads(raw)) for topic, raw in conn.published]


def test_main_possible_fall_publishes_bed_pause_then_motion(
    smoke, fake_conn, device_files, capsys
):
    code = smoke.main(args(device_files, BEDROOM, "--possible-fall"))

    out = capsys.readouterr().out
    assert code == 0
    assert f"PASS  {smoke.CHECK_POSSIBLE_FALL}" in out
    assert fake_conn.instances[-1].client_id == BEDROOM
    sent = published(fake_conn)
    assert [(t, p.get("occupied"), p.get("motion")) for t, p in sent] == [
        (f"silvercare/v1/{BEDROOM}/bed", False, None),
        (f"silvercare/v1/{BEDROOM}/motion", None, False),
    ]
    assert fake_conn.log[-3:] == [
        ("publish", f"silvercare/v1/{BEDROOM}/bed"),
        ("sleep", smoke.POSSIBLE_FALL_PAUSE_S),
        ("publish", f"silvercare/v1/{BEDROOM}/motion"),
    ]
    for _, payload in sent:
        assert payload["message_id"] in out
    assert "Possible-fall messages sent" in out


def test_main_possible_fall_prints_the_item_with_the_current_utc_time(
    smoke, fake_conn, device_files, capsys
):
    before = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=1)

    smoke.main(args(device_files, BEDROOM, "--possible-fall"))

    after = datetime.now(UTC) + timedelta(seconds=1)
    item = printed_item(capsys.readouterr().out)
    assert item["pk"] == {"S": f"DEVICE#{BEDROOM}"}
    assert item["entity_type"] == {"S": "TEST_MODE"}
    set_at = parse_ts(item["sk"]["S"].removeprefix("TEST_MODE#"))
    assert before <= set_at <= after


def test_main_possible_fall_prints_the_item_before_publishing(
    smoke, fake_conn, device_files, monkeypatch
):
    original = smoke.report_test_mode_item

    def recording(*a, **kw):
        fake_conn.log.append(("print item",))
        return original(*a, **kw)

    monkeypatch.setattr(smoke, "report_test_mode_item", recording)

    smoke.main(args(device_files, BEDROOM, "--possible-fall"))

    log = fake_conn.log
    assert log.index(("print item",)) < log.index(
        ("publish", f"silvercare/v1/{BEDROOM}/bed")
    )


def test_main_back_in_bed_publishes_one_bed_occupied(
    smoke, fake_conn, device_files, capsys
):
    code = smoke.main(args(device_files, BEDROOM, "--back-in-bed"))

    out = capsys.readouterr().out
    assert code == 0
    assert f"PASS  {smoke.CHECK_BACK_IN_BED}" in out
    ((topic, payload),) = published(fake_conn)
    assert topic == f"silvercare/v1/{BEDROOM}/bed"
    assert payload["occupied"] is True
    assert payload["message_id"] in out
    assert "Back-in-bed message sent" in out
    assert "TEST_MODE#" in out  # only in the "delete the item" reminder
    assert '"entity_type"' not in out


def test_main_payloads_on_the_wire_pass_ingest_validation(
    smoke, fake_conn, device_files, validation
):
    smoke.main(args(device_files, BEDROOM, "--possible-fall"))
    sent = published(fake_conn)
    smoke.main(args(device_files, BEDROOM, "--back-in-bed"))
    sent += published(fake_conn)

    for topic, payload in sent:
        message = topic.rsplit("/", 1)[1]
        result = validation.validate_event(
            as_rule_event(BEDROOM, message, payload), BEDROOM_MESSAGES
        )
        assert isinstance(result, validation.ValidMessage), (message, result)


@pytest.mark.parametrize(
    ("flag", "error"),
    [
        ("--possible-fall", "--possible-fall is for bedroom units only"),
        ("--back-in-bed", "--back-in-bed is for bedroom units only"),
    ],
)
def test_main_refuses_a_wearable_with_exit_2(
    smoke, fake_conn, device_files, capsys, flag, error
):
    code = smoke.main(args(device_files, WEARABLE, flag))

    captured = capsys.readouterr()
    assert code == 2
    assert error in captured.err
    assert fake_conn.instances == []
    assert "TEST_MODE" not in captured.out


@pytest.mark.parametrize(
    ("flag", "check", "skipped"),
    [
        ("--possible-fall", "CHECK_POSSIBLE_FALL", "Skipping the possible-fall"),
        ("--back-in-bed", "CHECK_BACK_IN_BED", "Skipping the back-in-bed"),
    ],
)
def test_main_skips_when_an_earlier_check_fails(
    smoke, fake_conn, device_files, capsys, flag, check, skipped
):
    fake_conn.refuse_other_client = False

    code = smoke.main(args(device_files, BEDROOM, flag))

    captured = capsys.readouterr()
    assert code != 0
    assert f"FAIL  {getattr(smoke, check)}" in captured.out
    assert skipped in captured.err
    assert len(fake_conn.instances) == 5
    assert '"entity_type"' not in captured.out


def test_main_possible_fall_without_puback_fails_and_prints_no_expectations(
    smoke, fake_conn, device_files, capsys
):
    fake_conn.fail_topic = f"silvercare/v1/{BEDROOM}/motion"

    code = smoke.main(args(device_files, BEDROOM, "--possible-fall"))

    out = capsys.readouterr().out
    assert code != 0
    assert f"FAIL  {smoke.CHECK_POSSIBLE_FALL}" in out
    assert "Possible-fall messages sent" not in out


def test_main_back_in_bed_without_puback_fails_and_prints_no_expectations(
    smoke, fake_conn, device_files, capsys
):
    fake_conn.fail_topic = f"silvercare/v1/{BEDROOM}/bed"

    code = smoke.main(args(device_files, BEDROOM, "--back-in-bed"))

    out = capsys.readouterr().out
    assert code != 0
    assert f"FAIL  {smoke.CHECK_BACK_IN_BED}" in out
    assert "Back-in-bed message sent" not in out


def test_main_without_the_flags_sends_no_possible_fall_messages(
    smoke, fake_conn, device_files, capsys
):
    smoke.main(args(device_files, BEDROOM))

    out = capsys.readouterr().out
    assert smoke.CHECK_POSSIBLE_FALL not in out
    assert smoke.CHECK_BACK_IN_BED not in out
    assert "TEST_MODE" not in out
    assert len(fake_conn.instances) == 5


def test_main_output_has_no_payload_values_or_keys(
    smoke, fake_conn, device_files, capsys
):
    smoke.main(args(device_files, BEDROOM, "--possible-fall"))
    smoke.main(args(device_files, BEDROOM, "--back-in-bed"))

    captured = capsys.readouterr()
    text = UUID_ANY_RE.sub("<id>", captured.out + captured.err)
    for value in ("occupied", "schema_version", "fw_version", PRIVATE_KEY_TEXT):
        assert value not in text, value
