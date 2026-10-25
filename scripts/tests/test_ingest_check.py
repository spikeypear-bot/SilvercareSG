"""Tests for scripts/ingest-check.py (docs/features/ingest-live.md, criteria 28, 29).

No network: the autouse no_network fixture blocks sockets, and DynamoDB calls go to a
botocore Stubber. Only synthetic IDs and values.
"""

from __future__ import annotations

import io

import boto3
import pytest
from botocore.stub import Stubber
from conftest import load_script

WEARABLE = "wearable-0001"
BEDROOM = "bedroom-0001"
ID_A = "00000000-0000-4000-8000-00000000000a"
ID_B = "00000000-0000-4000-8000-00000000000b"
ID_C = "00000000-0000-4000-8000-00000000000c"
ID_BAD = "00000000-0000-4000-8000-0000000000ff"
TABLE = "silvercare-latest"


@pytest.fixture(scope="module")
def check_mod():
    return load_script("ingest-check.py", "ingest_check")


@pytest.fixture(autouse=True)
def fake_aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-southeast-1")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")


def low_level_item(*message_ids, extra=True):
    """A low-level silvercare-latest item with entries holding synthetic values."""
    entries = []
    for message_id in message_ids:
        m = {"message_id": {"S": message_id}, "ts": {"S": "2026-10-18T03:12:00.000Z"}}
        if extra:
            m["hr_bpm"] = {"N": "187"}
            m["fw_version"] = {"S": "9.8.7-synthetic"}
        entries.append({"M": m})
    return {"entries": {"L": entries}}


def stubbed_client():
    client = boto3.client("dynamodb", region_name="ap-southeast-1")
    return client, Stubber(client)


def expect_reads(check_mod, stub, device_id, items):
    """Queue one GetItem response per item key (None = no item)."""
    for key, item in zip(check_mod.item_keys(device_id), items, strict=True):
        stub.add_response(
            "get_item",
            {"Item": item} if item is not None else {},
            {
                "TableName": TABLE,
                "Key": key,
                "ConsistentRead": True,
                "ProjectionExpression": "#entries",
                "ExpressionAttributeNames": {"#entries": "entries"},
            },
        )


# ---- pure helpers ----


@pytest.mark.parametrize(
    ("device_id", "expected"), [(WEARABLE, "wearable"), (BEDROOM, "bedroom")]
)
def test_device_type(check_mod, device_id, expected):
    assert check_mod.device_type(device_id) == expected


@pytest.mark.parametrize(
    "bad", ["wearable-001", "watch-0001", "wearable-0001\n", "", "bedroom-00001"]
)
def test_device_type_rejects_malformed_id(check_mod, bad):
    with pytest.raises(ValueError):
        check_mod.device_type(bad)


def test_parse_ids_splits_and_strips(check_mod):
    assert check_mod.parse_ids(f" {ID_A}, {ID_B} ,,") == [ID_A, ID_B]


def test_parse_ids_empty_string_is_empty_list(check_mod):
    assert check_mod.parse_ids("") == []


@pytest.mark.parametrize("bad", ["not-a-uuid", f"{ID_A},nope", f"{ID_A};{ID_B}"])
def test_parse_ids_rejects_malformed(check_mod, bad):
    with pytest.raises(ValueError):
        check_mod.parse_ids(bad)


@pytest.mark.parametrize(
    ("device_id", "messages"),
    [(WEARABLE, ["vitals", "fall", "status"]), (BEDROOM, ["motion", "bed", "status"])],
)
def test_item_keys_cover_every_message_type_of_the_device(
    check_mod, device_id, messages
):
    assert check_mod.item_keys(device_id) == [
        {"pk": {"S": f"DEVICE#{device_id}"}, "sk": {"S": f"MSG#{m}"}} for m in messages
    ]


def test_entry_ids_reads_message_ids_only(check_mod):
    assert check_mod.entry_ids(low_level_item(ID_A, ID_B)) == [ID_A, ID_B]


@pytest.mark.parametrize("item", [None, {}, {"entries": {"L": []}}])
def test_entry_ids_of_missing_or_empty_item(check_mod, item):
    assert check_mod.entry_ids(item) == []


def test_entry_ids_skips_entries_without_message_id(check_mod):
    item = {
        "entries": {
            "L": [{"M": {"ts": {"S": "x"}}}, {"M": {"message_id": {"S": ID_A}}}]
        }
    }

    assert check_mod.entry_ids(item) == [ID_A]


