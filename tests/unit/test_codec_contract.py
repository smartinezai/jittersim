"""The plan wire format and every way the decoder refuses a plan (mutation testing, 2026-10-18).

The dashboard sends a plan to the root helper as JSON. The helper trusts nothing in it, so the
decoder checks every field, count and limit. A mutation run showed that the older tests held
only a few of those checks. This file holds the rest in two parts:

* two complete payloads written out by hand pin the field names the two programs agree on, in
  both directions (a rename on one side breaks an upgrade, so it must fail a test);
* one table lists every refusal with the exact message, so that each limit is tested on both
  sides and each check is tested alone (a check that is accidentally joined to another with
  "and" instead of "or" would otherwise pass).
"""

import json
from copy import deepcopy
from dataclasses import replace
from ipaddress import IPv4Address as A

import pytest

from jittersim.config.models import (
    AccessOptions,
    Device,
    Impairment,
    MacPolicy,
    Profile,
    Randomness,
    SiteBlock,
    WebsitePolicy,
)
from jittersim.engine.access import AccessClient, AccessPlan
from jittersim.engine.codec import PlanCodecError, plan_from_payload, plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import (
    EveryoneElseRule,
    GeneralProfileRule,
    NetworkPlan,
    ServiceAssignmentRule,
    ServiceListRule,
    ServiceQuotaRule,
    WebsiteFilter,
)

RANDOM = {"variation_percent": 0, "enabled_probability": 100}


def imp(**changes):
    """The wire form of an Impairment with the given fields changed."""

    value = {
        "delay_ms": 0, "jitter_ms": 0, "loss_percent": 0, "bandwidth_kbit": None,
        "reorder_percent": 0, "duplicate_percent": 0, "corrupt_percent": 0,
        "delay_randomness": dict(RANDOM), "jitter_randomness": dict(RANDOM),
        "loss_randomness": dict(RANDOM), "bandwidth_randomness": dict(RANDOM),
        "reorder_randomness": dict(RANDOM), "duplicate_randomness": dict(RANDOM),
        "corrupt_randomness": dict(RANDOM), "loss_burst_packets": 0,
    }
    value.update(changes)
    return value


SLOW = Profile(
    "slow", "Slow",
    upload=Impairment(delay_ms=300, loss_percent=5, bandwidth_kbit=2000),
    download=Impairment(delay_ms=50, jitter_ms=10),
    homepage_bounce_percent=25,
)
SLOW_UP = imp(delay_ms=300, loss_percent=5, bandwidth_kbit=2000)
SLOW_DOWN = imp(delay_ms=50, jitter_ms=10)

ACCESS_PAYLOAD = {
    "version": 1,
    "options": {"enabled": True, "reject_wifi_joins": True, "dns_failure_percent": 7},
    "mac": {"mode": "denylist", "allowlist": [], "denylist": ["aa:bb:cc:dd:ee:ff"]},
    "websites": {
        "mode": "allow_only", "allowed_domains": ["example.com"],
        "blocked_domains": ["bad.example.org"],
    },
    "blocks": [{
        "id": "video", "name": "Video", "domains": ["video.example.com"],
        "device_id": "alice", "group_id": None, "enabled": True,
    }],
    "clients": [{
        "id": "alice", "address": "192.168.50.10", "mac": "02:00:00:00:50:0a",
        "block_ids": ["video"],
    }],
}


def full_access():
    return AccessPlan(
        options=AccessOptions(enabled=True, reject_wifi_joins=True, dns_failure_percent=7),
        mac=MacPolicy(mode="denylist", denylist=("aa:bb:cc:dd:ee:ff",)),
        websites=WebsitePolicy(
            mode="allow_only", allowed_domains=("example.com",),
            blocked_domains=("bad.example.org",),
        ),
        blocks=(SiteBlock("video", "Video", ("video.example.com",), device_id="alice"),),
        clients=(AccessClient("alice", "192.168.50.10", "02:00:00:00:50:0a", ("video",)),),
    )


def individual_plan():
    """Two devices and every other kind of rule that can sit beside devices."""

    return compile_plan(
        devices=(
            Device("alice", "Alice", "192.168.50.10", "slow"),
            Device("bob", "Bob", "192.168.50.11", "slow"),
        ),
        profiles={"slow": SLOW},
        protected_mac_addresses=("02:00:00:00:00:bb", "02:00:00:00:00:aa"),
        unverified_device_ids=("bob",),
        mac_policy=MacPolicy(
            mode="allowlist", allowlist=("aa:bb:cc:dd:ee:01",), denylist=("aa:bb:cc:dd:ee:02",)
        ),
        global_blocked_addresses=(A("203.0.113.9"), A("203.0.113.8")),
        global_allowed_addresses=(A("203.0.113.7"),),
        website_filters=(
            WebsiteFilter(A("192.168.50.10"), (A("203.0.113.20"),), (A("203.0.113.30"),)),
        ),
        blocked_device_addresses=(A("192.168.50.11"),),
        service_quota_rules=(ServiceQuotaRule(
            "alice-video", "alice", A("192.168.50.10"),
            (A("203.0.113.10"), A("203.0.113.5")), 0, Impairment(delay_ms=200),
            Impairment(loss_percent=5), active=True, homepage_bounce_percent=11,
        ),),
        service_assignment_rules=(ServiceAssignmentRule(
            "bob-news", "bob", A("192.168.50.11"), ("news.example.com",), 0,
            Impairment(delay_ms=210), Impairment(loss_percent=6), homepage_bounce_percent=12,
        ),),
        service_list_rules=(ServiceListRule(
            "games", ("games.example.com", "play.example.net"), 0, Impairment(delay_ms=220),
            Impairment(loss_percent=7), outage_active=True, homepage_bounce_percent=13,
        ),),
        everyone_else=EveryoneElseRule(
            0, Impairment(delay_ms=230), Impairment(loss_percent=8),
            unaffected_macs=("02:00:00:00:00:02", "02:00:00:00:00:01"), blocked=True,
            homepage_bounce_percent=14,
        ),
        access=full_access(),
    )


def queue(device_id, direction, mark, impairment):
    return {"device_id": device_id, "direction": direction, "mark": mark,
            "impairment": impairment}


