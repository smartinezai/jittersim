"""Validated interface settings for a test-network gateway."""

from dataclasses import dataclass
import re


class TopologyError(ValueError):
    """Raised when the configured gateway interfaces are unsafe."""


_INTERFACE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")


def _validate_interface(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _INTERFACE_PATTERN.fullmatch(value):
        raise TopologyError(
            f"{field_name} must be a Linux interface name with at most 15 safe characters"
        )
    if value == "lo":
        raise TopologyError(f"{field_name} cannot be the loopback interface")
    return value


@dataclass(frozen=True)
class Topology:
    """The two egress interfaces used by the routed test network."""

    uplink_interface: str
    test_interface: str

    def __post_init__(self) -> None:
        _validate_interface(self.uplink_interface, "uplink_interface")
        _validate_interface(self.test_interface, "test_interface")
        if self.uplink_interface == self.test_interface:
            raise TopologyError("uplink_interface and test_interface must be different")