def test_evaluate_passes_when_each_valid_id_once_and_no_invalid(check_mod):
    assert check_mod.evaluate([ID_A, ID_B], [ID_A, ID_B], [ID_BAD]) == []


def test_evaluate_fails_when_valid_id_missing(check_mod):
    failures = check_mod.evaluate([ID_A], [ID_A, ID_B], [])

    assert failures == [f"valid message {ID_B} is not stored"]


def test_evaluate_fails_when_valid_id_stored_twice(check_mod):
    failures = check_mod.evaluate([ID_A, ID_A], [ID_A], [])

    assert failures == [f"valid message {ID_A} is stored 2 times"]


def test_evaluate_fails_when_invalid_id_stored(check_mod):
    failures = check_mod.evaluate([ID_A, ID_BAD], [ID_A], [ID_BAD])

    assert failures == [f"invalid message {ID_BAD} is stored"]


def test_evaluate_lists_every_failure(check_mod):
    failures = check_mod.evaluate([ID_B, ID_B, ID_BAD], [ID_A, ID_B], [ID_BAD])

    assert len(failures) == 3


def test_evaluate_ignores_unrelated_stored_ids(check_mod):
    assert check_mod.evaluate([ID_A, ID_C], [ID_A], [ID_BAD]) == []


# ---- reading with a Stubber ----


def test_read_stored_ids_reads_every_item_strongly_consistent(check_mod):
    client, stub = stubbed_client()
    expect_reads(
        check_mod, stub, WEARABLE, [low_level_item(ID_A), None, low_level_item(ID_B)]
    )

    with stub:
        ids = check_mod.read_stored_ids(client, TABLE, WEARABLE)

    assert ids == [ID_A, ID_B]
    stub.assert_no_pending_responses()


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


def run_check(check_mod, client, valid, invalid, clock, **kwargs):
    return check_mod.check(
        client,
        TABLE,
        BEDROOM,
        valid,
        invalid,
        sleep=clock.sleep,
        clock=clock.clock,
        **kwargs,
    )


def test_check_passes_and_confirms_after_settle(check_mod):
    client, stub = stubbed_client()
    items = [low_level_item(ID_A), low_level_item(ID_B), None]
    expect_reads(check_mod, stub, BEDROOM, items)
    expect_reads(check_mod, stub, BEDROOM, items)
    clock = FakeClock()

    with stub:
        failures = run_check(check_mod, client, [ID_A, ID_B], [ID_BAD], clock)

    assert failures == []
    assert clock.sleeps == [check_mod.SETTLE_S]
    stub.assert_no_pending_responses()


def test_check_polls_until_valid_messages_arrive(check_mod):
    client, stub = stubbed_client()
    expect_reads(check_mod, stub, BEDROOM, [None, None, None])
    expect_reads(check_mod, stub, BEDROOM, [low_level_item(ID_A), None, None])
    expect_reads(check_mod, stub, BEDROOM, [low_level_item(ID_A), None, None])
    clock = FakeClock()

    with stub:
        failures = run_check(check_mod, client, [ID_A], [], clock)

    assert failures == []
    assert clock.sleeps == [check_mod.POLL_INTERVAL_S, check_mod.SETTLE_S]


def test_check_fails_if_invalid_message_arrives_during_settle(check_mod):
    client, stub = stubbed_client()
    expect_reads(check_mod, stub, BEDROOM, [low_level_item(ID_A), None, None])
    expect_reads(
        check_mod, stub, BEDROOM, [low_level_item(ID_A), low_level_item(ID_BAD), None]
    )
    clock = FakeClock()

    with stub:
        failures = run_check(check_mod, client, [ID_A], [ID_BAD], clock)

    assert failures == [f"invalid message {ID_BAD} is stored"]


def test_check_gives_up_after_timeout(check_mod):
    client, stub = stubbed_client()
    for _ in range(4):
        expect_reads(check_mod, stub, BEDROOM, [None, None, None])
    clock = FakeClock()

    with stub:
        failures = run_check(
            check_mod, client, [ID_A], [], clock, timeout_s=6.0, poll_interval_s=2.0
        )

    assert failures == [f"valid message {ID_A} is not stored"]
    assert clock.now <= 6.0
    stub.assert_no_pending_responses()


def test_default_timeout_is_30_seconds(check_mod):
    assert check_mod.TIMEOUT_S == 30.0
    assert check_mod.REGION == "ap-southeast-1"


# ---- report and main ----


