"""Protect ownership of the stable lock across installer execution."""

import os
import runpy
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/raspberry-pi/install-lock.py"


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

    class CompletedExec(Exception):
        pass

    with patch.dict(main.__globals__, {"acquire_lock": lambda: 9}), \
            patch.object(sys, "argv", [str(SCRIPT), "--installer", str(tmp_path / "install.sh"),
                                       "--source", str(tmp_path)]), \
            patch.object(os, "set_inheritable") as inherit, \
            patch.object(os, "dup2") as duplicate, \
            patch.object(os, "execve", side_effect=CompletedExec):
        with pytest.raises(CompletedExec):
            main()
    inherit.assert_called_once_with(9, True)
    duplicate.assert_not_called()


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


def test_staging_option_survives_actual_locked_shell_reentry(tmp_path):
    scripts = tmp_path / "deploy/raspberry-pi"
    scripts.mkdir(parents=True)
    launcher = scripts / "install-lock.py"
    launcher.write_text(SCRIPT.read_text().replace(
        'LOCK_ROOT = Path("/var/lib/jittersim-updates")',
        "LOCK_ROOT = Path(" + repr(str(tmp_path / "updates")) + ")"))
    (tmp_path / "pyproject.toml").touch()
    (scripts / "create-ap-profile.py").write_text("pass\n")
    prefix = (SCRIPT.parent / "install.sh").read_text().split("\nif ! getent", 1)[0]
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
    assert result.returncode == 0, result.stderr
