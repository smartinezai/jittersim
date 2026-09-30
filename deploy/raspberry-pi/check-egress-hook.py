"""Probe the outgoing Ethernet hook without changing live traffic rules."""

import os
import re
import subprocess
import sys


def main() -> None:
    interface = sys.argv[1]
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,15}", interface):
        raise SystemExit("Invalid test interface")
    table = f"jittersim_probe_{os.getpid()}"
    script = (
        f"add table netdev {table}\n"
        f'add chain netdev {table} probe {{ type filter hook egress device "{interface}" '
        "priority 0; policy accept; }\n"
        f"add rule netdev {table} probe ether daddr 02:00:00:00:00:01 counter\n"
    )
    try:
        result = subprocess.run(
            ["nft", "-f", "-"], input=script, text=True, capture_output=True, timeout=10
        )
        if result.returncode:
            raise SystemExit(
                "Unaffected devices require a kernel with the nftables Ethernet egress hook. "
                + result.stderr
            )
    finally:
        subprocess.run(["nft", "delete", "table", "netdev", table], capture_output=True, timeout=10)


if __name__ == "__main__":
    main()
