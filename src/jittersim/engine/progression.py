"""Progression math for time and traffic driven network changes."""

from dataclasses import replace
from typing import Optional

from jittersim.config.models import Impairment, Profile, Progression, Randomness


def progression_factor(
    *,
    progression: Progression,
    now: float,
    traffic_queries: int = 0,
    traffic_bytes: int = 0,
) -> float:
    """Return the bounded progress fraction for one active progression."""

    if progression.mode == "traffic":
        completed_queries = max(0, traffic_queries - progression.traffic_baseline_queries)
        return min(1.0, completed_queries / progression.traffic_target_queries)
    if progression.mode == "bytes":
        completed_bytes = max(0, traffic_bytes - progression.traffic_baseline_bytes)
        return min(1.0, completed_bytes / progression.traffic_target_bytes)
    elapsed = max(0.0, now - progression.started_at)
    return min(1.0, elapsed / progression.duration_seconds)


def interpolate_profile(*, start: Profile, end: Profile, factor: float) -> Profile:
    """Interpolate profile values while retaining the end profile's limits."""

    bounded_factor = min(1.0, max(0.0, factor))
    return replace(
        end,
        upload=interpolate_impairment(
            start=start.upload,
            end=end.upload,
            factor=bounded_factor,
        ),
        download=interpolate_impairment(
            start=start.download,
            end=end.download,
            factor=bounded_factor,
        ),
        reroll_interval_seconds=_interpolate_integer(
            start.reroll_interval_seconds,
            end.reroll_interval_seconds,
            bounded_factor,
        ),
        reroll_interval_randomness=_interpolate_randomness(
            start.reroll_interval_randomness,
            end.reroll_interval_randomness,
            bounded_factor,
        ),
        outage_probability=_interpolate_integer(
            start.outage_probability,
            end.outage_probability,
            bounded_factor,
        ),
        outage_duration_seconds=_interpolate_integer(
            start.outage_duration_seconds,
            end.outage_duration_seconds,
            bounded_factor,
        ),
        outage_duration_randomness=_interpolate_randomness(
            start.outage_duration_randomness,
            end.outage_duration_randomness,
            bounded_factor,
        ),
        outage_interval_seconds=_interpolate_integer(
            start.outage_interval_seconds,
            end.outage_interval_seconds,
            bounded_factor,
        ),
        outage_interval_randomness=_interpolate_randomness(
            start.outage_interval_randomness,
            end.outage_interval_randomness,
            bounded_factor,
        ),
        random_seed=start.random_seed if bounded_factor < 1 else end.random_seed,
        website_mode=start.website_mode if bounded_factor < 1 else end.website_mode,
        website_domains=start.website_domains if bounded_factor < 1 else end.website_domains,
        website_randomness=_interpolate_randomness(
            start.website_randomness,
            end.website_randomness,
            bounded_factor,
        ),
    )


def interpolate_impairment(
    *,
    start: Impairment,
    end: Impairment,
    factor: float,
) -> Impairment:
    """Interpolate all numeric impairment controls between two profiles."""

    return Impairment(
        delay_ms=_interpolate_integer(start.delay_ms, end.delay_ms, factor),
        jitter_ms=_interpolate_integer(start.jitter_ms, end.jitter_ms, factor),
        loss_percent=_interpolate_integer(start.loss_percent, end.loss_percent, factor),
        bandwidth_kbit=_interpolate_bandwidth(start.bandwidth_kbit, end.bandwidth_kbit, factor),
        reorder_percent=_interpolate_integer(
            start.reorder_percent,
            end.reorder_percent,
            factor,
        ),
        duplicate_percent=_interpolate_integer(
            start.duplicate_percent,
            end.duplicate_percent,
            factor,
        ),
        corrupt_percent=_interpolate_integer(
            start.corrupt_percent,
            end.corrupt_percent,
            factor,
        ),
        delay_randomness=_interpolate_randomness(
            start.delay_randomness,
            end.delay_randomness,
            factor,
        ),
        jitter_randomness=_interpolate_randomness(
            start.jitter_randomness,
            end.jitter_randomness,
            factor,
        ),
        loss_randomness=_interpolate_randomness(
            start.loss_randomness,
            end.loss_randomness,
            factor,
        ),
        bandwidth_randomness=_interpolate_randomness(
            start.bandwidth_randomness,
            end.bandwidth_randomness,
            factor,
        ),
        reorder_randomness=_interpolate_randomness(
            start.reorder_randomness,
            end.reorder_randomness,
            factor,
        ),
        duplicate_randomness=_interpolate_randomness(
            start.duplicate_randomness,
            end.duplicate_randomness,
            factor,
        ),
        corrupt_randomness=_interpolate_randomness(
            start.corrupt_randomness,
            end.corrupt_randomness,
            factor,
        ),
    )


def _interpolate_integer(start: int, end: int, factor: float) -> int:
    return round(start + (end - start) * factor)


def _interpolate_bandwidth(
    start: Optional[int],
    end: Optional[int],
    factor: float,
) -> Optional[int]:
    if start is None and end is None:
        return None
    if start is None or end is None:
        return end if factor >= 1 else start
    return _interpolate_integer(start, end, factor)


def _interpolate_randomness(
    start: Randomness,
    end: Randomness,
    factor: float,
) -> Randomness:
    return Randomness(
        variation_percent=_interpolate_integer(
            start.variation_percent,
            end.variation_percent,
            factor,
        ),
        enabled_probability=_interpolate_integer(
            start.enabled_probability,
            end.enabled_probability,
            factor,
        ),
    )
