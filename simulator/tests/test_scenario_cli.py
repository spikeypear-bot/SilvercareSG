"""Alert scenarios through main.main (docs/features/simulator-alert-scenarios.md,
criteria 1 to 4, 12, 13, 15, 16): --scenario flags, typed commands from a fake
command source, the scenario lines, and the dry-run output. Dry-runs only; no
network, no keys, no real stdin."""

from __future__ import annotations

import io
import re
import sys
import threading
import time
import uuid
from collections import Counter

import clock as clock_mod
import commands as commands_mod
import contract
import fakes
import main
import pytest
import scenarios
from clock import InstantClock
from conftest import START_EPOCH_MS, message_of, run_dry, ts_to_epoch_ms
from scenario_helpers import id_counter

BASE = ["--dry-run", "--random-seed", "11", "--start-at", "12:00"]
INSTANT = [*BASE, "--instant"]
W = "wearable-0005"
OTHER = "wearable-0006"

HEADER_RE = re.compile(
    r"^simulator dry-run: \d+ devices, random seed \d+, routine \d\d:\d\d$"
)
SUMMARY_RE = re.compile(
    r"^(?P<sgt>\d\d:\d\d) SGT routine \d\d:\d\d \| connected \d+/\d+"
    r" \| sent vitals (?P<vitals>\d+) status (?P<status>\d+) bed (?P<bed>\d+)"
    r" motion (?P<motion>\d+) fall (?P<fall>\d+) vitals_alert (?P<vitals_alert>\d+)"
    r" alert_cancel (?P<alert_cancel>\d+) \| dropped 0 \| failed 0$"
)
TOTAL_RE = re.compile(r"^total \d+:\d\d:\d\d \| connected \d+/\d+ \| sent .*$")
SCENARIO_RE = re.compile(
    r"^scenario (fall|hr-spike|no-pulse|spo2-drop|im-ok) wearable-\d{4}"
    r" (started|started \(after the fall message\)|ended|cut off: run ended"
    r"|skipped: (not worn|busy|too soon))$"
)
KINDS = ("vitals", "status", "bed", "motion", "fall", "vitals_alert", "alert_cancel")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def scenario_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.startswith("scenario ")]


def strip_ids(lines: list[dict]) -> list[dict]:
    return [
        {
            "topic": line["topic"],
            "payload": {k: v for k, v in line["payload"].items() if k != "message_id"},
        }
        for line in lines
    ]


def device_of(line: dict) -> str:
    return line["topic"].split("/")[2]


@pytest.fixture
def no_files(monkeypatch):
    """Fail the test if main gets as far as reading the seed/devices files."""

    def fail(*args, **kwargs):
        raise AssertionError("must not get this far")

    monkeypatch.setattr(main, "load_json", fail)
    monkeypatch.setattr(main, "missing_key_files", fail)
    monkeypatch.setattr(main, "paho_client_factory", fail)


# ---- flag errors (criteria 1, 2) ----


# Values never contain the error's own example (fall:wearable-0005@2m), so "not in
# the output" means not echoed.
@pytest.mark.parametrize(
    "value",
    [
        "spo2-drop",
        "spo2-drop:",
        ":wearable-0007",
        "fall:wearable-17",
        "fall:zzqx-0007",
        "fall wearable-0007",
        "fall:wearable-0007@",
        "fall:wearable-0007@2d",
        "fall:wearable-0007@1.5m",
        "fall:wearable-0007@-5s",
        "fall:wearable-0007@0s",
        "fall:wearable-0007:2m",
        "fall:wearable-0007@2m@3m",
        "zzqx:wearable-0007",
        "FALL:wearable-0007",
        "fall:wearable-0007@5m",  # at --duration
        "fall:wearable-0007@6m",  # past --duration
        "fall:wearable-0007@301s",
    ],
)
def test_bad_scenario_flag_exits_2_naming_its_position_not_its_value(
    value, no_files, capsys
):
    argv = [*INSTANT, "--duration", "5m", "--scenario", "im-ok:wearable-0001"]
    with pytest.raises(SystemExit) as exc:
        main.main(
            [*argv, "--scenario", value], stdout=io.StringIO(), stderr=io.StringIO()
        )
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--scenario 2: " in captured.err
    assert value not in captured.err
    assert "zzqx" not in captured.err


