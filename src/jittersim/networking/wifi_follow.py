"""Keep the Pi Wi-Fi on the router's name and security. Root only. Never contacts the router.

``Copy router Wi-Fi to Pi`` reads the router once and sets the Pi Wi-Fi's name, password and
security. When that worked the name and security are recorded in
``/var/lib/jittersim-wifi/router-copy.json`` (no password). That folder is outside every
update and rollback copy, so a rollback never brings back an older record. Recovery changes only the name, so the
password and security of the router copy stay in the Pi Wi-Fi and going back is a rename:

* ``boot`` (jittersim-ap.service, before the start): renames the saved Wi-Fi back to the
  router name unless an operator holds Recovery. ``boot-fallback`` puts the previous name
  back when the router name does not start, ``boot-done`` ends the boot step.
* ``recovery``: renames the running Wi-Fi to JitterSim-Recovery and holds it there across
  reboots. Use it instead of editing the profile by hand.
* ``router``: ends the hold and renames the running Wi-Fi back to the router name now.
* ``status``: names the state. It never prints a password.

A manual Wi-Fi change or a name reset in the dashboard deletes the record first, so nothing
renames the Wi-Fi over the owner's own choice. The record also keeps a salted HMAC of the copy's
password (computed by openssl from nmcli's output, never seen by Python), so a Wi-Fi whose
security or password changed some other way is never renamed either.

    sudo /opt/jittersim/.venv/bin/python -m jittersim.networking.wifi_follow status
"""

import argparse
import contextlib
import fcntl
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .executor import CommandExecutionError, CommandExecutor, SubprocessExecutor
from .wifi import AP_CONNECTION, RECOVERY_WIFI_NAME, WifiController, WifiError
from .wifi_security import SECURITY_MODES

STATE_DIR = Path("/var/lib/jittersim-wifi")
RECORD_PATH = STATE_DIR / "router-copy.json"
HOLD_PATH = STATE_DIR / "recovery-hold"
# One Pi Wi-Fi change at a time across the helper, this command and the boot step.
LOCK_PATH = Path("/run/jittersim-wifi.lock")
# Lives for one boot only: /run is emptied at every boot.
PREVIOUS_PATH = Path("/run/jittersim-wifi-boot-previous.json")
MAX_RECORD_BYTES = 1024
NOTHING_TO_RESTORE = 2
HEX = re.compile(r"[0-9a-f]{64}")

Fingerprint = Callable[[str], Optional[str]]


SYSTEM_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


def password_fingerprint(salt: str, search_path: str = SYSTEM_PATH) -> Optional[str]:
    """HMAC-SHA256 of the saved Pi Wi-Fi password, keyed with ``salt``, or None.

    nmcli writes the password straight into openssl through a pipe. This process only
    reads the digest, never the password. The record keeps the digest so a boot renames
    only a Wi-Fi whose password is still the router copy's, even after an older release
    without this record changed the Wi-Fi.
    """
    environment = {"PATH": search_path, "HOME": "/dev/null",
                   "XDG_CACHE_HOME": "/dev/null", "LC_ALL": "C"}
    reader = digest = None
    try:
        reader = subprocess.Popen(
            ("nmcli", "--escape", "no", "--show-secrets", "-g", "802-11-wireless-security.psk",
             "connection", "show", "id", AP_CONNECTION),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=environment, close_fds=True)
        digest = subprocess.Popen(
            ("openssl", "dgst", "-sha256", "-hmac", salt),
            stdin=reader.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=environment, close_fds=True)
        reader.stdout.close()
        output, _ = digest.communicate(timeout=10)
        if reader.wait(timeout=5) != 0 or digest.returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        for process in (reader, digest):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
    words = output.decode("ascii", "replace").split()
    return words[-1] if words and HEX.fullmatch(words[-1]) else None


def new_salt() -> str:
    return secrets.token_hex(16)


class FollowError(WifiError):
    """A refused follow step. The Pi Wi-Fi was not changed."""


@contextlib.contextmanager
def wifi_lock(path: Path = LOCK_PATH, wait: float = 30.0):
    """Hold the Pi Wi-Fi lock. Raises FollowError when another change keeps it.

    Without a usable /run (tests, a development machine) the change runs unlocked.
    """
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError:
        yield
        return
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise FollowError("Another Pi Wi-Fi change is running. Try again later.",
                                      reason="wifi-busy") from None
                time.sleep(0.2)
        yield
    finally:
        os.close(descriptor)