def test_report_pass(check_mod):
    out = io.StringIO()

    assert check_mod.report([], out) == 0
    assert out.getvalue() == "PASS\n"


def test_report_fail_lists_each_failure(check_mod):
    out = io.StringIO()

    code = check_mod.report(["a failed", "b failed"], out)

    assert code == 1
    assert out.getvalue() == "FAIL  a failed\nFAIL  b failed\n"


@pytest.fixture
def no_wait(check_mod, monkeypatch):
    """Make main's check() use a fake clock instead of real sleeps."""
    real_check = check_mod.check

    def fast_check(*args, **kwargs):
        clock = FakeClock()
        return real_check(*args, sleep=clock.sleep, clock=clock.clock, **kwargs)

    monkeypatch.setattr(check_mod, "check", fast_check)


def test_main_prints_pass_and_exits_zero(check_mod, no_wait, capsys):
    client, stub = stubbed_client()
    items = [low_level_item(ID_A), low_level_item(ID_B), low_level_item(ID_C)]
    expect_reads(check_mod, stub, WEARABLE, items)
    expect_reads(check_mod, stub, WEARABLE, items)

    with stub:
        code = check_mod.main(
            [
                "--device",
                WEARABLE,
                "--message-ids",
                f"{ID_A},{ID_B},{ID_C}",
                "--invalid-ids",
                ID_BAD,
            ],
            client=client,
        )

    assert code == 0
    assert capsys.readouterr().out == "PASS\n"


def test_main_prints_fail_and_never_field_values(check_mod, no_wait, capsys):
    client, stub = stubbed_client()
    for _ in range(20):
        expect_reads(
            check_mod,
            stub,
            WEARABLE,
            [low_level_item(ID_A, ID_A), None, low_level_item(ID_BAD)],
        )

    with stub:
        code = check_mod.main(
            ["--device", WEARABLE, "--message-ids", ID_A, "--invalid-ids", ID_BAD],
            client=client,
        )

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert code == 1
    assert f"FAIL  valid message {ID_A} is stored 2 times" in output
    assert f"FAIL  invalid message {ID_BAD} is stored" in output
    assert "PASS" not in output
    assert "187" not in output
    assert "9.8.7-synthetic" not in output
    assert "hr_bpm" not in output


def test_main_reports_aws_error_code_as_fail(check_mod, no_wait, capsys):
    client, stub = stubbed_client()
    stub.add_client_error(
        "get_item",
        service_error_code="AccessDeniedException",
        service_message="synthetic denial",
        http_status_code=400,
    )

    with stub:
        code = check_mod.main(
            ["--device", BEDROOM, "--message-ids", ID_A], client=client
        )

    out = capsys.readouterr().out
    assert code == 1
    assert out == f"FAIL  could not read {TABLE} (AccessDeniedException)\n"


@pytest.mark.parametrize(
    "argv",
    [
        ["--device", "watch-0001", "--message-ids", ID_A],
        ["--device", WEARABLE, "--message-ids", "not-a-uuid"],
        ["--device", WEARABLE, "--message-ids", ID_A, "--invalid-ids", "bad"],
        ["--device", WEARABLE, "--message-ids", ","],
    ],
)
def test_main_rejects_bad_arguments_without_reading(check_mod, capsys, argv):
    class NoCalls:
        def __getattr__(self, name):
            raise AssertionError("must not call AWS")

    assert check_mod.main(argv, client=NoCalls()) == 2
    assert "error:" in capsys.readouterr().err


def test_main_creates_client_in_singapore_region(check_mod, no_wait, monkeypatch):
    client, stub = stubbed_client()
    expect_reads(check_mod, stub, BEDROOM, [low_level_item(ID_A), None, None])
    expect_reads(check_mod, stub, BEDROOM, [low_level_item(ID_A), None, None])
    created = {}

    def fake_client(service, **kwargs):
        created.update(service=service, **kwargs)
        return client

    monkeypatch.setattr(boto3, "client", fake_client)

    with stub:
        code = check_mod.main(["--device", BEDROOM, "--message-ids", ID_A])

    assert code == 0
    assert created == {"service": "dynamodb", "region_name": "ap-southeast-1"}


def test_importing_creates_no_aws_client(monkeypatch):
    import sys

    monkeypatch.delitem(sys.modules, "ingest_check_fresh", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("no client at import")

    monkeypatch.setattr(boto3, "client", forbidden)
    load_script("ingest-check.py", "ingest_check_fresh")
