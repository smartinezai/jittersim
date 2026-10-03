"""Deterministic reroll helpers for scheduled profile behavior."""

import hashlib
import random
from dataclasses import replace
from typing import List, Optional, Tuple

from jittersim.config.models import Impairment, Profile, Randomness


def _is_enabled(randomness: Randomness, generator: random.Random) -> bool:
    return generator.random() * 100 < randomness.enabled_probability


def _reroll_integer(
    value: Optional[int],
    *,
    randomness: Randomness,
    generator: random.Random,
    minimum: int,
    maximum: int,
) -> Optional[int]:
    if value is None or not _is_enabled(randomness, generator):
        return None
    variation = value * randomness.variation_percent / 100
    rerolled = round(generator.uniform(value - variation, value + variation))
    return max(minimum, min(maximum, rerolled))


def reroll_impairment(*, impairment: Impairment, generator: random.Random) -> Impairment:
    """Reroll all enabled impairment values from one seeded random generator."""

    bandwidth = _reroll_integer(
        impairment.bandwidth_kbit,
        randomness=impairment.bandwidth_randomness,
        generator=generator,
        minimum=1,
        maximum=1_000_000,
    )
    return Impairment(
        delay_ms=_reroll_integer(
            impairment.delay_ms,
            randomness=impairment.delay_randomness,
            generator=generator,
            minimum=0,
            maximum=60_000,
        )
        or 0,
        jitter_ms=_reroll_integer(
            impairment.jitter_ms,
            randomness=impairment.jitter_randomness,
            generator=generator,
            minimum=0,
            maximum=60_000,
        )
        or 0,
        loss_percent=_reroll_integer(
            impairment.loss_percent,
            randomness=impairment.loss_randomness,
            generator=generator,
            minimum=0,
            maximum=100,
        )
        or 0,
        bandwidth_kbit=bandwidth,
        reorder_percent=_reroll_integer(
            impairment.reorder_percent,
            randomness=impairment.reorder_randomness,
            generator=generator,
            minimum=0,
            maximum=100,
        )
        or 0,
        duplicate_percent=_reroll_integer(
            impairment.duplicate_percent,
            randomness=impairment.duplicate_randomness,
            generator=generator,
            minimum=0,
            maximum=100,
        )
        or 0,
        corrupt_percent=_reroll_integer(
            impairment.corrupt_percent,
            randomness=impairment.corrupt_randomness,
            generator=generator,
            minimum=0,
            maximum=100,
        )
        or 0,
        loss_burst_packets=impairment.loss_burst_packets,
    )


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
