"""Keep the Pi Wi-Fi on the router's name and security. Root only. Never contacts the router.

``Copy router Wi-Fi to Pi`` reads the router once and sets the Pi Wi-Fi's name, password and
security. When that worked the name and security are recorded in
``/var/lib/jittersim-wifi/router-copy.json`` (no password). That folder is outside every
update and rollback copy, so a rollback never brings back an older record. Recovery changes only the name, so the
password and security of the router copy stay in the Pi Wi-Fi and going back is a rename:

* ``boot`` (jittersim-ap.service, before the start): renames the saved Wi-Fi back to the
  router name unless an operator holds Recovery. First it looks once at the Wi-Fi names on
  the air. When the router's own radio already sends the router name, the Pi keeps (or
  takes) JitterSim-Recovery for this boot, so two Wi-Fis never carry one name on two
  subnets. When the look fails or finds nothing, the boot behaves as before. A running
  access point is not looked at and keeps its name. ``boot-fallback`` puts the previous
  name back when the new name does not start (never the router name after a rename to
  Recovery), ``boot-done`` ends the boot step.
* ``uplink-check`` (jittersim-ap.service, before the start): refuses the start when the
  Ethernet uplink has an address in the Pi Wi-Fi's own 192.168.50.0/24 (exit status 3).
  Any error passes.
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
from ipaddress import IPv4Interface, IPv4Network
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .commands import Command
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
# What the boot step saw on the air and why it chose the name. For `status`. One boot only.
BOOT_CHECK_PATH = Path("/run/jittersim-wifi-boot-check.json")
MAX_RECORD_BYTES = 1024
NOTHING_TO_RESTORE = 2
# `uplink-check` found the uplink inside the Pi Wi-Fi subnet. Only this code stops the start,
# so a crash or an older module without the action (exit 1 or 2) never keeps the Wi-Fi off.
UPLINK_CONFLICT_EXIT = 3
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


# The look at the air before a boot rename. It never logs in to or changes the router.

# Results of `scan_air`. Only "seen" changes what the boot does. Every other result keeps
# the behaviour from before the look (fail open), so a radio problem never costs the Wi-Fi.
SCAN_SEEN = "seen"            # The router name is on the air from a BSSID that is not the Pi's.
SCAN_NOT_SEEN = "not-seen"    # Other Wi-Fis are on the air, but not the router name.
SCAN_EMPTY = "empty"          # The list was empty.
SCAN_ONLY_OWN = "only-own"    # Only the Pi's own BSSID sends the router name.
SCAN_BUSY = "busy"            # NetworkManager refused because the radio was busy.
SCAN_TIMEOUT = "timeout"      # nmcli did not answer in time.
SCAN_FAILED = "failed"        # Any other error, for example wlan0 not ready yet.
SCAN_SKIPPED = "skipped"      # The Pi Wi-Fi already runs. A running access point is never scanned.

# Seconds. The whole look stays below SCAN_BUDGET, so the boot waits at most about 20 s.
CACHED_TIMEOUT = 4
RESCAN_TIMEOUT = 12
BUSY_WAIT = 2
SCAN_BUDGET = 20
# At a cold boot wlan0 can still be "unavailable" (driver or firmware not ready) and every
# scan is refused. Wait for it in 1 s steps, but at most READY_WAIT of the budget.
READY_WAIT = 10
READY_STEP = 1
MAX_SCAN_BYTES = 65536
MAX_SCAN_LINES = 256
MAC = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}")

Sleep = Callable[[float], None]


def _split_terse(line: str) -> List[str]:
    """Split one `nmcli --terse --escape yes` line at its unescaped colons."""
    fields, current, escaped = [], [], False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


def parse_scan(output: str) -> List[Dict[str, str]]:
    """The (SSID, BSSID) pairs of `nmcli -t -f SSID,BSSID device wifi list`."""
    if len(output.encode("utf-8", "replace")) > MAX_SCAN_BYTES:
        raise ValueError("the Wi-Fi list is too long")
    networks = []
    for line in output.splitlines()[:MAX_SCAN_LINES]:
        fields = _split_terse(line)
        if len(fields) == 2 and MAC.fullmatch(fields[1]):
            networks.append({"ssid": fields[0], "bssid": fields[1].upper()})
    return networks


def _scan_error(error: CommandExecutionError) -> str:
    if error.timed_out:
        return SCAN_TIMEOUT
    text = (error.stderr or "").lower()
    # NetworkManager: "Device or resource busy", "Scanning not allowed immediately following
    # previous scan", or a scan that is still in progress.
    if "busy" in text or "not allowed immediately" in text or "in progress" in text:
        return SCAN_BUSY
    return SCAN_FAILED


def _nmcli(controller: WifiController, arguments, timeout: float) -> str:
    controller.commands_run += 1
    return controller.executor.run(Command(
        executable="nmcli", arguments=tuple(arguments),
        purpose="read the Wi-Fi names on the air (read only)",
        timeout_seconds=max(1, timeout),
    )).stdout


def _own_bssids(controller: WifiController) -> List[str]:
    """The MAC of the Pi's own Wi-Fi device. It is the Pi's BSSID while it sends."""
    try:
        output = _nmcli(controller, ("-g", "GENERAL.HWADDR", "device", "show",
                                     controller.interface), CACHED_TIMEOUT)
    except CommandExecutionError:
        return []
    value = output.strip().replace("\\", "").upper()
    return [value] if MAC.fullmatch(value) else []


def scan_air(controller: WifiController, ssid: str, *, sleep: Sleep = time.sleep,
             clock: Callable[[], float] = time.monotonic) -> Dict[str, str]:
    """Look once whether ``ssid`` is on the air from another radio. Never raises.

    First the list NetworkManager already has (no radio work). When that does not show the
    name, one rescan. A rescan is an active scan: the Pi sends Wi-Fi probe requests and
    nearby access points, the router's too, answer them. Nothing logs in anywhere. When the
    radio is busy the Pi waits briefly and reads the list once more instead of a second scan.
    """
    try:
        return _scan_air(controller, ssid, sleep, clock)
    except Exception:  # noqa: BLE001 - any surprise must keep the boot as it was.
        return {"result": SCAN_FAILED, "detail": "the Wi-Fi list could not be read"}


def _device_state(controller: WifiController) -> str:
    """Like "30 (disconnected)". Empty when nmcli does not know the device yet."""
    try:
        return _nmcli(controller, ("-g", "GENERAL.STATE", "device", "show",
                                   controller.interface), CACHED_TIMEOUT).strip()
    except CommandExecutionError:
        return ""


def _wait_until_available(controller, sleep, clock, start) -> str:
    state = _device_state(controller)
    while (not state or "unavailable" in state) and clock() - start + READY_STEP <= READY_WAIT:
        sleep(READY_STEP)
        state = _device_state(controller)
    return state


def _scan_air(controller, ssid, sleep, clock) -> Dict[str, str]:
    start = clock()
    state = _wait_until_available(controller, sleep, clock, start)
    try:
        active = _nmcli(controller, ("-g", "GENERAL.CONNECTION", "device", "show",
                                     controller.interface), CACHED_TIMEOUT).strip()
    except CommandExecutionError:
        active = ""
    if active == AP_CONNECTION:
        # A scan would take the running access point off its channel for a moment.
        return {"result": SCAN_SKIPPED, "detail": "the Pi Wi-Fi already runs"}
    own = _own_bssids(controller)

    def look(rescan: str, timeout: float) -> Dict[str, str]:
        left = SCAN_BUDGET - (clock() - start)
        if left < 1:
            return {"result": SCAN_TIMEOUT, "detail": "no time was left for the look"}
        try:
            output = _nmcli(controller, ("--terse", "--escape", "yes", "--fields", "SSID,BSSID",
                                         "device", "wifi", "list", "--rescan", rescan,
                                         "ifname", controller.interface), min(timeout, left))
        except CommandExecutionError as error:
            detail = "nmcli %s" % ("timed out" if error.timed_out else "refused the Wi-Fi list")
            if not state or "unavailable" in state:
                detail += " (wlan0 was still %s)" % (state or "not known to NetworkManager")
            return {"result": _scan_error(error), "detail": detail}
        networks = parse_scan(output)
        named = [network["bssid"] for network in networks if network["ssid"] == ssid]
        others = [bssid for bssid in named if bssid not in own]
        if others:
            return {"result": SCAN_SEEN, "bssid": others[0], "count": str(len(networks)),
                    "detail": "the router name is on the air from %s" % others[0]}
        if named:
            return {"result": SCAN_ONLY_OWN, "count": str(len(networks)),
                    "detail": "only the Pi's own radio sends the router name"}
        if not networks:
            return {"result": SCAN_EMPTY, "count": "0", "detail": "no Wi-Fi was on the air"}
        return {"result": SCAN_NOT_SEEN, "count": str(len(networks)),
                "detail": "the router name was not on the air"}

    cached = look("no", CACHED_TIMEOUT)
    if cached["result"] == SCAN_SEEN:
        cached["source"] = "cached list"
        return cached
    fresh = look("yes", RESCAN_TIMEOUT)
    fresh["source"] = "rescan"
    if fresh["result"] == SCAN_BUSY and SCAN_BUDGET - (clock() - start) > BUSY_WAIT + 1:
        # Another scan runs. Its results land in the list NetworkManager keeps.
        sleep(BUSY_WAIT)
        fresh = look("no", CACHED_TIMEOUT)
        fresh["source"] = "list after a busy radio"
    return fresh


def _write_boot_check(path: Path, decision: str, scan: Optional[Dict[str, str]],
                      reason: str) -> None:
    data = {"decision": decision, "reason": reason,
            "at": time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime())}
    if scan is not None:
        data.update({"scan_" + key: value for key, value in scan.items()})
    try:
        _write_json(path, data)
    except OSError:
        pass  # The record only explains the choice. It never stops the boot.


def read_boot_check(path: Path = BOOT_CHECK_PATH) -> Optional[Dict[str, str]]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        with os.fdopen(descriptor, "rb") as source:
            data = json.loads(source.read(MAX_RECORD_BYTES + 1).decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {key: value for key, value in data.items()
            if isinstance(key, str) and isinstance(value, str)}


# Boot. Only the saved name changes. jittersim-ap.service starts the radio afterwards.

def _rename_for_boot(controller: WifiController, current: str, target: str,
                     previous_path: Path, message: str, *, fallback: bool = True) -> None:
    """Rename the saved Wi-Fi. ``fallback``: boot-fallback may put ``current`` back.

    Without it no previous name is kept, so when ``target`` does not start, boot-fallback
    reports nothing to restore and the unit does not start ``current`` either.
    """
    if fallback:
        _write_json(previous_path, {"ssid": current})
    else:
        previous_path.unlink(missing_ok=True)
    try:
        controller.rename_saved(target)
    except (CommandExecutionError, WifiError):
        try:
            controller.rename_saved(current)
            previous_path.unlink(missing_ok=True)
        except (CommandExecutionError, WifiError):
            pass  # boot-fallback tries again after the start failed.
        raise WifiError(message) from None


def prepare_boot(controller: WifiController, record_path: Path = RECORD_PATH,
                 hold_path: Path = HOLD_PATH, previous_path: Path = PREVIOUS_PATH,
                 fingerprint: Fingerprint = password_fingerprint,
                 check_path: Optional[Path] = None,
                 scan: Optional[Callable[[str], Dict[str, str]]] = None) -> str:
    try:
        decision, result, reason = _prepare_boot(controller, record_path, hold_path,
                                                 previous_path, fingerprint, scan)
    except WifiError as error:
        if check_path is not None:
            _write_boot_check(check_path, "rename-failed", None, str(error).rstrip("."))
        raise
    if check_path is not None:
        _write_boot_check(check_path, decision, result, reason)
    return decision


def _prepare_boot(controller, record_path, hold_path, previous_path, fingerprint, scan):
    """(decision, scan result or None, reason)."""
    if controller._backup_exists():
        # The helper restores the interrupted change first.
        return "pending-change", None, "an unfinished Wi-Fi change is restored first"
    record = read_record(record_path)
    if record is None:
        return "not-remembered", None, "no router copy is remembered"
    if held(hold_path):
        return "recovery-hold", None, "an operator holds Recovery"
    live = controller.identity()
    if live["security"] != record["security"]:
        # The password may not be the router's any more. Leave the Wi-Fi as it is.
        return "security-differs", None, "the Pi Wi-Fi security changed since the copy"
    if live["ssid"] != record["ssid"] and not same_password(record, fingerprint):
        # Another password: an older release or a hand edit changed the Wi-Fi.
        return "password-differs", None, "the Pi Wi-Fi password changed since the copy"
    result = (scan or (lambda ssid: scan_air(controller, ssid)))(record["ssid"])
    if result.get("result") == SCAN_SKIPPED:
        # The radio sends a name now (it may be Recovery for a router that is on the air).
        # A saved name that differs from it would come up at the next start unchecked.
        return "ap-running", result, "the Pi Wi-Fi already runs, so its name stays as it is"
    if result.get("result") == SCAN_SEEN:
        reason = ("%s, so the Pi does not send the same name on a second subnet"
                  % result.get("detail", "the router name is on the air"))
        if live["ssid"] != record["ssid"]:
            return "router-on-air", result, reason  # Keep the Pi's own name.
        # No fallback to the router name: while the router sends it, a second round of
        # starts with that name would bring back the two Wi-Fis with one name.
        _rename_for_boot(controller, live["ssid"], RECOVERY_WIFI_NAME, previous_path,
                         "The boot rename to %s failed." % RECOVERY_WIFI_NAME, fallback=False)
        return "router-on-air", result, reason
    reason = "%s, the boot uses the router name" % result.get("detail", "the look found nothing")
    if live["ssid"] == record["ssid"]:
        return "router", result, reason
    _rename_for_boot(controller, live["ssid"], record["ssid"], previous_path,
                     "The boot rename to the router name failed.")
    return "switched", result, reason


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


# The Ethernet uplink must not use the Pi Wi-Fi's own subnet. Two connected routes for one
# subnet break the Pi's own routing, so the Wi-Fi does not start then (install refuses it too).
AP_NETWORK = IPv4Network("192.168.50.0/24")
INTERFACE = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]{0,14}")
UPLINK_OK, UPLINK_CONFLICT, UPLINK_UNKNOWN = "ok", "conflict", "unknown"


def check_uplink(executor: CommandExecutor, uplink: str,
                 test_interface: Optional[str] = None) -> Dict[str, str]:
    """{"result": ok, conflict or unknown, "detail": ...}. Never raises: unknown passes."""
    if not INTERFACE.fullmatch(uplink) or uplink == "lo" or uplink == test_interface:
        return {"result": UPLINK_UNKNOWN, "detail": "the uplink name is not usable"}
    try:
        output = executor.run(Command(
            executable="ip", arguments=("-j", "-4", "address", "show", "dev", uplink),
            purpose="read the Ethernet uplink address (read only)", timeout_seconds=5,
        )).stdout
        conflicts = []
        for link in json.loads(output or "[]"):
            for address in link.get("addr_info", []) if isinstance(link, dict) else []:
                if isinstance(address, dict) and address.get("family") == "inet":
                    network = IPv4Interface("%s/%s" % (address["local"], address["prefixlen"]))
                    if network.network.overlaps(AP_NETWORK):
                        conflicts.append(str(network))
    except Exception:  # noqa: BLE001 - a failed check must never keep the Wi-Fi off.
        return {"result": UPLINK_UNKNOWN, "detail": "the %s address could not be read" % uplink}
    if conflicts:
        return {"result": UPLINK_CONFLICT,
                "detail": "%s has %s, inside the Pi Wi-Fi's own %s" % (
                    uplink, conflicts[0], AP_NETWORK)}
    return {"result": UPLINK_OK, "detail": "%s does not use %s" % (uplink, AP_NETWORK)}


def status(controller: WifiController, record_path: Path = RECORD_PATH,
           hold_path: Path = HOLD_PATH,
           fingerprint: Fingerprint = password_fingerprint,
           check_path: Optional[Path] = None, uplink: Optional[str] = None) -> Dict[str, str]:
    live = controller.identity()
    check = read_boot_check(check_path) if check_path is not None else None
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
        elif check is not None and check.get("decision") in ("router-on-air", "ap-running"):
            state = check["decision"]
        else:
            state = "returns-at-boot"
    result = {
        "state": state,
        "wifi": "%s (%s)" % (live["ssid"], live["security"]),
        "router_copy": "%s (%s)" % (record["ssid"], record["security"]) if record else "-",
        "hold": "yes" if held(hold_path) else "no",
    }
    if check is not None:
        result["boot_decision"] = "%s (%s, %s)" % (
            check.get("decision", "-"), check.get("reason", "-"), check.get("at", "-"))
        if "scan_result" in check:
            result["boot_scan"] = "%s from the %s, %s Wi-Fis listed" % (
                check["scan_result"], check.get("scan_source", "-"), check.get("scan_count", "-"))
    else:
        result["boot_decision"] = "- (no boot step ran since the Pi started)"
    if uplink is not None:
        check_result = check_uplink(controller.executor, uplink, controller.interface)
        result["uplink"] = "%s (%s)" % (check_result["result"], check_result["detail"])
    return result


EXPLANATIONS = {
    "router": "The Pi Wi-Fi uses the router's name and security.",
    "recovery-hold": "Recovery is held by an operator. `router` or Copy router Wi-Fi to Pi ends it.",
    "returns-at-boot": ("The Pi Wi-Fi has another name. The next boot returns to the router name "
                        "unless the router's own Wi-Fi is on the air then. `router` returns now."),
    "router-on-air": ("At this boot the router's own Wi-Fi was on the air with the router name, so "
                      "the Pi kept its own name: two Wi-Fis with one name on two subnets make "
                      "devices switch between them. Switch the router Wi-Fi off and reboot to use "
                      "the router name, or run `router` to use it now anyway."),
    "ap-running": ("The boot step ran while the Pi Wi-Fi was already running, so it left the "
                   "name alone. The next boot looks at the air again. `router` returns now."),
    "not-remembered": "No router copy is remembered. Use Copy router Wi-Fi to Pi in the dashboard once.",
    "security-differs": "The Pi Wi-Fi security changed since the copy. Use Copy router Wi-Fi to Pi again.",
    "record-damaged": "The router copy record is damaged. Use Copy router Wi-Fi to Pi again.",
    "password-differs": "The Pi Wi-Fi password changed since the copy. Use Copy router Wi-Fi to Pi again.",
}


def main(argv: Optional[List[str]] = None, *, executor: Optional[CommandExecutor] = None,
         record_path: Path = RECORD_PATH, hold_path: Path = HOLD_PATH,
         previous_path: Path = PREVIOUS_PATH, lock_path: Path = LOCK_PATH,
         fingerprint: Fingerprint = password_fingerprint,
         check_path: Path = BOOT_CHECK_PATH,
         scan: Optional[Callable[[str], Dict[str, str]]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("action", choices=("status", "recovery", "router", "boot",
                                           "boot-fallback", "boot-done", "uplink-check"))
    parser.add_argument("--test-interface",
                        default=os.environ.get("JITTERSIM_TEST_INTERFACE") or "wlan0")
    parser.add_argument("--uplink", default=os.environ.get("JITTERSIM_UPLINK") or "eth0")
    arguments = parser.parse_args(argv)
    if arguments.action == "uplink-check":
        # Read only and without the Wi-Fi lock: it never changes the Wi-Fi.
        result = check_uplink(executor or SubprocessExecutor(timeout_seconds=5), arguments.uplink,
                              arguments.test_interface)
        if result["result"] == UPLINK_CONFLICT:
            print("The test Wi-Fi does not start: %s. Change the router LAN to another subnet "
                  "(for example 192.168.0.0/24), then reboot the Pi." % result["detail"],
                  file=sys.stderr)
            return UPLINK_CONFLICT_EXIT
        print("Uplink check: %s (%s)." % (result["result"], result["detail"]))
        return 0
    try:
        with wifi_lock(lock_path, wait=45.0):
            return _run(arguments, executor, record_path, hold_path, previous_path, fingerprint,
                        check_path, scan)
    except FollowError as error:
        print("Wi-Fi %s: %s" % (arguments.action, error), file=sys.stderr)
        return 1


def _run(arguments, executor, record_path, hold_path, previous_path, fingerprint,
         check_path, scan) -> int:
    controller = WifiController(executor or SubprocessExecutor(timeout_seconds=15),
                                arguments.test_interface)
    try:
        if arguments.action == "status":
            result = status(controller, record_path, hold_path, fingerprint, check_path,
                            arguments.uplink)
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
            decision = prepare_boot(controller, record_path, hold_path, previous_path, fingerprint,
                                    check_path, scan)
            print("Wi-Fi at boot: %s" % decision)
            check = read_boot_check(check_path)
            if check is not None:
                print("Why: %s." % check.get("reason", "-"))
        elif arguments.action == "boot-fallback":
            if not boot_fallback(controller, previous_path):
                return NOTHING_TO_RESTORE
            print("The new Pi Wi-Fi name did not start. The previous name is back.",
                  file=sys.stderr)
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
