"""Protect range validation, displayed rounding and helper compatibility."""

import json
import random
from dataclasses import asdict

import pytest

from jittersim.api.server import ControlServer, _impairment_from_payload, _settings_from_payload
from jittersim.config.models import (
    Device,
    Impairment,
    Profile,
    Randomness,
    SavedSettings,
    ValidationError,
)
from jittersim.config.storage import ConfigRepository
from jittersim.config.transfer import settings_to_payload
from jittersim.engine.codec import plan_from_payload, plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.engine.progression import interpolate_impairment
from jittersim.engine.randomizer import feature_range, impairment_range_preview, reroll_impairment
from jittersim.networking.helper import HelperClientError, _clean_queue_impairment
from jittersim.networking.renderer import _format_impairment_arguments


@pytest.mark.parametrize(
    "value", [True, "6", float("nan"), float("inf"), -0.01, 100.01, 6.001, 6.01, 6.0]
)
def test_invalid_packet_percentages_are_rejected(value):
    with pytest.raises(ValidationError):
        Impairment(corrupt_percent=value)


@pytest.mark.parametrize(
    "settings",
    [
        {"mode": "range"},
        {"mode": "range", "minimum": 8, "maximum": 7},
        {"mode": "range", "minimum": 0, "maximum": float("inf")},
        {"minimum": 0, "maximum": 13},
        {"mode": "other"},
    ],
)
def test_malformed_ranges_are_rejected(settings):
    with pytest.raises(ValidationError):
        Randomness(**settings)


def test_ranges_follow_the_owning_features_units():
    with pytest.raises(ValidationError):
        Impairment(delay_randomness=Randomness(mode="range", minimum=0.5, maximum=10))
    with pytest.raises(ValidationError):
        Impairment(corrupt_randomness=Randomness(mode="range", minimum=0, maximum=101))
    with pytest.raises(ValidationError):
        Impairment(corrupt_randomness=Randomness(mode="range", minimum=0.01, maximum=13))
    with pytest.raises(ValidationError):
        Impairment(bandwidth_randomness=Randomness(mode="range", minimum=0, maximum=10))
    with pytest.raises(ValidationError):
        Profile("p", "P", website_randomness=Randomness(mode="range", minimum=0, maximum=1))


def test_relative_preview_matches_actual_whole_percentage_selection():
    impairment = Impairment(corrupt_percent=6, corrupt_randomness=Randomness(variation_percent=7))
    preview = impairment_range_preview(impairment)["corrupt"]
    assert (preview["minimum"], preview["maximum"], preview["step"]) == (6, 6, 1)
    assert reroll_impairment(impairment=impairment, generator=random.Random(1)).corrupt_percent == 6
    wider = Impairment(corrupt_percent=60, corrupt_randomness=Randomness(variation_percent=7.25))
    assert feature_range(wider, "corrupt") == (56, 64)
    values = {
        reroll_impairment(impairment=wider, generator=random.Random(seed)).corrupt_percent
        for seed in range(200)
    }
    assert len(values) > 4
    assert all(type(value) is int and 56 <= value <= 64 for value in values)


@pytest.mark.parametrize(
    "base, variation, expected", [(100, 100, (0, 100)), (1, 1, (1, 1)), (0, 100, (0, 0))]
)
def test_preview_clamps_and_rounds_to_supported_values(base, variation, expected):
    impairment = Impairment(
        loss_percent=base, loss_randomness=Randomness(variation_percent=variation)
    )
    assert feature_range(impairment, "loss") == expected
    integer = Impairment(delay_ms=1, delay_randomness=Randomness(variation_percent=1))
    assert feature_range(integer, "delay") == (1, 1)
    assert reroll_impairment(impairment=integer, generator=random.Random(1)).delay_ms == 1


def test_custom_ranges_activate_zero_base_and_absent_bandwidth():
    impairment = Impairment(
        corrupt_randomness=Randomness(mode="range", minimum=5, maximum=13),
        bandwidth_randomness=Randomness(mode="range", minimum=1000, maximum=2000),
    )
    rolled = reroll_impairment(impairment=impairment, generator=random.Random(12))
    assert 5 <= rolled.corrupt_percent <= 13
    assert 1000 <= rolled.bandwidth_kbit <= 2000
    assert isinstance(rolled.bandwidth_kbit, int)
    off = Impairment(
        corrupt_randomness=Randomness(mode="range", minimum=5, maximum=13, enabled_probability=0)
    )
    assert reroll_impairment(impairment=off, generator=random.Random(12)).corrupt_percent == 0


