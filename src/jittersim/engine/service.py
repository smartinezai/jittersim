"""Services that connect persisted configuration to the pure policy compiler."""

from dataclasses import replace

from jittersim.config.storage import ConfigRepository

from .compiler import compile_plan
from .plan import EveryoneElseRule, NetworkPlan, ServiceAssignmentRule, ServiceQuotaRule
from .website import build_website_filters, resolve_domains


def compile_stored_plan(*, repository: ConfigRepository) -> NetworkPlan:
    """Compile every stored device assignment into one deterministic plan."""

    profiles = {profile.id: profile for profile in repository.list_profiles()}
    devices = []
    data_limits = {
        data_limit.device_id: data_limit for data_limit in repository.list_data_limits()
    }
    groups_by_device = {
        device_id: group
        for group in repository.list_device_groups()
        for device_id in group.device_ids
    }
    group_data_limits = {
        data_limit.group_id: data_limit
        for data_limit in repository.list_group_data_limits()
    }
    for device in repository.list_devices():
        group = groups_by_device.get(device.id)
        if group is not None and group.profile_id is not None:
            if group.profile_id not in profiles:
                raise ValueError(
                    f"device group {group.id} references a missing profile"
                )
            device = replace(device, profile_id=group.profile_id)
        data_limit = data_limits.get(device.id)
        if data_limit is not None and data_limit.enabled and data_limit.used_bytes >= data_limit.limit_bytes:
            if data_limit.profile_id not in profiles:
                raise ValueError(
                    f"data limit for device {device.id} references a missing profile"
                )
            device = replace(device, profile_id=data_limit.profile_id)
        elif group is not None:
            group_data_limit = group_data_limits.get(group.id)
            if (
                group_data_limit is not None
                and group_data_limit.enabled
                and group_data_limit.used_bytes >= group_data_limit.limit_bytes
            ):
                if group_data_limit.profile_id not in profiles:
                    raise ValueError(
                        f"group data limit for {group.id} references a missing profile"
                    )
                device = replace(device, profile_id=group_data_limit.profile_id)
        devices.append(device)
    global_blocked, website_filters = build_website_filters(
        devices=devices,
        profiles=profiles,
        global_policy=repository.get_website_policy(),
    )
    devices_by_id = {device.id: device for device in devices}
    service_quota_rules = []
    for quota in repository.list_service_quotas():
        if not quota.enabled:
            continue
        device = devices_by_id.get(quota.device_id)
        profile = profiles.get(quota.profile_id)
        if device is None:
            continue
        if profile is None:
            raise ValueError(f"service quota {quota.id} references a missing profile")
        addresses = resolve_domains(domains=quota.domains)
        if addresses:
            service_quota_rules.append(
                ServiceQuotaRule(
                    quota_id=quota.id,
                    device_id=device.id,
                    device_ip=device.ipv4_address,
                    destination_addresses=addresses,
                    mark=0,
                    upload_impairment=profile.upload,
                    download_impairment=profile.download,
                    active=quota.used_bytes >= quota.limit_bytes,
                )
            )
    service_assignment_rules = []
    for assignment in repository.list_service_assignments():
        if not assignment.enabled:
            continue
        device = devices_by_id.get(assignment.device_id)
        profile = profiles.get(assignment.profile_id)
        if device is None or profile is None:
            raise ValueError(f"service assignment {assignment.id} has a missing reference")
        service_assignment_rules.append(
            ServiceAssignmentRule(
                assignment_id=assignment.id,
                device_id=device.id,
                device_ip=device.ipv4_address,
                domains=assignment.domains,
                mark=0,
                upload_impairment=profile.upload,
                download_impairment=profile.download,
            )
        )
    everyone_else = None
    everyone_else_profile_id = repository.get_everyone_else_profile_id()
    if everyone_else_profile_id is not None:
        profile = profiles.get(everyone_else_profile_id)
        if profile is None:
            raise ValueError(
                f"everyone else references missing profile {everyone_else_profile_id}"
            )
        everyone_else = EveryoneElseRule(
            mark=0,
            upload_impairment=profile.upload,
            download_impairment=profile.download,
            unaffected_macs=tuple(
                unaffected.mac_address for unaffected in repository.list_unaffected_devices()
            ),
        )
    return compile_plan(
        devices=devices,
        profiles=profiles,
        mac_policy=repository.get_mac_policy(),
        global_blocked_addresses=global_blocked,
        website_filters=website_filters,
        service_quota_rules=service_quota_rules,
        service_assignment_rules=service_assignment_rules,
        everyone_else=everyone_else,
    )