def test_offset_just_before_duration_is_accepted():
    result = run_dry([*INSTANT, "--duration", "5m", "--scenario", f"im-ok:{W}@299s"])
    assert result.code == 0
    assert scenario_lines(result.stderr) == [
        f"scenario im-ok {W} started",
        f"scenario im-ok {W} ended",
    ]


def test_any_offset_is_accepted_without_duration(no_files):
    args = main.parse_args([*BASE, "--scenario", f"fall:{W}@24h"])
    assert args.scenario_requests == [scenarios.Request("fall", W, 24 * 3_600_000)]


def test_offset_defaults_to_30_s():
    args = main.parse_args([*BASE, "--scenario", f"fall:{W}"])
    assert args.scenario_requests == [scenarios.Request("fall", W, 30_000)]


@pytest.mark.parametrize(
    ("value", "devices", "problem"),
    [
        ("fall:bedroom-0005", None, "bedroom-0005 is not a wearable"),
        ("hr-spike:wearable-0099", None, "wearable-0099 is not in the devices file"),
        ("im-ok:wearable-0002", "wearable-0001", "wearable-0002 is not in this run"),
        ("fall:wearable-0001", "bedroom-0001", "wearable-0001 is not in this run"),
    ],
)
def test_bad_scenario_target_exits_2_before_anything_runs(value, devices, problem):
    argv = [*INSTANT, "--duration", "5m", "--scenario", f"im-ok:{W}"]
    if devices:
        argv = [*INSTANT, "--duration", "5m", "--devices", devices]
    result = run_dry([*argv, "--scenario", value])
    position = 1 if devices else 2
    assert result.code == 2
    assert result.stdout == ""
    assert result.stderr.startswith(f"error: --scenario {position}: {problem}")
    assert len(result.stderr.splitlines()) == 1
    assert value not in result.stderr


# ---- scenario lines and summaries (criteria 4, 13) ----


@pytest.fixture(scope="module")
def flagged():
    return run_dry(
        [
            *INSTANT,
            "--duration",
            "5m",
            "--scenario",
            f"hr-spike:{W}@30s",
            "--scenario",
            f"fall:{OTHER}@30s",
            "--scenario",
            f"im-ok:{OTHER}@70s",
            "--scenario",
            f"spo2-drop:{W}@90s",
            "--scenario",
            f"im-ok:{OTHER}@72s",
        ],
        new_id=id_counter(),
    )


def test_scenario_lines_wording_and_order(flagged):
    assert flagged.code == 0
    assert scenario_lines(flagged.stderr) == [
        f"scenario hr-spike {W} started",
        f"scenario fall {OTHER} started",
        f"scenario im-ok {OTHER} started",
        f"scenario fall {OTHER} ended",
        f"scenario im-ok {OTHER} ended",
        f"scenario im-ok {OTHER} skipped: too soon",
        f"scenario spo2-drop {W} skipped: busy",
        f"scenario hr-spike {W} ended",
    ]


def test_scenario_lines_come_between_the_right_minute_summaries(flagged):
    lines = flagged.stderr.splitlines()
    where = {line: i for i, line in enumerate(lines)}
    summary_at = {
        m["sgt"]: i for i, line in enumerate(lines) if (m := SUMMARY_RE.fullmatch(line))
    }
    assert where[f"scenario fall {OTHER} started"] < summary_at["12:01"]
    assert summary_at["12:01"] < where[f"scenario im-ok {OTHER} started"]
    assert where[f"scenario spo2-drop {W} skipped: busy"] < summary_at["12:02"]
    assert summary_at["12:03"] < where[f"scenario hr-spike {W} ended"]
    assert where[f"scenario hr-spike {W} ended"] < summary_at["12:04"]


def test_summaries_count_the_new_message_types(flagged):
    lines = flagged.stderr.splitlines()
    printed = Counter(message_of(line) for line in flagged.lines)
    assert printed["fall"] == printed["vitals_alert"] == printed["alert_cancel"] == 1
    summed = Counter()
    for line in lines[1:-1]:
        match = SUMMARY_RE.fullmatch(line)
        if match:
            for kind in KINDS:
                summed[kind] += int(match[kind])
    assert +summed == printed
    first = SUMMARY_RE.fullmatch(next(x for x in lines if " SGT " in x))
    assert (first["fall"], first["vitals_alert"], first["alert_cancel"]) == (
        "1",
        "1",
        "0",
    )
    assert lines[-1].endswith(
        f" motion {printed['motion']} fall 1 vitals_alert 1 alert_cancel 1"
        " | dropped 0 | failed 0"
    )


