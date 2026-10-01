"""Bounded saved dashboard permissions and verified local device identity."""

import json
import os
import re
import stat
import tempfile
import threading
import time
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any, Dict, List, Optional

ACCESS_FILE = Path("/etc/jittersim/dashboard-access.json")
MAX_ACCESS_DEVICES = 64
MAX_ACCESS_BYTES = 32 * 1024
_MAC = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")


class AccessError(ValueError):
    """Access could not be verified or safely saved."""


def normalize_mac(value: Any) -> str:
    if not isinstance(value, str):
        raise AccessError("Enter a valid device hardware address.")
    value = value.lower()
    if not _MAC.fullmatch(value) or int(value[:2], 16) & 1 or value == "00:00:00:00:00:00":
        raise AccessError("Enter a valid device hardware address.")
    return value


def validate_label(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 64 or any(
        ord(character) < 32 or ord(character) == 127 or 0xd800 <= ord(character) <= 0xdfff
        for character in value
    ):
        raise AccessError("Device name must contain at most 64 printable characters.")
    return value.strip()


class AccessStore:
    """One root-owned file replaced atomically under the helper's lock."""

    def __init__(self, path: Path, *, group_id: Optional[int] = None) -> None:
        self.path = path
        self.group_id = os.getegid() if group_id is None else group_id
        self._lock = threading.RLock()

    def list_devices(self) -> List[Dict[str, Any]]:
        with self._lock:
            try:
                descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                return []
            except OSError as error:
                raise AccessError("Saved dashboard access could not be read.") from error
            try:
                with os.fdopen(descriptor, "rb") as source:
                    details = os.fstat(source.fileno())
                    if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid()
                            or details.st_gid != self.group_id
                            or details.st_mode & 0o137):
                        raise AccessError("Saved dashboard access has unsafe ownership or permissions.")
                    raw = source.read(MAX_ACCESS_BYTES + 1)
                if len(raw) > MAX_ACCESS_BYTES:
                    raise AccessError("Saved dashboard access is too large.")
                data = json.loads(raw)
                if (not isinstance(data, dict) or set(data) != {"version", "devices"}
                        or type(data["version"]) is not int or data["version"] != 1
                        or not isinstance(data["devices"], list)
                        or len(data["devices"]) > MAX_ACCESS_DEVICES):
                    raise ValueError("invalid format")
                devices = data["devices"]
                seen = set()
                for device in devices:
                    if not isinstance(device, dict) or set(device) != {"mac_address", "name", "added_at"}:
                        raise ValueError("invalid entry")
                    mac = normalize_mac(device["mac_address"])
                    if mac != device["mac_address"] or mac in seen:
                        raise ValueError("duplicate or unnormalized address")
                    validate_label(device["name"])
                    if type(device["added_at"]) is not int or device["added_at"] < 0:
                        raise ValueError("invalid timestamp")
                    seen.add(mac)
                return devices
            except (ValueError, UnicodeError) as error:
                raise AccessError("Saved dashboard access is invalid. Use the Pi recovery command.") from error

    def initialize(self) -> None:
        """Create an empty file once and preserve a validated existing list."""
        with self._lock:
            self.list_devices()
            if not self.path.exists():
                self._save([])

    def add(self, mac_address: str, name: str = "") -> List[Dict[str, Any]]:
        mac = normalize_mac(mac_address)
        name = validate_label(name)
        with self._lock:
            devices = self.list_devices()
            if any(device["mac_address"] == mac for device in devices):
                return devices
            if len(devices) >= MAX_ACCESS_DEVICES:
                raise AccessError("Dashboard access is limited to 64 devices. Remove an entry first.")
            devices.append({"mac_address": mac, "name": name, "added_at": int(time.time())})
            self._save(devices)
            return devices

    def remove(self, mac_address: str) -> List[Dict[str, Any]]:
        mac = normalize_mac(mac_address)
        with self._lock:
            devices = self.list_devices()
            remaining = [device for device in devices if device["mac_address"] != mac]
            if remaining != devices:
                self._save(remaining)
            return remaining

    def _save(self, devices: List[Dict[str, Any]]) -> None:
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".dashboard-access-", dir=self.path.parent)
            with os.fdopen(descriptor, "wb") as destination:
                os.fchmod(destination.fileno(), 0o640)
                os.fchown(destination.fileno(), os.geteuid(), self.group_id)
                contents = json.dumps({"version": 1, "devices": devices}, ensure_ascii=False).encode("utf-8")
                if len(contents) > MAX_ACCESS_BYTES:
                    raise AccessError("Saved dashboard access is too large.")
                destination.write(contents)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, self.path)
            temporary = None
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as error:
            raise AccessError("Dashboard access could not be saved. Check the Pi helper and disk.") from error
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)


def identify_peer(applier: Any, peer: str, *, require_reachable: bool = True) -> Dict[str, Any]:
    """Resolve only a direct peer using kernel routes and current Wi-Fi stations."""
    address = IPv4Address(peer)
    if address.is_loopback:
        return {"network": "management", "mac_address": None, "can_register": False}
    route = applier.dashboard_peer_route(str(address))
    interface = route.get("dev")
    if not isinstance(interface, str) or route.get("type", "unicast") != "unicast":
        raise AccessError("The requesting device's network could not be verified.")
    test_peer = interface == applier.test_interface
    result = {"network": "test" if test_peer else "management", "mac_address": None,
              "can_register": False}
    # A routed caller can still use existing management access but cannot enroll a relay.
    if route.get("gateway") or interface not in (applier.test_interface, applier.uplink_interface):
        return result
    neighbors = applier.dashboard_peer_neighbors(str(address), interface)
    matches = [item for item in neighbors if item.get("dst") == str(address)
               and item.get("dev") == interface]
    if len(matches) != 1:
        return result
    state = matches[0].get("state")
    reachable = state == ["REACHABLE"]
    # Saved access can use a valid mapping while Linux refreshes reachability.
    # Adding access still requires a freshly confirmed mapping.
    if not reachable and (require_reachable or state not in (["STALE"], ["DELAY"], ["PROBE"])):
        return result
    mac = normalize_mac(matches[0].get("lladdr"))
    if test_peer:
        station_output = applier.list_connected_stations()
        if len(station_output) > 128 * 1024:
            raise AccessError("Wi-Fi device observations are too large.")
        stations = re.findall(r"^Station ([0-9a-fA-F:]{17})\b",
                              station_output, re.MULTILINE)
        if [value.lower() for value in stations].count(mac) != 1:
            return result
    result.update(mac_address=mac, can_register=reachable)
    return result
