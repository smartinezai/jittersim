"""Optional, project-owned journal defaults. No networking or package changes."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from pathlib import Path

NAME = "20-jittersim-outage.conf"
TARGET_NAME = "outage-journal.conf"
STATE_NAME = "outage-journal-owner.json"
HEADER = "# JitterSim outage journal defaults. Administrator settings take precedence.\n"
CONFIG_ROOTS = (Path("/usr/lib/systemd"), Path("/usr/local/lib/systemd"), Path("/run/systemd"), Path("/etc/systemd"))
VENDOR_ROOT = Path("/usr/local/lib/systemd/journald.conf.d")
CONFIG = Path("/etc/jittersim-outage-journal")
MAX_CONFIG = 64 * 1024
MAX_FILES = 64
OPTIONS = frozenset(("Storage", "SystemMaxUse", "SystemKeepFree", "SystemMaxFileSize", "SystemMaxFiles",
                     "MaxRetentionSec", "MaxFileSec", "RuntimeMaxUse", "RuntimeKeepFree", "RuntimeMaxFileSize",
                     "RuntimeMaxFiles", "RateLimitIntervalSec", "RateLimitBurst", "SplitMode", "MaxLevelStore"))


class PolicyError(RuntimeError):
    pass


def _read(path, limit=MAX_CONFIG):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022 or info.st_size > limit:
            raise PolicyError("unsafe-configuration")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            raise PolicyError("configuration-limit")
        return raw
    finally:
        os.close(descriptor)


def _directory(path):
    missing = []
    current = path
    for _ in range(32):
        if current.is_symlink():
            raise PolicyError("unsafe-directory")
        if current.exists():
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise PolicyError("unsafe-directory")
            break
        missing.append(current)
        current = current.parent
    else:
        raise PolicyError("directory-limit")
    for directory in reversed(missing):
        directory.mkdir(mode=0o755)


def _atomic(path, raw, mode=0o600):
    _directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".outage-journal-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _parse(raw):
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise PolicyError("ambiguous-configuration") from error
    section = None
    values = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if "\\" in stripped or "\0" in stripped:
            raise PolicyError("ambiguous-configuration")
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            continue
        if "=" not in stripped:
            raise PolicyError("ambiguous-configuration")
        if section == "Journal":
            key, value = stripped.split("=", 1)
            key, value = key.strip(), value.strip()
            if key in OPTIONS:
                values[key] = value
    return values


def _owned_link(link, target):
    try:
        info = link.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) and info.st_uid == os.geteuid() and os.readlink(link) == str(target)


def inspect_policy(roots=CONFIG_ROOTS, target=CONFIG / TARGET_NAME):
    """Reject ambiguous policy instead of overriding an administrator's choice."""
    # Preserve explicit choices even when a newer systemd reads a vendor main file.
    files = [root / "journald.conf" for root in roots
             if (root / "journald.conf").exists() or (root / "journald.conf").is_symlink()]
    effective = {}
    for root in roots:
        directory = root / "journald.conf.d"
        if directory.is_symlink():
            raise PolicyError("unsafe-directory")
        if not directory.exists():
            continue
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise PolicyError("unsafe-directory")
        for count, path in enumerate(directory.iterdir()):
            if count >= MAX_FILES:
                raise PolicyError("configuration-limit")
            if path.name.endswith(".conf"):
                effective[path.name] = path
                if len(effective) > MAX_FILES:
                    raise PolicyError("configuration-limit")
    size = 0
    for path in files + [effective[name] for name in sorted(effective)]:
        if path.is_symlink():
            if os.readlink(path) == "/dev/null":
                if path.name == NAME:
                    raise PolicyError("administrator-mask")
                continue
            if path.name == NAME and _owned_link(path, target):
                continue
            raise PolicyError("unsafe-configuration")
        if path.name == NAME:
            raise PolicyError("administrator-policy")
        raw = _read(path)
        size += len(raw)
        if size > MAX_CONFIG:
            raise PolicyError("configuration-limit")
        if _parse(raw):
            # Even a known explicit value remains the administrator's decision.
            raise PolicyError("administrator-policy")
    return True


def _matches_state(config):
    target = config / TARGET_NAME
    state = config / STATE_NAME
    if not target.exists() and not target.is_symlink() and not state.exists() and not state.is_symlink():
        return True
    try:
        raw = _read(target, 4096)
        receipt = json.loads(_read(state, 1024))
        return (isinstance(receipt, dict) and set(receipt) == {"sha256", "previous_persistent", "activation_pending"}
                and type(receipt["previous_persistent"]) is bool and type(receipt["activation_pending"]) is bool
                and receipt["sha256"] == hashlib.sha256(raw).hexdigest() and raw.startswith(HEADER.encode()))
    except (OSError, ValueError, PolicyError):
        return False


