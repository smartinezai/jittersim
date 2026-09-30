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


class NetworkApplierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.topology = Topology(uplink_interface="eth0", test_interface="wlan0")

    def test_applies_every_rendered_command(self) -> None:
        executor = FakeExecutor()
        report = NetworkApplier(executor=executor, topology=self.topology).apply(plan=make_plan())

        self.assertEqual(report.commands_run, len(executor.commands) - 2)
        self.assertGreater(report.commands_run, 0)

    def test_reset_runs_project_cleanup_commands(self) -> None:
        executor = RootQueueExecutor()
        commands_run = NetworkApplier(executor=executor, topology=self.topology).reset()

        self.assertEqual(commands_run, 5)
        self.assertEqual(executor.commands[2].executable, "nft")
        self.assertEqual(
            [command.executable for command in executor.commands[3:]], ["nft", "nft", "tc", "tc"]
        )

    def test_reset_reports_a_genuine_obsolete_table_cleanup_failure(self) -> None:
        class FailedLegacyCleanup(RootQueueExecutor):
            def run(self, command):
                if command.purpose == "remove obsolete JitterSim firewall tables":
                    raise CommandExecutionError("simulated permission failure")
                return super().run(command)

        with self.assertRaisesRegex(CommandExecutionError, "permission failure"):
            NetworkApplier(executor=FailedLegacyCleanup(), topology=self.topology).reset()

    def test_reset_leaves_a_foreign_root_queue_alone(self) -> None:
        executor = RootQueueExecutor(handle="20:", kind="htb")
        commands_run = NetworkApplier(executor=executor, topology=self.topology).reset()

        self.assertEqual(commands_run, 3)
        self.assertFalse(any(command.arguments[:2] == ("qdisc", "del") for command in executor.commands))

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

        self.assertEqual(context.exception.failed_command_index, 2)
        self.assertIsNone(context.exception.reset_error)
        self.assertEqual(executor.commands[-1].purpose, "remove obsolete JitterSim firewall tables")

    def test_reports_when_cleanup_also_fails(self) -> None:
        executor = FailingResetExecutor(fail_on_index=4)
        applier = NetworkApplier(executor=executor, topology=self.topology)

        with self.assertRaisesRegex(ApplyError, "cleanup also failed") as context:
            applier.apply(plan=make_plan())

        self.assertIsNotNone(context.exception.reset_error)


if __name__ == "__main__":
    unittest.main()
