"""Tests for `ingest-check.py --history` and the smoke test's --history command
(docs/features/ingest-history.md, criteria 20-22, 26).

No network: sockets are blocked (conftest); S3 and STS go to moto, DynamoDB to a
botocore Stubber. Only synthetic IDs and values.
"""

from __future__ import annotations

import gzip
import io
import json
from datetime import UTC, datetime, timedelta, timezone

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
MOTO_ACCOUNT = "123456789012"
BUCKET = f"silvercare-history-{MOTO_ACCOUNT}"
TABLE = "silvercare-latest"
SECRET_VALUE = "synthetic-field-value-777"


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
    monkeypatch.delenv("MOTO_ACCOUNT_ID", raising=False)


@pytest.fixture
def s3():
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name="ap-southeast-1")
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "ap-southeast-1"},
        )
        yield client


def gz_lines(*records) -> bytes:
    return gzip.compress(
        (
            "\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n"
        ).encode()
    )


def history_line(message_id, message="vitals"):
    return {
        "message_id": message_id,
        "device_id": WEARABLE,
        "message": message,
        "hr_bpm": SECRET_VALUE,
        "received_at": 1792293300120,
    }


def put(s3, message, date, name, *records):
    key = f"telemetry/message={message}/date={date}/{name}.jsonl.gz"
    s3.put_object(Bucket=BUCKET, Key=key, Body=gz_lines(*records))
    return key


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def yesterday() -> str:
    return (datetime.now(UTC).date() - timedelta(days=1)).isoformat()


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


def run_history(check_mod, s3, valid, invalid, started=None, device=WEARABLE, **kw):
    clock = kw.pop("clock", None) or FakeClock()
    return check_mod.check_history(
        s3,
        BUCKET,
        device,
        valid,
        invalid,
        started or datetime.now(UTC),
        sleep=kw.pop("sleep", clock.sleep),
        clock=clock.clock,
        **kw,
    )


# ---- pure helpers ----


def test_history_bucket_default_name(check_mod):
    assert check_mod.history_bucket("000000000000") == "silvercare-history-000000000000"


@pytest.mark.parametrize(
    "bad", ["", "12345", "1234567890123", "abcdefghijkl", "12345678901\n"]
)
def test_history_bucket_rejects_malformed_account(check_mod, bad):
    with pytest.raises(ValueError):
        check_mod.history_bucket(bad)


def test_history_prefixes_cover_device_types_today_and_yesterday(check_mod):
    now = datetime(2026, 10, 18, 3, 0, tzinfo=UTC)

    assert check_mod.history_prefixes(BEDROOM, now) == [
        "telemetry/message=motion/date=2026-10-18/",
        "telemetry/message=motion/date=2026-10-17/",
        "telemetry/message=bed/date=2026-10-18/",
        "telemetry/message=bed/date=2026-10-17/",
        "telemetry/message=status/date=2026-10-18/",
        "telemetry/message=status/date=2026-10-17/",
    ]


def test_history_prefixes_use_utc_date_not_local(check_mod):
    # 2026-10-18 01:00 in Singapore is still 2026-10-17 UTC.
    sgt = timezone(timedelta(hours=8))
    now = datetime(2026, 10, 18, 1, 0, tzinfo=sgt)

    prefixes = check_mod.history_prefixes(WEARABLE, now)

    assert prefixes[:2] == [
        "telemetry/message=vitals/date=2026-10-17/",
        "telemetry/message=vitals/date=2026-10-16/",
    ]


def test_ids_in_object_reads_message_ids_only(check_mod):
    data = gz_lines(history_line(ID_A), {"raw_body": "junk"}, history_line(ID_B))

    assert check_mod.ids_in_object(data) == {ID_A, ID_B}


def test_ids_in_object_skips_unreadable_lines(check_mod):
    data = gzip.compress(
        f'not json\n{{"message_id":"{ID_A}"}}\n[1]\n{{"message_id":5}}\n'.encode()
    )

    assert check_mod.ids_in_object(data) == {ID_A}


