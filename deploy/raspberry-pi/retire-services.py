#!/usr/bin/env python3
"""Retire service definitions that the Pi release no longer provides."""

import argparse
import shutil
import subprocess
from pathlib import Path

OBSOLETE_SERVICES = (
    "jittersim-access-boot.service",
    "jittersim-dns.service",
    "jittersim-update.service",
    "jittersim-ap.service",
)


def command(arguments):
    return subprocess.run(
        ["systemctl", *arguments], check=True, timeout=15,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def check_update_idle(units, *, run=subprocess.run):
    if (units / "jittersim-update.service").exists():
        result = run(
            ["systemctl", "is-active", "--quiet", "jittersim-update.service"],
            timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            raise RuntimeError("Wait for the current release update to finish before installing.")
        if result.returncode not in (3, 4):
            raise RuntimeError("Could not check whether a release update is running.")


def clear_failure(name, *, run=subprocess.run):
    result = run(
        ["systemctl", "is-failed", "--quiet", name],
        timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        run(["systemctl", "reset-failed", name], check=True, timeout=15,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif result.returncode not in (1, 4):
        raise RuntimeError("Could not check the retired service's failure state.")


def retire_services(
    units=Path("/etc/systemd/system"),
    backup=Path("/var/lib/jittersim-retired-services"),
    *, run=command, check=check_update_idle, clear=clear_failure,
):
    check(units)
    for name in OBSOLETE_SERVICES:
        source = units / name
        if not source.exists():
            continue
        backup.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup.chmod(0o700)
        destination = backup / name
        if not destination.exists():
            shutil.copyfile(source, destination)
            destination.chmod(0o600)
        # Inactive units can be unloaded. Resetting an unloaded unit fails
        # even when its installed definition still exists.
        clear(name)
        # The old AP unit's stop action disconnects clients. NetworkManager
        # already owns this release's active connection and boot activation.
        flags = ["disable"] if name == "jittersim-ap.service" else ["disable", "--now"]
        run([*flags, name])
        source.unlink()
    run(["daemon-reload"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check_update_idle(Path("/etc/systemd/system"))
    else:
        retire_services()
