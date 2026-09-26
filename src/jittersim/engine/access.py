"""Bounded access policies shared by the dashboard, helper and browser proxy."""

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from ipaddress import IPv4Address, IPv4Network
from typing import Optional, Tuple

from jittersim.config.models import (
    MAX_BLOCK_DOMAINS,
    MAX_SITE_BLOCKS,
    AccessOptions,
    MacPolicy,
    SavedSettings,
    SiteBlock,
    ValidationError,
    WebsitePolicy,
    _validate_identifier,
    normalize_domain,
    normalize_mac_address,
)

MAX_ACCESS_CLIENTS = 256
MAX_ACCESS_BYTES = 256 * 1024
MAX_EXPANDED_DOMAINS = 16384
TEST_NETWORK = IPv4Network("192.168.50.0/24")


def domain_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def validate_blocks(blocks, devices, groups):
    if len(blocks) > MAX_SITE_BLOCKS or len({rule.id for rule in blocks}) != len(blocks):
        raise ValidationError("use at most 32 site rules with unique IDs")
    if sum(len(rule.domains) for rule in blocks) > MAX_BLOCK_DOMAINS:
        raise ValidationError("site rules may contain at most 512 domains in total")
    for rule in blocks:
        if rule.device_id is not None and rule.device_id not in devices:
            raise ValidationError("site rule refers to a missing device")
        if rule.group_id is not None and rule.group_id not in groups:
            raise ValidationError("site rule refers to a missing group")


@dataclass(frozen=True)
class AccessClient:
    id: str
    address: str
    mac: Optional[str]
    block_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AccessPlan:
    options: AccessOptions = AccessOptions()
    mac: MacPolicy = MacPolicy()
    websites: WebsitePolicy = WebsitePolicy()
    blocks: Tuple[SiteBlock, ...] = ()
    clients: Tuple[AccessClient, ...] = ()

    def blocked_domains(self, client_id: Optional[str] = None) -> tuple:
        domains = set(self.websites.blocked_domains)
        selected = next((client.block_ids for client in self.clients if client.id == client_id), ())
        for rule in self.blocks:
            if rule.enabled and (
                rule.device_id is None and rule.group_id is None or rule.id in selected
            ):
                domains.update(rule.domains)
        return tuple(sorted(domains))

    def permits_host(self, address: str, host: str) -> bool:
        if not self.options.enabled:
            return True
        host = normalize_domain(host)
        client = next((client for client in self.clients if client.address == address), None)
        if any(
            domain_matches(host, domain)
            for domain in self.blocked_domains(client.id if client else None)
        ):
            return False
        return self.websites.mode != "allow_only" or any(
            domain_matches(host, domain) for domain in self.websites.allowed_domains
        )

    def payload(self) -> dict:
        return {"version": 1, **asdict(self)}

    def digest(self) -> str:
        return sha256(json.dumps(self.payload(), sort_keys=True).encode()).hexdigest()

    @property
    def has_dns_rules(self) -> bool:
        return self.options.enabled and (
            self.websites.mode == "allow_only"
            or bool(self.websites.blocked_domains)
            or any(rule.enabled for rule in self.blocks)
        )


def access_from_payload(value: object) -> AccessPlan:
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value.get("version") != 1
    ):
        raise ValidationError("unsupported access policy")
    if len(json.dumps(value).encode()) > MAX_ACCESS_BYTES:
        raise ValidationError("access policy is too large")
    try:
        options = AccessOptions(**value.get("options", {}))
        mac = MacPolicy(**value.get("mac", {}))
        websites = WebsitePolicy(**value.get("websites", {}))
        blocks = tuple(SiteBlock(**rule) for rule in value.get("blocks", []))
        raw_clients = value.get("clients", [])
        if not isinstance(raw_clients, (list, tuple)) or len(raw_clients) > MAX_ACCESS_CLIENTS:
            raise ValidationError("too many access clients")
        clients = []
        rule_ids = {rule.id for rule in blocks}
        for value in raw_clients:
            client = AccessClient(**value)
            # Reuse the rule identifier validation without accepting executable text.
            _validate_identifier(client.id, "access client id")
            address = IPv4Address(client.address)
            if address not in TEST_NETWORK or address in (
                TEST_NETWORK.network_address,
                TEST_NETWORK.broadcast_address,
                IPv4Address("192.168.50.1"),
            ):
                raise ValidationError("access client must use a test Wi-Fi address")
            mac_value = normalize_mac_address(client.mac) if client.mac is not None else None
            if (
                not isinstance(client.block_ids, (list, tuple))
                or len(client.block_ids) > MAX_SITE_BLOCKS
                or not set(client.block_ids) <= rule_ids
            ):
                raise ValidationError("access client refers to a missing block")
            clients.append(
                AccessClient(client.id, str(address), mac_value, tuple(client.block_ids))
            )
        if len({c.id for c in clients}) != len(clients) or len({c.address for c in clients}) != len(
            clients
        ):
            raise ValidationError("access clients must have distinct identities and addresses")
        if len({c.mac for c in clients if c.mac}) != len([c for c in clients if c.mac]):
            raise ValidationError("access clients must have distinct MAC addresses")
        validate_blocks(blocks, {c.id for c in clients}, {r.group_id for r in blocks if r.group_id})
        plan = AccessPlan(options, mac, websites, blocks, tuple(clients))
        # Equivalent clients share a DNS view. Bound worst-case distinct views too.
        views = {plan.blocked_domains(c.id) for c in clients} | {plan.blocked_domains()}
        if sum(len(view) + len(websites.allowed_domains) for view in views) > MAX_EXPANDED_DOMAINS:
            raise ValidationError("too many distinct device and domain combinations")
        return plan
    except (KeyError, TypeError, AttributeError) as error:
        raise ValidationError("invalid access policy") from error


def build_access_plan(settings: SavedSettings) -> AccessPlan:
    devices = {device.id: device for device in settings.devices}
    groups = {group.id: group for group in settings.groups}
    validate_blocks(settings.site_blocks, devices, groups)
    clients = []
    for device in devices.values():
        selected = tuple(
            rule.id
            for rule in settings.site_blocks
            if rule.device_id == device.id
            or (rule.group_id is not None and device.id in groups[rule.group_id].device_ids)
        )
        clients.append(
            AccessClient(device.id, str(device.ipv4_address), device.mac_address, selected)
        )
    plan = AccessPlan(
        settings.access_options,
        settings.mac_policy,
        settings.website_policy,
        settings.site_blocks,
        tuple(clients),
    )
    return access_from_payload(json.loads(json.dumps(plan.payload())))
