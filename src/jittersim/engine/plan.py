"""Pure data structures for a compiled network policy."""

from dataclasses import dataclass
from enum import Enum
from ipaddress import IPv4Address
from typing import Optional, Tuple

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
    allowed_addresses: Optional[Tuple[IPv4Address, ...]]
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
class ServiceListRule:
    """Hostnames and queue for one service list shared by every affected client.

    The destination addresses are not part of the plan. They change as DNS
    answers change, so the privileged helper keeps them in the list's
    nftables set separately from the plan.
    """

    list_id: str
    domains: Tuple[str, ...]
    mark: int
    upload_impairment: Impairment
    download_impairment: Impairment


def service_list_set_name(rule: "ServiceListRule") -> str:
    """Name the nftables set of destination addresses for one list."""

    return f"svc_{rule.mark}"


GENERAL_QUEUE_ID = "general-profile"


@dataclass(frozen=True)
class GeneralProfileRule:
    """One shared profile for unprotected test-network traffic."""

    profile_id: str
    allowed_addresses: Optional[Tuple[IPv4Address, ...]] = None
    outage_active: bool = False


@dataclass(frozen=True)
class NetworkPlan:
    """The complete device-specific plan without executable commands."""

    mark_rules: Tuple[MarkRule, ...]
    queues: Tuple[QueueSpec, ...]
    protected_mac_addresses: Tuple[str, ...] = ()
    unverified_device_ids: Tuple[str, ...] = ()
    mac_policy: MacPolicy = MacPolicy()
    global_blocked_addresses: Tuple[IPv4Address, ...] = ()
    global_allowed_addresses: Optional[Tuple[IPv4Address, ...]] = None
    website_filters: Tuple[WebsiteFilter, ...] = ()
    blocked_device_addresses: Tuple[IPv4Address, ...] = ()
    service_quota_rules: Tuple[ServiceQuotaRule, ...] = ()
    service_assignment_rules: Tuple[ServiceAssignmentRule, ...] = ()
    general_profile: Optional[GeneralProfileRule] = None
    service_list_rules: Tuple[ServiceListRule, ...] = ()
