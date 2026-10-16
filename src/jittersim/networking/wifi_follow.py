"""Keep the Pi Wi-Fi on the router's name and security. Root only. Never contacts the router.

``Copy router Wi-Fi to Pi`` in the dashboard reads the router once and remembers the copy that
started on this Pi (NetworkManager profile ``jittersim-ap-router``). This command uses only
that local copy:

* ``boot`` (jittersim-ap.service, before the start): puts the router copy back in place of any
  other profile unless an operator holds Recovery. ``boot-fallback`` puts the previous profile
  back when the router copy does not start, ``boot-done`` drops it once the Wi-Fi is up.
* ``recovery``: renames the running Wi-Fi to JitterSim-Recovery (same password and security)
  and holds it there across reboots. Use it instead of editing the profile by hand.
* ``router``: ends the hold and switches back to the router copy now.
* ``status``: names the state. It never prints a password.

A manual Wi-Fi change or a name reset in the dashboard forgets the router copy, so nothing
brings it back over the owner's choice.

    sudo /opt/jittersim/.venv/bin/python -m jittersim.networking.wifi_follow status
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .executor import CommandExecutionError, CommandExecutor, SubprocessExecutor
from .wifi import (
    HOLD_PATH,
    RECOVERY_WIFI_NAME,
    SSID_PROPERTY,
    WifiController,
    WifiError,
    matches,
)


def held(hold_path: Path) -> bool:
    return os.path.lexists(hold_path)


def prepare_boot(controller: WifiController, hold_path: Path = HOLD_PATH) -> str:
    return controller.prepare_boot(held(hold_path))


def boot_fallback(controller: WifiController) -> bool:
    return controller.boot_fallback()


def finish_boot(controller: WifiController) -> None:
    controller.finish_boot()


def _write_hold(hold_path: Path) -> None:
    descriptor = os.open(hold_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write("Recovery held by an operator. "
                     "Run `wifi_follow router` or Copy router Wi-Fi to Pi to end it.\n")


def use_recovery(controller: WifiController, hold_path: Path = HOLD_PATH) -> Dict[str, str]:
    """Hold first, so a reboot during the change cannot bring the router name back."""
    _write_hold(hold_path)
    try:
        return controller.rename(RECOVERY_WIFI_NAME)
    except WifiError as error:
        if error.reason != "wifi-restore-failed":
            # The router copy is still running, so a hold would only hide that.
            hold_path.unlink(missing_ok=True)
        raise


def use_router_copy(controller: WifiController, hold_path: Path = HOLD_PATH) -> Dict[str, str]:
    if controller.router_copy() is None:
        raise WifiError("No router copy is remembered. Use Copy router Wi-Fi to Pi once.",
                        reason="not-remembered")
    hold_path.unlink(missing_ok=True)
    return controller.use_router_copy()


def status(controller: WifiController, hold_path: Path = HOLD_PATH) -> Dict[str, Optional[str]]:
    live = controller._settings()
    remembered = controller.router_copy()
    if remembered is None:
        state = "not-remembered"
    elif matches(live, remembered):
        state = "router"
    elif held(hold_path):
        state = "recovery-hold"
    else:
        state = "returns-at-boot"
    return {
        "state": state,
        "wifi": live[SSID_PROPERTY],
        "router_copy": remembered[SSID_PROPERTY] if remembered else None,
        "hold": "yes" if held(hold_path) else "no",
    }


EXPLANATIONS = {
    "router": "The Pi Wi-Fi uses the router's name and security.",
    "recovery-hold": "Recovery is held by an operator. `router` or Copy router Wi-Fi to Pi ends it.",
    "returns-at-boot": "The Pi Wi-Fi differs from the router copy. The next boot or `router` returns to it.",
    "not-remembered": "No router copy is remembered. Use Copy router Wi-Fi to Pi in the dashboard once.",
}


def main(argv: Optional[List[str]] = None, *, executor: Optional[CommandExecutor] = None,
         hold_path: Path = HOLD_PATH) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("action", choices=("status", "recovery", "router",
                                           "boot", "boot-fallback", "boot-done"))
    parser.add_argument("--test-interface",
                        default=os.environ.get("JITTERSIM_TEST_INTERFACE") or "wlan0")
    arguments = parser.parse_args(argv)
    controller = WifiController(executor or SubprocessExecutor(timeout_seconds=15),
                                arguments.test_interface)
    try:
        if arguments.action == "status":
            for key, value in status(controller, hold_path).items():
                print("%s: %s" % (key, value if value is not None else "-"))
            print(EXPLANATIONS[status(controller, hold_path)["state"]])
            return 0
        if arguments.action == "recovery":
            result = use_recovery(controller, hold_path)
            print("Pi Wi-Fi is now %s (%s) and stays there until `router`." % (
                result["ssid"], result["security"]))
            return 0
        if arguments.action == "router":
            result = use_router_copy(controller, hold_path)
            print("Pi Wi-Fi is back on the router copy %s (%s)." % (result["ssid"], result["security"]))
            return 0
        if arguments.action == "boot":
            print("Wi-Fi at boot: %s" % prepare_boot(controller, hold_path))
            return 0
        if arguments.action == "boot-fallback":
            if boot_fallback(controller):
                print("The router copy did not start. The previous Pi Wi-Fi is back.", file=sys.stderr)
                return 0
            return 1
        finish_boot(controller)
        return 0
    except (CommandExecutionError, WifiError, OSError, ValueError) as error:
        # The message is project text. Command output and passwords never reach it.
        message = str(error) if isinstance(error, WifiError) else "the Pi Wi-Fi could not be changed"
        print("Wi-Fi %s: %s" % (arguments.action, message), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
