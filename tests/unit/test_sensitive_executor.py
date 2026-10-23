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
            # Shell builtins keep the pipe/privacy checks independent of Python
            # interpreter startup under load; timeout behavior is still real.
            executable.write_text("#!/bin/sh\n" + body)
            executable.chmod(0o700)
            with patch.dict(os.environ, {"PATH": directory + os.pathsep + os.environ["PATH"]}):
                return SubprocessExecutor().run(command)

    def test_input_is_private_and_child_cannot_write_normal_editor_history(self):
        command = Command("nmcli", ("connection", "edit", "id", "jittersim-ap"),
                          "private write", stdin="private-fixture-value", sensitive=True,
                          timeout_seconds=2)
        result = self.run_fixture(
            'test "$HOME" = /dev/null || exit 1\n'
            'test "$XDG_CACHE_HOME" = /dev/null || exit 1\n'
            'value=$(cat)\n'
            'case "$*" in *"$value"*) exit 1 ;; esac\n'
            'printf "%s" "$value"\nprintf "%s" "$value" >&2\n', command)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        self.assertNotIn("private-fixture-value", repr(command))

    def test_exact_checked_output_is_not_returned_and_spaces_are_preserved(self):
        command = Command("nmcli", ("connection", "show"), "private check", sensitive=True,
                          expected_stdout=" fixture-secret \n", timeout_seconds=2)
        result = self.run_fixture("printf ' fixture-secret \\n'\n", command)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("fixture-secret", repr(command))
        with self.assertRaises(CommandExecutionError):
            self.run_fixture("printf 'fixture-secret\\n'\n", command)

    def test_failure_timeout_and_excess_output_have_no_secret_context(self):
        # Only the sleeping child needs the short timeout. The other two end by themselves, so a
        # slow child start under load must not turn them into timeouts.
        def make(timeout_seconds):
            return Command("nmcli", ("connection", "show"), "private check", sensitive=True,
                           expected_stdout="private-fixture-value\n",
                           timeout_seconds=timeout_seconds)
        for body, timed_out, command in (
            ("printf 'private-fixture-value\\n' >&2\nexit 1\n", False, make(1)),
            ("printf 'private-fixture-value\\n'\nexec sleep 10\n", True, make(0.2)),
            ("i=0; while [ \"$i\" -lt 1000 ]; do printf '%1000s' x; i=$((i + 1)); done\n"
             "exec sleep 10\n", False, make(1)),
        ):
            with self.subTest(body=body):
                started = time.monotonic()
                with self.assertRaises(CommandExecutionError) as caught:
                    self.run_fixture(body, command)
                self.assertLess(time.monotonic() - started, 2)
                error = caught.exception
                self.assertEqual(error.timed_out, timed_out)
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
