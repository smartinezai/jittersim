"""Keep the Pi Wi-Fi on the router's name and security. Root only. Never contacts the router.

``Copy router Wi-Fi to Pi`` reads the router once and sets the Pi Wi-Fi's name, password and
security. When that worked the name and security are recorded in
``/etc/jittersim/wifi-router-copy.json`` (no password). Recovery changes only the name, so the
password and security of the router copy stay in the Pi Wi-Fi and going back is a rename:

* ``boot`` (jittersim-ap.service, before the start): renames the saved Wi-Fi back to the
  router name unless an operator holds Recovery. ``boot-fallback`` puts the previous name
  back when the router name does not start, ``boot-done`` ends the boot step.
* ``recovery``: renames the running Wi-Fi to JitterSim-Recovery and holds it there across
  reboots. Use it instead of editing the profile by hand.
* ``router``: ends the hold and renames the running Wi-Fi back to the router name now.
* ``status``: names the state. It never prints a password.

A manual Wi-Fi change or a name reset in the dashboard deletes the record first, so nothing
renames the Wi-Fi over the owner's own choice. A changed security type means the password may
not be the router's any more, so nothing is renamed then either.

    sudo /opt/jittersim/.venv/bin/python -m jittersim.networking.wifi_follow status
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .executor import CommandExecutionError, CommandExecutor, SubprocessExecutor
from .wifi import RECOVERY_WIFI_NAME, WifiController, WifiError
from .wifi_security import SECURITY_MODES

RECORD_PATH = Path("/etc/jittersim/wifi-router-copy.json")
HOLD_PATH = Path("/etc/jittersim/wifi-recovery-hold")
# Lives for one boot only: /run is emptied at every boot.
PREVIOUS_PATH = Path("/run/jittersim-wifi-boot-previous.json")
MAX_RECORD_BYTES = 1024
NOTHING_TO_RESTORE = 2


class FollowError(WifiError):
    """A refused follow step. The Pi Wi-Fi was not changed."""


def _write_json(path: Path, data: Dict[str, str]) -> None:
    """Replace ``path`` atomically with a root-only file."""
    temporary = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(data, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> Optional[Dict[str, str]]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as source:
        raw = source.read(MAX_RECORD_BYTES + 1)
    data = json.loads(raw.decode("utf-8")) if len(raw) <= MAX_RECORD_BYTES else None
    if (not isinstance(data, dict) or not isinstance(data.get("ssid"), str)
            or not 1 <= len(data["ssid"].encode("utf-8")) <= 32
            or any(ord(char) < 32 for char in data["ssid"])):
        raise ValueError("invalid Wi-Fi record")
    return data


def read_record(path: Path = RECORD_PATH) -> Optional[Dict[str, str]]:
    """The recorded router name and security, or None. A damaged record raises ValueError."""
    data = _read_json(path)
    if data is not None and data.get("security") not in SECURITY_MODES:
        raise ValueError("invalid Wi-Fi record")
    return data


def remember(ssid: str, security: str, path: Path = RECORD_PATH) -> None:
    if security not in SECURITY_MODES:
        raise ValueError("unsupported security")
    _write_json(path, {"ssid": ssid, "security": security})


def forget(path: Path = RECORD_PATH) -> None:
    """Raises OSError when the record may still exist."""
    path.unlink(missing_ok=True)


def held(hold_path: Path = HOLD_PATH) -> bool:
    return os.path.lexists(hold_path)


def end_hold(hold_path: Path = HOLD_PATH) -> None:
    hold_path.unlink(missing_ok=True)


def _refuse_while_changing(controller: WifiController) -> None:
    # The helper's safety copy of a running Wi-Fi change. Never race it.
    if controller._backup_exists():
        raise FollowError("A Pi Wi-Fi change is still running or unfinished. "
                          "Wait for it or restart jittersim-helper.service.", reason="busy")


# Boot. Only the saved name changes. jittersim-ap.service starts the radio afterwards.

def prepare_boot(controller: WifiController, record_path: Path = RECORD_PATH,
                 hold_path: Path = HOLD_PATH, previous_path: Path = PREVIOUS_PATH) -> str:
    if controller._backup_exists():
        return "pending-change"  # The helper restores the interrupted change first.
    record = read_record(record_path)
    if record is None:
        return "not-remembered"
    if held(hold_path):
        return "recovery-hold"
    live = controller.identity()
    if live["security"] != record["security"]:
        # The password may not be the router's any more. Leave the Wi-Fi as it is.
        return "security-differs"
    if live["ssid"] == record["ssid"]:
        return "router"
    _write_json(previous_path, {"ssid": live["ssid"]})
    try:
        controller.rename_saved(record["ssid"])
    except (CommandExecutionError, WifiError):
        try:
            controller.rename_saved(live["ssid"])
            previous_path.unlink(missing_ok=True)
        except (CommandExecutionError, WifiError):
            pass  # boot-fallback tries again after the start failed.
        raise WifiError("The boot rename to the router name failed.") from None
    return "switched"


def boot_fallback(controller: WifiController, previous_path: Path = PREVIOUS_PATH) -> bool:
    """Put back the name a boot rename replaced. False when there is none."""
    data = _read_json(previous_path)
    if data is None:
        return False
    controller.rename_saved(data["ssid"])
    previous_path.unlink(missing_ok=True)
    return True


def finish_boot(previous_path: Path = PREVIOUS_PATH) -> None:
    previous_path.unlink(missing_ok=True)


# Operator commands. Both use the checked rename with rollback of a running Wi-Fi.

def use_recovery(controller: WifiController, hold_path: Path = HOLD_PATH) -> Dict[str, str]:
    """Hold first, so a reboot during the change cannot bring the router name back."""
    _refuse_while_changing(controller)
    _write_json(hold_path, {"held_by": "operator"})
    try:
        return controller.rename(RECOVERY_WIFI_NAME)
    except WifiError as error:
        if error.reason == "wifi-restored":
            end_hold(hold_path)  # The router name still runs. A hold would only hide that.
        raise


def use_router_copy(controller: WifiController, record_path: Path = RECORD_PATH,
                    hold_path: Path = HOLD_PATH) -> Dict[str, str]:
    _refuse_while_changing(controller)
    record = read_record(record_path)
    if record is None:
        raise FollowError("No router copy is remembered. Use Copy router Wi-Fi to Pi once.",
                          reason="not-remembered")
    if controller.identity()["security"] != record["security"]:
        raise FollowError("The Pi Wi-Fi security changed since the router copy. "
                          "Use Copy router Wi-Fi to Pi again.", reason="security-differs")
    end_hold(hold_path)
    return controller.rename(record["ssid"])


def status(controller: WifiController, record_path: Path = RECORD_PATH,
           hold_path: Path = HOLD_PATH) -> Dict[str, str]:
    live = controller.identity()
    try:
        record = read_record(record_path)
    except (OSError, ValueError):
        record, state = None, "record-damaged"
    else:
        if record is None:
            state = "not-remembered"
        elif record["security"] != live["security"]:
            state = "security-differs"
        elif record["ssid"] == live["ssid"]:
            state = "router"
        elif held(hold_path):
            state = "recovery-hold"
        else:
            state = "returns-at-boot"
    return {
        "state": state,
        "wifi": "%s (%s)" % (live["ssid"], live["security"]),
        "router_copy": "%s (%s)" % (record["ssid"], record["security"]) if record else "-",
        "hold": "yes" if held(hold_path) else "no",
    }


EXPLANATIONS = {
    "router": "The Pi Wi-Fi uses the router's name and security.",
    "recovery-hold": "Recovery is held by an operator. `router` or Copy router Wi-Fi to Pi ends it.",
    "returns-at-boot": "The Pi Wi-Fi has another name. The next boot or `router` returns to the router name.",
    "not-remembered": "No router copy is remembered. Use Copy router Wi-Fi to Pi in the dashboard once.",
    "security-differs": "The Pi Wi-Fi security changed since the copy. Use Copy router Wi-Fi to Pi again.",
    "record-damaged": "The router copy record is damaged. Use Copy router Wi-Fi to Pi again.",
}


def main(argv: Optional[List[str]] = None, *, executor: Optional[CommandExecutor] = None,
         record_path: Path = RECORD_PATH, hold_path: Path = HOLD_PATH,
         previous_path: Path = PREVIOUS_PATH) -> int:
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
            result = status(controller, record_path, hold_path)
            for key, value in result.items():
                print("%s: %s" % (key, value))
            print(EXPLANATIONS[result["state"]])
        elif arguments.action == "recovery":
            result = use_recovery(controller, hold_path)
            print("Pi Wi-Fi is now %s (%s) and stays there until `router`." % (
                result["ssid"], result["security"]))
        elif arguments.action == "router":
            result = use_router_copy(controller, record_path, hold_path)
            print("Pi Wi-Fi is back on the router name %s (%s)." % (result["ssid"], result["security"]))
        elif arguments.action == "boot":
            print("Wi-Fi at boot: %s" % prepare_boot(controller, record_path, hold_path, previous_path))
        elif arguments.action == "boot-fallback":
            if not boot_fallback(controller, previous_path):
                return NOTHING_TO_RESTORE
            print("The router name did not start. The previous Pi Wi-Fi name is back.", file=sys.stderr)
        else:
            finish_boot(previous_path)
        return 0
    except (CommandExecutionError, WifiError, OSError, ValueError) as error:
        # Project text only. Command output and passwords never reach it.
        message = str(error) if isinstance(error, WifiError) else "the Pi Wi-Fi state could not be used"
        print("Wi-Fi %s: %s" % (arguments.action, message), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
