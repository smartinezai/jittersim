"""Build a bounded shared profile without individual device assignments."""

from jittersim.config.models import Profile
from jittersim.config.storage import ConfigRepository

from .compiler import compile_plan
from .plan import GeneralProfileRule, NetworkPlan
from .website import resolve_domains


def compile_general_plan(
    *, repository: ConfigRepository, profile: Profile, outage_active: bool = False
) -> NetworkPlan:
    """Keep global access checks separate from bypassable profile behavior."""

    website_policy = repository.get_website_policy()
    return compile_plan(
        devices=(),
        profiles={profile.id: profile},
        protected_mac_addresses=tuple(
            item.mac_address for item in repository.list_unaffected_devices()
        ),
        mac_policy=repository.get_mac_policy(),
        global_blocked_addresses=resolve_domains(domains=website_policy.blocked_domains),
        global_allowed_addresses=resolve_domains(domains=website_policy.allowed_domains)
        if website_policy.mode == "allow_only"
        else None,
        general_profile=GeneralProfileRule(
            profile_id=profile.id,
            allowed_addresses=resolve_domains(domains=profile.website_domains)
            if profile.website_mode == "allow_only"
            else None,
            outage_active=outage_active,
        ),
    )
