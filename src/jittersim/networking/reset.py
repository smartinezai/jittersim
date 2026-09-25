"""Safely clear a Pi network plan over SSH after stopping the dashboard."""

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from jittersim.config.storage import ConfigRepository, StorageError
from jittersim.networking.helper import HelperClient, HelperClientError


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Clear JitterSim network rules over SSH.")
    parser.add_argument("--database", type=Path, default=Path("/var/lib/jittersim/jittersim.db"))
    parser.add_argument("--helper-socket", type=Path, default=Path("/run/jittersim/helper.sock"))
    arguments = parser.parse_args(argv)
    try:
        dashboard = subprocess.run(
            ["systemctl", "is-active", "--quiet", "jittersim-dashboard.service"],
            check=False,
        )
    except OSError as error:
        print(f"Could not check the dashboard service: {error}", file=sys.stderr)
        return 1
    if dashboard.returncode == 0:
        print("Stop jittersim-dashboard.service before resetting the network.", file=sys.stderr)
        return 1
    if not arguments.database.is_file():
        print("The JitterSim database does not exist.", file=sys.stderr)
        return 1
    try:
        HelperClient(socket_path=arguments.helper_socket).reset()
        repository = ConfigRepository(arguments.database)
        try:
            repository.update_runtime_state(
                observed_state="reset",
                last_action="reset",
                last_result="success",
                last_error=None,
            )
        finally:
            repository.close()
    except (HelperClientError, OSError, sqlite3.Error, StorageError, ValueError) as error:
        print(f"Network reset failed: {error}", file=sys.stderr)
        return 1
    print("JitterSim network rules removed. The dashboard may now be started.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
