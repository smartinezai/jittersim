"""Compile validated configuration into a deterministic network plan."""

from ipaddress import IPv4Address
from typing import Mapping, Sequence

from jittersim.config.models import Device, MacPolicy, Profile

from .plan import Direction, MarkRule, NetworkPlan, QueueSpec, WebsiteFilter


class PlanError(ValueError):
    """Raised when a collection of devices cannot form one safe network plan."""


MAX_DEVICES_PER_PLAN = 15


def compile_plan(
    *,
    devices: Sequence[Device],
    profiles: Mapping[str, Profile],
    mac_policy: MacPolicy = MacPolicy(),
    global_blocked_addresses: Sequence[IPv4Address] = (),
    website_filters: Sequence[WebsiteFilter] = (),
    blocked_device_addresses: Sequence[IPv4Address] = (),
) -> NetworkPlan:
    """Compile device assignments into deterministic marks and directional queues."""

    if len(devices) > MAX_DEVICES_PER_PLAN:
        raise PlanError(f"a plan cannot contain more than {MAX_DEVICES_PER_PLAN} devices")

    devices_by_id = {}
    addresses_by_value = {}
    for device in devices:
        if device.id in devices_by_id:
            raise PlanError(f"duplicate device id: {device.id}")
        if device.ipv4_address in addresses_by_value:
            other_device_id = addresses_by_value[device.ipv4_address]
            raise PlanError(
                f"IPv4 address {device.ipv4_address} is assigned to both "
                f"{other_device_id} and {device.id}"
            )
        if device.profile_id not in profiles:
            raise PlanError(f"device {device.id} references missing profile {device.profile_id}")

        devices_by_id[device.id] = device
        addresses_by_value[device.ipv4_address] = device.id

    mark_rules = []
    queues = []
    for index, device_id in enumerate(sorted(devices_by_id), start=1):
        device = devices_by_id[device_id]
        profile = profiles[device.profile_id]
        mark = index
        mark_rules.append(
            MarkRule(
                device_id=device.id,
                ipv4_address=device.ipv4_address,
                mark=mark,
            )
        )
        queues.extend(
            (
                QueueSpec(
                    device_id=device.id,
                    direction=Direction.UPLOAD,
                    mark=mark,
                    impairment=profile.upload,
                ),
                QueueSpec(
                    device_id=device.id,
                    direction=Direction.DOWNLOAD,
                    mark=mark,
                    impairment=profile.download,
                ),
            )
        )

    device_addresses = set(addresses_by_value)
    seen_filter_devices = set()
    for website_filter in website_filters:
        if website_filter.device_ip not in device_addresses:
            raise PlanError(
                f"website filter references unknown device address {website_filter.device_ip}"
            )
        if website_filter.device_ip in seen_filter_devices:
            raise PlanError(
                f"duplicate website filter for device address {website_filter.device_ip}"
            )
        seen_filter_devices.add(website_filter.device_ip)
        if any(address.version != 4 for address in website_filter.blocked_addresses):
            raise PlanError("website filter contains a non-IPv4 blocked address")

    return NetworkPlan(
        mark_rules=tuple(mark_rules),
        queues=tuple(queues),
        mac_policy=mac_policy,
        global_blocked_addresses=tuple(sorted(set(global_blocked_addresses), key=str)),
        website_filters=tuple(website_filters),
        blocked_device_addresses=tuple(sorted(set(blocked_device_addresses), key=str)),
    )
