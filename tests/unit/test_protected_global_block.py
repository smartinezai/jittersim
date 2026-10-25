"""Protected MACs skip only global blocked sites, across every enforcement path."""

import json
from dataclasses import replace
from ipaddress import IPv4Address

import pytest

from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import AccessOptions, SavedSettings, SiteBlock, WebsitePolicy
from jittersim.config.storage import ConfigRepository
from jittersim.engine.access import AccessClient, AccessPlan, access_from_payload, build_access_plan
from jittersim.engine.compiler import compile_plan
from jittersim.networking.access import (
    DNS_PORT, DNS_VIEW_PORT_FIRST, DNS_VIEW_PORT_LAST, _dns_views, render_access_firewall, render_dns,
)
from jittersim.networking.adblocking import filtering_config
from jittersim.networking.browser_proxy import destination_allowed
from jittersim.networking.renderer import render_command_plan
from jittersim.networking.topology import Topology

PROTECTED = "02:00:00:00:00:99"
ORDINARY = "02:00:00:00:00:01"
CLIENT_IP = "192.168.50.10"
TOPOLOGY = Topology("eth0", "wlan0")


def policy(mode="unrestricted"):
    return AccessPlan(
        websites=WebsitePolicy(mode, ("example.com",), ("news.example.com",)),
        blocks=(SiteBlock("private", "Private", ("private.example.com",), device_id="saved"),),
        clients=(AccessClient("saved", CLIENT_IP, PROTECTED, ("private",)),),
        protected_mac_addresses=(PROTECTED,),
    )


def test_saved_protection_reaches_the_access_payload_without_device_or_ip_enrolment():
    with ConfigRepository(":memory:") as repository:
        repository.save_unaffected_device(UnaffectedDevice(PROTECTED))
        plan = build_access_plan(repository.access_settings())
        assert plan.protected_mac_addresses == (PROTECTED,)
        assert plan.clients == ()
        assert access_from_payload(json.loads(json.dumps(plan.payload()))) == plan
    assert build_access_plan(SavedSettings()).protected_mac_addresses == ()
    legacy = AccessPlan().payload()
    legacy.pop("protected_mac_addresses", None)
    assert access_from_payload(legacy).protected_mac_addresses == ()


@pytest.mark.parametrize("bad", [None, True, "bad", ["not a mac"], ["00:00:00:00:00:00"],
                                     ["ff:ff:ff:ff:ff:ff"], [PROTECTED] * 257])
def test_untrusted_protection_is_bounded_and_validated(bad):
    with pytest.raises(ValueError):
        access_from_payload(dict(AccessPlan().payload(), protected_mac_addresses=bad))


def test_protection_normalizes_without_moving_to_another_mac():
    plan = access_from_payload(dict(AccessPlan().payload(), protected_mac_addresses=[
        "02-00-00-00-00-99", PROTECTED]))
    assert plan.protected_mac_addresses == (PROTECTED,)
    assert replace(policy(), protected_mac_addresses=()).blocked_domains("saved") == (
        "news.example.com", "private.example.com")


@pytest.mark.parametrize("mode", ["unrestricted", "allow_only"])
def test_proxy_name_policy_requires_verified_mac_and_keeps_site_rules_and_allow_only(mode):
    plan = policy(mode)
    assert plan.permits_host(CLIENT_IP, "news.example.com", client_mac=PROTECTED)
    assert plan.permits_host(CLIENT_IP, "cdn.news.example.com", client_mac=PROTECTED)
    for mac in (None, ORDINARY):
        assert not plan.permits_host(CLIENT_IP, "news.example.com", client_mac=mac)
    assert not plan.permits_host(CLIENT_IP, "private.example.com", client_mac=PROTECTED)
    assert plan.permits_host(CLIENT_IP, "outside.test", client_mac=PROTECTED) == (
        mode == "unrestricted")


def test_address_rule_order_keeps_allow_only_before_protection_and_global_drop_after():
    target = IPv4Address("203.0.113.9")
    plan = compile_plan(devices=(), profiles={}, protected_mac_addresses=(PROTECTED,),
                        global_blocked_addresses=(target,), global_allowed_addresses=(target,))
    script = next(command.stdin for command in render_command_plan(plan=plan, topology=TOPOLOGY)
                  if command.stdin and "block global website destinations" in command.stdin)
    allow = script.index('drop comment "enforce global website allowlist"')
    protected = script.index('return comment "unaffected by profiles"')
    blocked = script.index('drop comment "block global website destinations"')
    assert allow < protected < blocked
    assert destination_allowed(plan, IPv4Address(CLIENT_IP), str(target), PROTECTED)
    assert not destination_allowed(plan, IPv4Address(CLIENT_IP), str(target), ORDINARY)
    assert not destination_allowed(plan, IPv4Address(CLIENT_IP), "203.0.113.10", PROTECTED)
    assert not destination_allowed(replace(plan, global_allowed_addresses=()),
                                   IPv4Address(CLIENT_IP), str(target), PROTECTED)


