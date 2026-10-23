"""Every limit and every normalisation of the configuration models (mutation testing, 2026-10-18).

A mutation run showed that most numeric limits in `config/models.py` were not held by a test:
a limit moved by one still passed. Each row of the tables below is one field. The test accepts
the lowest and the highest value, refuses the value one step outside each end with the exact
message the person sees, and refuses a boolean (which Python counts as an integer).
"""

from ipaddress import IPv4Address as A

import pytest

from jittersim.config.models import (
    AccessOptions,
    DataLimit,
    Device,
    DeviceGroup,
    DomainObservation,
    GroupDataLimit,
    Impairment,
    MacPolicy,
    Profile,
    Progression,
    Randomness,
    RandomWebsiteOutcome,
    RuntimeState,
    SavedSettings,
    Scenario,
    ScenarioRun,
    ScenarioStep,
    Schedule,
    ServiceAssignment,
    ServiceList,
    ServiceQuota,
    SiteBlock,
    TimeQuota,
    Timer,
    ValidationError,
    WebsitePolicy,
    normalize_domain,
    normalize_mac_address,
    normalize_service_hostname,
    time_quota_counter_id,
)

BIG = 2**63 - 1


def refuses(message, build, *args, **kwargs):
    with pytest.raises(ValidationError) as raised:
        build(*args, **kwargs)
    assert str(raised.value) == message


# Builders: a valid object with some fields changed.


def impairment(**f):
    return Impairment(**f)


def profile(**f):
    return Profile("p", "P", **f)


def observation(**f):
    return DomainObservation(**{
        "ipv4_address": "192.168.50.10", "domain": "example.com", "query_count": 1,
        "first_seen": 0, "last_seen": 0, **f})


def schedule(**f):
    return Schedule(**{
        "id": "s", "device_id": "d", "profile_id": "p", "days_of_week": (1,),
        "start_minute": 720, "end_minute": 60, **f})


def timer(**f):
    return Timer(**{"id": "t", "device_id": "d", "profile_id": "p", "started_at": 0,
                    "expires_at": 10, **f})


def scenario_run(**f):
    return ScenarioRun(**{"id": "r", "scenario_id": "s", "started_at": 0, "device_id": "d", **f})


def progression(**f):
    return Progression(**{
        "id": "g", "device_id": "d", "start_profile_id": "a", "end_profile_id": "b",
        "started_at": 0, "duration_seconds": 60, **f})


def data_limit(**f):
    return DataLimit(**{"device_id": "d", "profile_id": "p", "limit_bytes": 1, **f})


def group_limit(**f):
    return GroupDataLimit(**{"group_id": "g", "profile_id": "p", "limit_bytes": 1, **f})


def quota(**f):
    return ServiceQuota(**{
        "id": "q", "device_id": "d", "name": "Q", "domains": ("a.example.com",),
        "profile_id": "p", "limit_bytes": 1, **f})


def time_quota(**f):
    return TimeQuota(**{
        "id": "t", "device_id": "d", "name": "T", "domains": ("a.example.com",),
        "profile_id": "p", "limit_seconds": 60, **f})


def runtime(**f):
    return RuntimeState(**f)


def randomness(**f):
    return Randomness(**f)


def days(value):
    return schedule(days_of_week=(value,))


def outcome(**f):
    return RandomWebsiteOutcome(**{
        "device_id": "d", "domain": "example.com", "kept": True, "happened_at": 0, **f})


# name, builder, keyword, label in the message, lowest, highest, whether the highest builds
def limit_rows():
    rows = []
    for field, low, high in (
        ("delay_ms", 0, 60_000), ("jitter_ms", 0, 60_000), ("loss_percent", 0, 100),
        ("reorder_percent", 0, 100), ("duplicate_percent", 0, 100),
        ("corrupt_percent", 0, 100), ("loss_burst_packets", 0, 100),
        ("bandwidth_kbit", 1, 1_000_000),
    ):
        rows.append(("Impairment", impairment, field, field, low, high, True))
    for field, low, high in (
        ("random_seed", 0, BIG), ("reroll_interval_seconds", 0, 604_800),
        ("outage_probability", 0, 100), ("outage_duration_seconds", 0, 604_800),
        ("outage_interval_seconds", 0, 604_800), ("homepage_bounce_percent", 0, 100),
    ):
        rows.append(("Profile", profile, field, field, low, high, True))
    rows += [
        ("DomainObservation", observation, "query_count", "query_count", 1, 1_000_000_000, True),
        ("DomainObservation", observation, "first_seen", "first_seen", 0, BIG, False),
        ("DomainObservation", observation, "last_seen", "last_seen", 0, BIG, True),
        ("Schedule", schedule, "start_minute", "start_minute", 0, 1_439, True),
        ("Schedule", schedule, "end_minute", "end_minute", 0, 1_439, True),
        ("Schedule", days, "days_of_week", "day_of_week", 0, 6, True),
        ("Timer", timer, "started_at", "started_at", 0, BIG, False),
        ("Timer", timer, "expires_at", "expires_at", 1, BIG, True),
        ("ScenarioRun", scenario_run, "started_at", "started_at", 0, BIG, True),
        ("Progression", progression, "started_at", "started_at", 0, BIG, True),
        ("Progression", progression, "duration_seconds", "duration_seconds", 60, 604_800, True),
        ("Progression", progression, "traffic_target_queries", "traffic_target_queries",
         0, 1_000_000_000, True),
        ("Progression", progression, "traffic_baseline_queries", "traffic_baseline_queries",
         -1_000_000_000_000, 1_000_000_000_000, True),
        ("Progression", progression, "traffic_target_bytes", "traffic_target_bytes", 0, BIG, True),
        ("Progression", progression, "traffic_baseline_bytes", "traffic_baseline_bytes",
         0, BIG, True),
        ("RuntimeState", runtime, "configuration_version", "configuration_version", 0, BIG, True),
        ("ServiceQuota", quota, "weight_percent", "weight_percent", 10, 1_000, True),
        ("TimeQuota", time_quota, "limit_seconds", "limit_seconds", 60, 31_536_000, True),
        ("TimeQuota", time_quota, "window_duration_seconds", "window_duration_seconds",
         60, 604_800, True),
        ("TimeQuota", time_quota, "used_seconds", "used_bytes", 0, BIG, True),
        ("TimeQuota", time_quota, "last_sample_at", "last_sample_at", 0, BIG, True),
        ("RandomWebsiteOutcome", outcome, "happened_at", "happened_at", 0, BIG, True),
    ]
    for name, build in (("DataLimit", data_limit), ("GroupDataLimit", group_limit),
                        ("ServiceQuota", quota)):
        rows.append((name, build, "limit_bytes", "limit_bytes", 1, BIG, True))
        for field in ("used_bytes", "counter_bytes", "last_reset_at"):
            rows.append((name, build, field, field, 0, BIG, True))
    for field in ("counter_bytes", "last_reset_at"):
        rows.append(("TimeQuota", time_quota, field, field, 0, BIG, True))
    return rows


