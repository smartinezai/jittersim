"""Services that connect persisted configuration to the pure policy compiler."""

from dataclasses import replace

from jittersim.config.storage import ConfigRepository

from .access import build_access_plan
from .compiler import compile_plan
from .eligibility import eligible_clients
from .general import compile_general_plan
from .plan import (
    EveryoneElseRule,
    GeneralProfileRule,
    NetworkPlan,
    ServiceAssignmentRule,
    ServiceQuotaRule,
)
from .service_lists import build_service_list_rules
from .website import build_website_filters, lookup_session, resolve_domains


def compile_stored_plan(
    *, repository: ConfigRepository, connected_snapshot=None, device_scope=None,
    everyone_exceptions=None,
) -> NetworkPlan:
    """Compile every stored device assignment into one deterministic plan.

    With `device_scope` only those saved devices run: the saved Everyone and
    everyone-else profiles, service lists and every other device stay stopped.

    While an Everyone profile runs, the saved devices in `everyone_exceptions` run
    their own profile beside it and every other device stays on Everyone. None reads
    the exceptions the repository holds. An empty set is the whole-scope plan.

    The website lookups share one deadline, so a DNS server that does not answer
    delays the plan by seconds and not for good.
    """

    with lookup_session():
        return _compile_stored_plan(
            repository=repository, connected_snapshot=connected_snapshot,
            device_scope=device_scope, everyone_exceptions=everyone_exceptions,
        )


def _compile_stored_plan(
    *, repository: ConfigRepository, connected_snapshot=None, device_scope=None,
    everyone_exceptions=None,
) -> NetworkPlan:
    scoped = device_scope is not None
    general_profile = None if scoped else repository.get_general_profile()
    general_rule = None
    if general_profile is not None:
        if everyone_exceptions is None:
            everyone_exceptions = repository.get_everyone_exceptions()
        if not everyone_exceptions:
            return compile_general_plan(repository=repository, profile=general_profile)
        # Everyone runs and these devices opt out of it. Only they get device rules.
        device_scope = frozenset(everyone_exceptions)
        scoped = True
        general_rule = GeneralProfileRule(
            profile_id=general_profile.id,
            allowed_addresses=resolve_domains(domains=general_profile.website_domains)
            if general_profile.website_mode == "allow_only"
            else None,
        )
    clients = eligible_clients(repository, connected_snapshot)
    if scoped:
        clients = replace(
            clients, devices=tuple(device for device in clients.devices if device.id in device_scope)
        )
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
    for device in clients.devices:
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
    website_policy = repository.get_website_policy()
    global_blocked, website_filters = build_website_filters(
        devices=devices,
        profiles=profiles,
        global_policy=website_policy,
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
                    homepage_bounce_percent=profile.homepage_bounce_percent,
                    active=quota.used_bytes >= quota.limit_bytes,
                )
            )
    service_assignment_rules = []
    for assignment in repository.list_service_assignments():
        if not assignment.enabled or assignment.device_id in clients.excluded_ids:
            continue
        if scoped and assignment.device_id not in devices_by_id:
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
                homepage_bounce_percent=profile.homepage_bounce_percent,
            )
        )
    everyone_else = None
    everyone_else_profile_id = None if scoped else repository.get_everyone_else_profile_id()
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
            homepage_bounce_percent=profile.homepage_bounce_percent,
            unaffected_macs=tuple(
                unaffected.mac_address for unaffected in repository.list_unaffected_devices()
            ),
        )
    return compile_plan(
        devices=devices,
        profiles=profiles,
        protected_mac_addresses=clients.protected_macs,
        unverified_device_ids=clients.unverified_ids,
        mac_policy=repository.get_mac_policy(),
        access=build_access_plan(repository.access_settings()),
        global_blocked_addresses=global_blocked,
        global_allowed_addresses=resolve_domains(domains=website_policy.allowed_domains)
        if website_policy.mode == "allow_only"
        else None,
        website_filters=website_filters,
        service_quota_rules=service_quota_rules,
        service_assignment_rules=service_assignment_rules,
        # Service lists cover every affected client, so they also run beside Everyone.
        service_list_rules=(
            () if scoped and general_rule is None else build_service_list_rules(repository, profiles)
        ),
        everyone_else=everyone_else,
        general_profile=general_rule,
    )
