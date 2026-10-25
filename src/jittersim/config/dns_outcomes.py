"""Finite, current-epoch resolver observations. Nothing here is a traffic policy."""

import re
import time
from collections import Counter, OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from ipaddress import IPv4Address

from .exemptions import normalize_exempt_mac
from .models import _parse_test_address, normalize_domain

MAX_DNS_OUTCOME_ROWS = 512
MAX_DNS_OUTCOME_BATCH = 64
DNS_OUTCOME_WINDOW_SECONDS = 300
MAX_SAFE_COUNTER = 2**53 - 1
_OUTCOMES = ("blocked_decisions", "answered_noerror", "other_dns_errors")
_TOKEN = re.compile(r"[a-f0-9]{32}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def safe_integer(value, name="value", maximum=MAX_SAFE_COUNTER):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{name} is outside its integer bounds")
    return value


def eligible_dns_identities(snapshot, protected):
    """Require a unique, currently verified and reachable unprotected client."""
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("stale") not in (None, False)
        or snapshot.get("association_status_available") is not True
    ):
        return {}
    rows = snapshot.get("devices")
    if not isinstance(rows, list) or len(rows) > 256:
        return {}
    addresses, macs, checked = Counter(), Counter(), []
    for row in rows:
        if not isinstance(row, dict):
            return {}
        address = row.get("ipv4_address")
        try:
            address = str(_parse_test_address(address))
        except (TypeError, ValueError):
            if address is not None:
                return {}
            continue
        addresses[address] += 1
        try:
            mac = normalize_exempt_mac(row.get("mac_address"))
        except ValueError:
            checked.append((address, None, False))
            continue
        macs[mac] += 1
        checked.append(
            (
                address,
                mac,
                row.get("association_verified") is True
                and row.get("neighbour_state") == "reachable",
            )
        )
    return {
        address: mac
        for address, mac, verified in checked
        if verified and mac not in protected and addresses[address] == 1 and macs[mac] == 1
    }


@dataclass(frozen=True)
class DnsDecision:
    """One event from an authenticated resolver producer, not a matched query attempt."""

    sequence: int
    ipv4_address: str
    domain: str
    kind: str
    observed_at: int

    def __post_init__(self):
        safe_integer(self.sequence, "sequence")
        safe_integer(self.observed_at, "observation time")
        object.__setattr__(self, "ipv4_address", str(IPv4Address(self.ipv4_address)))
        object.__setattr__(self, "domain", normalize_domain(self.domain))
        if self.kind not in _OUTCOMES:
            raise ValueError("unsupported DNS event")


