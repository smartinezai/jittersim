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

    with patch.object(updates.shutil, "rmtree", interrupted), pytest.raises(KeyboardInterrupt):
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
