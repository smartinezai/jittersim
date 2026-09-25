import unittest

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.networking.applier import ApplyError, NetworkApplier
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


class NetworkApplierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.topology = Topology(uplink_interface="eth0", test_interface="wlan0")

    def test_applies_every_rendered_command(self) -> None:
        executor = FakeExecutor()
        report = NetworkApplier(executor=executor, topology=self.topology).apply(plan=make_plan())

        self.assertEqual(report.commands_run, len(executor.commands))
        self.assertGreater(report.commands_run, 0)

    def test_reset_runs_project_cleanup_commands(self) -> None:
        executor = FakeExecutor()
        commands_run = NetworkApplier(executor=executor, topology=self.topology).reset()

        self.assertEqual(commands_run, 3)
        self.assertEqual(executor.commands[0].executable, "nft")
        self.assertEqual([command.executable for command in executor.commands[1:]], ["tc", "tc"])

    def test_failed_apply_attempts_cleanup(self) -> None:
        executor = FakeExecutor(fail_on_index=1)
        applier = NetworkApplier(executor=executor, topology=self.topology)

        with self.assertRaisesRegex(ApplyError, "cleanup completed") as context:
            applier.apply(plan=make_plan())

        self.assertEqual(context.exception.failed_command_index, 1)
        self.assertIsNone(context.exception.reset_error)
        self.assertEqual(executor.commands[-3].purpose, "remove the project-owned nftables table")

    def test_reports_when_cleanup_also_fails(self) -> None:
        executor = FailingResetExecutor(fail_on_index=1)
        applier = NetworkApplier(executor=executor, topology=self.topology)

        with self.assertRaisesRegex(ApplyError, "cleanup also failed") as context:
            applier.apply(plan=make_plan())

        self.assertIsNotNone(context.exception.reset_error)


if __name__ == "__main__":
    unittest.main()
