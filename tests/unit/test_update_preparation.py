"""Demonstrate preparation, publication and command lifetime failures."""

import json
import os
import signal
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim import updates
from tests.release_support import have_ssh_keygen
from tests.unit import test_updates as fixture_support


@pytest.fixture
def pi():
    if not have_ssh_keygen():
        pytest.skip("ssh-keygen signs the release")
    stand_in = fixture_support.UpdateTests(methodName="runTest")
    stand_in.setUp()
    try:
        stand_in.stage()
        updates.request_update("v1", root=stand_in.root, code=stand_in.code,
                               config=stand_in.config, run=lambda *args: None)
        yield stand_in
    finally:
        stand_in.tearDown()


def test_insufficient_disk_space_is_refused_before_services_stop(pi):
    commands, run = pi.recorder()
    with patch("jittersim.updates.shutil.disk_usage") as disk:
        disk.return_value.free = 0
        with pytest.raises(updates.UpdateError, match="free space"):
            pi.apply(run, lambda: True)
    assert not any(command[:2] == ["systemctl", "stop"] for command in commands)
    assert (pi.code / "old-file").read_text() == "previous installation"


def test_package_preparation_failure_does_not_stop_services(pi):
    commands = []

    def run(command, timeout):
        commands.append(list(command))
        if "pip" in command:
            raise updates.UpdateError("controlled package preparation failure")

    with pytest.raises(updates.UpdateError):
        pi.apply(run, lambda: True)
    assert not any(command[:2] == ["systemctl", "stop"] for command in commands)
    assert not (pi.code / "src").exists()
    assert (pi.data / "jittersim.db").read_text() == "original settings"


def test_interruption_during_replacement_keeps_the_previous_copy_discoverable(pi):
    previous = pi.root / "backup"
    previous.mkdir()
    updates.atomic_json(previous / "complete.json", {"created_at": 1})
    (previous / "identity").write_text("previous complete copy")
    remove = updates.shutil.rmtree

    def interrupted(path, *args, **kwargs):
        result = remove(path, *args, **kwargs)
        if Path(path) == previous:
            raise KeyboardInterrupt()
        return result

    original_atomic = updates.atomic_json

    def interrupt_publication(path, value):
        if path.name == "backup-current.json":
            raise KeyboardInterrupt()
        return original_atomic(path, value)

    with patch.object(updates.shutil, "rmtree", interrupted), \
            patch.object(updates, "atomic_json", interrupt_publication), pytest.raises(KeyboardInterrupt):
        pi.apply(lambda *args: None, lambda: True)
    assert updates.update_status(root=pi.root, code=pi.code, config=pi.config)["rollback_available"]
    assert (previous / "identity").read_text() == "previous complete copy"


def test_uncertain_stop_and_failed_restart_keeps_a_retryable_request(pi):
    def fail_service(command, timeout):
        if command[:1] == ["systemctl"]:
            raise updates.UpdateError("controlled service failure")

    with pytest.raises(updates.UpdateError):
        pi.apply(fail_service, lambda: False)
    request = updates.read_json(pi.root / "request.json")
    assert request, "an unknown stop or failed restart must remain recoverable"
    assert updates.read_json(pi.root / "status.json")["phase"] == "failed"


def test_connection_query_rejects_excess_output():
    with pytest.raises(updates.UpdateError, match="output"):
        updates.read_command([sys.executable, "-c", "print('x' * 65536)"], timeout=5)


def test_command_diagnostic_drain_has_a_byte_limit():
    with pytest.raises(updates.UpdateError, match="output"):
        updates.run_command([sys.executable, "-c", "import sys; sys.stderr.write('x' * 2097152)"],
                            timeout=5)


