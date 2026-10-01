"""Pick the most useful name for a client from what the Pi knows about it."""

import re
from typing import Optional

UNKNOWN_DEVICE_NAME = "Unknown device"
_AUTOMATIC_NAME = re.compile(r"^Client \d{1,3}(?:\.\d{1,3}){3}$")


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


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
    placeholder counts as no name at all.
    """

    label = _text(saved_label)
    if label:
        return label
    saved = _text(saved_name)
    if saved and not is_automatic_device_name(saved):
        return saved
    return _text(hostname) or UNKNOWN_DEVICE_NAME