ROWS = limit_rows()
IDS = [f"{row[0]}.{row[2]}" for row in ROWS]


def make(build, field, value):
    return build(**{field: value}) if build is not days else build(value)


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_a_numeric_field_accepts_its_limits_and_refuses_one_step_outside(row):
    _, build, field, label, low, high, high_builds = row
    message = f"{label} must be between {low} and {high}"
    make(build, field, low)
    refuses(message, make, build, field, low - 1)
    if high_builds:
        make(build, field, high)
    refuses(message, make, build, field, high + 1)


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_a_numeric_field_refuses_a_boolean_and_a_fraction_and_text(row):
    _, build, field, label, low, _, _ = row
    for value in (True, False, 1.5, "1", None):
        if field in ("bandwidth_kbit", "random_seed") and value is None:
            continue
        if field == "homepage_bounce_percent":
            message = "homepage_bounce_percent must be an integer"
        else:
            message = f"{label} must be an integer"
        refuses(message, make, build, field, value)


# Validation that sits beside the numeric fields


def test_a_percent_is_whole():
    refuses("loss_percent must be an integer", impairment, loss_percent=0.5)


def test_a_timer_must_end_after_it_starts():
    timer(started_at=9, expires_at=10)
    refuses("expires_at must be later than started_at", timer, started_at=10, expires_at=10)
    refuses("expires_at must be later than started_at", timer, started_at=11, expires_at=10)


def test_the_highest_start_of_a_timer_leaves_no_room_for_the_end():
    refuses("expires_at must be later than started_at", timer, started_at=BIG, expires_at=BIG)


def test_an_observation_may_not_end_before_it_starts():
    observation(first_seen=5, last_seen=5)
    observation(first_seen=4, last_seen=5)
    refuses("first_seen must not be later than last_seen", observation, first_seen=6,
            last_seen=5)


def test_an_observation_is_normalised():
    seen = observation(ipv4_address="10.0.0.1", domain=" Example.COM. ",
                       mac_address="02-00-00-00-00-AA")
    assert seen.ipv4_address == A("10.0.0.1")
    assert seen.domain == "example.com"
    assert seen.mac_address == "02:00:00:00:00:aa"
    assert observation().mac_address is None


def test_an_observation_refuses_a_bad_address():
    refuses("mac_address must identify one device", observation, mac_address="00:00:00:00:00:00")
    refuses("mac_address must identify one device", observation, mac_address="zz")
    refuses("ipv4_address must be a valid IPv4 address", observation, ipv4_address="x")
    refuses("ipv4_address must be a valid IPv4 address", observation, ipv4_address=None)
    refuses("ipv4_address must be inside an RFC1918 private network", observation,
            ipv4_address="8.8.8.8")
    for address in ("10.255.255.255", "172.16.0.1", "172.31.255.255", "192.168.0.1"):
        observation(ipv4_address=address)
    for address in ("11.0.0.0", "172.15.255.255", "172.32.0.0", "192.169.0.0"):
        refuses("ipv4_address must be inside an RFC1918 private network", observation,
                ipv4_address=address)


def test_a_random_website_outcome_is_checked():
    assert outcome(domain="WWW.Example.com.").domain == "www.example.com"
    refuses("device_id must contain 1 to 64 lowercase letters, numbers, hyphens or underscores",
            outcome, device_id="Bad Id")
    refuses("kept must be a boolean", outcome, kept=1)
    refuses("kept must be a boolean", outcome, kept=None)


# Names, identifiers, domains and addresses


IDENTIFIER = "must contain 1 to 64 lowercase letters, numbers, hyphens or underscores"


def test_identifiers_accept_64_characters_and_refuse_65():
    Profile("a" * 64, "P")
    refuses(f"id {IDENTIFIER}", Profile, "a" * 65, "P")
    refuses(f"id {IDENTIFIER}", Profile, "", "P")
    refuses(f"id {IDENTIFIER}", Profile, "-a", "P")
    refuses(f"id {IDENTIFIER}", Profile, "A", "P")
    refuses(f"id {IDENTIFIER}", Profile, 5, "P")
    Profile("0_-", "P")


