import json
import unittest

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
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


class NetworkApplierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.topology = Topology(uplink_interface="eth0", test_interface="wlan0")

    def test_applies_every_rendered_command(self) -> None:
        executor = FakeExecutor()
        report = NetworkApplier(executor=executor, topology=self.topology).apply(plan=make_plan())

        self.assertEqual(report.commands_run, len(executor.commands) - 2)
        self.assertGreater(report.commands_run, 0)

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

        self.assertEqual(commands_run, 3)
        self.assertEqual(executor.commands[2].executable, "nft")
        self.assertEqual([command.executable for command in executor.commands[3:]], ["tc", "tc"])

    def test_reset_leaves_a_foreign_root_queue_alone(self) -> None:
        executor = RootQueueExecutor(handle="20:", kind="htb")
        commands_run = NetworkApplier(executor=executor, topology=self.topology).reset()

        self.assertEqual(commands_run, 1)
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
        executor = FakeExecutor(fail_on_index=3)
        applier = NetworkApplier(executor=executor, topology=self.topology)

        with self.assertRaisesRegex(ApplyError, "cleanup completed") as context:
            applier.apply(plan=make_plan())

        self.assertEqual(context.exception.failed_command_index, 1)
        self.assertIsNone(context.exception.reset_error)
        self.assertEqual(executor.commands[-1].purpose, "remove the project-owned nftables table")

    def test_reports_when_cleanup_also_fails(self) -> None:
        executor = FailingResetExecutor(fail_on_index=3)
        applier = NetworkApplier(executor=executor, topology=self.topology)

        with self.assertRaisesRegex(ApplyError, "cleanup also failed") as context:
            applier.apply(plan=make_plan())

        self.assertIsNotNone(context.exception.reset_error)


if __name__ == "__main__":
    unittest.main()
