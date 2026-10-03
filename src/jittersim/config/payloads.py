"""Keep the existing profile format when optional range controls are unused."""

from dataclasses import asdict

from .models import FEATURE_LIMITS, Impairment, Profile, Randomness


def randomness_payload(randomness: Randomness) -> dict:
    payload = asdict(randomness)
    if randomness.mode == "relative":
        for field in ("mode", "minimum", "maximum"):
            payload.pop(field)
    return payload


def impairment_payload(impairment: Impairment) -> dict:
    payload = asdict(impairment)
    for feature in FEATURE_LIMITS:
        field = feature + "_randomness"
        payload[field] = randomness_payload(getattr(impairment, field))
    return payload


def profile_payload(profile: Profile) -> dict:
    payload = asdict(profile)
    payload["upload"] = impairment_payload(profile.upload)
    payload["download"] = impairment_payload(profile.download)
    for field in (
        "reroll_interval_randomness",
        "outage_duration_randomness",
        "outage_interval_randomness",
        "website_randomness",
    ):
        payload[field] = randomness_payload(getattr(profile, field))
    return payload
