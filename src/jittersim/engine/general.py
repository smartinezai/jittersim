"""Build a bounded shared profile without individual device assignments."""

from jittersim.config.models import Profile
from jittersim.config.storage import ConfigRepository

from .access import build_access_plan
from .compiler import compile_plan
from .plan import GeneralProfileRule, NetworkPlan
from .service_lists import build_service_list_rules
from .website import lookup_session, resolve_domains


def compile_general_plan(
    *, repository: ConfigRepository, profile: Profile, outage_active: bool = False
) -> NetworkPlan:
    """Keep global access checks separate from bypassable profile behavior."""

    with lookup_session():
        return _compile_general_plan(
            repository=repository, profile=profile, outage_active=outage_active
        )


def _compile_general_plan(
    *, repository: ConfigRepository, profile: Profile, outage_active: bool
) -> NetworkPlan:
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
        service_list_rules=build_service_list_rules(repository),
        access=build_access_plan(repository.access_settings()),
    )
