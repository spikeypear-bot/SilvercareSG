"""scripts/seed-registry.py (docs/features/alerts-core.md, criteria 30 and 34).

Pure item building, the conditional put, and main() with moto or fakes. No network
(the autouse no_network fixture), and every file written goes under tmp_path.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from datetime import UTC, datetime
from types import ModuleType

import pytest
from botocore.exceptions import ClientError
from conftest import SCRIPTS_DIR, load_script
from helpers import device, write_devices_file

NOW = datetime(2026, 10, 18, 3, 0, tzinfo=UTC)
STAMP = "2026-10-18T03:00:00Z"
LATER = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)
REGION = "ap-southeast-1"


@pytest.fixture(scope="module")
def seed() -> ModuleType:
    return load_script("seed-registry.py", "seed_registry")


@pytest.fixture
def devices_file(tmp_path):
    return write_devices_file(
        tmp_path / "devices.json",
        [
            device("wearable-0001", canary=True),
            device("wearable-0004"),
            device("bedroom-0001", canary=True),
            device("bedroom-0003"),
        ],
    )


def by_key(items):
    return {(i["pk"], i["sk"]): i for i in items}


# ---- items (criterion 30) ----


def test_resident_profiles_are_exact(seed):
    items = seed.resident_items(NOW)

    assert items == [
        {
            "pk": f"RESIDENT#res-000{n}",
            "sk": "PROFILE",
            "entity_type": "RESIDENT",
            "display_name": f"Test Resident {letter}",
            "home_id": f"home-000{n}",
            "created_at": STAMP,
            "updated_at": STAMP,
        }
        for n, letter in ((1, "A"), (2, "B"), (3, "C"))
    ]


@pytest.mark.parametrize(
    ("device_id", "resident", "home"),
    [
        ("wearable-0001", "res-0001", "home-0001"),
        ("wearable-0003", "res-0003", "home-0003"),
        ("bedroom-0002", "res-0002", "home-0002"),
    ],
)
def test_assigned_device_item_is_exact(seed, device_id, resident, home):
    (item,) = seed.device_items([device(device_id)], NOW)

    assert item == {
        "pk": f"DEVICE#{device_id}",
        "sk": "ASSIGNMENT",
        "entity_type": "DEVICE",
        "device_type": device_id.split("-")[0],
        "resident_id": resident,
        "home_id": home,
        "assigned_at": STAMP,
        "gsi1_pk": f"RESIDENT#{resident}",
        "gsi1_sk": f"DEVICE#{device_id}",
    }


@pytest.mark.parametrize(
    "device_id", ["wearable-0004", "bedroom-0010", "wearable-0020"]
)
def test_spare_device_item_has_no_assignment(seed, device_id):
    (item,) = seed.device_items([device(device_id)], NOW)

    assert item == {
        "pk": f"DEVICE#{device_id}",
        "sk": "ASSIGNMENT",
        "entity_type": "DEVICE",
        "device_type": device_id.split("-")[0],
    }


def test_every_device_in_the_repo_list_gets_one_item(seed):
    devices = seed.load_devices(seed.DEFAULT_DEVICES_FILE)

    items = seed.build_items(devices, NOW)

    device_items = [i for i in items if i["sk"] == "ASSIGNMENT"]
    assert [i["pk"] for i in device_items] == [f"DEVICE#{d['id']}" for d in devices]
    assigned = {i["pk"] for i in device_items if "resident_id" in i}
    assert assigned == {
        f"DEVICE#{kind}-000{n}" for kind in ("wearable", "bedroom") for n in (1, 2, 3)
    }


def test_residents_come_first(seed, devices_file):
    items = seed.build_items(seed.load_devices(devices_file), NOW)

    assert [i["sk"] for i in items[:3]] == ["PROFILE"] * 3
    assert all(i["sk"] == "ASSIGNMENT" for i in items[3:])


def test_serialize_writes_every_attribute_as_a_string(seed):
    assert seed.serialize({"pk": "DEVICE#wearable-0001", "sk": "ASSIGNMENT"}) == {
        "pk": {"S": "DEVICE#wearable-0001"},
        "sk": {"S": "ASSIGNMENT"},
    }


def test_iso_now_is_utc_to_the_second(seed):
    from datetime import timedelta, timezone

    sgt = datetime(2026, 10, 18, 11, 0, 0, 999999, tzinfo=timezone(timedelta(hours=8)))

    assert seed.iso_now(sgt) == STAMP


# ---- devices.json validation ----


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        '{"devices": {}}',
        '{"devices": [{"id": "phone-0001", "type": "phone"}]}',
        '{"devices": [{"id": "wearable-0001", "type": "bedroom"}]}',
        (
            '{"devices": [{"id": "wearable-0001", "type": "wearable"},'
            ' {"id": "wearable-0001", "type": "wearable"}]}'
        ),
        '{"devices": ["wearable-0001"]}',
    ],
)
def test_malformed_devices_file_is_refused(seed, tmp_path, content):
    path = tmp_path / "devices.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        seed.load_devices(path)


# ---- conditional put ----


class FakeClient:
    """In-memory DynamoDB put_item honouring attribute_not_exists(pk)."""

    def __init__(self, error: str | None = None):
        self.items: dict[tuple[str, str], dict] = {}
        self.calls: list[dict] = []
        self.error = error

    def put_item(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise ClientError(
                {"Error": {"Code": self.error, "Message": "synthetic detail"}},
                "PutItem",
            )
        assert kwargs["ConditionExpression"] == "attribute_not_exists(pk)"
        item = kwargs["Item"]
        key = (item["pk"]["S"], item["sk"]["S"])
        if key in self.items:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}},
                "PutItem",
            )
        self.items[key] = item


def test_put_if_absent_reports_created_then_exists(seed):
    client = FakeClient()
    item = {"pk": "DEVICE#wearable-0004", "sk": "ASSIGNMENT"}

    assert seed.put_if_absent(client, "silvercare-registry", item) == "created"
    assert seed.put_if_absent(client, "silvercare-registry", item) == "exists"

    assert client.calls[0]["TableName"] == "silvercare-registry"
    assert client.calls[0]["ConditionExpression"] == "attribute_not_exists(pk)"


def test_put_if_absent_raises_other_errors(seed):
    client = FakeClient(error="ProvisionedThroughputExceededException")

    with pytest.raises(ClientError):
        seed.put_if_absent(client, "silvercare-registry", {"pk": "p", "sk": "s"})


# ---- main with moto ----


@pytest.fixture
def moto_registry(monkeypatch):
    import boto3
    from moto import mock_aws

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(
            TableName="silvercare-registry",
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client


def scan(client) -> dict:
    items = client.scan(TableName="silvercare-registry")["Items"]
    return {(i["pk"]["S"], i["sk"]["S"]): i for i in items}


def test_main_writes_every_item(seed, moto_registry, devices_file):
    out = io.StringIO()

    rc = seed.main(
        ["--devices-file", str(devices_file)], client=moto_registry, out=out, now=NOW
    )

    assert rc == 0
    stored = scan(moto_registry)
    expected = by_key(seed.build_items(seed.load_devices(devices_file), NOW))
    assert set(stored) == set(expected)
    assert stored[("DEVICE#wearable-0001", "ASSIGNMENT")]["resident_id"] == {
        "S": "res-0001"
    }
    assert out.getvalue().splitlines()[-1] == "done: 7 created"


def test_second_run_changes_nothing_and_reports_exists(
    seed, moto_registry, devices_file
):
    seed.main(
        ["--devices-file", str(devices_file)],
        client=moto_registry,
        out=io.StringIO(),
        now=NOW,
    )
    before = scan(moto_registry)
    out = io.StringIO()

    rc = seed.main(
        ["--devices-file", str(devices_file)], client=moto_registry, out=out, now=LATER
    )

    assert rc == 0
    assert scan(moto_registry) == before
    lines = out.getvalue().splitlines()
    assert all(line.endswith(": exists") for line in lines[:-1])
    assert lines[-1] == "done: 7 exists"


def test_existing_assignment_is_never_reassigned(seed, moto_registry, devices_file):
    # A developer already moved wearable-0001 to another resident by hand.
    moto_registry.put_item(
        TableName="silvercare-registry",
        Item={
            "pk": {"S": "DEVICE#wearable-0001"},
            "sk": {"S": "ASSIGNMENT"},
            "resident_id": {"S": "res-0002"},
        },
    )
    out = io.StringIO()

    seed.main(
        ["--devices-file", str(devices_file)], client=moto_registry, out=out, now=NOW
    )

    item = scan(moto_registry)[("DEVICE#wearable-0001", "ASSIGNMENT")]
    assert item["resident_id"] == {"S": "res-0002"}
    assert "DEVICE#wearable-0001 / ASSIGNMENT: exists" in out.getvalue()
    assert out.getvalue().splitlines()[-1] == "done: 6 created, 1 exists"


def test_output_has_only_keys_and_outcomes(seed, moto_registry, devices_file):
    out = io.StringIO()

    seed.main(
        ["--devices-file", str(devices_file)], client=moto_registry, out=out, now=NOW
    )

    text = out.getvalue()
    for hidden in ("Test Resident", "home-000", STAMP, "entity_type", "gsi1"):
        assert hidden not in text, hidden
    for line in text.splitlines()[:-1]:
        key, outcome = line.rsplit(": ", 1)
        assert outcome == "created"
        assert key.count(" / ") == 1


# ---- dry run, no network, no client ----


class NoCalls:
    def __getattr__(self, name):
        raise AssertionError(f"dry run must not call {name}")


def test_dry_run_prints_items_and_writes_nothing(seed, devices_file, monkeypatch):
    import boto3

    def no_client(*args, **kwargs):
        raise AssertionError("dry run must not create an AWS client")

    monkeypatch.setattr(boto3, "client", no_client)
    out = io.StringIO()

    rc = seed.main(
        ["--dry-run", "--devices-file", str(devices_file)],
        client=NoCalls(),
        out=out,
        now=NOW,
    )

    assert rc == 0
    lines = out.getvalue().splitlines()
    assert lines[:-1] == [
        "RESIDENT#res-0001 / PROFILE: dry-run",
        "RESIDENT#res-0002 / PROFILE: dry-run",
        "RESIDENT#res-0003 / PROFILE: dry-run",
        "DEVICE#wearable-0001 / ASSIGNMENT: dry-run",
        "DEVICE#wearable-0004 / ASSIGNMENT: dry-run",
        "DEVICE#bedroom-0001 / ASSIGNMENT: dry-run",
        "DEVICE#bedroom-0003 / ASSIGNMENT: dry-run",
    ]
    assert lines[-1] == "done: 7 dry-run"


def test_real_run_creates_client_in_singapore_region(seed, devices_file, monkeypatch):
    import boto3

    created: list = []
    fake = FakeClient()

    def fake_client(service, **kwargs):
        created.append((service, kwargs.get("region_name")))
        return fake

    monkeypatch.setattr(boto3, "client", fake_client)

    rc = seed.main(["--devices-file", str(devices_file)], out=io.StringIO(), now=NOW)

    assert rc == 0
    assert created == [("dynamodb", REGION)]
    assert len(fake.items) == 7


def test_import_creates_no_aws_client(monkeypatch):
    import boto3

    def no_client(*args, **kwargs):
        raise AssertionError("import must not create an AWS client")

    monkeypatch.setattr(boto3, "client", no_client)
    monkeypatch.setattr(boto3, "resource", no_client)
    monkeypatch.setattr(boto3, "Session", no_client)
    name = "seed_registry_fresh_import"
    spec = importlib.util.spec_from_file_location(
        name, SCRIPTS_DIR / "seed-registry.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)

    spec.loader.exec_module(module)

    assert "boto3" not in vars(module)


# ---- errors ----


def test_invalid_table_name_is_refused(seed, devices_file):
    out = io.StringIO()

    rc = seed.main(
        ["--table", "bad table!", "--devices-file", str(devices_file)],
        client=NoCalls(),
        out=out,
    )

    assert rc == 2
    assert out.getvalue() == "error: invalid table name\n"


def test_missing_devices_file_is_refused(seed, tmp_path):
    out = io.StringIO()

    rc = seed.main(
        ["--devices-file", str(tmp_path / "absent.json")], client=NoCalls(), out=out
    )

    assert rc == 2
    assert out.getvalue().startswith("error: ")


def test_aws_error_prints_only_the_code(seed, devices_file):
    out = io.StringIO()

    rc = seed.main(
        ["--devices-file", str(devices_file)],
        client=FakeClient(error="AccessDeniedException"),
        out=out,
        now=NOW,
    )

    assert rc == 1
    assert out.getvalue() == "error: AccessDeniedException\n"
    assert "synthetic detail" not in out.getvalue()