def test_dns_selects_views_by_mac_even_when_a_protected_device_reuses_an_ordinary_ip():
    plan = replace(policy(), clients=(AccessClient("saved", CLIENT_IP, ORDINARY, ("private",)),))
    views, ports = _dns_views(plan)
    rules = render_access_firewall(plan, TOPOLOGY)
    protected_port = ports[plan.blocked_domains(client_mac=PROTECTED)]
    for protocol in ("udp", "tcp"):
        protected = (f'ether saddr {{ {PROTECTED} }} {protocol} dport 53 '
                     f'redirect to :{protected_port}')
        ordinary = f'ether saddr {ORDINARY} ip saddr {CLIENT_IP} {protocol} dport 53'
        assert protected in rules
        assert rules.index(protected) < rules.index(ordinary)
    dns = render_dns(plan, "testbox.local")
    assert "access-control-view:" not in dns
    for blocked, name in views.items():
        assert f'interface-view: 192.168.50.1@{ports[blocked]} "{name}"' in dns
    assert 'ct original proto-dst != 53 drop' in rules
    assert f'th dport {{ {DNS_PORT}, {DNS_VIEW_PORT_FIRST}-{DNS_VIEW_PORT_LAST} }}' in rules


def test_each_dns_view_retains_allow_only_and_the_ad_resolver_keeps_ads_and_site_rules():
    plan = policy("allow_only")
    dns = render_dns(plan, "testbox.local")
    for view in dns.split("\nview:\n")[1:]:
        assert 'local-zone: "." always_nxdomain' in view
    ads = filtering_config(("ads.test",), (), plan).decode()
    assert 'local-zone: "news.example.com." always_nxdomain' not in ads
    assert 'local-zone: "ads.test." always_nxdomain' in ads
    assert 'local-zone: "private.example.com." always_nxdomain' in ads
    assert 'local-zone: "." always_nxdomain' in ads
    assert 'local-zone: "example.com." noview' in ads
    assert f'@{DNS_VIEW_PORT_FIRST}' not in ads


def test_dns_failure_and_panic_release_keep_their_scope():
    plan = replace(policy(), options=AccessOptions(dns_failure_percent=100))
    rules = render_access_firewall(plan, TOPOLOGY)
    assert f'udp dport {{ {DNS_PORT}, {DNS_VIEW_PORT_FIRST}-{DNS_VIEW_PORT_LAST} }} counter reject' in rules
    released = replace(plan, options=AccessOptions(enabled=False))
    assert 'redirect to' not in render_access_firewall(released, TOPOLOGY)
    assert 'interface-view:' not in render_dns(released, "testbox.local")
    assert 'always_nxdomain' not in render_dns(released, "testbox.local")


def test_maximum_number_of_distinct_views_stays_inside_the_reserved_range():
    # 8 independent targeted blocks yield 256 different combinations; the
    # protected default adds at most one more view to the full household view.
    blocks = tuple(SiteBlock(f"b{i}", f"B{i}", (f"b{i}.test",), group_id=f"g{i}")
                   for i in range(8))
    clients = tuple(AccessClient(f"c{n}", f"192.168.50.{n + 2}", None,
                                tuple(f"b{i}" for i in range(8) if n & (1 << i)))
                    for n in range(253))
    plan = access_from_payload(replace(policy(), blocks=blocks, clients=clients).payload())
    _, ports = _dns_views(plan)
    assert len(ports) == 254
    assert max(ports.values()) <= DNS_VIEW_PORT_LAST


@pytest.fixture
def protection_server(tmp_path):
    from jittersim.api.server import ControlServer
    from tests.unit.test_api import FakeHelper

    with ConfigRepository(tmp_path / "settings.db") as repository:
        repository.save_website_policy(WebsitePolicy(blocked_domains=("news.example.com",)))
        # Profile Reset does not turn separately enabled household rules back on.
        repository.update_runtime_state(observed_state="reset", last_action="reset",
                                        last_result="success", last_error=None)
        server = ControlServer(("127.0.0.1", 0), repository)
        server.helper_client = FakeHelper()
        try:
            yield server, repository, server.helper_client
        finally:
            server.server_close()


@pytest.mark.parametrize("remove", [False, True])
def test_protection_changes_refresh_household_dns_once_while_profiles_are_stopped(
    protection_server, remove
):
    from unittest.mock import patch

    server, repository, helper = protection_server
    if remove:
        repository.save_unaffected_device(UnaffectedDevice(PROTECTED))
    helper.apply_access(build_access_plan(repository.access_settings()))
    with patch.object(helper, "apply_access", wraps=helper.apply_access) as apply:
        path = "/api/unaffected-devices" + ("/delete" if remove else "")
        status, _, body = server.write_response_for(path, {"mac_address": PROTECTED})
        assert status == 200, body
        assert apply.call_count == 1
        assert apply.call_args.args[0].protected_mac_addresses == (() if remove else (PROTECTED,))
    assert helper.access_plan.permits_host(CLIENT_IP, "news.example.com", client_mac=PROTECTED) != remove
    assert json.loads(server.response_for("/api/access")[2])["applied"] is True
    assert repository.get_runtime_state().last_action == "reset"


