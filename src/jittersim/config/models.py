"""Validated domain objects used by the jittersim control plane."""

import re
from dataclasses import dataclass
from hashlib import sha256
from ipaddress import AddressValueError, IPv4Address, IPv4Network, ip_address
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


def time_quota_counter_id(quota_id: str) -> str:
    """Return a bounded internal counter name for one timed quota."""

    _validate_identifier(quota_id, "time quota id")
    return "tq-" + sha256(quota_id.encode("utf-8")).hexdigest()[:16]


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


def normalize_service_hostname(value: str) -> str:
    """Require a DNS name rather than a shared server address."""

    domain = normalize_domain(value)
    try:
        ip_address(domain)
    except ValueError:
        return domain
    raise ValidationError("browser service domains must be hostnames, not IP addresses")


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
    # Average length of a run of lost packets. 0 or 1 loses packets independently.
    loss_burst_packets: int = 0

    def __post_init__(self) -> None:
        _validate_number(self.delay_ms, "delay_ms", 0, 60_000)
        _validate_number(self.jitter_ms, "jitter_ms", 0, 60_000)
        _validate_percent(self.loss_percent, "loss_percent")
        _validate_percent(self.reorder_percent, "reorder_percent")
        _validate_percent(self.duplicate_percent, "duplicate_percent")
        _validate_percent(self.corrupt_percent, "corrupt_percent")
        _validate_number(self.loss_burst_packets, "loss_burst_packets", 0, 100)
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


MAX_UNAFFECTED_DEVICES = 256


@dataclass(frozen=True)
class UnaffectedDevice:
    """A test Wi-Fi client that keeps a normal connection, found by its MAC address."""

    mac_address: str
    name: str = ""

    def __post_init__(self) -> None:
        mac_address = normalize_mac_address(self.mac_address)
        if not isinstance(self.name, str):
            raise ValidationError("name must be a string")
        object.__setattr__(self, "mac_address", mac_address)
        object.__setattr__(self, "name", _validate_name(self.name.strip() or mac_address, "name"))


@dataclass(frozen=True)
class DeviceGroup:
    """A named set of devices that can share a profile and quota."""

    id: str
    name: str
    device_ids: Tuple[str, ...]
    profile_id: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "group id")
        _validate_name(self.name, "group name")
        if not isinstance(self.device_ids, (list, tuple)) or not self.device_ids:
            raise ValidationError("device_ids must contain at least one device")
        if len(self.device_ids) > 15:
            raise ValidationError("a group cannot contain more than 15 devices")
        normalized_ids = tuple(dict.fromkeys(self.device_ids))
        if len(normalized_ids) != len(self.device_ids):
            raise ValidationError("device_ids must not contain duplicates")
        for device_id in normalized_ids:
            _validate_identifier(device_id, "device_id")
        object.__setattr__(self, "device_ids", normalized_ids)
        if self.profile_id is not None:
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


def _validate_target(
    device_id: Optional[str], group_id: Optional[str], everyone_else: bool, owner: str
) -> None:
    """Check that exactly one of a device, a group or everyone else is named."""

    if device_id is not None:
        _validate_identifier(device_id, "device_id")
    if group_id is not None:
        _validate_identifier(group_id, "group_id")
    if not isinstance(everyone_else, bool):
        raise ValidationError("everyone_else must be a boolean")
    if (device_id is not None) + (group_id is not None) + everyone_else != 1:
        raise ValidationError(f"a {owner} needs exactly one of a device, a group or everyone else")


@dataclass(frozen=True)
class Schedule:
    """One repeating daily or weekly window for a device, a group or everyone else."""

    id: str
    device_id: Optional[str]
    profile_id: str
    days_of_week: Tuple[int, ...]
    start_minute: int
    end_minute: int
    enabled: bool = True
    group_id: Optional[str] = None
    everyone_else: bool = False

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "id")
        _validate_target(self.device_id, self.group_id, self.everyone_else, "schedule")
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


MAX_SCENARIO_STEPS = 20
MIN_SCENARIO_STEP_SECONDS = 10
MAX_SCENARIO_STEP_SECONDS = 86_400


