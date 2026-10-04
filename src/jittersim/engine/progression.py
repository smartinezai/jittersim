"""Progression math for time and traffic driven network changes."""

from dataclasses import replace
from typing import Optional

from jittersim.config.models import FEATURE_LIMITS, Impairment, Number, Profile, Progression, Randomness
from jittersim.engine.randomizer import feature_range, quantize_value


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
        homepage_bounce_percent=_interpolate_integer(
            start.homepage_bounce_percent, end.homepage_bounce_percent, bounded_factor
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
        random_mode=start.random_mode if bounded_factor < 1 else end.random_mode,
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
        delay_randomness=_interpolate_feature_randomness(start, end, factor, "delay"),
        jitter_randomness=_interpolate_feature_randomness(start, end, factor, "jitter"),
        loss_randomness=_interpolate_feature_randomness(start, end, factor, "loss"),
        bandwidth_randomness=_interpolate_feature_randomness(start, end, factor, "bandwidth"),
        reorder_randomness=_interpolate_feature_randomness(start, end, factor, "reorder"),
        duplicate_randomness=_interpolate_feature_randomness(start, end, factor, "duplicate"),
        corrupt_randomness=_interpolate_feature_randomness(start, end, factor, "corrupt"),
        loss_burst_packets=_interpolate_integer(
            start.loss_burst_packets,
            end.loss_burst_packets,
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
    interpolate_variation = (
        _interpolate_integer
        if float(start.variation_percent).is_integer() and float(end.variation_percent).is_integer()
        else _interpolate_percent
    )
    return Randomness(
        variation_percent=interpolate_variation(
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


def _interpolate_percent(start: Number, end: Number, factor: float) -> Number:
    return quantize_value(start + (end - start) * factor, 2)


def _interpolate_feature_randomness(
    start: Impairment, end: Impairment, factor: float, feature: str
) -> Randomness:

    first = getattr(start, feature + "_randomness")
    last = getattr(end, feature + "_randomness")
    if factor <= 0:
        return first
    if factor >= 1:
        return last
    if first.mode == last.mode == "relative":
        return _interpolate_randomness(first, last, factor)
    first_bounds, last_bounds = feature_range(start, feature), feature_range(end, feature)
    if first_bounds is None or last_bounds is None:
        return first
    places = FEATURE_LIMITS[feature][3]
    bounds = [quantize_value(a + (b - a) * factor, places)
              for a, b in zip(first_bounds, last_bounds)]
    return Randomness(
        mode="range", minimum=bounds[0], maximum=bounds[1],
        enabled_probability=_interpolate_integer(first.enabled_probability, last.enabled_probability, factor),
    )
