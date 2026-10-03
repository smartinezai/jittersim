"""Deterministic reroll helpers for scheduled profile behavior."""

import hashlib
import random
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_EVEN
from typing import List, Optional, Tuple, Union

from jittersim.config.models import FEATURE_LIMITS, Impairment, Number, Profile, Randomness


def _is_enabled(randomness: Randomness, generator: random.Random) -> bool:
    return generator.random() * 100 < randomness.enabled_probability


def quantize_value(value: Union[Number, Decimal], places: int) -> Number:
    """Use one rounding rule for selection, interpolation and displayed bounds."""

    rounded = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    return int(rounded) if rounded == rounded.to_integral_value() else float(rounded)


def feature_range(impairment: Impairment, feature: str) -> Optional[Tuple[Number, Number]]:
    """Return precisely the supported endpoints used by this feature's selector."""

    field, lower, upper, places, _ = FEATURE_LIMITS[feature]
    randomness = getattr(impairment, feature + "_randomness")
    value = getattr(impairment, field)
    if randomness.mode == "range":
        low, high = randomness.minimum, randomness.maximum
    elif value is None:
        return None
    else:
        base = Decimal(str(value))
        variation = base * Decimal(str(randomness.variation_percent)) / 100
        low, high = base - variation, base + variation
    return (
        max(lower, min(upper, quantize_value(low, places))),
        max(lower, min(upper, quantize_value(high, places))),
    )


def impairment_range_preview(impairment: Impairment) -> dict:
    """Describe the active range without storing or applying a profile."""

    result = {}
    for feature, (field, _, _, places, unit) in FEATURE_LIMITS.items():
        bounds = feature_range(impairment, feature)
        result[feature] = {
            "base": getattr(impairment, field),
            "mode": getattr(impairment, feature + "_randomness").mode,
            "variation_percent": getattr(impairment, feature + "_randomness").variation_percent,
            "minimum": bounds[0] if bounds else None,
            "maximum": bounds[1] if bounds else None,
            "step": 10 ** -places,
            "unit": unit,
            "enabled_probability": getattr(impairment, feature + "_randomness").enabled_probability,
        }
    return result


def reroll_impairment(*, impairment: Impairment, generator: random.Random) -> Impairment:
    """Reroll all enabled impairment values from one seeded random generator."""

    selected = {}
    # Keep the established draw order so adding fields does not reorder seed use.
    for feature in ("bandwidth", "delay", "jitter", "loss", "reorder", "duplicate", "corrupt"):
        field, _, _, places, _ = FEATURE_LIMITS[feature]
        bounds = feature_range(impairment, feature)
        randomness = getattr(impairment, feature + "_randomness")
        if bounds is None or not _is_enabled(randomness, generator):
            selected[field] = None if feature == "bandwidth" else 0
        else:
            low, high = bounds
            selected[field] = quantize_value(generator.uniform(low, high), places)
    return Impairment(**selected, loss_burst_packets=impairment.loss_burst_packets)


def reroll_profile(
    *,
    profile: Profile,
    generator: random.Random,
    website_outcomes: Optional[List[Tuple[str, bool]]] = None,
) -> Profile:
    """Reroll one profile's packet values and optional website entries."""

    selected_domains = []
    for domain in profile.website_domains:
        kept = _is_enabled(profile.website_randomness, generator)
        if kept:
            selected_domains.append(domain)
        if website_outcomes is not None:
            website_outcomes.append((domain, kept))
    return replace(
        profile,
        upload=reroll_impairment(impairment=profile.upload, generator=generator),
        download=reroll_impairment(impairment=profile.download, generator=generator),
        website_domains=tuple(selected_domains),
    )


def seeded_generator(*, profile: Profile, device_id: str, roll_number: int, session_seed: Optional[int] = None) -> random.Random:
    """Build a stable generator that changes only when the roll number changes."""

    seed = session_seed if session_seed is not None else profile.random_seed or 0
    material = f"{seed}:{device_id}:{roll_number}".encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def reroll_interval_seconds(*, profile: Profile, generator: random.Random) -> int:
    """Choose the next profile reroll interval in seconds."""

    return _random_interval(
        base_seconds=profile.reroll_interval_seconds,
        randomness=profile.reroll_interval_randomness,
        generator=generator,
    )


def outage_interval_seconds(*, profile: Profile, generator: random.Random) -> int:
    """Choose the next independent outage check interval in seconds."""

    base_seconds = profile.outage_interval_seconds or profile.reroll_interval_seconds
    return _random_interval(
        base_seconds=base_seconds,
        randomness=profile.outage_interval_randomness,
        generator=generator,
    )


def outage_duration_seconds(*, profile: Profile, generator: random.Random) -> int:
    """Choose how long one randomized outage should last."""

    return _random_interval(
        base_seconds=profile.outage_duration_seconds,
        randomness=profile.outage_duration_randomness,
        generator=generator,
    )


def outage_is_selected(*, profile: Profile, generator: random.Random) -> bool:
    """Return whether this roll starts an outage."""

    return generator.random() * 100 < profile.outage_probability


def _random_interval(
    *,
    base_seconds: int,
    randomness: Randomness,
    generator: random.Random,
) -> int:
    if base_seconds <= 0:
        return 0
    variation = base_seconds * randomness.variation_percent / 100
    return max(1, round(generator.uniform(base_seconds - variation, base_seconds + variation)))