def test_output_names_scenarios_devices_and_reasons_only(flagged):
    for line in flagged.stderr.splitlines():
        assert (
            HEADER_RE.fullmatch(line)
            or SUMMARY_RE.fullmatch(line)
            or SCENARIO_RE.fullmatch(line)
            or TOTAL_RE.fullmatch(line)
        ), line
    assert "{" not in flagged.stderr


def test_dry_run_prints_scenario_messages_in_time_order(flagged):
    lines = flagged.lines
    falls = [i for i, line in enumerate(lines) if message_of(line) == "fall"]
    assert len(falls) == 1
    fall_index = falls[0]
    others = [
        flagged.elapsed_ms(line) for i, line in enumerate(lines) if i != fall_index
    ]
    assert others == sorted(others)
    impact = flagged.elapsed_ms(lines[fall_index])
    assert impact == 30_000
    sent_at = impact + 20_000
    assert flagged.elapsed_ms(lines[fall_index - 1]) <= sent_at
    assert flagged.elapsed_ms(lines[fall_index + 1]) >= sent_at


def test_every_line_of_a_scenario_run_follows_the_contract(flagged):
    for line in flagged.lines:
        assert contract.problems(line["topic"], line["payload"]) == [], line["topic"]


def test_every_scenario_together_follows_the_contract():
    flags = []
    for n, name in enumerate(scenarios.NAMES, start=1):
        flags += ["--scenario", f"{name}:wearable-{n:04d}@{20 + n}s"]
    flags += [
        "--scenario",
        "fall:wearable-0010@30s",
        "--scenario",
        "im-ok:wearable-0010@35s",
    ]
    result = run_dry([*INSTANT, "--duration", "15m", *flags])
    assert result.code == 0
    kinds = Counter(message_of(line) for line in result.lines)
    assert (
        kinds["fall"] == 2 and kinds["vitals_alert"] == 3 and kinds["alert_cancel"] == 2
    )
    for line in result.lines:
        assert contract.problems(line["topic"], line["payload"]) == [], line["topic"]


# ---- determinism and runs without scenarios (criteria 12, 16) ----


def test_same_seed_start_and_scenarios_give_identical_output():
    argv = [*INSTANT, "--duration", "12m", "--scenario", f"fall:{W}@40s"]
    argv += ["--scenario", f"no-pulse:{OTHER}"]
    one = run_dry(argv, new_id=id_counter())
    two = run_dry(argv, new_id=id_counter())
    assert one.stdout == two.stdout and one.stderr == two.stderr


def test_a_run_without_scenarios_prints_no_scenario_lines_or_messages():
    result = run_dry([*INSTANT, "--duration", "10m"])
    assert scenario_lines(result.stderr) == []
    assert {message_of(line) for line in result.lines} <= {
        "vitals",
        "status",
        "bed",
        "motion",
    }
    assert result.stderr.splitlines()[-1].endswith(
        " fall 0 vitals_alert 0 alert_cancel 0 | dropped 0 | failed 0"
    )


def test_a_scenario_does_not_change_the_other_devices_output():
    argv = [*INSTANT, "--duration", "15m"]
    plain = run_dry(argv)
    with_scenarios = run_dry(
        [*argv, "--scenario", f"fall:{W}", "--scenario", f"im-ok:{W}@3m"]
    )
    assert strip_ids(
        [x for x in with_scenarios.lines if device_of(x) != W]
    ) == strip_ids([x for x in plain.lines if device_of(x) != W])


def test_message_ids_are_still_random_uuids_by_default():
    result = run_dry([*INSTANT, "--duration", "2m", "--scenario", f"im-ok:{W}"])
    (cancel,) = [x for x in result.lines if message_of(x) == "alert_cancel"]
    assert uuid.UUID(cancel["payload"]["message_id"]).version == 4


# ---- cut off when the run stops (criterion 4) ----