@dataclass(frozen=True)
class ScenarioStep:
    """One profile a scenario uses for a while."""

    profile_id: str
    duration_seconds: int

    def __post_init__(self) -> None:
        _validate_identifier(self.profile_id, "profile_id")
        duration = self.duration_seconds
        if (
            isinstance(duration, bool)
            or not isinstance(duration, int)
            or not MIN_SCENARIO_STEP_SECONDS <= duration <= MAX_SCENARIO_STEP_SECONDS
        ):
            raise ValidationError("a step must last 10 seconds to 24 hours")


@dataclass(frozen=True)
class Scenario:
    """A named list of profiles used one after another, optionally repeating."""

    id: str
    name: str
    steps: Tuple[ScenarioStep, ...]
    loop: bool = False

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "scenario id")
        object.__setattr__(self, "name", _validate_name(self.name, "scenario name"))
        if not isinstance(self.steps, (list, tuple)) or not self.steps:
            raise ValidationError("a scenario needs at least one step")
        if len(self.steps) > MAX_SCENARIO_STEPS:
            raise ValidationError(f"a scenario can have at most {MAX_SCENARIO_STEPS} steps")
        if not all(isinstance(step, ScenarioStep) for step in self.steps):
            raise ValidationError("each step must be a ScenarioStep")
        object.__setattr__(self, "steps", tuple(self.steps))
        if not isinstance(self.loop, bool):
            raise ValidationError("loop must be a boolean")

    @property
    def total_seconds(self) -> int:
        """Return how long one pass through the steps takes."""

        return sum(step.duration_seconds for step in self.steps)


@dataclass(frozen=True)
class ScenarioRun:
    """One scenario running for a device, a group or everyone else."""

    id: str
    scenario_id: str
    started_at: int
    device_id: Optional[str] = None
    group_id: Optional[str] = None
    everyone_else: bool = False
    enabled: bool = True

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "run id")
        _validate_identifier(self.scenario_id, "scenario_id")
        _validate_number(self.started_at, "started_at", 0, 2**63 - 1)
        _validate_target(self.device_id, self.group_id, self.everyone_else, "scenario run")
        if not isinstance(self.enabled, bool):
            raise ValidationError("enabled must be a boolean")


@dataclass(frozen=True)
class Progression:
    """A bounded session that moves one device between two profiles."""

    id: str
    device_id: str
    start_profile_id: str
    end_profile_id: str
    started_at: int
    duration_seconds: int
    mode: str = "time"
    traffic_target_queries: int = 0
    traffic_baseline_queries: int = 0
    traffic_target_bytes: int = 0
    traffic_baseline_bytes: int = 0
    website_domains: Tuple[str, ...] = ()
    enabled: bool = True

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "progression id")
        _validate_identifier(self.device_id, "device_id")
        _validate_identifier(self.start_profile_id, "start_profile_id")
        _validate_identifier(self.end_profile_id, "end_profile_id")
        _validate_number(self.started_at, "started_at", 0, 2**63 - 1)
        _validate_number(self.duration_seconds, "duration_seconds", 60, 604_800)
        if self.mode not in ("time", "traffic", "bytes"):
            raise ValidationError("progression mode is not supported")
        _validate_number(
            self.traffic_target_queries,
            "traffic_target_queries",
            0,
            1_000_000_000,
        )
        # Below zero after a device moves to an address with fewer recorded
        # queries, so the progress made so far still counts.
        _validate_number(
            self.traffic_baseline_queries,
            "traffic_baseline_queries",
            -1_000_000_000_000,
            1_000_000_000_000,
        )
        if self.mode == "traffic" and self.traffic_target_queries < 1:
            raise ValidationError("traffic mode requires a positive query target")
        _validate_number(
            self.traffic_target_bytes,
            "traffic_target_bytes",
            0,
            2**63 - 1,
        )
        _validate_number(
            self.traffic_baseline_bytes,
            "traffic_baseline_bytes",
            0,
            2**63 - 1,
        )
        if self.mode == "bytes" and self.traffic_target_bytes < 1:
            raise ValidationError("bytes mode requires a positive byte target")
        object.__setattr__(
            self,
            "website_domains",
            _normalize_domain_list(self.website_domains, "website_domains"),
        )
        if not isinstance(self.enabled, bool):
            raise ValidationError("enabled must be a boolean")


