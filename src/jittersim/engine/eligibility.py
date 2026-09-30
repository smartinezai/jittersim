"""Select verified clients before any profile behavior is chosen."""

from dataclasses import dataclass, replace
from ipaddress import IPv4Address
from typing import Optional

from jittersim.config.exemptions import normalize_exempt_mac
from jittersim.config.storage import ConfigRepository


@dataclass(frozen=True)
class EligibleClients:
    devices: tuple
    protected_macs: tuple
    excluded_ids: frozenset
    unverified_ids: tuple


def eligible_clients(
    repository: ConfigRepository, snapshot: Optional[dict] = None
) -> EligibleClients:
    protected = tuple(item.mac_address for item in repository.list_unaffected_devices())
    devices = repository.list_devices()
    eligible = []
    excluded = set()
    unverified = []
    by_mac = {}
    if snapshot is not None and snapshot.get("association_status_available") is True:
        for client in snapshot.get("devices", ()):
            if client.get("association_verified") is True:
                try:
                    mac = normalize_exempt_mac(client.get("mac_address"))
                    address = IPv4Address(client.get("ipv4_address"))
                except (TypeError, ValueError):
                    continue
                by_mac.setdefault(mac, []).append(address)
    for device in devices:
        if device.mac_address in protected:
            excluded.add(device.id)
            continue
        if protected and snapshot is not None:
            addresses = by_mac.get(device.mac_address, ())
            if len(addresses) != 1 or any(
                addresses[0] in other for mac, other in by_mac.items() if mac != device.mac_address
            ):
                excluded.add(device.id)
                unverified.append(device.id)
                continue
            device = replace(device, ipv4_address=addresses[0])
        eligible.append(device)
    return EligibleClients(tuple(eligible), protected, frozenset(excluded), tuple(unverified))


def station_snapshot(helper) -> dict:
    """Return an explicit unknown state when live identity cannot be checked."""

    from jittersim.networking.helper import HelperClientError

    try:
        return helper.connected_device_snapshot()
    except (AttributeError, HelperClientError, OSError, TypeError, ValueError):
        return {"devices": [], "association_status_available": False}
