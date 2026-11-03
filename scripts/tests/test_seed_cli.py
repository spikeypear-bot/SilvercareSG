"""scripts/seed.py end to end through main() (criteria 3, 9-19, 22).

Live mode runs against moto DynamoDB, Cognito, and STS (conftest's live fixture);
passwords files live under tmp_path only.
"""

from __future__ import annotations

import io
import json
import os
import re
from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError, NoCredentialsError
from conftest import Recorder, boto_client
from helpers import REPO_ROOT, write_devices_file

TABLE = "silvercare-registry"
NOW = datetime(2026, 11, 1, 3, 0, 0, tzinfo=UTC)
EMAILS = {
    "test-doctor": "test-doctor@example.com",
    "test-nurse": "test-nurse@example.com",
    "test-caregiver": "test-caregiver@example.com",
    "test-family": "test-family@example.com",
}
GROUP_OF = {
    "test-doctor": "doctor",
    "test-nurse": "nurse",
    "test-caregiver": "caregiver",
    "test-family": "family",
}
HANDMADE = "handmade-doctor@example.com"
KNOWN_PW = "Known-Synthetic-Pw-01"
WRITE_OPS = {
    "admin_create_user",
    "admin_set_user_password",
    "admin_add_user_to_group",
    "admin_remove_user_from_group",
    "admin_delete_user",
    "admin_update_user_attributes",
    "batch_write_item",
    "put_item",
    "delete_item",
    "update_item",
    "create_table",
}
# Strings from item contents that must never be printed.
CONTENT_MARKERS = (
    "Test Resident",
    "Synthetic note",
    "Test Contact",
    "+65",
    "Test Doctor",
    "Test Nurse",
    "Test Caregiver",
    "Test Family",
    "daughter",
)


def no_sleep(seconds: float) -> None:
    pass


def refuse_input(prompt: str) -> str:
    raise AssertionError("the prompt must not be shown")


def run(seed, argv, clients=None, client_factory=None, input_fn=refuse_input):
    out, err = io.StringIO(), io.StringIO()
    code = seed.main(
        argv,
        client_factory=client_factory,
        clients=clients,
        input_fn=input_fn,
        out=out,
        err=err,
        sleep=no_sleep,
        now=NOW,
    )
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def pwfile(tmp_path):
    return tmp_path / "pw" / "test-users.txt"


@pytest.fixture
def clients(live):
    return live[0]


@pytest.fixture
def pool_id(live):
    return live[1]


@pytest.fixture
def app_id(live):
    return live[2]


@pytest.fixture
def cog(live):
    return live[0]["cognito-idp"]._client


@pytest.fixture
def db(live):
    return live[0]["dynamodb"]._client


def seed_args(pwfile, *extra):
    return ["--yes", "--passwords-file", str(pwfile), *extra]


def scan(client, table=TABLE):
    items, kwargs = [], {"TableName": table, "ConsistentRead": True}
    while True:
        response = client.scan(**kwargs)
        items.extend(response["Items"])
        if "LastEvaluatedKey" not in response:
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def user_exists(cog, pool_id, email) -> bool:
    try:
        cog.admin_get_user(UserPoolId=pool_id, Username=email)
        return True
    except cog.exceptions.UserNotFoundException:
        return False


def attrs(cog, pool_id, email) -> dict:
    response = cog.admin_get_user(UserPoolId=pool_id, Username=email)
    return {a["Name"]: a["Value"] for a in response["UserAttributes"]}


def groups(cog, pool_id, email) -> set:
    response = cog.admin_list_groups_for_user(UserPoolId=pool_id, Username=email)
    return {g["GroupName"] for g in response["Groups"]}


def signs_in(cog, pool_id, app_id, email, password) -> bool:
    try:
        cog.admin_initiate_auth(
            UserPoolId=pool_id,
            ClientId=app_id,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": password},
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NotAuthorizedException":
            return False
        raise


def make_user(cog, pool_id, email, name, group_names, password=KNOWN_PW):
    cog.admin_create_user(
        UserPoolId=pool_id,
        Username=email,
        UserAttributes=[
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
            {"Name": "name", "Value": name},
        ],
        MessageAction="SUPPRESS",
    )
    cog.admin_set_user_password(
        UserPoolId=pool_id, Username=email, Password=password, Permanent=True
    )
    for group in group_names:
        cog.admin_add_user_to_group(UserPoolId=pool_id, Username=email, GroupName=group)


def password_lines(path) -> dict:
    lines = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#"):
            email, group, password = line.split()
            lines[email] = (group, password)
    return lines


def write_ops(*recorders) -> list[str]:
    return [op for r in recorders for op in r.ops() if op in WRITE_OPS]


def all_clients_untouched_by_writes(clients) -> bool:
    return not write_ops(*clients.values())


class FactorySpy:
    def __init__(self):
        self.calls = []

    def __call__(self, service, local=False):
        self.calls.append((service, local))
        raise AssertionError("no AWS client may be created")


# ---- criterion 3: validation creates no client and writes nothing ----


def bad_seed_file(seed, tmp_path, mutate):
    data = json.loads((REPO_ROOT / "scripts" / "seed-data.json").read_text())
    mutate(data)
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (
            lambda d: d["residents"][4].__setitem__("resident_id", "res-0004"),
            "res-0004",
        ),
        (lambda d: d["users"][0].__setitem__("password", "x"), "password"),
        (
            lambda d: d["links"].append(
                {"user_id": "test-ghost", "resident_id": "res-0001"}
            ),
            "test-ghost",
        ),
        (
            lambda d: d["links"].append(
                {"user_id": "test-family", "resident_id": "res-0404"}
            ),
            "res-0404",
        ),
        (lambda d: d.__setitem__("devices", d["devices"][:-1]), "bedroom-0020"),
    ],
    ids=[
        "dup-resident",
        "password",
        "unknown-user",
        "unknown-resident",
        "missing-device",
    ],
)
@pytest.mark.parametrize("mode", ["live", "local", "delete"])
def test_invalid_seed_exits_2_before_any_client(
    seed, tmp_path, pwfile, mutate, needle, mode
):
    path = bad_seed_file(seed, tmp_path, mutate)
    factory = FactorySpy()
    extra = {"live": [], "local": ["--local"], "delete": ["--delete"]}[mode]
    code, _out, err = run(
        seed,
        seed_args(pwfile, "--seed-file", str(path), *extra),
        client_factory=factory,
    )
    assert code == 2
    assert needle in err
    assert "nothing was written" in err
    assert factory.calls == []
    assert not pwfile.parent.exists()


