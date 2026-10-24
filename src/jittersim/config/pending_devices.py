"""Validated owner choices that have no effect on active network policy."""

from dataclasses import dataclass
from typing import Optional

from .device_labels import normalize_device_display_name
from .exemptions import normalize_exempt_mac
from .models import _validate_identifier

MAX_PENDING_DEVICE_CHOICES = 256
MAX_PENDING_TIME = 253402300799
MAX_PENDING_REVISION = 9223372036854775807
PENDING_DEVICE_SOURCES = ("owner-entered", "imported-neighbour-cache")


def validate_pending_integer(value: object, name: str, maximum: int, minimum: int = 0) -> int:
    """Reject booleans, fractions and values outside the storage bounds."""

    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class PendingDeviceChoice:
    """A prepared choice for a MAC hint, never proof of current identity."""

    mac_address: str
    action: str
    profile_id: Optional[str] = None
    display_name: str = ""
    source: str = "owner-entered"
    observed_at: Optional[int] = None
    saved_at: int = 0
    revision: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "mac_address", normalize_exempt_mac(self.mac_address))
        object.__setattr__(self, "display_name", normalize_device_display_name(self.display_name))
        if self.action not in ("profile", "protect"):
            raise ValueError("pending action must be profile or protect")
        if self.action == "profile":
            _validate_identifier(self.profile_id, "pending profile id")
        elif self.profile_id is not None:
            raise ValueError("pending protection cannot specify a profile")
        if self.source not in PENDING_DEVICE_SOURCES:
            raise ValueError("pending source is invalid")
        if self.observed_at is not None:
            validate_pending_integer(self.observed_at, "observation time", MAX_PENDING_TIME)
        validate_pending_integer(self.saved_at, "saved time", MAX_PENDING_TIME)
        validate_pending_integer(self.revision, "revision", MAX_PENDING_REVISION, 1)
