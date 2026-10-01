"""Turn saved service lists into plan rules."""

from typing import Mapping, Optional

from jittersim.config.models import Profile
from jittersim.config.storage import ConfigRepository

from .compiler import PlanError
from .plan import ServiceListRule


def build_service_list_rules(
    repository: ConfigRepository, profiles: Optional[Mapping[str, Profile]] = None
) -> tuple:
    """Return one rule for each enabled list using its profile's impairments.

    Lists apply to every affected client, so they do not depend on saved
    devices, schedules, quotas or the general profile.
    """

    saved_profiles = profiles or {profile.id: profile for profile in repository.list_profiles()}
    rules = []
    for service_list in repository.list_service_lists():
        if not service_list.enabled:
            continue
        profile = saved_profiles.get(service_list.profile_id)
        if profile is None:
            raise PlanError(f"service list {service_list.id} references a missing profile")
        rules.append(
            ServiceListRule(
                list_id=service_list.id,
                domains=service_list.domains,
                mark=0,
                upload_impairment=profile.upload,
                download_impairment=profile.download,
            )
        )
    return tuple(rules)
