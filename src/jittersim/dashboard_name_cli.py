"""Show or change the one dashboard address: `jittersim dashboard-name`."""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

from jittersim import localca, localname, mdnsname, updates

DASHBOARD_PORT = 8080
GROUP = "jittersim"


class NameBudget:
    def __init__(self):
        self.deadline = time.monotonic() + 120

    def check(self):
        if time.monotonic() >= self.deadline:
            raise updates.UpdateError("the dashboard name change exceeded its time allowance")

    def limit(self, seconds):
        self.check()
        return min(seconds, self.deadline - time.monotonic())


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
    run: Optional[Callable] = None,
    euid: Callable[[], int] = os.geteuid,
    config_dir: Optional[Path] = None,
    status_path: Optional[Path] = None,
    ask: Callable[[str], str] = _ask,
    dns_dir: Optional[Path] = None,
    update_root: Optional[Path] = None,
    operation: Optional[Callable] = None,
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

    runner = run or updates.capture_command
    ownership = operation or getattr(updates, "manual_identity_operation", None)
    problems = []
    try:
        if ownership is None:
            raise updates.UpdateError("the installed update manager cannot safely own a name change")
        budget = NameBudget()
        root = update_root or updates.UPDATE_ROOT
        with ownership(root):
            budget.check()
            # Re-read under the operation lock. An update may have changed the
            # identity while the confirmation was open.
            if localname.read_local_name(config / "trust.env") != current:
                raise updates.UpdateError("the dashboard name changed while confirmation was open")
            localca.prepare(config, host_name=name, neutral_name=name,
                            ethernet_ip=localca.detect_ethernet_ip() or None, budget=budget, run=runner)
            localca.set_permissions(config, GROUP)
            budget.check()
            localname.set_local_name(name, config, GROUP)
            localname.install_config(config, dns_dir=dns_dir or localname.SHARED_DNS_DIR, group=GROUP)
            for unit in ("jittersim-mdns.service", "jittersim-dashboard.service"):
                try:
                    runner(["systemctl", "restart", unit], budget.limit(30))
                except updates.CommandNotStopped:
                    raise
                except updates.UpdateError as error:
                    if type(error) is not updates.UpdateError:
                        raise
                    budget.check()
                    problems.append(unit)
            budget.check()
    except updates.CommandNotStopped:
        raise
    except (localca.TrustError, OSError, ValueError, updates.UpdateError) as error:
        if isinstance(error, updates.UpdateError) and type(error) is not updates.UpdateError:
            raise
        print("Dashboard name change did not finish: %s" % error, file=sys.stderr)
        return 1
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