INDIVIDUAL = {
    "general_profile": None,
    "protected_mac_addresses": ["02:00:00:00:00:aa", "02:00:00:00:00:bb"],
    "unverified_device_ids": ["bob"],
    "access": ACCESS_PAYLOAD,
    "mark_rules": [
        {"device_id": "alice", "ipv4_address": "192.168.50.10", "mark": 1,
         "homepage_bounce_percent": 25},
        {"device_id": "bob", "ipv4_address": "192.168.50.11", "mark": 2,
         "homepage_bounce_percent": 25},
    ],
    "queues": [
        queue("alice", "upload", 1, SLOW_UP),
        queue("alice", "download", 1, SLOW_DOWN),
        queue("bob", "upload", 2, SLOW_UP),
        queue("bob", "download", 2, SLOW_DOWN),
        queue("quota:alice-video", "upload", 3, imp(delay_ms=200)),
        queue("quota:alice-video", "download", 3, imp(loss_percent=5)),
        queue("assignment:bob-news", "upload", 4, imp(delay_ms=210)),
        queue("assignment:bob-news", "download", 4, imp(loss_percent=6)),
        queue("rule:everyone-else", "upload", 5, imp(delay_ms=230)),
        queue("rule:everyone-else", "download", 5, imp(loss_percent=8)),
        queue("servicelist:games", "upload", 6, imp(delay_ms=220)),
        queue("servicelist:games", "download", 6, imp(loss_percent=7)),
    ],
    "mac_policy": {
        "mode": "allowlist", "allowlist": ["aa:bb:cc:dd:ee:01"],
        "denylist": ["aa:bb:cc:dd:ee:02"],
    },
    "global_allowed_addresses": ["203.0.113.7"],
    "global_blocked_addresses": ["203.0.113.8", "203.0.113.9"],
    "website_filters": [{
        "device_ip": "192.168.50.10", "allowed_addresses": ["203.0.113.20"],
        "blocked_addresses": ["203.0.113.30"],
    }],
    "blocked_device_addresses": ["192.168.50.11"],
    "service_quota_rules": [{
        "quota_id": "alice-video", "device_id": "alice", "device_ip": "192.168.50.10",
        "destination_addresses": ["203.0.113.10", "203.0.113.5"], "mark": 3,
        "upload_impairment": imp(delay_ms=200), "download_impairment": imp(loss_percent=5),
        "active": True, "homepage_bounce_percent": 11,
    }],
    "service_assignment_rules": [{
        "assignment_id": "bob-news", "device_id": "bob", "device_ip": "192.168.50.11",
        "domains": ["news.example.com"], "mark": 4,
        "upload_impairment": imp(delay_ms=210), "download_impairment": imp(loss_percent=6),
        "homepage_bounce_percent": 12,
    }],
    "service_list_rules": [{
        "list_id": "games", "domains": ["games.example.com", "play.example.net"], "mark": 6,
        "upload_impairment": imp(delay_ms=220), "download_impairment": imp(loss_percent=7),
        "outage_active": True, "homepage_bounce_percent": 13,
    }],
    "everyone_else": {
        "mark": 5, "upload_impairment": imp(delay_ms=230),
        "download_impairment": imp(loss_percent=8),
        "unaffected_macs": ["02:00:00:00:00:01", "02:00:00:00:00:02"], "blocked": True,
        "homepage_bounce_percent": 14,
    },
    "homepage_policy_version": 1,
}


def general_plan():
    return compile_plan(
        devices=(),
        profiles={"slow": SLOW},
        protected_mac_addresses=("02:00:00:00:00:aa",),
        general_profile=GeneralProfileRule("slow", (A("203.0.113.9"), A("203.0.113.3")), True),
        service_list_rules=(ServiceListRule(
            "games", ("games.example.com",), 0, Impairment(delay_ms=220),
            Impairment(loss_percent=7), homepage_bounce_percent=13,
        ),),
        access=AccessPlan(),
    )


GENERAL = {
    "general_profile": {
        "profile_id": "slow", "allowed_addresses": ["203.0.113.9", "203.0.113.3"],
        "outage_active": True, "homepage_bounce_percent": 25,
    },
    "protected_mac_addresses": ["02:00:00:00:00:aa"],
    "unverified_device_ids": [],
    "access": {
        "version": 1,
        "options": {"enabled": True, "reject_wifi_joins": False, "dns_failure_percent": 0},
        "mac": {"mode": "disabled", "allowlist": [], "denylist": []},
        "websites": {"mode": "unrestricted", "allowed_domains": [], "blocked_domains": []},
        "blocks": [], "clients": [],
    },
    "mark_rules": [],
    "queues": [
        queue("general-profile", "upload", 1, SLOW_UP),
        queue("general-profile", "download", 1, SLOW_DOWN),
        queue("servicelist:games", "upload", 2, imp(delay_ms=220)),
        queue("servicelist:games", "download", 2, imp(loss_percent=7)),
    ],
    "mac_policy": {"mode": "disabled", "allowlist": [], "denylist": []},
    "global_allowed_addresses": None,
    "global_blocked_addresses": [],
    "website_filters": [],
    "blocked_device_addresses": [],
    "service_quota_rules": [],
    "service_assignment_rules": [],
    "service_list_rules": [{
        "list_id": "games", "domains": ["games.example.com"], "mark": 2,
        "upload_impairment": imp(delay_ms=220), "download_impairment": imp(loss_percent=7),
        "outage_active": False, "homepage_bounce_percent": 13,
    }],
    "everyone_else": None,
    "homepage_policy_version": 1,
}


def decode(payload):
    return plan_from_payload(deepcopy(payload))


def wire(plan):
    """The payload as the other program receives it: after JSON."""

    return json.loads(json.dumps(plan_to_payload(plan)))


def refused(payload, message, cause=None):
    with pytest.raises(PlanCodecError) as raised:
        plan_from_payload(deepcopy(payload))
    assert str(raised.value) == message
    if cause is not None:
        assert str(raised.value.__cause__) == cause


# The wire format


def test_a_full_plan_is_written_with_exactly_these_fields():
    assert wire(individual_plan()) == INDIVIDUAL


def test_a_full_payload_is_read_back_into_the_plan():
    assert decode(INDIVIDUAL) == individual_plan()


def test_a_general_profile_plan_is_written_with_exactly_these_fields():
    assert wire(general_plan()) == GENERAL


def test_a_general_profile_payload_is_read_back_into_the_plan():
    assert decode(GENERAL) == general_plan()


OWN = Profile(
    "own", "Own", upload=Impairment(delay_ms=900), download=Impairment(delay_ms=800),
)


def beside_everyone_plan():
    """Everyone runs one profile while two devices opt out and run their own (PR 149)."""

    return compile_plan(
        devices=(
            Device("alice", "Alice", "192.168.50.10", "own"),
            Device("bob", "Bob", "192.168.50.11", "own"),
        ),
        profiles={"slow": SLOW, "own": OWN},
        protected_mac_addresses=("02:00:00:00:00:aa",),
        general_profile=GeneralProfileRule("slow", (A("203.0.113.9"), A("203.0.113.3")), True),
        service_list_rules=(ServiceListRule(
            "games", ("games.example.com",), 0, Impairment(delay_ms=220),
            Impairment(loss_percent=7), homepage_bounce_percent=13,
        ),),
        blocked_device_addresses=(A("192.168.50.11"),),
        access=AccessPlan(),
    )


def beside_everyone_payload():
    """GENERAL with two devices beside Everyone. The list keeps mark 2, the devices follow it."""

    payload = deepcopy(GENERAL)
    payload["mark_rules"] = [
        {"device_id": "alice", "ipv4_address": "192.168.50.10", "mark": 3,
         "homepage_bounce_percent": 0},
        {"device_id": "bob", "ipv4_address": "192.168.50.11", "mark": 4,
         "homepage_bounce_percent": 0},
    ]
    payload["queues"][2:2] = [
        queue("alice", "upload", 3, imp(delay_ms=900)),
        queue("alice", "download", 3, imp(delay_ms=800)),
        queue("bob", "upload", 4, imp(delay_ms=900)),
        queue("bob", "download", 4, imp(delay_ms=800)),
    ]
    payload["blocked_device_addresses"] = ["192.168.50.11"]
    return payload


def test_devices_beside_the_general_profile_are_written_with_exactly_these_fields():
    assert wire(beside_everyone_plan()) == beside_everyone_payload()


def test_devices_beside_the_general_profile_are_read_back_into_the_plan():
    plan = decode(beside_everyone_payload())
    assert plan == beside_everyone_plan()
    assert plan.general_profile.profile_id == "slow"
    assert [(rule.device_id, rule.mark) for rule in plan.mark_rules] == [("alice", 3), ("bob", 4)]
    assert [rule.mark for rule in plan.service_list_rules] == [2]
    assert plan.blocked_device_addresses == (A("192.168.50.11"),)


def test_writing_a_plan_does_not_change_it():
    plan = individual_plan()
    plan_to_payload(plan)
    assert plan == individual_plan()


def test_a_plan_without_a_homepage_policy_has_no_homepage_fields():
    payload = plan_to_payload(replace(individual_plan(), homepage_policy_version=0))
    assert "homepage_policy_version" not in payload
    assert payload["general_profile"] is None
    for name in ("mark_rules", "service_quota_rules", "service_assignment_rules",
                 "service_list_rules"):
        assert all("homepage_bounce_percent" not in rule for rule in payload[name])
    assert "homepage_bounce_percent" not in payload["everyone_else"]
    general = plan_to_payload(replace(general_plan(), homepage_policy_version=0))
    assert "homepage_bounce_percent" not in general["general_profile"]


