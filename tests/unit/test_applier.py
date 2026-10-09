import json
import unittest

from jittersim.config import exemptions
from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.networking import applier as applier_module
from jittersim.networking.applier import ApplyError, NetworkApplier
from jittersim.networking.commands import CommandResult
from jittersim.networking.executor import CommandExecutionError, FakeExecutor
from jittersim.networking.topology import Topology


def make_plan():
    profile = Profile(
        id="slow",
        name="Slow",
        upload=Impairment(delay_ms=300),
        download=Impairment(loss_percent=10),
    )
    return compile_plan(
        devices=(Device("alice", "Alice", "192.168.50.10", "slow"),),
        profiles={"slow": profile},
    )


class FailingResetExecutor(FakeExecutor):
    def run(self, command):
        if "project-owned nftables table" in command.purpose:
            self.commands.append(command)
            raise CommandExecutionError("simulated reset failure")
        return super().run(command)


class PurposeFailingExecutor(FakeExecutor):
    """Fail every command whose purpose contains a text, like a missing kernel feature."""

    def __init__(self, purpose, *, soft=False):
        super().__init__()
        self.purpose = purpose
        self.soft = soft

    def run(self, command):
        if self.purpose in command.purpose:
            self.commands.append(command)
            if self.soft or command.ignore_failure:
                return CommandResult(return_code=2, stderr="not supported")
            raise CommandExecutionError("simulated missing kernel feature")
        return super().run(command)


class RootQueueExecutor(FakeExecutor):
    def __init__(self, *, handle="10:", kind="prio", fail_on_index=None):
        super().__init__(fail_on_index=fail_on_index)
        self.handle = handle
        self.kind = kind

    def run(self, command):
        if command.arguments[:3] == ("-j", "qdisc", "show"):
            self.commands.append(command)
            return CommandResult(
                return_code=0,
                stdout=json.dumps([{"handle": self.handle, "kind": self.kind}]),
            )
        return super().run(command)


class MissingInterfaceExecutor(RootQueueExecutor):
    """The test Wi-Fi interface is gone (Wi-Fi disabled, firmware crash, unplugged)."""

    def __init__(self, *, stderr='Cannot find device "wlan0"\n'):
        super().__init__()
        self.stderr = stderr

    def run(self, command):
        if command.arguments[:3] == ("-j", "qdisc", "show") and "wlan0" in command.arguments:
            self.commands.append(command)
            if command.ignore_failure:
                return CommandResult(return_code=1, stderr=self.stderr)
            raise CommandExecutionError("tc failed", return_code=1, stderr=self.stderr)
        return super().run(command)


class GatewayProbeExecutor(FakeExecutor):
    def __init__(self, *, gateway="192.168.0.1", ping_return_code=0) -> None:
        super().__init__()
        self.gateway = gateway
        self.ping_return_code = ping_return_code

    def run(self, command):
        self.commands.append(command)
        if command.executable == "nmcli":
            return CommandResult(return_code=0, stdout="router-ethernet\n")
        if command.executable == "ip":
            route = f"default via {self.gateway} dev eth0\n" if self.gateway else ""
            return CommandResult(return_code=0, stdout=route)
        if command.executable == "ping":
            return CommandResult(return_code=self.ping_return_code, stderr="probe result")
        return CommandResult(return_code=0)


QUEUE_SAMPLE = json.dumps(
    [
        {
            "kind": "prio",
            "handle": "10:",
            "root": True,
            "options": {"bands": 3},
            "bytes": 100,
            "packets": 2,
            "drops": 0,
            "overlimits": 0,
            "requeues": 0,
            "backlog": 0,
            "qlen": 0,
        },
        {
            "kind": "netem",
            "handle": "11:",
            "parent": "10:2",
            "options": {"limit": 1000, "loss-random": {"loss": 0.2, "correlation": 0}},
            "bytes": 1500,
            "packets": 10,
            "drops": 3,
            "overlimits": 0,
            "requeues": 0,
            "backlog": 0,
            "qlen": 0,
        },
    ]
)


class QueueStatsExecutor(FakeExecutor):
    """Answer the queue counter command with canned output per interface."""

    def __init__(self, outputs):
        super().__init__()
        self.outputs = outputs

    def run(self, command):
        if command.executable == "tc" and command.arguments[:3] == ("-s", "-j", "qdisc"):
            self.commands.append(command)
            return CommandResult(return_code=0, stdout=self.outputs[command.arguments[-1]])
        return super().run(command)


class NetworkApplierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.topology = Topology(uplink_interface="eth0", test_interface="wlan0")

    def test_applies_every_rendered_command(self) -> None:
        executor = FakeExecutor()
        report = NetworkApplier(executor=executor, topology=self.topology).apply(plan=make_plan())

        # Four inspections first: both root queues, then the Wi-Fi intake and
        # the ifb device, which a plan without upload corruption does not use.
        self.assertEqual(report.commands_run, len(executor.commands) - 4)
        self.assertFalse(report.upload_corruption_applied)

    def test_upload_corruption_runs_on_the_ifb_device(self) -> None:
        executor = FakeExecutor()
        corrupting = compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "bad"),),
            profiles={"bad": Profile(id="bad", name="Bad",
                                     upload=Impairment(delay_ms=10, corrupt_percent=6))},
        )
        report = NetworkApplier(executor=executor, topology=self.topology).apply(plan=corrupting)

        self.assertTrue(report.upload_corruption_applied)
        argv = [command.argv for command in executor.commands]
        self.assertIn(("ip", "link", "add", "name", "jittersim-ifb", "type", "ifb"), argv)
        (on_ifb,) = [a for a in argv if "jittersim-ifb" in a and "netem" in a]
        self.assertEqual(on_ifb[on_ifb.index("netem"):],
                         ("netem", "corrupt", "6%", "rate", "0bit"))
        (on_uplink,) = [a for a in argv if "eth0" in a and "netem" in a]
        self.assertIn("delay", on_uplink)
        self.assertEqual(on_uplink[on_uplink.index("corrupt") + 1], "0%")
        self.assertGreater(report.commands_run, 0)

    def test_dns_capture_without_exclusions_keeps_the_old_filter(self) -> None:
        executor = FakeExecutor()
        NetworkApplier(executor=executor, topology=self.topology).capture_dns_queries()

        arguments = executor.commands[-1].arguments

        self.assertEqual(arguments[-7:], ("udp", "port", "53", "or", "tcp", "port", "53"))
        self.assertNotIn("ether", arguments)
        self.assertNotIn("(", arguments)

    def test_dns_capture_never_sees_protected_clients(self) -> None:
        executor = FakeExecutor()
        NetworkApplier(executor=executor, topology=self.topology).capture_dns_queries(
            ["02:00:00:00:00:02", "02:00:00:00:00:01"]
        )

        arguments = executor.commands[-1].arguments
        tail = arguments[arguments.index("(") :]

        self.assertEqual(
            tail,
            (
                "(", "udp", "port", "53", "or", "tcp", "port", "53", ")",
                "and", "not", "ether", "host", "02:00:00:00:00:01",
                "and", "not", "ether", "host", "02:00:00:00:00:02",
            ),
        )

    def test_dns_capture_normalizes_and_deduplicates_excluded_addresses(self) -> None:
        executor = FakeExecutor()
        NetworkApplier(executor=executor, topology=self.topology).capture_dns_queries(
            ["02-00-00-00-00-01", "02:00:00:00:00:01", "02:00:00:00:00:AA"]
        )

        arguments = executor.commands[-1].arguments

        self.assertEqual(arguments.count("ether"), 2)
        self.assertIn("02:00:00:00:00:aa", arguments)

    def test_the_exclusion_limit_is_the_size_of_the_protected_list(self) -> None:
        self.assertEqual(applier_module.MAX_PROTECTED_MACS, exemptions.MAX_UNAFFECTED_DEVICES)
        self.assertEqual(applier_module.MAX_PROTECTED_MACS, 256)

    def test_dns_capture_keeps_every_term_for_the_largest_protected_list(self) -> None:
        macs = ["02:00:00:00:%02x:%02x" % (index // 256, index % 256) for index in range(256)]
        executor = FakeExecutor()
        NetworkApplier(executor=executor, topology=self.topology).capture_dns_queries(
            list(reversed(macs))
        )

        arguments = executor.commands[-1].arguments
        expected = ["(", "udp", "port", "53", "or", "tcp", "port", "53", ")"]
        for mac in sorted(macs):
            expected += ["and", "not", "ether", "host", mac]

        self.assertEqual(arguments[arguments.index("(") :], tuple(expected))
        self.assertEqual(arguments.count("ether"), 256)
        self.assertEqual(arguments[-1], "02:00:00:00:00:ff")
        self.assertEqual(arguments[-1], sorted(macs)[-1])

    def test_dns_capture_rejects_a_bad_or_oversized_exclusion_list(self) -> None:
        executor = FakeExecutor()
        applier = NetworkApplier(executor=executor, topology=self.topology)

        with self.assertRaises(ValueError):
            applier.capture_dns_queries(["not-a-mac"])
        with self.assertRaises(ValueError):
            applier.capture_dns_queries(
                ["02:00:00:00:%02x:%02x" % (index // 256, index % 256) for index in range(257)]
            )
        applier.capture_dns_queries(
            ["02:00:00:00:%02x:%02x" % (index // 256, index % 256) for index in range(256)]
        )
        self.assertEqual(len(executor.commands), 1)
    def test_gateway_probe_targets_only_the_default_router_on_the_uplink(self) -> None:
        executor = GatewayProbeExecutor()
        applier = NetworkApplier(executor=executor, topology=self.topology)

        self.assertTrue(applier.probe_default_gateway())
        probe = executor.commands[-1]
        self.assertEqual(probe.executable, "ping")
        self.assertEqual(
            probe.arguments,
            ("-n", "-c", "1", "-W", "1", "-I", "eth0", "192.168.0.1"),
        )
        self.assertEqual(probe.timeout_seconds, 2)

    def test_gateway_probe_reports_unknown_without_a_route(self) -> None:
        executor = GatewayProbeExecutor(gateway=None)
        applier = NetworkApplier(executor=executor, topology=self.topology)

        self.assertIsNone(applier.probe_default_gateway())
        self.assertFalse(any(command.executable == "ping" for command in executor.commands))

    def test_gateway_probe_distinguishes_no_reply_from_probe_error(self) -> None:
        no_reply = NetworkApplier(
            executor=GatewayProbeExecutor(ping_return_code=1), topology=self.topology
        )
        self.assertFalse(no_reply.probe_default_gateway())

        probe_error = NetworkApplier(
            executor=GatewayProbeExecutor(ping_return_code=2), topology=self.topology
        )
        with self.assertRaises(CommandExecutionError):
            probe_error.probe_default_gateway()

    def test_reset_runs_project_cleanup_commands(self) -> None:
        executor = RootQueueExecutor()
        commands_run = NetworkApplier(executor=executor, topology=self.topology).reset()

        # Two root inspections, then both project tables and both root queues,
        # then the Wi-Fi intake and the ifb device are looked for.
        self.assertEqual(commands_run, 4)
        self.assertEqual(
            [command.executable for command in executor.commands[2:]],
            ["nft", "nft", "tc", "tc", "tc", "ip"],
        )

    def test_reset_succeeds_when_the_test_wifi_interface_is_missing(self) -> None:
        # 4 October: Normal access failed "while inspecting the absent wlan0 root
        # queue". A missing interface has no project queue to remove.
        executor = MissingInterfaceExecutor()
        NetworkApplier(executor=executor, topology=self.topology).reset()

        removed = [command.arguments[3] for command in executor.commands
                   if command.arguments[:2] == ("qdisc", "del")]
        self.assertEqual(removed, ["eth0"])

    def test_reset_still_fails_on_other_root_queue_errors(self) -> None:
        executor = MissingInterfaceExecutor(stderr="RTNETLINK answers: Operation not permitted\n")
        with self.assertRaises(CommandExecutionError):
            NetworkApplier(executor=executor, topology=self.topology).reset()

    def test_reset_leaves_a_foreign_root_queue_alone(self) -> None:
        executor = RootQueueExecutor(handle="20:", kind="htb")
        commands_run = NetworkApplier(executor=executor, topology=self.topology).reset()

        self.assertEqual(commands_run, 2)
        self.assertFalse(any(command.arguments[:2] == ("qdisc", "del") for command in executor.commands))

    def test_a_plan_without_queues_removes_the_project_roots(self) -> None:
        executor = RootQueueExecutor()
        plan = compile_plan(devices=(), profiles={})

        NetworkApplier(executor=executor, topology=self.topology).apply(plan=plan)

        removed = [
            command.argv for command in executor.commands if command.arguments[:2] == ("qdisc", "del")
        ]
        self.assertEqual(
            removed,
            [
                ("tc", "qdisc", "del", "dev", "eth0", "root", "handle", "10:"),
                ("tc", "qdisc", "del", "dev", "wlan0", "root", "handle", "10:"),
            ],
        )
        # The nft script replaces the table in one transaction, so deny and
        # block rules never disappear, even for a moment.
        self.assertEqual(
            [command.argv for command in executor.commands if command.executable == "nft"],
            [("nft", "-f", "-")],
        )

    def test_apply_rejects_a_foreign_root_queue_before_mutation(self) -> None:
        executor = RootQueueExecutor(handle="20:", kind="htb")
        with self.assertRaisesRegex(ApplyError, "could not safely change"):
            NetworkApplier(executor=executor, topology=self.topology).apply(plan=make_plan())
        self.assertFalse(any(command.executable == "nft" for command in executor.commands))

    def test_failed_apply_attempts_cleanup(self) -> None:
        executor = FakeExecutor(fail_on_index=4)
        applier = NetworkApplier(executor=executor, topology=self.topology)

        with self.assertRaisesRegex(ApplyError, "cleanup completed") as context:
            applier.apply(plan=make_plan())

        self.assertEqual(context.exception.failed_command_index, 0)
        self.assertIsNone(context.exception.reset_error)
        purposes = [command.purpose for command in executor.commands]
        self.assertIn("remove the project-owned download table", purposes)
        self.assertEqual(purposes[-1], "check whether jittersim-ifb exists")

    def test_an_unusable_ifb_falls_back_to_safe_uploads_on_the_uplink(self) -> None:
        # The intake's tc action is missing: the plan is applied again with
        # upload queues on the uplink and no corruption, never with damage.
        executor = PurposeFailingExecutor("restore each connection's upload class")
        applier = NetworkApplier(executor=executor, topology=self.topology)
        damaging = compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "bad"),),
            profiles={"bad": Profile(id="bad", name="Bad",
                                     upload=Impairment(delay_ms=10, corrupt_percent=6,
                                                       duplicate_percent=7))},
        )

        report = applier.apply(plan=damaging)

        self.assertFalse(report.upload_corruption_applied)
        netems = [command.arguments for command in executor.commands
                  if "netem" in command.arguments and "eth0" in command.arguments]
        self.assertTrue(netems)
        for arguments in netems:
            self.assertEqual(arguments[arguments.index("corrupt") + 1], "0%")
            self.assertNotIn("duplicate", arguments)
        # Later applies go straight to the fallback.
        executor.commands.clear()
        self.assertFalse(applier.apply(plan=damaging).upload_corruption_applied)
        self.assertFalse(any(command.arguments[:2] == ("link", "add")
                             for command in executor.commands))

    def test_a_kernel_without_ifb_uses_the_fallback(self) -> None:
        executor = PurposeFailingExecutor("create jittersim-ifb", soft=True)
        corrupting = compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "bad"),),
            profiles={"bad": Profile(id="bad", name="Bad", upload=Impairment(corrupt_percent=6))},
        )
        report = NetworkApplier(executor=executor, topology=self.topology).apply(plan=corrupting)

        self.assertFalse(report.upload_corruption_applied)
        self.assertTrue(any("eth0" in command.arguments and "netem" in command.arguments
                            for command in executor.commands))

    def test_reports_when_cleanup_also_fails(self) -> None:
        executor = FailingResetExecutor(fail_on_index=4)
        applier = NetworkApplier(executor=executor, topology=self.topology)

        with self.assertRaisesRegex(ApplyError, "cleanup also failed") as context:
            applier.apply(plan=make_plan())

        self.assertIsNotNone(context.exception.reset_error)


    def test_queue_stats_reads_both_interfaces_and_keys_them_by_name(self) -> None:
        executor = QueueStatsExecutor({"eth0": QUEUE_SAMPLE, "wlan0": "[]"})

        stats = NetworkApplier(executor=executor, topology=self.topology).queue_stats()

        self.assertEqual(list(stats), ["eth0", "wlan0"])
        self.assertEqual(stats["eth0"], json.loads(QUEUE_SAMPLE))
        self.assertEqual(stats["wlan0"], [])
        self.assertEqual(
            [(command.executable, command.arguments) for command in executor.commands],
            [
                ("tc", ("-s", "-j", "qdisc", "show", "dev", "eth0")),
                ("tc", ("-s", "-j", "qdisc", "show", "dev", "wlan0")),
            ],
        )

    def test_queue_stats_rejects_output_that_is_not_json(self) -> None:
        executor = QueueStatsExecutor({"eth0": "not json", "wlan0": "[]"})

        with self.assertRaisesRegex(CommandExecutionError, "not JSON"):
            NetworkApplier(executor=executor, topology=self.topology).queue_stats()

    def test_queue_stats_rejects_a_json_object(self) -> None:
        executor = QueueStatsExecutor({"eth0": "[]", "wlan0": json.dumps({"kind": "prio"})})

        with self.assertRaisesRegex(CommandExecutionError, "unexpected queue counters"):
            NetworkApplier(executor=executor, topology=self.topology).queue_stats()

    def test_queue_stats_rejects_oversized_output(self) -> None:
        huge = json.dumps([{"kind": "x" * (600 * 1024)}])
        executor = QueueStatsExecutor({"eth0": huge, "wlan0": "[]"})

        with self.assertRaisesRegex(CommandExecutionError, "too large"):
            NetworkApplier(executor=executor, topology=self.topology).queue_stats()


if __name__ == "__main__":
    unittest.main()
