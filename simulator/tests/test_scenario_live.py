"""Alert scenarios in a live run (docs/features/simulator-alert-scenarios.md,
criterion 14), with the fake MQTT client and fake clock from fakes.py. The endpoint
is a fake host passed to main.main with a fake client factory: no network, and the
key files are empty placeholders in tmp_path."""

from __future__ import annotations

import json

import contract
import fakes
from conftest import START_EPOCH_MS, run_dry, ts_to_epoch_ms
from fakes import OK, REFUSE, World
from test_live import keys, run_live  # noqa: F401  (keys is a fixture)

W = "wearable-0005"
W2 = "wearable-0006"
DEVICES = f"{W},{W2},bedroom-0005"
ALERT_TYPES = {"fall", "vitals_alert", "alert_cancel"}


def message_of_topic(topic: str) -> str:
    return topic.split("/")[3]


def test_scenario_messages_go_on_the_devices_own_connection_qos_1(keys):  # noqa: F811
    world = World(fakes.FakeTime())
    result = run_live(
        keys,
        world,
        ["--duration", "3m", "--devices", DEVICES]
        + ["--scenario", f"hr-spike:{W}@30s", "--scenario", f"fall:{W2}@30s"]
        + ["--scenario", f"im-ok:{W2}@60s"],
        start_at="12:00",
    )
    assert result.code == 0
    alerts = [p for p in world.published if message_of_topic(p["topic"]) in ALERT_TYPES]
    assert sorted((p["device_id"], message_of_topic(p["topic"])) for p in alerts) == [
        (W, "vitals_alert"),
        (W2, "alert_cancel"),
        (W2, "fall"),
    ]
    for p in alerts:
        assert p["topic"].split("/")[2] == p["device_id"]
        assert p["qos"] == 1 and p["retain"] is False
        payload = json.loads(p["payload"])
        assert contract.problems(p["topic"], payload) == []
    times = {message_of_topic(p["topic"]): p["at"] for p in alerts}
    assert 40_000 <= times["vitals_alert"] < 40_000 + 100
    assert 50_000 <= times["fall"] < 50_000 + 100
    assert 60_000 <= times["alert_cancel"] < 60_000 + 100
    assert result.total().endswith(
        "fall 1 vitals_alert 1 alert_cancel 1 | dropped 0 | failed 0"
    )


def test_live_output_names_scenarios_and_devices_but_no_values(keys):  # noqa: F811
    world = World(fakes.FakeTime())
    result = run_live(
        keys,
        world,
        ["--duration", "2m", "--devices", DEVICES, "--scenario", f"spo2-drop:{W}"],
        start_at="12:00",
    )
    lines = [x for x in result.out_lines if x.startswith("scenario ")]
    assert lines == [
        f"scenario spo2-drop {W} started",
        f"scenario spo2-drop {W} cut off: run ended",
    ]
    assert "{" not in result.stdout and "spo2_pct" not in result.stdout
    assert result.stderr == ""


def test_scenario_messages_due_while_disconnected_are_dropped_not_queued(keys):  # noqa: F811
    # W2 drops at 45 s and can't reconnect before 70 s. Its fall message (50 s) and
    # the deferred alert_cancel (55 s) are due while it is down.
    world = World(
        fakes.FakeTime(),
        policy=lambda d, n, t: REFUSE if d == W2 and n > 1 and t < 70_000 else OK,
        drop=lambda d, t: d == W2 and 45_000 <= t < 46_000,
    )
    argv = ["--duration", "3m", "--devices", DEVICES]
    argv += ["--scenario", f"fall:{W2}@30s", "--scenario", f"im-ok:{W2}@52s"]
    result = run_live(keys, world, argv, start_at="12:00")
    assert result.code == 0

    (down,) = [t for t, d in world.drops if d == W2]
    up = max(t for t, d, ok in world.connacks if d == W2 and ok)
    assert 45_000 <= down < 46_000 and up >= 70_000

    sent_types = {message_of_topic(p["topic"]) for p in world.publishes(W2)}
    assert not sent_types & ALERT_TYPES  # never sent, not even after reconnecting
    assert not [p for p in world.publishes(W2) if down <= p["at"] < up]

    # The same run as a dry-run: every W2 message due while it was down is dropped.
    dry = run_dry(
        ["--dry-run", "--instant", "--random-seed", "3", "--start-at", "12:00"] + argv
    )

    def due_at(line):
        sent = ts_to_epoch_ms(line["payload"]["ts"]) - START_EPOCH_MS
        return sent + 20_000 if line["topic"].endswith("/fall") else sent

    missed = [
        line
        for line in dry.lines
        if line["topic"].split("/")[2] == W2 and down <= due_at(line) < up
    ]
    assert {message_of_topic(x["topic"]) for x in missed} >= {"fall", "alert_cancel"}
    total = result.total()
    assert f"| dropped {len(missed)} |" in total
    assert " fall 0 vitals_alert 0 alert_cancel 0 |" in total

    # The scenario itself still runs and says so.
    assert [x for x in result.out_lines if x.startswith("scenario ")] == [
        f"scenario fall {W2} started",
        f"scenario im-ok {W2} started (after the fall message)",
        f"scenario fall {W2} ended",
        f"scenario im-ok {W2} ended",
    ]
