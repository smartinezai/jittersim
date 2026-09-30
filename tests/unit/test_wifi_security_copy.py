"""Exercise router detection through preview, copy and Wi-Fi recovery."""

import unittest

from jittersim.networking.applier import NetworkApplier
from jittersim.networking.commands import CommandResult
from jittersim.networking.helper import HelperService
from jittersim.networking.router import _settings_from_wifi_data
from jittersim.networking.topology import Topology
from jittersim.networking.wifi import RECOVERY_CONNECTION, WifiError
from tests.wifi_support import WifiExecutor


class CopyExecutor(WifiExecutor):
    def run(self, command):
        if command.executable == "ip":
            self.commands.append(command)
            return CommandResult(0, stdout="default via 192.168.0.1 dev eth0\n")
        if command.executable == "nmcli" and command.arguments[-1] == "eth0":
            self.commands.append(command)
            return CommandResult(0, stdout="management\n")
        return super().run(command)


class SecurityCopyTests(unittest.TestCase):
    def setUp(self):
        self.source = {"SSID": "Home", "Passphrase": "home-test-password", "Enable": True,
                       "ModeEnabled": "WPA2-Personal", "Enable5G": False}
        self.executor = CopyExecutor()
        source = self.source

        class Router:
            def login(self):
                pass

            def read_wifi_settings(self):
                return _settings_from_wifi_data(source)

            def close(self):
                pass

        self.applier = NetworkApplier(executor=self.executor, topology=Topology("eth0", "wlan0"))
        self.service = HelperService(self.applier, router_factory=lambda *_: Router())

    def preview(self):
        return self.service.handle_payload({"action": "router-preview-ap", "admin_password": "admin-test"})

    def copy(self, preview, *, legacy=False):
        return self.service.handle_payload({"action": "router-sync-ap", "admin_password": "admin-test",
                                           "expected_fingerprint": preview["fingerprint"],
                                           "confirm_legacy": legacy})

    def test_each_router_mode_is_copied_and_kept_by_name_reset(self):
        cases = (("WPA-Personal", "WPA", "wpa-psk", "wpa"),
                 ("WPA2-Personal", "WPA2", "wpa-psk", "rsn"),
                 (4, "WPA2", "wpa-psk", "rsn"),
                 ("4", "WPA2", "wpa-psk", "rsn"),
                 ("WPA3-Personal", "WPA3", "sae", "rsn"),
                 ("WPA-WPA2-Personal", "WPA/WPA2", "wpa-psk", "wpa,rsn"),
                 (5, "WPA/WPA2", "wpa-psk", "wpa,rsn"),
                 ("WPA2-WPA3-Personal", "WPA2/WPA3", "wpa-psk", "rsn"))
        for reported, mode, key, proto in cases:
            with self.subTest(mode=reported):
                self.setUp()
                self.source["ModeEnabled"] = reported
                preview = self.preview()
                self.assertTrue(preview["ok"], preview)
                self.assertEqual(preview["security"], mode)
                self.assertNotIn("home-test-password", str(preview))
                self.assertFalse(self.executor.calls)
                result = self.copy(preview, legacy=preview["legacy"])
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["security"], mode)
                self.assertEqual(self.executor.settings["802-11-wireless-security.key-mgmt"], key)
                self.assertEqual(self.executor.settings["802-11-wireless-security.proto"], proto)
                before = dict(self.executor.settings)
                reset = self.applier.reset_wifi_name()
                before["802-11-wireless.ssid"] = "JitterSim"
                self.assertEqual(self.executor.settings, before)
                self.assertEqual(reset["security"], mode)
                self.assertFalse(self.executor.backups)

    def test_changed_source_and_unconfirmed_legacy_do_not_change_pi(self):
        preview = self.preview()
        self.source["ModeEnabled"] = "WPA-Personal"
        self.assertEqual(self.copy(preview)["reason"], "changed")
        self.assertEqual(self.copy(self.preview())["reason"], "legacy")
        self.assertFalse(self.executor.calls)

    def test_unknown_or_missing_security_is_not_guessed(self):
        for value in (None, False, True, 0, 10, "None", "WEP", "WPA2-Enterprise", "unexpected"):
            with self.subTest(value=value):
                self.source["ModeEnabled"] = value
                self.assertEqual(self.preview()["reason"], "security")
                self.assertFalse(self.executor.calls)

    def test_name_password_and_security_come_from_the_same_enabled_band(self):
        self.source.update(Enable=False, Enable5G=True, SSID5G="Other",
                           Passphrase5G="other-test-password", ModeEnabled5G="WPA3-Personal")
        preview = self.preview()
        self.assertEqual((preview["ssid"], preview["security"], preview["band"]),
                         ("Other", "WPA3", "5 GHz"))

    def test_failed_import_restores_previous_password_and_mode(self):
        before = dict(self.executor.settings)
        self.executor.failures.add(("up", 1))
        result = self.copy(self.preview())
        self.assertEqual(result["reason"], "wifi-restored")
        self.assertEqual(self.executor.settings, before)
        self.assertFalse(self.executor.backups)
        self.assertNotIn("home-test-password", str(result))
        self.assertEqual(self.executor.calls["up"], 2)

    def test_saved_mixed_mode_is_rejected_when_radio_omits_wpa3(self):
        before = dict(self.executor.settings)
        self.source["ModeEnabled"] = "WPA2-WPA3-Personal"
        self.executor.radio_overrides["key_mgmt"] = "WPA-PSK"
        result = self.copy(self.preview())
        self.assertEqual(result["reason"], "wifi-restored")
        self.assertEqual(self.executor.settings, before)

    def test_interrupted_import_recovers_even_if_original_profile_is_missing(self):
        before = dict(self.executor.settings)
        self.executor.backups[RECOVERY_CONNECTION] = dict(before)
        self.executor.exists = False
        self.assertTrue(self.applier.recover_wifi())
        self.assertEqual(self.executor.settings, before)
        self.assertEqual(self.executor.active, "jittersim-ap")
        self.assertFalse(self.executor.backups)

    def test_failed_restore_keeps_recovery_copy_for_an_explicit_retry(self):
        before = dict(self.executor.settings)
        self.executor.failures.update((("up", 1), ("up", 2)))
        with self.assertRaises(WifiError) as caught:
            self.applier.configure_access_point(ssid="New", password="new-test-password")
        self.assertEqual(caught.exception.reason, "wifi-restore-failed")
        self.assertEqual(self.executor.backups[RECOVERY_CONNECTION], {**before, "connection.autoconnect": "no"})
        self.assertTrue(self.applier.recover_wifi())
        self.assertFalse(self.executor.backups)
