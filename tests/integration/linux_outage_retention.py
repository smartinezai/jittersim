"""Guest-only proof of configured rotation and age cleanup after reboot proof."""

import argparse
import hashlib
import json
import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from tests.integration import linux_outage_journal as proof

EVIDENCE = Path("/var/lib/jittersim-outage-retention-proof")
DAY = 86400
MAX_FILES = 64
MAX_COPY = 96 * 1024**2
MAX_FILE = 64 * 1024**2


def _command(arguments, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("The retention proof deadline expired.")
    return proof._run(arguments, timeout=min(10, remaining))


@contextmanager
def guest_clock(evidence, deadline):
    # This guard must run here too so calling the helper cannot bypass it.
    proof._require_disposable()
    if os.environ.get("JITTERSIM_DISPOSABLE_RETENTION_PROOF") != "yes":
        raise RuntimeError("Explicit guest clock permission is required.")
    ntp = _command(["/usr/bin/timedatectl", "show", "--property=NTP", "--value"], deadline).strip()
    if ntp not in (b"yes", b"no"):
        raise RuntimeError("The guest clock policy was not recognized.")
    before, elapsed = time.time(), time.monotonic()
    proof.journal_policy._atomic(evidence / "clock-before.json", json.dumps({
        "realtime": before, "monotonic": elapsed, "ntp_enabled": ntp == b"yes",
        "notice": "Guest only. Discard this VM if the proof is interrupted."
    }).encode())
    try:
        if ntp == b"yes":
            _command(["/usr/bin/timedatectl", "set-ntp", "false"], deadline)
        def advance(days):
            if days not in (2, 8) or time.monotonic() >= deadline:
                raise RuntimeError("The guest clock change is outside the proof bounds.")
            time.clock_settime(time.CLOCK_REALTIME, before + days * DAY + time.monotonic() - elapsed)
        yield advance
    finally:
        # Restoration has its own small reserve even if the proof deadline expired.
        try:
            time.clock_settime(time.CLOCK_REALTIME, before + time.monotonic() - elapsed)
        finally:
            if ntp == b"yes":
                proof._run(["/usr/bin/timedatectl", "set-ntp", "true"], timeout=5)


def _journals(folder):
    if folder.is_symlink() or not folder.is_dir():
        raise RuntimeError("The guest journal directory is unsafe or missing.")
    entries = {}
    for count, path in enumerate(folder.iterdir()):
        if count >= MAX_FILES:
            raise RuntimeError("The guest journal file count exceeds the proof limit.")
        if not path.name.endswith(".journal"):
            continue
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or info.st_size > MAX_FILE):
            raise RuntimeError("A guest journal file exceeds the proof bounds.")
        entries[path.name] = {"inode": info.st_ino, "bytes": info.st_size}
    if sum(item["bytes"] for item in entries.values()) > MAX_COPY:
        raise RuntimeError("The guest journal total exceeds the proof limit.")
    return entries


