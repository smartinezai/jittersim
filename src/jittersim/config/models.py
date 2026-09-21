"""Validated domain objects used by the jittersim control plane."""

from dataclasses import dataclass
from ipaddress import AddressValueError, IPv4Address, IPv4Network
import re
from typing import Optional, Tuple, Union


class ValidationError(ValueError):
    """Raised when a jittersim configuration value is unsafe or malformed."""


_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_DOMAIN_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_MAC_ADDRESS_PATTERN = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")
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


def _normalize_string_list(values: Tuple[str, ...], field_name: str) -> Tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise ValidationError(f"{field_name} must be a list")
    normalized = tuple(dict.fromkeys(value.strip() for value in values if isinstance(value, str) and value.strip()))
    if len(normalized) > 256:
        raise ValidationError(f"{field_name} must contain no more than 256 items")
    if len(normalized) != len(values):
        raise ValidationError(f"{field_name} must contain non-empty strings")
    return normalized


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


def normalize_mac_address(value: str) -> str:
    """Return one lowercase colon-separated MAC address."""

    if not isinstance(value, str):
        raise ValidationError("mac_address must be a string")
    mac_address = value.strip().lower().replace("-", ":")
    if not _MAC_ADDRESS_PATTERN.fullmatch(mac_address):
        raise ValidationError("mac_address must contain six hexadecimal pairs")
    return mac_address


def _normalize_domain_list(values: Tuple[str, ...], field_name: str) -> Tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise ValidationError(f"{field_name} must be a list")
    normalized = tuple(dict.fromkeys(normalize_domain(value) for value in values))
    if len(normalized) > 100:
        raise ValidationError(f"{field_name} must contain no more than 100 domains")
    return normalized


@dataclass(frozen=True)
class Randomness:
    """Variation and enablement controls for one periodically rerolled value."""

    variation_percent: int = 0
    enabled_probability: int = 100

    def __post_init__(self) -> None:
        _validate_percent(self.variation_percent, "variation_percent")
        _validate_percent(self.enabled_probability, "enabled_probability")


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
    delay_randomness: Randomness = Randomness()
    jitter_randomness: Randomness = Randomness()
    loss_randomness: Randomness = Randomness()
    bandwidth_randomness: Randomness = Randomness()
    reorder_randomness: Randomness = Randomness()
    duplicate_randomness: Randomness = Randomness()
    corrupt_randomness: Randomness = Randomness()

    def __post_init__(self) -> None:
        _validate_number(self.delay_ms, "delay_ms", 0, 60_000)
        _validate_number(self.jitter_ms, "jitter_ms", 0, 60_000)
        _validate_percent(self.loss_percent, "loss_percent")
        _validate_percent(self.reorder_percent, "reorder_percent")
        _validate_percent(self.duplicate_percent, "duplicate_percent")
        _validate_percent(self.corrupt_percent, "corrupt_percent")
        if self.bandwidth_kbit is not None:
            _validate_number(self.bandwidth_kbit, "bandwidth_kbit", 1, 1_000_000)
        for value, field_name in (
            (self.delay_randomness, "delay_randomness"),
            (self.jitter_randomness, "jitter_randomness"),
            (self.loss_randomness, "loss_randomness"),
            (self.bandwidth_randomness, "bandwidth_randomness"),
            (self.reorder_randomness, "reorder_randomness"),
            (self.duplicate_randomness, "duplicate_randomness"),
            (self.corrupt_randomness, "corrupt_randomness"),
        ):
            if not isinstance(value, Randomness):
                raise ValidationError(f"{field_name} must be a Randomness")


@dataclass(frozen=True)
class Profile:
    """Named upload and download impairments that can be assigned to a device."""

    id: str
    name: str
    upload: Impairment = Impairment()
    download: Impairment = Impairment()
    random_seed: Optional[int] = None
    reroll_interval_seconds: int = 0
    reroll_interval_randomness: Randomness = Randomness()
    outage_probability: int = 0
    outage_duration_seconds: int = 0
    outage_duration_randomness: Randomness = Randomness()
    outage_interval_seconds: int = 0
    outage_interval_randomness: Randomness = Randomness()
    website_mode: str = "unrestricted"
    website_domains: Tuple[str, ...] = ()
    website_randomness: Randomness = Randomness()

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "id")
        _validate_name(self.name, "name")
        if not isinstance(self.upload, Impairment):
            raise ValidationError("upload must be an Impairment")
        if not isinstance(self.download, Impairment):
            raise ValidationError("download must be an Impairment")
        if self.random_seed is not None:
            _validate_number(self.random_seed, "random_seed", 0, 2**63 - 1)
        _validate_number(self.reroll_interval_seconds, "reroll_interval_seconds", 0, 604_800)
        _validate_percent(self.outage_probability, "outage_probability")
        _validate_number(self.outage_duration_seconds, "outage_duration_seconds", 0, 604_800)
        _validate_number(self.outage_interval_seconds, "outage_interval_seconds", 0, 604_800)
        for value, field_name in (
            (self.reroll_interval_randomness, "reroll_interval_randomness"),
            (self.outage_duration_randomness, "outage_duration_randomness"),
            (self.outage_interval_randomness, "outage_interval_randomness"),
            (self.website_randomness, "website_randomness"),
        ):
            if not isinstance(value, Randomness):
                raise ValidationError(f"{field_name} must be a Randomness")
        if self.website_mode not in ("unrestricted", "allow_only"):
            raise ValidationError("website_mode is not supported")
        object.__setattr__(self, "website_domains", _normalize_domain_list(self.website_domains, "website_domains"))


