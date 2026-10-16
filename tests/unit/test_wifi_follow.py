"""The Pi Wi-Fi follows the last verified router copy and leaves Recovery on its own only at boot."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from jittersim.networking import wifi_follow
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.helper import HelperService
from jittersim.networking.router import _settings_from_wifi_data
from jittersim.networking.topology import Topology
from jittersim.networking.wifi import (
    BOOT_PREVIOUS_CONNECTION,
    RECOVERY_CONNECTION,
    RECOVERY_WIFI_NAME,
    ROUTER_COPY_CONNECTION,
    WifiController,
    WifiError,
)
from tests.unit.test_wifi_security_copy import CopyExecutor

SSID = "802-11-wireless.ssid"
PSK = "802-11-wireless-security.psk"


class FollowRouterTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.hold = Path(directory.name) / "wifi-recovery-hold"
        self.executor = CopyExecutor()
        source = {"SSID": "Home", "Passphrase": "home-test-password", "Enable": True,
                  "ModeEnabled": "WPA2-Personal", "Enable5G": False}

        class Router:
            def login(self):
                pass

            def read_wifi_settings(self):
                return _settings_from_wifi_data(source)

            def close(self):
                pass

        self.applier = NetworkApplier(executor=self.executor, topology=Topology("eth0", "wlan0"))
        self.applier.wifi_hold_path = self.hold
        self.service = HelperService(self.applier, router_factory=lambda *_: Router())
        self.controller = WifiController(self.executor, "wlan0")

    def copy_router(self):
        preview = self.service.handle_payload({"action": "router-preview-ap", "admin_password": "admin-test"})
        result = self.service.handle_payload({"action": "router-sync-ap", "admin_password": "admin-test",
                                             "expected_fingerprint": preview["fingerprint"]})
        self.assertTrue(result["ok"], result)

    def go_to_recovery(self):
        """What the 4 October incident did by hand, through the supported command."""
        return wifi_follow.use_recovery(self.controller, self.hold)

    def commands_since(self, start):
        return [" ".join(command.arguments) for command in self.executor.commands[start:]]

    # Remembering and forgetting the router copy.

    def test_a_verified_router_copy_is_remembered_with_its_password(self):
        self.copy_router()
        remembered = self.executor.backups[ROUTER_COPY_CONNECTION]
        self.assertEqual(remembered[SSID], "Home")
        self.assertEqual(remembered["802-11-wireless-security.key-mgmt"], "wpa-psk")
        self.assertEqual(remembered["802-11-wireless-security.proto"], "rsn")
        self.assertEqual(remembered[PSK], "home-test-password")
        self.assertEqual(remembered["connection.autoconnect"], "no")
        self.assertEqual(self.executor.active, "jittersim-ap")
        # The router password never appears in a command line.
        for command in self.executor.commands:
            if not command.sensitive:
                self.assertNotIn("home-test-password", " ".join(command.arguments))

    def test_a_router_copy_ends_a_recovery_hold(self):
        self.copy_router()
        self.go_to_recovery()
        self.assertTrue(self.hold.exists())
        self.copy_router()
        self.assertFalse(self.hold.exists())
        self.assertEqual(self.executor.settings[SSID], "Home")

    def test_a_manual_change_or_name_reset_forgets_the_router_copy(self):
        for action in ("configure-ap", "wifi-reset"):
            with self.subTest(action=action):
                self.copy_router()
                self.assertIn(ROUTER_COPY_CONNECTION, self.executor.backups)
                payload = {"action": action}
                if action == "configure-ap":
                    payload.update(ssid="Owner choice", password="owner-test-password")
                result = self.service.handle_payload(payload)
                self.assertTrue(result["ok"], result)
                self.assertNotIn(ROUTER_COPY_CONNECTION, self.executor.backups)
                # Nothing turns the owner's choice back into the router copy at boot.
                self.assertEqual(wifi_follow.prepare_boot(self.controller, self.hold), "not-remembered")

    def test_a_manual_change_is_refused_when_the_router_copy_cannot_be_forgotten(self):
        self.copy_router()
        before = dict(self.executor.settings)
        deletes = self.executor.calls.get("delete", 0)
        self.executor.failures.add(("delete", deletes + 1))
        result = self.service.handle_payload({"action": "configure-ap", "ssid": "Owner choice",
                                              "password": "owner-test-password"})
        self.assertFalse(result["ok"])
        self.assertEqual(self.executor.settings, before)
        self.assertIn(ROUTER_COPY_CONNECTION, self.executor.backups)

    def test_a_copy_that_cannot_be_remembered_still_succeeds_and_leaves_no_old_copy(self):
        self.copy_router()
        self.executor.backups[ROUTER_COPY_CONNECTION][SSID] = "Old router name"
        clones = self.executor.calls.get("clone", 0)
        # The first clone is the copy's own safety copy. The second remembers the result.
        self.executor.failures.add(("clone", clones + 2))
        self.copy_router()
        self.assertEqual(self.executor.settings[SSID], "Home")
        self.assertNotIn(ROUTER_COPY_CONNECTION, self.executor.backups)

    # Boot: return to the router copy without contacting the router.

    def test_boot_returns_from_recovery_to_the_router_copy_without_starting_the_radio(self):
        self.copy_router()
        self.controller.rename(RECOVERY_WIFI_NAME)  # An earlier fallback without a hold.
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)
        start = len(self.executor.commands)
        self.assertEqual(wifi_follow.prepare_boot(self.controller, self.hold), "switched")
        self.assertEqual(self.executor.settings[SSID], "Home")
        self.assertEqual(self.executor.settings[PSK], "home-test-password")
        self.assertEqual(self.executor.settings["connection.autoconnect"], "no")
        self.assertEqual(self.executor.backups[BOOT_PREVIOUS_CONNECTION][SSID], RECOVERY_WIFI_NAME)
        for command in self.commands_since(start):
            self.assertNotIn(" up ", " " + command + " ")
            self.assertNotIn(" down ", " " + command + " ")
            self.assertNotIn("--show-secrets", command)
            self.assertNotIn("eth0", command)
        wifi_follow.finish_boot(self.controller)
        self.assertNotIn(BOOT_PREVIOUS_CONNECTION, self.executor.backups)

    def test_boot_leaves_the_wifi_alone_when_nothing_needs_to_change(self):
        cases = ("router", "recovery-hold", "not-remembered", "pending-change")
        for case in cases:
            with self.subTest(case=case):
                self.setUp()
                if case != "not-remembered":
                    self.copy_router()
                if case == "recovery-hold":
                    self.go_to_recovery()
                if case == "pending-change":
                    self.controller.rename(RECOVERY_WIFI_NAME)
                    self.executor.backups[RECOVERY_CONNECTION] = dict(self.executor.settings)
                before = dict(self.executor.settings)
                start = len(self.executor.commands)
                self.assertEqual(wifi_follow.prepare_boot(self.controller, self.hold), case)
                self.assertEqual(self.executor.settings, before)
                for command in self.commands_since(start):
                    for word in ("modify", "clone", "delete", "edit", "up", "down"):
                        self.assertNotIn(word, command.split())

    def test_a_failed_boot_swap_restores_the_previous_profile(self):
        self.copy_router()
        self.controller.rename(RECOVERY_WIFI_NAME)
        before = dict(self.executor.settings)
        clones = self.executor.calls.get("clone", 0)
        self.executor.failures.add(("clone", clones + 2))  # Copying the router profile in.
        with self.assertRaises(WifiError):
            wifi_follow.prepare_boot(self.controller, self.hold)
        self.assertTrue(self.executor.exists)
        self.assertEqual(self.executor.settings, before)
        self.assertNotIn(BOOT_PREVIOUS_CONNECTION, self.executor.backups)

    def test_boot_fallback_restores_the_previous_wifi_when_the_router_copy_will_not_start(self):
        self.copy_router()
        self.controller.rename(RECOVERY_WIFI_NAME)
        wifi_follow.prepare_boot(self.controller, self.hold)
        self.assertTrue(wifi_follow.boot_fallback(self.controller))
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)
        self.assertEqual(self.executor.settings["connection.autoconnect"], "no")
        self.assertNotIn(BOOT_PREVIOUS_CONNECTION, self.executor.backups)
        # The router copy is kept so the next boot tries it again.
        self.assertIn(ROUTER_COPY_CONNECTION, self.executor.backups)
        self.assertFalse(wifi_follow.boot_fallback(self.controller))

    def test_an_interrupted_boot_swap_is_repaired_on_the_next_boot(self):
        self.copy_router()
        self.controller.rename(RECOVERY_WIFI_NAME)
        self.executor.backups[BOOT_PREVIOUS_CONNECTION] = dict(self.executor.settings)
        self.executor.exists = False  # Power was lost between delete and clone.
        self.assertEqual(wifi_follow.prepare_boot(self.controller, self.hold), "restored")
        self.assertTrue(self.executor.exists)
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)
        self.assertNotIn(BOOT_PREVIOUS_CONNECTION, self.executor.backups)

    # Operator commands.

    def test_recovery_keeps_password_and_security_and_holds_until_router_is_chosen(self):
        self.copy_router()
        result = self.go_to_recovery()
        self.assertEqual(result["ssid"], RECOVERY_WIFI_NAME)
        self.assertEqual(self.executor.settings[PSK], "home-test-password")
        self.assertEqual(self.executor.settings["802-11-wireless-security.proto"], "rsn")
        self.assertTrue(self.hold.exists())
        self.assertEqual(wifi_follow.prepare_boot(self.controller, self.hold), "recovery-hold")
        result = wifi_follow.use_router_copy(self.controller, self.hold)
        self.assertEqual(result, {"ssid": "Home", "security": "WPA2"})
        self.assertFalse(self.hold.exists())
        self.assertEqual(self.executor.settings[SSID], "Home")
        self.assertEqual(self.executor.active, "jittersim-ap")
        self.assertNotIn(RECOVERY_CONNECTION, self.executor.backups)

    def test_returning_without_a_remembered_copy_changes_nothing(self):
        self.controller.rename(RECOVERY_WIFI_NAME)
        before = dict(self.executor.settings)
        with self.assertRaises(WifiError) as caught:
            wifi_follow.use_router_copy(self.controller, self.hold)
        self.assertIn("Copy router Wi-Fi to Pi", str(caught.exception))
        self.assertEqual(self.executor.settings, before)

    def test_a_failed_return_restores_recovery_and_a_failed_recovery_drops_the_hold(self):
        self.copy_router()
        self.go_to_recovery()
        ups = self.executor.calls["up"]
        self.executor.failures.add(("up", ups + 1))
        with self.assertRaises(WifiError) as caught:
            wifi_follow.use_router_copy(self.controller, self.hold)
        self.assertEqual(caught.exception.reason, "wifi-restored")
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)
        self.assertEqual(self.executor.active, "jittersim-ap")
        self.assertNotIn("home-test-password", str(caught.exception))

        self.setUp()
        self.copy_router()
        ups = self.executor.calls["up"]
        self.executor.failures.add(("up", ups + 1))
        with self.assertRaises(WifiError):
            self.go_to_recovery()
        self.assertEqual(self.executor.settings[SSID], "Home")
        self.assertFalse(self.hold.exists())

    def test_status_names_the_state_without_any_password(self):
        self.copy_router()
        self.go_to_recovery()
        output = io.StringIO()
        with redirect_stdout(output):
            code = wifi_follow.main(["status"], executor=self.executor, hold_path=self.hold)
        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn("recovery-hold", text)
        self.assertIn(RECOVERY_WIFI_NAME, text)
        self.assertIn("Home", text)
        self.assertNotIn("home-test-password", text)

    def test_cli_boot_steps_never_raise_and_report_with_exit_codes(self):
        self.copy_router()
        self.controller.rename(RECOVERY_WIFI_NAME)
        self.assertEqual(wifi_follow.main(["boot"], executor=self.executor, hold_path=self.hold), 0)
        self.assertEqual(wifi_follow.main(["boot-fallback"], executor=self.executor, hold_path=self.hold), 0)
        self.assertEqual(wifi_follow.main(["boot-fallback"], executor=self.executor, hold_path=self.hold), 1)
        self.assertEqual(wifi_follow.main(["boot-done"], executor=self.executor, hold_path=self.hold), 0)
        self.executor.exists = False
        self.assertEqual(wifi_follow.main(["boot"], executor=self.executor, hold_path=self.hold), 1)


if __name__ == "__main__":
    unittest.main()
