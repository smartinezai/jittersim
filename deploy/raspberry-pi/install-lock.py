#!/usr/bin/env python3
"""Hold the existing update lock while the manual installer runs."""

import argparse
import ast
import fcntl
import os
import stat
import sys
import types
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


def load_manager(source):
    """Load only the bounded owned standalone manager from this candidate."""
    directories = []
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        directories.append(descriptor)
        for name in ("src", "jittersim"):
            descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            directories.append(descriptor)
        for directory in directories:
            info = os.fstat(directory)
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise InstallLockError("unsafe candidate source directory")
        descriptor = os.open("updates.py", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > 512 * 1024):
                raise InstallLockError("unsafe candidate recovery manager")
            raw = handle.read(512 * 1024 + 1)
            after = os.fstat(handle.fileno())
            if len(raw) > 512 * 1024 or (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise InstallLockError("candidate recovery manager changed")
        parsed = ast.parse(raw)
        for name in ("RECOVERY_FORMAT", "OPERATION_FORMAT"):
            if not any(isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
                       and isinstance(node.value, ast.Constant)
                       and type(node.value.value) is int and node.value.value == 2 for node in parsed.body):
                raise InstallLockError("candidate recovery manager is incompatible")
        module = types.ModuleType("_jittersim_installer_updates")
        module.__file__ = str(source / "src/jittersim/updates.py")
        exec(compile(raw, module.__file__, "exec"), module.__dict__)
        return module
    finally:
        for directory in reversed(directories):
            os.close(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--enable-passwordless-staging", action="store_true")
    arguments = parser.parse_args()
    owned = False
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
        owned = True
        for key in PASSWORD_VARIABLES:
            os.environ.pop(key, None)
        manager = load_manager(arguments.source)
        manager.run_installer(arguments.installer.resolve(), arguments.source.resolve(), root=LOCK_ROOT,
                              descriptor=LOCK_FD,
                              enable_passwordless_staging=arguments.enable_passwordless_staging)
        print("Installation finished. Open the dashboard with its saved local name or the Pi address.")
        return 0
    except (OSError, RuntimeError, SyntaxError, ValueError):
        print("Installation could not obtain safe update ownership. "
              "Use management access to check the update status.", file=sys.stderr)
        return 1
    finally:
        if owned:
            os.close(LOCK_FD)


if __name__ == "__main__":
    raise SystemExit(main())