def _sync_folder(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json(path: Path, data: Dict[str, str]) -> None:
    """Replace ``path`` atomically with a root-only file."""
    path.parent.mkdir(mode=0o700, exist_ok=True)
    temporary = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(data, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _sync_folder(path.parent)
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
    if data is not None and (
            data.get("security") not in SECURITY_MODES
            or not isinstance(data.get("salt"), str) or not re.fullmatch(r"[0-9a-f]{32}", data["salt"])
            or not isinstance(data.get("password_hmac"), str) or not HEX.fullmatch(data["password_hmac"])):
        raise ValueError("invalid Wi-Fi record")
    return data


def remember(ssid: str, security: str, path: Path = RECORD_PATH,
             fingerprint: Fingerprint = password_fingerprint) -> None:
    """Record the running copy. Raises when its password cannot be fingerprinted."""
    if security not in SECURITY_MODES:
        raise ValueError("unsupported security")
    salt = new_salt()
    digest = fingerprint(salt)
    if digest is None or not HEX.fullmatch(digest):
        raise ValueError("the Wi-Fi password could not be fingerprinted")
    _write_json(path, {"ssid": ssid, "security": security, "salt": salt, "password_hmac": digest})


def same_password(record: Dict[str, str], fingerprint: Fingerprint) -> bool:
    digest = fingerprint(record["salt"])
    return digest is not None and secrets.compare_digest(digest, record["password_hmac"])


def forget(path: Path = RECORD_PATH) -> None:
    """Raises OSError when the record may still exist."""
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _sync_folder(path.parent)


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
                 hold_path: Path = HOLD_PATH, previous_path: Path = PREVIOUS_PATH,
                 fingerprint: Fingerprint = password_fingerprint) -> str:
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
    if not same_password(record, fingerprint):
        # Another password: an older release or a hand edit changed the Wi-Fi.
        return "password-differs"
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
                    hold_path: Path = HOLD_PATH,
                    fingerprint: Fingerprint = password_fingerprint) -> Dict[str, str]:
    _refuse_while_changing(controller)
    record = read_record(record_path)
    if record is None:
        raise FollowError("No router copy is remembered. Use Copy router Wi-Fi to Pi once.",
                          reason="not-remembered")
    if controller.identity()["security"] != record["security"]:
        raise FollowError("The Pi Wi-Fi security changed since the router copy. "
                          "Use Copy router Wi-Fi to Pi again.", reason="security-differs")
    if not same_password(record, fingerprint):
        raise FollowError("The Pi Wi-Fi password changed since the router copy. "
                          "Use Copy router Wi-Fi to Pi again.", reason="password-differs")
    end_hold(hold_path)
    return controller.rename(record["ssid"])


def status(controller: WifiController, record_path: Path = RECORD_PATH,
           hold_path: Path = HOLD_PATH,
           fingerprint: Fingerprint = password_fingerprint) -> Dict[str, str]:
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
        elif not same_password(record, fingerprint):
            state = "password-differs"
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
    "password-differs": "The Pi Wi-Fi password changed since the copy. Use Copy router Wi-Fi to Pi again.",
}


def main(argv: Optional[List[str]] = None, *, executor: Optional[CommandExecutor] = None,
         record_path: Path = RECORD_PATH, hold_path: Path = HOLD_PATH,
         previous_path: Path = PREVIOUS_PATH, lock_path: Path = LOCK_PATH,
         fingerprint: Fingerprint = password_fingerprint) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("action", choices=("status", "recovery", "router",
                                           "boot", "boot-fallback", "boot-done"))
    parser.add_argument("--test-interface",
                        default=os.environ.get("JITTERSIM_TEST_INTERFACE") or "wlan0")
    arguments = parser.parse_args(argv)
    try:
        with wifi_lock(lock_path, wait=45.0):
            return _run(arguments, executor, record_path, hold_path, previous_path, fingerprint)
    except FollowError as error:
        print("Wi-Fi %s: %s" % (arguments.action, error), file=sys.stderr)
        return 1


def _run(arguments, executor, record_path, hold_path, previous_path, fingerprint) -> int:
    controller = WifiController(executor or SubprocessExecutor(timeout_seconds=15),
                                arguments.test_interface)
    try:
        if arguments.action == "status":
            result = status(controller, record_path, hold_path, fingerprint)
            for key, value in result.items():
                print("%s: %s" % (key, value))
            print(EXPLANATIONS[result["state"]])
        elif arguments.action == "recovery":
            result = use_recovery(controller, hold_path)
            print("Pi Wi-Fi is now %s (%s) and stays there until `router`." % (
                result["ssid"], result["security"]))
        elif arguments.action == "router":
            result = use_router_copy(controller, record_path, hold_path, fingerprint)
            print("Pi Wi-Fi is back on the router name %s (%s)." % (result["ssid"], result["security"]))
        elif arguments.action == "boot":
            print("Wi-Fi at boot: %s" % prepare_boot(controller, record_path, hold_path, previous_path,
                                                         fingerprint))
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