def test_whole_fixed_range_and_seed_are_repeatable():
    impairment = Impairment(corrupt_randomness=Randomness(mode="range", minimum=6, maximum=6))
    first = reroll_impairment(impairment=impairment, generator=random.Random(4))
    assert first.corrupt_percent == 6
    assert first == reroll_impairment(impairment=impairment, generator=random.Random(4))


def test_progressions_keep_supported_values_and_mixed_ranges():
    start = Impairment(corrupt_percent=6, corrupt_randomness=Randomness(variation_percent=7))
    end = Impairment(
        corrupt_percent=7, corrupt_randomness=Randomness(mode="range", minimum=0, maximum=13)
    )
    assert interpolate_impairment(start=start, end=end, factor=0) == start
    assert interpolate_impairment(start=start, end=end, factor=1) == end
    middle = interpolate_impairment(start=start, end=end, factor=0.5)
    assert middle.corrupt_percent == 6
    assert feature_range(middle, "corrupt") == (3, 10)


def test_legacy_profiles_and_new_storage_round_trips(tmp_path):
    legacy = _impairment_from_payload(
        {
            "corrupt_percent": 6,
            "corrupt_randomness": {"variation_percent": 7, "enabled_probability": 66},
        }
    )
    assert legacy.corrupt_percent == 6
    assert legacy.corrupt_randomness.mode == "relative"
    assert legacy.corrupt_randomness.enabled_probability == 66
    profile = Profile(
        "p",
        "P",
        upload=Impairment(
            corrupt_percent=6, corrupt_randomness=Randomness(mode="range", minimum=0, maximum=13)
        ),
    )
    repository = ConfigRepository(tmp_path / "settings.db")
    repository.save_profile(profile)
    repository.close()
    reopened = ConfigRepository(tmp_path / "settings.db")
    try:
        assert reopened.get_profile("p") == profile
    finally:
        reopened.close()


def test_custom_range_plan_round_trip_and_whole_commands():
    profile = Profile(
        "p",
        "P",
        upload=Impairment(
            delay_ms=10,
            loss_percent=1,
            reorder_percent=2,
            duplicate_percent=3,
            corrupt_percent=6,
            corrupt_randomness=Randomness(mode="range", minimum=0, maximum=13),
        ),
    )
    plan = compile_plan(
        devices=[Device("client", "Client", "192.168.50.10", "p")], profiles={"p": profile}
    )
    assert plan_from_payload(plan_to_payload(plan)) == plan
    arguments = _format_impairment_arguments(plan.queues[0])
    # Upload queues on the uplink keep loss but never corrupt, duplicate or
    # reorder (corruption runs on the ifb device, see the upstream safety tests).
    assert "1%" in arguments and arguments[arguments.index("corrupt") + 1] == "0%"
    assert "duplicate" not in arguments and "reorder" not in arguments
    assert _clean_queue_impairment(asdict(profile.upload))["corrupt_percent"] == 6
    bad = asdict(profile.upload)
    bad["corrupt_percent"] = float("nan")
    with pytest.raises(HelperClientError):
        _clean_queue_impairment(bad)


def test_preview_is_read_only_and_rejects_invalid_ranges(tmp_path):
    repository = ConfigRepository(tmp_path / "preview.db")
    server = ControlServer(("127.0.0.1", 0), repository, admin_token="test-range")
    try:
        before = repository.get_runtime_state()
        payload = {"upload": {"corrupt_percent": 6, "corrupt_randomness": {"variation_percent": 7}}}
        status, _, body = server.write_response_for("/api/profiles/ranges", payload)
        assert status == 200
        preview = json.loads(body)["ranges"]["upload"]["corrupt"]
        assert (preview["minimum"], preview["maximum"]) == (6, 6)
        assert repository.get_runtime_state() == before
        assert repository.list_profiles() == ()
        status, _, _ = server.write_response_for(
            "/api/profiles/ranges",
            {"upload": {"corrupt_randomness": {"mode": "range", "minimum": 13, "maximum": 0}}},
        )
        assert status == 400
    finally:
        server.server_close()
        repository.close()


@pytest.mark.parametrize("mode", ["relative", "range"])
def test_export_import_retains_variation_precision_and_selected_mode(mode):
    randomness = (
        Randomness(variation_percent=7.25, enabled_probability=66)
        if mode == "relative"
        else Randomness(mode="range", minimum=0, maximum=13, enabled_probability=66)
    )
    settings = SavedSettings(
        profiles=(
            Profile("p", "P", upload=Impairment(corrupt_percent=6, corrupt_randomness=randomness)),
        )
    )
    payload = settings_to_payload(settings, exported_at=1)
    restored = _settings_from_payload(json.loads(json.dumps(payload)))
    assert restored == settings
    if mode == "relative":
        encoded = payload["profiles"][0]["upload"]["corrupt_randomness"]
        assert set(encoded) == {"variation_percent", "enabled_probability"}


