"""The `jittersim` command.

Commands for your Mac load only the HTTPS client so they start fast and never import the
Pi's networking code or its libraries. Everything else goes to `jittersim.cli`.
"""

import sys
from typing import Optional, Sequence

REMOTE_COMMANDS = ("connect", "profiles", "traffic", "impact", "backup")


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    remote = bool(raw_arguments) and (
        raw_arguments[0] in REMOTE_COMMANDS or raw_arguments[:2] == ["wifi", "show"]
    )
    if raw_arguments[:1] == ["status"]:
        from jittersim.remote_cli import remote_status_wanted

        remote = remote_status_wanted(raw_arguments[1:])
    if remote:
        from jittersim.remote_cli import main as remote_main

        return remote_main(raw_arguments)
    from jittersim.cli import main as pi_main

    return pi_main(raw_arguments)


if __name__ == "__main__":
    sys.exit(main())
