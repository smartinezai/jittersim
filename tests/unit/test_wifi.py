import unittest

from jittersim.networking.applier import NetworkApplier
from jittersim.networking.topology import Topology
from jittersim.networking.wifi import WifiError
from tests.wifi_support import WifiExecutor


class WifiRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.executor = WifiExecutor()
        self.applier = NetworkApplier(executor=self.executor, topology=Topology("eth0", "wlan0"))

    def assert_security(self):
        for name, value in (
            ("proto", "rsn"), ("pairwise", "ccmp"), ("group", "ccmp"),
            ("key-mgmt", "sae"), ("pmf", "3"),
        ):
            self.assertEqual(self.executor.settings["802-11-wireless-security." + name], value)

    def test_reset_changes_name_and_security_without_reading_or_writing_password(self):
        for name in ("Home: \\ network", "JitterSim", "家のネット"):
            with self.subTest(name=name):
                self.executor.settings["802-11-wireless.ssid"] = name
                result = self.applier.reset_wifi_name()
                self.assertEqual(result, {"ssid": "JitterSim", "security": "WPA3-SAE"})
                self.assertEqual(self.executor.settings["802-11-wireless-security.psk"],
                                 "fixture-wifi-password")
                self.assert_security()
        for command in self.executor.commands:
            self.assertNotIn("802-11-wireless-security.psk", " ".join(command.arguments))
            self.assertNotIn("--show-secrets", command.arguments)
            self.assertLessEqual(command.timeout_seconds, 12)
            self.assertNotIn("eth0", command.arguments)

    def test_manual_changes_use_stronger_security_and_verify_activation(self):
        self.applier.configure_access_point(ssid="Manual network", password="new-test-password")
        self.assertEqual(self.executor.settings["802-11-wireless.ssid"], "Manual network")
        self.assertEqual(self.executor.settings["802-11-wireless-security.psk"], "new-test-password")
        self.assert_security()
        self.assertEqual(self.executor.active, "jittersim-ap")

    def test_missing_or_wrong_interface_profile_cannot_change_any_connection(self):
        for missing, interface in ((True, "wlan0"), (False, "eth0")):
            with self.subTest(missing=missing):
                self.executor.exists = not missing
                self.executor.settings["connection.interface-name"] = interface
                with self.assertRaises(WifiError):
                    self.applier.reset_wifi_name()
                self.assertEqual(self.executor.calls, {})

    def test_activation_failure_restores_original_name_and_security(self):
        previous = dict(self.executor.settings)
        self.executor.failures.add(("up", 1))
        with self.assertRaises(WifiError) as caught:
            self.applier.reset_wifi_name()
        self.assertEqual(caught.exception.reason, "wifi-restored")
        self.assertEqual(self.executor.settings, previous)
        self.assertEqual(self.executor.active, "jittersim-ap")
        self.assertNotIn("fixture-wifi-password", str(caught.exception))
        self.assertEqual(self.executor.calls["up"], 2)

    def test_failed_restoration_is_reported_and_attempts_stay_bounded(self):
        self.executor.failures.update((("up", 1), ("up", 2)))
        with self.assertRaises(WifiError) as caught:
            self.applier.reset_wifi_name()
        self.assertEqual(caught.exception.reason, "wifi-restore-failed")
        self.assertEqual(self.executor.calls["up"], 2)
        self.assertNotIn("fixture-wifi-password", str(caught.exception))

    def test_saved_settings_without_active_connection_are_not_success(self):
        self.executor.keep_inactive = True
        with self.assertRaises(WifiError) as caught:
            self.applier.reset_wifi_name()
        self.assertEqual(caught.exception.reason, "wifi-restore-failed")

    def test_invalid_password_is_rejected_before_a_command_runs(self):
        for password in ("secret\npassword", "passwörd", "short"):
            with self.subTest(password=password):
                with self.assertRaises(ValueError):
                    self.applier.configure_access_point(ssid="Manual", password=password)
                self.assertEqual(self.executor.commands, [])

    def test_saved_wpa3_cannot_hide_weaker_or_invalid_active_radio_settings(self):
        for name, value in (
            ("status", "mode=AP\nwpa_state=COMPLETED\npairwise_cipher=TKIP\ngroup_cipher=CCMP\n"),
            ("key_mgmt", "WPA-PSK\n"), ("key_mgmt", "SAE WPA-PSK\n"),
            ("ieee80211w", "1\n"), ("list_networks", "7\tJitterSim\tany\n"),
            ("list_networks", "7\tJitterSim\tany\t[CURRENT]\n8\tOther\tany\t[CURRENT]\n"),
            ("list_networks", "x\tJitterSim\tany\t[CURRENT]\n"),
            ("status", "x" * 4097),
        ):
            with self.subTest(name=name, value=value[:30]):
                self.setUp()
                previous = dict(self.executor.settings)
                self.executor.radio_overrides[name] = value
                with self.assertRaises(WifiError) as caught:
                    self.applier.reset_wifi_name()
                self.assertEqual(caught.exception.reason, "wifi-restored")
                self.assertEqual(self.executor.settings, previous)
                self.assertEqual(self.executor.active, "jittersim-ap")

    def test_name_reset_preserves_an_existing_wpa3_network(self):
        self.applier.configure_access_point(ssid="Cloned home network", password="new-test-password")
        result = self.applier.reset_wifi_name()
        self.assertEqual(result["security"], "WPA3-SAE")
        self.assert_security()
        self.assertEqual(self.executor.settings["802-11-wireless-security.psk"], "new-test-password")
