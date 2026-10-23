"""Best-effort IPv4 resolution for website destination rules.

A plan is built while the control lock is held, so a DNS server that does not
answer must not hold it for long. Every lookup of one plan shares one deadline
and runs on a few threads. Names that answered in time are kept. The names that
did not are reported and left out of the rules until their lookup works. A
name whose answer expired a short while ago keeps it while a new lookup runs.
"""

import logging
import socket
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import FIRST_COMPLETED, Future
from concurrent.futures import wait as wait_for_futures
from contextlib import contextmanager
from ipaddress import AddressValueError, IPv4Address
from typing import Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence

from jittersim.config.models import MAX_GLOBAL_BLOCKED_DOMAINS, Device, WebsitePolicy

from .lookup_pool import MAX_UNFINISHED_LOOKUPS, BoundedLookupPool
from .plan import WebsiteFilter

LOGGER = logging.getLogger(__name__)
AddressResolver = Callable[..., Sequence[tuple]]
_RESOLUTION_CACHE_SECONDS = 60.0
# Room for a full global block list and the other domain lists. A smaller cache makes every
# plan look up the whole list again.
_MAX_CACHED_DOMAINS = MAX_GLOBAL_BLOCKED_DOMAINS + 2048
# An expired answer is still used for this long while a background lookup renews it, so a
# plan does not wait for names it already knows when many expire together.
_STALE_SECONDS = 600.0
# All the lookups of one plan wait this long together.
RESOLVE_WAIT_SECONDS = 10.0
RESOLVER_THREADS = 8
_lookup_pool = BoundedLookupPool(workers=RESOLVER_THREADS, name="website-dns")
# A name that missed the deadline is not waited for again for this long. Its lookup
# carries on in the background and fills the cache when it ends.
_TIMEOUT_MEMORY_SECONDS = 30.0
_MAX_REMEMBERED_TIMEOUTS = 1024
_MAX_NAMES_IN_MESSAGE = 8
_resolution_cache = OrderedDict()
# Guards the cache and the two dictionaries below.
_resolution_cache_lock = threading.Lock()
# System lookups still running, so a name that is slow is never looked up twice at once.
_pending: Dict[str, Future] = {}
# Names that missed the deadline and the time until which they are not waited for.
_recent_timeouts: Dict[str, float] = {}
_session_state = threading.local()
# The same set of slow names is logged again after this long.
_REPORT_REPEAT_SECONDS = 600.0
_MAX_REPORTED_SETS = 32
# Sets of slow names already logged and the time each was logged. Guarded by the cache lock.
_reported: Dict[tuple, float] = {}


@contextmanager
def without_dns() -> Iterator[None]:
    """Validate saved structure without starting or waiting for external work."""

    previous = getattr(_session_state, "without_dns", False)
    _session_state.without_dns = True
    try:
        yield
    finally:
        _session_state.without_dns = previous


class LookupSession:
    """The shared deadline of one plan and the names that missed it."""

    def __init__(self, seconds: float) -> None:
        self._deadline = time.monotonic() + seconds
        self.timed_out: List[str] = []

    def remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def note_timeout(self, name: str) -> None:
        if name not in self.timed_out:
            self.timed_out.append(name)


@contextmanager
def lookup_session() -> Iterator[LookupSession]:
    """Share one deadline between all lookups made while building one plan.

    A session inside a session joins the outer one. The outermost one logs the
    names that timed out when it ends.
    """

    outer = getattr(_session_state, "session", None)
    if outer is not None:
        yield outer
        return
    session = LookupSession(RESOLVE_WAIT_SECONDS)
    _session_state.session = session
    try:
        yield session
    finally:
        _session_state.session = None
        _report_timeouts(session.timed_out)