@pytest.mark.parametrize("data", [b"", b"not gzip", gzip.compress(b"\xff\xfe")])
def test_ids_in_object_of_unreadable_object_is_empty(check_mod, data):
    assert check_mod.ids_in_object(data) == set()


def test_evaluate_history_passes_with_valid_invalid_and_duplicates(check_mod):
    assert (
        check_mod.evaluate_history([ID_A, ID_A, ID_B, ID_BAD], [ID_A, ID_B], [ID_BAD])
        == []
    )


def test_evaluate_history_requires_invalid_ids(check_mod):
    assert check_mod.evaluate_history([ID_A], [ID_A], [ID_BAD]) == [
        f"history: invalid message {ID_BAD} is not in S3"
    ]


def test_evaluate_history_names_missing_valid_id(check_mod):
    assert check_mod.evaluate_history([ID_BAD], [ID_A], [ID_BAD]) == [
        f"history: valid message {ID_A} is not in S3"
    ]


# ---- bucket resolution ----


def test_resolve_bucket_defaults_to_account_from_sts(check_mod):
    sts = boto3.client("sts", region_name="ap-southeast-1")
    stub = Stubber(sts)
    stub.add_response(
        "get_caller_identity",
        {
            "Account": "000000000000",
            "Arn": "arn:aws:iam::000000000000:user/test",
            "UserId": "AIDATEST",
        },
        {},
    )

    with stub:
        assert check_mod.resolve_bucket(sts, None) == "silvercare-history-000000000000"
    stub.assert_no_pending_responses()


def test_resolve_bucket_override_does_not_call_sts(check_mod):
    class NoCalls:
        def __getattr__(self, name):
            raise AssertionError("must not call STS")

    assert check_mod.resolve_bucket(NoCalls(), "my-test-bucket") == "my-test-bucket"


# ---- check_history against moto S3 ----


def test_check_history_passes_when_all_ids_found(check_mod, s3):
    put(s3, "vitals", today(), "a", history_line(ID_A), history_line(ID_BAD))
    put(s3, "fall", today(), "b", history_line(ID_B, "fall"))
    put(
        s3,
        "status",
        today(),
        "c",
        history_line(ID_C, "status"),
        history_line(ID_C, "status"),
    )

    assert run_history(check_mod, s3, [ID_A, ID_B, ID_C], [ID_BAD]) == []


def test_check_history_finds_objects_under_yesterday(check_mod, s3):
    put(s3, "vitals", yesterday(), "a", history_line(ID_A))

    assert run_history(check_mod, s3, [ID_A], []) == []


def test_check_history_fails_naming_missing_ids(check_mod, s3):
    put(s3, "vitals", today(), "a", history_line(ID_A))

    failures = run_history(check_mod, s3, [ID_A, ID_B], [ID_BAD])

    assert failures == [
        f"history: valid message {ID_B} is not in S3",
        f"history: invalid message {ID_BAD} is not in S3",
    ]


def test_check_history_requires_invalid_ids(check_mod, s3):
    put(s3, "vitals", today(), "a", history_line(ID_A))

    assert run_history(check_mod, s3, [ID_A], [ID_BAD]) == [
        f"history: invalid message {ID_BAD} is not in S3"
    ]


def test_check_history_ignores_other_message_types_and_dates(check_mod, s3):
    put(s3, "motion", today(), "a", history_line(ID_A))  # not a wearable type
    put(s3, "unknown", today(), "b", history_line(ID_A))
    put(s3, "vitals", "2020-01-01", "c", history_line(ID_A))

    assert run_history(check_mod, s3, [ID_A], []) == [
        f"history: valid message {ID_A} is not in S3"
    ]


def test_check_history_ignores_objects_older_than_start_minus_15_minutes(check_mod, s3):
    put(s3, "vitals", today(), "a", history_line(ID_A))
    started = datetime.now(UTC) + timedelta(minutes=16)

    assert run_history(check_mod, s3, [ID_A], [], started=started) == [
        f"history: valid message {ID_A} is not in S3"
    ]