def _activate(deadline):
    for command in (("/usr/bin/systemctl", "restart", "systemd-journald.service"),
                    ("/usr/bin/journalctl", "--flush")):
        remaining = min(2.0, deadline - time.monotonic())
        if remaining <= 0:
            raise PolicyError("activation-deadline")
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=remaining, check=False)
        if result.returncode:
            raise PolicyError("activation-unavailable")


def install(config=CONFIG, roots=CONFIG_ROOTS, vendor=VENDOR_ROOT, disk=Path("/var/log"), activate=_activate):
    target, link = config / TARGET_NAME, vendor / NAME
    deadline = time.monotonic() + 8
    inspect_policy(roots, target)
    if not _matches_state(config):
        raise PolicyError("administrator-policy")
    if (link.exists() or link.is_symlink()) and not _owned_link(link, target):
        raise PolicyError("administrator-policy")
    # A kernel override has separate precedence and must not be reinterpreted.
    cmdline = Path("/proc/cmdline")
    if cmdline.exists():
        with cmdline.open("rb") as handle:
            raw = handle.read(8193)
        if len(raw) > 8192 or b"systemd.journald." in raw:
            raise PolicyError("administrator-policy")
    space = os.statvfs(disk)
    keep_free = max(256 * 1024**2, min(4 * 1024**3, space.f_blocks * space.f_frsize * 15 // 100))
    if space.f_bavail * space.f_frsize < keep_free + 64 * 1024**2:
        raise PolicyError("low-space")
    raw = (HEADER + "[Journal]\nStorage=persistent\nSystemMaxUse=64M\nSystemKeepFree=" + str(keep_free)
           + "\nSystemMaxFileSize=8M\nMaxRetentionSec=7day\nMaxFileSec=1day\nRuntimeMaxUse=8M\nRuntimeMaxFileSize=1M\n").encode()
    _directory(config)
    _directory(vendor)
    previous = json.loads(_read(config / STATE_NAME, 1024)) if (config / STATE_NAME).exists() else None
    prior_persistent = previous["previous_persistent"] if previous else (disk / "journal").is_dir()
    changed = not target.exists() or _read(target, 4096) != raw or not _owned_link(link, target)
    if not changed and not previous["activation_pending"]:
        return "unchanged"
    # An interruption with mismatched files is refused rather than guessed.
    state = {"sha256": hashlib.sha256(raw).hexdigest(), "previous_persistent": prior_persistent,
             "activation_pending": True}
    _atomic(config / STATE_NAME, json.dumps(state).encode())
    _atomic(target, raw)
    if not _owned_link(link, target):
        os.symlink(str(target), link)
    activate(deadline)
    state["activation_pending"] = False
    _atomic(config / STATE_NAME, json.dumps(state).encode())
    return "configured"


def remove(config=CONFIG, vendor=VENDOR_ROOT, activate=_activate, roots=CONFIG_ROOTS):
    target, link = config / TARGET_NAME, vendor / NAME
    if not _owned_link(link, target):
        if link.exists() or link.is_symlink():
            raise PolicyError("administrator-policy")
        return "unchanged"
    if not _matches_state(config):
        raise PolicyError("administrator-policy")
    state = json.loads(_read(config / STATE_NAME, 1024))
    inspect_policy(roots=roots, target=target)
    if state["previous_persistent"]:
        link.unlink()
        for path in (target, config / STATE_NAME):
            path.unlink()
        activate(time.monotonic() + 8)
        return "removed"
    # Keeping old disk evidence leaves the shared directory present. Without this
    # small compatibility setting Storage=auto would keep new logging persistent.
    raw = (HEADER + "[Journal]\nStorage=volatile\nRuntimeMaxUse=8M\nRuntimeMaxFileSize=1M\n").encode()
    if _read(target, 4096) == raw and not state["activation_pending"]:
        return "unchanged"
    state.update(sha256=hashlib.sha256(raw).hexdigest(), activation_pending=True)
    _atomic(config / STATE_NAME, json.dumps(state).encode())
    _atomic(target, raw)
    activate(time.monotonic() + 8)
    state["activation_pending"] = False
    _atomic(config / STATE_NAME, json.dumps(state).encode())
    return "previous-memory-storage-restored"


def check_runtime():
    result = subprocess.run(("/usr/bin/systemctl", "--version"), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2, check=False)
    if result.returncode or len(result.stdout) > 1024:
        raise PolicyError("unsupported-runtime")
    match = re.match(rb"systemd ([0-9]{1,4})(?:\s|$)", result.stdout)
    if match is None or int(match[1]) < 242:
        raise PolicyError("unsupported-runtime")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manage optional project-owned journal defaults.")
    parser.add_argument("action", choices=("install", "remove"))
    parser.add_argument("--config-root", type=Path, default=CONFIG)
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("Outage journal: root required.")
        return 1
    try:
        check_runtime()
        result = (install if args.action == "install" else remove)(config=args.config_root)
    except (PolicyError, OSError, subprocess.SubprocessError, ValueError):
        print("Outage journal: optional setup is unavailable. Administrator overrides and retained logs are kept.")
        return 1
    print("Outage journal: " + result + ".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