def test_a_payload_without_homepage_fields_decodes_with_chance_zero():
    payload = deepcopy(INDIVIDUAL)
    del payload["homepage_policy_version"]
    for name in ("mark_rules", "service_quota_rules", "service_assignment_rules",
                 "service_list_rules"):
        for rule in payload[name]:
            del rule["homepage_bounce_percent"]
    del payload["everyone_else"]["homepage_bounce_percent"]
    plan = decode(payload)
    assert plan == replace(
        individual_plan(), homepage_policy_version=0,
        mark_rules=tuple(replace(r, homepage_bounce_percent=0) for r in plan.mark_rules),
        service_quota_rules=tuple(
            replace(r, homepage_bounce_percent=0) for r in plan.service_quota_rules),
        service_assignment_rules=tuple(
            replace(r, homepage_bounce_percent=0) for r in plan.service_assignment_rules),
        service_list_rules=tuple(
            replace(r, homepage_bounce_percent=0) for r in plan.service_list_rules),
        everyone_else=replace(plan.everyone_else, homepage_bounce_percent=0),
    )
    assert plan.homepage_policy_version == 0


def test_the_smallest_payload_decodes_to_an_empty_plan_with_every_default():
    assert decode({"mark_rules": [], "queues": []}) == NetworkPlan(mark_rules=(), queues=())
    assert decode({"mark_rules": [], "queues": [], "homepage_policy_version": 1}) == NetworkPlan(
        mark_rules=(), queues=(), homepage_policy_version=1)


def test_the_chance_of_each_rule_goes_to_its_own_rule():
    payload = deepcopy(INDIVIDUAL)
    payload["mark_rules"][0]["homepage_bounce_percent"] = 0
    payload["mark_rules"][1]["homepage_bounce_percent"] = 100
    plan = decode(payload)
    assert [rule.homepage_bounce_percent for rule in plan.mark_rules] == [0, 100]
    assert plan.service_quota_rules[0].homepage_bounce_percent == 11
    assert plan.service_assignment_rules[0].homepage_bounce_percent == 12
    assert plan.service_list_rules[0].homepage_bounce_percent == 13
    assert plan.everyone_else.homepage_bounce_percent == 14
    assert decode(GENERAL).general_profile.homepage_bounce_percent == 25


def test_a_missing_chance_is_zero_when_the_policy_is_on():
    payload = deepcopy(INDIVIDUAL)
    for name in ("mark_rules", "service_quota_rules", "service_assignment_rules",
                 "service_list_rules"):
        del payload[name][0]["homepage_bounce_percent"]
    del payload["everyone_else"]["homepage_bounce_percent"]
    plan = decode(payload)
    assert plan.mark_rules[0].homepage_bounce_percent == 0
    assert plan.mark_rules[1].homepage_bounce_percent == 25
    assert plan.service_quota_rules[0].homepage_bounce_percent == 0
    assert plan.service_assignment_rules[0].homepage_bounce_percent == 0
    assert plan.service_list_rules[0].homepage_bounce_percent == 0
    assert plan.everyone_else.homepage_bounce_percent == 0
    general = deepcopy(GENERAL)
    del general["general_profile"]["homepage_bounce_percent"]
    assert decode(general).general_profile.homepage_bounce_percent == 0


def test_every_randomness_field_of_an_impairment_survives_the_round_trip():
    fields = ("delay", "jitter", "loss", "bandwidth", "reorder", "duplicate", "corrupt")
    for field in fields:
        impairment = Impairment(**{
            field + "_randomness": Randomness(variation_percent=7.25, enabled_probability=60)
        })
        plan = compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "p"),),
            profiles={"p": Profile("p", "P", upload=impairment, download=impairment)},
        )
        decoded = plan_from_payload(plan_to_payload(plan))
        assert decoded == plan, field
        assert getattr(decoded.queues[0].impairment, field + "_randomness") == Randomness(
            variation_percent=7.25, enabled_probability=60)


def test_a_value_range_survives_the_round_trip():
    impairment = Impairment(
        delay_ms=10,
        delay_randomness=Randomness(mode="range", minimum=5, maximum=50),
        bandwidth_kbit=500,
        bandwidth_randomness=Randomness(mode="range", minimum=100, maximum=900),
    )
    plan = compile_plan(
        devices=(Device("alice", "Alice", "192.168.50.10", "p"),),
        profiles={"p": Profile("p", "P", upload=impairment)},
    )
    assert plan_from_payload(plan_to_payload(plan)) == plan


# Limits that must be accepted exactly at the limit


def devices(count, first=10):
    return tuple(Device(f"d{index}", f"D{index}", f"192.168.50.{first + index}", "slow")
                 for index in range(count))


def round_trip(**rules):
    plan = compile_plan(profiles={"slow": SLOW}, **rules)
    assert plan_from_payload(plan_to_payload(plan)) == plan
    return plan


def test_fifteen_devices_are_accepted():
    assert len(round_trip(devices=devices(15)).mark_rules) == 15


def test_fourteen_devices_and_a_quota_use_the_fifteenth_mark():
    quota = ServiceQuotaRule("q", "d0", A("192.168.50.10"), (A("203.0.113.1"),), 0,
                             Impairment(delay_ms=1), Impairment())
    assert round_trip(devices=devices(14), service_quota_rules=(quota,)
                      ).service_quota_rules[0].mark == 15


def test_fourteen_devices_and_a_browser_rule_use_the_fifteenth_mark():
    rule = ServiceAssignmentRule("a", "d0", A("192.168.50.10"), ("a.example.com",), 0,
                                 Impairment(delay_ms=1), Impairment())
    assert round_trip(devices=devices(14), service_assignment_rules=(rule,)
                      ).service_assignment_rules[0].mark == 15


def test_fourteen_devices_and_everyone_else_use_the_fifteenth_mark():
    rule = EveryoneElseRule(0, Impairment(delay_ms=1), Impairment())
    assert round_trip(devices=devices(14), everyone_else=rule).everyone_else.mark == 15


def test_fourteen_devices_and_a_list_use_the_fifteenth_mark():
    rule = ServiceListRule("l", ("a.example.com",), 0, Impairment(delay_ms=1), Impairment())
    assert round_trip(devices=devices(14), service_list_rules=(rule,)
                      ).service_list_rules[0].mark == 15


def test_a_list_alone_uses_the_first_mark():
    rule = ServiceListRule("l", ("a.example.com",), 0, Impairment(delay_ms=1), Impairment())
    assert round_trip(devices=(), service_list_rules=(rule,)).service_list_rules[0].mark == 1


def test_the_fifteen_marks_can_be_split_over_every_kind_of_rule():
    quota = ServiceQuotaRule("q", "d0", A("192.168.50.10"), (A("203.0.113.1"),), 0,
                             Impairment(delay_ms=1), Impairment())
    rule = ServiceAssignmentRule("a", "d0", A("192.168.50.10"), ("a.example.com",), 0,
                                 Impairment(delay_ms=1), Impairment())
    lists = tuple(
        ServiceListRule(f"l{n}", (f"a.l{n}.example.com",), 0, Impairment(delay_ms=1), Impairment())
        for n in range(10)
    )
    plan = round_trip(
        devices=devices(2), service_quota_rules=(quota,), service_assignment_rules=(rule,),
        everyone_else=EveryoneElseRule(0, Impairment(delay_ms=1), Impairment()),
        service_list_rules=lists,
    )
    assert sorted(queue.mark for queue in plan.queues) == [
        mark for mark in range(1, 16) for _ in range(2)]


