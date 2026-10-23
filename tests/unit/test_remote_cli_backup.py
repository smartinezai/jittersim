"""jittersim backup pull against a disposable HTTPS dashboard."""

import errno
import json
import os
import re
import stat
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim.cli import main
from tests.remote_support import TEST_TOKEN, make_certificate, run

pytestmark = pytest.mark.usefixtures("remote_environment")

BACKUP = b"SQLite format 3\x00" + b"\x01" * 200
NAME = re.compile(r"jittersim-backup-\d{8}T\d{6}Z\.db")


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_pull_saves_the_backup_in_a_private_folder(dashboard, capsys, tmp_path):
    dashboard.answer("GET", "/api/backup", BACKUP)
    folder = tmp_path / "saved" / "backups"
    code, out, err, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))
    assert code == 0
    assert err == ""
    (saved,) = list(folder.iterdir())
    assert NAME.fullmatch(saved.name)
    assert saved.read_bytes() == BACKUP
    assert _mode(saved) == 0o600
    assert _mode(folder) == 0o700
    assert str(saved) in out
    assert dashboard.requests[0]["authorization"] == "Bearer " + TEST_TOKEN
    assert dashboard.paths() == [("GET", "/api/backup")]


def test_pull_uses_a_folder_in_the_home_directory_by_default(dashboard, capsys, tmp_path):
    dashboard.answer("GET", "/api/backup", BACKUP)
    code, out, _, _ = run(dashboard, capsys, "backup", "pull")
    assert code == 0
    folder = tmp_path / "home" / "jittersim-backups"
    assert [item.read_bytes() for item in folder.iterdir()] == [BACKUP]
    assert _mode(folder) == 0o700
    assert str(folder) in out


def test_pull_json_names_the_file(dashboard, capsys, tmp_path):
    dashboard.answer("GET", "/api/backup", BACKUP)
    code, out, _, _ = run(dashboard, capsys, "backup", "pull", "--json",
                          "--directory", str(tmp_path / "out"))
    assert code == 0
    answer = json.loads(out)
    assert answer["bytes"] == len(BACKUP)
    saved = Path(answer["path"])
    assert saved.parent == (tmp_path / "out").resolve()
    assert saved.read_bytes() == BACKUP


def test_pull_never_replaces_a_file(dashboard, capsys, tmp_path):
    dashboard.answer("GET", "/api/backup", BACKUP)
    folder = tmp_path / "backups"
    folder.mkdir()
    taken = folder / "jittersim-backup-20261023T120000Z.db"
    taken.write_bytes(b"older backup")
    stub = types.SimpleNamespace(strftime=lambda *_: taken.name, gmtime=lambda: None)
    with patch("jittersim.remote_cli.time", stub):
        code, out, err, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))
    assert code == 1
    assert "already exists" in err
    assert taken.read_bytes() == b"older backup"
    assert list(folder.iterdir()) == [taken]


def test_a_failed_write_leaves_no_half_file(dashboard, capsys, tmp_path, monkeypatch):
    def full_disk(descriptor, mode):
        os.close(descriptor)
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("jittersim.remote_cli.os.fdopen", full_disk)
    dashboard.answer("GET", "/api/backup", BACKUP)
    folder = tmp_path / "backups"
    code, _, err, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))
    assert code == 1
    assert "The command failed" in err
    assert list(folder.iterdir()) == []


def test_a_refused_token_saves_nothing(dashboard, capsys, tmp_path):
    dashboard.answer("GET", "/api/backup", {"error": "valid bearer authentication is required"},
                     status=401)
    folder = tmp_path / "backups"
    code, _, err, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))
    assert code == 1
    assert "refused the admin token" in err
    assert not folder.exists()


def test_an_unreachable_pi_saves_nothing(capsys, tmp_path):
    certificate, _ = make_certificate(tmp_path)
    folder = tmp_path / "backups"
    with patch("jittersim.remote_client.getpass.getpass", return_value=TEST_TOKEN):
        code = main(["backup", "pull", "--url", "https://127.0.0.1:9",
                     "--ca-cert", str(certificate), "--directory", str(folder)])
    output = capsys.readouterr()
    assert code == 1
    assert "Could not reach the dashboard at https://127.0.0.1:9" in output.err
    assert TEST_TOKEN not in output.out + output.err
    assert not folder.exists()


def test_an_answer_that_is_not_a_database_is_not_saved(dashboard, capsys, tmp_path):
    dashboard.answer("GET", "/api/backup", b"<html>sign in to this Wi-Fi</html>")
    folder = tmp_path / "backups"
    code, _, err, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))
    assert code == 1
    assert err.strip() == "The dashboard answer was not a database backup."
    assert not folder.exists()


def test_a_backup_over_the_limit_is_not_saved(dashboard, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr("jittersim.remote_cli.MAX_BACKUP_BYTES", 100)
    dashboard.answer("GET", "/api/backup", BACKUP)
    folder = tmp_path / "backups"
    code, _, err, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))
    assert code == 1
    assert err.strip() == "The dashboard answer was too large."
    assert not folder.exists()


def test_the_help_says_what_the_command_keeps_and_how_to_repeat_it(capsys):
    with pytest.raises(SystemExit) as stop:
        main(["backup", "pull", "--help"])
    assert stop.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "~/jittersim-backups" in text
    assert "never replaced" in text
    assert "keep it private" in text
    assert "cron or launchd" in text


def test_backup_without_a_word_names_pull(capsys):
    with pytest.raises(SystemExit) as stop:
        main(["backup", "--help"])
    assert stop.value.code == 0
    assert "jittersim backup pull" in capsys.readouterr().out
