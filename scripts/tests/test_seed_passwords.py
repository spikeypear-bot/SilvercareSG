"""Passwords: generation, the path rule, and the passwords file (criteria 11, 13, 16).

Every file lives under tmp_path. The repo-path tests only compute paths; conftest's
no_secrets_access fixture fails any test that touches the real secrets/ folder.
"""

from __future__ import annotations

import os
import stat
import string

import pytest
from helpers import REPO_ROOT

DOCTOR = "test-doctor@example.com"
NURSE = "test-nurse@example.com"
HANDMADE = "handmade-doctor@example.com"


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# ---- criterion 11: generate_password ----


def test_password_is_20_characters_with_every_class(seed):
    for _ in range(200):
        password = seed.generate_password()
        assert len(password) == 20
        assert any(c in string.ascii_lowercase for c in password)
        assert any(c in string.ascii_uppercase for c in password)
        assert any(c in string.digits for c in password)
        assert any(not c.isalnum() for c in password)
        assert not any(c.isspace() for c in password)


def test_passwords_differ(seed):
    assert len({seed.generate_password() for _ in range(50)}) == 50


def test_password_uses_the_secrets_module(seed, monkeypatch):
    calls = []
    real_choice = seed.secrets.choice

    class FakeSecrets:
        @staticmethod
        def choice(seq):
            calls.append(seq)
            return real_choice(seq)

    monkeypatch.setattr(seed, "secrets", FakeSecrets)
    seed.generate_password()
    assert len(calls) >= 20


def test_password_symbols_are_cognito_symbols(seed):
    # Cognito's allowed special characters (Cognito password policy docs).
    cognito_symbols = set("^$*.[]{}()?\"!@#%&/\\,><':;|_~`=+-")
    assert set(seed.PASSWORD_SYMBOLS) <= cognito_symbols


# ---- criterion 13: path rule ----


@pytest.fixture
def fake_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "secrets").mkdir(parents=True)
    (repo / "scripts").mkdir()
    return repo


def test_path_under_repo_secrets_is_allowed(seed, fake_repo):
    path = fake_repo / "secrets" / "test-users.txt"
    assert seed.check_passwords_path(path, repo_root=fake_repo) == path


def test_path_inside_repo_outside_secrets_is_refused(seed, fake_repo):
    with pytest.raises(ValueError):
        seed.check_passwords_path(fake_repo / "scripts" / "pw.txt", repo_root=fake_repo)


def test_path_at_repo_root_is_refused(seed, fake_repo):
    with pytest.raises(ValueError):
        seed.check_passwords_path(fake_repo / "pw.txt", repo_root=fake_repo)


def test_secrets_folder_itself_is_refused(seed, fake_repo):
    with pytest.raises(ValueError):
        seed.check_passwords_path(fake_repo / "secrets", repo_root=fake_repo)


def test_dotdot_escape_from_secrets_is_refused(seed, fake_repo):
    path = fake_repo / "secrets" / ".." / "scripts" / "pw.txt"
    with pytest.raises(ValueError):
        seed.check_passwords_path(path, repo_root=fake_repo)


def test_symlink_outside_repo_pointing_into_repo_is_refused(seed, fake_repo, tmp_path):
    link = tmp_path / "outside-link"
    link.symlink_to(fake_repo / "scripts")
    with pytest.raises(ValueError):
        seed.check_passwords_path(link / "pw.txt", repo_root=fake_repo)


def test_secrets_named_sibling_is_not_secrets(seed, fake_repo):
    with pytest.raises(ValueError):
        seed.check_passwords_path(
            fake_repo / "secrets-old" / "pw.txt", repo_root=fake_repo
        )


def test_path_outside_repo_is_allowed(seed, fake_repo, tmp_path):
    path = tmp_path / "elsewhere" / "pw.txt"
    assert seed.check_passwords_path(path, repo_root=fake_repo) == path