def test_two_browser_rules_with_one_hundred_domains_each_are_accepted():
    rules = tuple(
        ServiceAssignmentRule(
            f"r{index}", f"d{index}", A(f"192.168.50.{10 + index}"),
            tuple(f"h{number}.r{index}.example.com" for number in range(100)), 0,
            Impairment(delay_ms=1), Impairment())
        for index in range(2)
    )
    plan = round_trip(devices=devices(2), service_assignment_rules=rules)
    assert [len(rule.domains) for rule in plan.service_assignment_rules] == [100, 100]


def test_four_lists_with_one_hundred_hostnames_each_are_accepted():
    rules = tuple(
        ServiceListRule(
            f"l{index}", tuple(f"h{number}.l{index}.example.com" for number in range(100)), 0,
            Impairment(delay_ms=1), Impairment())
        for index in range(4)
    )
    plan = round_trip(devices=(), service_list_rules=rules)
    assert sum(len(rule.domains) for rule in plan.service_list_rules) == 400


def test_a_list_may_hold_a_hostname_and_one_below_it():
    rule = ServiceListRule("l", ("example.com", "a.example.com"), 0,
                           Impairment(delay_ms=1), Impairment())
    assert round_trip(devices=(), service_list_rules=(rule,)).service_list_rules[0].domains == (
        "example.com", "a.example.com")


def test_two_devices_may_each_have_a_rule_for_the_same_hostname():
    rules = tuple(
        ServiceAssignmentRule(f"r{index}", f"d{index}", A(f"192.168.50.{10 + index}"),
                              ("news.example.com",), 0, Impairment(delay_ms=1), Impairment())
        for index in range(2)
    )
    assert len(round_trip(devices=devices(2), service_assignment_rules=rules
                          ).service_assignment_rules) == 2


def test_two_hundred_fifty_six_protected_and_unaffected_addresses_are_accepted():
    macs = tuple(f"02:00:00:00:{number // 256:02x}:{number % 256:02x}" for number in range(256))
    plan = round_trip(
        devices=devices(1), protected_mac_addresses=macs,
        everyone_else=EveryoneElseRule(0, Impairment(delay_ms=1), Impairment(),
                                       unaffected_macs=macs[::-1]),
        unverified_device_ids=tuple(f"u{number}" for number in range(256)),
    )
    assert len(plan.protected_mac_addresses) == 256
    assert len(plan.everyone_else.unaffected_macs) == 256
    assert len(plan.unverified_device_ids) == 256


def test_four_thousand_ninety_six_general_profile_destinations_are_accepted():
    payload = deepcopy(GENERAL)
    addresses = [str(A(0xCB007100 + number)) for number in range(4096)]
    payload["general_profile"]["allowed_addresses"] = addresses
    assert [str(a) for a in decode(payload).general_profile.allowed_addresses] == addresses


def test_optional_sections_have_these_defaults_when_missing():
    payload = deepcopy(INDIVIDUAL)
    for name in ("mac_policy", "global_allowed_addresses", "global_blocked_addresses",
                 "website_filters", "blocked_device_addresses", "protected_mac_addresses",
                 "unverified_device_ids", "access", "everyone_else", "service_list_rules",
                 "service_assignment_rules", "service_quota_rules", "general_profile"):
        del payload[name]
    payload["queues"] = payload["queues"][:4]
    plan = decode(payload)
    assert plan.mac_policy == MacPolicy()
    assert plan.global_allowed_addresses is None
    assert plan.global_blocked_addresses == ()
    assert plan.website_filters == ()
    assert plan.blocked_device_addresses == ()
    assert plan.protected_mac_addresses == ()
    assert plan.unverified_device_ids == ()
    assert plan.access is None
    assert plan.everyone_else is None
    assert plan.general_profile is None
    assert plan.service_list_rules == plan.service_assignment_rules == ()
    assert plan.service_quota_rules == ()


def test_the_mac_policy_fields_have_their_own_defaults():
    payload = deepcopy(INDIVIDUAL)
    payload["mac_policy"] = {}
    assert decode(payload).mac_policy == MacPolicy()
    payload["mac_policy"] = {"denylist": ["aa:bb:cc:dd:ee:02"]}
    assert decode(payload).mac_policy == MacPolicy(denylist=("aa:bb:cc:dd:ee:02",))
    payload["mac_policy"] = {"mode": "allowlist", "allowlist": ["aa:bb:cc:dd:ee:01"]}
    assert decode(payload).mac_policy == MacPolicy(
        mode="allowlist", allowlist=("aa:bb:cc:dd:ee:01",))


def test_a_website_filter_without_lists_has_an_empty_allowlist_not_none():
    payload = deepcopy(INDIVIDUAL)
    payload["website_filters"] = [{"device_ip": "192.168.50.10"}]
    assert decode(payload).website_filters == (WebsiteFilter(A("192.168.50.10"), (), ()),)
    payload["website_filters"] = [{"device_ip": "192.168.50.10", "allowed_addresses": None}]
    assert decode(payload).website_filters == (WebsiteFilter(A("192.168.50.10"), None, ()),)


def test_a_quota_rule_without_the_active_field_is_inactive():
    payload = deepcopy(INDIVIDUAL)
    del payload["service_quota_rules"][0]["active"]
    assert decode(payload).service_quota_rules[0].active is False


def test_a_list_without_the_outage_field_has_no_outage():
    payload = deepcopy(INDIVIDUAL)
    del payload["service_list_rules"][0]["outage_active"]
    assert decode(payload).service_list_rules[0].outage_active is False


def test_everyone_else_without_the_optional_fields_blocks_nobody_and_spares_nobody():
    payload = deepcopy(INDIVIDUAL)
    del payload["everyone_else"]["blocked"]
    del payload["everyone_else"]["unaffected_macs"]
    decoded = decode(payload).everyone_else
    assert decoded.blocked is False
    assert decoded.unaffected_macs == ()


def test_protected_addresses_are_sorted_and_listed_once():
    payload = deepcopy(INDIVIDUAL)
    payload["protected_mac_addresses"] = [
        "02:00:00:00:00:BB", "02:00:00:00:00:aa", "02:00:00:00:00:bb"]
    assert decode(payload).protected_mac_addresses == (
        "02:00:00:00:00:aa", "02:00:00:00:00:bb")


# Refusals. Each row changes one thing in a good payload.


def edit(path, value):
    """An edit that puts VALUE at PATH (a list of keys and indexes)."""

    def apply(payload):
        target = payload
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return apply


def drop(path):
    def apply(payload):
        target = payload
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
    return apply


def several(*edits):
    def apply(payload):
        for item in edits:
            item(payload)
    return apply


def add_to(path, item):
    def apply(payload):
        target = payload
        for key in path:
            target = target[key]
        target.append(item)
    return apply


def copy_of(path, **changes):
    """Append a copy of the object at PATH with some fields changed."""

    def apply(payload):
        target = payload
        for key in path[:-1]:
            target = target[key]
        item = deepcopy(target[path[-1]])
        item.update(changes)
        target.append(item)
    return apply


def mark_rule(index, mark):
    return {"device_id": f"d{index}", "ipv4_address": f"192.168.50.{10 + index}", "mark": mark}


def many_quotas(count):
    def apply(payload):
        payload["service_quota_rules"] = [
            dict(payload["service_quota_rules"][0], quota_id=f"q{number}", mark=3 + number)
            for number in range(count)]
    return apply


def lists_of(domain_counts):
    def apply(payload):
        payload["service_list_rules"] = [
            {"list_id": f"l{index}", "mark": 6 + index,
             "domains": [f"h{number}.l{index}.example.com" for number in range(count)],
             "upload_impairment": imp(), "download_impairment": imp()}
            for index, count in enumerate(domain_counts)]
    return apply


def hostnames(prefix, count):
    return [f"h{number}.{prefix}.example.com" for number in range(count)]