def test_invalid_seed_with_injected_clients_makes_no_call(
    seed, tmp_path, pwfile, clients
):
    path = bad_seed_file(
        seed, tmp_path, lambda d: d["users"][1].__setitem__("password", "x")
    )
    code, _, _ = run(seed, seed_args(pwfile, "--seed-file", str(path)), clients=clients)
    assert code == 2
    assert all(r.calls == [] for r in clients.values())


def test_devices_file_mismatch_exits_2(seed, tmp_path, pwfile):
    devices = json.loads(
        (REPO_ROOT / "infra" / "devices" / "devices.json").read_text()
    )["devices"]
    devices.append({"id": "wearable-0021", "type": "wearable", "canary": False})
    path = write_devices_file(tmp_path / "devices.json", devices)
    factory = FactorySpy()
    code, _, err = run(
        seed, seed_args(pwfile, "--devices-file", str(path)), client_factory=factory
    )
    assert code == 2
    assert "wearable-0021" in err
    assert factory.calls == []


@pytest.mark.parametrize("content", ["{not json", "[]", ""])
def test_unreadable_seed_file_exits_2(seed, tmp_path, pwfile, content):
    path = tmp_path / "seed.json"
    path.write_text(content, encoding="utf-8")
    factory = FactorySpy()
    code, _, err = run(
        seed, seed_args(pwfile, "--seed-file", str(path)), client_factory=factory
    )
    assert code == 2
    assert "validation error" in err
    assert factory.calls == []


def test_missing_seed_file_exits_2(seed, tmp_path, pwfile):
    factory = FactorySpy()
    code, _, _ = run(
        seed,
        seed_args(pwfile, "--seed-file", str(tmp_path / "nope.json")),
        client_factory=factory,
    )
    assert code == 2
    assert factory.calls == []


def test_unhashable_link_id_exits_2_not_a_crash(seed, tmp_path, pwfile):
    path = bad_seed_file(
        seed,
        tmp_path,
        lambda d: d["links"].append(
            {"user_id": ["test-family"], "resident_id": "res-0001"}
        ),
    )
    factory = FactorySpy()
    code, _, _err = run(
        seed, seed_args(pwfile, "--seed-file", str(path)), client_factory=factory
    )
    assert code == 2
    assert factory.calls == []


# ---- criterion 17: flag combinations ----


@pytest.mark.parametrize(
    "flags",
    [
        ["--local", "--user-pool-id", "ap-southeast-1_abc123"],
        ["--local", "--reset-passwords"],
        ["--delete", "--reset-passwords"],
        ["--user-pool-id", "us-east-1_abc123"],
        ["--user-pool-id", "ap-southeast-1_"],
        ["--no-such-flag"],
    ],
)
def test_bad_flags_are_usage_errors(seed, pwfile, flags):
    factory = FactorySpy()
    code, _, err = run(seed, seed_args(pwfile, *flags), client_factory=factory)
    assert code == 2
    assert err
    assert factory.calls == []
    assert not pwfile.parent.exists()


def test_help_hides_the_test_only_flags(seed):
    code, out, _ = run(seed, ["--help"], client_factory=FactorySpy())
    assert code == 0
    assert "--seed-file" not in out and "--devices-file" not in out
    assert "--passwords-file" in out


# ---- criterion 13: passwords file path rule through main ----


@pytest.mark.parametrize(
    "relative",
    ["scripts/tests/pw-must-not-exist.txt", "pw-must-not-exist.txt", "infra/pw.txt"],
)
def test_passwords_file_in_repo_outside_secrets_exits_2(seed, relative):
    path = REPO_ROOT / relative
    factory = FactorySpy()
    code, _, err = run(
        seed, ["--yes", "--passwords-file", str(path)], client_factory=factory
    )
    assert code == 2
    assert "secrets/" in err
    assert factory.calls == []
    assert not path.exists()


