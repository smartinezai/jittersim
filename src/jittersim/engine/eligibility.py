"""Select verified clients before any profile behavior is chosen."""

import time
from dataclasses import dataclass, replace
from ipaddress import IPv4Address
from typing import Dict, Optional

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
        if protected and device.mac_address is None:
            # Identity is by MAC address. Without one the device cannot be told apart
            # from a protected client so it gets no profile until a MAC is saved.
            excluded.add(device.id)
            unverified.append(device.id)
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


# How long the last good client list stands in for a helper that is busy.
STALE_SNAPSHOT_SECONDS = 10.0


def station_snapshot(helper) -> dict:
    """Return the live clients, or the last good list marked stale, or an unknown state.

    A busy helper must not make the scheduler drop every device profile for a tick,
    so a recent good list is reused for a bounded time.
    """

    from jittersim.networking.helper import HelperClientError

    try:
        snapshot = helper.connected_device_snapshot()
    except (AttributeError, HelperClientError, OSError, TypeError, ValueError):
        recent_clients = getattr(helper, "recent_connected_devices", None)
        if callable(recent_clients):
            # The client remembers its last good scan and forgets it when the network changes.
            recent = recent_clients(STALE_SNAPSHOT_SECONDS)
            if isinstance(recent, dict):
                return {**recent, "stale": True}
            return {"devices": [], "association_status_available": False}
        last = getattr(helper, "_last_good_station_snapshot", None)
        if (isinstance(last, tuple) and len(last) == 2
                and 0 <= time.monotonic() - last[0] <= STALE_SNAPSHOT_SECONDS):
            return {**last[1], "stale": True}
        return {"devices": [], "association_status_available": False}
    if not callable(getattr(helper, "recent_connected_devices", None)):
        try:
            helper._last_good_station_snapshot = (time.monotonic(), snapshot)
        except AttributeError:
            pass
    return snapshot


def device_protection_states(
    repository: ConfigRepository, snapshot: Optional[dict] = None
) -> Dict[str, dict]:
    """Say for each saved device whether its profile applies, is paused or is unverified.

    The answer comes from the same check the plan uses so the pages and the network
    always agree. A saved device with no MAC address whose address now belongs to a
    protected client also names that client so the page can offer to link them.
    """

    clients = eligible_clients(repository, snapshot)
    protected = {item.mac_address: item for item in repository.list_unaffected_devices()}
    owners: Dict[str, set] = {}
    if snapshot is not None and snapshot.get("association_status_available") is True:
        for client in snapshot.get("devices", ()):
            if client.get("association_verified") is not True:
                continue
            try:
                mac = normalize_exempt_mac(client.get("mac_address"))
                address = str(IPv4Address(client.get("ipv4_address")))
            except (TypeError, ValueError):
                continue
            owners.setdefault(address, set()).add(mac)
    states = {}
    for device in repository.list_devices():
        if device.mac_address in protected:
            states[device.id] = {
                "effective_state": "protected",
                "effective_note": "On the Protected list. The saved profile is paused "
                "and traffic is not monitored.",
                "protected_match": None,
            }
        elif device.id in clients.excluded_ids:
            match = None
            if device.mac_address is None:
                macs = owners.get(str(device.ipv4_address), set())
                found = sorted(mac for mac in macs if mac in protected)
                if len(macs) == 1 and found:
                    item = protected[found[0]]
                    match = {
                        "mac_address": item.mac_address,
                        "name": item.name,
                    }
            if match is not None:
                note = (
                    "This saved device has no MAC address so JitterSim cannot tell it is "
                    "the protected %s. Its profile is paused until the MAC address is saved."
                    % match["name"]
                )
            elif device.mac_address is None:
                note = (
                    "This saved device has no MAC address so JitterSim cannot check it "
                    "against the Protected list. Its profile is paused until a MAC address "
                    "is saved."
                )
            else:
                note = (
                    "JitterSim cannot confirm where this device is on the test Wi-Fi. "
                    "Its profile is paused until it is seen again."
                )
            states[device.id] = {
                "effective_state": "unverified",
                "effective_note": note,
                "protected_match": match,
            }
        else:
            states[device.id] = {
                "effective_state": "active",
                "effective_note": None,
                "protected_match": None,
            }
    return states
