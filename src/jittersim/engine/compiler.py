"""Compile validated configuration into a deterministic network plan."""

from ipaddress import IPv4Address
from typing import Mapping, Sequence

from jittersim.config.models import Device, MacPolicy, Profile, normalize_service_hostname

from .plan import (
    Direction,
    MarkRule,
    NetworkPlan,
    QueueSpec,
    ServiceAssignmentRule,
    ServiceQuotaRule,
    WebsiteFilter,
)


class PlanError(ValueError):
    """Raised when a collection of devices cannot form one safe network plan."""


MAX_DEVICES_PER_PLAN = 15
MAX_SERVICE_QUOTAS_PER_PLAN = 15
# The kernel's prio qdisc supports at most 16 bands and band 0 carries unmarked
# traffic, so devices and service rules share 15 marks.
MAX_SHAPED_MARKS_PER_PLAN = 15
MAX_SERVICE_ASSIGNMENT_DOMAINS_PER_PLAN = 200


def compile_plan(
    *,
    devices: Sequence[Device],
    profiles: Mapping[str, Profile],
    mac_policy: MacPolicy = MacPolicy(),
    global_blocked_addresses: Sequence[IPv4Address] = (),
    website_filters: Sequence[WebsiteFilter] = (),
    blocked_device_addresses: Sequence[IPv4Address] = (),
    service_quota_rules: Sequence[ServiceQuotaRule] = (),
    service_assignment_rules: Sequence[ServiceAssignmentRule] = (),
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

    if len(service_quota_rules) > MAX_SERVICE_QUOTAS_PER_PLAN:
        raise PlanError(
            f"a plan cannot contain more than {MAX_SERVICE_QUOTAS_PER_PLAN} service quotas"
        )
    if (
        len(mark_rules) + len(service_quota_rules) + len(service_assignment_rules)
        > MAX_SHAPED_MARKS_PER_PLAN
    ):
        raise PlanError(
            "a plan cannot contain more than 15 combined devices and service rules"
        )
    if sum(len(rule.domains) for rule in service_assignment_rules) > MAX_SERVICE_ASSIGNMENT_DOMAINS_PER_PLAN:
        raise PlanError("a plan cannot contain more than 200 browser service domains")
    quota_ids = set()
    service_queues = []
    for index, quota_rule in enumerate(
        sorted(service_quota_rules, key=lambda rule: rule.quota_id),
        start=len(mark_rules) + 1,
    ):
        if quota_rule.quota_id in quota_ids:
            raise PlanError(f"duplicate service quota id: {quota_rule.quota_id}")
        if quota_rule.device_id not in devices_by_id:
            raise PlanError(
                f"service quota {quota_rule.quota_id} references unknown device"
            )
        device = devices_by_id[quota_rule.device_id]
        if quota_rule.device_ip != device.ipv4_address:
            raise PlanError(
                f"service quota {quota_rule.quota_id} has the wrong device address"
            )
        if not quota_rule.destination_addresses:
            raise PlanError(
                f"service quota {quota_rule.quota_id} has no destination addresses"
            )
        if any(address.version != 4 for address in quota_rule.destination_addresses):
            raise PlanError("service quota contains a non-IPv4 destination")
        if index > MAX_SHAPED_MARKS_PER_PLAN:
            raise PlanError("service quota mark is outside the safe range")
        quota_ids.add(quota_rule.quota_id)
        service_queues.extend(
            (
                QueueSpec(
                    device_id=f"quota:{quota_rule.quota_id}",
                    direction=Direction.UPLOAD,
                    mark=index,
                    impairment=quota_rule.upload_impairment,
                ),
                QueueSpec(
                    device_id=f"quota:{quota_rule.quota_id}",
                    direction=Direction.DOWNLOAD,
                    mark=index,
                    impairment=quota_rule.download_impairment,
                ),
            )
        )
    normalized_service_rules = tuple(
        ServiceQuotaRule(
            quota_id=rule.quota_id,
            device_id=rule.device_id,
            device_ip=rule.device_ip,
            destination_addresses=tuple(sorted(set(rule.destination_addresses), key=str)),
            mark=mark,
            upload_impairment=rule.upload_impairment,
            download_impairment=rule.download_impairment,
            active=rule.active,
        )
        for rule, mark in zip(
            sorted(service_quota_rules, key=lambda rule: rule.quota_id),
            range(len(mark_rules) + 1, len(mark_rules) + 1 + len(service_quota_rules)),
        )
    )

    assignment_ids = set()
    domains_by_device = {}
    assignment_queues = []
    normalized_assignments = []
    for index, rule in enumerate(
        sorted(service_assignment_rules, key=lambda assignment: assignment.assignment_id),
        start=len(mark_rules) + len(service_quota_rules) + 1,
    ):
        if rule.assignment_id in assignment_ids:
            raise PlanError(f"duplicate service assignment id: {rule.assignment_id}")
        device = devices_by_id.get(rule.device_id)
        if device is None or rule.device_ip != device.ipv4_address:
            raise PlanError(f"service assignment {rule.assignment_id} references an unknown device")
        if not rule.domains:
            raise PlanError(f"service assignment {rule.assignment_id} has no domains")
        try:
            normalized_domains = tuple(
                dict.fromkeys(normalize_service_hostname(domain) for domain in rule.domains)
            )
        except ValueError as error:
            raise PlanError("service assignment must contain only DNS hostnames") from error
        prior_domains = domains_by_device.setdefault(rule.device_id, [])
        for domain in normalized_domains:
            if any(
                domain == prior or domain.endswith("." + prior) or prior.endswith("." + domain)
                for prior in prior_domains
            ):
                raise PlanError(f"service assignment domains overlap on device {rule.device_id}")
            prior_domains.append(domain)
        assignment_ids.add(rule.assignment_id)
        normalized_assignments.append(
            ServiceAssignmentRule(
                assignment_id=rule.assignment_id,
                device_id=rule.device_id,
                device_ip=device.ipv4_address,
                domains=normalized_domains,
                mark=index,
                upload_impairment=rule.upload_impairment,
                download_impairment=rule.download_impairment,
            )
        )
        assignment_queues.extend(
            (
                QueueSpec(
                    device_id=f"assignment:{rule.assignment_id}",
                    direction=Direction.UPLOAD,
                    mark=index,
                    impairment=rule.upload_impairment,
                ),
                QueueSpec(
                    device_id=f"assignment:{rule.assignment_id}",
                    direction=Direction.DOWNLOAD,
                    mark=index,
                    impairment=rule.download_impairment,
                ),
            )
        )

    return NetworkPlan(
        mark_rules=tuple(mark_rules),
        queues=tuple(queues) + tuple(service_queues) + tuple(assignment_queues),
        mac_policy=mac_policy,
        global_blocked_addresses=tuple(sorted(set(global_blocked_addresses), key=str)),
        website_filters=tuple(website_filters),
        blocked_device_addresses=tuple(sorted(set(blocked_device_addresses), key=str)),
        service_quota_rules=normalized_service_rules,
        service_assignment_rules=tuple(normalized_assignments),
    )