class DnsOutcomeStore:
    """Repository-lock-owned RAM only. No database, log or backup history."""

    def __init__(self):
        self.minimum_started_ns = 0
        self.epoch = None
        self.session = None
        self.rows = OrderedDict()
        self.identities = {}
        self.reason = "producer_unavailable"
        self.available = False
        self.last_event_at = None
        self.sequence = -1
        self.version = None
        self.generation = None
        self.started_at = None
        self.ended_at = None
        self._clear_source = None

    def invalidate(self, reason="privacy_revision_changed"):
        # A source born before this barrier cannot relabel in-flight bytes into a new epoch.
        self.minimum_started_ns = time.monotonic_ns()
        self.epoch = self.session = self.version = self.generation = None
        self.rows.clear()
        self.identities.clear()
        self.available = False
        self.last_event_at = self.started_at = self.ended_at = None
        self.sequence = -1
        self.reason = reason
        if self._clear_source is not None:
            self._clear_source()

    def begin(self, *, epoch, session, generation, producer_started_ns, version, identities, now):
        if (
            not isinstance(epoch, str)
            or not _TOKEN.fullmatch(epoch)
            or not isinstance(session, str)
            or not _TOKEN.fullmatch(session)
            or not isinstance(generation, str)
            or not _DIGEST.fullmatch(generation)
        ):
            raise ValueError("invalid DNS producer identity")
        safe_integer(now, "window time")
        safe_integer(version, "policy revision")
        safe_integer(producer_started_ns, "producer start", 2**63 - 1)
        if producer_started_ns <= self.minimum_started_ns:
            raise ValueError("DNS producer must restart after the privacy barrier")
        if self.epoch is not None:
            raise ValueError("DNS observation epoch is already active")
        self.epoch, self.session, self.generation = epoch, session, generation
        self.version, self.identities = version, dict(identities)
        self.started_at = self.ended_at = now
        self.reason = "sender_loss_unobservable"
        self.available = True

    def add(self, events, *, epoch, session, generation, version, identities, now):
        if not isinstance(events, (tuple, list)) or len(events) > MAX_DNS_OUTCOME_BATCH:
            raise ValueError("DNS batch exceeds its limit")
        if any(not isinstance(event, DnsDecision) for event in events):
            raise ValueError("invalid DNS event batch")
        safe_integer(now, "window time")
        if self.epoch is None or (epoch, session, generation, version) != (
            self.epoch,
            self.session,
            self.generation,
            self.version,
        ):
            return
        if not self.started_at <= now <= self.started_at + DNS_OUTCOME_WINDOW_SECONDS:
            self.invalidate("window_expired")
            return
        for event in events:
            if event.sequence <= self.sequence:
                continue
            self.sequence = event.sequence
            if not self.started_at <= event.observed_at <= now or now - event.observed_at > 5:
                self.reason = "event_clock_or_age_unknown"
                continue
            mac = self.identities.get(event.ipv4_address)
            if mac is None or identities.get(event.ipv4_address) != mac:
                continue
            key = (event.ipv4_address, mac, event.domain)
            row = self.rows.get(key)
            if row is None:
                if len(self.rows) >= MAX_DNS_OUTCOME_ROWS:
                    self.rows.popitem(last=False)
                    self.reason = "aggregate_limit_reached"
                row = dict.fromkeys(_OUTCOMES, 0)
                self.rows[key] = row
            row[event.kind] = min(MAX_SAFE_COUNTER, row[event.kind] + 1)
            row["last_observed_at"] = event.observed_at
            self.rows.move_to_end(key)
            self.last_event_at = event.observed_at
        self.ended_at = now

    def snapshot(self, *, version, identities, now):
        if self.epoch is not None and version != self.version:
            self.invalidate()
        if self.started_at is not None and not (
            self.started_at <= now <= self.started_at + DNS_OUTCOME_WINDOW_SECONDS
        ):
            self.invalidate("window_expired")
        for key in tuple(self.rows):
            if identities.get(key[0]) != key[1]:
                del self.rows[key]
        self.last_event_at = max(
            (row["last_observed_at"] for row in self.rows.values()), default=None
        )
        rows = {key: dict(row) for key, row in self.rows.items()}
        return rows, {
            "status": "partial" if self.epoch is not None else "unavailable",
            "epoch": self.epoch,
            "policy_revision": self.version,
            "window_start": self.started_at,
            "window_end": self.ended_at,
            "lower_bound": True,
            "coverage_complete": False,
            "collector_available": self.available,
            "last_event_at": self.last_event_at,
            "dropped_events": None,
            "coverage_reason": self.reason,
        }


@contextmanager
def dns_outcome_transaction(repository):
    """Never wait for another process or hold the repository during helper calls."""
    with repository.locked_within(0):
        previous = repository._connection.execute("PRAGMA busy_timeout").fetchone()[0]
        repository._connection.execute("PRAGMA busy_timeout = 0")
        try:
            with repository.transaction():
                yield
        finally:
            repository._connection.execute(f"PRAGMA busy_timeout = {int(previous)}")


def unavailable_dns_outcomes(reason="producer_unavailable"):
    return {
        "status": "unavailable",
        "epoch": None,
        "policy_revision": None,
        "window_start": None,
        "window_end": None,
        "lower_bound": True,
        "coverage_complete": False,
        "collector_available": False,
        "last_event_at": None,
        "dropped_events": None,
        "coverage_reason": reason,
    }
