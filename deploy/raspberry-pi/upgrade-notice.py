#!/usr/bin/env python3
"""Tell the installer's user before an upgrade clears the DNS names under Traffic.

Names saved before JitterSim recorded the hardware address of each device cannot be tied to a
device. When any device is protected the new release clears them once. This script only reads
the database and never fails the installer.
"""

import sqlite3
import sys
from pathlib import Path


def names_to_clear(database):
    if not Path(database).is_file():
        return 0
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    except sqlite3.Error:
        return 0
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(domain_observations)")}
        if not columns or "mac_address" in columns:
            return 0
        if connection.execute("SELECT 1 FROM unaffected_devices LIMIT 1").fetchone() is None:
            return 0
        return connection.execute("SELECT COUNT(*) FROM domain_observations").fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        connection.close()


def main(arguments):
    count = names_to_clear(arguments[0]) if arguments else 0
    if count:
        print(
            f"Notice: this upgrade clears the {count} DNS names listed under Traffic because"
            " protected devices exist. The list fills again as devices browse."
            " Names are kept for 30 days from their last sighting."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
