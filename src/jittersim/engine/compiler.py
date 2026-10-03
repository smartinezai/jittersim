"""Compile validated configuration into a deterministic network plan."""

import re
from dataclasses import replace
from ipaddress import IPv4Address
from typing import Mapping, Optional, Sequence

from jittersim.config.exemptions import MAX_UNAFFECTED_DEVICES, normalize_exempt_mac
from jittersim.config.models import (
    Device,
    MacPolicy,
    Profile,
    ValidationError,
    normalize_service_hostname,
)

from .access import AccessPlan
from .plan import (
    EVERYONE_ELSE_QUEUE_ID,
    GENERAL_QUEUE_ID,
    Direction,
    EveryoneElseRule,
    GeneralProfileRule,
    MarkRule,
    NetworkPlan,
    QueueSpec,
    ServiceAssignmentRule,
    ServiceListRule,
    ServiceQuotaRule,
    WebsiteFilter,
)


_LIST_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class PlanError(ValueError):
    """Raised when a collection of devices cannot form one safe network plan."""


MAX_DEVICES_PER_PLAN = 15
MAX_SERVICE_QUOTAS_PER_PLAN = 15
# The kernel's prio qdisc supports at most 16 bands and band 0 carries unmarked
# traffic, so devices and service rules share 15 marks.
MAX_SHAPED_MARKS_PER_PLAN = 15
MAX_SERVICE_ASSIGNMENT_DOMAINS_PER_PLAN = 200
MAX_SERVICE_LIST_DOMAINS_PER_PLAN = 400


