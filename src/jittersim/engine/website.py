"""Best-effort IPv4 resolution for website destination rules."""

import socket
from ipaddress import AddressValueError, IPv4Address
from typing import Callable, Iterable, Optional, Sequence

from jittersim.config.models import Device, Profile, WebsitePolicy

from .plan import WebsiteFilter


AddressResolver = Callable[..., Sequence[tuple]]


def resolve_domains(
    *,
    domains: Iterable[str],
    resolver: Optional[AddressResolver] = None,
) -> tuple:
    """Resolve a bounded set of domain names to unique IPv4 addresses."""

    resolve = socket.getaddrinfo if resolver is None else resolver
    addresses = set()
    for domain in domains:
        try:
            results = resolve(domain, 443, socket.AF_INET, socket.SOCK_STREAM)
        except (OSError, ValueError):
            continue
        for result in results:
            try:
                addresses.add(IPv4Address(result[4][0]))
            except (IndexError, KeyError, AddressValueError, ValueError):
                continue
    return tuple(sorted(addresses, key=str))


def build_website_filters(
    *,
    devices: Sequence[Device],
    profiles: dict,
    global_policy: WebsitePolicy,
    resolver: Optional[AddressResolver] = None,
) -> tuple:
    """Resolve global and profile domain rules into per-device filters."""

    global_allowed = set(resolve_domains(domains=global_policy.allowed_domains, resolver=resolver))
    global_blocked = set(resolve_domains(domains=global_policy.blocked_domains, resolver=resolver))
    filters = []
    for device in devices:
        profile = profiles[device.profile_id]
        profile_allowed = set(resolve_domains(domains=profile.website_domains, resolver=resolver))
        allowed_addresses = None
        if global_policy.mode == "allow_only":
            allowed_addresses = global_allowed
            if profile.website_mode == "allow_only":
                allowed_addresses = allowed_addresses.intersection(profile_allowed)
        elif profile.website_mode == "allow_only":
            allowed_addresses = profile_allowed
        if allowed_addresses is not None:
            filters.append(
                WebsiteFilter(
                    device_ip=device.ipv4_address,
                    allowed_addresses=tuple(sorted(allowed_addresses, key=str)),
                )
            )
    return tuple(global_blocked), tuple(filters)