def test_names_are_trimmed_and_limited_to_100_characters():
    assert Scenario("s", "  Name  ", (ScenarioStep("p", 10),)).name == "Name"
    Scenario("s", "n" * 100, (ScenarioStep("p", 10),))
    refuses("scenario name must contain between 1 and 100 characters", Scenario, "s", "n" * 101,
            (ScenarioStep("p", 10),))
    refuses("scenario name must contain between 1 and 100 characters", Scenario, "s", "   ",
            (ScenarioStep("p", 10),))
    refuses("scenario name must contain between 1 and 100 characters", Scenario, "s", 5,
            (ScenarioStep("p", 10),))


def test_domains_are_lowercased_and_lose_their_final_dot():
    assert normalize_domain("Example.COM.") == "example.com"
    assert normalize_domain("  a.b.c  ") == "a.b.c"
    assert normalize_domain("example.com..") == "example.com"
    refuses("domain must contain valid DNS labels", normalize_domain, "a..b")
    refuses("domain must contain valid DNS labels", normalize_domain, "-a.com")
    refuses("domain must contain valid DNS labels", normalize_domain, "a_b.com")
    refuses("domain must be a string", normalize_domain, 5)
    refuses("domain must contain between 1 and 253 characters", normalize_domain, "")
    refuses("domain must contain between 1 and 253 characters", normalize_domain, ".")


def test_a_domain_may_have_253_characters_and_a_label_63():
    label = "a" * 63
    long_name = ".".join([label, label, label, "b" * 61])
    assert len(long_name) == 253
    assert normalize_domain(long_name) == long_name
    refuses("domain must contain between 1 and 253 characters", normalize_domain,
            long_name + "b")
    refuses("domain must contain valid DNS labels", normalize_domain, "a" * 64 + ".com")
    assert normalize_domain("a" * 63 + ".com") == "a" * 63 + ".com"


def test_a_service_hostname_needs_two_parts_and_is_not_an_address():
    assert normalize_service_hostname("Example.COM") == "example.com"
    refuses("browser service domains must have at least two parts such as example.com",
            normalize_service_hostname, "localhost")
    for address in ("10.0.0.1", "1.2.3.4", "192.168.50.7"):
        refuses("browser service domains must be hostnames, not IP addresses",
                normalize_service_hostname, address)


def test_a_mac_address_is_lowercase_with_colons():
    assert normalize_mac_address(" AA-BB-CC-DD-EE-FF ") == "aa:bb:cc:dd:ee:ff"
    for bad in ("aa:bb:cc:dd:ee", "aa:bb:cc:dd:ee:fg", "aabbccddeeff", "aa:bb:cc:dd:ee:ff:00"):
        refuses("mac_address must contain six hexadecimal pairs", normalize_mac_address, bad)
    refuses("mac_address must be a string", normalize_mac_address, 5)


def test_a_device_names_the_field_that_is_wrong():
    refuses(f"id {IDENTIFIER}", Device, "Bad", "D", "192.168.50.7", "p")
    refuses("name must contain between 1 and 100 characters", Device, "d", " ",
            "192.168.50.7", "p")
    refuses(f"profile_id {IDENTIFIER}", Device, "d", "D", "192.168.50.7", "Bad")
    refuses("name must contain between 1 and 100 characters", Profile, "p", "")
    refuses(f"profile_id {IDENTIFIER}", ScenarioStep, "Bad", 10)
    refuses(f"scenario id {IDENTIFIER}", Scenario, "Bad", "S", (ScenarioStep("p", 10),))


def test_a_device_normalises_its_addresses():
    device = Device("d", "D", "192.168.50.7", "p", mac_address="AA-BB-CC-DD-EE-FF")
    assert device.ipv4_address == A("192.168.50.7")
    assert device.mac_address == "aa:bb:cc:dd:ee:ff"
    assert Device("d", "D", "192.168.50.7", "p").mac_address is None


def test_the_counter_name_of_a_time_quota_is_fixed():
    # The name goes into the firewall counters and into saved state, so it must never change.
    assert time_quota_counter_id("daily") == "tq-6f31304cdfd0a3a6"
    assert time_quota_counter_id("a") == "tq-ca978112ca1bbdca"
    assert time_quota_counter_id("a-b_c1") == "tq-5e8ef4784002f9ff"
    refuses(f"time quota id {IDENTIFIER}", time_quota_counter_id, "Bad ID")


# Randomness


def test_randomness_variation_is_a_number_in_steps_of_one_hundredth():
    randomness(variation_percent=0)
    randomness(variation_percent=100)
    randomness(variation_percent=0.01)
    randomness(variation_percent=99.99)
    limit = "variation_percent must be a finite number between 0 and 100"
    refuses(limit, randomness, variation_percent=-0.01)
    refuses(limit, randomness, variation_percent=100.01)
    refuses(limit, randomness, variation_percent=float("nan"))
    refuses(limit, randomness, variation_percent=float("inf"))
    refuses(limit, randomness, variation_percent=True)
    refuses(limit, randomness, variation_percent="5")
    refuses("variation_percent must be in steps of 0.01", randomness, variation_percent=0.005)
    refuses("variation_percent must be in steps of 0.01", randomness, variation_percent=50.001)


def test_the_chance_that_a_value_is_on_is_a_percent():
    randomness(enabled_probability=0)
    randomness(enabled_probability=100)
    refuses("enabled_probability must be between 0 and 100", randomness,
            enabled_probability=101)
    refuses("enabled_probability must be between 0 and 100", randomness,
            enabled_probability=-1)


