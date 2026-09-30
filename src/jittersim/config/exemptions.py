"""Validated identities for clients that bypass profile behavior."""

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
