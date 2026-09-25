"""Export saved settings as JSON and check imported settings before saving."""

from collections import Counter
from dataclasses import asdict
from typing import Any, Dict, Iterable

from .models import MAX_UNAFFECTED_DEVICES, SavedSettings

SETTINGS_FORMAT = "jittersim-settings"
SETTINGS_VERSION = 1


def _json_values(value: Any) -> Any:
    # asdict keeps tuples; JSON only has lists.
    if isinstance(value, dict):
        return {key: _json_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_values(item) for item in value]
    return value


def _reset_fields(limit: Any) -> Dict[str, Any]:
    return {
        "reset_mode": limit.reset_mode,
        "reset_interval_seconds": limit.reset_interval_seconds,
        "reset_times": list(limit.reset_times),
        "enabled": limit.enabled,
    }


def settings_to_payload(settings: SavedSettings, *, exported_at: int) -> Dict[str, Any]:
    """Describe saved settings as JSON values, leaving out usage and counters."""

    return {
        "format": SETTINGS_FORMAT,
        "version": SETTINGS_VERSION,
        "exported_at": exported_at,
        "profiles": [_json_values(asdict(profile)) for profile in settings.profiles],
        "devices": [
            {
                "id": device.id,
                "name": device.name,
                "ipv4_address": str(device.ipv4_address),
                "profile_id": device.profile_id,
                "mac_address": device.mac_address,
            }
            for device in settings.devices
        ],
        "groups": [
            {
                "id": group.id,
                "name": group.name,
                "device_ids": list(group.device_ids),
                "profile_id": group.profile_id,
            }
            for group in settings.groups
        ],
        "schedules": [_json_values(asdict(schedule)) for schedule in settings.schedules],
        "policies": {
            "mac": _json_values(asdict(settings.mac_policy)),
            "websites": _json_values(asdict(settings.website_policy)),
        },
        "data_limits": [
            {
                "device_id": limit.device_id,
                "profile_id": limit.profile_id,
                "limit_bytes": limit.limit_bytes,
                **_reset_fields(limit),
            }
            for limit in settings.data_limits
        ],
        "group_data_limits": [
            {
                "group_id": limit.group_id,
                "profile_id": limit.profile_id,
                "limit_bytes": limit.limit_bytes,
                **_reset_fields(limit),
            }
            for limit in settings.group_data_limits
        ],
        "service_quotas": [
            {
                "id": quota.id,
                "device_id": quota.device_id,
                "name": quota.name,
                "domains": list(quota.domains),
                "profile_id": quota.profile_id,
                "limit_bytes": quota.limit_bytes,
                "weight_percent": quota.weight_percent,
                **_reset_fields(quota),
            }
            for quota in settings.service_quotas
        ],
        "time_quotas": [
            {
                "id": quota.id,
                "device_id": quota.device_id,
                "name": quota.name,
                "domains": list(quota.domains),
                "profile_id": quota.profile_id,
                "limit_seconds": quota.limit_seconds,
                "window_duration_seconds": quota.window_duration_seconds,
                **_reset_fields(quota),
            }
            for quota in settings.time_quotas
        ],
        "service_assignments": [
            _json_values(asdict(assignment)) for assignment in settings.service_assignments
        ],
        "everyone_else": {
            "profile_id": settings.everyone_else_profile_id,
            "unaffected_devices": [
                {"mac_address": device.mac_address, "name": device.name}
                for device in settings.unaffected_devices
            ],
        },
    }


def _require_unique(kind: str, identifiers: Iterable[str]) -> None:
    for identifier, count in Counter(identifiers).items():
        if count > 1:
            raise ValueError(f"{kind} {identifier} appears twice")