def test_relative_path_is_resolved_against_cwd(seed, fake_repo, monkeypatch):
    monkeypatch.chdir(fake_repo)
    with pytest.raises(ValueError):
        seed.check_passwords_path("pw.txt", repo_root=fake_repo)


def test_real_repo_path_outside_secrets_is_refused(seed):
    with pytest.raises(ValueError):
        seed.check_passwords_path(REPO_ROOT / "scripts" / "pw.txt")


def test_default_passwords_file_is_secrets_test_users(seed):
    assert (
        seed.SECRETS_DIR / "test-users.txt" == REPO_ROOT / "secrets" / "test-users.txt"
    )


# ---- criterion 13: writing ----


def test_new_file_has_mode_600_in_new_folder_with_mode_700(seed, tmp_path):
    path = tmp_path / "new-folder" / "test-users.txt"
    assert (
        seed.write_passwords_file(path, {DOCTOR: ("doctor", "Pw-1aaaaaaaaaaaaaaa")})
        == 1
    )
    assert mode(path) == 0o600
    assert mode(path.parent) == 0o700
    assert path.read_text() == f"{DOCTOR} doctor Pw-1aaaaaaaaaaaaaaa\n"


def test_existing_folder_mode_is_left_alone(seed, tmp_path):
    folder = tmp_path / "existing"
    folder.mkdir(mode=0o755)
    os.chmod(folder, 0o755)
    seed.write_passwords_file(folder / "pw.txt", {DOCTOR: ("doctor", "x")})
    assert mode(folder) == 0o755
    assert mode(folder / "pw.txt") == 0o600


def test_merge_keeps_other_lines_and_replaces_only_set_users(seed, tmp_path):
    path = tmp_path / "pw.txt"
    path.write_text(
        "# shared privately\n"
        f"{HANDMADE} doctor Hand-made-Pw-1\n"
        f"{DOCTOR} doctor Old-Doctor-Pw-1\n"
        f"{NURSE} nurse Old-Nurse-Pw-1\n"
    )
    seed.write_passwords_file(path, {DOCTOR: ("doctor", "New-Doctor-Pw-1")})
    assert path.read_text().splitlines() == [
        "# shared privately",
        f"{HANDMADE} doctor Hand-made-Pw-1",
        f"{DOCTOR} doctor New-Doctor-Pw-1",
        f"{NURSE} nurse Old-Nurse-Pw-1",
    ]


def test_merge_appends_new_users(seed, tmp_path):
    path = tmp_path / "pw.txt"
    path.write_text(f"{HANDMADE} doctor Hand-made-Pw-1\n")
    seed.write_passwords_file(path, {NURSE: ("nurse", "New-Nurse-Pw-1")})
    assert path.read_text().splitlines() == [
        f"{HANDMADE} doctor Hand-made-Pw-1",
        f"{NURSE} nurse New-Nurse-Pw-1",
    ]


def test_merge_collapses_duplicate_lines_of_a_set_user(seed, tmp_path):
    path = tmp_path / "pw.txt"
    path.write_text(f"{DOCTOR} doctor A\n{DOCTOR} doctor B\n")
    seed.write_passwords_file(path, {DOCTOR: ("doctor", "C")})
    assert path.read_text().splitlines() == [f"{DOCTOR} doctor C"]


def test_empty_entries_leave_file_untouched(seed, tmp_path):
    path = tmp_path / "pw.txt"
    path.write_text("keep me\n")
    os.chmod(path, 0o640)
    before = os.stat(path).st_mtime_ns
    assert seed.write_passwords_file(path, {}) == 0
    assert path.read_text() == "keep me\n"
    assert os.stat(path).st_mtime_ns == before


def test_empty_entries_create_no_file(seed, tmp_path):
    path = tmp_path / "missing" / "pw.txt"
    seed.write_passwords_file(path, {})
    assert not path.parent.exists()