def test_deferred_im_ok_and_its_fall_are_cut_off_at_duration():
    # The fall message goes out at 115 s, so the im-ok's cancel is due at 120 s:
    # the end of the run.
    result = run_dry(
        [*INSTANT, "--duration", "2m"]
        + ["--scenario", f"fall:{W}@95s", "--scenario", f"im-ok:{W}@100s"]
    )
    assert result.code == 0
    assert scenario_lines(result.stderr) == [
        f"scenario fall {W} started",
        f"scenario im-ok {W} started (after the fall message)",
        f"scenario im-ok {W} cut off: run ended",
        f"scenario fall {W} cut off: run ended",
    ]
    kinds = Counter(message_of(x) for x in result.lines)
    assert kinds["fall"] == 1 and kinds["alert_cancel"] == 0
    assert " fall 1 vitals_alert 0 alert_cancel 0 |" in result.stderr.splitlines()[-1]


def test_cut_off_lines_come_after_the_last_summary_and_before_the_total():
    result = run_dry([*INSTANT, "--duration", "2m", "--scenario", f"hr-spike:{W}@1m"])
    lines = result.stderr.splitlines()
    assert lines[-2] == f"scenario hr-spike {W} cut off: run ended"
    assert SUMMARY_RE.fullmatch(lines[-3]) and lines[-1].startswith("total 0:02:00")
    assert scenario_lines(result.stderr) == [
        f"scenario hr-spike {W} started",
        f"scenario hr-spike {W} cut off: run ended",
    ]


@pytest.mark.parametrize("name", ["hr-spike", "spo2-drop", "no-pulse"])
def test_active_vitals_scenario_is_cut_off_at_duration(name):
    result = run_dry([*INSTANT, "--duration", "3m", "--scenario", f"{name}:{W}@1m"])
    assert scenario_lines(result.stderr) == [
        f"scenario {name} {W} started",
        f"scenario {name} {W} cut off: run ended",
    ]


def test_active_fall_is_cut_off_at_duration():
    result = run_dry([*INSTANT, "--duration", "5m", "--scenario", f"fall:{W}"])
    assert scenario_lines(result.stderr) == [
        f"scenario fall {W} started",
        f"scenario fall {W} cut off: run ended",
    ]


def test_cut_off_lines_are_sorted_by_device_im_ok_first():
    result = run_dry(
        [*INSTANT, "--duration", "1m"]
        # Both falls' messages are due at 58 s and 60 s, so both im-oks are deferred
        # past the end (63 s and 65 s).
        + ["--scenario", f"fall:{OTHER}@38s", "--scenario", f"no-pulse:{W}@20s"]
        + ["--scenario", f"im-ok:{OTHER}@40s", "--scenario", "fall:wearable-0004@40s"]
        + ["--scenario", "im-ok:wearable-0004@45s"]
    )
    cut = [x for x in scenario_lines(result.stderr) if x.endswith("cut off: run ended")]
    assert cut == [
        "scenario im-ok wearable-0004 cut off: run ended",
        "scenario fall wearable-0004 cut off: run ended",
        f"scenario no-pulse {W} cut off: run ended",
        f"scenario im-ok {OTHER} cut off: run ended",
        f"scenario fall {OTHER} cut off: run ended",
    ]


def test_scenarios_are_cut_off_when_the_run_is_stopped():
    fake = fakes.FakeTime()
    stop = threading.Event()
    fake.hooks.append((90.0, stop.set))
    result = run_dry(
        [*BASE, "--scenario", f"fall:{W}@30s", "--scenario", f"im-ok:{OTHER}@40s"],
        clock=clock_mod.RealClock(fake.monotonic, fake.sleep),
        stop=stop,
    )
    assert result.code == 0
    assert scenario_lines(result.stderr) == [
        f"scenario fall {W} started",
        f"scenario im-ok {OTHER} started",
        f"scenario im-ok {OTHER} ended",
        f"scenario fall {W} cut off: run ended",
    ]
    assert result.stderr.splitlines()[-1].startswith("total 0:01:30 ")


def test_no_cut_off_lines_when_every_scenario_finished():
    result = run_dry(
        [*INSTANT, "--duration", "5m"]
        + ["--scenario", f"hr-spike:{W}@30s", "--scenario", f"im-ok:{OTHER}@30s"]
        + [
            "--scenario",
            "fall:wearable-0004@30s",
            "--scenario",
            "im-ok:wearable-0004@40s",
        ]
    )
    lines = scenario_lines(result.stderr)
    assert len(lines) == 8
    assert not any("cut off" in x for x in lines)
    assert all(
        x.endswith(("started", "ended", "(after the fall message)")) for x in lines
    )