MARKS = ["mark_rules"]
QUOTA = ["service_quota_rules", 0]
ASSIGNMENT = ["service_assignment_rules", 0]
LIST = ["service_list_rules", 0]
EVERYONE = ["everyone_else"]
QUEUES = ["queues"]

PLAN_REFUSALS = [
    # the plan and its two lists
    ("plan is a list", None, "plan must be an object"),
    ("no mark rules", drop(MARKS), "mark_rules must be an array"),
    ("mark rules are an object", edit(MARKS, {}), "mark_rules must be an array"),
    ("no queues", drop(QUEUES), "queues must be an array"),
    ("queues are an object", edit(QUEUES, {}), "queues must be an array"),
    ("sixteen devices", edit(MARKS, [mark_rule(n, 1 + n % 15) for n in range(16)]),
     "a plan cannot contain more than 15 devices"),
    # mark rules
    ("mark rule is text", edit(MARKS + [0], "alice"), "mark_rules[0] must be an object"),
    ("mark rule has no device", drop(MARKS + [0, "device_id"]), "mark_rules[0] is invalid"),
    ("mark rule has no address", drop(MARKS + [0, "ipv4_address"]), "mark_rules[0] is invalid"),
    ("mark rule has no mark", drop(MARKS + [0, "mark"]), "mark_rules[0] is invalid"),
    ("mark rule has a public address", edit(MARKS + [0, "ipv4_address"], "8.8.8.8"),
     "mark_rules[0] is invalid"),
    ("mark rule has a bad id", edit(MARKS + [0, "device_id"], "Bad Id"),
     "mark_rules[0] is invalid"),
    ("mark is text", edit(MARKS + [0, "mark"], "1"), "mark_rules[0].mark must be an integer"),
    ("mark is true", edit(MARKS + [0, "mark"], True), "mark_rules[0].mark must be an integer"),
    ("mark is a float", edit(MARKS + [0, "mark"], 1.0),
     "mark_rules[0].mark must be an integer"),
    ("mark is zero", edit(MARKS + [0, "mark"], 0),
     "mark_rules[0].mark is outside the safe range"),
    ("mark is sixteen", edit(MARKS + [0, "mark"], 16),
     "mark_rules[0].mark is outside the safe range"),
    ("device twice", edit(MARKS + [1, "device_id"], "alice"),
     "duplicate mark rule for device alice"),
    ("mark twice", edit(MARKS + [1, "mark"], 1), "duplicate mark value 1"),
    ("address twice", edit(MARKS + [1, "ipv4_address"], "192.168.50.10"),
     "duplicate IPv4 address 192.168.50.10"),
    # service quota rules
    ("quotas are an object", edit(["service_quota_rules"], {}),
     "service_quota_rules must be an array"),
    ("quotas are null", edit(["service_quota_rules"], None),
     "service_quota_rules must be an array"),
    ("sixteen quotas", many_quotas(16), "a plan cannot contain more than 15 service quotas"),
    ("fifteen quotas", many_quotas(15), "service_quota_rules[13].mark is outside the safe range"),
    ("quota is text", edit(QUOTA, "x"), "service_quota_rules[0] must be an object"),
    ("quota has no id", drop(QUOTA + ["quota_id"]), "service_quota_rules[0] is invalid"),
    ("quota has no device", drop(QUOTA + ["device_id"]), "service_quota_rules[0] is invalid"),
    ("quota has a bad address", edit(QUOTA + ["device_ip"], "x"),
     "service_quota_rules[0] is invalid"),
    ("quota has no destinations key", drop(QUOTA + ["destination_addresses"]),
     "service_quota_rules[0] is invalid"),
    ("quota has a bad destination", edit(QUOTA + ["destination_addresses"], ["x"]),
     "service_quota_rules[0] is invalid"),
    ("quota has no mark", drop(QUOTA + ["mark"]), "service_quota_rules[0] is invalid"),
    ("quota has no upload impairment", drop(QUOTA + ["upload_impairment"]),
     "service_quota_rules[0] is invalid"),
    ("quota has no download impairment", drop(QUOTA + ["download_impairment"]),
     "service_quota_rules[0] is invalid"),
    ("quota id is bad", edit(QUOTA + ["quota_id"], "Bad ID"),
     "service_quota_rules[0].quota_id is invalid"),
    ("quota id is a number", edit(QUOTA + ["quota_id"], 5),
     "service_quota_rules[0].quota_id is invalid"),
    ("quota device is unknown", edit(QUOTA + ["device_id"], "zed"),
     "service_quota_rules[0] references an unknown device"),
    ("quota device is a number", edit(QUOTA + ["device_id"], 5),
     "service_quota_rules[0] references an unknown device"),
    ("quota device is a list", edit(QUOTA + ["device_id"], ["alice"]),
     "service_quota_rules[0] references an unknown device"),
    ("quota has the wrong address", edit(QUOTA + ["device_ip"], "192.168.50.11"),
     "service_quota_rules[0] has the wrong device address"),
    ("quota has no destination", edit(QUOTA + ["destination_addresses"], []),
     "service_quota_rules[0] has no destinations"),
    ("quota active is text", edit(QUOTA + ["active"], "yes"),
     "service_quota_rules[0].active is invalid"),
    ("quota active is a number", edit(QUOTA + ["active"], 1),
     "service_quota_rules[0].active is invalid"),
    ("quota mark is a device mark", edit(QUOTA + ["mark"], 2),
     "service_quota_rules[0].mark is outside the safe range"),
    ("quota mark is sixteen", edit(QUOTA + ["mark"], 16),
     "service_quota_rules[0].mark is outside the safe range"),
    ("quota id twice", copy_of(["service_quota_rules", 0], mark=4),
     "duplicate service quota mark 4"),
    ("quota mark twice", copy_of(["service_quota_rules", 0], quota_id="other"),
     "duplicate service quota mark 3"),
    ("quota mark is another device's", several(
        edit(MARKS + [1, "mark"], 10), edit(QUOTA + ["mark"], 10)),
     "duplicate service quota mark 10"),
    # service assignment rules
    ("assignments are an object", edit(["service_assignment_rules"], {}),
     "service_assignment_rules must be an array"),
    ("assignment is text", edit(ASSIGNMENT, "x"), "service assignment must be an object"),
    ("assignment has no id", drop(ASSIGNMENT + ["assignment_id"]),
     "service_assignment_rules[0] is invalid"),
    ("assignment has no domains", drop(ASSIGNMENT + ["domains"]), "domains must be an array"),
    ("assignment domains are text", edit(ASSIGNMENT + ["domains"], "a.example.com"),
     "domains must be an array"),
    ("assignment has a short name", edit(ASSIGNMENT + ["domains"], ["localhost"]),
     "service_assignment_rules[0] is invalid"),
    ("assignment mark is text", edit(ASSIGNMENT + ["mark"], "4"),
     "service_assignment_rules[0] is invalid"),
    ("assignment has a bad impairment", edit(ASSIGNMENT + ["upload_impairment"], {"x": 1}),
     "service_assignment_rules[0] is invalid"),
    ("assignment id is bad", edit(ASSIGNMENT + ["assignment_id"], "Bad ID"),
     "service_assignment_rules[0].assignment_id is invalid"),
    ("assignment id is a number", edit(ASSIGNMENT + ["assignment_id"], 7),
     "service_assignment_rules[0].assignment_id is invalid"),
    ("assignment device is unknown", edit(ASSIGNMENT + ["device_id"], "zed"),
     "service_assignment_rules[0] references an unknown device"),
    ("assignment has the wrong address", edit(ASSIGNMENT + ["device_ip"], "192.168.50.10"),
     "service_assignment_rules[0] references an unknown device"),
    ("assignment has no domain", edit(ASSIGNMENT + ["domains"], []),
     "service_assignment_rules[0].domains is invalid"),
    ("assignment has a domain twice", edit(ASSIGNMENT + ["domains"], ["a.example.com"] * 2),
     "service_assignment_rules[0].domains is invalid"),
    ("assignment has 101 domains", edit(ASSIGNMENT + ["domains"], hostnames("a", 101)),
     "service_assignment_rules[0].domains is invalid"),
    ("assignment mark is a quota mark", edit(ASSIGNMENT + ["mark"], 3),
     "service_assignment_rules[0].mark is outside the safe range"),
    ("assignment mark is sixteen", edit(ASSIGNMENT + ["mark"], 16),
     "service_assignment_rules[0].mark is outside the safe range"),
    ("assignment id twice", copy_of(["service_assignment_rules", 0], domains=["b.example.org"],
                                    mark=5),
     "duplicate service assignment mark 5"),
    ("assignment mark twice", copy_of(["service_assignment_rules", 0], assignment_id="other",
                                      domains=["b.example.org"]),
     "duplicate service assignment mark 4"),
    ("assignment mark is a device mark", several(
        edit(MARKS + [0, "mark"], 9), edit(ASSIGNMENT + ["mark"], 9)),
     "duplicate service assignment mark 9"),
    ("assignment mark is a quota's", several(
        edit(QUOTA + ["mark"], 8), edit(ASSIGNMENT + ["mark"], 8)),
     "duplicate service assignment mark 8"),
    ("assignment domains are nested", edit(ASSIGNMENT + ["domains"],
                                           ["a.example.com", "b.a.example.com"]),
     "overlapping service assignment domains on bob"),
    ("assignment domains are nested the other way", edit(ASSIGNMENT + ["domains"],
                                                        ["b.a.example.com", "a.example.com"]),
     "overlapping service assignment domains on bob"),
    ("assignments share a domain", copy_of(["service_assignment_rules", 0],
                                           assignment_id="other", mark=7),
     "overlapping service assignment domains on bob"),
    ("assignments are nested", copy_of(["service_assignment_rules", 0], assignment_id="other",
                                       mark=7, domains=["x.news.example.com"]),
     "overlapping service assignment domains on bob"),
    ("too many shaped classes with assignments", several(
        edit(MARKS, [mark_rule(n, 1 + n) for n in range(14)]),
        edit(["service_quota_rules"], [dict(
            INDIVIDUAL["service_quota_rules"][0], device_id="d0", device_ip="192.168.50.10",
            mark=15)]),
        edit(["service_assignment_rules"], [
            dict(INDIVIDUAL["service_assignment_rules"][0], device_id="d1",
                 device_ip="192.168.50.11", assignment_id=f"a{n}", mark=15)
            for n in range(2)])),
     "a plan has too many shaped traffic classes"),
    ("too many shaped classes with quotas", several(
        edit(MARKS, [mark_rule(n, 1 + n) for n in range(10)]),
        edit(["service_quota_rules"], [dict(
            INDIVIDUAL["service_quota_rules"][0], device_id="d0", device_ip="192.168.50.10",
            quota_id=f"q{n}", mark=11 + n) for n in range(5)]),
        edit(["service_assignment_rules"], [dict(
            INDIVIDUAL["service_assignment_rules"][0], device_id="d1",
            device_ip="192.168.50.11", assignment_id=f"a{n}", mark=16) for n in range(2)])),
     "a plan has too many shaped traffic classes"),
    ("201 browser domains", several(
        edit(ASSIGNMENT + ["domains"], hostnames("a", 101)),
        copy_of(["service_assignment_rules", 0], domains=hostnames("b", 100))),
     "a plan has too many browser service domains"),
    # everyone else
    ("everyone else is text", edit(EVERYONE, "x"), "everyone_else must be an object"),
    ("everyone else has no mark", drop(EVERYONE + ["mark"]), "everyone_else is invalid"),
    ("everyone else mark is text", edit(EVERYONE + ["mark"], "5"), "everyone_else is invalid"),
    ("everyone else has no upload", drop(EVERYONE + ["upload_impairment"]),
     "everyone_else is invalid"),
    ("everyone else has no download", drop(EVERYONE + ["download_impairment"]),
     "everyone_else is invalid"),
    ("everyone else macs are text", edit(EVERYONE + ["unaffected_macs"], "02:00:00:00:00:01"),
     "everyone_else is invalid"),
    ("everyone else has a bad mac", edit(EVERYONE + ["unaffected_macs"], ["zz"]),
     "everyone_else is invalid"),
    ("everyone else blocked is text", edit(EVERYONE + ["blocked"], "yes"),
     "everyone_else.blocked is invalid"),
    ("everyone else blocked is a number", edit(EVERYONE + ["blocked"], 1),
     "everyone_else.blocked is invalid"),
    ("everyone else has 257 macs", edit(EVERYONE + ["unaffected_macs"], [
        f"02:00:00:00:{number // 256:02x}:{number % 256:02x}" for number in range(257)]),
     "everyone_else.unaffected_macs is invalid"),
    ("everyone else lists a mac twice", edit(EVERYONE + ["unaffected_macs"],
                                             ["02:00:00:00:00:01"] * 2),
     "everyone_else.unaffected_macs is invalid"),
    ("everyone else mark is a browser mark", edit(EVERYONE + ["mark"], 4),
     "everyone_else.mark is outside the safe range"),
    ("everyone else mark is sixteen", edit(EVERYONE + ["mark"], 16),
     "everyone_else.mark is outside the safe range"),
    ("everyone else mark is the number of rules", several(
        edit(ASSIGNMENT + ["mark"], 9), edit(EVERYONE + ["mark"], 4)),
     "everyone_else.mark is outside the safe range"),
    ("everyone else mark is below the number of rules", several(
        edit(QUOTA + ["mark"], 8), edit(EVERYONE + ["mark"], 3)),
     "everyone_else.mark is outside the safe range"),
    ("everyone else mark is a device mark", several(
        edit(MARKS + [0, "mark"], 12), edit(EVERYONE + ["mark"], 12)),
     "everyone_else.mark is outside the safe range"),
    ("everyone else mark is a service mark", several(
        edit(ASSIGNMENT + ["mark"], 12), edit(EVERYONE + ["mark"], 12)),
     "everyone_else.mark is outside the safe range"),
    # service lists
    ("lists are an object", edit(["service_list_rules"], {}),
     "service_list_rules must be an array"),
    ("list is text", edit(LIST, "x"), "service_list_rules[0] must be an object"),
    ("list has no id", drop(LIST + ["list_id"]), "service_list_rules[0] is invalid"),
    ("list has no domains", drop(LIST + ["domains"]), "service_list_rules[0] is invalid"),
    ("list has no mark", drop(LIST + ["mark"]), "service_list_rules[0] is invalid"),
    ("list has no upload", drop(LIST + ["upload_impairment"]),
     "service_list_rules[0] is invalid"),
    ("list has a short name", edit(LIST + ["domains"], ["localhost"]),
     "service_list_rules[0] is invalid"),
    ("list domains are text", edit(LIST + ["domains"], "a.example.com"),
     "domains must be an array"),
    ("list mark is text", edit(LIST + ["mark"], "6"),
     "service_list_rules[0].mark must be an integer"),
    ("list has a bad impairment", edit(LIST + ["download_impairment"], {"x": 1}),
     "impairment is invalid"),
    ("list has an impairment that is not an object", edit(LIST + ["download_impairment"], 5),
     "impairment must be an object"),
    ("list id is bad", edit(LIST + ["list_id"], "Bad ID"),
     "service_list_rules[0].list_id is invalid"),
    ("list id is a number", edit(LIST + ["list_id"], 5),
     "service_list_rules[0].list_id is invalid"),
    ("list has no domain", edit(LIST + ["domains"], []),
     "service_list_rules[0].domains is invalid"),
    ("list has a domain twice", edit(LIST + ["domains"], ["a.example.com"] * 2),
     "service_list_rules[0].domains is invalid"),
    ("list has 101 domains", edit(LIST + ["domains"], hostnames("a", 101)),
     "service_list_rules[0].domains is invalid"),
    ("list mark is zero", edit(LIST + ["mark"], 0),
     "service_list_rules[0].mark is outside the safe range"),
    ("list mark is sixteen", edit(LIST + ["mark"], 16),
     "service_list_rules[0].mark is outside the safe range"),
    ("list outage is text", edit(LIST + ["outage_active"], "no"),
     "service_list_rules[0].outage_active is invalid"),
    ("list outage is a number", edit(LIST + ["outage_active"], 0),
     "service_list_rules[0].outage_active is invalid"),
    ("list id twice", copy_of(["service_list_rules", 0], mark=7),
     "duplicate service list mark 7"),
    ("list mark twice", copy_of(["service_list_rules", 0], list_id="other",
                                domains=["a.example.org"]),
     "duplicate service list mark 6"),
    ("list mark is a device mark", several(
        edit(MARKS + [0, "mark"], 7), copy_of(["service_list_rules", 0], list_id="other",
                                              domains=["a.example.org"], mark=7)),
     "duplicate service list mark 7"),
    ("list mark is everyone else's", copy_of(["service_list_rules", 0], list_id="other",
                                             domains=["a.example.org"], mark=5),
     "duplicate service list mark 5"),
    ("lists share a hostname", copy_of(["service_list_rules", 0], list_id="other", mark=7,
                                       domains=["games.example.com"]),
     "service list hostnames overlap on games.example.com"),
    ("lists nest a hostname below", copy_of(["service_list_rules", 0], list_id="other", mark=7,
                                            domains=["x.games.example.com"]),
     "service list hostnames overlap on x.games.example.com"),
    ("lists nest a hostname above", copy_of(["service_list_rules", 0], list_id="other", mark=7,
                                            domains=["example.net"]),
     "service list hostnames overlap on example.net"),
    ("sixteen shaped classes with lists", lists_of([1] * 11),
     "a plan has too many shaped traffic classes"),
    ("401 list hostnames", lists_of([100, 100, 100, 100, 1]),
     "a plan has too many service list hostnames"),
    # queues
    ("a queue is missing", edit(QUEUES, INDIVIDUAL["queues"][:-1]),
     "a plan must contain two queues per device or service"),
    ("a queue is extra", add_to(QUEUES, queue("alice", "upload", 1, imp())),
     "a plan must contain two queues per device or service"),
    ("queue is text", edit(QUEUES + [0], "x"), "queues[0] must be an object"),
    ("queue has no device", drop(QUEUES + [0, "device_id"]), "queues[0] is invalid"),
    ("queue has no direction", drop(QUEUES + [0, "direction"]), "queues[0] is invalid"),
    ("queue has no mark", drop(QUEUES + [0, "mark"]), "queues[0] is invalid"),
    ("queue has no impairment", drop(QUEUES + [0, "impairment"]), "queues[0] is invalid"),
    ("queue goes sideways", edit(QUEUES + [0, "direction"], "sideways"), "queues[0] is invalid"),
    ("queue mark is text", edit(QUEUES + [0, "mark"], "1"), "queues[0] is invalid"),
    ("queue has a bad impairment", edit(QUEUES + [0, "impairment"], {"x": 1}),
     "queues[0] is invalid"),
    ("queue names an unknown class", edit(QUEUES + [0, "device_id"], "zed"),
     "queues[0] references an unknown traffic class"),
    ("queue has the wrong mark", edit(QUEUES + [0, "mark"], 2), "queues[0] has the wrong mark"),
    ("queue repeats a direction", edit(QUEUES + [1, "direction"], "upload"),
     "duplicate upload queue for device alice"),
    ("quota upload differs", edit(QUEUES + [4, "impairment"], imp(delay_ms=1)),
     "service queue impairment does not match its rule"),
    ("quota download differs", edit(QUEUES + [5, "impairment"], imp(loss_percent=1)),
     "service queue impairment does not match its rule"),
    ("browser upload differs", edit(QUEUES + [6, "impairment"], imp(delay_ms=1)),
     "browser service queue impairment does not match its rule"),
    ("browser download differs", edit(QUEUES + [7, "impairment"], imp(loss_percent=1)),
     "browser service queue impairment does not match its rule"),
    ("everyone else upload differs", edit(QUEUES + [8, "impairment"], imp(delay_ms=1)),
     "everyone-else queue impairment does not match its rule"),
    ("everyone else download differs", edit(QUEUES + [9, "impairment"], imp(loss_percent=1)),
     "everyone-else queue impairment does not match its rule"),
    ("list upload differs", edit(QUEUES + [10, "impairment"], imp(delay_ms=1)),
     "service list queue impairment does not match its rule"),
    ("list download differs", edit(QUEUES + [11, "impairment"], imp(loss_percent=1)),
     "service list queue impairment does not match its rule"),
    # network policy
    ("mac policy is text", edit(["mac_policy"], "x"), "mac_policy must be an object"),
    ("mac policy mode is unknown", edit(["mac_policy", "mode"], "maybe"),
     "network policy is invalid"),
    ("mac policy has a bad address", edit(["mac_policy", "denylist"], ["zz"]),
     "network policy is invalid"),
    ("blocked addresses are text", edit(["global_blocked_addresses"], "203.0.113.8"),
     "global_blocked_addresses must be an array"),
    ("blocked address is bad", edit(["global_blocked_addresses"], ["x"]),
     "network policy is invalid"),
    ("allowed addresses are text", edit(["global_allowed_addresses"], "203.0.113.7"),
     "global_allowed_addresses must be an array"),
    ("allowed address is bad", edit(["global_allowed_addresses"], ["x"]),
     "network policy is invalid"),
    ("filters are text", edit(["website_filters"], "x"), "website_filters must be an array"),
    ("filter is text", edit(["website_filters", 0], "x"), "website_filters[0] must be an object"),
    ("filter has no device", drop(["website_filters", 0, "device_ip"]),
     "network policy is invalid"),
    ("filter allowlist is text", edit(["website_filters", 0, "allowed_addresses"], "x"),
     "website_filters[0].allowed_addresses must be an array"),
    ("filter blocklist is text", edit(["website_filters", 0, "blocked_addresses"], "x"),
     "website_filters[0].blocked_addresses must be an array"),
    ("filter address is bad", edit(["website_filters", 0, "blocked_addresses"], ["x"]),
     "network policy is invalid"),
    ("filter device is unknown", edit(["website_filters", 0, "device_ip"], "192.168.50.99"),
     "website_filters[0] references an unknown device"),
    ("filter twice", copy_of(["website_filters", 0]),
     "duplicate website filter for device address 192.168.50.10"),
    ("blocked devices are text", edit(["blocked_device_addresses"], "x"),
     "blocked_device_addresses must be an array"),
    ("blocked device is bad", edit(["blocked_device_addresses"], ["x"]),
     "network policy is invalid"),
    # protected addresses, unverified devices and the access policy
    ("protected addresses are text", edit(["protected_mac_addresses"], "x"),
     "protected_mac_addresses must be an array"),
    ("257 protected addresses", edit(["protected_mac_addresses"], [
        f"02:00:00:00:{number // 256:02x}:{number % 256:02x}" for number in range(257)]),
     "too many protected MAC addresses"),
    ("protected address is bad", edit(["protected_mac_addresses"], ["zz"]),
     "MAC address is invalid"),
    ("protected address is multicast", edit(["protected_mac_addresses"], ["01:00:00:00:00:01"]),
     "MAC address must identify one device"),
    ("protected address is zero", edit(["protected_mac_addresses"], ["00:00:00:00:00:00"]),
     "MAC address must identify one device"),
    ("unverified ids are text", edit(["unverified_device_ids"], "bob"),
     "unverified_device_ids must be an array"),
    ("257 unverified ids", edit(["unverified_device_ids"], [f"u{n}" for n in range(257)]),
     "unverified device IDs are invalid"),
    ("unverified id is bad", edit(["unverified_device_ids"], ["Bad"]),
     "unverified device IDs are invalid"),
    ("unverified id is a number", edit(["unverified_device_ids"], [5]),
     "unverified device IDs are invalid"),
    ("unverified id is empty", edit(["unverified_device_ids"], [""]),
     "unverified device IDs are invalid"),
    ("access is from the future", edit(["access", "version"], 2), "access policy is invalid"),
    ("access is text", edit(["access"], "x"), "access policy is invalid"),
    # the homepage policy
    ("homepage policy is two", edit(["homepage_policy_version"], 2),
     "unsupported homepage policy version"),
    ("homepage policy is true", edit(["homepage_policy_version"], True),
     "unsupported homepage policy version"),
    ("homepage policy is text", edit(["homepage_policy_version"], "1"),
     "unsupported homepage policy version"),
    ("homepage policy is minus one", edit(["homepage_policy_version"], -1),
     "unsupported homepage policy version"),
]