def check_settings(
    settings: SavedSettings, *, max_traffic_classes: int, max_service_domains: int
) -> None:
    """Raise ValueError when imported settings do not fit together."""

    _require_unique("profile", (profile.id for profile in settings.profiles))
    _require_unique("device", (device.id for device in settings.devices))
    _require_unique("device address", (str(device.ipv4_address) for device in settings.devices))
    _require_unique("group", (group.id for group in settings.groups))
    _require_unique("schedule", (schedule.id for schedule in settings.schedules))
    _require_unique("data limit for device", (limit.device_id for limit in settings.data_limits))
    _require_unique(
        "shared limit for group", (limit.group_id for limit in settings.group_data_limits)
    )
    _require_unique("service limit", (quota.id for quota in settings.service_quotas))
    _require_unique("time limit", (quota.id for quota in settings.time_quotas))
    _require_unique(
        "browser service profile",
        (assignment.id for assignment in settings.service_assignments),
    )
    _require_unique(
        "unaffected device", (device.mac_address for device in settings.unaffected_devices)
    )
    if len(settings.unaffected_devices) > MAX_UNAFFECTED_DEVICES:
        raise ValueError(f"the unaffected list holds at most {MAX_UNAFFECTED_DEVICES} devices")

    profile_ids = {profile.id for profile in settings.profiles}
    device_ids = {device.id for device in settings.devices}
    group_ids = {group.id for group in settings.groups}

    def require_profile(owner: str, profile_id: str) -> None:
        if profile_id not in profile_ids:
            raise ValueError(f"{owner} uses the missing profile {profile_id}")

    def require_device(owner: str, device_id: str) -> None:
        if device_id not in device_ids:
            raise ValueError(f"{owner} uses the missing device {device_id}")

    for device in settings.devices:
        require_profile(f"device {device.id}", device.profile_id)
    grouped = set()
    for group in settings.groups:
        if group.profile_id is not None:
            require_profile(f"group {group.id}", group.profile_id)
        for device_id in group.device_ids:
            require_device(f"group {group.id}", device_id)
            if device_id in grouped:
                raise ValueError(f"device {device_id} is in more than one group")
            grouped.add(device_id)
    for schedule in settings.schedules:
        if schedule.device_id is not None:
            require_device(f"schedule {schedule.id}", schedule.device_id)
        if schedule.group_id is not None and schedule.group_id not in group_ids:
            raise ValueError(f"schedule {schedule.id} uses the missing group {schedule.group_id}")
        require_profile(f"schedule {schedule.id}", schedule.profile_id)
    for limit in settings.data_limits:
        require_device(f"data limit for {limit.device_id}", limit.device_id)
        require_profile(f"data limit for {limit.device_id}", limit.profile_id)
    for limit in settings.group_data_limits:
        if limit.group_id not in group_ids:
            raise ValueError(f"shared limit uses the missing group {limit.group_id}")
        require_profile(f"shared limit for {limit.group_id}", limit.profile_id)
    for kind, quotas in (
        ("service limit", settings.service_quotas),
        ("time limit", settings.time_quotas),
    ):
        for quota in quotas:
            require_device(f"{kind} {quota.id}", quota.device_id)
            require_profile(f"{kind} {quota.id}", quota.profile_id)

    for assignment in settings.service_assignments:
        require_device(f"browser service profile {assignment.id}", assignment.device_id)
        require_profile(f"browser service profile {assignment.id}", assignment.profile_id)
    if settings.everyone_else_profile_id is not None:
        require_profile("everyone else", settings.everyone_else_profile_id)
    devices_by_mac = {
        device.mac_address: device.id for device in settings.devices if device.mac_address
    }
    for unaffected in settings.unaffected_devices:
        if unaffected.mac_address in devices_by_mac:
            raise ValueError(
                f"{unaffected.mac_address} is unaffected and belongs to device "
                f"{devices_by_mac[unaffected.mac_address]}"
            )
    # Only enabled browser service profiles reach the plan.
    assignments = tuple(
        assignment for assignment in settings.service_assignments if assignment.enabled
    )
    domains_by_device: Dict[str, list] = {}
    for assignment in assignments:
        previous = domains_by_device.setdefault(assignment.device_id, [])
        for domain in assignment.domains:
            if any(
                domain == other or domain.endswith("." + other) or other.endswith("." + domain)
                for other in previous
            ):
                raise ValueError(
                    f"browser service profiles overlap on device {assignment.device_id}"
                )
        previous.extend(assignment.domains)
    if sum(len(assignment.domains) for assignment in assignments) > max_service_domains:
        raise ValueError(
            f"browser service profiles can list at most {max_service_domains} domains"
        )

    traffic_classes = (
        len(settings.devices)
        + sum(quota.enabled for quota in settings.service_quotas)
        + sum(quota.enabled for quota in settings.time_quotas)
        + len(assignments)
        # One class serves everyone else's profile and all its schedules.
        + (
            settings.everyone_else_profile_id is not None
            or any(schedule.everyone_else and schedule.enabled for schedule in settings.schedules)
        )
    )
    if traffic_classes > max_traffic_classes:
        raise ValueError(
            f"JitterSim can shape at most {max_traffic_classes} devices and service rules "
            "together"
        )