def _validate_quota_schedule(
    *,
    reset_mode: str,
    reset_interval_seconds: int,
    reset_times: Tuple[int, ...],
) -> Tuple[int, ...]:
    if reset_mode not in ("manual", "interval", "daily"):
        raise ValidationError("reset_mode is not supported")
    _validate_number(
        reset_interval_seconds,
        "reset_interval_seconds",
        0,
        31_536_000,
    )
    if reset_mode == "interval" and not 60 <= reset_interval_seconds <= 31_536_000:
        raise ValidationError("interval resets must be between 60 seconds and 365 days")
    if reset_mode != "interval" and reset_interval_seconds != 0:
        raise ValidationError("reset_interval_seconds is only valid for interval resets")
    if not isinstance(reset_times, (list, tuple)):
        raise ValidationError("reset_times must be a list")
    normalized_times = tuple(sorted(set(reset_times)))
    if len(normalized_times) > 24:
        raise ValidationError("reset_times must contain no more than 24 times")
    for reset_time in normalized_times:
        _validate_number(reset_time, "reset_time", 0, 1_439)
    if reset_mode == "daily" and not normalized_times:
        raise ValidationError("daily resets require at least one reset time")
    if reset_mode != "daily" and normalized_times:
        raise ValidationError("reset_times is only valid for daily resets")
    return normalized_times


def _validate_quota_usage(
    *,
    limit_bytes: int,
    used_bytes: int,
    counter_bytes: int,
    last_reset_at: int,
) -> None:
    maximum = 2**63 - 1
    _validate_number(limit_bytes, "limit_bytes", 1, maximum)
    _validate_number(used_bytes, "used_bytes", 0, maximum)
    _validate_number(counter_bytes, "counter_bytes", 0, maximum)
    _validate_number(last_reset_at, "last_reset_at", 0, maximum)


@dataclass(frozen=True)
class DataLimit:
    """A device byte quota that activates one fallback profile when reached."""

    device_id: str
    profile_id: str
    limit_bytes: int
    reset_mode: str = "manual"
    reset_interval_seconds: int = 0
    reset_times: Tuple[int, ...] = ()
    used_bytes: int = 0
    counter_bytes: int = 0
    last_reset_at: int = 0
    enabled: bool = True

    def __post_init__(self) -> None:
        _validate_identifier(self.device_id, "device_id")
        _validate_identifier(self.profile_id, "profile_id")
        _validate_quota_usage(
            limit_bytes=self.limit_bytes,
            used_bytes=self.used_bytes,
            counter_bytes=self.counter_bytes,
            last_reset_at=self.last_reset_at,
        )
        normalized_times = _validate_quota_schedule(
            reset_mode=self.reset_mode,
            reset_interval_seconds=self.reset_interval_seconds,
            reset_times=self.reset_times,
        )
        object.__setattr__(self, "reset_times", normalized_times)
        if not isinstance(self.enabled, bool):
            raise ValidationError("enabled must be a boolean")


@dataclass(frozen=True)
class GroupDataLimit:
    """A byte quota shared by all devices in one group."""

    group_id: str
    profile_id: str
    limit_bytes: int
    reset_mode: str = "manual"
    reset_interval_seconds: int = 0
    reset_times: Tuple[int, ...] = ()
    used_bytes: int = 0
    counter_bytes: int = 0
    last_reset_at: int = 0
    enabled: bool = True

    def __post_init__(self) -> None:
        _validate_identifier(self.group_id, "group_id")
        _validate_identifier(self.profile_id, "profile_id")
        _validate_quota_usage(
            limit_bytes=self.limit_bytes,
            used_bytes=self.used_bytes,
            counter_bytes=self.counter_bytes,
            last_reset_at=self.last_reset_at,
        )
        normalized_times = _validate_quota_schedule(
            reset_mode=self.reset_mode,
            reset_interval_seconds=self.reset_interval_seconds,
            reset_times=self.reset_times,
        )
        object.__setattr__(self, "reset_times", normalized_times)
        if not isinstance(self.enabled, bool):
            raise ValidationError("enabled must be a boolean")