CHANCE_PLACES = [MARKS + [0], MARKS + [1], QUOTA, ASSIGNMENT, LIST, EVERYONE]
for place in CHANCE_PLACES:
    name = "/".join(str(part) for part in place)
    for label, value in (("minus one", -1), ("101", 101), ("text", "5"), ("a float", 5.0),
                         ("true", True)):
        PLAN_REFUSALS.append((f"chance of {name} is {label}",
                              edit(place + ["homepage_bounce_percent"], value),
                              "invalid homepage chance"))
    PLAN_REFUSALS.append((f"chance of {name} without the policy", several(
        drop(["homepage_policy_version"])), "invalid homepage chance"))


@pytest.mark.parametrize(
    "case", PLAN_REFUSALS, ids=[case[0] for case in PLAN_REFUSALS])
def test_the_decoder_refuses(case):
    _, change, message = case
    payload = deepcopy(INDIVIDUAL)
    if change is None:
        payload = []
    else:
        change(payload)
    refused(payload, message)


def test_a_payload_that_cannot_be_decoded_keeps_the_reason_of_the_first_failure():
    for path, cause in (
        (QUOTA + ["destination_addresses"],
         "service_quota_rules[0].destination_addresses must be an array"),
        (QUOTA + ["mark"], "service_quota_rules[0].mark must be an integer"),
        (ASSIGNMENT + ["mark"], "service assignment mark must be an integer"),
        (EVERYONE + ["mark"], "everyone_else.mark must be an integer"),
        (EVERYONE + ["unaffected_macs"], "everyone_else.unaffected_macs must be an array"),
        (QUEUES + [0, "mark"], "queues[0].mark must be an integer"),
    ):
        payload = deepcopy(INDIVIDUAL)
        edit(path, "x")(payload)
        with pytest.raises(PlanCodecError) as raised:
            plan_from_payload(payload)
        assert str(raised.value.__cause__) == cause, path


