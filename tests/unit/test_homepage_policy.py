import json
from dataclasses import replace
from ipaddress import IPv4Address

import pytest

from jittersim.config.models import Device, Profile, ValidationError
from jittersim.config.storage import _serialize_profile_settings
from jittersim.engine.codec import PlanCodecError, plan_from_payload, plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import GeneralProfileRule, ServiceListRule
from jittersim.networking.homepage import policy_chance


@pytest.mark.parametrize("value", [-1, 101, True, 0.5, "10"])
def test_chance_is_a_bounded_integer(value):
    with pytest.raises(ValidationError):
        Profile(id="p", name="P", homepage_bounce_percent=value)


def test_off_storage_is_compatible_and_nonzero_round_trips():
    off = Profile(id="p", name="P")
    assert "homepage_bounce_percent" not in json.loads(_serialize_profile_settings(off))
    assert (
        json.loads(_serialize_profile_settings(replace(off, homepage_bounce_percent=30)))[
            "homepage_bounce_percent"
        ]
        == 30
    )


def general_plan(chance=100, service=0):
    return compile_plan(
        devices=(),
        profiles={"p": Profile(id="p", name="P", homepage_bounce_percent=chance)},
        general_profile=GeneralProfileRule("p"),
        service_list_rules=(
            ServiceListRule(
                "listed",
                ("example.com",),
                0,
                Profile(id="s", name="S").upload,
                Profile(id="s", name="S").download,
                homepage_bounce_percent=service,
            ),
        ),
    )


def test_matching_zero_overrides_general_chance_and_codec_preserves_metadata():
    plan = general_plan()
    decoded = plan_from_payload(plan_to_payload(plan))
    assert decoded == plan
    assert (
        policy_chance(
            decoded,
            IPv4Address("192.168.50.2"),
            "example.com",
            "02:00:00:00:00:02",
            ("93.184.216.34",),
        )
        == 0
    )
    assert (
        policy_chance(
            decoded,
            IPv4Address("192.168.50.2"),
            "other.org",
            "02:00:00:00:00:02",
            ("93.184.216.34",),
        )
        == 100
    )
    assert (
        policy_chance(
            replace(plan, homepage_policy_version=0),
            IPv4Address("192.168.50.2"),
            "other.org",
            "02:00:00:00:00:02",
            ("93.184.216.34",),
        )
        == 0
    )


@pytest.mark.parametrize("value", [-1, 101, True, "10"])
def test_codec_rejects_invalid_chance(value):
    payload = plan_to_payload(general_plan())
    payload["general_profile"]["homepage_bounce_percent"] = value
    with pytest.raises(PlanCodecError):
        plan_from_payload(payload)


def test_device_chance_comes_from_compiled_profile():
    profile = Profile(id="p", name="P", homepage_bounce_percent=35)
    plan = compile_plan(devices=(Device("d", "D", "192.168.50.2", "p"),), profiles={"p": profile})
    assert plan.mark_rules[0].homepage_bounce_percent == 35
    assert (
        policy_chance(
            plan,
            IPv4Address("192.168.50.2"),
            "example.com",
            "02:00:00:00:00:02",
            ("93.184.216.34",),
        )
        == 35
    )
    assert (
        policy_chance(
            replace(plan, blocked_device_addresses=(IPv4Address("192.168.50.2"),)),
            IPv4Address("192.168.50.2"),
            "example.com",
            "02:00:00:00:00:02",
            ("93.184.216.34",),
        )
        == 0
    )


def test_older_helper_receives_no_new_fields_even_with_nonzero_saved_chance():
    from jittersim.networking.helper import HelperClient

    client = HelperClient(socket_path=__import__("pathlib").Path("/not-used"))
    client._request = lambda _: {"ok": True, "general_profile": True}
    payload = client._homepage_wire_plan(general_plan())
    assert "homepage_policy_version" not in payload
    assert "homepage_bounce_percent" not in payload["general_profile"]
    assert "homepage_bounce_percent" not in payload["service_list_rules"][0]
    decoded = plan_from_payload(payload)
    assert decoded.homepage_policy_version == 0
    assert decoded.general_profile.homepage_bounce_percent == 0