def test_no_cut_off_lines_without_scenarios():
    result = run_dry([*INSTANT, "--duration", "2m"])
    assert "cut off" not in result.stderr


# ---- typed commands (criterion 3) ----
# A non-instant dry-run with an InstantClock: typed commands are on, time still jumps.


class FakeCommands:
    """Lines typed at elapsed times; records every poll."""

    def __init__(self, script: list[tuple[int, str]]) -> None:
        self.script = sorted(script)
        self.polls: list[int] = []
        self.delivered: list[tuple[int, str]] = []

    def poll(self, now_ms: int) -> list[str]:
        self.polls.append(now_ms)
        out = []
        while self.script and self.script[0][0] <= now_ms:
            _, line = self.script.pop(0)
            self.delivered.append((now_ms, line))
            out.append(line)
        return out


class NeverPoll:
    def poll(self, now_ms: int) -> list[str]:
        raise AssertionError("typed commands must be off")


def typed_run(script, duration="5m", extra=()):
    source = FakeCommands(script)
    result = run_dry(
        [*BASE, "--duration", duration, *extra],
        clock=InstantClock(),
        commands=source,
        new_id=id_counter(),
    )
    return result, source


def test_typed_command_starts_the_scenario_at_once():
    result, source = typed_run([(30_000, f"fall {W}\n")])
    assert result.code == 0
    ((at, _),) = source.delivered
    assert 30_000 <= at < 30_500
    assert scenario_lines(result.stderr) == [
        f"scenario fall {W} started",
        f"scenario fall {W} cut off: run ended",  # down for 10 min; the run is 5
    ]
    (fall,) = [x for x in result.lines if message_of(x) == "fall"]
    assert device_of(fall) == W
    assert ts_to_epoch_ms(fall["payload"]["ts"]) == START_EPOCH_MS + at


def test_typed_im_ok_sends_alert_cancel_at_the_time_it_was_typed():
    result, source = typed_run([(45_000, f"im-ok {W}")])
    ((at, _),) = source.delivered
    (cancel,) = [x for x in result.lines if message_of(x) == "alert_cancel"]
    assert device_of(cancel) == W
    assert ts_to_epoch_ms(cancel["payload"]["ts"]) == START_EPOCH_MS + at
    assert set(cancel["payload"]) == {"schema_version", "message_id", "ts"}


def test_typed_commands_are_polled_throughout_the_run():
    _, source = typed_run([])
    assert source.polls
    assert max(b - a for a, b in zip(source.polls, source.polls[1:])) <= 250
    assert max(source.polls) < 5 * 60_000


def test_help_lists_the_scenario_names():
    result, _ = typed_run([(10_000, "help\n")])
    assert result.code == 0
    help_lines = [x for x in result.stderr.splitlines() if x.startswith("scenarios: ")]
    assert len(help_lines) == 1
    for name in scenarios.NAMES:
        assert name in help_lines[0]
    assert scenario_lines(result.stderr) == []


@pytest.mark.parametrize(
    "line",
    [
        "zzqx wearable-0005",
        "zzqx",
        "fall wearable-0005 zzqx",
        "fall zzqx-0005",
        "fall wearable-12zzqx",
        "help zzqx",
        "fall:wearable-0005",
    ],
)
def test_bad_typed_line_prints_one_error_without_the_text_and_the_run_goes_on(line):
    result, _ = typed_run([(10_000, line + "\n"), (20_000, f"im-ok {W}\n")])
    assert result.code == 0
    errors = [x for x in result.stderr.splitlines() if x.startswith("error: ")]
    assert len(errors) == 1
    assert errors[0].startswith("error: command: ")
    assert "zzqx" not in result.stderr
    assert line not in result.stderr
    assert scenario_lines(result.stderr) == [
        f"scenario im-ok {W} started",
        f"scenario im-ok {W} ended",
    ]
    assert result.stderr.splitlines()[-1].startswith("total 0:05:00")


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("fall bedroom-0005", "bedroom-0005 is not a wearable"),
        ("fall wearable-0099", "wearable-0099 is not in the devices file"),
        ("fall wearable-0002", "wearable-0002 is not in this run (--devices)"),
    ],
)
def test_typed_command_for_a_device_outside_the_run_is_an_error(line, problem):
    result, _ = typed_run(
        [(10_000, line)], extra=["--devices", f"{W},wearable-0001,bedroom-0005"]
    )
    assert result.code == 0
    errors = [x for x in result.stderr.splitlines() if x.startswith("error: ")]
    assert errors == [f"error: command: {problem}"]
    assert scenario_lines(result.stderr) == []


