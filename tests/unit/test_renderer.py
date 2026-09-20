import unittest
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, MacPolicy, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import WebsiteFilter
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
        )

        commands = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )

        self.assertTrue(commands[0].ignore_failure)
        self.assertEqual(commands[0].argv, ("nft", "delete", "table", "inet", "jittersim"))
        self.assertEqual(commands[1].argv, ("nft", "-f", "-"))
        self.assertIn("ip saddr 192.168.50.10 meta mark set 1", commands[1].stdin or "")
        self.assertIn("ip daddr 192.168.50.11 meta mark set 2", commands[1].stdin or "")

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
                ),
            ),
        )

        nft_script = render_command_plan(
            plan=plan,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )[1].stdin

        self.assertIn("ether saddr { aa:bb:cc:dd:ee:ff } drop", nft_script or "")
        self.assertIn("ip daddr { 203.0.113.10 } drop", nft_script or "")
        self.assertIn("ip daddr != { 203.0.113.20 } drop", nft_script or "")


if __name__ == "__main__":
    unittest.main()
