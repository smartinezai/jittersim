"""Remember the destination addresses learned for each service list."""

import time
from ipaddress import IPv4Address
from typing import Callable, Dict, Iterable, List, Sequence, Tuple

DEFAULT_ADDRESS_TTL_SECONDS = 6 * 60 * 60
DEFAULT_MAX_ADDRESSES_PER_LIST = 4096


class ServiceAddressBook:
    """Hold addresses with a last-seen time so stale ones can be removed.

    Seeing an address again refreshes it. The book is not thread safe. The
    privileged helper uses it only while holding its network lock.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_ADDRESS_TTL_SECONDS,
        max_per_list: int = DEFAULT_MAX_ADDRESSES_PER_LIST,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_per_list = max_per_list
        self._clock = clock
        self._seen: Dict[str, Dict[IPv4Address, float]] = {}

    def record(
        self, list_id: str, addresses: Iterable[IPv4Address]
    ) -> Tuple[Tuple[IPv4Address, ...], Tuple[IPv4Address, ...]]:
        """Remember addresses and return (newly added, pushed out to stay within the limit)."""

        now = self._clock()
        entries = self._seen.setdefault(list_id, {})
        fresh: List[IPv4Address] = []
        for address in addresses:
            if address not in entries:
                fresh.append(address)
            entries.pop(address, None)
            entries[address] = now
        evicted: List[IPv4Address] = []
        while len(entries) > self._max_per_list:
            oldest = next(iter(entries))
            del entries[oldest]
            evicted.append(oldest)
        return tuple(fresh), tuple(evicted)

    def discard(self, list_id: str, addresses: Iterable[IPv4Address]) -> None:
        """Forget addresses that could not be added to the kernel set."""

        entries = self._seen.get(list_id, {})
        for address in addresses:
            entries.pop(address, None)

    def expire(self) -> Dict[str, Tuple[IPv4Address, ...]]:
        """Remove and return addresses not seen within the time to live."""

        cutoff = self._clock() - self._ttl_seconds
        expired: Dict[str, Tuple[IPv4Address, ...]] = {}
        for list_id, entries in list(self._seen.items()):
            stale = tuple(address for address, seen in entries.items() if seen < cutoff)
            for address in stale:
                del entries[address]
            if stale:
                expired[list_id] = stale
            if not entries:
                del self._seen[list_id]
        return expired

    def snapshot(self, list_ids: Sequence[str]) -> Dict[str, Tuple[IPv4Address, ...]]:
        """Return the live addresses of the requested lists."""

        return {
            list_id: tuple(self._seen[list_id])
            for list_id in list_ids
            if self._seen.get(list_id)
        }