def test_blank_typed_line_is_ignored():
    result, _ = typed_run([(10_000, "\n"), (11_000, "   \n")])
    assert result.code == 0
    assert [x for x in result.stderr.splitlines() if x.startswith("error")] == []


def test_typed_skip_reasons_are_printed():
    result, _ = typed_run(
        [
            (30_000, f"hr-spike {W}"),
            (40_000, f"fall {W}"),
            (50_000, f"im-ok {W}"),
            (52_000, f"im-ok {W}"),
        ]
    )
    assert scenario_lines(result.stderr) == [
        f"scenario hr-spike {W} started",
        f"scenario fall {W} skipped: busy",
        f"scenario im-ok {W} started",
        f"scenario im-ok {W} ended",
        f"scenario im-ok {W} skipped: too soon",
        f"scenario hr-spike {W} ended",
    ]


def test_typed_commands_are_off_with_instant():
    result = run_dry([*INSTANT, "--duration", "3m"], commands=NeverPoll())
    assert result.code == 0


def test_typed_and_flagged_scenarios_together():
    result, _ = typed_run(
        [(60_000, f"no-pulse {OTHER}")], extra=["--scenario", f"fall:{W}"]
    )
    lines = scenario_lines(result.stderr)
    assert lines == [
        f"scenario fall {W} started",
        f"scenario no-pulse {OTHER} started",
        f"scenario no-pulse {OTHER} ended",  # 3 minutes later; the fall lasts 10
        f"scenario fall {W} cut off: run ended",
    ]


# ---- stdin (criterion 3) ----


class FakeStdin(io.StringIO):
    def __init__(self, text: str, terminal: bool) -> None:
        super().__init__(text)
        self.terminal = terminal

    def isatty(self) -> bool:
        return self.terminal


def test_stdin_that_is_not_a_terminal_is_never_read(monkeypatch):
    monkeypatch.setattr(sys, "stdin", FakeStdin(f"fall {W}\n", terminal=False))
    monkeypatch.setattr(main, "StdinCommands", lambda *a: pytest.fail("read stdin"))
    result = run_dry([*BASE, "--duration", "2m"], clock=InstantClock())
    assert result.code == 0
    assert scenario_lines(result.stderr) == []


def test_terminal_stdin_is_not_read_with_instant(monkeypatch):
    monkeypatch.setattr(sys, "stdin", FakeStdin(f"fall {W}\n", terminal=True))
    monkeypatch.setattr(main, "StdinCommands", lambda *a: pytest.fail("read stdin"))
    result = run_dry([*INSTANT, "--duration", "2m"])
    assert result.code == 0
    assert scenario_lines(result.stderr) == []


def test_terminal_stdin_is_read_when_the_run_is_not_instant(monkeypatch):
    stdin = FakeStdin("", terminal=True)
    monkeypatch.setattr(sys, "stdin", stdin)
    made = []

    def fake_source(stream):
        made.append(stream)
        return FakeCommands([(5_000, f"im-ok {W}")])

    monkeypatch.setattr(main, "StdinCommands", fake_source)
    result = run_dry([*BASE, "--duration", "1m"], clock=InstantClock())
    assert made == [stdin]
    assert f"scenario im-ok {W} started" in scenario_lines(result.stderr)


def test_stdin_commands_reads_lines_on_a_background_thread():
    source = commands_mod.StdinCommands(io.StringIO(f"help\nfall {W}\n"))
    got: list[str] = []
    deadline = time.monotonic() + 5
    while len(got) < 2 and time.monotonic() < deadline:
        got += source.poll(0)
    assert got == ["help\n", f"fall {W}\n"]
    assert source.poll(0) == []


def test_stdin_commands_stops_quietly_when_stdin_is_closed():
    stream = io.StringIO("")
    stream.close()
    source = commands_mod.StdinCommands(stream)
    assert source.poll(0) == []


def test_typed_lines_never_reach_the_output_as_text():
    # Fixed message IDs, so a digit run in a random UUID can't match by chance.
    result, _ = typed_run([(10_000, "fall wearable-0005 4242zzqx")])
    text = UUID_RE.sub("", result.stderr)
    assert "4242" not in text and "zzqx" not in text
    assert "zzqx" not in result.stdout
