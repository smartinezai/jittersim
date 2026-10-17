"""Validation and local hints for connected-device labels."""

import re
from typing import Optional

CONNECTED_DEVICE_TYPES = (
    "unknown",
    "phone",
    "tablet",
    "laptop",
    "desktop",
    "tv_console",
    "other",
)
MAX_SAVED_CONNECTED_DEVICE_LABELS = 256
_MAC_ADDRESS_PATTERN = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


class DeviceLabelError(ValueError):
    """Raised when a saved connected-device label is invalid."""


def normalize_device_mac(value: object) -> str:
    """Return a normalized hardware address or reject it."""

    if not isinstance(value, str):
        raise DeviceLabelError("MAC address must be text")
    normalized = value.strip().lower()
    if not _MAC_ADDRESS_PATTERN.fullmatch(normalized):
        raise DeviceLabelError("MAC address is invalid")
    first_octet = int(normalized[:2], 16)
    if first_octet & 1:
        raise DeviceLabelError("MAC address must identify one device")
    return normalized


def normalize_device_display_name(value: object) -> str:
    """Trim and bound an administrator-entered device name."""

    if not isinstance(value, str):
        raise DeviceLabelError("display name must be text")
    normalized = value.strip()
    if len(normalized) > 64 or _CONTROL_CHARACTER_PATTERN.search(normalized):
        raise DeviceLabelError("display name must be at most 64 characters")
    return normalized


def normalize_device_type(value: object) -> str:
    """Return a supported display category or reject it."""

    if not isinstance(value, str) or value not in CONNECTED_DEVICE_TYPES:
        raise DeviceLabelError("device type is invalid")
    return value


def guess_device_type(hostname: Optional[str]) -> str:
    """Suggest a device category only when its local DHCP name is clear."""

    name = (hostname or "").casefold().replace("_", "-").replace(" ", "-")
    if not name or len(name) > 64:
        return "unknown"
    if any(hint in name for hint in ("ipad", "android-tablet", "pixel-tablet", "tablet")):
        return "tablet"
    if any(hint in name for hint in ("iphone", "pixel-phone", "pixel-", "android-phone")):
        return "phone"
    if any(hint in name for hint in ("macbook", "laptop", "thinkpad", "chromebook")):
        return "laptop"
    if any(hint in name for hint in ("imac", "desktop", "workstation")):
        return "desktop"
    if any(
        hint in name
        for hint in ("apple-tv", "appletv", "roku", "fire-tv", "firetv", "playstation", "xbox")
    ):
        return "tv_console"
    return "unknown"