def test_command_group_is_only_signalled_while_its_leader_is_owned(monkeypatch):
    create = updates.subprocess.Popen
    kill = updates.os.killpg
    owned = []

    def start(*args, **kwargs):
        process = create(*args, **kwargs)
        owned.append(process)
        return process

    def cancel(group, sig):
        process = next(item for item in owned if item.pid == group)
        assert process.returncode is None, "a reaped leader's group number may have been reused"
        return kill(group, sig)

    monkeypatch.setattr(updates.subprocess, "Popen", start)
    monkeypatch.setattr(updates.os, "killpg", cancel)
    updates.run_command([sys.executable, "-c", "pass"], timeout=5)


def test_timeout_ends_a_child_that_retains_the_output_pipe(tmp_path):
    pid_file = tmp_path / "child.pid"
    child = "import os, pathlib, sys, time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
    parent = ("import pathlib, subprocess, sys, time; "
              "subprocess.Popen([sys.executable, '-c', " + json.dumps(child) + ", "
              + json.dumps(str(pid_file)) + "]); "
              "time.sleep(0.05)")
    pid = None
    try:
        with pytest.raises(updates.UpdateError):
            updates.read_command([sys.executable, "-c", parent], timeout=0.3)
        pid = int(pid_file.read_text())
        until = time.monotonic() + 2
        alive = True
        while alive and time.monotonic() < until:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                alive = False
            if alive and sys.platform.startswith("linux"):
                # A container's PID 1 may retain a terminated orphan as a zombie.
                state = Path("/proc") / str(pid) / "stat"
                try:
                    alive = state.read_text().rsplit(")", 1)[1].split()[0] != "Z"
                except FileNotFoundError:
                    alive = False
            if alive:
                time.sleep(0.02)
        assert not alive, "the owned child must not survive its command deadline"
    finally:
        if pid is None and pid_file.exists():
            pid = int(pid_file.read_text())
        if pid is not None:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_trickling_output_does_not_extend_the_command_deadline():
    start = time.monotonic()
    script = "import sys, time\nwhile True:\n print('x', flush=True)\n time.sleep(0.03)"
    with pytest.raises(updates.UpdateError, match="timed out"):
        updates.run_command([sys.executable, "-c", script], timeout=0.3)
    assert time.monotonic() - start < 2


