"""Show or change the one dashboard address: `jittersim dashboard-name`."""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

from jittersim import localname, mdnsname

DASHBOARD_PORT = 8080
GROUP = "jittersim"


def _address(name: str) -> str:
    return "https://%s:%d" % (name, DASHBOARD_PORT)


def _ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return ""


def _show(name: str, status_path: Path, out: Callable[[str], None]) -> None:
    out("Dashboard address: " + _address(name))
    out("It is the same address on the router network and on the test Wi-Fi.")
    for line in mdnsname.summary_lines(mdnsname.read_status(status_path, name=name)):
        out(line)


def main(
    argv: Sequence[str],
    *,
    run: Callable = subprocess.run,
    euid: Callable[[], int] = os.geteuid,
    config_dir: Optional[Path] = None,
    status_path: Optional[Path] = None,
    ask: Callable[[str], str] = _ask,
) -> int:
    parser = argparse.ArgumentParser(
        prog="jittersim dashboard-name",
        description="Show or change the one neutral address that opens the dashboard on "
                    "the router network and on the test Wi-Fi.")
    parser.add_argument("--config-dir", type=Path, default=localname.CONFIG_DIR)
    parser.add_argument("--status", type=Path, default=mdnsname.STATUS_FILE)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("show", help="print the address and whether it is announced")
    change = commands.add_parser("set", help="change the name (one word followed by .local)")
    change.add_argument("name")
    change.add_argument("--yes", action="store_true", help="change without asking")
    arguments = parser.parse_args(list(argv))
    config = config_dir or arguments.config_dir
    status = status_path or arguments.status
    current = localname.read_local_name(config / "trust.env")

    if arguments.command in (None, "show"):
        _show(current, status, print)
        return 0

    try:
        name = localname.validate_local_name(arguments.name)
    except ValueError as error:
        print("Dashboard name not changed: %s." % error, file=sys.stderr)
        return 2
    if euid() != 0:
        print("Run this with sudo on the Pi: sudo jittersim dashboard-name set %s" % name,
              file=sys.stderr)
        return 1
    if name == current:
        print("The dashboard name is already %s." % name)
        return 0
    if not arguments.yes:
        answer = ask(
            "Change the dashboard address to %s? Devices must use the new address and sign "
            "in once there. Type yes to continue: " % _address(name))
        if answer.strip().lower() != "yes":
            print("Dashboard name not changed.")
            return 0

    python = sys.executable or "python3"
    # The certificate comes first. If it cannot be issued, nothing else changes.
    certificate = run(
        [python, "-m", "jittersim.localca", "ensure", "--config-dir", str(config),
         "--neutral-name", name, "--group", GROUP],
        capture_output=True, text=True, timeout=120)
    if certificate.returncode != 0:
        print("Dashboard name not changed: the certificate for %s could not be issued." % name,
              file=sys.stderr)
        return 1
    try:
        localname.set_local_name(name, config, GROUP)
    except (OSError, ValueError) as error:
        print("Dashboard name not changed: %s" % error, file=sys.stderr)
        return 1

    problems = []
    steps = (
        ([python, "-m", "jittersim.localname", "install", "--config-dir", str(config),
          "--group", GROUP], "the test Wi-Fi resolver record"),
        (["systemctl", "restart", "jittersim-mdns.service"], "jittersim-mdns.service"),
        (["systemctl", "restart", "jittersim-dashboard.service"], "jittersim-dashboard.service"),
    )
    for command, label in steps:
        done = run(command, capture_output=True, text=True, timeout=120)
        if done.returncode != 0:
            problems.append(label)
    print("Dashboard address is now %s." % _address(name))
    print("Use it on the router Wi-Fi and on the test Wi-Fi. Browsers keep a remembered sign-in "
          "for each address, so sign in once at the new address and bookmark it.")
    print("Test Wi-Fi devices that do not use mDNS learn the new name after "
          "'sudo jittersim access apply' and a restart of the test Wi-Fi.")
    if problems:
        print("These steps failed, so the new name may not be live yet: %s. "
              "Run 'sudo jittersim diagnostics' and restart the service." % ", ".join(problems),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
