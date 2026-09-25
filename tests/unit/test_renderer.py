import unittest
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, MacPolicy, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import ServiceQuotaRule, WebsiteFilter
from jittersim.networking.renderer import render_command_plan, render_reset_commands
from jittersim.networking.topology import Topology, TopologyError


def make_device(device_id: str, address: str, profile_id: str) -> Device:
    return Device(
        id=device_id,
        name=device_id,
        ipv4_address=address,
        profile_id=profile_id,
    )


class TopologyTests(unittest.TestCase):
    def test_rejects_an_interface_injection_value(self) -> None:
        with self.assertRaises(TopologyError):
            Topology(uplink_interface="eth0;reboot", test_interface="wlan0")

    def test_rejects_using_loopback_or_the_same_interface(self) -> None:
        with self.assertRaises(TopologyError):
            Topology(uplink_interface="lo", test_interface="wlan0")
        with self.assertRaises(TopologyError):
            Topology(uplink_interface="eth0", test_interface="eth0")


class RendererTests(unittest.TestCase):
    def test_renders_marks_and_direction_specific_queues(self) -> None:
        profiles = {
            "normal": Profile(id="normal", name="Normal"),
            "slow": Profile(
                id="slow",
                name="Slow",
                upload=Impairment(
                    delay_ms=3_000,
                    jitter_ms=700,
                    loss_percent=10,
                    bandwidth_kbit=2_000,
                ),
                download=Impairment(delay_ms=500),
            ),
        }
        plan = compile_plan(
            devices=(
                make_device("alice", "192.168.50.10", "slow"),
                make_device("bob", "192.168.50.11", "normal"),
            ),
            profiles=profiles,
            service_quota_rules=(
                ServiceQuotaRule(
                    quota_id="alice-video",
                    device_id="alice",
                    device_ip=IPv4Address("192.168.50.10"),
                    destination_addresses=(IPv4Address("203.0.113.10"),),
                    mark=0,
                    upload_impairment=Impairment(delay_ms=100),
                    download_impairment=Impairment(delay_ms=200),
                    active=True,
                ),
            ),
        )

        commands = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )

        self.assertEqual(commands[0].argv, ("nft", "-f", "-"))
        self.assertIn("ip saddr 192.168.50.10 meta mark set 1", commands[0].stdin or "")
        self.assertIn("ip daddr 192.168.50.11 meta mark set 2", commands[0].stdin or "")
        self.assertIn("usage-service alice-video upload", commands[0].stdin or "")
        self.assertIn("apply-service-quota alice-video download", commands[0].stdin or "")
        self.assertIn("meta mark set 3", commands[0].stdin or "")

        root_commands = [
            command for command in commands if "create project queue root" in command.purpose
        ]
        self.assertEqual([command.argv[4] for command in root_commands], ["eth0", "wlan0"])
        for command in root_commands:
            priomap_start = command.argv.index("priomap") + 1
        self.assertEqual(command.argv[priomap_start:], ("0",) * 16)

        alice_upload = [
            command
            for command in commands
            if "upload impairment for device alice" in command.purpose
        ]
        self.assertEqual(len(alice_upload), 1)
        self.assertIn("delay", alice_upload[0].argv)
        self.assertIn("3000ms", alice_upload[0].argv)
        self.assertIn("loss", alice_upload[0].argv)
        self.assertIn("10%", alice_upload[0].argv)
        self.assertIn("rate", alice_upload[0].argv)

        bob_filters = [command for command in commands if "route mark 2" in command.purpose]
        self.assertEqual(len(bob_filters), 2)
        bob_cleanup = [
            command
            for command in commands
            if "remove stale impairment for device bob" in command.purpose
        ]
        self.assertEqual(len(bob_cleanup), 2)
        quota_upload = [
            command
            for command in commands
            if "upload impairment for device quota:alice-video" in command.purpose
        ]
        self.assertEqual(len(quota_upload), 1)

    def test_unmarked_traffic_bypasses_device_impairments(self) -> None:
        lossy = Impairment(loss_percent=100)
        plan = compile_plan(
            devices=(make_device("alice", "192.168.50.10", "lossy"),),
            profiles={"lossy": Profile(id="lossy", name="Lossy", upload=lossy, download=lossy)},
        )

        commands = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )

        for interface in ("eth0", "wlan0"):
            with self.subTest(interface=interface):
                tc = [command.argv for command in commands if interface in command.argv]
                root = next(argv for argv in tc if "root" in argv)
                netem = [argv for argv in tc if "netem" in argv]
                filters = [argv for argv in tc if argv[1] == "filter"]
                # Unmarked traffic (the Pi itself, ARP, DHCP, unassigned clients)
                # uses band 0, which is class 10:1.
                self.assertEqual(root[root.index("priomap") + 1 :], ("0",) * 16)
                # Clear any impairment an older layout attached to that band.
                self.assertIn(("tc", "qdisc", "del", "dev", interface, "parent", "10:1"), tc)
                self.assertEqual([argv[argv.index("parent") + 1] for argv in netem], ["10:2"])
                self.assertEqual([argv[argv.index("flowid") + 1] for argv in filters], ["10:2"])

    def test_filters_use_a_fixed_priority_so_reapplying_replaces_them(self) -> None:
        plan = compile_plan(
            devices=(
                make_device("alice", "192.168.50.10", "normal"),
                make_device("bob", "192.168.50.11", "normal"),
            ),
            profiles={"normal": Profile(id="normal", name="Normal")},
        )

        commands = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )

        filters = [command.argv for command in commands if command.argv[1] == "filter"]
        self.assertEqual(len(filters), 4)
        for argv in filters:
            self.assertIn("pref", argv)
            self.assertEqual(argv[argv.index("pref") + 1], "1")

    def test_reordering_is_only_rendered_with_a_delay(self) -> None:
        profiles = {
            "reorder": Profile(
                id="reorder",
                name="Reorder",
                upload=Impairment(reorder_percent=10),
                download=Impairment(delay_ms=50, reorder_percent=10),
            )
        }
        plan = compile_plan(
            devices=(make_device("alice", "192.168.50.10", "reorder"),),
            profiles=profiles,
        )

        commands = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )

        upload = next(c for c in commands if "upload impairment for device alice" in c.purpose)
        download = next(c for c in commands if "download impairment for device alice" in c.purpose)
        # tc rejects "netem reorder" unless the queue also delays packets.
        self.assertNotIn("reorder", upload.argv)
        self.assertIn("reorder", download.argv)

    def test_interfaces_without_queues_remove_the_project_root(self) -> None:
        plan = compile_plan(
            devices=(),
            profiles={},
            mac_policy=MacPolicy(mode="denylist", denylist=("aa:bb:cc:dd:ee:ff",)),
        )

        commands = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )

        tc = [command for command in commands if command.executable == "tc"]
        # prio needs at least two bands, so there is no root to build here.
        self.assertEqual(
            [command.argv for command in tc],
            [
                ("tc", "qdisc", "del", "dev", "eth0", "root", "handle", "10:"),
                ("tc", "qdisc", "del", "dev", "wlan0", "root", "handle", "10:"),
            ],
        )
        self.assertTrue(all(command.ignore_failure for command in tc))

    def test_nftables_rules_are_replaced_in_one_transaction(self) -> None:
        plan = compile_plan(
            devices=(make_device("alice", "192.168.50.10", "normal"),),
            profiles={"normal": Profile(id="normal", name="Normal")},
        )

        commands = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )

        nft = [command for command in commands if command.executable == "nft"]
        self.assertEqual([command.argv for command in nft], [("nft", "-f", "-")])
        self.assertTrue(
            (nft[0].stdin or "").startswith(
                "add table inet jittersim\n"
                "delete table inet jittersim\n"
                "add table inet jittersim\n"
            )
        )

    def test_class_ids_are_hexadecimal(self) -> None:
        slow = Profile(id="slow", name="Slow", upload=Impairment(delay_ms=20))
        plan = compile_plan(
            devices=tuple(
                make_device(f"device-{index:02d}", f"192.168.50.{index + 10}", "slow")
                for index in range(1, 16)
            ),
            profiles={"slow": slow},
        )

        commands = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )

        eth0 = [command.argv for command in commands if "eth0" in command.argv]
        parents = [argv[argv.index("parent") + 1] for argv in eth0 if "netem" in argv]
        flowids = [argv[argv.index("flowid") + 1] for argv in eth0 if argv[1] == "filter"]
        # tc reads the minor number as hex: mark 9 is band 9, class 10:a.
        expected = [f"10:{mark + 1:x}" for mark in range(1, 16)]
        self.assertEqual(parents, expected)
        self.assertEqual(flowids, expected)
        self.assertEqual(expected[-1], "10:10")

    def test_blocked_sites_without_an_allowlist_keep_other_traffic(self) -> None:
        plan = compile_plan(
            devices=(
                make_device("alice", "192.168.50.10", "normal"),
                make_device("bob", "192.168.50.11", "normal"),
            ),
            profiles={"normal": Profile(id="normal", name="Normal")},
            website_filters=(
                WebsiteFilter(
                    device_ip=IPv4Address("192.168.50.10"),
                    allowed_addresses=None,
                    blocked_addresses=(IPv4Address("203.0.113.30"),),
                ),
                WebsiteFilter(
                    device_ip=IPv4Address("192.168.50.11"),
                    allowed_addresses=(),
                ),
            ),
        )

        nft_script = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )[0].stdin or ""

        self.assertIn("ip saddr 192.168.50.10 ip daddr { 203.0.113.30 } drop", nft_script)
        self.assertNotIn("ip saddr 192.168.50.10 drop", nft_script)
        # An allow-only rule with no resolved addresses still blocks everything.
        self.assertIn("ip saddr 192.168.50.11 drop", nft_script)

    def test_netem_options_are_always_stated_so_old_values_do_not_linger(self) -> None:
        profiles = {
            "plain": Profile(id="plain", name="Plain", upload=Impairment(delay_ms=100)),
            "limited": Profile(
                id="limited",
                name="Limited",
                upload=Impairment(delay_ms=100, corrupt_percent=5, bandwidth_kbit=1_000),
            ),
        }
        plan = compile_plan(
            devices=(
                make_device("alice", "192.168.50.10", "plain"),
                make_device("bob", "192.168.50.11", "limited"),
            ),
            profiles=profiles,
        )

        commands = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )

        def netem_arguments(device_id):
            purpose = f"upload impairment for device {device_id}"
            command = next(c for c in commands if purpose in c.purpose)
            return command.argv[command.argv.index("netem") :]

        # "tc qdisc replace" changes an existing netem in place and keeps any
        # corrupt or rate setting it is not given.
        plain = netem_arguments("alice")
        limited = netem_arguments("bob")
        self.assertEqual(plain[plain.index("corrupt") + 1], "0%")
        self.assertEqual(plain[plain.index("rate") + 1], "0bit")
        self.assertEqual(limited[limited.index("corrupt") + 1], "5%")
        self.assertEqual(limited[limited.index("rate") + 1], "1000kbit")

    def _upload_netem(self, impairment: Impairment):
        plan = compile_plan(
            devices=(make_device("alice", "192.168.50.10", "chosen"),),
            profiles={"chosen": Profile(id="chosen", name="Chosen", upload=impairment)},
        )
        commands = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )
        command = next(c for c in commands if "upload impairment for device alice" in c.purpose)
        return command.argv[command.argv.index("netem") :]

    def test_bursty_loss_uses_the_gilbert_model_with_the_same_average(self) -> None:
        bursty = self._upload_netem(Impairment(loss_percent=10, loss_burst_packets=4))
        independent = self._upload_netem(Impairment(loss_percent=10, loss_burst_packets=1))
        total = self._upload_netem(Impairment(loss_percent=100, loss_burst_packets=4))

        # Leaving the losing state with r = 1/4 gives bursts of 4 on average, and
        # p = 0.1 * r / 0.9 keeps the average loss at 10%.
        self.assertEqual(bursty[bursty.index("loss") : bursty.index("loss") + 4],
                         ("loss", "gemodel", "2.7778%", "25.0000%"))
        self.assertEqual(independent[independent.index("loss") + 1], "random")
        self.assertEqual(total[total.index("loss") : total.index("loss") + 3],
                         ("loss", "random", "100%"))

    def test_frequent_loss_lengthens_bursts_instead_of_breaking_the_model(self) -> None:
        frequent = self._upload_netem(Impairment(loss_percent=70, loss_burst_packets=2))

        # Runs of 2 cannot lose 70% on average, so the good state is left after
        # every packet and runs last (1 - 0.7) / 0.7 on the way out.
        self.assertEqual(frequent[frequent.index("loss") : frequent.index("loss") + 4],
                         ("loss", "gemodel", "100.0000%", "42.8571%"))
        for loss in range(1, 100):
            for burst in (2, 3, 10, 100):
                arguments = self._upload_netem(
                    Impairment(loss_percent=loss, loss_burst_packets=burst)
                )
                position = arguments.index("gemodel")
                percentages = [
                    float(value.rstrip("%")) for value in arguments[position + 1 : position + 3]
                ]
                self.assertTrue(all(0 < value <= 100 for value in percentages), (loss, burst))

    def test_jitter_keeps_packets_in_order(self) -> None:
        jitter = self._upload_netem(Impairment(delay_ms=100, jitter_ms=30))
        limited = self._upload_netem(Impairment(delay_ms=100, jitter_ms=30, bandwidth_kbit=500))
        steady = self._upload_netem(Impairment(delay_ms=100))

        # netem only keeps packets in order when a rate serializes them.
        self.assertEqual(jitter[jitter.index("rate") + 1], "10gbit")
        self.assertEqual(limited[limited.index("rate") + 1], "500kbit")
        self.assertEqual(steady[steady.index("rate") + 1], "0bit")

    def test_renders_jitter_even_without_a_base_delay(self) -> None:
        profile = Profile(
            id="jitter-only",
            name="Jitter only",
            upload=Impairment(jitter_ms=250),
        )
        plan = compile_plan(
            devices=(make_device("alice", "192.168.50.10", "jitter-only"),),
            profiles={"jitter-only": profile},
        )

        commands = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )

        upload_command = next(
            command
            for command in commands
            if "upload impairment for device alice" in command.purpose
        )
        self.assertIn("delay", upload_command.argv)
        self.assertIn("0ms", upload_command.argv)
        self.assertIn("250ms", upload_command.argv)

    def test_reset_targets_only_project_owned_state(self) -> None:
        commands = render_reset_commands(
            topology=Topology(uplink_interface="eth0", test_interface="wlan0")
        )

        self.assertEqual(commands[0].argv, ("nft", "delete", "table", "inet", "jittersim"))
        self.assertTrue(all(command.ignore_failure for command in commands))
        self.assertEqual(
            [command.argv for command in commands[1:]],
            [
                ("tc", "qdisc", "del", "dev", "eth0", "root", "handle", "10:"),
                ("tc", "qdisc", "del", "dev", "wlan0", "root", "handle", "10:"),
            ],
        )

    def test_renders_mac_and_website_filters_before_marks(self) -> None:
        plan = compile_plan(
            devices=(make_device("alice", "192.168.50.10", "normal"),),
            profiles={"normal": Profile(id="normal", name="Normal")},
            mac_policy=MacPolicy(mode="denylist", denylist=("aa:bb:cc:dd:ee:ff",)),
            global_blocked_addresses=(IPv4Address("203.0.113.10"),),
            website_filters=(
                WebsiteFilter(
                    device_ip=IPv4Address("192.168.50.10"),
                    allowed_addresses=(IPv4Address("203.0.113.20"),),
                    blocked_addresses=(IPv4Address("203.0.113.30"),),
                ),
            ),
        )

        nft_script = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )[0].stdin

        self.assertIn("ether saddr { aa:bb:cc:dd:ee:ff } drop", nft_script or "")
        self.assertIn(
            'iifname "wlan0" oifname "eth0" meta nfproto ipv6 drop',
            nft_script or "",
        )
        self.assertIn("ip daddr { 203.0.113.10 } drop", nft_script or "")
        self.assertIn("ip daddr != { 203.0.113.20 } drop", nft_script or "")
        self.assertIn("ip daddr { 203.0.113.30 } drop", nft_script or "")


if __name__ == "__main__":
    unittest.main()
