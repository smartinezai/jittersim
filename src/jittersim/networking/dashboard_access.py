"""Bounded saved dashboard permissions and verified local device identity.

Two reasons can grant a verified device access. A manual grant is saved in the
root-owned file. A protection grant is derived from the dashboard's protected
list and is only held in memory: it is trusted after the dashboard replaced the
whole set in this helper process, so a restart or rollback can only narrow it.
"""

import json
import os
import re
import stat
import tempfile
import threading
import time
import uuid
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ACCESS_FILE = Path("/etc/jittersim/dashboard-access.json")
MAX_ACCESS_DEVICES = 64
MAX_ACCESS_BYTES = 32 * 1024
# The protected list allows 256 devices. Every one of them can also be granted.
MAX_PROTECTED_ACCESS_DEVICES = 256
MAX_EFFECTIVE_ACCESS_DEVICES = MAX_ACCESS_DEVICES + MAX_PROTECTED_ACCESS_DEVICES
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
    if (
        not isinstance(value, str)
        or len(value) > 64
        or any(
            ord(character) < 32 or ord(character) == 127 or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
    ):
        raise AccessError("Device name must contain at most 64 printable characters.")
    return value.strip()


def is_active(device: Dict[str, Any]) -> bool:
    """A replaced entry is kept for undo but no longer grants access."""
    return "replaced_by" not in device


class AccessStore:
    """One root-owned file replaced atomically under the helper's lock."""

    def __init__(self, path: Path, *, group_id: Optional[int] = None) -> None:
        self.path = path
        self.group_id = os.getegid() if group_id is None else group_id
        self._lock = threading.RLock()
        # None until the dashboard sends the protected set in this process lifetime.
        self._protected: Optional[Dict[str, Dict[str, Any]]] = None
        # Saved grants a dashboard request created and may still take back, by address.
        # Only in memory: after a restart nothing can be taken back, so access is kept.
        self._claims: Dict[str, str] = {}

    @property
    def protected_synced(self) -> bool:
        return self._protected is not None

    def sync_protected(self, devices: Any) -> Dict[str, Any]:
        """Replace the whole protection-derived set. Repeating it is harmless."""
        if not isinstance(devices, list):
            raise AccessError("Protected devices must be sent as one list.")
        if len(devices) > MAX_PROTECTED_ACCESS_DEVICES:
            raise AccessError(
                "Dashboard access from protection is limited to 256 devices."
            )
        names: Dict[str, str] = {}
        for item in devices:
            if not isinstance(item, dict) or "mac_address" not in item or set(item) - {
                "mac_address", "name"
            }:
                raise AccessError("Each protected device needs only its address and name.")
            mac = normalize_mac(item["mac_address"])
            if mac in names:
                raise AccessError("A protected device is listed twice.")
            names[mac] = validate_label(item.get("name", ""))
        with self._lock:
            previous = self._protected or {}
            now = int(time.time())
            self._protected = {
                mac: {
                    "mac_address": mac,
                    "name": name,
                    "added_at": previous.get(mac, {}).get("added_at", now),
                }
                for mac, name in names.items()
            }
        return {"protected_synced": True, "protected_count": len(names)}

    def access_details(
        self, devices: List[Dict[str, Any]], identity: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Name the reasons that grant this verified device access."""
        mac = identity.get("mac_address")
        grants = []
        active = [
            device for device in devices if device["mac_address"] == mac and is_active(device)
        ]
        if mac and any(device.get("manual", True) for device in active):
            grants.append("manual")
        if mac and any(device.get("protected") for device in active):
            grants.append("protection")
        return {"grants": grants, "protected_synced": self.protected_synced}

    def _effective(self, manual: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The union of saved grants and protection grants, one active entry per address."""
        protected = self._protected or {}
        if not protected:
            return manual
        active = {device["mac_address"] for device in manual if is_active(device)}
        devices = [
            dict(device, protected=True)
            if is_active(device) and device["mac_address"] in protected
            else device
            for device in manual
        ]
        devices.extend(
            dict(entry, manual=False, protected=True)
            for mac, entry in protected.items()
            if mac not in active
        )
        return devices

    def list_devices(self) -> List[Dict[str, Any]]:
        with self._lock:
            return self._effective(self._read_saved())

    def saved_devices(self) -> List[Dict[str, Any]]:
        """Only the saved grants, exactly as the root-owned file holds them."""
        return self._read_saved()

    def _read_saved(self) -> List[Dict[str, Any]]:
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
                    if (
                        not stat.S_ISREG(details.st_mode)
                        or details.st_uid != os.geteuid()
                        or details.st_gid != self.group_id
                        or details.st_mode & 0o137
                    ):
                        raise AccessError(
                            "Saved dashboard access has unsafe ownership or permissions."
                        )
                    raw = source.read(MAX_ACCESS_BYTES + 1)
                if len(raw) > MAX_ACCESS_BYTES:
                    raise AccessError("Saved dashboard access is too large.")
                data = json.loads(raw)
                if (
                    not isinstance(data, dict)
                    or set(data) != {"version", "devices"}
                    or type(data["version"]) is not int
                    or data["version"] != 1
                    or not isinstance(data["devices"], list)
                    or len(data["devices"]) > MAX_ACCESS_DEVICES
                ):
                    raise ValueError("invalid format")
                devices = data["devices"]
                seen = set()
                for device in devices:
                    base = {"mac_address", "name", "added_at"}
                    if (
                        not isinstance(device, dict)
                        or not base <= set(device)
                        or set(device)
                        - base
                        - {"replaced_by", "replaced_at", "owner_id", "replacement_id", "undo_id"}
                    ):
                        raise ValueError("invalid entry")
                    for key in ("owner_id", "replacement_id", "undo_id"):
                        if key in device and (
                            not isinstance(device[key], str)
                            or not re.fullmatch(r"[0-9a-f]{32}", device[key])
                        ):
                            raise ValueError("invalid move ownership")
                    if ("replaced_by" in device) != ("replaced_at" in device):
                        raise ValueError("incomplete replacement")
                    if "replacement_id" in device and "replaced_by" not in device:
                        raise ValueError("invalid move replacement")
                    if "undo_id" in device and "replaced_by" in device:
                        raise ValueError("invalid Undo receipt")
                    if "replaced_by" in device:
                        if (
                            normalize_mac(device["replaced_by"]) != device["replaced_by"]
                            or type(device["replaced_at"]) is not int
                            or device["replaced_at"] < 0
                        ):
                            raise ValueError("invalid replacement")
                    mac = normalize_mac(device["mac_address"])
                    if mac != device["mac_address"] or mac in seen:
                        raise ValueError("duplicate or unnormalized address")
                    validate_label(device["name"])
                    if type(device["added_at"]) is not int or device["added_at"] < 0:
                        raise ValueError("invalid timestamp")
                    seen.add(mac)
                return devices
            except (ValueError, UnicodeError) as error:
                raise AccessError(
                    "Saved dashboard access is invalid. Use the Pi recovery command."
                ) from error

    def initialize(self) -> None:
        """Create an empty file once and preserve a validated existing list."""
        with self._lock:
            self._read_saved()
            if not self.path.exists():
                self._save([])

    def add(
        self, mac_address: str, name: str = "", *, claim_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Save a grant. With claim_id, only the request that created it may take it back."""
        mac = normalize_mac(mac_address)
        name = validate_label(name)
        with self._lock:
            devices = self._read_saved()
            if any(device["mac_address"] == mac and is_active(device) for device in devices):
                # Someone else saved or registered it too: it is no longer one request's.
                self._claims.pop(mac, None)
                return self._effective(devices)
            # Registering a replaced address again supersedes its old entry.
            devices = [device for device in devices if device["mac_address"] != mac]
            if len(devices) >= MAX_ACCESS_DEVICES:
                raise AccessError(
                    "Dashboard access is limited to 64 devices. Remove an entry first."
                )
            devices.append({"mac_address": mac, "name": name, "added_at": int(time.time())})
            self._save(devices)
            if claim_id is None:
                self._claims.pop(mac, None)
            else:
                self._claims[mac] = claim_id
            return self._effective(devices)

    def holds_claim(self, mac_address: str, claim_id: str) -> bool:
        with self._lock:
            return self._claims.get(normalize_mac(mac_address)) == claim_id

    def remove_claimed(self, mac_address: str, claim_id: str) -> Tuple[List[Dict[str, Any]], bool]:
        """Take back only the exact grant this claim created and nobody else adopted."""
        mac = normalize_mac(mac_address)
        with self._lock:
            devices = self._read_saved()
            if self._claims.get(mac) != claim_id or not any(
                device["mac_address"] == mac and is_active(device) for device in devices
            ):
                return self._effective(devices), False
            remaining = [device for device in devices if device["mac_address"] != mac]
            self._save(remaining)
            self._claims.pop(mac, None)
            return self._effective(remaining), True

    def move(
        self, old_address: str, new_address: str, *, move_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Give an active entry to a new address and keep the old one marked as replaced."""
        old = normalize_mac(old_address)
        new = normalize_mac(new_address)
        move_id = uuid.uuid4().hex if move_id is None else move_id
        if not isinstance(move_id, str) or not re.fullmatch(r"[0-9a-f]{32}", move_id):
            raise AccessError("Move ownership identifier is invalid.")
        with self._lock:
            # Only a saved grant moves. A protection grant follows the protected list.
            devices = self._read_saved()
            entry = next((item for item in devices if item["mac_address"] == old), None)
            if entry is None or not is_active(entry) or old == new:
                raise AccessError("That device is not on the allowed list.")
            self._claims.pop(old, None)
            self._claims.pop(new, None)
            if any(item["mac_address"] == new and is_active(item) for item in devices):
                raise AccessError("This device is already allowed.")
            # An earlier replaced entry for the new address is superseded by the new one.
            devices = [item for item in devices if item["mac_address"] != new]
            if len(devices) >= MAX_ACCESS_DEVICES:
                raise AccessError(
                    "Dashboard access is limited to 64 devices. Remove an entry first."
                )
            now = int(time.time())
            devices = [
                dict(
                    {key: value for key, value in item.items() if key != "undo_id"},
                    replaced_by=new,
                    replaced_at=now,
                    replacement_id=move_id,
                )
                if item["mac_address"] == old
                else item
                for item in devices
            ]
            devices.append(
                {"mac_address": new, "name": entry["name"], "added_at": now, "owner_id": move_id}
            )
            self._save(devices)
            return self._effective(devices)

    def undo_move(self, old_address: str, *, move_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Reactivate a replaced entry and drop the address that took its place."""
        old = normalize_mac(old_address)
        with self._lock:
            devices = self._read_saved()
            entry = next((item for item in devices if item["mac_address"] == old), None)
            if entry is None or is_active(entry):
                raise AccessError("There is no move to undo for that device.")
            new = entry["replaced_by"]
            self._claims.pop(old, None)
            self._claims.pop(new, None)
            successor = next((item for item in devices if item["mac_address"] == new), None)
            if successor is not None and not is_active(successor):
                raise AccessError("That device moved again. Undo the latest move first.")
            expected = entry.get("replacement_id")
            if (
                not expected
                or move_id is not None
                and move_id != expected
                or successor is None
                or successor.get("owner_id") != expected
            ):
                raise AccessError(
                    "Device ownership changed or cannot be proved. Use management recovery."
                )
            devices = [
                dict(
                    {
                        key: value
                        for key, value in item.items()
                        if key not in ("replaced_by", "replaced_at", "replacement_id", "undo_id")
                    },
                    undo_id=expected,
                )
                if item["mac_address"] == old
                else item
                for item in devices
                if item["mac_address"] != new
            ]
            self._save(devices)
            return self._effective(devices)

    def remove(self, mac_address: str) -> List[Dict[str, Any]]:
        mac = normalize_mac(mac_address)
        with self._lock:
            # Removing a saved grant leaves a protection grant in place.
            devices = self._read_saved()
            self._claims.pop(mac, None)
            remaining = [device for device in devices if device["mac_address"] != mac]
            if remaining != devices:
                self._save(remaining)
            return self._effective(remaining)

    def _save(self, devices: List[Dict[str, Any]]) -> None:
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".dashboard-access-", dir=self.path.parent
            )
            with os.fdopen(descriptor, "wb") as destination:
                os.fchmod(destination.fileno(), 0o640)
                os.fchown(destination.fileno(), os.geteuid(), self.group_id)
                contents = json.dumps(
                    {"version": 1, "devices": devices}, ensure_ascii=False
                ).encode("utf-8")
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
            raise AccessError(
                "Dashboard access could not be saved. Check the Pi helper and disk."
            ) from error
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
    result = {
        "network": "test" if test_peer else "management",
        "mac_address": None,
        "can_register": False,
    }
    # A routed caller can still use existing management access but cannot enroll a relay.
    if route.get("gateway") or interface not in (applier.test_interface, applier.uplink_interface):
        return result
    neighbors = applier.dashboard_peer_neighbors(str(address), interface)
    matches = [
        item
        for item in neighbors
        if item.get("dst") == str(address) and item.get("dev") == interface
    ]
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
        stations = re.findall(r"^Station ([0-9a-fA-F:]{17})\b", station_output, re.MULTILINE)
        if [value.lower() for value in stations].count(mac) != 1:
            return result
    result.update(mac_address=mac, can_register=reachable)
    return result