def test_randomness_has_a_relative_and_a_range_mode():
    refuses("randomness mode must be relative or range", randomness, mode="absolute")
    refuses("relative variation cannot also contain range endpoints", randomness, minimum=1)
    refuses("relative variation cannot also contain range endpoints", randomness, maximum=1)
    refuses("relative variation cannot also contain range endpoints", randomness, minimum=1,
            maximum=2)
    assert randomness(mode="range", minimum=1, maximum=2).maximum == 2
    randomness(mode="range", minimum=0, maximum=0)
    randomness(mode="range", minimum=0, maximum=1_000_000)
    refuses("range minimum must be a finite number between 0 and 1000000", randomness,
            mode="range", minimum=-1, maximum=2)
    refuses("range minimum must be a finite number between 0 and 1000000", randomness,
            mode="range", minimum=1_000_001, maximum=1_000_001)
    refuses("range maximum must be a finite number between 0 and 1000000", randomness,
            mode="range", minimum=0, maximum=1_000_001)
    refuses("range maximum must be a finite number between 0 and 1000000", randomness,
            mode="range", minimum=0, maximum=-1)
    refuses("range minimum must be a finite number between 0 and 1000000", randomness,
            mode="range", maximum=2)
    refuses("range minimum must not exceed maximum", randomness, mode="range", minimum=3,
            maximum=2)


RANGE_LIMITS = (
    ("delay", 0, 60_000), ("jitter", 0, 60_000), ("loss", 0, 100), ("bandwidth", 1, 1_000_000),
    ("reorder", 0, 100), ("duplicate", 0, 100), ("corrupt", 0, 100),
)


def range_message(field, kind, value, low, high):
    """Randomness checks 0 to 1,000,000 first, then the impairment checks its own field."""

    if not 0 <= value <= 1_000_000:
        return f"range {kind} must be a finite number between 0 and 1000000"
    return f"{field} {kind} must be a finite number between {low} and {high}"


@pytest.mark.parametrize("feature,low,high", RANGE_LIMITS, ids=[row[0] for row in RANGE_LIMITS])
def test_a_value_range_of_an_impairment_stays_inside_the_limits_of_its_field(feature, low, high):
    field = feature + "_randomness"

    def build(minimum, maximum):
        return impairment(**{field: randomness(mode="range", minimum=minimum, maximum=maximum)})

    build(low, high)
    build(low, low)
    build(high, high)
    refuses(range_message(field, "minimum", high + 1, low, high), build, high + 1, high + 1)
    refuses(range_message(field, "maximum", high + 1, low, high), build, low, high + 1)
    if low > 0:
        refuses(range_message(field, "minimum", low - 1, low, high), build, low - 1, high)
    refuses(f"{field} minimum must be a whole number", build, low + 0.5, high)
    refuses(f"{field} maximum must be a whole number", build, low, high - 0.5)
    refuses(f"{field} must be a Randomness", impairment, **{field: {"mode": "range"}})


def test_an_impairment_without_a_range_ignores_the_range_limits():
    impairment(delay_randomness=randomness(variation_percent=100.0))


def test_profiles_take_no_value_range_in_their_timing_fields():
    for field in ("reroll_interval_randomness", "outage_duration_randomness",
                  "outage_interval_randomness", "website_randomness"):
        refuses(f"{field} does not support a custom value range", profile,
                **{field: randomness(mode="range", minimum=1, maximum=2)})
        refuses(f"{field} must be a Randomness", profile, **{field: None})
        profile(**{field: randomness(variation_percent=5)})
    refuses("upload must be an Impairment", profile, upload=None)
    refuses("download must be an Impairment", profile, download={})


def test_a_profile_chooses_a_repeatable_or_a_fresh_pattern():
    profile(random_mode="repeatable", random_seed=5)
    profile(random_mode="fresh")
    refuses("random_mode must be repeatable or fresh", profile, random_mode="other")
    refuses("Clear the saved seed before choosing a fresh pattern", profile,
            random_mode="fresh", random_seed=0)
    refuses("website_mode is not supported", profile, website_mode="other")
    profile(website_mode="allow_only")


def test_a_profile_keeps_a_clean_website_list():
    kept = profile(website_domains=("B.example.com", "a.example.com.", "b.example.com"))
    assert kept.website_domains == ("b.example.com", "a.example.com")
    profile(website_domains=tuple(f"h{n}.example.com" for n in range(100)))
    refuses("website_domains must contain no more than 100 domains", profile,
            website_domains=tuple(f"h{n}.example.com" for n in range(101)))
    refuses("website_domains must be a list", profile, website_domains="example.com")
    assert profile(website_domains=["a.com"]).website_domains == ("a.com",)
    assert profile(website_domains=tuple("a.com" for _ in range(200))).website_domains == (
        "a.com",)


# Scenarios, groups and schedules


def test_a_step_lasts_from_ten_seconds_to_a_day():
    message = "a step must last 10 seconds to 24 hours"
    ScenarioStep("p", 10)
    ScenarioStep("p", 86_400)
    for bad in (9, 86_401, True, 10.5, "10", None):
        refuses(message, ScenarioStep, "p", bad)


