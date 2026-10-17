"""All mutable update and identity work shares physical command ownership."""

import os
from unittest.mock import patch
import signal
import shlex
import subprocess
import sys
import time
import types

import pytest

from jittersim import updates
from tests.marker_support import PUBLISH_MARKER


@pytest.fixture
def root(tmp_path):
    directory = tmp_path / "updates"
    directory.mkdir(mode=0o700)
    return directory


class Pending:
    pid = 123


def test_unknown_work_is_saved_before_releasing_shared_ownership(root):
    with pytest.raises(updates.CommandNotStopped):
        with updates.update_lock(root):
            raise updates.CommandNotStopped("controlled command", Pending())
    record = updates.read_json(root / "operation-pending.json")
    assert record["group"] == Pending.pid
    assert record["boot"] == updates._current_boot()


def test_existing_live_operation_refuses_new_mutable_work(root, monkeypatch):
    updates.atomic_json(root / "operation-pending.json", {
        "format": 1, "group": Pending.pid, "boot": updates._current_boot()})
    monkeypatch.setattr(updates, "command_still_running", lambda *args: True)
    with pytest.raises(updates.UpdateError, match="command"):
        with updates.update_lock(root):
            pytest.fail("another command still owns mutable work")


def test_only_confirmed_completion_clears_the_pending_operation(root, monkeypatch):
    updates.atomic_json(root / "operation-pending.json", {
        "format": 1, "group": Pending.pid, "boot": updates._current_boot()})
    monkeypatch.setattr(updates, "command_still_running", lambda *args: False)
    with updates.update_lock(root):
        assert not (root / "operation-pending.json").exists()


@pytest.mark.parametrize("contents", ["not JSON", "{}", '{"format": 2, "group": 123, "boot": null}'])
def test_unknown_pending_record_refuses_mutation(root, contents):
    path = root / "operation-pending.json"
    path.write_text(contents)
    path.chmod(0o600)
    with pytest.raises(updates.UpdateError):
        with updates.update_lock(root):
            pytest.fail("unknown operation ownership must remain blocked")


def test_identity_change_refuses_any_retained_request(root):
    updates.atomic_json(root / "request.json", {"version": "v1", "rollback": True})
    with pytest.raises(updates.UpdateError, match="update"):
        with updates.manual_identity_operation(root):
            pytest.fail("the queued recovery still owns its settings")


