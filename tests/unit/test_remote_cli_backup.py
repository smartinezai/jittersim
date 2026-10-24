"""jittersim backup pull against a disposable HTTPS dashboard."""

import errno
import json
import os
import re
import socket
import stat
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim.cli import main
from tests.remote_support import TEST_TOKEN, make_certificate, run
from tests.unit.test_remote_hardening import tls_server

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


def test_a_cut_short_download_never_becomes_a_backup(capsys, tmp_path):
    certificate, key = make_certificate(tmp_path)

    def cut_short(connection, _stop):
        connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 4096\r\n"
                           b"Connection: close\r\n\r\n" + BACKUP)
        connection.shutdown(socket.SHUT_WR)

    folder = tmp_path / "backups"
    with tls_server(certificate, key, cut_short) as (url, _requests), \
            patch("jittersim.remote_client.getpass.getpass", return_value=TEST_TOKEN):
        code = main(["backup", "pull", "--url", url, "--ca-cert", str(certificate),
                     "--directory", str(folder)])

    assert code == 1
    assert "stopped before it was complete" in capsys.readouterr().err
    assert not folder.exists()


def test_an_oversize_answer_without_a_length_says_it_is_too_large(
    capsys, tmp_path, monkeypatch
):
    certificate, key = make_certificate(tmp_path)
    monkeypatch.setattr("jittersim.remote_cli.MAX_BACKUP_BYTES", 100)

    def no_length(connection, _stop):
        connection.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + BACKUP)
        connection.shutdown(socket.SHUT_WR)

    with tls_server(certificate, key, no_length) as (url, _requests), \
            patch("jittersim.remote_client.getpass.getpass", return_value=TEST_TOKEN):
        code = main(["backup", "pull", "--url", url, "--ca-cert", str(certificate),
                     "--directory", str(tmp_path / "backups")])

    assert code == 1
    assert capsys.readouterr().err.strip() == "The dashboard answer was too large."


def test_an_answer_that_does_not_say_how_long_it_is_never_becomes_a_backup(capsys, tmp_path):
    certificate, key = make_certificate(tmp_path)

    def no_length(connection, _stop):
        connection.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + BACKUP)
        connection.shutdown(socket.SHUT_WR)

    folder = tmp_path / "backups"
    with tls_server(certificate, key, no_length) as (url, _requests), \
            patch("jittersim.remote_client.getpass.getpass", return_value=TEST_TOKEN):
        code = main(["backup", "pull", "--url", url, "--ca-cert", str(certificate),
                     "--directory", str(folder)])

    assert code == 1
    assert "stopped before it was complete" in capsys.readouterr().err
    assert not folder.exists()


def test_a_backup_exactly_at_the_limit_is_saved(dashboard, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr("jittersim.remote_cli.MAX_BACKUP_BYTES", len(BACKUP))
    dashboard.answer("GET", "/api/backup", BACKUP)
    folder = tmp_path / "backups"
    code, _, err, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))

    assert code == 0, err
    (saved,) = folder.iterdir()
    assert saved.read_bytes() == BACKUP


@pytest.mark.parametrize("error", [OSError(errno.ENOSPC, "full disk"), KeyboardInterrupt()])
def test_a_partial_write_or_cancellation_leaves_no_backup(
    dashboard, capsys, tmp_path, monkeypatch, error
):
    real_fdopen = os.fdopen

    class InterruptedWriter:
        def __init__(self, descriptor, mode):
            self.handle = real_fdopen(descriptor, mode)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.handle.close()

        def write(self, data):
            self.handle.write(data[:20])
            self.handle.flush()
            raise error

    def interrupt_backup_write(descriptor, mode, **kwargs):
        if mode == "wb":
            return InterruptedWriter(descriptor, mode)
        return real_fdopen(descriptor, mode, **kwargs)

    monkeypatch.setattr("jittersim.remote_cli.os.fdopen", interrupt_backup_write)
    dashboard.answer("GET", "/api/backup", BACKUP)
    folder = tmp_path / "backups"
    code, _, _, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))

    assert code == 1
    assert list(folder.iterdir()) == []


def test_a_sync_failure_never_publishes_a_backup(dashboard, capsys, tmp_path, monkeypatch):
    def fail_sync(_descriptor):
        raise OSError(errno.EIO, "sync failed")

    monkeypatch.setattr("jittersim.remote_cli.os.fsync", fail_sync)
    dashboard.answer("GET", "/api/backup", BACKUP)
    folder = tmp_path / "backups"
    code, _, _, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))

    assert code == 1
    assert list(folder.iterdir()) == []


def test_a_file_that_appears_before_publication_is_never_replaced(
    dashboard, capsys, tmp_path, monkeypatch
):
    real_link = os.link
    folder = tmp_path / "backups"
    raced_paths = []

    def another_backup_appears(source, destination, **directories):
        assert (folder / source).read_bytes() == BACKUP
        assert _mode(folder / source) == 0o600
        (folder / destination).write_bytes(b"keep this backup")
        raced_paths.append(folder / destination)
        real_link(source, destination, **directories)

    monkeypatch.setattr("jittersim.remote_cli.os.link", another_backup_appears)
    dashboard.answer("GET", "/api/backup", BACKUP)
    code, _, err, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))

    assert code == 1
    assert "already exists" in err
    assert list(folder.iterdir()) == raced_paths
    assert raced_paths[0].read_bytes() == b"keep this backup"


