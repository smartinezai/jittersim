"""Protect ownership of the stable lock across installer execution."""

import os
import runpy
import signal
import shlex
import subprocess
import sys
import time
import types
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/raspberry-pi/install-lock.py"


def simulated_installer_manager(update_root, manager_path):
    """Use actual ownership while isolating host roles and stop-state provisioning."""
    manager = (SCRIPT.parents[2] / "src/jittersim/updates.py").read_text().replace(
        'UPDATE_ROOT = Path("/var/lib/jittersim-updates")', "UPDATE_ROOT = Path(" + repr(str(update_root)) + ")")
    manager = manager.replace('MANAGER_PATH = Path("/usr/local/lib/jittersim-update.py")',
                              "MANAGER_PATH = Path(" + repr(str(manager_path)) + ")")
    manager = manager.replace("return os.geteuid() == 0", "return True")
    shim = ("def prepare_manual_install(source, *, root=UPDATE_ROOT, descriptor=9):\n"
            "    check_operation(root=root, descriptor=descriptor)\n"
            "def quiesce_manual_install(*args, **kwargs):\n"
            "    check_operation(root=UPDATE_ROOT, descriptor=9)\n\n")
    return manager.replace('if __name__ == "__main__":', shim + 'if __name__ == "__main__":')


def test_busy_anchor_is_preserved_and_a_later_owner_can_take_it(tmp_path):
    module = runpy.run_path(str(SCRIPT))
    descriptor = module["acquire_lock"](tmp_path)
    anchor = tmp_path / "lock"
    inode = anchor.stat().st_ino
    try:
        with pytest.raises(BlockingIOError):
            module["acquire_lock"](tmp_path)
    finally:
        os.close(descriptor)
    descriptor = module["acquire_lock"](tmp_path)
    os.close(descriptor)
    assert anchor.stat().st_ino == inode


def test_spoofed_unlocked_descriptor_cannot_bypass_another_owner(tmp_path):
    module = runpy.run_path(str(SCRIPT))
    holder = module["acquire_lock"](tmp_path)
    spoof = os.open(tmp_path / "lock", os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):
            module["verify_lock"](spoof, tmp_path)
        module["verify_lock"](holder, tmp_path)
    finally:
        os.close(spoof)
        os.close(holder)


def test_foreign_stale_symlink_and_shared_directory_are_refused(tmp_path):
    module = runpy.run_path(str(SCRIPT))
    descriptor = module["acquire_lock"](tmp_path)
    other = tmp_path / "other"
    other.write_text("unrelated")
    foreign = os.open(other, os.O_RDWR)
    try:
        with pytest.raises(module["InstallLockError"]):
            module["verify_lock"](foreign, tmp_path)
    finally:
        os.close(foreign)
        os.close(descriptor)
    with pytest.raises(OSError):
        module["verify_lock"](1000000, tmp_path)
    (tmp_path / "lock").unlink()
    (tmp_path / "lock").symlink_to(other)
    with pytest.raises(OSError):
        module["acquire_lock"](tmp_path)
    tmp_path.chmod(0o755)
    with pytest.raises(module["InstallLockError"]):
        module["acquire_lock"](tmp_path)


def test_descriptor_nine_is_kept_when_acquisition_already_returned_it(tmp_path):
    module = runpy.run_path(str(SCRIPT))
    main = module["main"]

    class CompletedRun(Exception):
        pass

    def supervised(*args, **kwargs):
        assert kwargs["descriptor"] == 9
        closed.assert_not_called()
        raise CompletedRun

    with patch.dict(main.__globals__, {"acquire_lock": lambda: 9,
                                      "load_manager": lambda *args: types.SimpleNamespace(run_installer=supervised)}), \
            patch.object(sys, "argv", [str(SCRIPT), "--installer", str(tmp_path / "install.sh"),
                                       "--source", str(tmp_path)]), \
            patch.object(os, "set_inheritable") as inherit, \
            patch.object(os, "dup2") as duplicate, \
            patch.object(os, "close") as closed:
        with pytest.raises(CompletedRun):
            main()
    inherit.assert_called_once_with(9, True)
    duplicate.assert_not_called()
    closed.assert_called_once_with(9)


def test_inherited_descriptor_holds_lock_through_child_completion(tmp_path):
    module = runpy.run_path(str(SCRIPT))
    descriptor = module["acquire_lock"](tmp_path)
    child = subprocess.Popen(
        [sys.executable, "-c", "import os,sys; os.write(1,b'ready\\n'); sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, pass_fds=(descriptor,),
    )
    try:
        assert child.stdout.readline() == b"ready\n"
        os.close(descriptor)
        with pytest.raises(BlockingIOError):
            module["acquire_lock"](tmp_path)
        child.communicate(timeout=2)
        later = module["acquire_lock"](tmp_path)
        os.close(later)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=2)