def _copy_archives(folder, destination, deadline):
    inventory = _journals(folder)
    archive = {name: info for name, info in inventory.items() if "@" in name}
    if not archive:
        raise RuntimeError("There are no archived journals to preserve.")
    free = os.statvfs(destination)
    if free.f_bavail * free.f_frsize < MAX_COPY + 256 * 1024**2:
        raise RuntimeError("There is insufficient space to preserve proof evidence.")
    result = {}
    for name, expected in archive.items():
        descriptor = os.open(folder / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if before.st_ino != expected["inode"] or before.st_size != expected["bytes"]:
                raise RuntimeError("An archived journal changed before copying.")
            digest, copied = hashlib.sha256(), 0
            descriptor = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                while copied < expected["bytes"]:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("The archive-copy deadline expired.")
                    block = source.read(min(1024**2, expected["bytes"] - copied))
                    if not block:
                        raise RuntimeError("An archived journal ended before its recorded size.")
                    target.write(block)
                    digest.update(block)
                    copied += len(block)
                target.flush()
                os.fsync(target.fileno())
            after = os.fstat(source.fileno())
            if (after.st_ino, after.st_size, after.st_mtime_ns) != (before.st_ino, before.st_size, before.st_mtime_ns):
                raise RuntimeError("An archived journal changed while copying.")
        result[name] = {**expected, "sha256": digest.hexdigest()}
    return result


def _marker(deadline):
    operation = proof.diagnostic_records.Operation("reset", "helper")
    operation.finish("succeeded")
    if operation.event is None:
        raise RuntimeError("The retention marker was not created.")
    _command(["/usr/bin/journalctl", "--sync"], deadline)
    if deadline - time.monotonic() < 11:
        raise RuntimeError("There is insufficient time to read the safe marker.")
    payload = proof.diagnostic_export.build_export()
    entries = json.loads(payload)["entries"]
    phases = {item["event"].get("phase") for item in entries
              if item["event"].get("operation") == operation.event["operation"]}
    if phases != {"start", "outcome"}:
        raise RuntimeError("Run the retention proof under the jittersim-helper unit.")
    return operation.event["operation"], payload


def run(source):
    proof._require_disposable()
    if os.environ.get("JITTERSIM_DISPOSABLE_RETENTION_PROOF") != "yes":
        raise RuntimeError("Explicit guest clock permission is required.")
    deadline = time.monotonic() + 120
    reboot = json.loads(proof.journal_policy._read(proof.REBOOT_RESULT, 4096))
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")
    if (reboot.get("phase") != "verified-after-reboot" or reboot.get("source") != source
            or reboot.get("current_boot") != boot or reboot.get("modules") != proof._identity()):
        raise RuntimeError("The same-source reboot proof must pass first.")
    policy = proof.journal_policy
    if not policy._matches_state(policy.CONFIG):
        raise RuntimeError("The project journal policy is not owned or unchanged.")
    configured = policy._read(policy.CONFIG / policy.TARGET_NAME, 4096)
    if any(line not in configured.splitlines() for line in
           (b"Storage=persistent", b"SystemMaxUse=64M", b"SystemMaxFileSize=8M", b"MaxRetentionSec=7day", b"MaxFileSec=1day")):
        raise RuntimeError("The exact production retention settings are required.")
    reserves = [line.split(b"=", 1)[1] for line in configured.splitlines()
                if line.startswith(b"SystemKeepFree=")]
    if len(reserves) != 1 or not re.fullmatch(rb"[0-9]{1,12}", reserves[0]):
        raise RuntimeError("The configured free-space reserve is invalid.")
    reserve = int(reserves[0])
    if not 256 * 1024**2 <= reserve <= 4 * 1024**3:
        raise RuntimeError("The configured free-space reserve is outside the policy bounds.")
    machine = Path("/etc/machine-id").read_text().strip()
    if not re.fullmatch("[0-9a-f]{32}", machine):
        raise RuntimeError("The guest machine identity is invalid.")
    folder = Path("/var/log/journal") / machine
    if folder.parent.is_symlink():
        raise RuntimeError("The guest journal parent is unsafe.")
    EVIDENCE.mkdir(mode=0o700, exist_ok=False)
    policy._atomic(EVIDENCE / "reboot-result.json", json.dumps(reboot).encode())
    policy._atomic(EVIDENCE / "configured-policy.txt", configured)
    _command(["/usr/bin/journalctl", "--sync"], deadline)
    _command(["/usr/bin/journalctl", "--rotate"], deadline)
    preserved = _copy_archives(folder, EVIDENCE, deadline)
    policy._atomic(EVIDENCE / "archive-inventory.json", json.dumps(preserved).encode())
    first, payload = _marker(deadline)
    policy._atomic(EVIDENCE / "before-clock-export.json", payload)
    active = _journals(folder).get("system.journal")
    if active is None:
        raise RuntimeError("The active system journal is unavailable.")
    result = {"phase": "retention-not-yet-proven", "source": source,
              "modules": proof._identity(), "reboot": reboot, "preserved": preserved}
    with guest_clock(EVIDENCE, deadline) as advance:
        advance(2)
        second, payload = _marker(deadline)
        after_rotation = _journals(folder)
        archived = [name for name, info in after_rotation.items()
                    if "@" in name and info["inode"] == active["inode"]]
        if not archived or after_rotation.get("system.journal", {}).get("inode") == active["inode"]:
            raise RuntimeError("Automatic one-day journal rotation did not occur.")
        space = os.statvfs(folder)
        if space.f_bavail * space.f_frsize < reserve + 64 * 1024**2:
            raise RuntimeError("The proof cannot distinguish age cleanup from free-space cleanup.")
        if sum(info["bytes"] for info in after_rotation.values()) >= 64 * 1024**2:
            raise RuntimeError("The proof cannot distinguish age cleanup from size cleanup.")
        policy._atomic(EVIDENCE / "after-rotation-export.json", payload)
        advance(8)
        third, payload = _marker(deadline)
        after_cleanup = _journals(folder)
        if any(name in after_cleanup for name in archived):
            raise RuntimeError("Automatic seven-day cleanup did not remove the old archive.")
        if not all((EVIDENCE / name).is_file() for name in preserved):
            raise RuntimeError("The saved previous journal evidence was lost.")
        policy._atomic(EVIDENCE / "after-cleanup-export.json", payload)
        result.update(phase="verified-rotation-and-age-cleanup", markers=[first, second, third],
                      rotation_archive=archived, after_rotation=after_rotation, after_cleanup=after_cleanup,
                      clock_steps_days=[2, 8], method="automatic journald processing with unchanged seven-day policy")
    result["guest_clock_restored"] = True
    policy._atomic(EVIDENCE / "result.json", json.dumps(result).encode())
    print(json.dumps(result, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    arguments = parser.parse_args()
    if not re.fullmatch("[a-f0-9]{40}", arguments.source):
        raise RuntimeError("Use the exact feature commit identity.")
    run(arguments.source)


if __name__ == "__main__":
    main()
