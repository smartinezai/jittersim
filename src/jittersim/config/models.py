"""Validated domain objects used by the jittersim control plane."""

from dataclasses import dataclass
from ipaddress import AddressValueError, IPv4Address, IPv4Network
import re
from typing import Optional, Union


class ValidationError(ValueError):
    """Raised when a jittersim configuration value is unsafe or malformed."""


_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_DOMAIN_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_RFC1918_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)
IPv4Input = Union[str, IPv4Address]


def _validate_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValidationError(
            f"{field_name} must contain 1 to 64 lowercase letters, numbers, hyphens or underscores"
        )
    return value


def _validate_name(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ValidationError(f"{field_name} must contain between 1 and 100 characters")
    return value.strip()


def _validate_number(
    value: int,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field_name} must be an integer")
    if value < minimum or value > maximum:
        raise ValidationError(f"{field_name} must be between {minimum} and {maximum}")
    return value


def _validate_percent(value: int, field_name: str) -> int:
    return _validate_number(value, field_name, minimum=0, maximum=100)


def normalize_domain(value: str) -> str:
    """Return one safe lowercase DNS name without its optional trailing dot."""

    if not isinstance(value, str):
        raise ValidationError("domain must be a string")
    domain = value.strip().lower().rstrip(".")
    if not domain or len(domain) > 253:
        raise ValidationError("domain must contain between 1 and 253 characters")
    labels = domain.split(".")
    if any(len(label) > 63 or not _DOMAIN_LABEL_PATTERN.fullmatch(label) for label in labels):
        raise ValidationError("domain must contain valid DNS labels")
    return domain


def _parse_test_address(value: IPv4Input) -> IPv4Address:
    try:
        address = value if isinstance(value, IPv4Address) else IPv4Address(value)
    except (AddressValueError, ValueError) as error:
        raise ValidationError("ipv4_address must be a valid IPv4 address") from error

    if not any(address in network for network in _RFC1918_NETWORKS):
        raise ValidationError("ipv4_address must be inside an RFC1918 private network")
    return address


@dataclass(frozen=True)
class Impairment:
    """Packet-level behavior for one traffic direction."""

    delay_ms: int = 0
    jitter_ms: int = 0
    loss_percent: int = 0
    bandwidth_kbit: Optional[int] = None
    reorder_percent: int = 0
    duplicate_percent: int = 0
    corrupt_percent: int = 0

    def __post_init__(self) -> None:
        _validate_number(self.delay_ms, "delay_ms", 0, 60_000)
        _validate_number(self.jitter_ms, "jitter_ms", 0, 60_000)
        _validate_percent(self.loss_percent, "loss_percent")
        _validate_percent(self.reorder_percent, "reorder_percent")
        _validate_percent(self.duplicate_percent, "duplicate_percent")
        _validate_percent(self.corrupt_percent, "corrupt_percent")
        if self.bandwidth_kbit is not None:
            _validate_number(self.bandwidth_kbit, "bandwidth_kbit", 1, 1_000_000)


@dataclass(frozen=True)
class Profile:
    """Named upload and download impairments that can be assigned to a device."""

    id: str
    name: str
    upload: Impairment = Impairment()
    download: Impairment = Impairment()
    random_seed: Optional[int] = None

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "id")
        _validate_name(self.name, "name")
        if not isinstance(self.upload, Impairment):
            raise ValidationError("upload must be an Impairment")
        if not isinstance(self.download, Impairment):
            raise ValidationError("download must be an Impairment")
        if self.random_seed is not None:
            _validate_number(self.random_seed, "random_seed", 0, 2**63 - 1)


@dataclass(frozen=True)
class Device:
    """A test-network client identified by a reserved private IPv4 address."""

    id: str
    name: str
    ipv4_address: IPv4Input
    profile_id: str

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "id")
        _validate_name(self.name, "name")
        object.__setattr__(self, "ipv4_address", _parse_test_address(self.ipv4_address))
        _validate_identifier(self.profile_id, "profile_id")


@dataclass(frozen=True)
class DomainObservation:
    """One bounded aggregate of a DNS name observed from a test client."""

    ipv4_address: IPv4Input
    domain: str
    query_count: int
    first_seen: int
    last_seen: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "ipv4_address", _parse_test_address(self.ipv4_address))
        object.__setattr__(self, "domain", normalize_domain(self.domain))
        _validate_number(self.query_count, "query_count", 1, 1_000_000_000)
        _validate_number(self.first_seen, "first_seen", 0, 2**63 - 1)
        _validate_number(self.last_seen, "last_seen", 0, 2**63 - 1)
        if self.first_seen > self.last_seen:
            raise ValidationError("first_seen must not be later than last_seen")


_OBSERVED_STATES = frozenset(("unknown", "applying", "resetting", "applied", "reset", "error"))


@dataclass(frozen=True)
class RuntimeState:
    """Observed helper state associated with the persisted desired configuration."""

    configuration_version: int = 0
    observed_state: str = "unknown"
    last_action: Optional[str] = None
    last_result: Optional[str] = None
    last_error: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_number(self.configuration_version, "configuration_version", 0, 2**63 - 1)
        if self.observed_state not in _OBSERVED_STATES:
            raise ValidationError("observed_state is not supported")
        for value, field_name in (
            (self.last_action, "last_action"),
            (self.last_result, "last_result"),
            (self.last_error, "last_error"),
        ):
            if value is not None and (not isinstance(value, str) or len(value) > 500):
                raise ValidationError(f"{field_name} must be no more than 500 characters")