def test_check_history_includes_objects_within_lookback(check_mod, s3):
    put(s3, "vitals", today(), "a", history_line(ID_A))
    started = datetime.now(UTC) + timedelta(minutes=14)

    assert run_history(check_mod, s3, [ID_A], [], started=started) == []


def test_check_history_polls_until_object_appears(check_mod, s3):
    clock = FakeClock()

    def sleep(seconds):
        clock.sleep(seconds)
        put(s3, "fall", today(), "late", history_line(ID_A, "fall"))

    failures = run_history(check_mod, s3, [ID_A], [], clock=clock, sleep=sleep)

    assert failures == []
    assert clock.sleeps == [check_mod.HISTORY_POLL_INTERVAL_S]


def test_check_history_gives_up_within_timeout(check_mod, s3):
    clock = FakeClock()

    failures = run_history(check_mod, s3, [ID_A], [], clock=clock)

    assert failures == [f"history: valid message {ID_A} is not in S3"]
    assert clock.now <= check_mod.HISTORY_TIMEOUT_S
    assert check_mod.HISTORY_TIMEOUT_S == 420.0


# ---- main --history ----


def low_level_item(*ids):
    return {"entries": {"L": [{"M": {"message_id": {"S": i}}} for i in ids]}}


def stub_latest_pass(check_mod, device_id, valid_ids):
    """DynamoDB stub where the latest check passes (two rounds of reads)."""
    client = boto3.client("dynamodb", region_name="ap-southeast-1")
    stub = Stubber(client)
    items = [low_level_item(*valid_ids)] + [None] * 2
    for _ in range(2):
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
    return client, stub


@pytest.fixture
def no_wait(check_mod, monkeypatch):
    real_check, real_history = check_mod.check, check_mod.check_history

    def fast_check(*args, **kwargs):
        clock = FakeClock()
        return real_check(*args, sleep=clock.sleep, clock=clock.clock, **kwargs)

    def fast_history(*args, **kwargs):
        clock = FakeClock()
        return real_history(*args, sleep=clock.sleep, clock=clock.clock, **kwargs)

    monkeypatch.setattr(check_mod, "check", fast_check)
    monkeypatch.setattr(check_mod, "check_history", fast_history)


def argv(*extra, device=WEARABLE, valid=(ID_A,), invalid=(ID_BAD,)):
    return [
        "--device",
        device,
        "--message-ids",
        ",".join(valid),
        "--invalid-ids",
        ",".join(invalid),
        *extra,
    ]


def test_main_history_pass_with_default_bucket_from_sts(check_mod, s3, no_wait, capsys):
    put(s3, "vitals", today(), "a", history_line(ID_A), history_line(ID_BAD))
    client, stub = stub_latest_pass(check_mod, WEARABLE, [ID_A])
    sts = boto3.client("sts", region_name="ap-southeast-1")

    with stub:
        code = check_mod.main(
            argv("--history"), client=client, s3_client=s3, sts_client=sts
        )

    assert capsys.readouterr().out == "PASS\n"
    assert code == 0


def test_main_history_fail_names_ids_and_prints_no_contents(
    check_mod, s3, no_wait, capsys
):
    put(s3, "vitals", today(), "a", history_line(ID_A))
    client, stub = stub_latest_pass(check_mod, WEARABLE, [ID_A])
    sts = boto3.client("sts", region_name="ap-southeast-1")

    with stub:
        code = check_mod.main(
            argv("--history"), client=client, s3_client=s3, sts_client=sts
        )

    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert code == 1
    assert text == f"FAIL  history: invalid message {ID_BAD} is not in S3\n"
    for value in (SECRET_VALUE, "hr_bpm", WEARABLE, "{", "telemetry/"):
        assert value not in text


