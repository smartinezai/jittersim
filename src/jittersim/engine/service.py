"""Services that connect persisted configuration to the pure policy compiler."""

from jittersim.config.storage import ConfigRepository

from .compiler import compile_plan
from .plan import NetworkPlan


def compile_stored_plan(*, repository: ConfigRepository) -> NetworkPlan:
    """Compile every stored device assignment into one deterministic plan."""

    profiles = {profile.id: profile for profile in repository.list_profiles()}
    return compile_plan(devices=repository.list_devices(), profiles=profiles)