@pytest.mark.parametrize("value", [True, "0.25", float("nan"), float("inf"), -0.01, 100.01, 0.001])
def test_helper_rejects_unsupported_fractional_packet_values(value):
    payload = asdict(Impairment())
    payload["corrupt_percent"] = value
    with pytest.raises(HelperClientError):
        _clean_queue_impairment(payload)


def _prior_relative_result(impairment, generator):
    # Preserve the formula and draw order from 0a41c2f's _reroll_integer.
    fields = (
        ("bandwidth_kbit", "bandwidth_randomness", 1, 1_000_000),
        ("delay_ms", "delay_randomness", 0, 60_000),
        ("jitter_ms", "jitter_randomness", 0, 60_000),
        ("loss_percent", "loss_randomness", 0, 100),
        ("reorder_percent", "reorder_randomness", 0, 100),
        ("duplicate_percent", "duplicate_randomness", 0, 100),
        ("corrupt_percent", "corrupt_randomness", 0, 100),
    )
    selected = {}
    for field, control, minimum, maximum in fields:
        value = getattr(impairment, field)
        settings = getattr(impairment, control)
        if value is None or not generator.random() * 100 < settings.enabled_probability:
            selected[field] = None if field == "bandwidth_kbit" else 0
        else:
            variation = value * settings.variation_percent / 100
            sampled = round(generator.uniform(value - variation, value + variation))
            selected[field] = max(minimum, min(maximum, sampled))
    return Impairment(**selected, loss_burst_packets=impairment.loss_burst_packets)


@pytest.mark.parametrize(
    "feature, field, base, variation, chance",
    [
        ("corrupt", "corrupt_percent", 5, 10, 100),
        ("loss", "loss_percent", 5, 10, 100),
        ("corrupt", "corrupt_percent", 99, 20, 66),
        ("loss", "loss_percent", 100, 100, 100),
        ("delay", "delay_ms", 60_000, 100, 100),
        ("jitter", "jitter_ms", 5, 10, 37),
        ("bandwidth", "bandwidth_kbit", 1, 100, 100),
        ("bandwidth", "bandwidth_kbit", 1_000_000, 100, 20),
        ("duplicate", "duplicate_percent", 99, 20, 0),
        ("reorder", "reorder_percent", 5, 10, 100),
    ],
)
def test_relative_seed_outputs_match_the_prior_sampler(feature, field, base, variation, chance):
    impairment = Impairment(**{field: base, feature + "_randomness": Randomness(variation, chance)})
    for seed in range(200):
        assert reroll_impairment(impairment=impairment, generator=random.Random(seed)) == (
            _prior_relative_result(impairment, random.Random(seed))
        ), seed


def test_relative_mixed_chances_preserve_prior_draw_order_and_burst_length():
    impairment = Impairment(
        delay_ms=5,
        jitter_ms=60_000,
        loss_percent=99,
        bandwidth_kbit=1,
        reorder_percent=5,
        duplicate_percent=99,
        corrupt_percent=100,
        loss_burst_packets=4,
        delay_randomness=Randomness(10, 23),
        jitter_randomness=Randomness(100, 72),
        loss_randomness=Randomness(20, 76),
        bandwidth_randomness=Randomness(100, 20),
        reorder_randomness=Randomness(10, 79),
        duplicate_randomness=Randomness(20, 80),
        corrupt_randomness=Randomness(100, 66),
    )
    for seed in range(200):
        assert reroll_impairment(impairment=impairment, generator=random.Random(seed)) == (
            _prior_relative_result(impairment, random.Random(seed))
        ), seed


def test_rounding_displayed_bounds_does_not_expand_the_relative_draw():
    impairment = Impairment(loss_percent=5, loss_randomness=Randomness(10))
    assert feature_range(impairment, "loss") == (4, 6)
    values = {
        reroll_impairment(impairment=impairment, generator=random.Random(seed)).loss_percent
        for seed in range(1000)
    }
    assert values == {5}


@pytest.mark.parametrize("first,last,expected", [(10, 11, 10), (10.25, 10.75, 10.5)])
def test_relative_progression_keeps_legacy_whole_variation(first, last, expected):
    start = Impairment(corrupt_percent=5, corrupt_randomness=Randomness(first))
    end = Impairment(corrupt_percent=5, corrupt_randomness=Randomness(last))
    middle = interpolate_impairment(start=start, end=end, factor=0.5)
    assert middle.corrupt_randomness.variation_percent == expected
