#!/usr/bin/env python3
"""Refuse to install while a release update is running."""

import subprocess
from pathlib import Path


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


if __name__ == "__main__":
    check_update_idle(Path("/etc/systemd/system"))
