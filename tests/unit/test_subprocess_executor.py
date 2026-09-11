import subprocess
import unittest
from unittest.mock import patch

from jittersim.networking.commands import Command
from jittersim.networking.executor import CommandExecutionError, SubprocessExecutor


class SubprocessExecutorTests(unittest.TestCase):
    @patch("jittersim.networking.executor.subprocess.run")
    def test_passes_argument_vector_and_stdin_without_a_shell(self, run_command) -> None:
        run_command.return_value = subprocess.CompletedProcess(
            args=("nft", "-f", "-"),
            returncode=0,
            stdout="ok",
            stderr="",
        )
        command = Command(
            executable="nft",
            arguments=("-f", "-"),
            purpose="apply rules",
            stdin="add table inet jittersim\n",
        )

        result = SubprocessExecutor(timeout_seconds=3).run(command)

        run_command.assert_called_once_with(
            ("nft", "-f", "-"),
            input="add table inet jittersim\n",
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
        self.assertEqual(result.stdout, "ok")

    @patch("jittersim.networking.executor.subprocess.run")
    def test_rejects_non_networking_executables(self, run_command) -> None:
        command = Command("sh", ("-c", "echo unsafe"), "unsafe command")

        with self.assertRaisesRegex(CommandExecutionError, "unsupported networking executable"):
            SubprocessExecutor().run(command)

        run_command.assert_not_called()

    @patch("jittersim.networking.executor.subprocess.run")
    def test_returns_an_allowed_nonzero_result_for_cleanup(self, run_command) -> None:
        run_command.return_value = subprocess.CompletedProcess(
            args=("nft", "delete"),
            returncode=1,
            stdout="",
            stderr="table does not exist",
        )
        command = Command(
            executable="nft",
            arguments=("delete", "table", "inet", "jittersim"),
            purpose="remove old table",
            ignore_failure=True,
        )

        result = SubprocessExecutor().run(command)

        self.assertEqual(result.return_code, 1)
        self.assertEqual(result.stderr, "table does not exist")

    @patch("jittersim.networking.executor.subprocess.run")
    def test_reports_command_failures_with_captured_output(self, run_command) -> None:
        run_command.return_value = subprocess.CompletedProcess(
            args=("tc", "qdisc"),
            returncode=2,
            stdout="",
            stderr="invalid qdisc",
        )
        command = Command("tc", ("qdisc",), "apply queue")

        with self.assertRaisesRegex(CommandExecutionError, "exit code 2") as context:
            SubprocessExecutor().run(command)

        self.assertEqual(context.exception.stderr, "invalid qdisc")
        self.assertEqual(context.exception.return_code, 2)

    @patch("jittersim.networking.executor.subprocess.run")
    def test_reports_timeouts(self, run_command) -> None:
        run_command.side_effect = subprocess.TimeoutExpired(cmd=("tc",), timeout=10)

        with self.assertRaisesRegex(CommandExecutionError, "timed out") as context:
            SubprocessExecutor().run(Command("tc", ("qdisc",), "apply queue"))

        self.assertTrue(context.exception.timed_out)


if __name__ == "__main__":
    unittest.main()