def describe_timed_out_names(names: Iterable[str]) -> str:
    """List names for a message, keeping a long list short."""

    names = tuple(names)
    shown = ", ".join(names[:_MAX_NAMES_IN_MESSAGE])
    if len(names) > _MAX_NAMES_IN_MESSAGE:
        shown += f" and {len(names) - _MAX_NAMES_IN_MESSAGE} more"
    return shown


def _report_timeouts(names: Sequence[str]) -> None:
    """Log a set of names that timed out once and again only after a long while.

    Plans are built every second and by more than one thread, so the same names would
    otherwise fill the log. The scheduler and the dashboard share this memory.
    """

    current = tuple(sorted(names))
    if not current:
        return
    now = time.monotonic()
    with _resolution_cache_lock:
        last = _reported.get(current)
        if last is not None and now - last < _REPORT_REPEAT_SECONDS:
            return
        if len(_reported) >= _MAX_REPORTED_SETS:
            for old in [key for key, at in _reported.items() if now - at >= _REPORT_REPEAT_SECONDS]:
                del _reported[old]
            if len(_reported) >= _MAX_REPORTED_SETS:
                _reported.clear()
        _reported[current] = now
    LOGGER.warning(
        "These website names could not be looked up in time and are left out of the "
        "rules until their lookup works: %s",
        describe_timed_out_names(current),
    )


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
            # An expired answer stays until the new one replaces it (see _STALE_SECONDS).

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
        _resolution_cache[domain] = (time.monotonic() + _RESOLUTION_CACHE_SECONDS, result_addresses)
        _resolution_cache.move_to_end(domain)
        while len(_resolution_cache) > _MAX_CACHED_DOMAINS:
            _resolution_cache.popitem(last=False)
    return result_addresses


def _fresh_addresses(domain: str) -> Optional[tuple]:
    """Return a cached answer that has not expired, or None."""

    with _resolution_cache_lock:
        cached = _resolution_cache.get(domain)
        if cached is None or cached[0] <= time.monotonic():
            return None
        return cached[1]


def _stale_entry(domain: str) -> Optional[tuple]:
    """Return (expiry, addresses) of an answer at most _STALE_SECONDS past its expiry, or None."""

    with _resolution_cache_lock:
        cached = _resolution_cache.get(domain)
        if cached is None or cached[0] + _STALE_SECONDS <= time.monotonic():
            return None
        return cached


def _renew(stale: List[tuple]) -> None:
    """Start background lookups for expired answers, the oldest first.

    The oldest first, so every name is renewed in turn however long the list is. Renewals
    leave RESOLVER_THREADS places of the shared pool free for names that have no answer.
    """

    for _, name in sorted(stale):
        if _lookup_pool.unfinished() >= MAX_UNFINISHED_LOOKUPS - RESOLVER_THREADS:
            return
        _start_lookup(name, None)


def _recently_timed_out(domain: str) -> bool:
    with _resolution_cache_lock:
        until = _recent_timeouts.get(domain)
        if until is None:
            return False
        if until <= time.monotonic():
            del _recent_timeouts[domain]
            return False
        return True


def _remember_timeout(domain: str) -> None:
    now = time.monotonic()
    with _resolution_cache_lock:
        if len(_recent_timeouts) >= _MAX_REMEMBERED_TIMEOUTS:
            for name in [name for name, until in _recent_timeouts.items() if until <= now]:
                del _recent_timeouts[name]
            if len(_recent_timeouts) >= _MAX_REMEMBERED_TIMEOUTS:
                _recent_timeouts.clear()
        _recent_timeouts[domain] = now + _TIMEOUT_MEMORY_SECONDS


def _forget_pending(domain: str, future: Future) -> None:
    with _resolution_cache_lock:
        if _pending.get(domain) is future:
            del _pending[domain]


def _system_lookup(domain: str) -> tuple:
    # Looked up here and not bound at import so that a test can replace it.
    return _cached_addresses(domain=domain)