@pytest.mark.parametrize("retained", [False, True])
def test_staging_option_and_retained_request_survive_actual_locked_shell_reentry(tmp_path, retained):
    scripts = tmp_path / "deploy/raspberry-pi"
    scripts.mkdir(parents=True)
    launcher = scripts / "install-lock.py"
    launcher.write_text(SCRIPT.read_text().replace(
        'LOCK_ROOT = Path("/var/lib/jittersim-updates")',
        "LOCK_ROOT = Path(" + repr(str(tmp_path / "updates")) + ")"))
    (tmp_path / "pyproject.toml").touch()
    (scripts / "create-ap-profile.py").write_text("pass\n")
    package = tmp_path / "src/jittersim"
    package.mkdir(parents=True)
    update_root = tmp_path / "updates"
    update_root.mkdir(mode=0o700)
    manager = simulated_installer_manager(update_root, tmp_path / "installed-manager.py")
    (package / "updates.py").write_text(manager)
    if retained:
        (update_root / "request.json").write_text('{"rollback": true}')
        (update_root / "request.json").chmod(0o600)
    from tests.release_support import ed25519_line
    config = tmp_path / "config"
    config.mkdir()
    (config / "release-signers").write_text("jittersim-release " + ed25519_line("fixture") + "\n")
    prefix = (SCRIPT.parent / "install.sh").read_text().split("\nif ! getent", 1)[0].replace(
        "CONFIG_DIR=/etc/jittersim", "CONFIG_DIR=" + shlex.quote(str(config)))
    installer = scripts / "install.sh"
    installer.write_text(prefix + '\n[ "$enable_passwordless_staging" = yes ]\n'
                         + '[ "$SOURCE_DIR" = "' + str(tmp_path) + '" ]\n')
    binary = tmp_path / "bin"
    binary.mkdir()
    identity = binary / "id"
    identity.write_text("#!/bin/sh\nprintf '0\\n'\n")
    identity.chmod(0o700)
    result = subprocess.run(["sh", str(installer), "--enable-passwordless-staging", str(tmp_path)],
                            capture_output=True, text=True, timeout=5,
                            env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]})
    if retained:
        assert result.returncode != 0, "retained work must refuse the installer before its first mutation"
        assert "update status" in result.stderr
        assert (update_root / "request.json").read_text() == '{"rollback": true}'
    else:
        assert result.returncode == 0, result.stderr


def test_actual_installer_entry_fences_a_survivor_that_closes_descriptor_nine(tmp_path):
    update_root = tmp_path / "updates"
    update_root.mkdir(mode=0o700)
    launcher = tmp_path / "install-lock.py"
    launcher.write_text(SCRIPT.read_text().replace('LOCK_ROOT = Path("/var/lib/jittersim-updates")',
                                                   "LOCK_ROOT = Path(" + repr(str(update_root)) + ")"))
    package = tmp_path / "src/jittersim"
    package.mkdir(parents=True)
    manager = (SCRIPT.parents[2] / "src/jittersim/updates.py").read_text().replace(
        'UPDATE_ROOT = Path("/var/lib/jittersim-updates")', "UPDATE_ROOT = Path(" + repr(str(update_root)) + ")")
    manager = manager.replace('MANAGER_PATH = Path("/usr/local/lib/jittersim-update.py")',
                              "MANAGER_PATH = Path(" + repr(str(tmp_path / "installed-manager.py")) + ")")
    # This fixture isolates actual launcher ownership. Candidate stop-state
    # provisioning has its separate source and real Linux acceptance proof.
    manager += "\ndef prepare_manual_install(*args, **kwargs):\n    pass\n"
    (package / "updates.py").write_text(manager)
    ready, overlap = tmp_path / "ready", tmp_path / "must-not-overlap"
    installer = tmp_path / "install.sh"
    child = "import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    installer.write_text(shlex.quote(sys.executable) + " -c " + shlex.quote(child) + " "
                         + shlex.quote(str(ready)) + " 9>&- &\nwait\n")
    contender = tmp_path / "contender.sh"
    contender.write_text("touch " + shlex.quote(str(overlap)) + "\n")
    owner = subprocess.Popen([sys.executable, str(launcher), "--installer", str(installer), "--source", str(tmp_path)],
                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    child_pid = group = None
    try:
        until = time.monotonic() + 3
        while not ready.exists():
            assert owner.poll() is None
            assert time.monotonic() < until
            time.sleep(.01)
        child_pid = int(ready.read_text())
        group = os.getpgid(child_pid)
        owner.kill()
        owner.wait(timeout=3)
        if group != owner.pid:
            os.kill(group, signal.SIGKILL)
        time.sleep(.1)
        result = subprocess.run([sys.executable, str(launcher), "--installer", str(contender), "--source", str(tmp_path)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        assert result.returncode != 0 and not overlap.exists(), "a surviving installer child still owns mutable work"
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=3)
        if group is not None:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
