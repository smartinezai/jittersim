import unittest
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, MacPolicy, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import EveryoneElseRule, ServiceQuotaRule, WebsiteFilter
from jittersim.networking.renderer import (
    queue_band,
    queue_handle,
    queue_parent,
    render_command_plan,
    render_reset_commands,
)
from jittersim.networking.topology import Topology, TopologyError


def make_device(device_id: str, address: str, profile_id: str) -> Device:
    return Device(
        id=device_id,
        name=device_id,
        ipv4_address=address,
        profile_id=profile_id,
    )


# Copied from a render on the base commit with 8 devices that each delay both directions.
EIGHT_DEVICE_TC_COMMANDS = """\
tc qdisc replace dev eth0 root handle 10: prio bands 9 priomap 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
tc qdisc replace dev eth0 parent 10:1 handle 100: fq_codel
tc qdisc replace dev eth0 parent 10:2 handle 41: netem delay 50ms corrupt 0% rate 0bit
tc filter replace dev eth0 protocol ip parent 10: pref 1 handle 1 fw flowid 10:2
tc filter replace dev eth0 protocol ipv6 parent 10: pref 101 handle 1 fw flowid 10:2
tc qdisc replace dev eth0 parent 10:3 handle 42: netem delay 50ms corrupt 0% rate 0bit
tc filter replace dev eth0 protocol ip parent 10: pref 2 handle 2 fw flowid 10:3
tc filter replace dev eth0 protocol ipv6 parent 10: pref 102 handle 2 fw flowid 10:3
tc qdisc replace dev eth0 parent 10:4 handle 43: netem delay 50ms corrupt 0% rate 0bit
tc filter replace dev eth0 protocol ip parent 10: pref 3 handle 3 fw flowid 10:4
tc filter replace dev eth0 protocol ipv6 parent 10: pref 103 handle 3 fw flowid 10:4
tc qdisc replace dev eth0 parent 10:5 handle 44: netem delay 50ms corrupt 0% rate 0bit
tc filter replace dev eth0 protocol ip parent 10: pref 4 handle 4 fw flowid 10:5
tc filter replace dev eth0 protocol ipv6 parent 10: pref 104 handle 4 fw flowid 10:5
tc qdisc replace dev eth0 parent 10:6 handle 45: netem delay 50ms corrupt 0% rate 0bit
tc filter replace dev eth0 protocol ip parent 10: pref 5 handle 5 fw flowid 10:6
tc filter replace dev eth0 protocol ipv6 parent 10: pref 105 handle 5 fw flowid 10:6
tc qdisc replace dev eth0 parent 10:7 handle 46: netem delay 50ms corrupt 0% rate 0bit
tc filter replace dev eth0 protocol ip parent 10: pref 6 handle 6 fw flowid 10:7
tc filter replace dev eth0 protocol ipv6 parent 10: pref 106 handle 6 fw flowid 10:7
tc qdisc replace dev eth0 parent 10:8 handle 47: netem delay 50ms corrupt 0% rate 0bit
tc filter replace dev eth0 protocol ip parent 10: pref 7 handle 7 fw flowid 10:8
tc filter replace dev eth0 protocol ipv6 parent 10: pref 107 handle 7 fw flowid 10:8
tc qdisc replace dev eth0 parent 10:9 handle 48: netem delay 50ms corrupt 0% rate 0bit
tc filter replace dev eth0 protocol ip parent 10: pref 8 handle 8 fw flowid 10:9
tc filter replace dev eth0 protocol ipv6 parent 10: pref 108 handle 8 fw flowid 10:9
tc qdisc replace dev wlan0 root handle 10: prio bands 9 priomap 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
tc qdisc replace dev wlan0 parent 10:1 handle 100: fq_codel
tc qdisc replace dev wlan0 parent 10:2 handle 41: netem delay 60ms corrupt 0% rate 0bit
tc filter replace dev wlan0 protocol ip parent 10: pref 1 handle 1 fw flowid 10:2
tc filter replace dev wlan0 protocol ipv6 parent 10: pref 101 handle 1 fw flowid 10:2
tc qdisc replace dev wlan0 parent 10:3 handle 42: netem delay 60ms corrupt 0% rate 0bit
tc filter replace dev wlan0 protocol ip parent 10: pref 2 handle 2 fw flowid 10:3
tc filter replace dev wlan0 protocol ipv6 parent 10: pref 102 handle 2 fw flowid 10:3
tc qdisc replace dev wlan0 parent 10:4 handle 43: netem delay 60ms corrupt 0% rate 0bit
tc filter replace dev wlan0 protocol ip parent 10: pref 3 handle 3 fw flowid 10:4
tc filter replace dev wlan0 protocol ipv6 parent 10: pref 103 handle 3 fw flowid 10:4
tc qdisc replace dev wlan0 parent 10:5 handle 44: netem delay 60ms corrupt 0% rate 0bit
tc filter replace dev wlan0 protocol ip parent 10: pref 4 handle 4 fw flowid 10:5
tc filter replace dev wlan0 protocol ipv6 parent 10: pref 104 handle 4 fw flowid 10:5
tc qdisc replace dev wlan0 parent 10:6 handle 45: netem delay 60ms corrupt 0% rate 0bit
tc filter replace dev wlan0 protocol ip parent 10: pref 5 handle 5 fw flowid 10:6
tc filter replace dev wlan0 protocol ipv6 parent 10: pref 105 handle 5 fw flowid 10:6
tc qdisc replace dev wlan0 parent 10:7 handle 46: netem delay 60ms corrupt 0% rate 0bit
tc filter replace dev wlan0 protocol ip parent 10: pref 6 handle 6 fw flowid 10:7
tc filter replace dev wlan0 protocol ipv6 parent 10: pref 106 handle 6 fw flowid 10:7
tc qdisc replace dev wlan0 parent 10:8 handle 47: netem delay 60ms corrupt 0% rate 0bit
tc filter replace dev wlan0 protocol ip parent 10: pref 7 handle 7 fw flowid 10:8
tc filter replace dev wlan0 protocol ipv6 parent 10: pref 107 handle 7 fw flowid 10:8
tc qdisc replace dev wlan0 parent 10:9 handle 48: netem delay 60ms corrupt 0% rate 0bit
tc filter replace dev wlan0 protocol ip parent 10: pref 8 handle 8 fw flowid 10:9
tc filter replace dev wlan0 protocol ipv6 parent 10: pref 108 handle 8 fw flowid 10:9
""".splitlines()


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
    def test_empty_plan_does_not_request_an_invalid_priority_queue(self) -> None:
        plan = compile_plan(devices=(), profiles={})
        commands = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="enp1s0", test_interface="wlp2s0"),
        )
        self.assertEqual([command.executable for command in commands], ["nft"])
        self.assertIn("add chain inet jittersim forward", commands[0].stdin)

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

        self.assertFalse(commands[0].ignore_failure)
        self.assertEqual(commands[0].argv, ("nft", "-f", "-"))
        self.assertIn("ip saddr 192.168.50.10 meta mark set 1", commands[0].stdin or "")
        self.assertIn("ip daddr 192.168.50.11 meta mark 0 meta mark set 2", commands[0].stdin or "")
        self.assertIn("usage-service alice-video upload", commands[0].stdin or "")
        self.assertIn("apply-service-quota alice-video download", commands[0].stdin or "")
        self.assertIn("meta mark set 3", commands[0].stdin or "")
        self.assertIn("add chain inet jittersim output", commands[0].stdin or "")
        self.assertIn("oifname \"eth0\" meta mark 1 counter", commands[0].stdin or "")

        root_commands = [
            command for command in commands if "create project queue root" in command.purpose
        ]
        self.assertEqual([command.argv[4] for command in root_commands], ["eth0", "wlan0"])
        for command in root_commands:
            priomap_start = command.argv.index("priomap") + 1
        self.assertEqual(command.argv[priomap_start:], ("0",) * 16)
        ordinary_queues = [
            command for command in commands if "fair queuing for ordinary traffic" in command.purpose
        ]
        self.assertEqual([command.argv[-1] for command in ordinary_queues], ["fq_codel"] * 2)
        self.assertEqual([command.argv[-4] for command in ordinary_queues], ["10:1"] * 2)
        first_device_filter = next(
            command for command in commands if command.purpose == "route ip mark 1 on eth0"
        )
        self.assertEqual(first_device_filter.argv[-1], "10:2")

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

        bob_filters = [command for command in commands if "route ip mark 2" in command.purpose]
        self.assertEqual(len(bob_filters), 2)
        ipv6_filters = [command for command in commands if "route ipv6 mark 2" in command.purpose]
        self.assertEqual(len(ipv6_filters), 2)
        bob_queues = [
            command
            for command in commands
            if "fair queuing for device bob" in command.purpose
        ]
        self.assertEqual(len(bob_queues), 2)
        self.assertTrue(all(command.argv[-1] == "fq_codel" for command in bob_queues))
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
                # That band gets a fair queue, replacing any impairment an older
                # layout attached to it.
                self.assertIn(
                    (
                        "tc", "qdisc", "replace", "dev", interface,
                        "parent", "10:1", "handle", "100:", "fq_codel",
                    ),
                    tc,
                )
                self.assertEqual([argv[argv.index("parent") + 1] for argv in netem], ["10:2"])
                self.assertEqual({argv[argv.index("flowid") + 1] for argv in filters}, {"10:2"})

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
        # One fixed priority per mark and protocol; tc rejects two protocols
        # at the same priority.
        self.assertEqual(
            sorted(
                (
                    argv[4],
                    argv[argv.index("protocol") + 1],
                    argv[argv.index("pref") + 1],
                    argv[argv.index("handle") + 1],
                )
                for argv in filters
            ),
            sorted(
                (interface, protocol, pref, mark)
                for interface in ("eth0", "wlan0")
                for protocol, pref, mark in (
                    ("ip", "1", "1"),
                    ("ip", "2", "2"),
                    ("ipv6", "101", "1"),
                    ("ipv6", "102", "2"),
                )
            ),
        )

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

    def test_plans_without_queues_render_no_queue_commands(self) -> None:
        plan = compile_plan(
            devices=(),
            profiles={},
            mac_policy=MacPolicy(mode="denylist", denylist=("aa:bb:cc:dd:ee:ff",)),
        )

        commands = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )

        # prio needs at least two bands; the applier resets the roots instead.
        self.assertEqual([command.executable for command in commands], ["nft"])

    def test_each_child_kind_has_its_own_handle(self) -> None:
        topology = Topology(uplink_interface="eth0", test_interface="wlan0")

        def child(profile):
            plan = compile_plan(
                devices=(make_device("alice", "192.168.50.10", profile.id),),
                profiles={profile.id: profile},
            )
            commands = render_command_plan(plan=plan, topology=topology)
            self.assertFalse(any(command.argv[1:3] == ("qdisc", "del") for command in commands))
            (argv,) = [
                command.argv
                for command in commands
                if "eth0" in command.argv and "10:2" in command.argv and command.argv[1] == "qdisc"
            ]
            return argv[argv.index("handle") + 1 :]

        normal = child(Profile(id="normal", name="Normal"))
        slow = child(Profile(id="slow", name="Slow", upload=Impairment(delay_ms=50)))

        # tc cannot change a netem into an fq_codel, so a band that switches kind
        # gets a new child under another handle instead of a change.
        self.assertEqual(normal, ("61:", "fq_codel"))
        self.assertEqual(slow[:3], ("41:", "netem", "delay"))

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
        # Both project tables are declared, dropped and rebuilt in the one script.
        self.assertTrue(
            (nft[0].stdin or "").startswith(
                "add table inet jittersim\n"
                "delete table inet jittersim\n"
                "add table netdev jittersim_download\n"
                "delete table netdev jittersim_download\n"
                "add table netdev jittersim_download\n"
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
        flowids = [
            argv[argv.index("flowid") + 1]
            for argv in eth0
            if argv[1] == "filter" and "ipv6" not in argv
        ]
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

    def test_jitter_alone_does_not_allow_reorder(self) -> None:
        profiles = {
            "jitter": Profile(
                id="jitter",
                name="Jitter",
                upload=Impairment(jitter_ms=50, reorder_percent=10),
            )
        }
        plan = compile_plan(
            devices=(make_device("alice", "192.168.50.10", "jitter"),),
            profiles=profiles,
        )

        commands = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )

        upload = next(c for c in commands if "upload impairment for device alice" in c.purpose)
        # Jitter renders as a zero delay but tc still rejects "netem delay 0ms 50ms reorder 10%".
        # The stated rate keeps the jittered packets in order instead.
        netem = list(upload.argv[upload.argv.index("netem") :])
        self.assertEqual(netem, ["netem", "delay", "0ms", "50ms", "corrupt", "0%", "rate", "10gbit"])

    def test_device_queues_are_replaced_in_place_under_kind_specific_handles(self) -> None:
        plan = compile_plan(
            devices=(
                make_device("alice", "192.168.50.10", "duplicate"),
                make_device("bob", "192.168.50.11", "normal"),
            ),
            profiles={
                "duplicate": Profile(
                    id="duplicate",
                    name="Duplicate",
                    upload=Impairment(duplicate_percent=10),
                    download=Impairment(duplicate_percent=10),
                ),
                "normal": Profile(id="normal", name="Normal"),
            },
        )
        commands = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )

        for interface in ("eth0", "wlan0"):
            queue_commands = [
                command
                for command in commands
                if command.executable == "tc"
                and command.arguments[:2] in (("qdisc", "del"), ("qdisc", "replace"))
                and command.arguments[3] == interface
                and "parent" in command.arguments
                and command.arguments[command.arguments.index("parent") + 1] != "10:1"
            ]
            # Nothing is deleted first: a replaced netem keeps the packets it holds and
            # a band that changes kind gets a child under the other kind's handle.
            self.assertEqual(
                [command.arguments[1] for command in queue_commands],
                ["replace", "replace"],
            )
            self.assertEqual(
                [command.arguments[command.arguments.index("handle") + 1] for command in queue_commands],
                ["41:", "62:"],
            )
            self.assertEqual(
                [command.arguments[command.arguments.index("parent") + 1] for command in queue_commands],
                ["10:2", "10:3"],
            )

    def test_reset_targets_only_project_owned_state(self) -> None:
        commands = render_reset_commands(
            topology=Topology(uplink_interface="eth0", test_interface="wlan0")
        )

        self.assertEqual(commands[0].argv, ("nft", "delete", "table", "inet", "jittersim"))
        self.assertEqual(
            commands[1].argv, ("nft", "delete", "table", "netdev", "jittersim_download")
        )
        # Missing tables and queues are not failures: the reset is idempotent.
        self.assertTrue(all(command.ignore_failure for command in commands))
        self.assertEqual(
            [command.argv for command in commands[2:]],
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
        self.assertNotIn("meta nfproto ipv6 drop", nft_script or "")
        self.assertIn("ip daddr { 203.0.113.10 } drop", nft_script or "")
        self.assertIn("ip daddr != { 203.0.113.20 } drop", nft_script or "")
        self.assertIn("ip daddr { 203.0.113.30 } drop", nft_script or "")

    def test_mac_filtering_uses_only_the_list_its_mode_names(self) -> None:
        allowed, denied = "11:22:33:44:55:66", "aa:bb:cc:dd:ee:ff"
        expected = {
            "disabled": [],
            "allowlist": [f"ether saddr != {{ {allowed} }} drop"],
            "denylist": [f"ether saddr {{ {denied} }} drop"],
        }
        for mode, fragments in expected.items():
            with self.subTest(mode=mode):
                policy = MacPolicy(mode=mode, allowlist=(allowed,), denylist=(denied,))
                script = render_command_plan(
                    plan=compile_plan(devices=(), profiles={}, mac_policy=policy),
                    topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
                )[0].stdin
                drops = [line for line in script.splitlines() if "ether saddr" in line]
                # One rule in the input chain and one in the forward chain.
                self.assertEqual(len(drops), 2 * len(fragments))
                for fragment in fragments:
                    self.assertIn(fragment, script)


class EveryoneElseRenderingTests(unittest.TestCase):
    topology = Topology(uplink_interface="eth0", test_interface="wlan0")

    def plan(self, *, devices=(), unaffected_macs=(), blocked=False):
        return compile_plan(
            devices=devices,
            profiles={"normal": Profile(id="normal", name="Normal")},
            everyone_else=EveryoneElseRule(
                mark=0,
                upload_impairment=Impairment(delay_ms=100),
                download_impairment=Impairment(),
                unaffected_macs=unaffected_macs,
                blocked=blocked,
            ),
        )

    def lines(self, **changes):
        plan = self.plan(**changes)
        return render_command_plan(plan=plan, topology=self.topology)[0].stdin.splitlines()

    def test_marks_clients_that_are_neither_saved_nor_unaffected(self) -> None:
        lines = self.lines(
            devices=(make_device("alice", "192.168.50.10", "normal"),),
            unaffected_macs=("aa:bb:cc:dd:ee:ff",),
        )

        # Protected clients leave the chains before any mark. Uploads are marked in
        # the forward hook and downloads get a marker that the egress hook on the
        # test interface, which sees the client MAC, turns into the queue mark.
        protected = [
            'add rule inet jittersim forward iifname "wlan0" ether saddr { aa:bb:cc:dd:ee:ff } '
            'meta mark set 0 return comment "unaffected by profiles"',
            'add rule netdev jittersim_download download ether daddr { aa:bb:cc:dd:ee:ff } '
            'meta mark set 0 return comment "unaffected by profiles"',
        ]
        expected = [
            'add rule inet jittersim forward iifname "wlan0" meta nfproto ipv4 '
            'ip saddr != { 192.168.50.10 } meta mark set 2 counter comment "everyone else upload"',
            'add rule inet jittersim forward oifname "wlan0" meta nfproto ipv4 '
            "ip daddr != { 192.168.50.10 } meta mark set 268435456 "
            'comment "identify everyone else downloads"',
            "add rule netdev jittersim_download download meta mark 268435456 "
            'meta mark set 2 counter comment "everyone else download"',
        ]
        for line in protected:
            self.assertIn(line, lines)
        start = lines.index(expected[0])
        self.assertEqual(lines[start:start + 3], expected)
        self.assertLess(max(lines.index(line) for line in protected), start)
        # Saved devices keep their own rules, which come after these.
        device_rule = next(i for i, line in enumerate(lines) if "usage-device alice upload" in line)
        self.assertLess(start, device_rule)
        self.assertEqual([line for line in lines if "ct mark" in line], [])

    def test_without_a_list_or_saved_devices_every_client_is_marked(self) -> None:
        lines = self.lines()

        self.assertIn(
            'add rule inet jittersim forward iifname "wlan0" meta nfproto ipv4 '
            'meta mark set 1 counter comment "everyone else upload"',
            lines,
        )
        self.assertIn(
            'add rule inet jittersim forward oifname "wlan0" meta nfproto ipv4 '
            'meta mark set 268435456 comment "identify everyone else downloads"',
            lines,
        )
        self.assertIn(
            "add rule netdev jittersim_download download meta mark 268435456 "
            'meta mark set 1 counter comment "everyone else download"',
            lines,
        )
        self.assertEqual([line for line in lines if "unaffected by profiles" in line], [])

    def test_an_outage_drops_everyone_else_in_both_directions(self) -> None:
        lines = self.lines(
            devices=(make_device("alice", "192.168.50.10", "normal"),),
            unaffected_macs=("aa:bb:cc:dd:ee:ff",),
            blocked=True,
        )

        # A client asking for an address sends from 0.0.0.0 and may be a saved
        # device, so DHCP still reaches the Pi. Protected clients returned earlier.
        self.assertIn(
            'add rule inet jittersim input iifname "wlan0" meta nfproto ipv4 '
            "ip saddr != { 192.168.50.10 } "
            'ip saddr != 0.0.0.0 drop comment "temporarily disconnect everyone else"',
            lines,
        )
        self.assertIn(
            'add rule inet jittersim forward iifname "wlan0" meta nfproto ipv4 '
            'ip saddr != { 192.168.50.10 } drop comment "temporarily disconnect everyone else"',
            lines,
        )
        self.assertIn(
            "add rule netdev jittersim_download download meta mark 268435456 drop "
            'comment "temporarily disconnect everyone else"',
            lines,
        )
        protected = next(i for i, line in enumerate(lines) if "unaffected by profiles" in line)
        dropped = next(i for i, line in enumerate(lines) if "temporarily disconnect" in line)
        self.assertLess(protected, dropped)

    def test_everyone_else_gets_a_queue_on_both_interfaces(self) -> None:
        commands = render_command_plan(plan=self.plan(), topology=self.topology)

        children = {
            (command.arguments[3], command.arguments[8])
            for command in commands
            if command.executable == "tc"
            and command.arguments[:2] == ("qdisc", "replace")
            and command.arguments[5] == "10:2"
        }
        self.assertEqual(children, {("eth0", "netem"), ("wlan0", "fq_codel")})

# mark -> (band that tc reads as a hexadecimal class minor, parent class, netem child handle)
QUEUE_NAMES = {
    1: ("2", "10:2", "41:"),
    2: ("3", "10:3", "42:"),
    3: ("4", "10:4", "43:"),
    4: ("5", "10:5", "44:"),
    5: ("6", "10:6", "45:"),
    6: ("7", "10:7", "46:"),
    7: ("8", "10:8", "47:"),
    8: ("9", "10:9", "48:"),
    9: ("a", "10:a", "49:"),
    10: ("b", "10:b", "4a:"),
    11: ("c", "10:c", "4b:"),
    12: ("d", "10:d", "4c:"),
    13: ("e", "10:e", "4d:"),
    14: ("f", "10:f", "4e:"),
    15: ("10", "10:10", "4f:"),
}


HEX_BANDS = ["2", "3", "4", "5", "6", "7", "8", "9", "a", "b", "c", "d", "e", "f", "10"]


def delayed_devices(count: int) -> tuple:
    return tuple(
        make_device(f"d{index:02d}", f"192.168.50.{10 + index}", "delay")
        for index in range(1, count + 1)
    )


def render_delayed_plan(count: int):
    profile = Profile(
        id="delay",
        name="Delay",
        upload=Impairment(delay_ms=50),
        download=Impairment(delay_ms=60),
    )
    plan = compile_plan(devices=delayed_devices(count), profiles={"delay": profile})
    return render_command_plan(
        plan=plan,
        topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
    )


def argument_after(command, keyword: str) -> str:
    return command.arguments[command.arguments.index(keyword) + 1]


class QueueNameHelperTests(unittest.TestCase):
    def test_band_parent_and_handle_for_marks_one_to_fifteen(self) -> None:
        for mark, (band, parent, handle) in QUEUE_NAMES.items():
            with self.subTest(mark=mark):
                self.assertEqual(queue_band(mark), band)
                self.assertEqual(queue_parent(mark), parent)
                self.assertEqual(queue_handle(mark), handle)

    def test_a_child_handle_never_collides_with_the_root_or_the_ordinary_queue(self) -> None:
        # tc reads the digits of a handle as a hexadecimal major number.
        majors = [int(queue_handle(mark).rstrip(":"), 16) for mark in range(1, 16)]
        self.assertEqual(len(set(majors)), 15)
        self.assertFalse({0x10, 0x100} & set(majors))


class QueueClassTests(unittest.TestCase):
    def test_fifteen_devices_use_the_same_hex_band_everywhere(self) -> None:
        commands = [command for command in render_delayed_plan(15) if command.executable == "tc"]

        for interface in ("eth0", "wlan0"):
            on_interface = [command for command in commands if command.arguments[3] == interface]
            roots = [command for command in on_interface if "prio" in command.arguments]
            self.assertEqual([argument_after(command, "bands") for command in roots], ["16"])
            self.assertFalse(
                any(command.arguments[:2] == ("qdisc", "del") for command in on_interface)
            )
            netem = [
                command
                for command in on_interface
                if command.arguments[:2] == ("qdisc", "replace") and "netem" in command.arguments
            ]
            self.assertEqual(
                [argument_after(command, "parent") for command in netem],
                ["10:" + band for band in HEX_BANDS],
            )
            self.assertEqual(
                [argument_after(command, "handle") for command in netem],
                [f"{0x40 + mark:x}:" for mark in range(1, 16)],
            )
            filters = [command for command in on_interface if command.arguments[0] == "filter"]
            self.assertEqual(len(filters), 30)
            for protocol in ("ip", "ipv6"):
                self.assertEqual(
                    [
                        argument_after(command, "flowid")
                        for command in filters
                        if argument_after(command, "protocol") == protocol
                    ],
                    ["10:" + band for band in HEX_BANDS],
                )
            self.assertEqual(
                [
                    argument_after(command, "handle")
                    for command in filters
                    if argument_after(command, "protocol") == "ip"
                ],
                [str(mark) for mark in range(1, 16)],
            )

    def test_every_class_exists_among_the_prio_bands_for_each_device_count(self) -> None:
        for count in range(1, 16):
            with self.subTest(devices=count):
                commands = [
                    command for command in render_delayed_plan(count) if command.executable == "tc"
                ]
                for command in commands:
                    if "prio" in command.arguments:
                        bands = int(argument_after(command, "bands"))
                        self.assertEqual(bands, count + 1)
                        continue
                    for keyword in ("parent", "flowid"):
                        if keyword not in command.arguments:
                            continue
                        major, minor = argument_after(command, keyword).split(":")
                        # A filter's parent is the bare root handle without a class.
                        if major == "10" and minor:
                            # tc reads a class minor number as hexadecimal.
                            self.assertTrue(1 <= int(minor, 16) <= count + 1, command.argv)

    def test_eight_devices_render_the_same_commands_as_before(self) -> None:
        commands = [command for command in render_delayed_plan(8) if command.executable == "tc"]

        self.assertEqual([" ".join(command.argv) for command in commands], EIGHT_DEVICE_TC_COMMANDS)


if __name__ == "__main__":
    unittest.main()
