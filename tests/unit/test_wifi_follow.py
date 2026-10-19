"""The Pi Wi-Fi follows the last router copy that worked. Recovery is only a different name."""

import hashlib
import hmac
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from jittersim.networking import wifi_follow
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.commands import CommandResult
from jittersim.networking.executor import CommandExecutionError, SubprocessExecutor
from jittersim.networking.helper import HelperService
from jittersim.networking.router import _settings_from_wifi_data
from jittersim.networking.topology import Topology
from jittersim.networking.wifi import (
    RECOVERY_CONNECTION,
    RECOVERY_WIFI_NAME,
    WifiController,
    WifiError,
)
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
        self.lock = root / "wifi.lock"
        self.check = root / "boot-check.json"
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
        self.applier.wifi_lock_path = self.lock
        self.applier.wifi_fingerprint = self.fingerprint
        self.service = HelperService(self.applier, router_factory=lambda *_: Router())
        self.controller = WifiController(self.executor, "wlan0")

    def fingerprint(self, salt):
        """What openssl computes from nmcli's output (the password and a newline)."""
        password = (self.executor.settings[PSK] + "\n").encode()
        return hmac.new(salt.encode(), password, hashlib.sha256).hexdigest()

    def copy_router(self):
        preview = self.service.handle_payload({"action": "router-preview-ap", "admin_password": "admin-test"})
        result = self.service.handle_payload({"action": "router-sync-ap", "admin_password": "admin-test",
                                             "expected_fingerprint": preview["fingerprint"]})
        self.assertTrue(result["ok"], result)

    def boot(self):
        self.executor.active = ""  # At boot the access point has not started yet.
        return wifi_follow.prepare_boot(self.controller, self.record, self.hold, self.previous,
                                        self.fingerprint)

    def run_cli(self, *argv):
        if argv[0] == "boot":
            self.executor.active = ""  # At boot the access point has not started yet.
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = wifi_follow.main(list(argv), executor=self.executor, record_path=self.record,
                                    hold_path=self.hold, previous_path=self.previous,
                                    lock_path=self.lock, fingerprint=self.fingerprint,
                                    check_path=self.check)
        return code, output.getvalue() + errors.getvalue()

    def writes_since(self, start):
        return [command for command in self.executor.commands[start:]
                if command.executable == "nmcli" and set(command.arguments) & set(WRITES)]

    # The record of the router copy.

    def test_a_router_copy_records_its_name_and_security_but_never_the_password(self):
        self.copy_router()
        record = json.loads(self.record.read_text())
        self.assertEqual({key: record[key] for key in ("ssid", "security")},
                         {"ssid": "Home", "security": "WPA2"})
        self.assertEqual(record["password_hmac"], self.fingerprint(record["salt"]))
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

    def test_a_copy_deletes_the_older_record_before_it_changes_the_wifi(self):
        self.copy_router()
        seen = []
        self.executor.on_up = lambda: seen.append(self.record.exists())
        self.source["SSID"] = "New router name"
        self.copy_router()
        # While the new password was being started no record named the older router.
        self.assertEqual(seen, [False])
        self.assertEqual(json.loads(self.record.read_text())["ssid"], "New router name")

    def test_a_held_lock_makes_every_wifi_change_wait_then_refuse(self):
        import fcntl
        import os
        self.copy_router()
        self.controller.rename(RECOVERY_WIFI_NAME)
        descriptor = os.open(self.lock, os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        before = dict(self.executor.settings)
        start = len(self.executor.commands)
        with self.assertRaises(WifiError) as caught:
            with wifi_follow.wifi_lock(self.lock, wait=0.3):
                pass
        self.assertEqual(caught.exception.reason, "wifi-busy")
        result = self.service.handle_payload({"action": "configure-ap", "ssid": "Owner choice",
                                              "password": "owner-test-password"})
        self.assertFalse(result["ok"])
        self.assertTrue(self.record.exists())
        self.assertEqual(self.executor.settings, before)
        self.assertEqual(self.writes_since(start), [])
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        self.assertEqual(self.run_cli("boot")[0], 0)
        self.assertEqual(self.executor.settings[SSID], "Home")

    def test_a_password_changed_outside_this_code_is_never_given_the_router_name(self):
        # For example an older release's manual change after a rollback, with the same security.
        self.copy_router()
        self.controller.rename(RECOVERY_WIFI_NAME)
        self.executor.settings[PSK] = "older-release-password"
        start = len(self.executor.commands)
        self.assertEqual(self.boot(), "password-differs")
        self.assertEqual(self.writes_since(start), [])
        code, text = self.run_cli("router")
        self.assertEqual(code, 1)
        self.assertIn("password changed", text)
        self.assertIn("state: password-differs", self.run_cli("status")[1])
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)
        # Without a fingerprint nothing is recorded, so nothing is renamed later.
        self.applier.wifi_fingerprint = lambda salt: None
        self.copy_router()
        self.assertFalse(self.record.exists())

    def test_the_real_fingerprint_pipes_the_password_from_nmcli_into_openssl(self):
        if subprocess.run(["sh", "-c", "command -v openssl"], capture_output=True).returncode:
            self.skipTest("openssl is not installed")
        directory = self.record.parent / "bin"
        directory.mkdir()
        calls = directory / "calls"
        fake = directory / "nmcli"
        fake.write_text("#!/bin/sh\necho \"$*\" >> %s\necho fixture-wifi-password\n" % calls)
        fake.chmod(0o755)
        openssl = subprocess.run(["sh", "-c", "command -v openssl"], capture_output=True,
                                 text=True).stdout.strip()
        os.symlink(openssl, directory / "openssl")
        salt = "0" * 32
        digest = wifi_follow.password_fingerprint(salt, search_path=str(directory))
        expected = hmac.new(salt.encode(), b"fixture-wifi-password\n", hashlib.sha256).hexdigest()
        self.assertEqual(digest, expected)
        self.assertNotIn("fixture-wifi-password", calls.read_text())
        self.assertIn("--show-secrets", calls.read_text())
        fake.write_text("#!/bin/sh\nexit 10\n")
        self.assertIsNone(wifi_follow.password_fingerprint(salt, search_path=str(directory)))

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