def test_main_history_bucket_override_skips_sts(check_mod, s3, no_wait, capsys):
    s3.create_bucket(
        Bucket="override-test-bucket",
        CreateBucketConfiguration={"LocationConstraint": "ap-southeast-1"},
    )
    key = f"telemetry/message=vitals/date={today()}/x.jsonl.gz"
    s3.put_object(
        Bucket="override-test-bucket",
        Key=key,
        Body=gz_lines(history_line(ID_A), history_line(ID_BAD)),
    )
    client, stub = stub_latest_pass(check_mod, WEARABLE, [ID_A])

    class NoCalls:
        def __getattr__(self, name):
            raise AssertionError("must not call STS")

    with stub:
        code = check_mod.main(
            argv("--history", "--bucket", "override-test-bucket"),
            client=client,
            s3_client=s3,
            sts_client=NoCalls(),
        )

    assert capsys.readouterr().out == "PASS\n"
    assert code == 0


def test_main_history_missing_bucket_reports_error_code(check_mod, s3, no_wait, capsys):
    client, stub = stub_latest_pass(check_mod, WEARABLE, [ID_A])

    with stub:
        code = check_mod.main(
            argv("--history", "--bucket", "no-such-test-bucket"),
            client=client,
            s3_client=s3,
        )

    assert code == 1
    assert (
        capsys.readouterr().out
        == "FAIL  could not read no-such-test-bucket (NoSuchBucket)\n"
    )


def test_main_history_sts_error_reports_failure(check_mod, no_wait, capsys):
    client, stub = stub_latest_pass(check_mod, WEARABLE, [ID_A])
    sts = boto3.client("sts", region_name="ap-southeast-1")
    sts_stub = Stubber(sts)
    sts_stub.add_client_error("get_caller_identity", service_error_code="ExpiredToken")

    with stub, sts_stub:
        code = check_mod.main(argv("--history"), client=client, sts_client=sts)

    assert code == 1
    assert "(ExpiredToken)" in capsys.readouterr().out


def test_main_without_history_does_not_touch_s3(check_mod, no_wait, capsys):
    client, stub = stub_latest_pass(check_mod, WEARABLE, [ID_A])

    class NoCalls:
        def __getattr__(self, name):
            raise AssertionError("must not call S3 or STS")

    with stub:
        code = check_mod.main(
            argv(), client=client, s3_client=NoCalls(), sts_client=NoCalls()
        )

    assert code == 0


@pytest.mark.parametrize(
    "extra",
    [
        ["--bucket", "some-test-bucket"],
        ["--history", "--bucket", "Bad_Bucket"],
        ["--history", "--bucket", "x"],
    ],
)
def test_main_rejects_bad_bucket_arguments(check_mod, capsys, extra):
    class NoCalls:
        def __getattr__(self, name):
            raise AssertionError("must not call AWS")

    assert (
        check_mod.main(
            argv(*extra), client=NoCalls(), s3_client=NoCalls(), sts_client=NoCalls()
        )
        == 2
    )
    assert "error:" in capsys.readouterr().err


# ---- iot-smoke-test.py prints the --history variant (criterion 22) ----


def test_smoke_history_command(smoke):
    cmd = smoke.ingest_check_command(WEARABLE, ["a", "b"], ["c"], history=True)

    assert cmd == (
        "python scripts/ingest-check.py --device wearable-0001"
        " --message-ids a,b --invalid-ids c --history"
    )


def test_smoke_report_ingest_prints_history_command(smoke, check_mod):
    n = iter(range(1, 100))
    messages = smoke.build_ingest_messages(
        "bedroom",
        datetime(2026, 10, 18, 3, 15, tzinfo=UTC),
        lambda: f"00000000-0000-4000-8000-{next(n):012d}",
    )
    out = io.StringIO()

    smoke.report_ingest(BEDROOM, messages, out)

    history_lines = [
        line.strip()
        for line in out.getvalue().splitlines()
        if line.strip().endswith("--history")
    ]
    assert len(history_lines) == 1
    args = history_lines[0].split()[2:]
    parsed_valid = args[args.index("--message-ids") + 1].split(",")
    parsed_invalid = args[args.index("--invalid-ids") + 1].split(",")
    assert parsed_valid == [p["message_id"] for k, _, p in messages if k == "valid"]
    assert parsed_invalid == [p["message_id"] for k, _, p in messages if k == "invalid"]
    assert check_mod.parse_ids(",".join(parsed_valid + parsed_invalid))
