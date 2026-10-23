"""The Pi files that announce the one dashboard name on both networks."""

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

PI = Path(__file__).resolve().parents[2] / "deploy" / "raspberry-pi"


def rules(text: str) -> list:
    return [" ".join(line.split()) for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def rendered_firewall(uplink: str = "eth0", test_interface: str = "wlan0") -> list:
    text = (PI / "jittersim-control-firewall.nft").read_text()
    text = text.replace("@UPLINK_INTERFACE@", uplink).replace("@TEST_INTERFACE@", test_interface)
    return rules(text)


class ControlFirewallTests(unittest.TestCase):
    def test_mdns_is_open_on_the_test_wifi_for_udp_5353_only(self) -> None:
        mdns = [line for line in rendered_firewall() if re.search(r"\b5353\b", line)]

        self.assertEqual(len(mdns), 2)
        inbound = [line for line in mdns if line.startswith("iifname")]
        outbound = [line for line in mdns if line.startswith("oifname")]
        self.assertEqual(len(inbound), 1)
        self.assertEqual(len(outbound), 1)
        self.assertRegex(inbound[0], r'^iifname "wlan0" udp dport 5353 counter accept\b')
        self.assertRegex(outbound[0], r'^oifname "wlan0" udp sport 5353 counter accept\b')
        for line in mdns:
            self.assertNotIn("tcp", line)
            self.assertNotIn("drop", line)

    def test_nothing_else_is_opened_on_the_test_wifi(self) -> None:
        accepted = [line for line in rendered_firewall()
                    if '"wlan0"' in line and line.rstrip().split(" comment")[0].endswith("accept")]

        self.assertEqual(sorted(re.sub(r" comment .*", "", line) for line in accepted), sorted([
            'iifname "wlan0" tcp dport 8080 counter accept',
            'iifname "wlan0" ip daddr 192.168.50.1 tcp dport 8443 accept',
            'iifname "wlan0" udp dport 5353 counter accept',
            'iifname "wlan0" ip saddr 192.168.50.0/24 tcp dport 3128 accept',
            'oifname "wlan0" udp sport 5353 counter accept',
        ]))

    def test_everything_that_was_closed_stays_closed(self) -> None:
        text = rendered_firewall()
        for line in (
            'iifname "wlan0" tcp dport 22 counter drop comment "deny SSH from test Wi-Fi"',
            'tcp dport 8080 counter drop comment "keep dashboard on private management paths"',
            'tcp dport 8443 drop comment "keep homepage policy on test Wi-Fi"',
            'tcp dport 3128 counter drop comment "keep browser proxy on test Wi-Fi"',
        ):
            self.assertIn(line, text)
        joined = "\n".join(text)
        self.assertIn('iifname "wlan0" oifname "eth0" ip daddr', joined)
        self.assertIn("deny test Wi-Fi access to private upstream networks", joined)

    def test_the_resolver_port_is_not_touched_and_ethernet_gets_no_mdns_rule(self) -> None:
        joined = "\n".join(rendered_firewall("eth9", "wlan9"))
        self.assertNotIn("5335", joined)
        self.assertNotRegex(joined, r'"eth9"[^\n]*5353')
        self.assertNotRegex(joined, r'"wlan9"[^\n]*5353[^\n]*drop')

    def test_the_interfaces_come_from_the_saved_settings(self) -> None:
        mdns = [line for line in rendered_firewall("eth7", "wlan7") if "5353" in line]
        self.assertTrue(all('"wlan7"' in line for line in mdns))

    def test_a_test_wifi_packet_from_port_53_is_dropped_before_it_is_forwarded(self) -> None:
        # Follow-up F1 of the security audit of 2026-10-18. The DNS watcher reads the frames
        # the Pi sends out of the test interface. A device could send a packet from UDP port
        # 53 to itself or to another device through the Pi and pass for a forwarded answer.
        # The packets are checked on a real kernel in tests/kernel/test_access.py.
        chains = {}
        current = None
        for line in rendered_firewall("eth7", "wlan7"):
            if line.startswith("chain "):
                current = line.split()[1]
                chains[current] = []
            elif current:
                chains[current].append(line)
        found = [(name, line) for name, lines in chains.items() for line in lines
                 if re.search(r"\bsport 53\b", line)]

        self.assertEqual(len(found), 1, found)
        name, line = found[0]
        self.assertEqual(name, "forward")
        self.assertRegex(line, r'^iifname "wlan7" udp sport 53 counter drop\b')
        # It matches the interface a packet came in on. An answer the Pi makes itself leaves
        # through the output chain and an answer from the router or the internet arrives on
        # the uplink, so neither passes this rule.
        self.assertNotIn("oifname", line)
        self.assertNotIn("eth7", line)


class NameServiceUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.unit = (PI / "jittersim-mdns.service").read_text()

    def test_the_unit_runs_the_bundled_publisher_with_the_system_python(self) -> None:
        self.assertIn("ExecStart=/usr/bin/python3 -m jittersim.mdnsname publish", self.unit)
        self.assertIn("Environment=PYTHONPATH=/opt/jittersim/src", self.unit)
        # avahi-publish-address cannot bind a record to one network.
        self.assertNotIn("avahi-publish", self.unit)
        self.assertFalse((PI / "publish-dashboard-name.sh").exists())

    def test_avahi_is_wanted_not_required_so_a_failure_never_blocks_the_dashboard(self) -> None:
        self.assertIn("Wants=avahi-daemon.service", self.unit)
        self.assertNotIn("Requires=", self.unit)
        dashboard = (PI / "jittersim-dashboard.service").read_text()
        self.assertNotIn("jittersim-mdns", dashboard)

    def test_the_status_report_has_its_own_readable_folder(self) -> None:
        self.assertIn("RuntimeDirectory=jittersim-mdns", self.unit)
        self.assertIn("RuntimeDirectoryMode=0755", self.unit)

    def test_the_publisher_runs_without_privileges_it_does_not_need(self) -> None:
        for setting in ("NoNewPrivileges=true", "ProtectSystem=strict", "ProtectHome=true",
                        "CapabilityBoundingSet=\n", "RestrictAddressFamilies=AF_UNIX AF_INET AF_NETLINK"):
            self.assertIn(setting, self.unit)


class DispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.log = self.root / "systemctl.log"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        # `is-active` answers from a flag file (running unless the test removes it).
        self.running = self.root / "unit-is-active"
        self.running.write_text("")
        fake = bin_dir / "systemctl"
        fake.write_text(
            '#!/bin/sh\necho "$@" >> "%s"\n'
            'if [ "$1" = is-active ]; then [ -e "%s" ]; exit $?; fi\n' % (self.log, self.running))
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.runtime = self.root / "runtime.env"
        script = (PI / "jittersim-mdns-dispatcher").read_text().replace(
            "/etc/jittersim/runtime.env", str(self.runtime))
        self.script = self.root / "dispatcher"
        self.script.write_text(script)
        self.env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
        self.runtime.write_text("JITTERSIM_UPLINK=eth0\nJITTERSIM_TEST_INTERFACE=wlan0\n")

    def call(self, *arguments: str) -> list:
        """The systemctl calls that were not the question whether the unit runs."""
        subprocess.run(["sh", str(self.script), *arguments], check=True, env=self.env,
                       capture_output=True, timeout=10)
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return [line for line in lines if not line.startswith("is-active")]

    def test_an_ethernet_address_change_wakes_the_publisher_without_restarting_it(self) -> None:
        for action in ("up", "down", "dhcp4-change", "reapply"):
            with self.subTest(action=action):
                self.log.unlink(missing_ok=True)
                calls = self.call("eth0", action)
                self.assertEqual(len(calls), 1)
                self.assertIn("kill", calls[0])
                self.assertIn("--signal=HUP", calls[0])
                self.assertIn("jittersim-mdns.service", calls[0])
                self.assertNotIn("restart", calls[0])

    def test_the_test_wifi_coming_up_or_going_down_wakes_it_too(self) -> None:
        self.assertEqual(len(self.call("wlan0", "up")), 1)
        self.log.unlink()
        self.assertEqual(len(self.call("wlan0", "down")), 1)

    def test_other_interfaces_and_other_events_are_ignored(self) -> None:
        self.assertEqual(self.call("docker0", "up"), [])
        self.assertEqual(self.call("lo", "up"), [])
        self.assertEqual(self.call("eth0", "pre-up"), [])
        self.assertEqual(self.call("eth0", "vpn-up"), [])
        self.assertEqual(self.call("eth0", ""), [])
        self.assertEqual(self.call("", "up"), [])

    def test_the_saved_interface_names_are_used(self) -> None:
        self.runtime.write_text("JITTERSIM_UPLINK=end0\nJITTERSIM_TEST_INTERFACE=wlan1\n")
        self.assertEqual(self.call("eth0", "up"), [])
        self.assertEqual(len(self.call("end0", "up")), 1)
        self.log.unlink()
        self.assertEqual(len(self.call("wlan1", "up")), 1)

    def test_without_the_saved_settings_nothing_happens(self) -> None:
        self.runtime.unlink()
        self.assertEqual(self.call("eth0", "up"), [])

    def test_a_missing_or_stopped_service_never_fails_the_network_event(self) -> None:
        (self.root / "bin" / "systemctl").write_text("#!/bin/sh\nexit 5\n")
        subprocess.run(["sh", str(self.script), "eth0", "up"], check=True, env=self.env,
                       capture_output=True, timeout=10)

    def test_the_wake_up_is_sent_only_after_asking_whether_the_service_runs(self) -> None:
        self.call("eth0", "up")

        raw = self.log.read_text().splitlines()
        self.assertEqual(len(raw), 2)
        self.assertRegex(raw[0], r"^is-active\b.*jittersim-mdns\.service$")
        self.assertIn("kill", raw[1])

    def test_a_stopped_or_restarting_publisher_is_not_signalled(self) -> None:
        self.running.unlink()
        for action in ("up", "down", "dhcp4-change", "reapply"):
            with self.subTest(action=action):
                self.assertEqual(self.call("eth0", action), [])

    def test_a_signal_that_cannot_be_sent_never_fails_the_network_event(self) -> None:
        (self.root / "bin" / "systemctl").write_text(
            '#!/bin/sh\n[ "$1" = kill ] && exit 5\nexit 0\n')
        subprocess.run(["sh", str(self.script), "eth0", "up"], check=True, env=self.env,
                       capture_output=True, timeout=10)


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.installer = (PI / "install.sh").read_text()

    def test_avahi_is_limited_before_the_control_firewall_is_rendered_and_restarted(self) -> None:
        limit = self.installer.index("-m jittersim.mdnsname configure")
        render = self.installer.index('"$SOURCE_DIR/deploy/raspberry-pi/jittersim-control-firewall.nft"')
        restart = self.installer.index("systemctl restart jittersim-control-firewall.service")
        self.assertLess(limit, render)
        self.assertLess(render, restart)

    def test_avahi_and_the_dbus_module_are_optional_packages(self) -> None:
        self.assertIn("apt-get install -y avahi-daemon avahi-utils python3-dbus || return 1",
                      self.installer)
        self.assertIn("if ! prepare_dashboard_name_service; then", self.installer)
        self.assertIn("systemctl stop avahi-daemon.service avahi-daemon.socket || true",
                      self.installer)
        # A failure never stops the installer.
        self.assertIn("if setup_dashboard_name_service; then", self.installer)

    def test_the_name_publisher_is_started_only_when_avahi_was_limited(self) -> None:
        self.assertIn('if [ "$dashboard_name_ready" = yes ]; then', self.installer)
        start = self.installer.index('if [ "$dashboard_name_ready" = yes ]; then')
        self.assertIn("setup_dashboard_name_service", self.installer[start:start + 200])

    def test_the_installer_no_longer_uses_the_all_networks_publisher(self) -> None:
        self.assertNotIn("avahi-publish", self.installer)
        self.assertNotIn("publish-dashboard-name", self.installer)
        self.assertNotIn("jittersim.local from the management network", self.installer)

    def test_the_final_message_names_the_one_address(self) -> None:
        self.assertIn("https://$local_name:8080", self.installer)
        self.assertIn("the same on the router network and the test Wi-Fi", self.installer)


if __name__ == "__main__":
    unittest.main()
