"""Engine rules that no earlier test held down (mutation testing, 2026-10-23).

A mutation run on the schedule sweep, the progression between profiles, the seeded random
generator, the client eligibility check, the device conditions and the scenario clock showed
behaviour that could change without a failing test. Each test below says which behaviour it
protects. The survivors that cannot change a result are listed in
docs/mutation-testing-2026-10-18.md.
"""

import random
import time
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from ipaddress import IPv4Address
from unittest.mock import patch

import pytest

from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import (
    Device,
    Impairment,
    Profile,
    Progression,
    Randomness,
    Scenario,
    ScenarioRun,
    ScenarioStep,
    Schedule,
)
from jittersim.config.storage import ConfigRepository
from jittersim.engine import eligibility
from jittersim.engine.compiler import compile_plan
from jittersim.engine.conditions import DeviceCondition, device_conditions, everyone_else_condition
from jittersim.engine.eligibility import device_protection_states, eligible_clients
from jittersim.engine.plan import EveryoneElseRule, ServiceQuotaRule
from jittersim.engine.progression import (
    interpolate_impairment,
    interpolate_profile,
    progression_factor,
)
from jittersim.engine.randomizer import (
    impairment_range_preview,
    outage_duration_seconds,
    outage_interval_seconds,
    outage_is_selected,
    quantize_value,
    reroll_impairment,
    reroll_interval_seconds,
    reroll_profile,
    seeded_generator,
)
from jittersim.engine.scenarios import ScenarioPosition, active_scenario_runs
from jittersim.engine.schedules import is_schedule_active, next_schedule_change

# Schedules


def test_a_window_covers_its_first_minute_and_not_its_end():
    window = Schedule("w", "phone", "slow", (0,), 10 * 60, 11 * 60)
    # 2026-09-21 is a Monday.
    for moment, expected in [
        (datetime(2026, 9, 21, 9, 59), False),
        (datetime(2026, 9, 21, 10, 0), True),
        (datetime(2026, 9, 21, 10, 59), True),
        (datetime(2026, 9, 21, 11, 0), False),
        (datetime(2026, 9, 22, 10, 30), False),
    ]:
        assert is_schedule_active(schedule=window, moment=moment) is expected, moment


def test_a_window_over_midnight_belongs_to_the_day_it_starts_on():
    night = Schedule("n", "phone", "slow", (0,), 23 * 60, 60)
    for moment, expected in [
        (datetime(2026, 9, 21, 22, 59), False),
        (datetime(2026, 9, 21, 23, 0), True),
        (datetime(2026, 9, 21, 23, 59), True),
        (datetime(2026, 9, 22, 0, 59), True),
        (datetime(2026, 9, 22, 1, 0), False),
        (datetime(2026, 9, 22, 23, 30), False),
        (datetime(2026, 9, 21, 0, 30), False),
    ]:
        assert is_schedule_active(schedule=night, moment=moment) is expected, moment