def test_a_scenario_has_one_to_twenty_steps():
    step = ScenarioStep("p", 10)
    assert Scenario("s", "S", [step] * 20).steps == (step,) * 20
    refuses("a scenario can have at most 20 steps", Scenario, "s", "S", (step,) * 21)
    refuses("a scenario needs at least one step", Scenario, "s", "S", ())
    refuses("a scenario needs at least one step", Scenario, "s", "S", "x")
    refuses("each step must be a ScenarioStep", Scenario, "s", "S", (step, "x"))
    refuses("loop must be a boolean", Scenario, "s", "S", (step,), loop=1)
    assert Scenario("s", "S", (step, step)).total_seconds == 20
    assert isinstance(Scenario("s", "S", [step]).steps, tuple)


def test_a_group_has_one_to_fifteen_devices():
    ids = tuple(f"d{n}" for n in range(15))
    assert DeviceGroup("g", "G", list(ids)).device_ids == ids
    refuses("a group cannot contain more than 15 devices", DeviceGroup, "g", "G",
            ids + ("d15",))
    refuses("device_ids must contain at least one device", DeviceGroup, "g", "G", ())
    refuses("device_ids must contain at least one device", DeviceGroup, "g", "G", "d1")
    refuses("device_ids must not contain duplicates", DeviceGroup, "g", "G", ("a", "a"))
    refuses(f"device_id {IDENTIFIER}", DeviceGroup, "g", "G", ("Bad",))
    assert DeviceGroup("g", "G", ("a",), profile_id="p").profile_id == "p"
    assert DeviceGroup("g", "G", ("a",)).profile_id is None
    refuses(f"profile_id {IDENTIFIER}", DeviceGroup, "g", "G", ("a",), profile_id="Bad")
    refuses(f"group id {IDENTIFIER}", DeviceGroup, "Bad", "G", ("a",))
    refuses("group name must contain between 1 and 100 characters", DeviceGroup, "g", " ",
            ("a",))


def test_a_schedule_names_days_and_two_different_minutes():
    assert schedule(days_of_week=[6, 0, 6, 3]).days_of_week == (0, 3, 6)
    refuses("days_of_week must contain at least one day", schedule, days_of_week=())
    refuses("days_of_week must contain at least one day", schedule, days_of_week="1")
    refuses("schedule start and end must be different", schedule, start_minute=5, end_minute=5)
    refuses("enabled must be a boolean", schedule, enabled=1)
    refuses("a schedule needs exactly one of a device, a group or everyone else", schedule,
            device_id=None)
    refuses("a schedule needs exactly one of a device, a group or everyone else", schedule,
            group_id="g")
    refuses("a schedule needs exactly one of a device, a group or everyone else", schedule,
            device_id=None, everyone_else=True, group_id="g")
    refuses("everyone_else must be a boolean", schedule, everyone_else=0)
    assert schedule(device_id=None, everyone_else=True).everyone_else is True
    assert schedule(device_id=None, group_id="g").group_id == "g"
    refuses(f"group_id {IDENTIFIER}", schedule, device_id=None, group_id="Bad")
    refuses(f"device_id {IDENTIFIER}", schedule, device_id="Bad")
    refuses(f"id {IDENTIFIER}", schedule, id="Bad")
    refuses(f"profile_id {IDENTIFIER}", schedule, profile_id="Bad")


def test_a_timer_and_a_scenario_run_check_their_flags_and_ids():
    refuses("enabled must be a boolean", timer, enabled=1)
    refuses(f"timer id {IDENTIFIER}", timer, id="Bad")
    refuses(f"device_id {IDENTIFIER}", timer, device_id="Bad")
    refuses(f"profile_id {IDENTIFIER}", timer, profile_id="Bad")
    refuses("enabled must be a boolean", scenario_run, enabled=1)
    refuses(f"run id {IDENTIFIER}", scenario_run, id="Bad")
    refuses(f"scenario_id {IDENTIFIER}", scenario_run, scenario_id="Bad")
    refuses("a scenario run needs exactly one of a device, a group or everyone else",
            scenario_run, device_id=None)


def test_a_progression_checks_its_mode_and_targets():
    refuses("progression mode is not supported", progression, mode="other")
    progression(mode="traffic", traffic_target_queries=1)
    refuses("traffic mode requires a positive query target", progression, mode="traffic",
            traffic_target_queries=0)
    progression(mode="bytes", traffic_target_bytes=1)
    refuses("bytes mode requires a positive byte target", progression, mode="bytes",
            traffic_target_bytes=0)
    progression(mode="time", traffic_target_queries=0, traffic_target_bytes=0)
    refuses("enabled must be a boolean", progression, enabled=1)
    kept = progression(website_domains=("A.example.com", "a.example.com."))
    assert kept.website_domains == ("a.example.com",)
    refuses("website_domains must contain no more than 100 domains", progression,
            website_domains=tuple(f"h{n}.example.com" for n in range(101)))
    refuses(f"progression id {IDENTIFIER}", progression, id="Bad")
    refuses(f"device_id {IDENTIFIER}", progression, device_id="Bad")
    refuses(f"start_profile_id {IDENTIFIER}", progression, start_profile_id="Bad")
    refuses(f"end_profile_id {IDENTIFIER}", progression, end_profile_id="Bad")


# Quotas