def test_command_environment_refuses_inherited_package_sources(monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://invalid.example/private-index")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://invalid.example/extra-index")
    monkeypatch.setenv("JITTERSIM_ADMIN_TOKEN", "throwaway-test-value")
    script = ("import json, os; print(json.dumps({name: value for name, value in os.environ.items() "
              "if name.startswith('PIP_') or 'TOKEN' in name or 'PASSWORD' in name}))")
    answer = updates.read_command([sys.executable, "-c", script], timeout=5)
    assert json.loads(answer) == {"PIP_CONFIG_FILE": os.devnull}


def test_failed_command_never_exposes_arbitrary_diagnostics():
    script = "import sys; sys.stderr.write('throwaway private diagnostic'); sys.exit(3)"
    with pytest.raises(updates.UpdateError, match="exit status 3") as failure:
        updates.run_command([sys.executable, "-c", script], timeout=5)
    assert "private diagnostic" not in str(failure.value)


def test_a_malformed_selector_never_falls_back_to_an_older_copy(pi):
    previous = pi.root / "backup"
    previous.mkdir()
    updates.atomic_json(previous / "complete.json", {"created_at": 1})
    updates.atomic_json(pi.root / "backup-current.json", {"name": "../../outside", "attempt": "a" * 32})
    with pytest.raises(updates.UpdateError, match="recovery"):
        updates.recovery_backup(pi.root)


def test_mutable_snapshot_refuses_a_link_before_copying_outside(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("unrelated contents")
    (source / "settings").symlink_to(outside)
    with pytest.raises(updates.UpdateError, match="link"):
        updates.snapshot_tree(source, tmp_path / "copy", updates.UpdateBudget())
    assert outside.read_text() == "unrelated contents"


def test_snapshot_has_a_finite_entry_limit(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for index in range(4):
        (source / str(index)).write_text("x")
    monkeypatch.setattr(updates, "MAX_SNAPSHOT_ENTRIES", 3, raising=False)
    with pytest.raises(updates.UpdateError, match="entries"):
        updates.snapshot_tree(source, tmp_path / "copy", updates.UpdateBudget())


def test_snapshot_refuses_late_work_without_changing_the_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "settings").write_text("current")
    budget = updates.UpdateBudget()
    budget.deadline = time.monotonic() - 1
    with pytest.raises(updates.UpdateError, match="time"):
        updates.snapshot_tree(source, tmp_path / "copy", budget)
    assert (source / "settings").read_text() == "current"


def test_offline_install_forces_equal_version_and_never_uses_an_index(tmp_path):
    commands = []
    updates.offline_install(tmp_path / "python", [str(tmp_path / "prepared.whl")],
                            lambda command, timeout: commands.append(command), updates.UpdateBudget())
    install = commands[0]
    assert "--force-reinstall" in install
    assert "--no-index" in install and "--no-deps" in install
    assert install[-1] == str(tmp_path / "prepared.whl")
    assert commands[-1][-3:] == ["-I", "-c", updates.IMPORT_CHECK]


def test_prepared_wheels_are_rechecked_against_the_signed_release(tmp_path):
    path = tmp_path / "prepare"
    path.mkdir(mode=0o700)
    (path / "wheels").mkdir()
    wheel = path / "wheels" / "example-1.0-py3-none-any.whl"
    wheel.write_bytes(b"approved wheel")
    record = {"commit": "a" * 40, "version": "0.2.0"}
    import hashlib
    updates.atomic_json(path / "wheels.json", dict(record, wheels=[{
        "name": wheel.name, "size": wheel.stat().st_size,
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }]))
    assert updates.prepared_wheels(path, record, updates.UpdateBudget()) == [str(wheel)]
    wheel.write_bytes(b"replaced wheel")
    with pytest.raises(updates.UpdateError, match="changed"):
        updates.prepared_wheels(path, record, updates.UpdateBudget())


def test_build_outputs_exceeding_the_private_allowance_are_refused(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(updates, "MAX_PREPARATION_BYTES", 10)

    def build(command, timeout):
        if "wheel" in command:
            (tmp_path / "prepared/wheels/example-1-py3-none-any.whl").write_bytes(b"x" * 11)

    with pytest.raises(updates.UpdateError, match="large"):
        updates.prepare_install(source, tmp_path / "prepared", Path(sys.executable),
                                {"commit": "a" * 40, "version": "0.2.0"},
                                build, updates.UpdateBudget())


def test_selector_replace_followed_by_sync_failure_restores_this_attempt(pi):
    real_atomic = updates.atomic_json
    failures = []

    def interrupted(path, value):
        result = real_atomic(path, value)
        if path.name == "backup-current.json" and not failures:
            failures.append(path)
            raise OSError("controlled directory sync failure after replace")
        return result

    with patch.object(updates, "atomic_json", interrupted), pytest.raises(OSError):
        pi.apply(lambda *args: None, lambda: True)
    assert failures
    assert (pi.code / "old-file").read_text() == "previous installation"
    assert updates.read_json(pi.root / "status.json")["phase"] == "rolled-back"
    assert not (pi.root / "request.json").exists()


def test_termination_after_publication_before_installing_is_recovered(pi):
    real_atomic = updates.atomic_json

    def interrupted(path, value):
        result = real_atomic(path, value)
        if path.name == "backup-current.json":
            raise KeyboardInterrupt()
        return result

    with patch.object(updates, "atomic_json", interrupted), pytest.raises(KeyboardInterrupt):
        pi.apply(lambda *args: None, lambda: True)
    assert (pi.root / "request.json").exists()
    pi.apply(lambda *args: None, lambda: True)
    assert (pi.code / "old-file").read_text() == "previous installation"
    assert updates.read_json(pi.root / "status.json")["phase"] == "rolled-back"


def test_unknown_writer_state_never_publishes_a_mutable_snapshot(pi):
    commands = []
    with patch.object(updates, "writers_stopped", return_value=False, create=True):
        with pytest.raises(updates.UpdateError, match="stop"):
            pi.apply(lambda command, timeout: commands.append(command), lambda: True)
    assert not (pi.root / "backup-current.json").exists()
    assert not (pi.code / "src").exists()


def test_invalid_restore_input_is_refused_before_live_code_is_deleted(pi):
    pi.apply(lambda *args: None, lambda: True)
    backup = updates.recovery_backup(pi.root)
    updates.shutil.rmtree(backup / "data")
    updates.request_update("", rollback=True, root=pi.root, code=pi.code, config=pi.config,
                           run=lambda *args: None)
    before = (pi.code / "src/jittersim/api/server.py").read_bytes()
    with pytest.raises(updates.UpdateError, match="recovery"):
        pi.apply(lambda *args: None, lambda: True)
    assert (pi.code / "src/jittersim/api/server.py").read_bytes() == before


def test_package_builds_finish_before_stop_and_outage_installs_only_wheels(pi):
    commands, run = pi.recorder()
    pi.apply(run, lambda: True)
    stop = next(index for index, command in enumerate(commands) if command[:2] == ["systemctl", "stop"])
    assert any("wheel" in command for command in commands[:stop])
    installs = [command for command in commands[stop:] if "pip" in command and "install" in command]
    assert installs
    assert all("--no-index" in command and "--no-deps" in command for command in installs)
    assert all(command[-1].endswith(".whl") for command in installs)


def test_health_body_has_a_total_deadline_even_when_bytes_keep_arriving(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(updates.time, "monotonic", lambda: clock[0])

    class MemoryFile:
        def read_text(self):
            return "JITTERSIM_ADMIN_TOKEN=throwaway-health-test\n"

        def __str__(self):
            return "in-memory-health-fixture"

    class MemoryConfig:
        def __truediv__(self, name):
            return MemoryFile()

    class Transport:
        def settimeout(self, value):
            assert 0 < value <= 10

    class Context:
        def wrap_socket(self, sock, server_hostname):
            return sock

    class Response:
        status = 200

        def read(self, amount):
            clock[0] += 100
            return b'{"status":"ok","helper_reachable":true}'

        def read1(self, amount):
            clock[0] += 4
            return b" "

    class Connection:
        def __init__(self, *args, **kwargs):
            self.sock = None

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(updates.ssl, "create_default_context", lambda **kwargs: Context())
    monkeypatch.setattr(updates.socket, "create_connection", lambda *args, **kwargs: Transport())
    monkeypatch.setattr(updates.http.client, "HTTPSConnection", Connection)
    assert not updates.healthy(config=MemoryConfig(), run=lambda *args: None)
    assert clock[0] <= 112, "continuing body bytes must not reset health's total allowance"


def test_cleanup_keeps_exactly_two_complete_copies_including_the_legacy_copy(pi):
    previous = pi.root / "backup"
    previous.mkdir(mode=0o700)
    updates.atomic_json(previous / "complete.json", {"created_at": 1})
    pi.apply(lambda *args: None, lambda: True)
    pi.stage(sequence=101, version="v2")
    updates.request_update("v2", root=pi.root, code=pi.code, config=pi.config,
                           run=lambda *args: None)
    pi.apply(lambda *args: None, lambda: True)
    updates.cleanup_generations(pi.root, updates.UpdateBudget())
    complete = [path for path in pi.root.iterdir() if path.is_dir() and (path / "complete.json").exists()]
    assert len(complete) == 2
    assert updates.recovery_backup(pi.root) in complete


def test_selector_name_is_bound_to_its_completed_attempt(pi):
    name, attempt = "backup-" + "a" * 32, "b" * 32
    backup = pi.root / name
    backup.mkdir(mode=0o700)
    updates.atomic_json(backup / "complete.json", {"format": 2, "attempt": attempt})
    updates.atomic_json(pi.root / "backup-current.json", {"format": 2, "name": name, "attempt": attempt})
    with pytest.raises(updates.UpdateError, match="recovery"):
        updates.recovery_backup(pi.root)


def test_retry_restores_the_pinned_copy_even_if_the_selector_changes(pi):
    pi.apply(lambda *args: None, lambda: True)
    first = updates.recovery_backup(pi.root)
    updates.request_update("", rollback=True, root=pi.root, code=pi.code, config=pi.config,
                           run=lambda *args: None)
    next_attempt = "b" * 32
    other = pi.root / ("backup-" + next_attempt)
    updates.shutil.copytree(first, other, symlinks=True)
    record = updates.read_json(other / "complete.json")
    record["attempt"] = next_attempt
    updates.atomic_json(other / "complete.json", record)
    updates.atomic_json(pi.root / "backup-current.json", {"format": 2, "name": other.name,
                                                        "attempt": next_attempt})
    pi.apply(lambda *args: None, lambda: True)
    assert (pi.code / "old-file").read_text() == "previous installation"
    assert updates.read_json(pi.root / "status.json")["phase"] == "rolled-back"


def test_a_timed_out_command_with_unknown_completion_never_starts_restore(pi):
    commands = []

    class StillOwned:
        pid = 123456789

    def run(command, timeout):
        commands.append(command)
        if "pip" in command and "install" in command and command[0] == str(pi.code / ".venv/bin/python"):
            raise updates.CommandNotStopped("python", StillOwned())

    with pytest.raises(updates.CommandNotStopped):
        pi.apply(run, lambda: True)
    assert (pi.code / "src").exists(), "unknown writes must not race a restore"
    assert updates.read_json(pi.root / "request.json")["command_pending"]["group"] == StillOwned.pid
    with patch.object(updates, "command_still_running", return_value=True):
        commands.clear()
        with pytest.raises(updates.UpdateError, match="unfinished"):
            pi.apply(lambda command, timeout: commands.append(command), lambda: True)
        assert commands == []
    pi.apply(lambda *args: None, lambda: True)
    assert (pi.code / "old-file").read_text() == "previous installation"


def test_transient_uploads_are_not_copied_into_recovery(pi):
    incoming = pi.data / "incoming-release"
    incoming.mkdir()
    (incoming / "unfinished-upload").write_bytes(b"transient input")
    pi.apply(lambda *args: None, lambda: True)
    assert not (updates.recovery_backup(pi.root) / "data/incoming-release").exists()


def test_manager_compatibility_requires_runnable_standalone_source(tmp_path):
    source = tmp_path / "updates.py"
    source.write_text("RECOVERY_FORMAT = 2\ndef broken(\n")
    with pytest.raises(updates.UpdateError, match="recovery manager"):
        updates.compatible_manager(source)


def test_manual_manager_publication_requires_the_shared_lock_and_is_atomic(tmp_path):
    import fcntl
    root = tmp_path / "updates"
    root.mkdir(mode=0o700)
    destination = tmp_path / "installed.py"
    destination.write_text("previous manager")
    source = tmp_path / "manager.py"
    source.write_text("RECOVERY_FORMAT = 2\n")
    with (root / "lock").open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (root / "lock").open("a+") as unrelated:
            with pytest.raises(updates.UpdateError, match="ownership"):
                updates.bootstrap_manager(source, destination, root=root, descriptor=unrelated.fileno())
        assert destination.read_text() == "previous manager"
        original = updates.os.replace

        def interrupted(old, new):
            if Path(new) == destination:
                raise OSError("controlled publication interruption")
            return original(old, new)

        with patch.object(updates.os, "replace", interrupted), pytest.raises(OSError):
            updates.bootstrap_manager(source, destination, root=root, descriptor=owner.fileno())
        assert destination.read_text() == "previous manager"
        updates.bootstrap_manager(source, destination, root=root, descriptor=owner.fileno())
        assert destination.read_bytes() == source.read_bytes()
        assert destination.stat().st_mode & 0o777 == 0o644


def test_the_database_snapshot_includes_committed_wal_after_the_writer_ends(pi, tmp_path):
    import sqlite3
    import subprocess
    database = pi.data / "jittersim.db"
    database.unlink()
    ready = tmp_path / "ready"
    script = ("import pathlib, sqlite3, sys, time\n"
              "db=sqlite3.connect(sys.argv[1])\n"
              "db.execute('PRAGMA journal_mode=WAL')\n"
              "db.execute('CREATE TABLE settings(value INTEGER)')\n"
              "db.execute('INSERT INTO settings VALUES (43)')\n"
              "db.commit()\n"
              "pathlib.Path(sys.argv[2]).touch()\n"
              "time.sleep(60)\n")
    writer = subprocess.Popen([sys.executable, "-c", script, str(database), str(ready)])
    try:
        until = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < until:
            time.sleep(0.01)
        assert ready.exists()

        def end_writer(command, timeout):
            if command[:2] == ["systemctl", "stop"] and writer.poll() is None:
                writer.kill()
                writer.wait(timeout=5)

        pi.apply(end_writer, lambda: True)
        saved = updates.recovery_backup(pi.root) / "data"
        assert (saved / "jittersim.db-wal").exists()
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE settings SET value=44")
        updates.request_update("", rollback=True, root=pi.root, code=pi.code, config=pi.config,
                               run=lambda *args: None)
        pi.apply(lambda *args: None, lambda: True)
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT value FROM settings").fetchone() == (43,)
    finally:
        if writer.poll() is None:
            writer.kill()
        writer.wait(timeout=5)


def test_preparation_monitor_tolerates_removed_build_temporaries(tmp_path, monkeypatch):
    path = tmp_path / "prepare"
    path.mkdir()
    temporary = path / "temporary"
    temporary.write_text("short-lived build input")
    real_lstat = Path.lstat

    def vanished(entry, *args, **kwargs):
        if entry == temporary and entry.exists():
            entry.unlink()
        return real_lstat(entry, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", vanished)
    updates._check_preparation(path, updates.UpdateBudget())


def test_snapshot_allowance_is_shared_by_all_saved_trees(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a").write_bytes(b"aa")
    (second / "b").write_bytes(b"bb")
    monkeypatch.setattr(updates, "MAX_SNAPSHOT_BYTES", 3)
    with pytest.raises(updates.UpdateError, match="complete recovery snapshot"):
        updates.snapshot_requirements([("config", first), ("data", second)], updates.UpdateBudget())


def test_manual_installer_checks_manager_compatibility_before_system_changes():
    installer = Path(__file__).resolve().parents[2] / "deploy/raspberry-pi/install.sh"
    commands = fixture_support.installer_lines()
    check = next(index for index, line in enumerate(commands) if line.endswith('check-manager "$SOURCE_DIR/src/jittersim/updates.py"'))
    first_write = next(index for index, line in enumerate(commands) if 'apt-get' in line)
    assert check < first_write
    assert installer.exists()


def test_successful_failure_recovery_also_bounds_complete_copy_retention(pi):
    pi.apply(lambda *args: None, lambda: True)
    pi.stage(sequence=101, version="v2")
    updates.request_update("v2", root=pi.root, code=pi.code, config=pi.config, run=lambda *args: None)
    pi.apply(lambda *args: None, lambda: True)
    pi.stage(sequence=102, version="v3")
    updates.request_update("v3", root=pi.root, code=pi.code, config=pi.config, run=lambda *args: None)

    def fail_install(command, timeout):
        if "pip" in command and "install" in command and command[0] == str(pi.code / ".venv/bin/python"):
            raise updates.UpdateError("controlled offline installation failure")

    with pytest.raises(updates.UpdateError):
        pi.apply(fail_install, lambda: True)
    assert updates.read_json(pi.root / "status.json")["phase"] == "rolled-back"
    complete = [path for path in pi.root.iterdir() if path.is_dir() and (path / "complete.json").exists()]
    assert len(complete) == 2
