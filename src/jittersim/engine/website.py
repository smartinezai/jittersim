"""Best-effort IPv4 resolution for website destination rules."""

import socket
from collections import OrderedDict
from ipaddress import AddressValueError, IPv4Address
import threading
import time
from typing import Callable, Iterable, Mapping, Optional, Sequence

from jittersim.config.models import Device, Profile, WebsitePolicy

from .plan import WebsiteFilter


AddressResolver = Callable[..., Sequence[tuple]]
_RESOLUTION_CACHE_SECONDS = 60.0
_MAX_CACHED_DOMAINS = 512
_resolution_cache = OrderedDict()
_resolution_cache_lock = threading.Lock()


def _cached_addresses(*, domain: str) -> tuple:
    """Return a short-lived IPv4 DNS result without repeating each plan lookup."""

    now = time.monotonic()
    with _resolution_cache_lock:
        cached = _resolution_cache.get(domain)
        if cached is not None:
            expires_at, addresses = cached
            if expires_at > now:
                _resolution_cache.move_to_end(domain)
                return addresses
            del _resolution_cache[domain]

    addresses = set()
    try:
        results = socket.getaddrinfo(domain, 443, socket.AF_INET, socket.SOCK_STREAM)
    except (OSError, ValueError):
        results = ()
    for result in results:
        try:
            addresses.add(IPv4Address(result[4][0]))
        except (IndexError, KeyError, AddressValueError, ValueError):
            continue
    result_addresses = tuple(sorted(addresses, key=str))
    with _resolution_cache_lock:
        _resolution_cache[domain] = (now + _RESOLUTION_CACHE_SECONDS, result_addresses)
        _resolution_cache.move_to_end(domain)
        while len(_resolution_cache) > _MAX_CACHED_DOMAINS:
            _resolution_cache.popitem(last=False)
    return result_addresses


def resolve_domains(
    *,
    domains: Iterable[str],
    resolver: Optional[AddressResolver] = None,
) -> tuple:
    """Resolve a bounded set of domain names to unique IPv4 addresses."""

    addresses = set()
    for domain in domains:
        if resolver is None:
            addresses.update(_cached_addresses(domain=domain))
            continue
        try:
            results = resolver(domain, 443, socket.AF_INET, socket.SOCK_STREAM)
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
    blocked_domains_by_device: Optional[Mapping[str, Sequence[str]]] = None,
    resolver: Optional[AddressResolver] = None,
) -> tuple:
    """Resolve global and profile domain rules into per-device filters."""

    global_allowed = set(resolve_domains(domains=global_policy.allowed_domains, resolver=resolver))
    global_blocked = set(resolve_domains(domains=global_policy.blocked_domains, resolver=resolver))
    device_blocked_domains = blocked_domains_by_device or {}
    filters = []
    for device in devices:
        profile = profiles[device.profile_id]
        profile_allowed = set(resolve_domains(domains=profile.website_domains, resolver=resolver))
        profile_blocked = set(
            resolve_domains(
                domains=device_blocked_domains.get(device.id, ()),
                resolver=resolver,
            )
        )
        allowed_addresses = None
        if global_policy.mode == "allow_only":
            allowed_addresses = global_allowed
            if profile.website_mode == "allow_only":
                allowed_addresses = allowed_addresses.intersection(profile_allowed)
        elif profile.website_mode == "allow_only":
            allowed_addresses = profile_allowed
        if allowed_addresses is not None or profile_blocked:
            filters.append(
                WebsiteFilter(
                    device_ip=device.ipv4_address,
                    allowed_addresses=tuple(sorted(allowed_addresses or (), key=str)),
                    blocked_addresses=tuple(sorted(profile_blocked, key=str)),
                )
            )
    return tuple(global_blocked), tuple(filters)