def test_reset_modes_and_their_times():
    assert data_limit(reset_mode="daily", reset_times=[30, 10, 10]).reset_times == (10, 30)
    assert quota(reset_mode="daily", reset_times=[30, 10, 10]).reset_times == (10, 30)
    assert time_quota(reset_mode="daily", reset_times=(7, 7)).reset_times == (7,)
    assert group_limit(reset_mode="daily", reset_times=(9, 8)).reset_times == (8, 9)
    data_limit(reset_mode="interval", reset_interval_seconds=60)
    data_limit(reset_mode="interval", reset_interval_seconds=31_536_000)
    refuses("reset_mode is not supported", data_limit, reset_mode="weekly")
    refuses("interval resets must be between 60 seconds and 365 days", data_limit,
            reset_mode="interval", reset_interval_seconds=59)
    refuses("interval resets must be between 60 seconds and 365 days", data_limit,
            reset_mode="interval", reset_interval_seconds=0)
    refuses("reset_interval_seconds must be between 0 and 31536000", data_limit,
            reset_mode="interval", reset_interval_seconds=31_536_001)
    refuses("reset_interval_seconds must be between 0 and 31536000", data_limit,
            reset_mode="interval", reset_interval_seconds=-1)
    refuses("reset_interval_seconds is only valid for interval resets", data_limit,
            reset_mode="manual", reset_interval_seconds=60)
    refuses("reset_interval_seconds is only valid for interval resets", data_limit,
            reset_mode="daily", reset_interval_seconds=60, reset_times=(1,))
    refuses("reset_times must be a list", data_limit, reset_times="12")
    refuses("daily resets require at least one reset time", data_limit, reset_mode="daily")
    refuses("reset_times is only valid for daily resets", data_limit, reset_times=(1,))
    refuses("reset_times is only valid for daily resets", data_limit, reset_mode="interval",
            reset_interval_seconds=60, reset_times=(1,))
    data_limit(reset_mode="daily", reset_times=tuple(range(24)))
    refuses("reset_times must contain no more than 24 times", data_limit, reset_mode="daily",
            reset_times=tuple(range(25)))
    data_limit(reset_mode="daily", reset_times=(0, 1_439))
    refuses("reset_time must be between 0 and 1439", data_limit, reset_mode="daily",
            reset_times=(1_440,))
    refuses("reset_time must be between 0 and 1439", data_limit, reset_mode="daily",
            reset_times=(-1,))


def test_a_data_limit_and_a_group_limit_check_their_flags_and_ids():
    refuses("enabled must be a boolean", data_limit, enabled=1)
    refuses("enabled must be a boolean", group_limit, enabled=0)
    refuses(f"device_id {IDENTIFIER}", data_limit, device_id="Bad")
    refuses(f"profile_id {IDENTIFIER}", data_limit, profile_id="Bad")
    refuses(f"group_id {IDENTIFIER}", group_limit, group_id="Bad")
    refuses(f"profile_id {IDENTIFIER}", group_limit, profile_id="Bad")
    refuses("reset_mode is not supported", group_limit, reset_mode="weekly")
    assert group_limit(used_bytes=5, counter_bytes=6, last_reset_at=7).last_reset_at == 7
    assert data_limit(used_bytes=5, counter_bytes=6, last_reset_at=7).counter_bytes == 6
    assert group_limit(limit_bytes=9).limit_bytes == 9
    assert group_limit(reset_mode="interval", reset_interval_seconds=60
                       ).reset_interval_seconds == 60


def test_a_service_quota_keeps_a_clean_domain_list():
    assert quota(domains=("B.example.com", "b.example.com.")).domains == ("b.example.com",)
    refuses("service quota requires at least one domain", quota, domains=())
    refuses("domains must be a list", quota, domains="a.example.com")
    refuses("domains must contain no more than 100 domains", quota,
            domains=tuple(f"h{n}.example.com" for n in range(101)))
    refuses("enabled must be a boolean", quota, enabled=1)
    refuses(f"service quota id {IDENTIFIER}", quota, id="Bad")
    refuses("service quota name must contain between 1 and 100 characters", quota, name="")
    refuses(f"device_id {IDENTIFIER}", quota, device_id="Bad")
    refuses(f"profile_id {IDENTIFIER}", quota, profile_id="Bad")
    refuses("reset_mode is not supported", quota, reset_mode="weekly")


def test_a_time_quota_keeps_a_clean_domain_list_and_a_valid_window():
    assert time_quota(domains=("B.example.com", "b.example.com.")).domains == ("b.example.com",)
    refuses("time quota requires at least one domain", time_quota, domains=())
    refuses("domains must contain no more than 100 domains", time_quota,
            domains=tuple(f"h{n}.example.com" for n in range(101)))
    refuses("enabled must be a boolean", time_quota, enabled=1)
    refuses(f"time quota id {IDENTIFIER}", time_quota, id="Bad")
    refuses("time quota name must contain between 1 and 100 characters", time_quota, name="")
    refuses(f"device_id {IDENTIFIER}", time_quota, device_id="Bad")
    refuses(f"profile_id {IDENTIFIER}", time_quota, profile_id="Bad")
    refuses("reset_mode is not supported", time_quota, reset_mode="weekly")


def test_a_time_quota_timer_has_both_ends_or_none():
    time_quota()
    time_quota(started_at=1, expires_at=2)
    message = "time quota timer must have both start and expiry times"
    refuses(message, time_quota, started_at=0, expires_at=5)
    refuses(message, time_quota, started_at=5, expires_at=0)
    refuses("time quota expiry must be later than its start", time_quota, started_at=5,
            expires_at=5)
    refuses("time quota expiry must be later than its start", time_quota, started_at=6,
            expires_at=5)
    time_quota(started_at=1, expires_at=61, window_duration_seconds=60)
    refuses("time quota timer exceeds its configured window", time_quota, started_at=1,
            expires_at=62, window_duration_seconds=60)
    refuses("started_at must be between 0 and 9223372036854775807", time_quota,
            started_at=BIG + 1, expires_at=BIG + 1)
    refuses("expires_at must be between 0 and 9223372036854775807", time_quota,
            started_at=1, expires_at=BIG + 1)
    refuses("expires_at must be between 0 and 9223372036854775807", time_quota,
            started_at=1, expires_at=-1)