def test_existing_file_becomes_mode_600(seed, tmp_path):
    path = tmp_path / "pw.txt"
    path.write_text(f"{HANDMADE} doctor Hand-made-Pw-1\n")
    os.chmod(path, 0o644)
    seed.write_passwords_file(path, {DOCTOR: ("doctor", "x")})
    assert mode(path) == 0o600


def test_temp_file_is_created_mode_600_in_same_folder(seed, tmp_path, monkeypatch):
    path = tmp_path / "pw.txt"
    seen = {}
    real_replace = os.replace

    def spy_replace(src, dst, *args, **kwargs):
        seen["src"] = os.fspath(src)
        seen["mode"] = mode(src)
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", spy_replace)
    seed.write_passwords_file(path, {DOCTOR: ("doctor", "x")})
    assert os.path.dirname(seen["src"]) == str(tmp_path)
    assert seen["mode"] == 0o600


def test_failed_replace_leaves_old_file_and_no_temp(seed, tmp_path, monkeypatch):
    path = tmp_path / "pw.txt"
    path.write_text(f"{DOCTOR} doctor Old-Doctor-Pw-1\n")

    def broken_replace(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        seed.write_passwords_file(path, {DOCTOR: ("doctor", "New-Doctor-Pw-1")})
    assert path.read_text() == f"{DOCTOR} doctor Old-Doctor-Pw-1\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pw.txt"]


def test_failed_write_mid_file_leaves_old_file_and_no_temp(seed, tmp_path, monkeypatch):
    path = tmp_path / "pw.txt"
    path.write_text(f"{DOCTOR} doctor Old-Doctor-Pw-1\n")

    def broken_fsync(fd):
        raise OSError("io error")

    monkeypatch.setattr(os, "fsync", broken_fsync)
    with pytest.raises(OSError):
        seed.write_passwords_file(path, {DOCTOR: ("doctor", "New-Doctor-Pw-1")})
    assert path.read_text() == f"{DOCTOR} doctor Old-Doctor-Pw-1\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pw.txt"]


# ---- criterion 16: removing lines ----


def test_remove_keeps_other_lines(seed, tmp_path):
    path = tmp_path / "pw.txt"
    path.write_text(f"{HANDMADE} doctor H\n{DOCTOR} doctor D\n{NURSE} nurse N\n")
    assert seed.remove_password_lines(path, [DOCTOR, NURSE]) == 2
    assert path.read_text() == f"{HANDMADE} doctor H\n"
    assert mode(path) == 0o600


def test_remove_deletes_file_when_no_user_lines_left(seed, tmp_path):
    path = tmp_path / "pw.txt"
    path.write_text(f"# header\n{DOCTOR} doctor D\n\n")
    seed.remove_password_lines(path, [DOCTOR])
    assert not path.exists()


def test_remove_on_missing_file_is_fine(seed, tmp_path):
    assert seed.remove_password_lines(tmp_path / "nope.txt", [DOCTOR]) == 0
    assert not (tmp_path / "nope.txt").exists()


def test_remove_with_no_matching_line_leaves_file_alone(seed, tmp_path):
    path = tmp_path / "pw.txt"
    path.write_text(f"{HANDMADE} doctor H\n")
    os.chmod(path, 0o644)
    before = os.stat(path).st_mtime_ns
    assert seed.remove_password_lines(path, [DOCTOR]) == 0
    assert path.read_text() == f"{HANDMADE} doctor H\n"
    assert mode(path) == 0o644
    assert os.stat(path).st_mtime_ns == before


def test_secrets_guard_recognises_secrets_paths():
    # Pure path check: never touches the real folder.
    from conftest import _in_secrets

    assert _in_secrets(REPO_ROOT / "secrets")
    assert _in_secrets(str(REPO_ROOT / "secrets" / "test-users.txt"))
    assert _in_secrets(REPO_ROOT / "scripts" / ".." / "secrets" / "x")
    assert not _in_secrets(REPO_ROOT / "secrets-old" / "x")
    assert not _in_secrets(REPO_ROOT / "scripts" / "seed.py")
    assert not _in_secrets(3)