def test_relative_passwords_file_in_repo_exits_2(seed, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    factory = FactorySpy()
    code, _, _ = run(
        seed, ["--yes", "--passwords-file", "pw.txt"], client_factory=factory
    )
    assert code == 2
    assert not (REPO_ROOT / "pw.txt").exists()


def test_passwords_file_in_repo_outside_secrets_refused_for_delete(seed):
    factory = FactorySpy()
    code, _, _ = run(
        seed,
        ["--delete", "--yes", "--passwords-file", str(REPO_ROOT / "infra" / "pw.txt")],
        client_factory=factory,
    )
    assert code == 2
    assert factory.calls == []


# ---- criteria 9-11: a fresh live seed ----


def test_fresh_seed_creates_four_users_each_in_one_group(
    seed, clients, pool_id, cog, pwfile
):
    code, out, err = run(seed, seed_args(pwfile), clients=clients)
    assert code == 0, err
    for user_id, email in EMAILS.items():
        assert groups(cog, pool_id, email) == {GROUP_OF[user_id]}
        user_attrs = attrs(cog, pool_id, email)
        assert user_attrs["email"] == email
        assert user_attrs["email_verified"] == "true"
        assert user_attrs["name"] == "Test " + user_id.split("-")[1].title()
        status = cog.admin_get_user(UserPoolId=pool_id, Username=email)["UserStatus"]
        assert status == "CONFIRMED"
        assert f"cognito: {user_id} created" in out


def test_users_are_created_silently_then_password_then_group(seed, clients, pwfile):
    run(seed, seed_args(pwfile), clients=clients)
    calls = clients["cognito-idp"].calls
    creates = [kw for op, kw in calls if op == "admin_create_user"]
    assert len(creates) == 4
    assert all(kw["MessageAction"] == "SUPPRESS" for kw in creates)
    for email in EMAILS.values():
        ops = [
            op
            for op, kw in calls
            if kw.get("Username") == email
            and op
            in {
                "admin_create_user",
                "admin_set_user_password",
                "admin_add_user_to_group",
            }
        ]
        assert ops == [
            "admin_create_user",
            "admin_set_user_password",
            "admin_add_user_to_group",
        ]
    sets = [kw for op, kw in calls if op == "admin_set_user_password"]
    assert all(kw["Permanent"] is True for kw in sets)


def test_fresh_seed_passwords_file_has_four_working_passwords(
    seed, clients, pool_id, app_id, cog, pwfile
):
    code, out, _err = run(seed, seed_args(pwfile), clients=clients)
    assert code == 0
    lines = password_lines(pwfile)
    assert set(lines) == set(EMAILS.values())
    for user_id, email in EMAILS.items():
        group, password = lines[email]
        assert group == GROUP_OF[user_id]
        assert len(password) == 20
        assert signs_in(cog, pool_id, app_id, email, password)
    assert f"passwords: 4 written to {pwfile}" in out
    assert oct(os.stat(pwfile).st_mode & 0o777) == oct(0o600)
    assert oct(os.stat(pwfile.parent).st_mode & 0o777) == oct(0o700)


def test_no_password_is_printed(seed, clients, pwfile, capsys):
    code, out, err = run(seed, seed_args(pwfile), clients=clients)
    assert code == 0
    captured = capsys.readouterr()
    for _, password in password_lines(pwfile).values():
        for text in (out, err, captured.out, captured.err):
            assert password not in text


def test_links_use_the_cognito_subs(seed, clients, pool_id, cog, db, pwfile):
    run(seed, seed_args(pwfile), clients=clients)
    family_sub = attrs(cog, pool_id, EMAILS["test-family"])["sub"]
    caregiver_sub = attrs(cog, pool_id, EMAILS["test-caregiver"])["sub"]
    links = {
        (i["pk"]["S"], i["sk"]["S"])
        for i in scan(db)
        if i["pk"]["S"].startswith("USER#")
    }
    assert links == {
        (f"USER#{family_sub}", "RESIDENT#res-0002"),
        (f"USER#{family_sub}", "RESIDENT#res-0005"),
        (f"USER#{caregiver_sub}", "RESIDENT#res-0001"),
        (f"USER#{caregiver_sub}", "RESIDENT#res-0002"),
        (f"USER#{caregiver_sub}", "RESIDENT#res-0003"),
    }
    assert len(scan(db)) == 85


# ---- criterion 9: finding the pool ----


def test_no_pool_exits_1_before_writing(seed, clients, pool_id, cog, pwfile):
    cog.delete_user_pool(UserPoolId=pool_id)
    code, _out, err = run(seed, seed_args(pwfile), clients=clients)
    assert code == 1
    assert "silvercare-users" in err and "found 0" in err
    assert all_clients_untouched_by_writes(clients)
    assert not pwfile.parent.exists()


def test_two_pools_with_the_name_exits_1_before_writing(seed, clients, cog, pwfile):
    cog.create_user_pool(PoolName="silvercare-users")
    code, _, err = run(seed, ["--passwords-file", str(pwfile)], clients=clients)
    assert code == 1
    assert "found 2" in err
    assert all_clients_untouched_by_writes(clients)


def test_other_pools_are_ignored(seed, clients, cog, pwfile):
    cog.create_user_pool(PoolName="silvercare-users-old")
    cog.create_user_pool(PoolName="another-pool")
    code, _, _ = run(seed, seed_args(pwfile), clients=clients)
    assert code == 0


def test_user_pool_id_flag_skips_lookup(seed, clients, pool_id, pwfile):
    code, out, _ = run(
        seed, seed_args(pwfile, "--user-pool-id", pool_id), clients=clients
    )
    assert code == 0
    assert "list_user_pools" not in clients["cognito-idp"].ops()
    assert f"user pool: {pool_id}" in out


def test_find_user_pool_follows_pagination(seed):
    class Pages:
        def __init__(self):
            self.tokens = []

        def list_user_pools(self, MaxResults, NextToken=None):
            self.tokens.append(NextToken)
            pages = {
                None: ([{"Id": "ap-southeast-1_a", "Name": "x"}], "t1"),
                "t1": ([{"Id": "ap-southeast-1_b", "Name": "y"}], "t2"),
                "t2": ([{"Id": "ap-southeast-1_c", "Name": "silvercare-users"}], None),
            }
            pools, token = pages[NextToken]
            response = {"UserPools": pools}
            if token:
                response["NextToken"] = token
            return response

    client = Pages()
    assert seed.find_user_pool(client) == "ap-southeast-1_c"
    assert client.tokens == [None, "t1", "t2"]


def test_find_user_pool_counts_duplicates_across_pages(seed):
    class Pages:
        def list_user_pools(self, MaxResults, NextToken=None):
            if NextToken is None:
                return {
                    "UserPools": [{"Id": "a", "Name": "silvercare-users"}],
                    "NextToken": "n",
                }
            return {"UserPools": [{"Id": "b", "Name": "silvercare-users"}]}

    with pytest.raises(seed.SeedError):
        seed.find_user_pool(Pages())


# ---- criterion 18: the confirmation prompt ----


def test_prompt_shows_account_region_pool_and_plan(seed, clients, pool_id, pwfile):
    shown = {}

    def answer(prompt):
        shown["prompt"] = prompt
        shown["writes_before"] = write_ops(*clients.values())
        return "seed"

    code, out, _ = run(
        seed, ["--passwords-file", str(pwfile)], clients=clients, input_fn=answer
    )
    assert code == 0
    assert "seed" in shown["prompt"]
    assert shown["writes_before"] == []
    assert "account: 123456789012" in out
    assert "region: ap-southeast-1" in out
    assert f"user pool: {pool_id}" in out
    assert "plan:" in out


@pytest.mark.parametrize(
    "answer", ["", "y", "yes", "Seed", "SEED", "delete", "seed please", "no", "seeds"]
)
def test_prompt_refuses_anything_but_seed(
    seed, clients, pool_id, cog, db, pwfile, answer
):
    code, out, _ = run(
        seed,
        ["--passwords-file", str(pwfile)],
        clients=clients,
        input_fn=lambda p: answer,
    )
    assert code == 1
    assert "nothing was written" in out
    assert all_clients_untouched_by_writes(clients)
    assert scan(db) == []
    assert not any(user_exists(cog, pool_id, e) for e in EMAILS.values())
    assert not pwfile.parent.exists()


def test_prompt_end_of_input_aborts(seed, clients, db, pwfile):
    def eof(prompt):
        raise EOFError

    code, out, _ = run(
        seed, ["--passwords-file", str(pwfile)], clients=clients, input_fn=eof
    )
    assert code == 1
    assert "nothing was written" in out
    assert all_clients_untouched_by_writes(clients)
    assert not pwfile.parent.exists()


@pytest.mark.parametrize("answer", ["seed", "Delete", "", "yes"])
def test_delete_prompt_refuses_anything_but_delete(
    seed, clients, pool_id, cog, db, pwfile, answer
):
    assert run(seed, seed_args(pwfile), clients=clients)[0] == 0
    before = pwfile.read_text()
    for r in clients.values():
        r.calls.clear()
    code, out, _ = run(
        seed,
        ["--delete", "--passwords-file", str(pwfile)],
        clients=clients,
        input_fn=lambda p: answer,
    )
    assert code == 1
    assert "nothing was written" in out
    assert all_clients_untouched_by_writes(clients)
    assert len(scan(db)) == 85
    assert all(user_exists(cog, pool_id, e) for e in EMAILS.values())
    assert pwfile.read_text() == before


def test_delete_prompt_accepts_delete(seed, clients, db, pwfile):
    run(seed, seed_args(pwfile), clients=clients)
    code, _, _ = run(
        seed,
        ["--delete", "--passwords-file", str(pwfile)],
        clients=clients,
        input_fn=lambda p: "delete",
    )
    assert code == 0
    assert scan(db) == []


def test_yes_skips_the_prompt(seed, clients, pwfile):
    code, _, _ = run(seed, seed_args(pwfile), clients=clients, input_fn=refuse_input)
    assert code == 0


# ---- criterion 12 and 15: re-runs and existing users ----


def test_rerun_is_idempotent(seed, clients, pool_id, app_id, cog, db, pwfile):
    assert run(seed, seed_args(pwfile), clients=clients)[0] == 0
    first_file = pwfile.read_text()
    first_items = {(i["pk"]["S"], i["sk"]["S"]) for i in scan(db)}
    subs = {e: attrs(cog, pool_id, e)["sub"] for e in EMAILS.values()}
    clients["cognito-idp"].calls.clear()
    code, out, _ = run(seed, seed_args(pwfile), clients=clients)
    assert code == 0
    assert pwfile.read_text() == first_file
    assert {(i["pk"]["S"], i["sk"]["S"]) for i in scan(db)} == first_items
    assert {e: attrs(cog, pool_id, e)["sub"] for e in EMAILS.values()} == subs
    for user_id in EMAILS:
        assert f"cognito: {user_id} exists, kept" in out
    ops = clients["cognito-idp"].ops()
    assert "admin_create_user" not in ops and "admin_set_user_password" not in ops
    for email, (_, password) in password_lines(pwfile).items():
        assert signs_in(cog, pool_id, app_id, email, password)
    assert "passwords:" not in out


@pytest.fixture
def existing_nurse(cog, pool_id, pwfile):
    """test-nurse made earlier, renamed, in extra groups; its line already saved."""
    cog.create_group(UserPoolId=pool_id, GroupName="beta-testers")
    make_user(
        cog,
        pool_id,
        EMAILS["test-nurse"],
        "Test Nurse Renamed",
        ["nurse", "doctor", "family", "beta-testers"],
    )
    pwfile.parent.mkdir(mode=0o700)
    pwfile.write_text(
        f"# synthetic test users\n{HANDMADE} doctor Hand-Made-Pw-01\n"
        f"{EMAILS['test-nurse']} nurse {KNOWN_PW}\n"
    )
    return EMAILS["test-nurse"]


def test_existing_user_keeps_password_without_reset(
    seed, clients, pool_id, app_id, cog, pwfile, existing_nurse
):
    code, out, _ = run(seed, seed_args(pwfile), clients=clients)
    assert code == 0
    assert signs_in(cog, pool_id, app_id, existing_nurse, KNOWN_PW)
    assert "cognito: test-nurse exists, kept" in out
    sets = [
        kw["Username"]
        for op, kw in clients["cognito-idp"].calls
        if op == "admin_set_user_password"
    ]
    assert existing_nurse not in sets
    assert f"passwords: 3 written to {pwfile}" in out


def test_existing_users_line_and_other_lines_are_kept(
    seed, clients, pwfile, existing_nurse
):
    run(seed, seed_args(pwfile), clients=clients)
    text = pwfile.read_text().splitlines()
    assert text[:3] == [
        "# synthetic test users",
        f"{HANDMADE} doctor Hand-Made-Pw-01",
        f"{existing_nurse} nurse {KNOWN_PW}",
    ]
    assert len(password_lines(pwfile)) == 5


def test_existing_user_is_moved_to_its_group_only(
    seed, clients, pool_id, cog, pwfile, existing_nurse
):
    run(seed, seed_args(pwfile), clients=clients)
    assert groups(cog, pool_id, existing_nurse) == {"nurse", "beta-testers"}


def test_existing_user_attributes_are_not_changed(
    seed, clients, pool_id, cog, pwfile, existing_nurse
):
    run(seed, seed_args(pwfile), clients=clients)
    assert attrs(cog, pool_id, existing_nurse)["name"] == "Test Nurse Renamed"
    assert "admin_update_user_attributes" not in clients["cognito-idp"].ops()


def test_reset_passwords_gives_all_four_new_passwords(
    seed, clients, pool_id, app_id, cog, pwfile, existing_nurse
):
    assert run(seed, seed_args(pwfile), clients=clients)[0] == 0
    before = password_lines(pwfile)
    code, out, _ = run(seed, seed_args(pwfile, "--reset-passwords"), clients=clients)
    assert code == 0
    after = password_lines(pwfile)
    assert not signs_in(cog, pool_id, app_id, existing_nurse, KNOWN_PW)
    for email in EMAILS.values():
        assert after[email][1] != before[email][1]
        assert signs_in(cog, pool_id, app_id, email, after[email][1])
    assert after[HANDMADE] == before[HANDMADE]
    assert f"passwords: 4 written to {pwfile}" in out
    assert out.count("exists, password reset") == 4


# ---- hand-made users ----


@pytest.fixture
def handmade(cog, pool_id):
    make_user(cog, pool_id, HANDMADE, "Hand Made Doctor", ["doctor"])
    return HANDMADE


@pytest.mark.parametrize(
    "steps",
    [
        [[]],
        [[], ["--reset-passwords"]],
        [[], ["--delete"]],
        [["--delete"]],
    ],
    ids=["seed", "seed-reset", "seed-delete", "delete-only"],
)
def test_handmade_user_is_never_read_or_changed(
    seed, clients, pool_id, app_id, cog, pwfile, handmade, steps
):
    before = attrs(cog, pool_id, handmade)
    for extra in steps:
        assert run(seed, seed_args(pwfile, *extra), clients=clients)[0] == 0
    touched = [
        op
        for op, kw in clients["cognito-idp"].calls
        if kw.get("Username", "").lower() == handmade
    ]
    assert touched == []
    assert attrs(cog, pool_id, handmade) == before
    assert groups(cog, pool_id, handmade) == {"doctor"}
    assert signs_in(cog, pool_id, app_id, handmade, KNOWN_PW)


# ---- no passwords on failures ----


class LeakyCognito(Recorder):
    """Fails one call with an error message that contains every password set so far."""

    def __init__(self, client, op, number, code="InternalErrorException"):
        super().__init__(client)
        self.op, self.number, self.code = op, number, code
        self.passwords = []

    def __getattr__(self, name):
        target = super().__getattr__(name)
        if name != self.op and name != "admin_set_user_password":
            return target

        def call(*args, **kwargs):
            if name == "admin_set_user_password":
                self.passwords.append(kwargs["Password"])
            if name == self.op and self.ops().count(name) + 1 == self.number:
                self.calls.append((name, kwargs))
                raise ClientError(
                    {
                        "Error": {
                            "Code": self.code,
                            "Message": "leak " + " ".join(self.passwords),
                        }
                    },
                    "".join(p.title() for p in name.split("_")),
                )
            return target(*args, **kwargs)

        return call


@pytest.mark.parametrize(
    ("op", "number", "operation"),
    [
        ("admin_add_user_to_group", 3, "AdminAddUserToGroup"),
        ("admin_set_user_password", 2, "AdminSetUserPassword"),
        ("admin_get_user", 5, "AdminGetUser"),
    ],
)
def test_cognito_failure_midway_prints_no_password(
    seed, clients, db, pwfile, capsys, op, number, operation
):
    leaky = LeakyCognito(clients["cognito-idp"]._client, op, number)
    clients["cognito-idp"] = leaky
    code, out, err = run(seed, seed_args(pwfile), clients=clients)
    assert code == 1
    captured = capsys.readouterr()
    assert leaky.passwords
    for password in leaky.passwords:
        for text in (out, err, captured.out, captured.err):
            assert password not in text
    assert "leak" not in err
    assert re.search(rf"error: {operation} failed \(InternalErrorException\)", err)
    assert scan(db) == []  # links need every sub: no registry write after a failure


def test_cognito_failure_still_saves_passwords_already_set(
    seed, clients, pool_id, app_id, cog, pwfile
):
    leaky = LeakyCognito(clients["cognito-idp"]._client, "admin_add_user_to_group", 3)
    clients["cognito-idp"] = leaky
    code, _out, _ = run(seed, seed_args(pwfile), clients=clients)
    assert code == 1
    lines = password_lines(pwfile)
    # doctor and nurse finished; caregiver's password was set before its group failed.
    assert set(lines) == {
        EMAILS["test-doctor"],
        EMAILS["test-nurse"],
        EMAILS["test-caregiver"],
    }
    for email, (_, password) in lines.items():
        assert signs_in(cog, pool_id, app_id, email, password)


def test_passwords_file_write_failure_prints_no_password(
    seed, clients, db, pwfile, monkeypatch
):
    pwfile.parent.mkdir()
    pwfile.write_text(f"{HANDMADE} doctor Hand-Made-Pw-01\n")
    set_passwords = []
    real = clients["cognito-idp"]

    class Spy(Recorder):
        def __getattr__(self, name):
            target = super().__getattr__(name)
            if name == "admin_set_user_password":

                def call(**kwargs):
                    set_passwords.append(kwargs["Password"])
                    return target(**kwargs)

                return call
            return target

    clients["cognito-idp"] = Spy(real._client)

    def broken_replace(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    code, out, err = run(seed, seed_args(pwfile), clients=clients)
    assert code == 1
    assert "could not write the passwords file" in err
    assert len(set_passwords) == 4
    for password in set_passwords:
        assert password not in out and password not in err
    assert pwfile.read_text() == f"{HANDMADE} doctor Hand-Made-Pw-01\n"
    assert sorted(p.name for p in pwfile.parent.iterdir()) == ["test-users.txt"]
    assert scan(db) == []


# ---- criterion 16: delete in the live account ----


@pytest.fixture
def seeded_live(seed, clients, pwfile):
    assert run(seed, seed_args(pwfile), clients=clients)[0] == 0
    return pwfile


def seed_links(db):
    return [i for i in scan(db) if i.get("linked_by", {}).get("S") == "seed"]


def test_delete_removes_users_items_and_password_lines(
    seed, clients, pool_id, cog, db, seeded_live
):
    with seeded_live.open("a") as handle:
        handle.write(f"{HANDMADE} doctor Hand-Made-Pw-01\n")
    code, out, _ = run(seed, seed_args(seeded_live, "--delete"), clients=clients)
    assert code == 0
    assert scan(db) == []
    assert not any(user_exists(cog, pool_id, e) for e in EMAILS.values())
    assert seeded_live.read_text() == f"{HANDMADE} doctor Hand-Made-Pw-01\n"
    assert "registry: 5 seed links deleted" in out
    assert "passwords: 4 lines removed" in out


def test_delete_removes_passwords_file_when_empty(seed, clients, seeded_live):
    run(seed, seed_args(seeded_live, "--delete"), clients=clients)
    assert not seeded_live.exists()


def test_delete_after_user_deleted_by_hand_leaves_no_seed_links(
    seed, clients, pool_id, cog, db, seeded_live
):
    cog.admin_delete_user(UserPoolId=pool_id, Username=EMAILS["test-family"])
    code, out, err = run(seed, seed_args(seeded_live, "--delete"), clients=clients)
    assert code == 0, err
    assert seed_links(db) == []
    assert scan(db) == []
    assert "cognito: test-family already gone" in out


def test_delete_after_user_recreated_by_hand_leaves_no_seed_links(
    seed, clients, pool_id, cog, db, seeded_live
):
    # The family user is deleted and re-seeded: its old links keep the old sub.
    cog.admin_delete_user(UserPoolId=pool_id, Username=EMAILS["test-family"])
    assert run(seed, seed_args(seeded_live), clients=clients)[0] == 0
    assert len(seed_links(db)) == 7
    code, _, _ = run(seed, seed_args(seeded_live, "--delete"), clients=clients)
    assert code == 0
    assert seed_links(db) == []


def test_delete_on_partly_deleted_seed_succeeds(
    seed, clients, pool_id, cog, db, seeded_live
):
    for item in scan(db)[::3]:
        db.delete_item(TableName=TABLE, Key={"pk": item["pk"], "sk": item["sk"]})
    cog.admin_delete_user(UserPoolId=pool_id, Username=EMAILS["test-doctor"])
    seeded_live.unlink()
    code, out, err = run(seed, seed_args(seeded_live, "--delete"), clients=clients)
    assert code == 0, err
    assert scan(db) == []
    assert not any(user_exists(cog, pool_id, e) for e in EMAILS.values())
    assert "passwords: 0 lines removed" in out
    assert not seeded_live.exists()


def test_delete_twice_succeeds(seed, clients, db, seeded_live):
    assert run(seed, seed_args(seeded_live, "--delete"), clients=clients)[0] == 0
    code, out, _ = run(seed, seed_args(seeded_live, "--delete"), clients=clients)
    assert code == 0
    assert (
        len(re.findall(r"^cognito: test-[a-z]+ already gone$", out, re.MULTILINE)) == 4
    )


def test_delete_leaves_password_file_without_matching_lines_alone(
    seed, clients, pwfile
):
    pwfile.parent.mkdir()
    pwfile.write_text(f"{HANDMADE} doctor Hand-Made-Pw-01\n")
    os.chmod(pwfile, 0o644)
    code, _, _ = run(seed, seed_args(pwfile, "--delete"), clients=clients)
    assert code == 0
    assert pwfile.read_text() == f"{HANDMADE} doctor Hand-Made-Pw-01\n"
    assert os.stat(pwfile).st_mode & 0o777 == 0o644


def test_delete_leaves_foreign_items_and_links(seed, clients, db, seeded_live):
    foreign = [
        {
            "pk": {"S": "USER#handmade-sub"},
            "sk": {"S": "RESIDENT#res-0002"},
            "linked_by": {"S": "admin"},
            "relationship": {"S": "family"},
            "gsi1_pk": {"S": "RESIDENT#res-0002"},
            "gsi1_sk": {"S": "USER#handmade-sub"},
        },
        {
            "pk": {"S": "RESIDENT#res-9001"},
            "sk": {"S": "PROFILE"},
            "entity_type": {"S": "RESIDENT"},
        },
        {"pk": {"S": "DEVICE#bedroom-9001"}, "sk": {"S": "ASSIGNMENT"}},
    ]
    for item in foreign:
        db.put_item(TableName=TABLE, Item=item)
    assert run(seed, seed_args(seeded_live, "--delete"), clients=clients)[0] == 0
    assert sorted(scan(db), key=str) == sorted(foreign, key=str)


# ---- criterion 22: output and exit codes ----


def test_output_has_counts_and_ids_only(seed, clients, pwfile):
    code, out, err = run(seed, seed_args(pwfile), clients=clients)
    assert code == 0
    assert err == ""
    assert "registry: 85 items written" in out
    for marker in CONTENT_MARKERS:
        assert marker not in out
    for line in out.splitlines():
        assert re.match(
            r"^(account|region|user pool|plan|cognito|passwords|registry): ", line
        ), line


def test_delete_output_has_no_item_contents(seed, clients, seeded_live):
    code, out, err = run(seed, seed_args(seeded_live, "--delete"), clients=clients)
    assert code == 0
    for marker in CONTENT_MARKERS:
        assert marker not in out + err


def test_dynamodb_error_shows_operation_and_code_only(seed, clients, pwfile):
    clients["dynamodb"].fail = {
        "batch_write_item": (
            2,
            "ValidationException",
            "Item res-0003 Test Resident C bad",
        )
    }
    code, _out, err = run(seed, seed_args(pwfile), clients=clients)
    assert code == 1
    assert "error: BatchWriteItem failed (ValidationException)" in err
    assert "Test Resident" not in err and "res-0003" not in err


def test_missing_credentials_exits_1(seed, pwfile):
    class NoCreds:
        def get_caller_identity(self):
            raise NoCredentialsError()

    code, _, err = run(
        seed, seed_args(pwfile), client_factory=lambda service, local=False: NoCreds()
    )
    assert code == 1
    assert "NoCredentialsError" in err


def test_sts_access_denied_exits_1_before_writing(seed, clients, pwfile):
    clients["sts"].fail = {
        "get_caller_identity": (1, "AccessDenied", "user arn:aws:iam::1:x")
    }
    code, _, err = run(seed, seed_args(pwfile), clients=clients)
    assert code == 1
    assert "GetCallerIdentity failed (AccessDenied)" in err
    assert "arn:" not in err
    assert all_clients_untouched_by_writes(clients)


# ---- criteria 14 and 19: --local ----


def local_factory(client):
    calls = []

    def factory(service, local=False):
        calls.append((service, local))
        assert service == "dynamodb" and local is True
        return client

    factory.calls = calls
    return factory


def test_local_skips_cognito_sts_and_prompt(seed, moto, pwfile):
    client = boto_client("dynamodb")
    factory = local_factory(client)
    code, out, err = run(
        seed, ["--local"], client_factory=factory, input_fn=refuse_input
    )
    assert code == 0, err
    assert factory.calls == [("dynamodb", True)]
    assert f"registry: table {TABLE} created" in out
    assert "registry: 85 items written" in out
    assert len(scan(client)) == 85


def test_local_links_use_fixed_synthetic_subs(seed, moto):
    client = boto_client("dynamodb")
    run(seed, ["--local"], client_factory=local_factory(client))
    users = {i["pk"]["S"] for i in scan(client) if i["pk"]["S"].startswith("USER#")}
    assert users == {
        "USER#00000000-0000-4000-8000-000000000003",
        "USER#00000000-0000-4000-8000-000000000004",
    }


def test_local_writes_no_passwords_file(seed, moto, tmp_path):
    client = boto_client("dynamodb")
    run(seed, ["--local"], client_factory=local_factory(client))
    assert not seed.DEFAULT_PASSWORDS_FILE.parent.exists()


def test_local_rerun_and_delete(seed, moto):
    client = boto_client("dynamodb")
    factory = local_factory(client)
    assert run(seed, ["--local"], client_factory=factory)[0] == 0
    code, out, _ = run(seed, ["--local"], client_factory=factory)
    assert code == 0
    assert "created" not in out
    assert len(scan(client)) == 85
    code, out, _ = run(seed, ["--local", "--delete"], client_factory=factory)
    assert code == 0
    assert "registry: 5 seed links deleted" in out
    assert scan(client) == []


def test_local_with_injected_clients_uses_only_dynamodb(seed, ddb):
    rec = Recorder(ddb)
    code, _, _ = run(seed, ["--local"], clients={"dynamodb": rec})
    assert code == 0
    assert "create_table" not in rec.ops()


# ---- criterion 18/19/21: the real client factory ----


@pytest.mark.parametrize("service", ["dynamodb", "cognito-idp", "sts"])
def test_region_is_fixed_whatever_the_environment_says(
    seed, aws_env, monkeypatch, service
):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    client = seed.make_client(service)
    assert client.meta.region_name == "ap-southeast-1"
    assert "ap-southeast-1" in client.meta.endpoint_url or service == "sts"
    assert client.meta.config.retries["mode"] == "adaptive"


def test_configured_endpoint_url_is_ignored_for_live(seed, aws_env, monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://somewhere-else.invalid:4566")
    client = seed.make_client("dynamodb")
    assert "somewhere-else" not in client.meta.endpoint_url


def test_local_client_uses_dynamodb_endpoint(seed, aws_env, monkeypatch):
    monkeypatch.setenv("DYNAMODB_ENDPOINT", "http://localhost:8123")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    client = seed.make_client("dynamodb", local=True)
    assert client.meta.endpoint_url == "http://localhost:8123"
    assert client.meta.region_name == "ap-southeast-1"
    assert client._request_signer._credentials.access_key == "local"
    assert client.meta.config.retries["mode"] == "adaptive"


def test_local_client_default_endpoint(seed, aws_env):
    client = seed.make_client("dynamodb", local=True)
    assert client.meta.endpoint_url == "http://dynamodb-local:8000"


def test_full_live_run_with_real_factory_lands_in_singapore(
    seed, aws_env, monkeypatch, pwfile
):
    from moto import mock_aws

    with mock_aws():
        sg_ddb = boto_client("dynamodb")
        sg_ddb.create_table(TableName=TABLE, **seed.REGISTRY_TABLE_DEFINITION)
        sg_cog = boto_client("cognito-idp")
        pool = sg_cog.create_user_pool(
            PoolName="silvercare-users", UsernameAttributes=["email"]
        )
        for group in GROUP_OF.values():
            sg_cog.create_group(UserPoolId=pool["UserPool"]["Id"], GroupName=group)
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
        monkeypatch.setenv("AWS_REGION", "us-west-2")
        code, _out, err = run(seed, seed_args(pwfile))
        assert code == 0, err
        assert len(scan(sg_ddb)) == 85
        assert boto_client("dynamodb", "us-west-2").list_tables()["TableNames"] == []