def _lookup_with(resolver: AddressResolver, domain: str) -> tuple:
    try:
        results = resolver(domain, 443, socket.AF_INET, socket.SOCK_STREAM)
    except (OSError, ValueError):
        return ()
    addresses = set()
    for result in results:
        try:
            addresses.add(IPv4Address(result[4][0]))
        except (IndexError, KeyError, AddressValueError, ValueError):
            continue
    return tuple(addresses)


def _start_lookup(domain: str, resolver: Optional[AddressResolver]) -> Optional[Future]:
    """Start bounded work or join a system lookup that is still running."""

    if resolver is not None:
        return _lookup_pool.submit(_lookup_with, resolver, domain)
    with _resolution_cache_lock:
        running = _pending.get(domain)
        if running is not None:
            return running
        future = _lookup_pool.submit(_system_lookup, domain)
        if future is None:
            return None
        _pending[domain] = future
    # Outside the lock because a finished future calls this at once.
    future.add_done_callback(lambda done, name=domain: _forget_pending(name, done))
    return future


def resolve_domains(
    *,
    domains: Iterable[str],
    resolver: Optional[AddressResolver] = None,
) -> tuple:
    """Resolve names within the plan deadline and process-wide worker limits."""

    if getattr(_session_state, "without_dns", False):
        return ()
    names = deque(dict.fromkeys(domains))
    if not names:
        return ()
    session = getattr(_session_state, "session", None) or LookupSession(RESOLVE_WAIT_SECONDS)
    addresses = set()
    waiting: Dict[str, Future] = {}
    # (expiry, name) of the expired answers used in this call. Renewed after the lookups below.
    stale: List[tuple] = []

    def missed(name: str) -> None:
        session.note_timeout(name)
        if resolver is None:
            _remember_timeout(name)

    try:
        while names or waiting:
            while names and len(waiting) < RESOLVER_THREADS:
                name = names.popleft()
                if resolver is None:
                    cached = _fresh_addresses(name)
                    if cached is None:
                        entry = _stale_entry(name)
                        if entry is not None:
                            stale.append((entry[0], name))
                            cached = entry[1]
                    if cached is not None:
                        addresses.update(cached)
                        continue
                    if _recently_timed_out(name):
                        session.note_timeout(name)
                        continue
                if name in session.timed_out or session.remaining() <= 0:
                    session.note_timeout(name)
                    continue
                future = _start_lookup(name, resolver)
                if future is None:
                    if waiting:
                        names.appendleft(name)
                        break
                    # Other callers fill the pool. Wait for a place within the deadline.
                    if _lookup_pool.wait_for_room(session.remaining()):
                        names.appendleft(name)
                        continue
                    missed(name)
                    continue
                waiting[name] = future
            if not waiting:
                continue
            done, _ = wait_for_futures(
                waiting.values(), timeout=session.remaining(), return_when=FIRST_COMPLETED
            )
            if not done:
                break
            for name, future in tuple(waiting.items()):
                if future not in done:
                    continue
                del waiting[name]
                if future.cancelled():
                    missed(name)
                    continue
                try:
                    addresses.update(future.result())
                except Exception:  # one unresolvable name must not hide the others
                    LOGGER.debug("could not resolve %s", name, exc_info=True)
        for name in names:
            entry = _stale_entry(name) if resolver is None else None
            if entry is not None:
                addresses.update(entry[1])
                stale.append((entry[0], name))
            else:
                session.note_timeout(name)
        for name in waiting:
            missed(name)
        _renew(stale)
    finally:
        if resolver is not None:
            for future in waiting.values():
                future.cancel()
        # System work stays bounded and can supply a late cache answer or be joined
        # by another caller. A timed-out caller never shuts down the shared pool.
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
                    allowed_addresses=None
                    if allowed_addresses is None
                    else tuple(sorted(allowed_addresses, key=str)),
                    blocked_addresses=tuple(sorted(profile_blocked, key=str)),
                )
            )
    return tuple(global_blocked), tuple(filters)
