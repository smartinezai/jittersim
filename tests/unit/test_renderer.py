import unittest

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.networking.renderer import render_command_plan
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

        root_commands = [command for command in commands if "create project queue root" in command.purpose]
        self.assertEqual([command.argv[4] for command in root_commands], ["eth0", "wlan0"])

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


if __name__ == "__main__":
    unittest.main()