# Service rules for a browser


def assignment(**f):
    return ServiceAssignment(**{
        "id": "a", "device_id": "d", "name": "A", "domains": ("a.example.com",),
        "profile_id": "p", **f})


def test_a_browser_assignment_normalises_and_refuses_overlaps():
    assert assignment(domains=("A.Example.COM.",)).domains == ("a.example.com",)
    assert assignment(domains=("a.example.com", "A.EXAMPLE.com.")).domains == ("a.example.com",)
    assignment(domains=("a.example.com", "b.example.com"))
    assignment(domains=("a.example.com", "ba.example.com"))
    refuses("service assignment domains overlap", assignment,
            domains=("a.example.com", "b.a.example.com"))
    refuses("service assignment domains overlap", assignment,
            domains=("b.a.example.com", "a.example.com"))
    refuses("service assignment domains overlap", assignment,
            domains=("a.example.com", "c.example.com", "x.a.example.com"))
    refuses("service assignment requires at least one domain", assignment, domains=())
    refuses("browser service domains must have at least two parts such as example.com",
            assignment, domains=("localhost",))
    refuses("domains must be a list", assignment, domains="a.example.com")
    refuses("domains must contain no more than 100 domains", assignment,
            domains=tuple(f"h{n}.example.com" for n in range(101)))
    refuses("enabled must be a boolean", assignment, enabled=1)
    refuses(f"service assignment id {IDENTIFIER}", assignment, id="Bad")
    refuses("service assignment name must contain between 1 and 100 characters", assignment,
            name=" ")
    refuses(f"device_id {IDENTIFIER}", assignment, device_id="Bad")
    refuses(f"profile_id {IDENTIFIER}", assignment, profile_id="Bad")


def service_list(**f):
    return ServiceList(**{"id": "l", "name": "L", "profile_id": "p", **f})


def test_a_service_list_holds_services_and_hostnames():
    lst = service_list(name="  Games  ", hostnames=["A.example.com", "a.example.com."])
    assert lst.name == "Games"
    assert lst.hostnames == ("a.example.com",)
    assert lst.services == ()
    assert isinstance(service_list(hostnames=["a.example.com"]).hostnames, tuple)
    refuses("service list requires at least one service or hostname", service_list)
    refuses("services and hostnames must be lists", service_list, services="x",
            hostnames=("a.example.com",))
    refuses("services and hostnames must be lists", service_list, hostnames="a.example.com")
    refuses("services and hostnames must be lists", service_list, services=("x",),
            hostnames="a.example.com")
    refuses("browser service domains must have at least two parts such as example.com",
            service_list, hostnames=("localhost",))
    refuses("enabled must be a boolean", service_list, hostnames=("a.example.com",), enabled=1)
    refuses(f"service list id {IDENTIFIER}", service_list, id="Bad", hostnames=("a.example.com",))
    refuses("service list name must contain between 1 and 100 characters", service_list,
            name="", hostnames=("a.example.com",))
    refuses(f"profile_id {IDENTIFIER}", service_list, profile_id="Bad",
            hostnames=("a.example.com",))


def test_a_service_list_holds_at_most_one_hundred_hostnames():
    names = [f"h{n}.example.com" for n in range(100)]
    assert len(service_list(hostnames=names).domains) == 100
    refuses("service list must contain no more than 100 hostnames", service_list,
            hostnames=names + ["h100.example.com"])
    assert service_list(hostnames=names + names).hostnames == tuple(names)


def test_a_service_list_takes_each_service_once():
    from jittersim.config.service_catalogue import SERVICE_CATALOGUE

    key = SERVICE_CATALOGUE[0].key
    assert service_list(services=[key]).services == (key,)
    refuses("services must not repeat", service_list, services=(key, key))
    refuses("service is not in the catalogue", service_list, services=("no-such-service",))


# Access rules


def test_the_mac_policy_lists_are_clean_and_limited_to_256():
    policy = MacPolicy(mode="denylist", denylist=("AA-BB-CC-DD-EE-FF", "aa:bb:cc:dd:ee:ff"),
                       allowlist=["11:22:33:44:55:66"])
    assert policy.denylist == ("aa:bb:cc:dd:ee:ff",)
    assert policy.allowlist == ("11:22:33:44:55:66",)
    macs = tuple(f"aa:bb:cc:dd:{n // 256:02x}:{n % 256:02x}" for n in range(257))
    MacPolicy(mode="allowlist", allowlist=macs[:256], denylist=macs[:256])
    message = "MAC lists must contain no more than 256 addresses"
    refuses(message, MacPolicy, allowlist=macs)
    refuses(message, MacPolicy, denylist=macs)
    refuses(message, MacPolicy, allowlist=macs, denylist=macs)
    refuses("allowlist mode requires at least one allowed MAC address", MacPolicy,
            mode="allowlist")
    refuses("allowlist mode requires at least one allowed MAC address", MacPolicy,
            mode="allowlist", denylist=("aa:bb:cc:dd:ee:ff",))
    MacPolicy(mode="denylist")
    MacPolicy(mode="disabled", allowlist=())
    refuses("mac filter mode is not supported", MacPolicy, mode="other")
    refuses("mac_address must contain six hexadecimal pairs", MacPolicy, allowlist=("x",))