def test_borrowed_installer_descriptor_keeps_its_parent_lock(root):
    with updates.update_lock(root) as descriptor:
        script = ("import os,sys; from pathlib import Path; from jittersim.updates import update_lock; "
                  "fd=int(sys.argv[2]); "
                  "ctx=update_lock(Path(sys.argv[1]),descriptor=fd); "
                  "ctx.__enter__(); ctx.__exit__(None,None,None); os.fstat(fd)")
        subprocess.run([sys.executable, "-c", script, str(root), str(descriptor)],
                       pass_fds=(descriptor,), check=True, timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with pytest.raises(updates.UpdateError):
            with updates.update_lock(root):
                pytest.fail("a borrowed descriptor must never unlock its parent")


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_unsafe_anchor_is_refused_before_mutation(root, kind):
    other = root.parent / "other"
    other.touch(mode=0o600)
    if kind == "symlink":
        (root / "lock").symlink_to(other)
    else:
        os.link(other, root / "lock")
    with pytest.raises(updates.UpdateError):
        with updates.update_lock(root):
            pytest.fail("a replaceable or shared anchor is unsafe")


def test_unknown_leaf_keeps_kernel_lock_after_parent_scope_exits(root, monkeypatch, tmp_path):
    processes = []
    spawn = updates.subprocess.Popen
    kill = updates.os.killpg

    def start(*args, **kwargs):
        process = spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(updates.subprocess, "Popen", start)
    monkeypatch.setattr(updates.os, "killpg", lambda *args: None)
    marker = tmp_path / "started"
    script = ("import os,sys,time; from pathlib import Path; "
              "os.fstat(int(sys.argv[1])); Path(sys.argv[2]).touch(); time.sleep(30)")
    try:
        with pytest.raises(updates.CommandNotStopped):
            with updates.update_lock(root) as descriptor:
                updates.run_command([sys.executable, "-c", script, str(descriptor), str(marker)], timeout=0.4)
        assert marker.exists()
        # Kernel ownership is independent of the durable completion record.
        with (root / "lock").open("a") as handle:
            with pytest.raises(BlockingIOError):
                updates.fcntl.flock(handle, updates.fcntl.LOCK_EX | updates.fcntl.LOCK_NB)
    finally:
        for process in processes:
            if process.poll() is None:
                kill(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def test_killed_owner_and_leader_cannot_hide_a_writer_without_lock_fd(root, tmp_path):
    leader_file, writer_file = tmp_path / "leader.pid", tmp_path / "writer.pid"
    writes = tmp_path / "owned-writes"
    writer = (PUBLISH_MARKER + "import os,sys,time; from pathlib import Path; "
              "publish_marker(sys.argv[1], str(os.getpid())); "
              "target=Path(sys.argv[2]); "
              "\nfor number in range(300):\n target.write_text(str(number))\n time.sleep(.1)\n")
    leaf = (PUBLISH_MARKER + "import os,subprocess,sys,time; from pathlib import Path; "
            "publish_marker(sys.argv[1], str(os.getpid())); "
            "subprocess.Popen([sys.executable,'-c'," + repr(writer) + ",sys.argv[2],sys.argv[3]],close_fds=True); "
            "time.sleep(30)")
    owner = ("from pathlib import Path; from jittersim.updates import update_lock,run_command; import sys; "
             "\nwith update_lock(Path(sys.argv[1])):\n"
             " run_command([sys.executable,'-c'," + repr(leaf) + ",*sys.argv[2:]],timeout=30)\n")
    process = subprocess.Popen([sys.executable, "-c", owner, str(root), str(leader_file),
                                str(writer_file), str(writes)], start_new_session=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    leader = writer_pid = None
    try:
        until = time.monotonic() + 3
        while not (leader_file.exists() and writer_file.exists() and writes.exists()):
            assert process.poll() is None, "the owned fixture failed before its writer started"
            assert time.monotonic() < until
            time.sleep(0.01)
        leader, writer_pid = int(leader_file.read_text()), int(writer_file.read_text())
        process.kill()
        process.wait(timeout=3)
        os.kill(leader, signal.SIGKILL)
        time.sleep(0.1)
        assert os.getpgid(writer_pid) == leader
        with pytest.raises(updates.UpdateError, match="command"):
            with updates.update_lock(root):
                pytest.fail("the FD-free writer still owns unfinished work")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        for pid in (writer_pid, leader):
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_running_publication_failure_never_executes_mutable_leaf(root, tmp_path, monkeypatch):
    marker = tmp_path / "must-not-run"
    atomic = updates.atomic_json

    def interrupted(path, record):
        result = atomic(path, record)
        if path.name == "operation-pending.json" and record.get("state") == "running":
            raise OSError("controlled running-lease sync failure")
        return result

    monkeypatch.setattr(updates, "atomic_json", interrupted)
    with pytest.raises(updates.UpdateError):
        with updates.update_lock(root):
            updates.run_command([sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
                                 str(marker)], timeout=2)
    assert not marker.exists()


def test_parent_death_before_go_never_executes_mutable_leaf(root, tmp_path):
    ready, marker = tmp_path / "before-go", tmp_path / "must-not-run"
    source = (PUBLISH_MARKER + "import sys,time; from pathlib import Path; from jittersim import updates; "
              "atomic=updates.atomic_json; "
              "\ndef pause(path,record):\n"
              " if path.name=='operation-pending.json' and record.get('state')=='running':\n"
              "  publish_marker(sys.argv[2], str(record['group']))\n  time.sleep(30)\n"
              " return atomic(path,record)\n"
              "updates.atomic_json=pause\n"
              "with updates.update_lock(Path(sys.argv[1])):\n"
              " updates.run_command([sys.executable,'-c','from pathlib import Path; import sys; Path(sys.argv[1]).touch()',sys.argv[3]],timeout=30)\n")
    owner = subprocess.Popen([sys.executable, "-c", source, str(root), str(ready), str(marker)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    child = None
    try:
        until = time.monotonic() + 3
        while not ready.exists():
            assert owner.poll() is None, "the owned fixture ran before durable group publication"
            assert time.monotonic() < until
            time.sleep(0.01)
        child = int(ready.read_text())
        owner.kill()
        owner.wait(timeout=3)
        until = time.monotonic() + 3
        while True:
            try:
                with updates.update_lock(root):
                    break
            except updates.UpdateError:
                assert time.monotonic() < until
                time.sleep(0.01)
        assert not marker.exists()
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=3)
        if child is not None:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_old_manager_cannot_accept_new_operation_records(tmp_path):
    source = tmp_path / "old-manager.py"
    source.write_text("RECOVERY_FORMAT = 2\n")
    with pytest.raises(updates.UpdateError, match="ownership"):
        updates.compatible_manager(source)


def test_installer_admission_refuses_retained_requests_without_releasing_parent(root):
    with updates.update_lock(root) as descriptor:
        updates.atomic_json(root / "request.json", {"rollback": True})
        with pytest.raises(updates.UpdateError, match="update"):
            updates.check_operation(root=root, descriptor=descriptor)
        with pytest.raises(updates.UpdateError):
            with updates.update_lock(root):
                pytest.fail("installer refusal must not release its parent's descriptor")


def test_unknown_command_fence_prevents_manual_manager_replacement(root, tmp_path):
    source, destination = tmp_path / "current.py", tmp_path / "installed.py"
    source.write_text("RECOVERY_FORMAT = 2\nOPERATION_FORMAT = 2\n")
    destination.write_text("previous manager")
    with updates.update_lock(root) as descriptor:
        updates.atomic_json(root / "operation-pending.json", {
            "format": 2, "state": "launching", "owner": os.getpid(),
            "group": None, "boot": updates._current_boot(), "nonce": "a" * 32})
        with pytest.raises(updates.UpdateError, match="command"):
            updates.bootstrap_manager(source, destination, root=root, descriptor=descriptor)
    assert destination.read_text() == "previous manager"


@pytest.mark.parametrize("record", [
    {"format": True, "group": 123, "boot": None},
    {"format": 1, "group": 2 ** 63, "boot": None},
])
def test_non_integer_format_and_unusable_group_cannot_be_cleared(root, monkeypatch, record):
    record["boot"] = updates._current_boot()
    updates.atomic_json(root / "operation-pending.json", record)
    if record["format"] is True:
        monkeypatch.setattr(updates, "command_still_running", lambda *args: False)
    with pytest.raises(updates.UpdateError):
        with updates.update_lock(root):
            pytest.fail("unsupported pending ownership must remain refused")


def test_new_command_cannot_overwrite_an_unknown_lease_inside_the_same_scope(root, tmp_path):
    marker = tmp_path / "must-not-run"
    with updates.update_lock(root):
        updates.atomic_json(root / "operation-pending.json", {
            "format": 2, "state": "launching", "owner": os.getpid(),
            "group": None, "boot": updates._current_boot(), "nonce": "a" * 32})
        before = (root / "operation-pending.json").read_bytes()
        with pytest.raises(updates.OperationOwnershipUnknown):
            updates.run_command([sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
                                 str(marker)], timeout=1)
        assert (root / "operation-pending.json").read_bytes() == before
    assert not marker.exists()


def test_late_child_go_cannot_execute_after_parent_death_with_process_relative_clock(root, tmp_path):
    ready, marker = tmp_path / "after-go", tmp_path / "must-not-run"
    # Older macOS Python uses a process-relative monotonic epoch. The child
    # must compare an absolute clock even when that differs from its parent.
    source = ("import sys; from pathlib import Path; from jittersim import updates; "
              "clock=updates.time.monotonic; updates.time.monotonic=lambda:clock()+1000000; "
              "updates.COMMAND_BARRIER=updates.COMMAND_BARRIER.replace('os.close(fd)',"
              + repr("Path_PLACEHOLDER.write_text(str(os.getpid()))\ntime.sleep(.4)\nos.close(fd)")
              + ").replace('Path_PLACEHOLDER',"
              + repr("__import__('pathlib').Path(" + repr(str(ready)) + ")") + ")\n"
              "with updates.update_lock(Path(sys.argv[1])):\n"
              " updates.run_command([sys.executable,'-c','from pathlib import Path; import sys; Path(sys.argv[1]).touch()',sys.argv[2]],timeout=.25)\n")
    owner = subprocess.Popen([sys.executable, "-c", source, str(root), str(marker)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        until = time.monotonic() + 3
        while not ready.exists():
            assert owner.poll() is None
            assert time.monotonic() < until
            time.sleep(.01)
        owner.kill()
        owner.wait(timeout=3)
        time.sleep(.6)
        assert not marker.exists(), "a late GO cannot execute after the shared command deadline"
        with updates.update_lock(root):
            pass
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=3)


def test_unknown_health_command_propagates_without_authorizing_recovery(root):
    failure = updates.OperationOwnershipUnknown("controlled unknown health launch")

    def run(*args):
        raise failure

    with pytest.raises(updates.OperationOwnershipUnknown) as caught:
        updates.healthy(config=root, run=run)
    assert caught.value is failure


def test_killed_installer_parent_and_shell_keep_fd_free_child_owned(root, tmp_path):
    installer, ready = tmp_path / "install.sh", tmp_path / "ready"
    child = PUBLISH_MARKER + "import os,sys,time; publish_marker(sys.argv[1], str(os.getpid())); time.sleep(30)"
    installer.write_text("#!/bin/sh\n" + repr(sys.executable) + " -c "
                         + shlex.quote(child) + " "
                         + shlex.quote(str(ready)) + " 9>&- &\nwait\n")
    source = ("import sys; from pathlib import Path; from jittersim import updates; "
              "fd=updates.os.open(Path(sys.argv[1])/'lock',updates.os.O_RDWR|updates.os.O_CREAT,0o600); "
              "updates.os.dup2(fd,9); "
              "updates.run_installer(Path(sys.argv[2]),Path(sys.argv[3]),root=Path(sys.argv[1]),"
              "descriptor=9,prepare=lambda *args,**kwargs:None,timeout=30)")
    parent = subprocess.Popen([sys.executable, "-c", source, str(root), str(installer), str(tmp_path)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    child_pid = shell_pid = None
    try:
        until = time.monotonic() + 3
        while not ready.exists():
            assert parent.poll() is None, "the actual installer ownership entry failed before its child started"
            assert time.monotonic() < until
            time.sleep(.01)
        child_pid = int(ready.read_text())
        shell_pid = os.getpgid(child_pid)
        parent.kill()
        parent.wait(timeout=3)
        os.kill(shell_pid, signal.SIGKILL)
        time.sleep(.1)
        with pytest.raises(updates.UpdateError, match="command"):
            with updates.update_lock(root):
                pytest.fail("the installer child that closed FD9 must still own unfinished work")
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=3)
        for pid in (child_pid, shell_pid):
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


@pytest.mark.parametrize("rejected_nonce", ["b" * 32, "é" * 32, ""])
def test_borrowed_installer_scope_needs_its_exact_nonce_and_group(root, monkeypatch, rejected_nonce):
    updates.atomic_json(root / "installer-pending.json", {
        "format": 2, "state": "running", "owner": os.getpid(), "group": os.getpgrp(),
        "boot": updates._current_boot(), "nonce": "a" * 32})
    descriptor = os.open(root / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        monkeypatch.setenv("JITTERSIM_INSTALLER_NONCE", rejected_nonce)
        with pytest.raises(updates.UpdateError):
            updates.check_operation(root=root, descriptor=descriptor)
        monkeypatch.setenv("JITTERSIM_INSTALLER_NONCE", "a" * 32)
        updates.check_operation(root=root, descriptor=descriptor)
        assert (root / "installer-pending.json").exists()
        with pytest.raises(updates.UpdateError):
            with updates.update_lock(root):
                pytest.fail("ordinary caller cannot borrow an installer identity")
        updates.atomic_json(root / "operation-pending.json", {
            "format": 2, "state": "launching", "owner": os.getpid(), "group": None,
            "boot": updates._current_boot(), "nonce": "c" * 32})
        with pytest.raises(updates.UpdateError):
            updates.check_operation(root=root, descriptor=descriptor)
    finally:
        os.close(descriptor)


def test_outer_installer_completion_keeps_independent_leaf_fence(root, tmp_path):
    installer = tmp_path / "install.sh"
    installer.write_text("#!/bin/sh\nexit 0\n")
    source = ("import os,sys; from pathlib import Path; from jittersim import updates; "
              "root=Path(sys.argv[1]); fd=os.open(root/'lock',os.O_RDWR|os.O_CREAT,0o600); os.dup2(fd,9); "
              "original=updates._completed_command\n"
              "def completed(nonce):\n"
              " updates.atomic_json(root/'operation-pending.json',{'format':2,'state':'launching','owner':os.getpid(),"
              "'group':None,'boot':updates._current_boot(),'nonce':'c'*32})\n"
              " return original(nonce)\n"
              "updates._completed_command=completed\n"
              "updates.run_installer(Path(sys.argv[2]),Path(sys.argv[3]),root=root,descriptor=9,"
              "prepare=lambda *args,**kwargs:None,timeout=2)\n")
    subprocess.run([sys.executable, "-c", source, str(root), str(installer), str(tmp_path)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5)
    assert not (root / "installer-pending.json").exists()
    assert (root / "operation-pending.json").exists()


def test_source_bundle_is_verified_before_executing_a_candidate_module(root):
    with pytest.raises(updates.UpdateError):
        updates.prepare_manual_install(root, descriptor=9)


def test_installer_candidate_module_has_a_fixed_owned_path_before_execution(tmp_path):
    source = tmp_path / "source"
    package = source / "src/jittersim"
    package.mkdir(parents=True)
    target = package / "panic_guard.py"
    target.write_text("raise RuntimeError('must not execute')\n")
    target.chmod(0o666)
    with pytest.raises(updates.UpdateError):
        updates.owned_source_bytes(source, "src/jittersim/panic_guard.py", maximum=1024)


def test_installer_child_rereads_proven_legacy_only_after_physical_writer_stop(root, tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.json"
    units = tmp_path / "units"
    units.mkdir()
    (units / updates.WRITERS[0]).touch()
    updates.atomic_json(legacy, {"current": False})
    calls = []

    class Guard:
        owner_uid = os.geteuid()

        def read(self):
            return None

        def inspect_upgrade(self, path):
            calls.append("inspect")
            return updates.read_json(path)

        def provision_upgrade(self, path):
            calls.append("provision")
            assert updates.read_json(path)["current"] is True, "the parent preview must not be written later"

    module = types.SimpleNamespace(PanicGuard=Guard, PANIC_LEGACY_PATH=legacy)
    monkeypatch.setattr(updates, "_panic_candidate", lambda *args: module)
    monkeypatch.setenv("JITTERSIM_INSTALLER_NONCE", "a" * 32)
    updates.atomic_json(root / "installer-pending.json", {
        "format": 2, "state": "running", "owner": os.getpid(), "group": os.getpgrp(),
        "boot": updates._current_boot(), "nonce": "a" * 32})

    def run(command, timeout):
        calls.append("stop")
        assert command[:2] == ["systemctl", "stop"]
        updates.atomic_json(legacy, {"current": True})

    def query(command, timeout):
        calls.append("proof")
        return "ActiveState=inactive\nMainPID=0\nControlPID=0\nTasksCurrent=0\n"

    descriptor = os.open(root / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        # No upload intake on this machine, so its cleanup runs no command.
        with patch.object(updates, "_link_present", lambda name: False):
            updates.quiesce_manual_install(tmp_path, root=root, descriptor=descriptor,
                                          units=units, run=run, query=query,
                                          config=tmp_path / "no-config")
    finally:
        os.close(descriptor)
    assert calls == ["inspect", "stop", "proof", "provision"]


def test_installer_child_refuses_without_its_outer_group_lease(root, tmp_path):
    descriptor = os.open(root / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with pytest.raises(updates.UpdateError):
            updates.quiesce_manual_install(tmp_path, root=root, descriptor=descriptor)
    finally:
        os.close(descriptor)


def _installer_with_failing_cleanup(root, tmp_path, monkeypatch, failure):
    units = tmp_path / "units"
    units.mkdir()
    for name in updates.WRITERS:
        (units / name).touch()
    calls = []

    class Guard:
        owner_uid = os.geteuid()

        def read(self):
            return {"current": True}

        def provision_upgrade(self, path):
            calls.append(["provision"])

    module = types.SimpleNamespace(PanicGuard=Guard, PANIC_LEGACY_PATH=tmp_path / "legacy.json")
    monkeypatch.setattr(updates, "_panic_candidate", lambda *args: module)
    monkeypatch.setenv("JITTERSIM_INSTALLER_NONCE", "a" * 32)
    updates.atomic_json(root / "installer-pending.json", {
        "format": 2, "state": "running", "owner": os.getpid(), "group": os.getpgrp(),
        "boot": updates._current_boot(), "nonce": "a" * 32})

    def cleanup(*args, **kwargs):
        raise failure

    monkeypatch.setattr(updates, "remove_upload_intake", cleanup)
    descriptor = os.open(root / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with pytest.raises(type(failure)):
            updates.quiesce_manual_install(
                tmp_path, root=root, descriptor=descriptor, units=units,
                run=lambda command, timeout: calls.append(command),
                query=lambda command, timeout: "ActiveState=inactive\nMainPID=0\nControlPID=0\nTasksCurrent=0\n",
                config=tmp_path / "no-config")
    finally:
        os.close(descriptor)
    return calls


def test_installer_restarts_the_unchanged_installation_when_intake_cleanup_fails(root, tmp_path, monkeypatch):
    calls = _installer_with_failing_cleanup(
        root, tmp_path, monkeypatch, updates.UpdateError("tc failed"))
    # install.sh runs under set -eu and stops here: the helper and its Panic must run again.
    assert calls == [["systemctl", "stop", *updates.WRITERS],
                     ["systemctl", "start", *reversed(updates.WRITERS)]]


@pytest.mark.parametrize("failure", [
    updates.CommandNotStopped("tc", Pending()),
    updates.OperationOwnershipUnknown("tc"),
])
def test_installer_never_restarts_over_a_cleanup_command_that_may_still_run(root, tmp_path, monkeypatch, failure):
    calls = _installer_with_failing_cleanup(root, tmp_path, monkeypatch, failure)
    assert calls == [["systemctl", "stop", *updates.WRITERS]]
