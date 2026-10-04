"""Supported personal Wi-Fi modes and their NetworkManager settings."""

from typing import Dict, Mapping, Optional

PREFIX = "802-11-wireless-security."
SECURITY_MODES = {
    "WPA": ("wpa-psk", "wpa", "tkip", "tkip", "1"),
    "WPA2": ("wpa-psk", "rsn", "ccmp", "ccmp", "1"),
    "WPA3": ("sae", "rsn", "ccmp", "ccmp", "3"),
    "WPA/WPA2": ("wpa-psk", "wpa,rsn", "tkip,ccmp", "tkip,ccmp", "1"),
    "WPA2/WPA3": ("wpa-psk", "rsn", "ccmp", "ccmp", "2"),
}
SECURITY_PROPERTIES = tuple(PREFIX + name for name in
                            ("key-mgmt", "proto", "pairwise", "group", "pmf"))
LEGACY_MODES = frozenset(("WPA", "WPA/WPA2"))


def security_settings(mode: str) -> Dict[str, str]:
    """Return a complete supported configuration without stale properties."""
    if not isinstance(mode, str) or mode not in SECURITY_MODES:
        raise ValueError("Unsupported personal Wi-Fi security type.")
    return dict(zip(SECURITY_PROPERTIES, SECURITY_MODES[mode]))


def router_security(value: object) -> Optional[str]:
    """Decode known firmware labels without guessing unknown numeric codes."""
    numeric = {4: "WPA2", 5: "WPA/WPA2"}
    if type(value) is int:
        return numeric.get(value)
    if not isinstance(value, str):
        return None
    labels = {
        "4": "WPA2", "5": "WPA/WPA2",
        "WPA-Personal": "WPA", "WPA2-Personal": "WPA2",
        "WPA3-Personal": "WPA3", "WPA-WPA2-Personal": "WPA/WPA2",
        "WPA2-WPA3-Personal": "WPA2/WPA3",
        "WPA3-Personal-Transition": "WPA2/WPA3",
    }
    return labels.get(value)


def saved_security(settings: Mapping[str, str]) -> str:
    """Describe the installed personal mode without reading its password."""
    key = settings[PREFIX + "key-mgmt"]
    protocols = set(settings[PREFIX + "proto"].split(",")) - {""}
    if key == "sae" and protocols == {"rsn"}:
        return "WPA3"
    if key == "wpa-psk":
        if protocols == {"wpa"}:
            return "WPA"
        if not protocols or protocols == {"wpa", "rsn"}:
            return "WPA/WPA2"
        if protocols == {"rsn"}:
            return "WPA2/WPA3" if settings[PREFIX + "pmf"] == "2" else "WPA2"
    raise ValueError("The installed Wi-Fi security type is unsupported.")
