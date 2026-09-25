"""Pure data structures for a compiled network policy."""

from dataclasses import dataclass
from enum import Enum
from ipaddress import IPv4Address
from typing import Tuple

from jittersim.config.models import Impairment, MacPolicy


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
class WebsiteFilter:
    """Resolved destination addresses allowed or blocked for one test client."""

    device_ip: IPv4Address
    allowed_addresses: Tuple[IPv4Address, ...]
    blocked_addresses: Tuple[IPv4Address, ...] = ()


@dataclass(frozen=True)
class ServiceQuotaRule:
    """Destination match and fallback queue for one service quota."""

    quota_id: str
    device_id: str
    device_ip: IPv4Address
    destination_addresses: Tuple[IPv4Address, ...]
    mark: int
    upload_impairment: Impairment
    download_impairment: Impairment
    active: bool = False


@dataclass(frozen=True)
class ServiceAssignmentRule:
    """Hostname match and queue for one managed browser assignment."""

    assignment_id: str
    device_id: str
    device_ip: IPv4Address
    domains: Tuple[str, ...]
    mark: int
    upload_impairment: Impairment
    download_impairment: Impairment


@dataclass(frozen=True)
class NetworkPlan:
    """The complete device-specific plan without executable commands."""

    mark_rules: Tuple[MarkRule, ...]
    queues: Tuple[QueueSpec, ...]
    mac_policy: MacPolicy = MacPolicy()
    global_blocked_addresses: Tuple[IPv4Address, ...] = ()
    website_filters: Tuple[WebsiteFilter, ...] = ()
    blocked_device_addresses: Tuple[IPv4Address, ...] = ()
    service_quota_rules: Tuple[ServiceQuotaRule, ...] = ()
    service_assignment_rules: Tuple[ServiceAssignmentRule, ...] = ()