def compile_plan(
    *,
    devices: Sequence[Device],
    profiles: Mapping[str, Profile],
    protected_mac_addresses: Sequence[str] = (),
    unverified_device_ids: Sequence[str] = (),
    mac_policy: MacPolicy = MacPolicy(),
    global_blocked_addresses: Sequence[IPv4Address] = (),
    global_allowed_addresses: Optional[Sequence[IPv4Address]] = None,
    website_filters: Sequence[WebsiteFilter] = (),
    blocked_device_addresses: Sequence[IPv4Address] = (),
    service_quota_rules: Sequence[ServiceQuotaRule] = (),
    service_assignment_rules: Sequence[ServiceAssignmentRule] = (),
    general_profile: Optional[GeneralProfileRule] = None,
    service_list_rules: Sequence[ServiceListRule] = (),
    everyone_else: Optional[EveryoneElseRule] = None,
    access: Optional[AccessPlan] = None,
) -> NetworkPlan:
    """Compile device assignments into deterministic marks and directional queues."""

    if general_profile is not None and everyone_else is not None:
        raise PlanError("a general profile already covers everyone else")

    if len(protected_mac_addresses) > MAX_UNAFFECTED_DEVICES:
        raise PlanError("a plan cannot contain more than 256 protected MAC addresses")
    try:
        protected_macs = tuple(
            sorted(set(normalize_exempt_mac(mac) for mac in protected_mac_addresses))
        )
    except ValueError as error:
        raise PlanError(str(error)) from error
    if any(device.mac_address in protected_macs for device in devices):
        raise PlanError("a protected client cannot have profile rules")
    if len(devices) > MAX_DEVICES_PER_PLAN:
        raise PlanError(f"a plan cannot contain more than {MAX_DEVICES_PER_PLAN} devices")
    if general_profile is not None:
        if devices or service_quota_rules or service_assignment_rules or website_filters or blocked_device_addresses:
            raise PlanError("a general profile cannot contain individual profile rules")
        if general_profile.profile_id not in profiles:
            raise PlanError("the general profile is missing")
        if type(general_profile.outage_active) is not bool:
            raise PlanError("the general profile outage state is invalid")
        if general_profile.allowed_addresses is not None and (
            len(general_profile.allowed_addresses) > 4096
            or any(not isinstance(address, IPv4Address) for address in general_profile.allowed_addresses)
        ):
            raise PlanError("the general profile destinations are invalid")

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
    if general_profile is not None:
        profile = profiles[general_profile.profile_id]
        queues.extend((
            QueueSpec(GENERAL_QUEUE_ID, Direction.UPLOAD, 1, profile.upload),
            QueueSpec(GENERAL_QUEUE_ID, Direction.DOWNLOAD, 1, profile.download),
        ))
    for index, device_id in enumerate(sorted(devices_by_id), start=1):
        device = devices_by_id[device_id]
        profile = profiles[device.profile_id]
        mark = index
        mark_rules.append(
            MarkRule(
                device_id=device.id,
                ipv4_address=device.ipv4_address,
                mark=mark,
                homepage_bounce_percent=profile.homepage_bounce_percent,
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
        len(mark_rules)
        + len(service_quota_rules)
        + len(service_assignment_rules)
        + len(service_list_rules)
        + (1 if general_profile is not None else 0)
        + (1 if everyone_else is not None else 0)
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
            homepage_bounce_percent=rule.homepage_bounce_percent,
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
                homepage_bounce_percent=rule.homepage_bounce_percent,
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

    normalized_everyone_else = None
    everyone_else_queues = ()
    if everyone_else is not None:
        try:
            unaffected_macs = tuple(
                sorted(
                    dict.fromkeys(
                        normalize_exempt_mac(value) for value in everyone_else.unaffected_macs
                    )
                )
            )
        except ValueError as error:
            raise PlanError("everyone else has an invalid unaffected MAC address") from error
        if len(unaffected_macs) > MAX_UNAFFECTED_DEVICES:
            raise PlanError(
                f"a plan cannot list more than {MAX_UNAFFECTED_DEVICES} unaffected devices"
            )
        mark = len(mark_rules) + len(service_quota_rules) + len(service_assignment_rules) + 1
        normalized_everyone_else = replace(
            everyone_else, mark=mark, unaffected_macs=unaffected_macs
        )
        everyone_else_queues = (
            QueueSpec(
                device_id=EVERYONE_ELSE_QUEUE_ID,
                direction=Direction.UPLOAD,
                mark=mark,
                impairment=everyone_else.upload_impairment,
            ),
            QueueSpec(
                device_id=EVERYONE_ELSE_QUEUE_ID,
                direction=Direction.DOWNLOAD,
                mark=mark,
                impairment=everyone_else.download_impairment,
            ),
        )

    list_queues = []
    normalized_lists = []
    list_domains = []
    first_list_mark = (
        len(mark_rules)
        + len(service_quota_rules)
        + len(service_assignment_rules)
        + (1 if general_profile is not None else 0)
        + (1 if everyone_else is not None else 0)
        + 1
    )
    if sum(len(rule.domains) for rule in service_list_rules) > MAX_SERVICE_LIST_DOMAINS_PER_PLAN:
        raise PlanError(
            f"a plan cannot contain more than {MAX_SERVICE_LIST_DOMAINS_PER_PLAN} "
            "service list hostnames"
        )
    list_ids = set()
    for index, list_rule in enumerate(
        sorted(service_list_rules, key=lambda item: item.list_id), start=first_list_mark
    ):
        if not isinstance(list_rule.list_id, str) or not _LIST_ID_PATTERN.fullmatch(list_rule.list_id):
            raise PlanError("service list id is invalid")
        if list_rule.list_id in list_ids:
            raise PlanError(f"duplicate service list id: {list_rule.list_id}")
        list_ids.add(list_rule.list_id)
        if type(list_rule.outage_active) is not bool:
            raise PlanError(f"service list {list_rule.list_id} outage state is invalid")
        if not list_rule.domains:
            raise PlanError(f"service list {list_rule.list_id} has no hostnames")
        try:
            domains = tuple(
                dict.fromkeys(normalize_service_hostname(domain) for domain in list_rule.domains)
            )
        except ValidationError as error:
            raise PlanError("service list must contain only DNS hostnames") from error
        for domain in domains:
            other = next(
                (
                    owner
                    for prior, owner in list_domains
                    if owner != list_rule.list_id
                    and (
                        domain == prior
                        or domain.endswith("." + prior)
                        or prior.endswith("." + domain)
                    )
                ),
                None,
            )
            if other is not None:
                raise PlanError(
                    f"service lists {other} and {list_rule.list_id} overlap on {domain}"
                )
            list_domains.append((domain, list_rule.list_id))
        normalized_lists.append(
            ServiceListRule(
                list_id=list_rule.list_id,
                domains=domains,
                mark=index,
                upload_impairment=list_rule.upload_impairment,
                download_impairment=list_rule.download_impairment,
                outage_active=list_rule.outage_active,
                homepage_bounce_percent=list_rule.homepage_bounce_percent,
            )
        )
        for direction, impairment in (
            (Direction.UPLOAD, list_rule.upload_impairment),
            (Direction.DOWNLOAD, list_rule.download_impairment),
        ):
            list_queues.append(
                QueueSpec(
                    device_id=f"servicelist:{list_rule.list_id}",
                    direction=direction,
                    mark=index,
                    impairment=impairment,
                )
            )
    if first_list_mark + len(normalized_lists) - 1 > MAX_SHAPED_MARKS_PER_PLAN:
        raise PlanError("service list mark is outside the safe range")

    return NetworkPlan(
        protected_mac_addresses=protected_macs,
        unverified_device_ids=tuple(unverified_device_ids),
        mark_rules=tuple(mark_rules),
        queues=(
            tuple(queues)
            + tuple(service_queues)
            + tuple(assignment_queues)
            + everyone_else_queues
            + tuple(list_queues)
        ),
        mac_policy=mac_policy,
        global_allowed_addresses=None
        if global_allowed_addresses is None
        else tuple(sorted(set(global_allowed_addresses), key=str)),
        global_blocked_addresses=tuple(sorted(set(global_blocked_addresses), key=str)),
        website_filters=tuple(website_filters),
        blocked_device_addresses=tuple(sorted(set(blocked_device_addresses), key=str)),
        service_quota_rules=normalized_service_rules,
        service_assignment_rules=tuple(normalized_assignments),
        general_profile=(replace(general_profile, homepage_bounce_percent=
            profiles[general_profile.profile_id].homepage_bounce_percent)
            if general_profile is not None else None),
        service_list_rules=tuple(normalized_lists),
        everyone_else=normalized_everyone_else,
        access=access,
        homepage_policy_version=1,
    )