def test_an_impairment_the_model_refuses_is_reported_as_invalid():
    payload = deepcopy(INDIVIDUAL)
    payload["queues"][0]["impairment"]["delay_ms"] = -1
    refused(payload, "queues[0] is invalid", cause="impairment is invalid")
    payload = deepcopy(INDIVIDUAL)
    payload["queues"][0]["impairment"]["delay_randomness"] = "often"
    refused(payload, "queues[0] is invalid", cause="impairment is invalid")


GENERAL_REFUSALS = [
    ("general profile is text", edit(["general_profile"], "x"),
     "general_profile must be an object"),
    ("general profile lacks a field", drop(["general_profile", "outage_active"]),
     "the general profile fields are invalid"),
    ("general profile lacks the destinations", drop(["general_profile", "allowed_addresses"]),
     "the general profile fields are invalid"),
    ("general profile lacks the id", drop(["general_profile", "profile_id"]),
     "the general profile fields are invalid"),
    ("general profile has another field", edit(["general_profile", "extra"], 1),
     "the general profile fields are invalid"),
    ("general profile id is bad", edit(["general_profile", "profile_id"], "Bad ID"),
     "the general profile ID is invalid"),
    ("general profile id is a number", edit(["general_profile", "profile_id"], 5),
     "the general profile ID is invalid"),
    ("general profile outage is text", edit(["general_profile", "outage_active"], "yes"),
     "the general profile outage state is invalid"),
    ("general profile outage is a number", edit(["general_profile", "outage_active"], 1),
     "the general profile outage state is invalid"),
    ("general destinations are text", edit(["general_profile", "allowed_addresses"], "x"),
     "general profile destinations must be an array"),
    ("4097 general destinations", edit(["general_profile", "allowed_addresses"], [
        str(A(0xCB007100 + number)) for number in range(4097)]),
     "too many general profile destinations"),
    ("a destination is a number", edit(["general_profile", "allowed_addresses"], [5]),
     "general profile destinations must be IPv4 strings"),
    ("a destination is bad", edit(["general_profile", "allowed_addresses"], ["999.1.1.1"]),
     "general profile destinations are invalid"),
    ("general profile beside a device that takes mark 1", add_to(MARKS, {
        "device_id": "alice", "ipv4_address": "192.168.50.10", "mark": 1}),
     "mark 1 belongs to the general profile"),
    ("general profile beside a device with the queue identity of the general profile",
     add_to(MARKS, {"device_id": "general-profile", "ipv4_address": "192.168.50.10", "mark": 3}),
     "a device cannot use the general profile's queue identity"),
    ("general profile beside everyone else", edit(EVERYONE, {
        "mark": 3, "upload_impairment": imp(), "download_impairment": imp()}),
     "a profile set for Everyone already covers everyone else"),
    ("general profile beside a website filter", edit(["website_filters"], [
        {"device_ip": "192.168.50.10"}]),
     "website_filters[0] references an unknown device"),
]


