"""Protect password writes and the recovery decision from false success."""

import unittest

from jittersim.networking.commands import CommandResult
from jittersim.networking.executor import CommandExecutionError
from jittersim.networking.wifi import WifiController, WifiError
from tests.wifi_support import WifiExecutor


class WifiSecretTests(unittest.TestCase):
    def test_password_uses_private_input_and_exact_check_before_activation(self):
        executor = WifiExecutor()
        password = ' leading " # \\ $ ` ; trailing '
        WifiController(executor, "wlan0").configure("Manual", password)
        for command in executor.commands:
            self.assertNotIn(password, str(command.argv))
            self.assertNotIn(password, repr(command))
        write = next(command for command in executor.commands if "edit" in command.arguments)
        check = next(command for command in executor.commands if command.expected_stdout is not None)
        self.assertTrue(write.sensitive)
        self.assertEqual(write.stdin.splitlines()[1], password)
        self.assertEqual(check.expected_stdout, password + "\n")
        activation = next(command for command in executor.commands if "up" in command.arguments)
        self.assertLess(executor.commands.index(check), executor.commands.index(activation))

    def test_editor_zero_with_old_password_restores_without_new_activation(self):
        class RefusingEditor(WifiExecutor):
            def run(self, command):
                if "edit" in command.arguments:
                    self.commands.append(command)
                    return CommandResult(0)
                return super().run(command)

        executor = RefusingEditor()
        before = dict(executor.settings)
        with self.assertRaises(WifiError) as caught:
            WifiController(executor, "wlan0").configure("New", "new-password")
        self.assertEqual(caught.exception.reason, "wifi-restored")
        self.assertEqual(executor.settings, {**before, "connection.autoconnect": "no"})
        self.assertEqual(executor.calls["up"], 1)
        self.assertFalse(executor.backups)

    def test_editor_failure_restores_and_keeps_secrets_out_of_error(self):
        executor = WifiExecutor()
        before = dict(executor.settings)
        executor.failures.add(("edit", 1))
        with self.assertRaises(WifiError) as caught:
            WifiController(executor, "wlan0").configure("New", "new-password")
        self.assertEqual(caught.exception.reason, "wifi-restored")
        self.assertEqual(executor.settings, {**before, "connection.autoconnect": "no"})
        self.assertNotIn("fixture-wifi-password", str(caught.exception))

    def test_direct_controller_rejects_input_injection_before_any_command(self):
        for password in (None, "short", "password\nquit", "password\rquit", "passwörd", "x" * 64):
            with self.subTest(password=password):
                executor = WifiExecutor()
                with self.assertRaises(ValueError):
                    WifiController(executor, "wlan0").configure("Manual", password)
                self.assertFalse(executor.commands)

    def test_saved_then_interrupted_write_is_restored_without_retrying_secret(self):
        class InterruptedEditor(WifiExecutor):
            def run(self, command):
                result = super().run(command)
                if "edit" in command.arguments:
                    raise CommandExecutionError("private networking command failed", timed_out=True)
                return result

        executor = InterruptedEditor()
        before = dict(executor.settings)
        with self.assertRaises(WifiError) as caught:
            WifiController(executor, "wlan0").configure("New", "new-password")
        self.assertEqual(caught.exception.reason, "wifi-restored")
        self.assertEqual(executor.settings, {**before, "connection.autoconnect": "no"})
        self.assertEqual(executor.calls["edit"], 1)
        self.assertEqual(executor.calls["up"], 1)
