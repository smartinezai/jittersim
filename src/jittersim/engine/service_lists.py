"""Turn saved service lists into plan rules."""

from typing import Callable, Mapping, Optional, Tuple

from jittersim.config.models import Profile, ServiceList
from jittersim.config.storage import ConfigRepository

from .compiler import PlanError
from .plan import ServiceListRule

# Given a list and its saved profile, return the profile values to use now and
# whether a random outage cuts the list right now.
ListRoll = Callable[[ServiceList, Profile], Tuple[Profile, bool]]


def build_service_list_rules(
    repository: ConfigRepository,
    profiles: Optional[Mapping[str, Profile]] = None,
    *,
    roll: Optional[ListRoll] = None,
) -> tuple:
    """Return one rule for each enabled list using its profile's impairments.

    Lists apply to every affected client, so they do not depend on saved
    devices, schedules, quotas or the general profile. `roll` lets the
    scheduler give a list its rerolled values and its random outages. Without
    it a list uses the saved profile as it is and is never in an outage.
    """

    saved_profiles = profiles or {profile.id: profile for profile in repository.list_profiles()}
    rules = []
    for service_list in repository.list_service_lists():
        if not service_list.enabled:
            continue
        profile = saved_profiles.get(service_list.profile_id)
        if profile is None:
            raise PlanError(f"service list {service_list.id} references a missing profile")
        outage_active = False
        if roll is not None:
            profile, outage_active = roll(service_list, profile)
        rules.append(
            ServiceListRule(
                list_id=service_list.id,
                domains=service_list.domains,
                mark=0,
                upload_impairment=profile.upload,
                download_impairment=profile.download,
                homepage_bounce_percent=profile.homepage_bounce_percent,
                outage_active=outage_active,
            )
        )
    return tuple(rules)
