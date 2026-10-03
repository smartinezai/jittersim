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
    assert "6%" in arguments and "1%" in arguments and "2%" in arguments
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