@dataclass(frozen=True)
class ServiceAssignment:
    """An immediate browser-proxy profile for named domains on one device."""

    id: str
    device_id: str
    name: str
    domains: Tuple[str, ...]
    profile_id: str
    enabled: bool = True

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "service assignment id")
        _validate_identifier(self.device_id, "device_id")
        _validate_name(self.name, "service assignment name")
        _validate_identifier(self.profile_id, "profile_id")
        object.__setattr__(self, "domains", _normalize_domain_list(self.domains, "domains"))
        object.__setattr__(self, "domains", tuple(normalize_service_hostname(domain) for domain in self.domains))
        if not self.domains:
            raise ValidationError("service assignment requires at least one domain")
        for index, domain in enumerate(self.domains):
            if any(
                domain.endswith("." + previous) or previous.endswith("." + domain)
                for previous in self.domains[:index]
            ):
                raise ValidationError("service assignment domains overlap")
        if not isinstance(self.enabled, bool):
            raise ValidationError("enabled must be a boolean")


@dataclass(frozen=True)
class ServiceQuota:
    """A weighted byte quota for selected observed domains on one device."""

    id: str
    device_id: str
    name: str
    domains: Tuple[str, ...]
    profile_id: str
    limit_bytes: int
    weight_percent: int = 100
    reset_mode: str = "manual"
    reset_interval_seconds: int = 0
    reset_times: Tuple[int, ...] = ()
    used_bytes: int = 0
    counter_bytes: int = 0
    last_reset_at: int = 0
    enabled: bool = True

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "service quota id")
        _validate_identifier(self.device_id, "device_id")
        _validate_name(self.name, "service quota name")
        _validate_identifier(self.profile_id, "profile_id")
        object.__setattr__(self, "domains", _normalize_domain_list(self.domains, "domains"))
        if not self.domains:
            raise ValidationError("service quota requires at least one domain")
        _validate_number(self.weight_percent, "weight_percent", 10, 1_000)
        _validate_quota_usage(
            limit_bytes=self.limit_bytes,
            used_bytes=self.used_bytes,
            counter_bytes=self.counter_bytes,
            last_reset_at=self.last_reset_at,
        )
        normalized_times = _validate_quota_schedule(
            reset_mode=self.reset_mode,
            reset_interval_seconds=self.reset_interval_seconds,
            reset_times=self.reset_times,
        )
        object.__setattr__(self, "reset_times", normalized_times)
        if not isinstance(self.enabled, bool):
            raise ValidationError("enabled must be a boolean")


@dataclass(frozen=True)
class TimeQuota:
    """A service quota measured in active traffic time inside a timer window."""

    id: str
    device_id: str
    name: str
    domains: Tuple[str, ...]
    profile_id: str
    limit_seconds: int
    window_duration_seconds: int = 43_200
    reset_mode: str = "manual"
    reset_interval_seconds: int = 0
    reset_times: Tuple[int, ...] = ()
    used_seconds: int = 0
    counter_bytes: int = 0
    last_sample_at: int = 0
    last_reset_at: int = 0
    started_at: int = 0
    expires_at: int = 0
    enabled: bool = True

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "time quota id")
        _validate_identifier(self.device_id, "device_id")
        _validate_name(self.name, "time quota name")
        _validate_identifier(self.profile_id, "profile_id")
        object.__setattr__(self, "domains", _normalize_domain_list(self.domains, "domains"))
        if not self.domains:
            raise ValidationError("time quota requires at least one domain")
        _validate_number(self.limit_seconds, "limit_seconds", 60, 31_536_000)
        _validate_number(
            self.window_duration_seconds,
            "window_duration_seconds",
            60,
            604_800,
        )
        _validate_quota_usage(
            limit_bytes=self.limit_seconds,
            used_bytes=self.used_seconds,
            counter_bytes=self.counter_bytes,
            last_reset_at=self.last_reset_at,
        )
        _validate_number(self.last_sample_at, "last_sample_at", 0, 2**63 - 1)
        _validate_number(self.started_at, "started_at", 0, 2**63 - 1)
        _validate_number(self.expires_at, "expires_at", 0, 2**63 - 1)
        if (self.started_at == 0) != (self.expires_at == 0):
            raise ValidationError("time quota timer must have both start and expiry times")
        if self.expires_at and self.expires_at <= self.started_at:
            raise ValidationError("time quota expiry must be later than its start")
        if self.expires_at and self.expires_at - self.started_at > self.window_duration_seconds:
            raise ValidationError("time quota timer exceeds its configured window")
        normalized_times = _validate_quota_schedule(
            reset_mode=self.reset_mode,
            reset_interval_seconds=self.reset_interval_seconds,
            reset_times=self.reset_times,
        )
        object.__setattr__(self, "reset_times", normalized_times)
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