@pytest.fixture
def berlin(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("requires a process time zone")
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def stamp(date, *, fold=0):
    return datetime.fromisoformat(date).replace(fold=fold).timestamp()


def test_the_next_change_looks_only_at_the_target_that_was_asked_for(berlin):
    monday = (0,)
    schedules = [
        Schedule("own", "phone", "slow", monday, 12 * 60, 13 * 60),
        Schedule("family", None, "slow", monday, 14 * 60, 15 * 60, group_id="family"),
        Schedule("other-device", "tablet", "slow", monday, 9 * 60, 10 * 60),
        Schedule("other-group", None, "slow", monday, 9 * 60 + 30, 9 * 60 + 45, group_id="other"),
        Schedule("everyone", None, "slow", monday, 9 * 60 + 10, 9 * 60 + 20, everyone_else=True),
    ]

    def change(now, **target):
        return next_schedule_change(schedules=schedules, now=stamp(now), **target)

    # The schedules of another device, another group and everyone else are not this device's.
    assert change("2026-09-28T08:00:00", device_id="phone") == stamp("2026-09-28T12:00:00")
    assert change("2026-09-28T08:00:00", device_id="phone", group_id="family") == stamp(
        "2026-09-28T12:00:00")
    # The device's group counts as well.
    assert change("2026-09-28T13:30:00", device_id="phone", group_id="family") == stamp(
        "2026-09-28T14:00:00")
    assert change("2026-09-28T08:00:00", group_id="family") == stamp("2026-09-28T14:00:00")
    # Everyone else has its own schedules only.
    assert change("2026-09-28T08:00:00", everyone_else=True) == stamp("2026-09-28T09:10:00")


def test_a_turned_off_schedule_does_not_hide_the_ones_after_it(berlin):
    off = Schedule("a-off", "phone", "slow", (0,), 9 * 60, 10 * 60, enabled=False)
    on = Schedule("b-on", "phone", "slow", (0,), 11 * 60, 12 * 60)

    assert next_schedule_change(
        schedules=[off, on], device_id="phone", now=stamp("2026-09-28T08:00:00")
    ) == stamp("2026-09-28T11:00:00")


def test_the_next_change_is_exact_when_now_has_seconds(berlin):
    day = Schedule("day", "phone", "slow", (0,), 8 * 60, 18 * 60)
    now = stamp("2026-09-28T09:30:00") + 30.25

    assert next_schedule_change(schedules=[day], device_id="phone", now=now) == stamp(
        "2026-09-28T18:00:00")


def test_the_first_pass_through_a_repeated_hour_counts(berlin):
    # The clocks go back from 03:00 to 02:00 on 25 October 2026, so 02:00 to 02:59 happens twice.
    window = Schedule("fall", "phone", "slow", (6,), 120, 150)

    # Before the change the window starts at the first 02:00 and ends at the first 02:30.
    assert next_schedule_change(
        schedules=[window], device_id="phone", now=stamp("2026-10-25T01:00:00")
    ) == stamp("2026-10-25T02:00:00")
    assert next_schedule_change(
        schedules=[window], device_id="phone", now=stamp("2026-10-25T02:10:00")
    ) == stamp("2026-10-25T02:30:00")


def test_a_clock_change_is_found_to_the_second_however_far_away_it_is(berlin):
    # The clocks jump from 02:00 to 03:00 on 29 March 2026, so the window 02:30 to 03:30 starts
    # at 03:00. 20:00 is exactly six hours before: a scan step of the sweep ends on the change.
    # 14:00 is thirteen hours before: the change lies in a later step.
    window = Schedule("spring", "phone", "slow", (6,), 150, 210)

    for now in ("2026-03-28T20:00:00", "2026-03-28T14:00:00"):
        assert next_schedule_change(
            schedules=[window], device_id="phone", now=stamp(now)
        ) == stamp("2026-03-29T03:00:00"), now


# Conditions

ALICE = Device("alice", "Alice", "192.168.50.10", "profile")


def plan_with(upload=Impairment(), download=Impairment(), **changes):
    profile = Profile(id="profile", name="Profile", upload=upload, download=download)
    return compile_plan(devices=(ALICE,), profiles={"profile": profile}, **changes)


def test_loss_comes_in_bursts_from_two_packets_on():
    expected = {0: "loss 5%", 1: "loss 5%", 2: "loss 5% in bursts", 3: "loss 5% in bursts"}
    for burst, text in expected.items():
        plan = plan_with(upload=Impairment(loss_percent=5, loss_burst_packets=burst))
        assert device_conditions(plan)["alice"].reasons == (f"upload: {text}",), burst


def test_reordering_is_named_when_there_is_a_delay_to_reorder():
    plan = plan_with(upload=Impairment(delay_ms=10, reorder_percent=25))

    assert device_conditions(plan)["alice"] == DeviceCondition(
        "degraded", ("upload: delay 10 ms, reordering 25%",))


def test_the_effects_of_a_service_rule_are_listed_one_after_another():
    quota = ServiceQuotaRule(
        quota_id="video", device_id="alice", device_ip=ALICE.ipv4_address,
        destination_addresses=(IPv4Address("203.0.113.10"),), mark=0, upload_impairment=Impairment(),
        download_impairment=Impairment(delay_ms=50, loss_percent=5), active=True,
    )

    condition = device_conditions(plan_with(service_quota_rules=(quota,)))["alice"]

    assert condition.reasons == ("service limit video, download: delay 50 ms, loss 5%",)


def test_a_device_without_a_queue_has_no_effect():
    plan = replace(plan_with(upload=Impairment(delay_ms=100)), queues=())

    assert device_conditions(plan) == {"alice": DeviceCondition("normal")}


def test_everyone_else_names_the_outage_before_the_lost_packets():
    rule = EveryoneElseRule(
        mark=0, upload_impairment=Impairment(), download_impairment=Impairment(loss_percent=100),
        blocked=True,
    )

    condition = everyone_else_condition(compile_plan(devices=(), profiles={}, everyone_else=rule))

    assert condition == DeviceCondition("offline", ("outage", "download: every packet lost"))


# Scenarios


def test_active_runs_come_with_their_scenario_and_position_newest_first(tmp_path):
    repository = ConfigRepository(tmp_path / "settings.db")
    try:
        for profile_id in ("3g", "slow"):
            repository.save_profile(Profile(id=profile_id, name=profile_id))
        for name, address in (("alice", "192.168.50.10"), ("bob", "192.168.50.11")):
            repository.save_device(Device(name, name.title(), address, "3g"))
        scenario = Scenario(
            id="commute", name="Commute", steps=(ScenarioStep("3g", 60), ScenarioStep("slow", 30)))
        repository.save_scenario(scenario)
        first = ScenarioRun(id="run-1", scenario_id="commute", started_at=100, device_id="alice")
        second = ScenarioRun(id="run-2", scenario_id="commute", started_at=150, device_id="bob")
        repository.start_scenario_run(first)
        repository.start_scenario_run(second)

        assert active_scenario_runs(repository, now=170) == (
            (second, scenario, ScenarioPosition(0, "3g", 40, 70)),
            (first, scenario, ScenarioPosition(1, "slow", 20, 20)),
        )
        # The first run has taken its 90 seconds and is no longer listed.
        assert active_scenario_runs(repository, now=200) == (
            (second, scenario, ScenarioPosition(0, "3g", 10, 40)),
        )
        assert active_scenario_runs(repository, now=240) == ()
    finally:
        repository.close()


# The seeded generator and the rolls


class Draws:
    """A generator that returns fixed numbers and remembers the bounds it was asked for."""

    def __init__(self, draw=0.5, uniform=None):
        self.draw = draw
        self.chosen = uniform
        self.bounds = []

    def random(self):
        return self.draw

    def uniform(self, low, high):
        self.bounds.append((low, high))
        return (low + high) / 2 if self.chosen is None else self.chosen


@pytest.mark.parametrize("draw, chance, selected", [
    (0.499, 50, True), (0.5, 50, False), (0.9, 50, False),
    (0.0, 0, False), (0.0, 1, True), (0.999, 100, True),
])
def test_an_outage_and_a_feature_are_chosen_when_the_draw_is_below_the_chance(
        draw, chance, selected):
    profile = Profile(id="p", name="P", outage_probability=chance)
    assert outage_is_selected(profile=profile, generator=Draws(draw)) is selected

    impairment = Impairment(delay_ms=100, delay_randomness=Randomness(enabled_probability=chance))
    rolled = reroll_impairment(impairment=impairment, generator=Draws(draw))
    assert rolled.delay_ms == (100 if selected else 0)


def test_the_intervals_use_their_own_base_and_variation():
    profile = Profile(
        id="p", name="P",
        reroll_interval_seconds=100, reroll_interval_randomness=Randomness(variation_percent=10),
        outage_interval_seconds=200, outage_interval_randomness=Randomness(variation_percent=20),
        outage_duration_seconds=300, outage_duration_randomness=Randomness(variation_percent=30),
    )
    results = {}
    for name, choose in (("reroll", reroll_interval_seconds), ("interval", outage_interval_seconds),
                         ("duration", outage_duration_seconds)):
        draws = Draws(uniform=123.4)
        assert choose(profile=profile, generator=draws) == 123
        results[name] = draws.bounds

    assert results == {
        "reroll": [(90.0, 110.0)], "interval": [(160.0, 240.0)], "duration": [(210.0, 390.0)],
    }


def test_the_outage_check_falls_back_to_the_reroll_interval():
    profile = Profile(
        id="p", name="P", reroll_interval_seconds=100,
        outage_interval_randomness=Randomness(variation_percent=50),
    )
    draws = Draws()

    outage_interval_seconds(profile=profile, generator=draws)

    assert draws.bounds == [(50.0, 150.0)]


def test_an_interval_is_at_least_one_second_and_a_disabled_one_draws_nothing():
    draws = Draws(uniform=0.2)
    short = Profile(
        id="p", name="P", reroll_interval_seconds=5,
        reroll_interval_randomness=Randomness(variation_percent=100),
    )
    assert reroll_interval_seconds(profile=short, generator=draws) == 1
    assert draws.bounds == [(0.0, 10.0)]

    one_second = Profile(id="p", name="P", reroll_interval_seconds=1)
    assert reroll_interval_seconds(profile=one_second, generator=Draws(uniform=1.0)) == 1

    draws = Draws()
    assert reroll_interval_seconds(profile=Profile(id="p", name="P"), generator=draws) == 0
    assert draws.bounds == []


def test_a_profile_rolls_upload_and_then_download_from_one_generator():
    profile = Profile(
        id="p", name="P",
        upload=Impairment(delay_ms=200, delay_randomness=Randomness(variation_percent=50)),
        download=Impairment(delay_ms=100, delay_randomness=Randomness(variation_percent=50)),
    )
    reference = random.Random(5)
    upload = reroll_impairment(impairment=profile.upload, generator=reference)
    download = reroll_impairment(impairment=profile.download, generator=reference)

    rolled = reroll_profile(profile=profile, generator=random.Random(5))

    assert (rolled.upload, rolled.download) == (upload, download)
    assert (upload.delay_ms, download.delay_ms) != (200, 100)


def test_the_generator_of_a_roll_comes_from_the_seed_the_device_and_the_roll_number():
    profile = Profile(id="p", name="P", random_seed=42)
    with_no_seed = Profile(id="p", name="P")

    def first(**changes):
        values = {"profile": profile, "device_id": "alice", "roll_number": 3}
        values.update(changes)
        return seeded_generator(**values).random()

    # Saved seeds must keep choosing the same values after an update.
    assert first() == 0.9859988078400352
    assert first(profile=with_no_seed) == 0.14974056190205298
    # A seed that belongs to the session wins over the profile's, even when it is 0.
    assert first(session_seed=7) == 0.1445271277466319
    assert first(session_seed=0) == first(profile=with_no_seed)
    assert first(device_id="bob") != first()
    assert first(roll_number=4) != first()


def test_quantizing_rounds_half_to_even_and_keeps_whole_numbers_whole():
    for value, places, expected in [(5.0, 2, 5), (0.125, 2, 0.12), (0.135, 2, 0.14),
                                    (2.5, 0, 2), (3.5, 0, 4), (Decimal("1.005"), 2, 1)]:
        result = quantize_value(value, places)
        assert result == expected and type(result) is type(expected), (value, places)


def test_the_range_preview_describes_every_feature():
    impairment = Impairment(
        delay_ms=100,
        delay_randomness=Randomness(variation_percent=20, enabled_probability=70),
        jitter_randomness=Randomness(mode="range", minimum=5, maximum=15),
    )

    preview = impairment_range_preview(impairment)

    assert list(preview) == ["delay", "jitter", "bandwidth", "loss", "reorder", "duplicate",
                             "corrupt"]
    assert preview["delay"] == {
        "base": 100, "mode": "relative", "variation_percent": 20, "minimum": 80, "maximum": 120,
        "step": 1, "unit": "ms", "enabled_probability": 70,
    }
    assert preview["jitter"] == {
        "base": 0, "mode": "range", "variation_percent": 0, "minimum": 5, "maximum": 15,
        "step": 1, "unit": "ms", "enabled_probability": 100,
    }
    # With no limit set there is nothing to show.
    assert preview["bandwidth"] == {
        "base": None, "mode": "relative", "variation_percent": 0, "minimum": None,
        "maximum": None, "step": 1, "unit": "kbit/s", "enabled_probability": 100,
    }


# Progression


def progression(**changes):
    values = {
        "id": "p", "device_id": "alice", "start_profile_id": "normal", "end_profile_id": "slow",
        "started_at": 100, "duration_seconds": 600,
    }
    values.update(changes)
    return Progression(**values)


def test_a_time_progression_starts_at_zero_and_ends_at_one():
    item = progression()

    assert progression_factor(progression=item, now=50) == 0.0
    assert progression_factor(progression=item, now=400) == 0.5
    assert progression_factor(progression=item, now=5_000) == 1.0


def test_a_traffic_progression_counts_what_came_after_its_baseline():
    item = progression(mode="traffic", traffic_target_queries=100, traffic_baseline_queries=25)

    # Without any traffic nothing has happened yet.
    assert progression_factor(progression=replace(item, traffic_baseline_queries=0), now=100) == 0.0
    # Fewer queries than at the start (the device moved) do not count backwards.
    assert progression_factor(progression=item, now=100, traffic_queries=10) == 0.0
    assert progression_factor(progression=item, now=100, traffic_queries=75) == 0.5
    assert progression_factor(progression=item, now=100, traffic_queries=500) == 1.0
    # A baseline below zero keeps the progress that was made before the move.
    moved = replace(item, traffic_baseline_queries=-50)
    assert progression_factor(progression=moved, now=100, traffic_queries=0) == 0.5


def test_a_bytes_progression_counts_what_came_after_its_baseline():
    item = progression(mode="bytes", traffic_target_bytes=1_000, traffic_baseline_bytes=250)

    assert progression_factor(progression=replace(item, traffic_baseline_bytes=0), now=100) == 0.0
    assert progression_factor(progression=item, now=100, traffic_bytes=100) == 0.0
    assert progression_factor(progression=item, now=100, traffic_bytes=750) == 0.5
    assert progression_factor(progression=item, now=100, traffic_bytes=50_000) == 1.0


def test_every_impairment_value_moves_between_the_two_profiles():
    start = Impairment(
        delay_ms=100, jitter_ms=40, loss_percent=0, bandwidth_kbit=2_000, reorder_percent=0,
        duplicate_percent=0, corrupt_percent=0, loss_burst_packets=0,
        delay_randomness=Randomness(variation_percent=10),
        jitter_randomness=Randomness(variation_percent=20),
        loss_randomness=Randomness(variation_percent=0, enabled_probability=100),
        bandwidth_randomness=Randomness(variation_percent=0),
        reorder_randomness=Randomness(variation_percent=8),
        duplicate_randomness=Randomness(variation_percent=4),
        corrupt_randomness=Randomness(variation_percent=12, enabled_probability=100),
    )
    end = Impairment(
        delay_ms=500, jitter_ms=120, loss_percent=40, bandwidth_kbit=400, reorder_percent=20,
        duplicate_percent=8, corrupt_percent=4, loss_burst_packets=8,
        delay_randomness=Randomness(variation_percent=30),
        jitter_randomness=Randomness(variation_percent=60),
        loss_randomness=Randomness(variation_percent=40, enabled_probability=60),
        bandwidth_randomness=Randomness(variation_percent=80),
        reorder_randomness=Randomness(variation_percent=40),
        duplicate_randomness=Randomness(variation_percent=20),
        corrupt_randomness=Randomness(variation_percent=60, enabled_probability=20),
    )

    assert interpolate_impairment(start=start, end=end, factor=0.25) == Impairment(
        delay_ms=200, jitter_ms=60, loss_percent=10, bandwidth_kbit=1_600, reorder_percent=5,
        duplicate_percent=2, corrupt_percent=1, loss_burst_packets=2,
        delay_randomness=Randomness(variation_percent=15),
        jitter_randomness=Randomness(variation_percent=30),
        loss_randomness=Randomness(variation_percent=10, enabled_probability=90),
        bandwidth_randomness=Randomness(variation_percent=20),
        reorder_randomness=Randomness(variation_percent=16),
        duplicate_randomness=Randomness(variation_percent=8),
        corrupt_randomness=Randomness(variation_percent=24, enabled_probability=80),
    )


def test_a_bandwidth_limit_that_only_one_side_has_switches_at_the_end():
    limited, free = Impairment(bandwidth_kbit=800), Impairment()

    def bandwidth(start, end, factor):
        return interpolate_impairment(start=start, end=end, factor=factor).bandwidth_kbit

    assert bandwidth(free, free, 0.5) is None
    assert bandwidth(free, limited, 0.999) is None
    assert bandwidth(free, limited, 1.0) == 800
    assert bandwidth(limited, free, 0.999) == 800
    assert bandwidth(limited, free, 1.0) is None


def test_a_profile_moves_every_setting_and_changes_modes_only_at_the_end():
    start = Profile(
        id="normal", name="Normal", upload=Impairment(delay_ms=100), download=Impairment(delay_ms=0),
        random_seed=11, random_mode="repeatable", reroll_interval_seconds=100,
        reroll_interval_randomness=Randomness(variation_percent=10),
        homepage_bounce_percent=0, outage_probability=20, outage_duration_seconds=10,
        outage_duration_randomness=Randomness(variation_percent=0),
        outage_interval_seconds=200, outage_interval_randomness=Randomness(variation_percent=20,
                                                                           enabled_probability=50),
        website_mode="unrestricted", website_domains=("a.example",),
        website_randomness=Randomness(variation_percent=0),
    )
    end = Profile(
        id="slow", name="Slow", upload=Impairment(delay_ms=500), download=Impairment(delay_ms=400),
        random_seed=None, random_mode="fresh", reroll_interval_seconds=300,
        reroll_interval_randomness=Randomness(variation_percent=30, enabled_probability=60),
        homepage_bounce_percent=40, outage_probability=60, outage_duration_seconds=50,
        outage_duration_randomness=Randomness(variation_percent=40, enabled_probability=20),
        outage_interval_seconds=600, outage_interval_randomness=Randomness(variation_percent=60,
                                                                           enabled_probability=90),
        website_mode="allow_only", website_domains=("b.example",),
        website_randomness=Randomness(variation_percent=20, enabled_probability=40),
    )

    assert interpolate_profile(start=start, end=end, factor=0.25) == Profile(
        id="slow", name="Slow", upload=Impairment(delay_ms=200), download=Impairment(delay_ms=100),
        random_seed=11, random_mode="repeatable", reroll_interval_seconds=150,
        reroll_interval_randomness=Randomness(variation_percent=15, enabled_probability=90),
        homepage_bounce_percent=10, outage_probability=30, outage_duration_seconds=20,
        outage_duration_randomness=Randomness(variation_percent=10, enabled_probability=80),
        outage_interval_seconds=300, outage_interval_randomness=Randomness(variation_percent=30,
                                                                           enabled_probability=60),
        website_mode="unrestricted", website_domains=("a.example",),
        website_randomness=Randomness(variation_percent=5, enabled_probability=85),
    )
    # A factor outside 0 to 1 counts as the nearest end.
    assert interpolate_profile(start=start, end=end, factor=-1) == replace(
        start, id="slow", name="Slow")
    assert interpolate_profile(start=start, end=end, factor=1) == end
    assert interpolate_profile(start=start, end=end, factor=1.5) == end
    assert interpolate_profile(start=start, end=end, factor=2.0) == end


def test_a_variation_with_decimals_moves_in_hundredths_and_whole_ones_in_whole_numbers():
    def website_variation(first, last, factor):
        start = Profile(id="a", name="A", website_randomness=Randomness(variation_percent=first))
        end = Profile(id="b", name="B", website_randomness=Randomness(variation_percent=last))
        return interpolate_profile(
            start=start, end=end, factor=factor).website_randomness.variation_percent

    # Halves of a hundredth round to the even one.
    assert website_variation(1.5, 1.75, 0.5) == 1.62
    # One side with decimals is enough to keep the decimals.
    assert website_variation(10, 20.5, 0.5) == 15.25
    assert website_variation(10, 21, 0.25) == 13


def test_a_custom_range_moves_with_the_other_side_and_keeps_the_end_as_it_is():
    narrow = Impairment(delay_ms=0, delay_randomness=Randomness(
        mode="range", minimum=10, maximum=30, enabled_probability=100))
    wide = Impairment(delay_ms=100, delay_randomness=Randomness(
        variation_percent=50, enabled_probability=60))

    middle = interpolate_impairment(start=narrow, end=wide, factor=0.5).delay_randomness
    assert middle == Randomness(mode="range", minimum=30, maximum=90, enabled_probability=80)
    # At the end the plain settings of the end profile are used, not a range made from them.
    assert interpolate_impairment(start=narrow, end=wide, factor=1.0).delay_randomness == (
        wide.delay_randomness)
    assert interpolate_impairment(start=narrow, end=wide, factor=0.0).delay_randomness == (
        narrow.delay_randomness)


def test_a_feature_without_a_limit_on_one_side_keeps_the_start_settings_in_between():
    unlimited = Impairment()
    ranged = Impairment(bandwidth_kbit=1_000, bandwidth_randomness=Randomness(
        mode="range", minimum=500, maximum=900))

    for start, end in ((unlimited, ranged), (ranged, unlimited)):
        halfway = interpolate_impairment(start=start, end=end, factor=0.5)
        assert halfway.bandwidth_randomness == start.bandwidth_randomness


# Which clients get their profile

PHONE = "aa:bb:cc:00:00:01"
MAC = {index: f"aa:bb:cc:00:00:{index:02d}" for index in range(2, 10)}


def client(mac, address, *, verified=True):
    return {"mac_address": mac, "ipv4_address": address, "association_verified": verified}


def seen(*clients):
    return {"association_status_available": True, "devices": list(clients)}


@pytest.fixture
def repository(tmp_path):
    repository = ConfigRepository(tmp_path / "settings.db")
    repository.save_profile(Profile(id="p", name="P"))
    repository.save_unaffected_device(UnaffectedDevice(PHONE, "Phone", 1))
    yield repository
    repository.close()


def save(repository, device_id, address, mac=None):
    repository.save_device(Device(device_id, device_id, address, "p", mac))


def test_a_saved_device_gets_its_profile_only_while_it_is_seen_alone_at_its_address(repository):
    save(repository, "a-protected", "192.168.50.10", PHONE)
    save(repository, "b-no-mac", "192.168.50.11")
    save(repository, "c-seen", "192.168.50.12", MAC[3])
    save(repository, "d-unseen", "192.168.50.13", MAC[4])
    save(repository, "e-shared-address", "192.168.50.14", MAC[5])
    save(repository, "f-seen", "192.168.50.15", MAC[6])
    live = seen(
        # Rows that cannot be read or are not verified do not end the list.
        client(None, "192.168.50.99"),
        client("not a mac", "nope"),
        client(MAC[9], "192.168.50.98", verified=False),
        client(PHONE, "192.168.50.10"),
        client(MAC[3], "192.168.50.30"),
        client(MAC[5], "192.168.50.50"),
        client(MAC[7], "192.168.50.50"),
        client(MAC[6], "192.168.50.60"),
    )

    result = eligible_clients(repository, live)

    # The live address replaces the saved one.
    assert [(device.id, str(device.ipv4_address)) for device in result.devices] == [
        ("c-seen", "192.168.50.30"), ("f-seen", "192.168.50.60")]
    assert result.excluded_ids == frozenset(
        {"a-protected", "b-no-mac", "d-unseen", "e-shared-address"})
    assert result.unverified_ids == ("b-no-mac", "d-unseen", "e-shared-address")
    assert result.protected_macs == (PHONE,)


def test_without_a_live_list_only_devices_without_a_mac_or_on_the_protected_list_wait(repository):
    save(repository, "a-protected", "192.168.50.10", PHONE)
    save(repository, "b-no-mac", "192.168.50.11")
    save(repository, "c-saved", "192.168.50.12", MAC[3])

    result = eligible_clients(repository, None)

    assert [(device.id, str(device.ipv4_address)) for device in result.devices] == [
        ("c-saved", "192.168.50.12")]
    assert result.excluded_ids == frozenset({"a-protected", "b-no-mac"})
    assert result.unverified_ids == ("b-no-mac",)


def test_a_live_list_without_clients_leaves_every_device_with_a_mac_unverified(repository):
    save(repository, "a-saved", "192.168.50.12", MAC[3])

    for live in ({"association_status_available": True}, seen()):
        result = eligible_clients(repository, live)

        assert result.devices == ()
        assert result.unverified_ids == ("a-saved",)


def test_each_saved_device_says_why_its_profile_applies_or_waits(repository):
    save(repository, "a-protected", "192.168.50.10", PHONE)
    save(repository, "b-owned", "192.168.50.11")
    save(repository, "c-unknown", "192.168.50.12")
    save(repository, "d-unseen", "192.168.50.13", MAC[4])
    save(repository, "e-active", "192.168.50.14", MAC[5])
    save(repository, "f-shared", "192.168.50.15")
    live = seen(
        # Rows that are not verified or cannot be read do not end the list.
        client(PHONE, "192.168.50.11", verified=False),
        client("not a mac", "nope"),
        client(PHONE, "192.168.50.11"),
        client(MAC[5], "192.168.50.14"),
        client(PHONE, "192.168.50.15"),
        client(MAC[8], "192.168.50.15"),
    )

    states = device_protection_states(repository, live)

    assert set(states) == {"a-protected", "b-owned", "c-unknown", "d-unseen", "e-active", "f-shared"}
    assert {name: state["effective_state"] for name, state in states.items()} == {
        "a-protected": "protected", "b-owned": "unverified", "c-unknown": "unverified",
        "d-unseen": "unverified", "e-active": "active", "f-shared": "unverified",
    }
    assert {key for state in states.values() for key in state} == {
        "effective_state", "effective_note", "protected_match"}
    assert states["a-protected"]["protected_match"] is None
    assert states["a-protected"]["effective_note"].startswith("On the Protected list")
    # A device without a MAC address that sits where a protected client is names that client.
    assert states["b-owned"]["protected_match"] == {"mac_address": PHONE, "name": "Phone"}
    assert "protected Phone" in states["b-owned"]["effective_note"]
    # Two clients at one address do not prove which one it is.
    for name in ("c-unknown", "f-shared"):
        assert states[name]["protected_match"] is None
        assert "no MAC address" in states[name]["effective_note"], name
    assert states["d-unseen"]["protected_match"] is None
    assert "cannot confirm where this device is" in states["d-unseen"]["effective_note"]
    assert states["e-active"] == {
        "effective_state": "active", "effective_note": None, "protected_match": None}


def test_a_live_list_without_clients_pauses_a_device_with_a_mac(repository):
    save(repository, "a-saved", "192.168.50.12", MAC[3])

    state = device_protection_states(repository, {"association_status_available": True})

    assert state["a-saved"]["effective_state"] == "unverified"


class BusyHelper:
    def __init__(self):
        self.busy = False

    def connected_device_snapshot(self):
        if self.busy:
            raise OSError("busy")
        return {"devices": [], "association_status_available": True}


def test_the_last_good_clients_stand_in_for_a_busy_helper_for_ten_seconds_at_most():
    helper = BusyHelper()
    with patch.object(eligibility.time, "monotonic", return_value=100.0):
        eligibility.station_snapshot(helper)
    helper.busy = True
    unknown = {"devices": [], "association_status_available": False}

    for now, reused in ((100.0, True), (110.0, True), (110.5, False)):
        with patch.object(eligibility.time, "monotonic", return_value=now):
            result = eligibility.station_snapshot(helper)
        if reused:
            assert result == {"devices": [], "association_status_available": True, "stale": True}
        else:
            assert result == unknown
