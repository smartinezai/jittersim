import unittest
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, MacPolicy, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import ServiceQuotaRule, WebsiteFilter
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
tc qdisc del dev eth0 parent 10:2 handle 11:
tc qdisc del dev eth0 parent 10:3 handle 12:
tc qdisc del dev eth0 parent 10:4 handle 13:
tc qdisc del dev eth0 parent 10:5 handle 14:
tc qdisc del dev eth0 parent 10:6 handle 15:
tc qdisc del dev eth0 parent 10:7 handle 16:
tc qdisc del dev eth0 parent 10:8 handle 17:
tc qdisc del dev eth0 parent 10:9 handle 18:
tc qdisc replace dev eth0 parent 10:2 handle 11: netem delay 50ms
tc filter replace dev eth0 protocol ip parent 10: pref 1 handle 1 fw flowid 10:2
tc filter replace dev eth0 protocol ipv6 parent 10: pref 101 handle 1 fw flowid 10:2
tc qdisc replace dev eth0 parent 10:3 handle 12: netem delay 50ms
tc filter replace dev eth0 protocol ip parent 10: pref 2 handle 2 fw flowid 10:3
tc filter replace dev eth0 protocol ipv6 parent 10: pref 102 handle 2 fw flowid 10:3
tc qdisc replace dev eth0 parent 10:4 handle 13: netem delay 50ms
tc filter replace dev eth0 protocol ip parent 10: pref 3 handle 3 fw flowid 10:4
tc filter replace dev eth0 protocol ipv6 parent 10: pref 103 handle 3 fw flowid 10:4
tc qdisc replace dev eth0 parent 10:5 handle 14: netem delay 50ms
tc filter replace dev eth0 protocol ip parent 10: pref 4 handle 4 fw flowid 10:5
tc filter replace dev eth0 protocol ipv6 parent 10: pref 104 handle 4 fw flowid 10:5
tc qdisc replace dev eth0 parent 10:6 handle 15: netem delay 50ms
tc filter replace dev eth0 protocol ip parent 10: pref 5 handle 5 fw flowid 10:6
tc filter replace dev eth0 protocol ipv6 parent 10: pref 105 handle 5 fw flowid 10:6
tc qdisc replace dev eth0 parent 10:7 handle 16: netem delay 50ms
tc filter replace dev eth0 protocol ip parent 10: pref 6 handle 6 fw flowid 10:7
tc filter replace dev eth0 protocol ipv6 parent 10: pref 106 handle 6 fw flowid 10:7
tc qdisc replace dev eth0 parent 10:8 handle 17: netem delay 50ms
tc filter replace dev eth0 protocol ip parent 10: pref 7 handle 7 fw flowid 10:8
tc filter replace dev eth0 protocol ipv6 parent 10: pref 107 handle 7 fw flowid 10:8
tc qdisc replace dev eth0 parent 10:9 handle 18: netem delay 50ms
tc filter replace dev eth0 protocol ip parent 10: pref 8 handle 8 fw flowid 10:9
tc filter replace dev eth0 protocol ipv6 parent 10: pref 108 handle 8 fw flowid 10:9
tc qdisc replace dev wlan0 root handle 10: prio bands 9 priomap 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
tc qdisc replace dev wlan0 parent 10:1 handle 100: fq_codel
tc qdisc del dev wlan0 parent 10:2 handle 11:
tc qdisc del dev wlan0 parent 10:3 handle 12:
tc qdisc del dev wlan0 parent 10:4 handle 13:
tc qdisc del dev wlan0 parent 10:5 handle 14:
tc qdisc del dev wlan0 parent 10:6 handle 15:
tc qdisc del dev wlan0 parent 10:7 handle 16:
tc qdisc del dev wlan0 parent 10:8 handle 17:
tc qdisc del dev wlan0 parent 10:9 handle 18:
tc qdisc replace dev wlan0 parent 10:2 handle 11: netem delay 60ms
tc filter replace dev wlan0 protocol ip parent 10: pref 1 handle 1 fw flowid 10:2
tc filter replace dev wlan0 protocol ipv6 parent 10: pref 101 handle 1 fw flowid 10:2
tc qdisc replace dev wlan0 parent 10:3 handle 12: netem delay 60ms
tc filter replace dev wlan0 protocol ip parent 10: pref 2 handle 2 fw flowid 10:3
tc filter replace dev wlan0 protocol ipv6 parent 10: pref 102 handle 2 fw flowid 10:3
tc qdisc replace dev wlan0 parent 10:4 handle 13: netem delay 60ms
tc filter replace dev wlan0 protocol ip parent 10: pref 3 handle 3 fw flowid 10:4
tc filter replace dev wlan0 protocol ipv6 parent 10: pref 103 handle 3 fw flowid 10:4
tc qdisc replace dev wlan0 parent 10:5 handle 14: netem delay 60ms
tc filter replace dev wlan0 protocol ip parent 10: pref 4 handle 4 fw flowid 10:5
tc filter replace dev wlan0 protocol ipv6 parent 10: pref 104 handle 4 fw flowid 10:5
tc qdisc replace dev wlan0 parent 10:6 handle 15: netem delay 60ms
tc filter replace dev wlan0 protocol ip parent 10: pref 5 handle 5 fw flowid 10:6
tc filter replace dev wlan0 protocol ipv6 parent 10: pref 105 handle 5 fw flowid 10:6
tc qdisc replace dev wlan0 parent 10:7 handle 16: netem delay 60ms
tc filter replace dev wlan0 protocol ip parent 10: pref 6 handle 6 fw flowid 10:7
tc filter replace dev wlan0 protocol ipv6 parent 10: pref 106 handle 6 fw flowid 10:7
tc qdisc replace dev wlan0 parent 10:8 handle 17: netem delay 60ms
tc filter replace dev wlan0 protocol ip parent 10: pref 7 handle 7 fw flowid 10:8
tc filter replace dev wlan0 protocol ipv6 parent 10: pref 107 handle 7 fw flowid 10:8
tc qdisc replace dev wlan0 parent 10:9 handle 18: netem delay 60ms
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
        netem = list(upload.argv[upload.argv.index("netem") :])
        self.assertEqual(netem, ["netem", "delay", "0ms", "50ms"])

    def test_clears_all_previous_device_queues_before_replacements(self) -> None:
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
            self.assertEqual(
                [command.arguments[1] for command in queue_commands],
                ["del", "del", "replace", "replace"],
            )
            self.assertEqual(
                [command.arguments[-1] for command in queue_commands[:2]],
                ["11:", "12:"],
            )

    def test_reset_targets_only_project_owned_state(self) -> None:
        commands = render_reset_commands(
            topology=Topology(uplink_interface="eth0", test_interface="wlan0")
        )

        self.assertEqual(commands[0].argv, ("nft", "delete", "table", "inet", "jittersim"))
        self.assertEqual(
            commands[1].argv, ("nft", "delete", "table", "netdev", "jittersim_download")
        )
        self.assertFalse(commands[2].ignore_failure)
        self.assertTrue(all(command.ignore_failure for command in commands[:2] + commands[3:]))
        self.assertEqual(
            [command.argv for command in commands[3:]],
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


# mark -> (band that tc reads as a hexadecimal class minor, parent class, child qdisc handle)
QUEUE_NAMES = {
    1: ("2", "10:2", "11:"),
    2: ("3", "10:3", "12:"),
    3: ("4", "10:4", "13:"),
    4: ("5", "10:5", "14:"),
    5: ("6", "10:6", "15:"),
    6: ("7", "10:7", "16:"),
    7: ("8", "10:8", "17:"),
    8: ("9", "10:9", "18:"),
    9: ("a", "10:a", "19:"),
    10: ("b", "10:b", "20:"),
    11: ("c", "10:c", "21:"),
    12: ("d", "10:d", "22:"),
    13: ("e", "10:e", "23:"),
    14: ("f", "10:f", "24:"),
    15: ("10", "10:10", "25:"),
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
            clears = [
                command
                for command in on_interface
                if command.arguments[:2] == ("qdisc", "del")
            ]
            self.assertEqual(
                [argument_after(command, "parent") for command in clears],
                ["10:" + band for band in HEX_BANDS],
            )
            self.assertEqual(
                [argument_after(command, "handle") for command in clears],
                [f"{mark + 10}:" for mark in range(1, 16)],
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
                [f"{mark + 10}:" for mark in range(1, 16)],
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
