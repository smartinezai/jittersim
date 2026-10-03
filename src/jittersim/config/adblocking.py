"""Bounded domain lists shared by the DNS blocker and browser proxy."""

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import FrozenSet, Iterable, Tuple

from .exemptions import MAX_UNAFFECTED_DEVICES, normalize_exempt_mac
from .models import normalize_service_hostname

MAX_ALLOWED_DOMAINS = 256
MAX_BLOCKED_DOMAINS = 200_000
MAX_LIST_BYTES = 8 * 1024 * 1024
MAX_STATE_BYTES = 128 * 1024
ACTIVE_FILE = Path("/run/jittersim/adblocking-active.json")
DOMAIN_FILE = Path("/var/lib/jittersim-adblocking/domains.txt")
HEALTH_DOMAIN = "jittersim-adblocking-check.invalid"


def ad_domain(value: object) -> str:
    try:
        domain = normalize_service_hostname(value)
    except ValueError:
        # Hosts lists can include DNS labels with underscores.
        if not isinstance(value, str) or "_" not in value:
            raise
        domain = value.strip().lower().rstrip(".")
        if any(
            not re.fullmatch(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?", label)
            for label in domain.split(".")
        ):
            raise ValueError("Use a complete domain name such as ads.example.com") from None
    if "." not in domain or len(domain) > 253:
        raise ValueError("Use a complete domain name such as ads.example.com")
    return domain


def allowed_domains(values: Iterable[str]) -> Tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or len(values) > MAX_ALLOWED_DOMAINS:
        raise ValueError("The allowed-domain list can contain at most 256 domains")
    return tuple(sorted({ad_domain(value) for value in values}))


def blocking_macs(values: Iterable[str]) -> Tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or len(values) > MAX_UNAFFECTED_DEVICES:
        raise ValueError("Ad blocking can contain at most 256 devices")
    return tuple(sorted({normalize_exempt_mac(value) for value in values}))


def parse_hosts(data: bytes) -> Tuple[str, ...]:
    """Read a hosts-format list without accepting configuration directives."""

    if len(data) > MAX_LIST_BYTES:
        raise ValueError("The blocklist exceeds its size limit")
    domains = set()
    for line in data.decode("utf-8").splitlines():
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        if fields[0] not in {"0.0.0.0", "127.0.0.1", "::", "::1"}:
            continue
        for value in fields[1:]:
            try:
                ip_address(value)
            except ValueError:
                pass
            else:
                continue
            if value in {
                "localhost",
                "localhost.localdomain",
                "local",
                "broadcasthost",
                "ip6-localhost",
                "ip6-loopback",
            }:
                continue
            domains.add(ad_domain(value))
            if len(domains) > MAX_BLOCKED_DOMAINS:
                raise ValueError("The blocklist contains too many domains")
    if not domains:
        raise ValueError("The blocklist contains no valid domains")
    return tuple(sorted(domains))


def domain_bytes(domains: Iterable[str]) -> bytes:
    return ("\n".join(domains) + "\n").encode("ascii")


def read_domains(path: Path) -> Tuple[Tuple[str, ...], str]:
    with path.open("rb") as source:
        data = source.read(MAX_LIST_BYTES + 1)
    if len(data) > MAX_LIST_BYTES:
        raise ValueError("The blocklist exceeds its size limit")
    lines = data.decode("ascii").splitlines()
    if not lines or len(lines) > MAX_BLOCKED_DOMAINS:
        raise ValueError("The blocklist contains an invalid number of domains")
    return tuple(ad_domain(line) for line in lines), hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class DomainPolicy:
    domains: FrozenSet[str]
    allowances: FrozenSet[str] = frozenset()

    @staticmethod
    def _suffixes(domain: str):
        parts = domain.split(".")
        return (".".join(parts[index:]) for index in range(len(parts) - 1))

    def allowed(self, domain: str) -> bool:
        return any(suffix in self.allowances for suffix in self._suffixes(domain))

    def blocked(self, domain: str) -> bool:
        try:
            domain = ad_domain(domain)
        except ValueError:
            return False
        return not self.allowed(domain) and any(
            suffix in self.domains for suffix in self._suffixes(domain)
        )


class ActiveBlocking:
    """Cache the applied list without storing any client lookup history."""

    def __init__(
        self, path: Path = ACTIVE_FILE, domains_path: Path = DOMAIN_FILE, clock=time.monotonic
    ):
        self.path = path
        self.domains_path = domains_path
        self.clock = clock
        self._generation = None
        self._state = {}
        self._policy = DomainPolicy(frozenset())
        self._lock = threading.RLock()

    def _refresh(self) -> None:
        try:
            info = self.path.stat()
            generation = (info.st_ino, info.st_mtime_ns, info.st_size)
            if generation == self._generation:
                return
            with self.path.open("rb") as source:
                data = source.read(MAX_STATE_BYTES + 1)
            if len(data) > MAX_STATE_BYTES:
                raise ValueError("Invalid blocker state")
            state = json.loads(data)
            macs = blocking_macs(state["mac_addresses"])
            allowances = allowed_domains(state["allowed_domains"])
            expires = state["expires_at"]
            if isinstance(expires, bool) or not isinstance(expires, (int, float)):
                raise ValueError("Invalid blocker expiry")
            if not self.clock() < expires <= self.clock() + 6:
                self._state = {}
                self._generation = None
                return
            if state.get("list_digest") != self._state.get("list_digest"):
                domains, digest = read_domains(self.domains_path)
                if digest != state["list_digest"]:
                    raise ValueError("The applied blocklist changed")
                self._policy = DomainPolicy(frozenset(domains), frozenset(allowances))
            elif allowances != tuple(sorted(self._policy.allowances)):
                self._policy = DomainPolicy(self._policy.domains, frozenset(allowances))
            self._state = {**state, "mac_addresses": macs}
            self._generation = generation
        except (OSError, ValueError, KeyError, TypeError):
            self._state = {}
            self._generation = None

    def enabled(self) -> bool:
        with self._lock:
            self._refresh()
            return (
                bool(self._state.get("mac_addresses")) and self.clock() < self._state["expires_at"]
            )

    def blocks(self, mac: str, host: str) -> bool:
        with self._lock:
            return (
                self.enabled()
                and mac in self._state["mac_addresses"]
                and self._policy.blocked(host)
            )