@pytest.mark.parametrize("mode", [0o777, 0o1777, 0o775, 0o757])
def test_an_existing_folder_that_others_can_write_in_is_refused_before_the_download(
    dashboard, capsys, tmp_path, mode
):
    dashboard.answer("GET", "/api/backup", BACKUP)
    folder = tmp_path / "shared"
    folder.mkdir()
    folder.chmod(mode)
    code, _, err, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))

    assert code == 1
    assert f"The folder {folder.resolve()} can be changed by other users" in err
    assert "chmod go-w" in err and "--directory" in err
    assert dashboard.requests == []
    assert list(folder.iterdir()) == []


@pytest.mark.parametrize("mode", [0o700, 0o755, 0o750])
def test_an_existing_folder_only_you_can_write_in_is_used(dashboard, capsys, tmp_path, mode):
    dashboard.answer("GET", "/api/backup", BACKUP)
    folder = tmp_path / "mine"
    folder.mkdir()
    folder.chmod(mode)
    code, _, err, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))

    assert code == 0, err
    assert [item.read_bytes() for item in folder.iterdir()] == [BACKUP]
    assert _mode(folder) == mode


def test_an_existing_folder_owned_by_someone_else_is_refused(
    dashboard, capsys, tmp_path, monkeypatch
):
    dashboard.answer("GET", "/api/backup", BACKUP)
    folder = tmp_path / "theirs"
    folder.mkdir(mode=0o700)
    real_fstat = os.fstat

    def owned_by_another_user(descriptor):
        info = real_fstat(descriptor)
        if stat.S_ISDIR(info.st_mode):
            return types.SimpleNamespace(st_uid=info.st_uid + 1, st_mode=info.st_mode)
        return info

    monkeypatch.setattr("jittersim.remote_cli.os.fstat", owned_by_another_user)
    code, _, err, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))

    assert code == 1
    assert f"The folder {folder.resolve()} can be changed by other users" in err
    assert dashboard.requests == []
    assert list(folder.iterdir()) == []


def test_a_disk_without_hard_links_says_so_and_names_the_folder(
    dashboard, capsys, tmp_path, monkeypatch
):
    def no_links(source, destination, **_):
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr("jittersim.remote_cli.os.link", no_links)
    dashboard.answer("GET", "/api/backup", BACKUP)
    folder = tmp_path / "usb"
    code, _, err, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))

    assert code == 1
    assert str(folder.resolve()) in err
    assert "Operation not supported" in err
    assert "hard links" in err and "exFAT" in err
    assert list(folder.iterdir()) == []


def test_a_folder_that_cannot_be_used_is_named(dashboard, capsys, tmp_path):
    dashboard.answer("GET", "/api/backup", BACKUP)
    not_a_folder = tmp_path / "a-file"
    not_a_folder.write_text("x")
    code, _, err, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(not_a_folder))

    assert code == 1
    assert f"The folder {not_a_folder.resolve()} cannot be used" in err
    assert "--directory" in err
    assert dashboard.requests == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to a read-only folder")
def test_a_folder_that_cannot_take_the_file_is_named(dashboard, capsys, tmp_path):
    dashboard.answer("GET", "/api/backup", BACKUP)
    folder = tmp_path / "read-only"
    folder.mkdir()
    folder.chmod(0o500)
    try:
        code, _, err, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))
    finally:
        folder.chmod(0o700)

    assert code == 1
    assert f"The folder {folder.resolve()} cannot take the backup" in err
    assert list(folder.iterdir()) == []


@pytest.mark.parametrize("target_exists", [True, False])
def test_a_link_already_at_the_destination_is_never_followed(
    dashboard, capsys, tmp_path, target_exists
):
    dashboard.answer("GET", "/api/backup", BACKUP)
    folder = tmp_path / "backups"
    folder.mkdir()
    target = tmp_path / "somewhere-else"
    if target_exists:
        target.write_bytes(b"not a backup")
    taken = folder / "jittersim-backup-20261023T120000Z.db"
    taken.symlink_to(target)
    stub = types.SimpleNamespace(strftime=lambda *_: taken.name, gmtime=lambda: None)
    with patch("jittersim.remote_cli.time", stub):
        code, _, err, _ = run(dashboard, capsys, "backup", "pull", "--directory", str(folder))

    assert code == 1
    assert "already exists" in err
    assert target.read_bytes() == b"not a backup" if target_exists else not target.exists()
    assert list(folder.iterdir()) == [taken]


def test_the_help_says_what_the_command_keeps_and_how_to_repeat_it(capsys):
    with pytest.raises(SystemExit) as stop:
        main(["backup", "pull", "--help"])
    assert stop.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "~/jittersim-backups" in text
    assert "never replaced" in text
    assert "keep it private" in text
    assert "old backups are never deleted" in text
    assert "other users own or can write in is refused" in text
    assert "does not check the contents" in text and "quick_check" in text
    assert "cron or launchd" in text


def test_backup_without_a_word_names_pull(capsys):
    with pytest.raises(SystemExit) as stop:
        main(["backup", "--help"])
    assert stop.value.code == 0
    assert "jittersim backup pull" in capsys.readouterr().out
