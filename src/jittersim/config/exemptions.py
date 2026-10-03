"""Validated identities for clients that bypass profile behavior."""

import re
from dataclasses import dataclass

from .device_labels import normalize_device_display_name, normalize_device_mac

MAX_UNAFFECTED_DEVICES = 256


def normalize_exempt_mac(value: object) -> str:
    if isinstance(value, str):
        value = value.replace("-", ":")
    mac = normalize_device_mac(value)
    if mac == "00:00:00:00:00:00":
        raise ValueError("MAC address must identify one device")
    return mac


@dataclass(frozen=True)
class UnaffectedDevice:
    mac_address: str
    display_name: str = ""
    created_at: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "mac_address", normalize_exempt_mac(self.mac_address))
        object.__setattr__(self, "display_name", normalize_device_display_name(self.display_name))
        if (
            isinstance(self.created_at, bool)
            or not isinstance(self.created_at, int)
            or self.created_at < 0
        ):
            raise ValueError("creation time must be a nonnegative integer")

    @property
    def name(self) -> str:
        """The display name, or the address when no name was given."""

        return self.display_name or self.mac_address


MAX_ADDRESS_MOVES = 64


@dataclass(frozen=True)
class AddressMove:
    """A device that came back with a new Wi-Fi address and took over its old entries."""

    old_mac: str
    new_mac: str
    name: str = ""
    moved_access: bool = False
    moved_protection: bool = False
    moved_at: int = 0
    move_id: str = ""
    phase: str = "saved"
    previous_protection_owner: str = ""
    prepared_version: int = -1

    def __post_init__(self) -> None:
        object.__setattr__(self, "old_mac", normalize_exempt_mac(self.old_mac))
        object.__setattr__(self, "new_mac", normalize_exempt_mac(self.new_mac))
        object.__setattr__(self, "name", normalize_device_display_name(self.name))
        if self.old_mac == self.new_mac:
            raise ValueError("A move needs two different addresses")
        if type(self.moved_access) is not bool or type(self.moved_protection) is not bool:
            raise ValueError("A move records what it moved")
        if type(self.moved_at) is not int or self.moved_at < 0:
            raise ValueError("move time must be a nonnegative integer")

        for value in (self.move_id, self.previous_protection_owner):
            if not isinstance(value, str) or value and not re.fullmatch(r"[0-9a-f]{32}", value):
                raise ValueError("move ownership identifier is invalid")
        if self.phase not in ("pending", "saved", "undoing"):
            raise ValueError("move recovery phase is invalid")
        if type(self.prepared_version) is not int or self.prepared_version < -1:
            raise ValueError("move preparation version is invalid")
        if self.phase != "saved" and (not self.move_id or self.prepared_version < 0):
            raise ValueError("pending recovery needs ownership and a saved version")