ROUTER_BSSID = "AA:BB:CC:DD:EE:01"
PI_BSSID = "B8:27:EB:00:00:01"
LOOK_PURPOSE = "read the Wi-Fi names on the air (read only)"


def listing(*pairs):
    """`nmcli --terse --escape yes -f SSID,BSSID device wifi list` output."""
    return "".join("%s:%s\n" % (ssid.replace("\\", "\\\\").replace(":", "\\:"),
                                bssid.replace(":", "\\:")) for ssid, bssid in pairs)


class BootScanTests(unittest.TestCase):
    """D1: the boot looks once at the air before it gives the Pi the router name."""

    fingerprint = FollowRouterTests.fingerprint
    copy_router = FollowRouterTests.copy_router
    run_cli = FollowRouterTests.run_cli
    writes_since = FollowRouterTests.writes_since

    def setUp(self):
        FollowRouterTests.setUp(self)
        self.sleeps = []
        self.now = 0.0
        self.looking = False
        self.slow_calls = False
        run = self.executor.run

        def run_on_the_test_clock(command):
            # Worst case: while the look runs, every nmcli call takes its whole timeout.
            if self.slow_calls and self.looking and command.executable == "nmcli":
                self.now += command.timeout_seconds
            return run(command)

        self.executor.run = run_on_the_test_clock

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def at_boot(self, *, on_router_name=False):
        """The Pi after a reboot: router copy recorded, the access point not started yet."""
        self.copy_router()
        if not on_router_name:
            self.controller.rename(RECOVERY_WIFI_NAME)
        self.executor.active = ""

    def scan(self, ssid):
        self.looking = True
        try:
            return wifi_follow.scan_air(self.controller, ssid, sleep=self.sleep,
                                        clock=lambda: self.now)
        finally:
            self.looking = False

    def boot(self):
        return wifi_follow.prepare_boot(self.controller, self.record, self.hold, self.previous,
                                        self.fingerprint, self.check, self.scan)

    def lists(self, start=0):
        return [command.arguments[command.arguments.index("--rescan") + 1]
                for command in self.executor.commands[start:]
                if command.executable == "nmcli" and "list" in command.arguments]

    def test_the_router_name_on_the_air_from_another_radio_keeps_recovery(self):
        self.at_boot()
        self.executor.scan_output = listing(("Neighbour", "11:22:33:44:55:66"),
                                            ("Home", ROUTER_BSSID))
        start = len(self.executor.commands)
        self.assertEqual(self.boot(), "router-on-air")
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)
        self.assertEqual(self.writes_since(start), [])
        self.assertEqual(self.lists(start), ["no"])  # The cached list was enough: no scan.
        self.assertFalse(self.previous.exists())
        self.assertFalse(self.hold.exists())  # Not held: the next boot looks again.
        check = json.loads(self.check.read_text())
        self.assertEqual((check["decision"], check["scan_result"], check["scan_bssid"]),
                         ("router-on-air", "seen", ROUTER_BSSID))
        code, text = self.run_cli("status")
        self.assertIn("state: router-on-air", text)
        self.assertIn("boot_scan: seen from the cached list, 2 Wi-Fis listed", text)
        self.assertIn(ROUTER_BSSID, text)
        self.assertIn("Switch the router Wi-Fi off and reboot", text)
        self.assertNotIn("home-test-password", text)
        # The next boot without the router radio returns to the router name.
        self.executor.scan_output = ""
        self.assertEqual(self.boot(), "switched")
        self.assertEqual(self.executor.settings[SSID], "Home")

    def test_a_pi_already_on_the_router_name_switches_to_recovery_for_this_boot(self):
        self.at_boot(on_router_name=True)
        self.executor.scan_output = listing(("Home", ROUTER_BSSID))
        start = len(self.executor.commands)
        self.assertEqual(self.boot(), "router-on-air")
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)
        self.assertEqual(self.executor.settings[PSK], "home-test-password")
        self.assertEqual([command.arguments for command in self.writes_since(start)],
                         [("connection", "modify", "id", "jittersim-ap", SSID, RECOVERY_WIFI_NAME)])
        # Recovery does not start after ten tries: the fallback has nothing to restore
        # (status 2), so the unit skips the second round and never starts the router name
        # while the router sends it.
        self.assertFalse(self.previous.exists())
        start = len(self.executor.commands)
        self.assertEqual(self.run_cli("boot-fallback")[0], wifi_follow.NOTHING_TO_RESTORE)
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)
        self.assertEqual(self.writes_since(start), [])

    def test_the_rename_to_recovery_drops_an_older_previous_name(self):
        self.at_boot(on_router_name=True)
        self.previous.write_text('{"ssid": "Home"}')  # From an earlier start in this boot.
        self.executor.scan_output = listing(("Home", ROUTER_BSSID))
        self.assertEqual(self.boot(), "router-on-air")
        self.assertFalse(self.previous.exists())
        self.assertEqual(self.run_cli("boot-fallback")[0], wifi_follow.NOTHING_TO_RESTORE)
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)

    def test_a_failed_rename_to_recovery_keeps_the_router_name(self):
        self.at_boot(on_router_name=True)
        self.executor.scan_output = listing(("Home", ROUTER_BSSID))
        self.executor.failures.add(("modify", self.executor.calls["modify"] + 1))
        with self.assertRaises(WifiError):
            self.boot()
        self.assertEqual(self.executor.settings[SSID], "Home")
        self.assertFalse(self.previous.exists())
        self.assertEqual(json.loads(self.check.read_text())["decision"], "rename-failed")

    def test_only_the_pis_own_bssid_does_not_count(self):
        self.at_boot()
        self.executor.scan_output = listing(("Home", PI_BSSID.lower()))
        self.assertEqual(self.boot(), "switched")
        self.assertEqual(self.executor.settings[SSID], "Home")
        self.assertEqual(json.loads(self.check.read_text())["scan_result"], "only-own")

    def test_an_empty_list_scans_once_then_behaves_as_before(self):
        self.at_boot()
        start = len(self.executor.commands)
        self.assertEqual(self.boot(), "switched")
        self.assertEqual(self.lists(start), ["no", "yes"])
        self.assertEqual(self.executor.settings[SSID], "Home")
        check = json.loads(self.check.read_text())
        self.assertEqual((check["scan_result"], check["scan_source"]), ("empty", "rescan"))

    def test_the_rescan_finds_the_router_name_after_an_empty_cached_list(self):
        self.at_boot()
        self.executor.scan_replies = ["", listing(("Home", ROUTER_BSSID))]
        self.assertEqual(self.boot(), "router-on-air")
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)

    def test_other_names_on_the_air_behave_as_before(self):
        self.at_boot()
        self.executor.scan_output = listing(("Home-5G", ROUTER_BSSID), ("home", "AA:BB:CC:DD:EE:02"),
                                            ("", "AA:BB:CC:DD:EE:03"))
        self.assertEqual(self.boot(), "switched")
        self.assertEqual(json.loads(self.check.read_text())["scan_result"], "not-seen")

    def test_a_scan_error_fails_open(self):
        failure = CommandExecutionError("nmcli failed", return_code=10,
                                        stderr="Error: Scanning not allowed while unavailable.")
        for replies in ([failure, failure], [failure, ""], [ValueError("surprise")] * 2):
            with self.subTest(replies=replies):
                self.setUp()
                self.at_boot()
                self.executor.scan_replies = list(replies)
                self.assertEqual(self.boot(), "switched")
                self.assertEqual(self.executor.settings[SSID], "Home")

    def test_a_busy_radio_waits_once_and_reads_the_list_again(self):
        busy = CommandExecutionError("nmcli failed", return_code=1,
                                     stderr="Error: Device or resource busy")
        self.at_boot()
        self.executor.scan_replies = ["", busy, listing(("Home", ROUTER_BSSID))]
        start = len(self.executor.commands)
        self.assertEqual(self.boot(), "router-on-air")
        self.assertEqual(self.lists(start), ["no", "yes", "no"])
        self.assertEqual(self.sleeps, [wifi_follow.BUSY_WAIT])
        self.assertEqual(json.loads(self.check.read_text())["scan_source"],
                         "list after a busy radio")
        # Still busy (or empty) after the wait: as before, and never a second scan.
        self.setUp()
        self.at_boot()
        self.executor.scan_replies = ["", busy, busy]
        start = len(self.executor.commands)
        self.assertEqual(self.boot(), "switched")
        self.assertEqual(self.lists(start), ["no", "yes", "no"])
        self.assertEqual(json.loads(self.check.read_text())["scan_result"], "busy")

    def test_a_timeout_fails_open_and_the_look_stays_within_its_budget(self):
        timeout = CommandExecutionError("nmcli timed out", timed_out=True)
        self.at_boot()
        self.executor.scan_replies = [timeout, timeout]
        self.assertEqual(self.boot(), "switched")
        self.assertEqual(json.loads(self.check.read_text())["scan_result"], "timeout")
        for command in self.executor.commands:
            if command.executable == "nmcli" and "list" in command.arguments:
                self.assertLessEqual(command.timeout_seconds, wifi_follow.RESCAN_TIMEOUT)
        # The budget is used up by the cached list: no rescan is started at all.
        self.setUp()
        self.at_boot()
        run = self.executor.run

        def run_and_use_the_budget(command):
            try:
                return run(command)
            finally:
                if "list" in command.arguments:
                    self.now = wifi_follow.SCAN_BUDGET

        self.executor.run = run_and_use_the_budget
        start = len(self.executor.commands)
        result = wifi_follow.scan_air(self.controller, "Home", sleep=self.sleeps.append,
                                      clock=lambda: self.now)
        self.assertEqual(result["result"], "timeout")
        self.assertEqual(self.lists(start), ["no"])

    def test_a_running_access_point_is_never_scanned_or_renamed(self):
        # For example `systemctl restart jittersim-ap` after a router-on-air boot: the radio
        # sends Recovery. Renaming the saved profile would bring the router name back at the
        # next start without a look.
        self.copy_router()
        self.controller.rename(RECOVERY_WIFI_NAME)  # The fake keeps jittersim-ap active.
        start = len(self.executor.commands)
        self.assertEqual(self.boot(), "ap-running")
        self.assertEqual(self.lists(start), [])
        self.assertEqual(self.writes_since(start), [])
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)
        self.assertFalse(self.previous.exists())
        self.assertEqual(json.loads(self.check.read_text())["scan_result"], "skipped")
        text = self.run_cli("status")[1]
        self.assertIn("state: ap-running", text)
        self.assertIn("left the name alone", text)

    def test_a_cold_boot_waits_for_wlan0_to_become_available(self):
        self.at_boot()
        self.executor.device_states = ["20 (unavailable)", "20 (unavailable)",
                                       CommandExecutionError("Device not found")]
        self.executor.scan_output = listing(("Home", ROUTER_BSSID))
        self.assertEqual(self.boot(), "router-on-air")
        self.assertEqual(self.sleeps, [wifi_follow.READY_STEP] * 3)
        self.assertEqual(self.executor.settings[SSID], RECOVERY_WIFI_NAME)

    def test_a_translated_state_still_waits_for_wlan0(self):
        # The state text follows the locale. The number does not: 20 is "unavailable".
        self.at_boot()
        self.executor.device_states = ["20 (nicht verf\u00fcgbar)", "20 (non disponible)"]
        self.executor.device_state = "30 (getrennt)"
        self.executor.scan_output = listing(("Home", ROUTER_BSSID))
        self.assertEqual(self.boot(), "router-on-air")
        self.assertEqual(self.sleeps, [wifi_follow.READY_STEP] * 2)

    def test_a_translated_state_in_the_detail_is_still_called_unavailable(self):
        self.at_boot()
        self.executor.device_state = "20 (nicht verf\u00fcgbar)"
        self.executor.scan_replies = [CommandExecutionError("nmcli failed", return_code=10)] * 2
        self.assertEqual(self.boot(), "switched")
        self.assertIn("wlan0 was still 20 (nicht verf\u00fcgbar)",
                      json.loads(self.check.read_text())["scan_detail"])

    def test_every_nmcli_call_of_the_look_asks_for_untranslated_output(self):
        self.at_boot()
        self.executor.scan_output = listing(("Neighbour", "11:22:33:44:55:66"))
        start = len(self.executor.commands)
        self.boot()
        looked = [command for command in self.executor.commands[start:]
                  if command.executable == "nmcli"
                  and command.purpose == LOOK_PURPOSE]
        self.assertEqual({command.arguments[command.arguments.index("--rescan") + 1]
                          if "--rescan" in command.arguments else command.arguments[1]
                          for command in looked}, {"no", "yes", "GENERAL.STATE",
                                                   "GENERAL.CONNECTION", "GENERAL.HWADDR"})
        self.assertTrue(all(command.c_locale for command in looked), looked)

    def test_a_german_nmcli_is_asked_untranslated_so_the_wait_and_the_busy_retry_work(self):
        # A fake nmcli that answers in German unless it runs with LC_ALL=C.UTF-8 and without
        # LANGUAGE, as the real one does. The Pi may run with de_DE.UTF-8 and the look must
        # not depend on that.
        directory = self.record.parent / "german"
        directory.mkdir()
        (directory / "other").write_text(listing(("Neighbour", "11:22:33:44:55:66")))
        (directory / "home").write_text(listing(("Home", ROUTER_BSSID)))
        fake = directory / "nmcli"
        fake.write_text(r"""#!/bin/sh
dir=DIRECTORY
english() { [ "$LC_ALL" = C.UTF-8 ] && [ -z "$LANGUAGE" ]; }
case "$*" in
*GENERAL.STATE*)
    n=$(cat "$dir/reads" 2>/dev/null || echo 0)
    echo $((n + 1)) > "$dir/reads"
    if [ "$n" -lt 2 ]; then
        if english; then echo "20 (unavailable)"; else echo "20 (nicht verfuegbar)"; fi
    elif english; then echo "30 (disconnected)"; else echo "30 (getrennt)"; fi ;;
*GENERAL.CONNECTION*) echo "" ;;
*GENERAL.HWADDR*) echo 'B8\:27\:EB\:00\:00\:01' ;;
*"wifi list"*)
    if [ "$(cat "$dir/reads" 2>/dev/null || echo 0)" -lt 3 ]; then
        if english; then echo "Error: Scanning not allowed while unavailable." >&2
        else echo "Fehler: Scannen ist nicht erlaubt, solange das Geraet nicht verfuegbar ist." >&2
        fi
        exit 10
    fi
    case "$*" in
    *"--rescan yes"*)
        touch "$dir/rescanned"
        if english; then echo "Error: Device or resource busy" >&2
        else echo "Fehler: Das Geraet oder die Ressource ist belegt" >&2; fi
        exit 1 ;;
    *)  if [ -e "$dir/rescanned" ]; then cat "$dir/home"; else cat "$dir/other"; fi ;;
    esac ;;
esac
""".replace("DIRECTORY", str(directory)))
        fake.chmod(0o755)
        slept = []
        environment = {"PATH": "%s:%s" % (directory, os.environ["PATH"]), "LC_ALL": "de_DE.UTF-8",
                       "LANG": "de_DE.UTF-8", "LANGUAGE": "de"}
        with mock.patch.dict(os.environ, environment):
            controller = WifiController(SubprocessExecutor(timeout_seconds=15), "wlan0")
            result = wifi_follow.scan_air(controller, "Home", sleep=slept.append)
        self.assertEqual(result["result"], "seen", result)
        self.assertEqual(result["source"], "list after a busy radio")
        self.assertEqual(slept, [wifi_follow.READY_STEP] * 2 + [wifi_follow.BUSY_WAIT])

    def test_a_name_with_an_umlaut_still_matches_because_the_look_keeps_utf8(self):
        # With plain LC_ALL=C the real nmcli prints such a name with "?", so it would never
        # match and the look would silently do nothing for it.
        directory = self.record.parent / "umlaut"
        directory.mkdir()
        fake = directory / "nmcli"
        fake.write_text(r"""#!/bin/sh
case "$*" in
*GENERAL.STATE*) echo "30 (disconnected)" ;;
*GENERAL.CONNECTION*) echo "" ;;
*GENERAL.HWADDR*) echo 'B8\:27\:EB\:00\:00\:01' ;;
*"wifi list"*)
    case "$LC_ALL" in
    *UTF-8) printf 'FRITZ!Box M\303\274ller:AA\\:BB\\:CC\\:DD\\:EE\\:01\n' ;;
    *) printf 'FRITZ!Box M?ller:AA\\:BB\\:CC\\:DD\\:EE\\:01\n' ;;
    esac ;;
esac
""")
        fake.chmod(0o755)
        environment = {"PATH": "%s:%s" % (directory, os.environ["PATH"]), "LC_ALL": "de_DE.UTF-8",
                       "LANG": "de_DE.UTF-8", "LANGUAGE": "de"}
        with mock.patch.dict(os.environ, environment):
            controller = WifiController(SubprocessExecutor(timeout_seconds=15), "wlan0")
            result = wifi_follow.scan_air(controller, "FRITZ!Box Müller", sleep=lambda _: None)
        self.assertEqual(result["result"], "seen", result)
        self.assertEqual(result["bssid"], ROUTER_BSSID)

    def test_a_wlan0_that_stays_unavailable_fails_open_within_the_budget(self):
        refused = CommandExecutionError("nmcli failed", return_code=10,
                                        stderr="Error: Scanning not allowed while unavailable.")
        for slow in (False, True):
            with self.subTest(slow_calls=slow):
                self.setUp()
                self.slow_calls = slow
                self.at_boot()
                self.executor.device_state = "20 (unavailable)"
                self.executor.scan_replies = [refused, refused]
                self.assertEqual(self.boot(), "switched")
                self.assertLessEqual(sum(self.sleeps), wifi_follow.READY_WAIT)
                self.assertLessEqual(self.now, wifi_follow.SCAN_BUDGET)
                check = json.loads(self.check.read_text())
                self.assertIn(check["scan_result"], ("failed", "timeout"))
                if not slow:
                    self.assertEqual(check["scan_result"], "failed")
                    self.assertIn("unavailable", check["scan_detail"])

    def test_the_look_stays_within_its_budget_when_every_call_takes_its_whole_timeout(self):
        timeout = CommandExecutionError("nmcli timed out", timed_out=True)
        busy = CommandExecutionError("nmcli failed", return_code=1,
                                     stderr="Error: Device or resource busy")
        other = listing(("Neighbour", "11:22:33:44:55:66"))
        cases = {
            "wlan0 never becomes available": dict(device_state="20 (unavailable)",
                                                  scan_replies=[timeout, timeout]),
            "wlan0 is unknown to NetworkManager": dict(
                device_state=CommandExecutionError("Device not found"),
                scan_replies=[timeout, timeout]),
            "wlan0 becomes available late": dict(
                device_states=["20 (unavailable)"] * 6, scan_replies=[other, busy, other]),
            "the radio stays busy": dict(scan_replies=[other, busy, busy]),
            "every list times out": dict(scan_replies=[timeout, timeout, timeout]),
        }
        for name, settings in cases.items():
            with self.subTest(name):
                self.setUp()
                self.slow_calls = True
                self.at_boot()
                for key, value in settings.items():
                    setattr(self.executor, key, value)
                self.boot()
                self.assertLessEqual(self.now, wifi_follow.SCAN_BUDGET)
                for command in self.executor.commands:
                    if command.purpose == LOOK_PURPOSE:
                        self.assertGreaterEqual(command.timeout_seconds, 1)

    def test_held_or_changed_wifis_are_not_scanned(self):
        self.at_boot()
        wifi_follow.use_recovery(self.controller, self.hold)
        self.executor.active = ""
        start = len(self.executor.commands)
        self.assertEqual(self.boot(), "recovery-hold")
        self.assertEqual(self.lists(start), [])
        self.assertEqual(json.loads(self.check.read_text())["reason"], "an operator holds Recovery")

    def test_the_cli_boot_says_why_and_status_shows_the_decision(self):
        self.at_boot()
        self.executor.scan_output = listing(("Home", ROUTER_BSSID))
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = wifi_follow.main(["boot"], executor=self.executor, record_path=self.record,
                                    hold_path=self.hold, previous_path=self.previous,
                                    lock_path=self.lock, fingerprint=self.fingerprint,
                                    check_path=self.check, scan=self.scan)
        self.assertEqual(code, 0)
        self.assertIn("Wi-Fi at boot: router-on-air", output.getvalue())
        self.assertIn("Why: the router name is on the air from %s" % ROUTER_BSSID,
                      output.getvalue())
        code, text = self.run_cli("status")
        self.assertIn("boot_decision: router-on-air (", text)

    def test_status_without_a_boot_record_says_so(self):
        self.copy_router()
        self.assertIn("boot_decision: - (no boot step ran", self.run_cli("status")[1])

    def test_the_scan_parser_handles_escapes_and_bad_lines(self):
        output = listing(("Ho:me\\x", ROUTER_BSSID)) + "garbage\n" + "Home:not-a-mac\n"
        self.assertEqual(wifi_follow.parse_scan(output),
                         [{"ssid": "Ho:me\\x", "bssid": ROUTER_BSSID}])
        with self.assertRaises(ValueError):
            wifi_follow.parse_scan("x" * (wifi_follow.MAX_SCAN_BYTES + 1))


