#!/usr/bin/env python3
"""Hold the existing update lock while the manual installer runs."""

import argparse
import fcntl
import os
import stat
import sys
from pathlib import Path

LOCK_ROOT = Path("/var/lib/jittersim-updates")
LOCK_FD = 9
PASSWORD_VARIABLES = ("JITTERSIM_AP_PASSWORD", "ap_password", "jittersim_initial_ap_secret")


class InstallLockError(RuntimeError):
    """Installation ownership could not be established safely."""


def checked_directory(root):
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(descriptor)
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        os.close(descriptor)
        raise InstallLockError("unsafe update directory")
    return descriptor


def check_anchor(descriptor, directory):
    opened = os.fstat(descriptor)
    named = os.stat("lock", dir_fd=directory, follow_symlinks=False)
    if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode)
            or opened.st_uid != os.geteuid() or opened.st_mode & 0o022
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
        raise InstallLockError("unsafe update lock")


def acquire_lock(root=LOCK_ROOT):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = checked_directory(root)
    descriptor = None
    try:
        descriptor = os.open("lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        check_anchor(descriptor, directory)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_anchor(descriptor, directory)
        return descriptor
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise
    finally:
        os.close(directory)


def verify_lock(descriptor=LOCK_FD, root=LOCK_ROOT):
    directory = checked_directory(root)
    try:
        check_anchor(descriptor, directory)
        # This reuses the inherited open file description. It never opens a
        # second anchor and also upgrades a caller-supplied descriptor safely.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_anchor(descriptor, directory)
    finally:
        os.close(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--enable-passwordless-staging", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.verify:
            verify_lock()
            return 0
        if arguments.installer is None or arguments.source is None:
            raise InstallLockError("installer source is required")
        descriptor = acquire_lock()
        if descriptor != LOCK_FD:
            os.dup2(descriptor, LOCK_FD, inheritable=True)
            os.close(descriptor)
        else:
            os.set_inheritable(LOCK_FD, True)
        environment = {key: value for key, value in os.environ.items()
                       if key not in PASSWORD_VARIABLES}
        environment["JITTERSIM_INSTALL_LOCK_FD"] = str(LOCK_FD)
        installer_arguments = ["sh", str(arguments.installer.resolve())]
        if arguments.enable_passwordless_staging:
            installer_arguments.append("--enable-passwordless-staging")
        installer_arguments.append(str(arguments.source.resolve()))
        os.execve("/bin/sh", installer_arguments, environment)
    except (OSError, InstallLockError):
        print("Installation could not obtain safe update ownership. "
              "Use management access to check the update status.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
