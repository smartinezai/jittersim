"""Which clients are protected and so must never be monitored."""

from typing import Any, Dict, Iterable, Mapping, Set, Tuple

from .exemptions import normalize_exempt_mac


def protected_macs(repository: Any) -> Tuple[str, ...]:
    """Return the hardware addresses in the Unaffected list in a stable order."""

    return tuple(sorted(item.mac_address for item in repository.list_unaffected_devices()))


def protected_addresses(repository: Any, stations: Iterable[Mapping[str, Any]]) -> Set[str]:
    """Return every IPv4 address currently tied to a protected MAC.

    Live stations show where a protected client is now. Saved devices show where it
    was assigned. Both are used because either can be stale.
    """

    macs = set(protected_macs(repository))
    if not macs:
        return set()
    addresses = {
        str(station["ipv4_address"])
        for station in stations
        if station.get("mac_address") in macs and station.get("ipv4_address")
    }
    for device in repository.list_devices():
        if device.mac_address in macs:
            addresses.add(str(device.ipv4_address))
    return addresses


def station_macs_by_address(stations: Iterable[Mapping[str, Any]]) -> Dict[str, str]:
    """Map each client address to its hardware address when exactly one is known.

    An address that two stations claim at once is left out so no row is attributed to
    the wrong device. Such a row is then matched by its address alone.
    """

    owners: Dict[str, Set[str]] = {}
    for station in stations:
        address = station.get("ipv4_address")
        if not address:
            continue
        try:
            mac = normalize_exempt_mac(station.get("mac_address"))
        except ValueError:
            continue
        owners.setdefault(str(address), set()).add(mac)
    return {address: next(iter(macs)) for address, macs in owners.items() if len(macs) == 1}


def observation_is_protected(observation: Any, macs: Set[str], addresses: Set[str]) -> bool:
    """Tell whether a stored or fresh observation belongs to a protected client.

    The recorded hardware address is the reliable key. The address covers rows saved
    before hardware addresses were recorded.
    """

    return observation.mac_address in macs or str(observation.ipv4_address) in addresses