@dataclass(frozen=True)
class Device:
    """A test-network client identified by a reserved private IPv4 address."""

    id: str
    name: str
    ipv4_address: IPv4Input
    profile_id: str
    mac_address: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "id")
        _validate_name(self.name, "name")
        object.__setattr__(self, "ipv4_address", _parse_test_address(self.ipv4_address))
        _validate_identifier(self.profile_id, "profile_id")
        if self.mac_address is not None:
            object.__setattr__(self, "mac_address", normalize_mac_address(self.mac_address))


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


@dataclass(frozen=True)
class Schedule:
    """One repeating daily or weekly window for a device profile."""

    id: str
    device_id: str
    profile_id: str
    days_of_week: Tuple[int, ...]
    start_minute: int
    end_minute: int
    enabled: bool = True

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "id")
        _validate_identifier(self.device_id, "device_id")
        _validate_identifier(self.profile_id, "profile_id")
        if not isinstance(self.days_of_week, (list, tuple)) or not self.days_of_week:
            raise ValidationError("days_of_week must contain at least one day")
        normalized_days = tuple(sorted(set(_validate_number(day, "day_of_week", 0, 6) for day in self.days_of_week)))
        object.__setattr__(self, "days_of_week", normalized_days)
        _validate_number(self.start_minute, "start_minute", 0, 1_439)
        _validate_number(self.end_minute, "end_minute", 0, 1_439)
        if self.start_minute == self.end_minute:
            raise ValidationError("schedule start and end must be different")
        if not isinstance(self.enabled, bool):
            raise ValidationError("enabled must be a boolean")


@dataclass(frozen=True)
class Timer:
    """One temporary profile override for a device."""

    id: str
    device_id: str
    profile_id: str
    started_at: int
    expires_at: int
    enabled: bool = True

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "timer id")
        _validate_identifier(self.device_id, "device_id")
        _validate_identifier(self.profile_id, "profile_id")
        _validate_number(self.started_at, "started_at", 0, 2**63 - 1)
        _validate_number(self.expires_at, "expires_at", 1, 2**63 - 1)
        if self.expires_at <= self.started_at:
            raise ValidationError("expires_at must be later than started_at")
        if not isinstance(self.enabled, bool):
            raise ValidationError("enabled must be a boolean")


@dataclass(frozen=True)
class MacPolicy:
    """MAC filtering policy for clients arriving through the test access point."""

    mode: str = "disabled"
    allowlist: Tuple[str, ...] = ()
    denylist: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in ("disabled", "allowlist", "denylist"):
            raise ValidationError("mac filter mode is not supported")
        allowlist = tuple(dict.fromkeys(normalize_mac_address(value) for value in self.allowlist))
        denylist = tuple(dict.fromkeys(normalize_mac_address(value) for value in self.denylist))
        if len(allowlist) > 256 or len(denylist) > 256:
            raise ValidationError("MAC lists must contain no more than 256 addresses")
        if self.mode == "allowlist" and not allowlist:
            raise ValidationError("allowlist mode requires at least one allowed MAC address")
        object.__setattr__(self, "allowlist", allowlist)
        object.__setattr__(self, "denylist", denylist)


@dataclass(frozen=True)
class WebsitePolicy:
    """Global domain rules applied before profile-specific website rules."""

    mode: str = "unrestricted"
    allowed_domains: Tuple[str, ...] = ()
    blocked_domains: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in ("unrestricted", "allow_only"):
            raise ValidationError("website policy mode is not supported")
        object.__setattr__(self, "allowed_domains", _normalize_domain_list(self.allowed_domains, "allowed_domains"))
        object.__setattr__(self, "blocked_domains", _normalize_domain_list(self.blocked_domains, "blocked_domains"))


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
