"""Exercise the real pipe boundary with a harmless owned executable."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from jittersim.networking.commands import Command, CommandError
from jittersim.networking.executor import CommandExecutionError, SubprocessExecutor


class SensitiveExecutorTests(unittest.TestCase):
    def run_fixture(self, body, command):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "nmcli"
            executable.write_text("#!/usr/bin/env python3\nimport os, sys, time\n" + body)
            executable.chmod(0o700)
            with patch.dict(os.environ, {"PATH": directory + os.pathsep + os.environ["PATH"]}):
                return SubprocessExecutor().run(command)

    def test_input_is_private_and_child_cannot_write_normal_editor_history(self):
        command = Command("nmcli", ("connection", "edit", "id", "jittersim-ap"),
                          "private write", stdin="private-fixture-value", sensitive=True,
                          timeout_seconds=2)
        result = self.run_fixture(
            "assert os.environ['HOME'] == '/dev/null'\n"
            "assert os.environ['XDG_CACHE_HOME'] == '/dev/null'\n"
            "value = sys.stdin.read()\n"
            "assert value not in ' '.join(sys.argv)\n"
            "print(value)\nprint(value, file=sys.stderr)\n", command)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        self.assertNotIn("private-fixture-value", repr(command))

    def test_exact_checked_output_is_not_returned_and_spaces_are_preserved(self):
        command = Command("nmcli", ("connection", "show"), "private check", sensitive=True,
                          expected_stdout=" fixture-secret \n", timeout_seconds=2)
        result = self.run_fixture("sys.stdout.write(' fixture-secret \\n')\n", command)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("fixture-secret", repr(command))
        with self.assertRaises(CommandExecutionError):
            self.run_fixture("sys.stdout.write('fixture-secret\\n')\n", command)

    def test_failure_timeout_and_excess_output_have_no_secret_context(self):
        command = Command("nmcli", ("connection", "show"), "private check", sensitive=True,
                          expected_stdout="private-fixture-value\n", timeout_seconds=0.2)
        for body in (
            "print('private-fixture-value', file=sys.stderr)\nsys.exit(1)\n",
            "print('private-fixture-value', flush=True)\ntime.sleep(10)\n",
            "sys.stdout.write('x' * 1000000)\nsys.stdout.flush()\ntime.sleep(10)\n",
        ):
            with self.subTest(body=body):
                started = time.monotonic()
                with self.assertRaises(CommandExecutionError) as caught:
                    self.run_fixture(body, command)
                self.assertLess(time.monotonic() - started, 2)
                error = caught.exception
                self.assertEqual((error.stdout, error.stderr), ("", ""))
                self.assertIsNone(error.__context__)
                self.assertNotIn("private-fixture-value", str(error))

    def test_cannot_start_does_not_expose_original_error(self):
        with patch("jittersim.networking.executor.subprocess.Popen",
                   side_effect=OSError("private-fixture-value")):
            with self.assertRaises(CommandExecutionError) as caught:
                SubprocessExecutor().run(Command("nmcli", ("connection", "edit"),
                                                 "private write", sensitive=True))
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(caught.exception.stderr, "")

    def test_private_commands_cannot_ignore_failures_or_mix_input_and_output(self):
        for options in ({"ignore_failure": True}, {"timeout_is_success": True,
                        "timeout_seconds": 1}, {"stdin": "value", "expected_stdout": "value"}):
            with self.subTest(options=options):
                with self.assertRaises(CommandError):
                    Command("nmcli", ("connection", "edit"), "private write", sensitive=True,
                            **options)

    def test_cleanup_failures_are_private_and_do_not_skip_other_pipe_cleanup(self):
        process = MagicMock()
        process.communicate.side_effect = OSError("private-fixture-value")
        process.poll.return_value = None
        process.kill.side_effect = OSError("private-fixture-value")
        process.wait.side_effect = OSError("private-fixture-value")
        process.stdin.close.side_effect = OSError("private-fixture-value")
        with patch("jittersim.networking.executor.subprocess.Popen", return_value=process):
            with self.assertRaises(CommandExecutionError) as caught:
                SubprocessExecutor().run(Command("nmcli", ("connection", "edit"),
                                                 "private write", stdin="private-fixture-value",
                                                 sensitive=True))
        process.stdin.close.assert_called_once()
        process.stdout.close.assert_called_once()
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual((caught.exception.stdout, caught.exception.stderr), ("", ""))