@pytest.mark.parametrize(
    "case", GENERAL_REFUSALS, ids=[case[0] for case in GENERAL_REFUSALS])
def test_the_decoder_refuses_a_general_profile_plan(case):
    _, change, message = case
    payload = deepcopy(GENERAL)
    change(payload)
    refused(payload, message)


def test_a_general_profile_beside_devices_that_take_mark_1_is_refused():
    # INDIVIDUAL gives mark 1 to alice, and mark 1 is the queue of Everyone.
    payload = deepcopy(INDIVIDUAL)
    payload["general_profile"] = deepcopy(GENERAL["general_profile"])
    refused(payload, "mark 1 belongs to the general profile")


def test_general_profile_destinations_may_be_empty_or_missing_as_none():
    payload = deepcopy(GENERAL)
    payload["general_profile"]["allowed_addresses"] = []
    assert decode(payload).general_profile.allowed_addresses == ()
    payload["general_profile"]["allowed_addresses"] = None
    assert decode(payload).general_profile.allowed_addresses is None


def test_the_general_profile_takes_the_first_mark():
    payload = deepcopy(GENERAL)
    payload["queues"][0]["mark"] = 2
    payload["queues"][1]["mark"] = 2
    refused(payload, "queues[0] has the wrong mark")
    payload = deepcopy(GENERAL)
    payload["service_list_rules"][0]["mark"] = 1
    refused(payload, "duplicate service list mark 1")


def test_the_decoder_never_raises_anything_but_its_own_error_for_odd_values():
    odd = [None, 0, 1, -1, 1.5, True, "", "x", [], {}, [1], {"a": 1}, [[]], [{}]]
    for name in INDIVIDUAL:
        for value in odd:
            payload = deepcopy(INDIVIDUAL)
            payload[name] = value
            try:
                plan_from_payload(payload)
            except PlanCodecError:
                pass