class IpExecutor:
    def __init__(self, reply):
        self.reply = reply
        self.commands = []

    def run(self, command):
        self.commands.append(command)
        if isinstance(self.reply, Exception):
            raise self.reply
        return CommandResult(0, stdout=self.reply)


def addresses(*cidrs):
    return json.dumps([{"ifname": "eth0", "addr_info": [
        {"family": "inet", "local": cidr.split("/")[0], "prefixlen": int(cidr.split("/")[1])}
        for cidr in cidrs]}])


class UplinkCheckTests(unittest.TestCase):
    """D3: no test Wi-Fi when the Ethernet uplink sits in 192.168.50.0/24."""

    def cli(self, reply, uplink="eth0"):
        output, errors = io.StringIO(), io.StringIO()
        executor = IpExecutor(reply)
        with redirect_stdout(output), redirect_stderr(errors):
            code = wifi_follow.main(["uplink-check", "--uplink=" + uplink], executor=executor)
        return code, output.getvalue() + errors.getvalue(), executor

    def test_an_uplink_in_the_wifi_subnet_refuses_the_start(self):
        for cidrs in (("192.168.50.23/24",), ("192.168.0.5/24", "192.168.50.2/24"),
                      ("192.168.0.0/16",)):
            with self.subTest(cidrs=cidrs):
                code, text, executor = self.cli(addresses(*cidrs))
                self.assertEqual(code, wifi_follow.UPLINK_CONFLICT_EXIT)
                self.assertIn("does not start", text)
                self.assertIn("192.168.50.0/24", text)
                self.assertEqual(executor.commands[0].arguments,
                                 ("-j", "-4", "address", "show", "dev", "eth0"))

    def test_another_subnet_or_any_error_lets_the_wifi_start(self):
        for reply in (addresses("192.168.0.23/24"), addresses("192.168.51.2/24"), "[]",
                      "not json", '[{"addr_info": [{"family": "inet"}]}]',
                      CommandExecutionError("ip failed")):
            with self.subTest(reply=str(reply)[:30]):
                code, text, _ = self.cli(reply)
                self.assertEqual(code, 0, text)

    def test_an_unusable_uplink_name_is_never_passed_to_ip(self):
        for uplink in ("-x", "wlan0"):  # wlan0 is the test interface.
            with self.subTest(uplink=uplink):
                code, text, executor = self.cli(addresses("192.168.50.2/24"), uplink=uplink)
                self.assertEqual(code, 0)
                self.assertEqual(executor.commands, [])

    def test_only_a_found_conflict_stops_the_unit(self):
        # Exit 1 is a crash, 2 an argparse error (an older module without the action).
        unit = (Path(__file__).resolve().parents[2] / "deploy" / "raspberry-pi"
                / "jittersim-ap.service").read_text().splitlines()
        line = [line for line in unit if "uplink-check" in line and line.startswith("ExecStartPre=")]
        self.assertEqual(len(line), 1)
        script = line[0].split("-c '", 1)[1].rsplit("'", 1)[0].replace("$$", "$")
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "python"
            for status, stops in ((0, False), (1, False), (2, False),
                                  (wifi_follow.UPLINK_CONFLICT_EXIT, True)):
                with self.subTest(status=status):
                    fake.write_text("#!/bin/sh\nexit %d\n" % status)
                    fake.chmod(0o755)
                    done = subprocess.run(
                        ["/bin/sh", "-c", script.replace("/opt/jittersim/.venv/bin/python",
                                                         str(fake))], timeout=10)
                    self.assertEqual(done.returncode != 0, stops)

    def test_the_unit_checks_the_uplink_before_the_rename_and_the_start(self):
        unit = (Path(__file__).resolve().parents[2] / "deploy" / "raspberry-pi"
                / "jittersim-ap.service").read_text().splitlines()
        pre = [line.split("=", 1)[1] for line in unit if line.startswith("ExecStartPre=")]
        self.assertIn("nft list table", pre[0])
        self.assertTrue(pre[1].startswith("-") and pre[1].endswith("wifi_follow boot"))
        # Last before the start, without "-": its own exit status 3 stops the unit.
        self.assertIn("wifi_follow uplink-check;", pre[2])
        self.assertFalse(pre[2].startswith("-"))
        self.assertEqual(len(pre), 3)


if __name__ == "__main__":
    unittest.main()