MAX_SITE_BLOCKS = 32
MAX_BLOCK_DOMAINS = 512


@dataclass(frozen=True)
class SiteBlock:
    """One named set of domains blocked independently of traffic profiles."""

    id: str
    name: str
    domains: Tuple[str, ...]
    device_id: Optional[str] = None
    group_id: Optional[str] = None
    enabled: bool = True

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "site block id")
        object.__setattr__(self, "name", _validate_name(self.name, "site block name"))
        if any(ord(character) < 32 for character in self.name):
            raise ValidationError("site block name contains a control character")
        if not isinstance(self.enabled, bool):
            raise ValidationError("site block enabled must be true or false")
        if self.device_id is not None:
            _validate_identifier(self.device_id, "site block device")
        if self.group_id is not None:
            _validate_identifier(self.group_id, "site block group")
        if self.device_id is not None and self.group_id is not None:
            raise ValidationError("choose a device, a group or everyone for a site block")
        values = _normalize_domain_list(self.domains, "blocked domains")
        if not values or len(values) > 64:
            raise ValidationError("a site block needs between 1 and 64 domains")
        object.__setattr__(self, "domains", tuple(normalize_service_hostname(d) for d in values))


@dataclass(frozen=True)
class AccessOptions:
    """Explicit controls for persistent household restrictions."""

    enabled: bool = True
    reject_wifi_joins: bool = False
    dns_failure_percent: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool) or not isinstance(self.reject_wifi_joins, bool):
            raise ValidationError("access options must be true or false")
        if (
            isinstance(self.dns_failure_percent, bool)
            or not isinstance(self.dns_failure_percent, int)
            or not 0 <= self.dns_failure_percent <= 100
        ):
            raise ValidationError("DNS failure chance must be a whole number from 0 to 100")


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


@dataclass(frozen=True)
class SavedSettings:
    """Every saved setting that can move between installations, without usage."""

    profiles: Tuple[Profile, ...] = ()
    devices: Tuple[Device, ...] = ()
    groups: Tuple[DeviceGroup, ...] = ()
    schedules: Tuple[Schedule, ...] = ()
    mac_policy: MacPolicy = MacPolicy()
    website_policy: WebsitePolicy = WebsitePolicy()
    data_limits: Tuple[DataLimit, ...] = ()
    group_data_limits: Tuple[GroupDataLimit, ...] = ()
    service_quotas: Tuple[ServiceQuota, ...] = ()
    time_quotas: Tuple[TimeQuota, ...] = ()
    service_assignments: Tuple[ServiceAssignment, ...] = ()
    everyone_else_profile_id: Optional[str] = None
    unaffected_devices: Tuple[UnaffectedDevice, ...] = ()
    scenarios: Tuple[Scenario, ...] = ()
    site_blocks: Tuple[SiteBlock, ...] = ()
    access_options: AccessOptions = AccessOptions()
    persist_profiles_after_gateway_outage: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.persist_profiles_after_gateway_outage, bool):
            raise ValidationError("gateway-outage persistence must be true or false")
