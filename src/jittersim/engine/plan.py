"""Pure data structures for a compiled network policy."""

from dataclasses import dataclass
from enum import Enum
from ipaddress import IPv4Address
from typing import Tuple

from jittersim.config.models import Impairment


class Direction(str, Enum):
    """A direction relative to the controlled test device."""

    UPLOAD = "upload"
    DOWNLOAD = "download"


@dataclass(frozen=True)
class MarkRule:
    """A device address and the mark used to identify its traffic."""

    device_id: str
    ipv4_address: IPv4Address
    mark: int


@dataclass(frozen=True)
class QueueSpec:
    """The impairment queue assigned to one device and direction."""

    device_id: str
    direction: Direction
    mark: int
    impairment: Impairment


@dataclass(frozen=True)
class NetworkPlan:
    """The complete device-specific plan without executable commands."""

    mark_rules: Tuple[MarkRule, ...]
    queues: Tuple[QueueSpec, ...]

