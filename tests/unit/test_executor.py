import unittest

from jittersim.networking.commands import Command, CommandError
from jittersim.networking.executor import CommandExecutionError, FakeExecutor


class CommandTests(unittest.TestCase):
    def test_exposes_an_argument_vector_without_shell_parsing(self) -> None:
        command = Command(
            executable="tc",
            arguments=("qdisc", "show", "dev", "eth0"),
            purpose="inspect the test interface",
        )

        self.assertEqual(command.argv, ("tc", "qdisc", "show", "dev", "eth0"))

    def test_rejects_shell_style_executable_values(self) -> None:
        with self.assertRaises(CommandError):
            Command(
                executable="tc; rm -rf /",
                arguments=("qdisc",),
                purpose="invalid command",
            )


class FakeExecutorTests(unittest.TestCase):
    def test_records_commands_in_order(self) -> None:
        executor = FakeExecutor()
        first = Command("nft", ("list", "table", "inet", "jittersim"), "inspect rules")
        second = Command("tc", ("qdisc", "show", "dev", "eth0"), "inspect queues")

        executor.run(first)
        executor.run(second)

        self.assertEqual(executor.commands, [first, second])

    def test_can_simulate_failure_at_a_specific_command(self) -> None:
        executor = FakeExecutor(fail_on_index=1)
        first = Command("ip", ("link", "show"), "inspect links")
        second = Command("nft", ("-f", "-"), "apply rules")

        executor.run(first)
        with self.assertRaisesRegex(CommandExecutionError, "command 1"):
            executor.run(second)

        self.assertEqual(executor.commands, [first, second])


if __name__ == "__main__":
    unittest.main()
