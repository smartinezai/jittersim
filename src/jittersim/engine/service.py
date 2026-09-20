"""Services that connect persisted configuration to the pure policy compiler."""

from jittersim.config.storage import ConfigRepository

from .compiler import compile_plan
from .plan import NetworkPlan
from .website import build_website_filters


def compile_stored_plan(*, repository: ConfigRepository) -> NetworkPlan:
    """Compile every stored device assignment into one deterministic plan."""

    profiles = {profile.id: profile for profile in repository.list_profiles()}
    devices = repository.list_devices()
    global_blocked, website_filters = build_website_filters(
        devices=devices,
        profiles=profiles,
        global_policy=repository.get_website_policy(),
    )
    return compile_plan(
        devices=devices,
        profiles=profiles,
        mac_policy=repository.get_mac_policy(),
        global_blocked_addresses=global_blocked,
        website_filters=website_filters,
    )
