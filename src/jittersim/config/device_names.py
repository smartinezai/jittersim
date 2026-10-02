"""Pick the most useful name for a client from what the Pi knows about it."""

import re
from typing import Optional

UNKNOWN_DEVICE_NAME = "Unknown device"
_AUTOMATIC_NAME = re.compile(r"^Client [0-9]{1,3}(?:\.[0-9]{1,3}){3}$")
# Control characters (C0 and DEL) and the bidirectional controls that can make a
# name read as something else on screen. Emoji and accents are not listed.
_HIDDEN_CHARACTERS = re.compile("[\x00-\x1f\x7f‎‏‪-‮⁦-⁩]")


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def has_hidden_characters(name: str) -> bool:
    """Tell a name that holds a control character or a text direction control."""

    return _HIDDEN_CHARACTERS.search(name) is not None


def _usable(value: object) -> str:
    """Return the trimmed text or an empty string when it cannot be shown as a name."""

    text = _text(value)
    return "" if has_hidden_characters(text) else text


def is_automatic_device_name(name: object) -> bool:
    """Tell the placeholder the dashboard gave a client saved without a name."""

    return _AUTOMATIC_NAME.fullmatch(_text(name)) is not None


def resolve_device_name(
    *,
    saved_label: Optional[str] = None,
    saved_name: Optional[str] = None,
    hostname: Optional[str] = None,
) -> str:
    """Return the best display name.

    A name the user typed beats a hostname the device announces. The automatic
    placeholder counts as no name at all and so does a name that holds a hidden
    control character.
    """

    label = _usable(saved_label)
    if label:
        return label
    saved = _usable(saved_name)
    if saved and not is_automatic_device_name(saved):
        return saved
    return _usable(hostname) or UNKNOWN_DEVICE_NAME


def saved_device_title(
    *, saved_label: Optional[str], saved_name: Optional[str], ipv4_address: object
) -> str:
    """Name a saved device in a list or in a message about it.

    An unnamed device keeps its address so that two of them never look the same.
    """

    name = resolve_device_name(saved_label=saved_label, saved_name=saved_name)
    address = str(ipv4_address or "")
    if name == UNKNOWN_DEVICE_NAME and address:
        return f"{UNKNOWN_DEVICE_NAME} ({address})"
    return name
