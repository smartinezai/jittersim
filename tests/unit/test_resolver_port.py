"""The filtered resolver keeps its own port and never touches the mDNS port."""

import re
import unittest
from dataclasses import replace
from pathlib import Path

from jittersim.config.models import AccessOptions, SiteBlock
from jittersim.engine.access import AccessClient, AccessPlan
from jittersim.networking import access
from jittersim.networking.access import render_access_firewall, render_dns
from jittersim.networking.topology import Topology

ROOT = Path(__file__).resolve().parents[2]
TOPOLOGY = Topology("eth0", "wlan0")
BLOCKED = AccessPlan(
    blocks=(SiteBlock("video", "Video", ("video.example.com",), device_id="child"),),
    clients=(AccessClient("child", "192.168.50.10", "02:00:00:00:50:0a", ("video",)),),
)


class ResolverPortTests(unittest.TestCase):
    def test_resolver_listens_on_5335_and_is_redirected_to(self):
        self.assertEqual(access.DNS_PORT, 5335)
        config = render_dns(BLOCKED)
        self.assertIn("interface: 127.0.0.1@5335", config)
        self.assertIn("interface: 192.168.50.1@5335", config)
        self.assertNotIn("5353", config)
        rules = render_access_firewall(BLOCKED, TOPOLOGY)
        # A redirect to :53 would loop test-client queries back to dnsmasq and
        # skip the filter.
        for protocol in ("udp", "tcp"):
            self.assertIn(f'iifname "wlan0" {protocol} dport 53 redirect to :5335', rules)
        self.assertNotIn("redirect to :53\n", rules)

    def test_access_table_never_mentions_the_mdns_port(self):
        # Avahi answers the dashboard name on 5353. Any rule on that port in the
        # access table stops devices resolving the Pi.
        for plan in (
            AccessPlan(),
            AccessPlan(options=AccessOptions(enabled=False)),
            BLOCKED,
            replace(BLOCKED, protected_mac_addresses=("02:00:00:00:50:0a",)),
            AccessPlan(options=AccessOptions(dns_failure_percent=100)),
        ):
            rules = render_access_firewall(plan, TOPOLOGY)
            self.assertNotRegex(rules, r"\b5353\b")
            # The private resolver port stays closed to everything but the test
            # interface and loopback.
            self.assertIn(
                'iifname != "wlan0" iifname != "lo" meta l4proto { tcp, udp } th dport { 5335, 5400-5656 } drop',
                rules,
            )

    def test_control_firewall_opens_mdns_on_the_test_wifi_only_and_not_the_resolver_port(self):
        text = (ROOT / "deploy/raspberry-pi/jittersim-control-firewall.nft").read_text()
        rules = [line.strip() for line in text.splitlines() if not line.strip().startswith("#")]
        mdns = [line for line in rules if re.search(r"\b(dport|sport) 5353\b", line)]
        # One name is announced on the test Wi-Fi, so mDNS is accepted there and
        # nowhere else. Nothing about it is dropped any more.
        self.assertEqual(len(mdns), 2)
        self.assertTrue(all("accept" in line and "udp" in line and "TEST_INTERFACE" in line
                            for line in mdns))
        # Test-client DNS arrives on the test interface at the resolver port
        # after the redirect. Dropping it times out UDP lookups.
        self.assertFalse([line for line in rules if "5335" in line])


if __name__ == "__main__":
    unittest.main()


def test_the_guard_keeps_sign_in_and_dhcp_open_before_its_drops():
    from jittersim.networking.access import transition_firewall

    rules = transition_firewall(TOPOLOGY).splitlines()

    def index(fragment):
        matches = [number for number, line in enumerate(rules) if fragment in line]
        assert matches, fragment
        return matches[0]

    for chain, direction, port_rule in (
        ("input", "iifname", 'tcp dport 8080 accept'),
        ("input", "iifname", 'udp dport 67 accept'),
        ("output", "oifname", 'tcp sport 8080 accept'),
        ("output", "oifname", 'udp sport 67 accept'),
    ):
        accept = index(f'jittersim_access_transition {chain} {direction} "wlan0" {port_rule}')
        drop = index(f'jittersim_access_transition {chain} {direction} "wlan0" drop')
        assert accept < drop, (chain, port_rule)
    # Nothing is forwarded to or from the test Wi-Fi while the guard stands.
    assert not [
        line for line in rules if line.startswith("add rule") and " forward " in line and "accept" in line
    ]
