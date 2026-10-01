"""Which clients are protected and so must never be monitored."""

from typing import Any, Iterable, Mapping, Set, Tuple


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
