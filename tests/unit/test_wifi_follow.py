"""The Pi Wi-Fi follows the last router copy that worked. Recovery is only a different name."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from jittersim.networking import wifi_follow
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.helper import HelperService
from jittersim.networking.router import _settings_from_wifi_data
from jittersim.networking.topology import Topology
from jittersim.networking.wifi import RECOVERY_CONNECTION, RECOVERY_WIFI_NAME, WifiController, WifiError
from tests.unit.test_wifi_security_copy import CopyExecutor

SSID = "802-11-wireless.ssid"
PSK = "802-11-wireless-security.psk"
WRITES = ("modify", "clone", "delete", "edit", "up", "down")


class FollowRouterTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.record = root / "wifi-router-copy.json"
        self.hold = root / "wifi-recovery-hold"
        self.previous = root / "boot-previous.json"
        self.executor = CopyExecutor()
        self.source = source = {"SSID": "Home", "Passphrase": "home-test-password", "Enable": True,
                                "ModeEnabled": "WPA2-Personal", "Enable5G": False}

        class Router:
            def login(self):
                pass

            def read_wifi_settings(self):
                return _settings_from_wifi_data(source)

            def close(self):
                pass

        self.applier = NetworkApplier(executor=self.executor, topology=Topology("eth0", "wlan0"))
        self.applier.wifi_record_path = self.record
        self.applier.wifi_hold_path = self.hold
        self.service = HelperService(self.applier, router_factory=lambda *_: Router())
        self.controller = WifiController(self.executor, "wlan0")

    def copy_router(self):
        preview = self.service.handle_payload({"action": "router-preview-ap", "admin_password": "admin-test"})
        result = self.service.handle_payload({"action": "router-sync-ap", "admin_password": "admin-test",
                                             "expected_fingerprint": preview["fingerprint"]})
        self.assertTrue(result["ok"], result)

    def boot(self):
        return wifi_follow.prepare_boot(self.controller, self.record, self.hold, self.previous)

    def run_cli(self, *argv):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = wifi_follow.main(list(argv), executor=self.executor, record_path=self.record,
                                    hold_path=self.hold, previous_path=self.previous)
        return code, output.getvalue() + errors.getvalue()

    def writes_since(self, start):
        return [command for command in self.executor.commands[start:]
                if command.executable == "nmcli" and set(command.arguments) & set(WRITES)]

    # The record of the router copy.

    def test_a_router_copy_records_its_name_and_security_but_never_the_password(self):
        self.copy_router()
        self.assertEqual(json.loads(self.record.read_text()), {"ssid": "Home", "security": "WPA2"})
        self.assertEqual(self.record.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("home-test-password", self.record.read_text())
        self.assertEqual(self.executor.settings[PSK], "home-test-password")

    def test_a_router_copy_ends_a_recovery_hold(self):
        self.copy_router()
        wifi_follow.use_recovery(self.controller, self.hold)
        self.assertTrue(self.hold.exists())
        self.copy_router()
        self.assertFalse(self.hold.exists())
        self.assertEqual(self.executor.settings[SSID], "Home")

    def test_a_manual_change_or_name_reset_deletes_the_record_first(self):
        for action in ("configure-ap", "wifi-reset"):
            with self.subTest(action=action):
                self.copy_router()
                payload = {"action": action}
                if action == "configure-ap":
                    payload.update(ssid="Owner choice", password="owner-test-password")
                result = self.service.handle_payload(payload)
                self.assertTrue(result["ok"], result)
                self.assertFalse(self.record.exists())
                self.assertEqual(self.boot(), "not-remembered")

    def test_a_manual_change_is_refused_when_the_record_cannot_be_deleted(self):
        self.copy_router()
        before = dict(self.executor.settings)
        self.applier.wifi_record_path = self.record.parent  # A directory: unlink fails.
        for payload in ({"action": "configure-ap", "ssid": "Owner choice", "password": "owner-test-password"},
                        {"action": "wifi-reset"}):
            with self.subTest(action=payload["action"]):
                result = self.service.handle_payload(payload)
                self.assertFalse(result["ok"])
                self.assertEqual(self.executor.settings, before)

    def test_a_copy_that_cannot_be_recorded_still_works_and_drops_the_older_record(self):
        import os
        self.copy_router()
        self.source["SSID"] = "New router name"
        # The atomic write cannot create its temporary file. Deleting still works.
        blocker = self.record.with_name(".%s.%d.tmp" % (self.record.name, os.getpid()))
        blocker.mkdir()
        self.copy_router()
        self.assertEqual(self.executor.settings[SSID], "New router name")
        self.assertFalse(self.record.exists())  # Never a boot back to the old name.
        self.assertEqual(self.boot(), "not-remembered")

    # Boot: rename back to the router name without contacting the router.

    def test_boot_renames_recovery_back_to_the_router_name_without_starting_the_radio(self):
        self.copy_router()
        self.controller.rename(RECOVERY_WIFI_NAME)
        start = len(self.executor.commands)
        self.assertEqual(self.boot(), "switched")
        self.assertEqual(self.executor.settings[SSID], "Home")
        self.assertEqual(self.executor.settings[PSK], "home-test-password")
        writes = self.writes_since(start)
        self.assertEqual([command.arguments for command in writes],
                         [("connection", "modify", "id", "jittersim-ap", SSID, "Home")])
        for command in self.executor.commands[start:]:
            self.assertNotIn("--show-secrets", command.arguments)
            self.assertNotEqual(command.executable, "wpa_cli")
        self.assertEqual(json.loads(self.previous.read_text()), {"ssid": RECOVERY_WIFI_NAME})
        wifi_follow.finish_boot(self.previous)
        self.assertFalse(self.previous.exists())

    def test_boot_leaves_the_wifi_alone_when_it_must_not_or_need_not_change(self):
        for case in ("router", "recovery-hold", "not-remembered", "pending-change", "security-differs"):
            with self.subTest(case=case):
                self.setUp()
                if case != "not-remembered":
                    self.copy_router()
                if case == "recovery-hold":
                    wifi_follow.use_recovery(self.controller, self.hold)
                if case == "pending-change":
                    self.controller.rename(RECOVERY_WIFI_NAME)
                    self.executor.backups[RECOVERY_CONNECTION] = dict(self.executor.settings)
                if case == "security-differs":
                    self.controller.rename(RECOVERY_WIFI_NAME)
                    self.executor.settings["802-11-wireless-security.key-mgmt"] = "sae"
                    self.executor.settings["802-11-wireless-security.pmf"] = "3"
                before = dict(self.executor.settings)
                start = len(self.executor.commands)
                self.assertEqual(self.boot(), case)
                self.assertEqual(self.executor.settings, before)
                self.assertEqual(self.writes_since(start), [])
                self.assertFalse(self.previous.exists())

    def test_a_damaged_record_changes_nothing(self):
        self.copy_router()
        self.controller.rename(RECOVERY_WIFI_NAME)
        for content in ("not json", '{"ssid": "Home", "security": "WEP"}', '{"ssid": ""}', "x" * 2000):
            with self.subTest(content=content[:20]):
                self.record.write_text(content)
                code, text = self.run_cli("boot")
                self.assertEqual(code, 1)
                self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)
                code, text = self.run_cli("status")
                self.assertIn("record-damaged", text)

    def test_a_failed_boot_rename_puts_the_previous_name_back(self):
        self.copy_router()
        self.controller.rename(RECOVERY_WIFI_NAME)
        modifies = self.executor.calls["modify"]
        self.executor.failures.add(("modify", modifies + 1))
        with self.assertRaises(WifiError):
            self.boot()
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)
        self.assertFalse(self.previous.exists())

    def test_boot_fallback_puts_the_previous_name_back_and_reports_when_there_is_none(self):
        self.copy_router()
        self.controller.rename(RECOVERY_WIFI_NAME)
        self.assertEqual(self.run_cli("boot")[0], 0)
        self.assertEqual(self.run_cli("boot-fallback")[0], 0)
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)
        self.assertFalse(self.previous.exists())
        # Nothing renamed at this boot: the unit then skips the second round of starts.
        self.assertEqual(self.run_cli("boot-fallback")[0], wifi_follow.NOTHING_TO_RESTORE)
        # The record stays, so the next boot tries the router name again.
        self.assertEqual(self.boot(), "switched")
        modifies = self.executor.calls["modify"]
        self.executor.failures.add(("modify", modifies + 1))
        self.assertEqual(self.run_cli("boot-fallback")[0], 1)  # The unit still tries a start.
        self.assertEqual(self.run_cli("boot-done")[0], 0)

    # Operator commands.

    def test_recovery_holds_across_boots_and_router_returns(self):
        self.copy_router()
        code, text = self.run_cli("recovery")
        self.assertEqual(code, 0, text)
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)
        self.assertEqual(self.executor.settings[PSK], "home-test-password")
        self.assertEqual(self.executor.settings["802-11-wireless-security.proto"], "rsn")
        self.assertTrue(self.hold.exists())
        self.assertEqual(self.boot(), "recovery-hold")
        code, text = self.run_cli("status")
        self.assertIn("state: recovery-hold", text)
        self.assertIn("router_copy: Home (WPA2)", text)
        self.assertNotIn("home-test-password", text)
        code, text = self.run_cli("router")
        self.assertEqual(code, 0, text)
        self.assertFalse(self.hold.exists())
        self.assertEqual(self.executor.settings[SSID], "Home")
        self.assertEqual(self.executor.active, "jittersim-ap")
        self.assertIn("state: router", self.run_cli("status")[1])

    def test_operator_commands_refuse_while_a_dashboard_change_is_unfinished(self):
        self.copy_router()
        self.executor.backups[RECOVERY_CONNECTION] = dict(self.executor.settings)
        for action in ("recovery", "router"):
            with self.subTest(action=action):
                start = len(self.executor.commands)
                code, text = self.run_cli(action)
                self.assertEqual(code, 1)
                self.assertIn("still running or unfinished", text)
                self.assertEqual(self.writes_since(start), [])
                self.assertFalse(self.hold.exists())

    def test_router_refuses_without_a_record_or_with_changed_security(self):
        self.controller.rename(RECOVERY_WIFI_NAME)
        code, text = self.run_cli("router")
        self.assertEqual(code, 1)
        self.assertIn("Copy router Wi-Fi to Pi", text)
        self.copy_router()
        self.controller.rename(RECOVERY_WIFI_NAME)
        self.executor.settings["802-11-wireless-security.pmf"] = "2"  # Now WPA2/WPA3.
        code, text = self.run_cli("router")
        self.assertEqual(code, 1)
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)

    def test_a_failed_switch_restores_the_running_name_and_keeps_hold_consistent(self):
        self.copy_router()
        ups = self.executor.calls["up"]
        self.executor.failures.add(("up", ups + 1))
        code, text = self.run_cli("recovery")
        self.assertEqual(code, 1)
        self.assertEqual(self.executor.settings[SSID], "Home")
        self.assertFalse(self.hold.exists())  # The router name still runs.

        self.assertEqual(self.run_cli("recovery")[0], 0)
        ups = self.executor.calls["up"]
        self.executor.failures.add(("up", ups + 1))
        code, text = self.run_cli("router")
        self.assertEqual(code, 1)
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)
        self.assertEqual(self.executor.active, "jittersim-ap")
        self.assertNotIn("home-test-password", text)


if __name__ == "__main__":
    unittest.main()