def test_the_website_policy_lists_are_clean():
    policy = WebsitePolicy(mode="allow_only", allowed_domains=("A.example.com", "a.example.com."),
                           blocked_domains=("B.example.com.",))
    assert policy.allowed_domains == ("a.example.com",)
    assert policy.blocked_domains == ("b.example.com",)
    refuses("website policy mode is not supported", WebsitePolicy, mode="other")
    refuses("allowed_domains must be a list", WebsitePolicy, allowed_domains="a.com")
    refuses("blocked_domains must be a list", WebsitePolicy, blocked_domains="a.com")
    refuses("allowed_domains must contain no more than 100 domains", WebsitePolicy,
            allowed_domains=tuple(f"h{n}.example.com" for n in range(101)))
    full = tuple(f"h{n}.example.com" for n in range(2000))
    assert WebsitePolicy(blocked_domains=full + full[:5]).blocked_domains == full
    refuses("blocked_domains must contain no more than 2000 domains", WebsitePolicy,
            blocked_domains=full + ("h2000.example.com",))
    # 1600 names of 52 characters are 83200 characters, 1538 of them 79976.
    long_names = tuple(f"{n:04d}{'a' * 36}.example.com" for n in range(1600))
    refuses("blocked_domains must contain no more than 80000 characters in total",
            WebsitePolicy, blocked_domains=long_names)
    assert len(WebsitePolicy(blocked_domains=long_names[:1538]).blocked_domains) == 1538
    refuses("blocked_domains: 'bad_name!.com': domain must contain valid DNS labels",
            WebsitePolicy, blocked_domains=full[:10] + ("bad_name!.com",))
    refuses("blocked_domains: 'a.example.com\\nb.example.com': domain must contain valid DNS labels",
            WebsitePolicy, blocked_domains=("a.example.com\nb.example.com",))


def block(**f):
    return SiteBlock(**{"id": "b", "name": "B", "domains": ("a.example.com",), **f})


def test_a_site_block_takes_one_to_sixty_four_hostnames():
    assert block(domains=("A.Example.com", "a.example.com.")).domains == ("a.example.com",)
    block(domains=tuple(f"h{n}.example.com" for n in range(64)))
    message = "a site block needs between 1 and 64 domains"
    refuses(message, block, domains=tuple(f"h{n}.example.com" for n in range(65)))
    refuses(message, block, domains=())
    refuses("browser service domains must have at least two parts such as example.com", block,
            domains=("localhost",))
    refuses("blocked domains must be a list", block, domains="a.example.com")


def test_a_site_block_names_one_target_and_a_clean_name():
    assert block(name="  Video  ").name == "Video"
    assert block(name="a b").name == "a b"
    assert block(name=" ".join(["x"] * 3)).name == "x x x"
    refuses("site block name contains a control character", block, name="a\x1fb")
    refuses("site block name contains a control character", block, name="a\tb")
    refuses("site block name must contain between 1 and 100 characters", block, name="")
    refuses("site block enabled must be true or false", block, enabled=1)
    assert block(device_id="d").device_id == "d"
    assert block(group_id="g").group_id == "g"
    assert block().device_id is None and block().group_id is None
    refuses("choose a device, a group or everyone for a site block", block, device_id="d",
            group_id="g")
    refuses(f"site block device {IDENTIFIER}", block, device_id="Bad")
    refuses(f"site block group {IDENTIFIER}", block, group_id="Bad")
    refuses(f"site block id {IDENTIFIER}", block, id="Bad")


def test_access_options_are_two_flags_and_a_percent():
    assert AccessOptions(enabled=False, reject_wifi_joins=True, dns_failure_percent=100
                         ).dns_failure_percent == 100
    AccessOptions(dns_failure_percent=0)
    flags = "access options must be true or false"
    refuses(flags, AccessOptions, enabled=1)
    refuses(flags, AccessOptions, reject_wifi_joins=1)
    refuses(flags, AccessOptions, enabled="yes", reject_wifi_joins="no")
    chance = "DNS failure chance must be a whole number from 0 to 100"
    for bad in (-1, 101, True, 1.5, "5", None):
        refuses(chance, AccessOptions, dns_failure_percent=bad)


# Saved state


def test_the_runtime_state_keeps_short_messages():
    states = ("unknown", "applying", "resetting", "applied", "reset", "error")
    for state in states:
        assert runtime(observed_state=state).observed_state == state
    refuses("observed_state is not supported", runtime, observed_state="other")
    for field in ("last_action", "last_result", "last_error"):
        assert getattr(runtime(**{field: "x" * 500}), field) == "x" * 500
        assert getattr(runtime(**{field: None}), field) is None
        assert getattr(runtime(**{field: ""}), field) == ""
        message = f"{field} must be no more than 500 characters"
        refuses(message, runtime, **{field: "x" * 501})
        refuses(message, runtime, **{field: 5})
        refuses(message, runtime, **{field: ["x"]})


def test_saved_settings_check_their_flag_and_the_general_profile():
    assert SavedSettings().general_profile_id is None
    assert SavedSettings(general_profile_id="p").general_profile_id == "p"
    assert SavedSettings(persist_profiles_after_gateway_outage=True
                         ).persist_profiles_after_gateway_outage is True
    refuses("gateway-outage persistence must be true or false", SavedSettings,
            persist_profiles_after_gateway_outage=1)
    refuses(f"general_profile_id {IDENTIFIER}", SavedSettings, general_profile_id="Bad")