def test_protection_move_and_undo_refresh_the_saved_mac_tuple(protection_server):
    from unittest.mock import patch

    server, repository, helper = protection_server
    repository.save_unaffected_device(UnaffectedDevice(PROTECTED))
    helper.apply_access(build_access_plan(repository.access_settings()))
    with patch.object(helper, "apply_access", wraps=helper.apply_access) as apply:
        status, _, body = server.move_device_address(PROTECTED, ORDINARY, access_moved=False)
        assert status == 200, body
        assert apply.call_count == 1
        assert apply.call_args.args[0].protected_mac_addresses == (ORDINARY,)
        assert json.loads(body)["moved"]["protection"] is True
        status, _, body = server.undo_device_address_move(PROTECTED, "127.0.0.1")
        assert status == 200, body
        assert apply.call_count == 2
        assert apply.call_args.args[0].protected_mac_addresses == (PROTECTED,)
    assert json.loads(server.response_for("/api/access")[2])["applied"] is True


@pytest.mark.parametrize("remove", [False, True])
def test_protection_change_does_not_reenable_disabled_household_rules(protection_server, remove):
    from unittest.mock import patch

    server, repository, helper = protection_server
    repository.save_access_policies(None, None, options=AccessOptions(enabled=False))
    if remove:
        repository.save_unaffected_device(UnaffectedDevice(PROTECTED))
    with patch.object(helper, "apply_access", wraps=helper.apply_access) as apply:
        path = "/api/unaffected-devices" + ("/delete" if remove else "")
        status, _, body = server.write_response_for(path, {"mac_address": PROTECTED})
        assert status == 200, body
        apply.assert_not_called()
    assert repository.access_settings().access_options.enabled is False


@pytest.mark.parametrize("remove", [False, True])
def test_failed_household_refresh_reports_saved_protection_and_live_failure(protection_server, remove):
    server, repository, helper = protection_server
    if remove:
        repository.save_unaffected_device(UnaffectedDevice(PROTECTED))
    helper.apply_access(build_access_plan(repository.access_settings()))
    helper.fail_action = "access"
    path = "/api/unaffected-devices" + ("/delete" if remove else "")
    status, _, body = server.write_response_for(path, {"mac_address": PROTECTED})
    data = json.loads(body)
    assert status == 503, data
    assert data["live_applied"] is False
    assert "Apply access rules" in data["error"]
    assert [entry["mac_address"] for entry in data["devices"]] == ([] if remove else [PROTECTED])
    assert build_access_plan(repository.access_settings()).protected_mac_addresses == (
        () if remove else (PROTECTED,))
    assert json.loads(server.response_for("/api/access")[2])["applied"] is False


def test_protected_dns_view_omits_global_names_while_everyone_retains_them():
    plan = policy("allow_only")
    views, _ = _dns_views(plan)
    sections = {section.split('name: "', 1)[1].split('"', 1)[0]: section
                for section in render_dns(plan, "testbox.local").split("\nview:\n")[1:]}
    protected = sections[views[plan.blocked_domains("saved")]]
    assert 'local-zone: "news.example.com." always_nxdomain' not in protected
    assert 'local-zone: "private.example.com." always_nxdomain' in protected
    assert 'local-zone: "." always_nxdomain' in protected
    assert 'local-zone: "news.example.com." always_nxdomain' in sections["everyone"]


def test_address_refresh_preserves_the_protected_tuple(tmp_path):
    from jittersim.networking.access import AccessManager, read_access
    from tests.access_support import AccessExecutor

    manager = AccessManager(executor=AccessExecutor(tmp_path), topology=TOPOLOGY,
                            directory=tmp_path, runtime_path=tmp_path / "runtime.json")
    manager.apply(policy())
    manager.refresh_addresses([{"mac_address": PROTECTED, "ipv4_address": "192.168.50.11"}])
    assert manager.applied.clients[0].address == "192.168.50.11"
    assert manager.applied.protected_mac_addresses == (PROTECTED,)
    assert read_access(manager.runtime_path).protected_mac_addresses == (PROTECTED,)


def test_failed_household_refresh_keeps_the_saved_move_details(protection_server):
    server, repository, helper = protection_server
    repository.save_unaffected_device(UnaffectedDevice(PROTECTED))
    helper.apply_access(build_access_plan(repository.access_settings()))
    helper.fail_action = "access"
    status, _, body = server.move_device_address(PROTECTED, ORDINARY, access_moved=False)
    data = json.loads(body)
    assert status == 503 and data["saved"] is True
    assert data["live_applied"] is False
    assert data["moved"] == {"old_mac": PROTECTED, "new_mac": ORDINARY,
                             "access": False, "protection": True}
    assert build_access_plan(repository.access_settings()).protected_mac_addresses == (ORDINARY,)
    assert helper.access_plan.protected_mac_addresses == (PROTECTED,)
