import subprocess
import unittest
from unittest.mock import patch

from jittersim.networking.commands import Command
from jittersim.networking.executor import CommandExecutionError, SubprocessExecutor


class SubprocessExecutorTests(unittest.TestCase):
    @patch("jittersim.networking.executor.subprocess.run")
    def test_allows_the_fixed_wifi_station_inspection_command(self, run_command) -> None:
        run_command.return_value = subprocess.CompletedProcess(
            args=("iw", "dev", "wlan0", "station", "dump"),
            returncode=0,
            stdout="Station aa:bb:cc:dd:ee:ff (on wlan0)\n",
            stderr="",
        )
        command = Command(
            executable="iw",
            arguments=("dev", "wlan0", "station", "dump"),
            purpose="inspect Wi-Fi clients",
            timeout_seconds=5,
        )

        result = SubprocessExecutor().run(command)

        self.assertIn("Station aa:bb:cc:dd:ee:ff", result.stdout)
        run_command.assert_called_once_with(
            ("iw", "dev", "wlan0", "station", "dump"),
            input=None,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )

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
    def test_only_a_command_that_asks_for_it_runs_with_the_english_locale(self, run_command) -> None:
        run_command.return_value = subprocess.CompletedProcess(
            args=("nmcli",), returncode=0, stdout="", stderr="")
        english = Command("nmcli", ("-g", "GENERAL.STATE", "device", "show", "wlan0"),
                          "read the state", c_locale=True)
        plain = Command("nmcli", ("-g", "GENERAL.STATE", "device", "show", "wlan0"),
                        "read the state")

        with patch.dict("os.environ", {"LC_ALL": "de_DE.UTF-8", "LANGUAGE": "de", "PATH": "/bin"},
                        clear=True):
            SubprocessExecutor().run(english)
            SubprocessExecutor().run(plain)

        asked, ignored = run_command.call_args_list
        # The rest of the environment is kept. A command that does not ask inherits it.
        self.assertEqual(asked.kwargs["env"], {"LC_ALL": "C", "LANGUAGE": "de", "PATH": "/bin"})
        self.assertNotIn("env", ignored.kwargs)

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

    @patch("jittersim.networking.executor.subprocess.run")
    def test_returns_partial_output_when_timeout_is_an_expected_end(self, run_command) -> None:
        run_command.side_effect = subprocess.TimeoutExpired(
            cmd=("tcpdump",), timeout=2, output="partial packet\n"
        )
        command = Command(
            "tcpdump",
            ("-c", "128"),
            "sample DNS queries",
            timeout_seconds=2,
            timeout_is_success=True,
        )

        result = SubprocessExecutor(timeout_seconds=10).run(command)

        self.assertEqual(result.return_code, 0)
        self.assertEqual(result.stdout, "partial packet\n")
        run_command.assert_called_once_with(
            ("tcpdump", "-c", "128"),
            input=None,
            text=True,
            capture_output=True,
            timeout=2,
            check=False,
        )

    @patch("jittersim.networking.executor.subprocess.run")
    def test_reports_missing_executables(self, run_command) -> None:
        run_command.side_effect = FileNotFoundError("tc not found")

        with self.assertRaisesRegex(CommandExecutionError, "could not start") as context:
            SubprocessExecutor().run(Command("tc", ("qdisc",), "apply queue"))

        self.assertIn("tc not found", context.exception.stderr)


if __name__ == "__main__":
    unittest.main()
