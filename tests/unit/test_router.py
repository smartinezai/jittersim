import hashlib
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from Crypto.Cipher import AES

from jittersim.networking.router import (
    RouterError,
    TG3442DERouter,
    _EndpointUnavailable,
    _Form,
    _FormParser,
    _wifi_enabled,
)


class FirmwareServer(ThreadingHTTPServer):
    """Serve the protocol used by the Vodafone firmware with harmless settings."""

    daemon_threads = True
    password = "router-test-password"
    salt = bytes.fromhex("0011223344556677")
    iv = bytes.fromhex("1020304050607080")
    session_id = "session-test"
    nonce = "0123456789abcdef" * 3

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FirmwareHandler)
        self.key = hashlib.pbkdf2_hmac("sha256", self.password.encode(), self.salt, 1000, 16)
        self.calls = []
        self.settings = {
            "SSID": "Home-Test",
            "Passphrase": "wifi-test-password",
            "Enable": True,
            "SSID5G": "Home-Test",
            "Passphrase5G": "wifi-test-password",
            "Enable5G": True,
            "EnableWiFiFunction": True,
            "BandSteerEnable": True,
            "SplitSSIDEnable": False,
            "SSIDAdvertisementEnabled": True,
            "SSIDAdvertisementEnabled5G": False,
            "ModeEnabled": "WPA2-Personal",
            "ModeEnabled5G": "WPA2-Personal",
            "EnableGuest": False,
            "SSIDGuest": "Guest-Test",
            "ModeEnabledGuest": "WPA2-Personal",
            "PassphraseGuest": "",
            "IsolationEnabledGuest": True,
            "DefaultKeyPassphrase": "display-only-placeholder",
            "WiFiSchedule": [],
        }
        self.api_status = 200
        self.tamper_wifi = False
        self.tamper_nonce = False
        self.login_status = "AdminMatch"
        self.legacy_html = ""
        self.writes = []
        self.partial_write = False
        self.fail_write = False
        self.tamper_after_write = False
        self.slow_wifi = False
        self.unexpected_band = False
        self.omit_wifi_master = False
        self.numeric_security_modes = False
        self.effective_band_reads = False
        self.write_delay = 0.0
        self.drop_write_reply = False
        self.master_page_value = None
        self.conflicting_master_page = False
        self.omit_master_page = False

    def seal(self, value, auth_data):
        cipher = AES.new(self.key, AES.MODE_CCM, nonce=self.iv, mac_len=16)
        cipher.update(auth_data.encode())
        ciphertext, tag = cipher.encrypt_and_digest(value.encode())
        return (ciphertext + tag).hex()


class FirmwareHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, body, *, status=200, headers=None):
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)

    def authenticated(self):
        return self.headers.get(
            "csrfNonce"
        ) == self.server.nonce and "PHPSESSID=fixture-session" in self.headers.get("Cookie", "")

    def do_GET(self):
        parsed = urlsplit(self.path)
        self.server.calls.append(("GET", parsed.path))
        if parsed.path == "/" and parsed.query:
            self.respond(self.server.legacy_html)
        elif parsed.path == "/":
            master = self.server.master_page_value
            if master is None:
                master = {True: "true", False: "false"}.get(self.server.settings.get("EnableWiFiFunction"), "unknown")
            master_script = "" if self.server.omit_master_page else f'_ga.isWifiEnabled="{master}";'
            if self.server.conflicting_master_page:
                master_script += '_ga.isWifiEnabled="false";_ga.isWifiEnabled="true";'
            self.respond(
                f'<script>var currentSessionId="{self.server.session_id}";'
                f'var myIv="{self.server.iv.hex()}";'
                f'var mySalt="{self.server.salt.hex()}";var _ga={{}};{master_script}</script>'
            )
        elif parsed.path == "/base_95x.js":
            self.respond("// Firmware uses a server session cookie.")
        elif parsed.path == "/php/wifi_general_data.php":
            if not self.authenticated():
                self.respond("not authenticated", status=401)
                return
            if self.server.api_status != 200:
                self.respond("endpoint unavailable", status=self.server.api_status)
                return
            if json.loads(unquote(parsed.query)) != {"IsManualOperation": "false"}:
                self.respond("unexpected query", status=400)
                return
            if self.server.slow_wifi:
                self.send_response(200)
                self.send_header("Content-Length", "1000")
                self.end_headers()
                try:
                    for _ in range(1000):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.005)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            settings = dict(self.server.settings)
            if self.server.effective_band_reads and not settings["EnableWiFiFunction"]:
                settings["Enable"] = settings["Enable5G"] = False
            if self.server.omit_wifi_master:
                settings.pop("EnableWiFiFunction")
            encrypted = self.server.seal(json.dumps(settings), "wifidata")
            if self.server.tamper_wifi:
                encrypted = encrypted[:-2] + ("00" if encrypted[-2:] != "00" else "ff")
            self.respond(json.dumps({"wifidata": encrypted}))
        else:
            self.respond("unexpected endpoint", status=404)

    def do_POST(self):
        path = urlsplit(self.path).path
        self.server.calls.append(("POST", path))
        if path == "/php/ajaxSet_Password.php":
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            encrypted = bytes.fromhex(body["EncryptData"])
            cipher = AES.new(self.server.key, AES.MODE_CCM, nonce=self.server.iv, mac_len=16)
            cipher.update(b"loginPassword")
            try:
                credentials = json.loads(
                    cipher.decrypt_and_verify(encrypted[:-16], encrypted[-16:])
                )
            except ValueError:
                self.respond(json.dumps({"p_status": "NoMatch", "detail": "AdminMatch"}))
                return
            if credentials != {
                "Password": self.server.password,
                "Nonce": self.server.session_id,
            }:
                self.respond("unexpected login", status=400)
                return
            nonce = self.server.seal(self.server.nonce, "nonce")
            if self.server.tamper_nonce:
                nonce = nonce[:-2] + ("00" if nonce[-2:] != "00" else "ff")
            self.respond(
                json.dumps({"p_status": self.server.login_status, "encryptData": nonce}),
                headers={"Set-Cookie": "PHPSESSID=fixture-session; Path=/"},
            )
        elif path == "/php/ajaxSet_wifi_general_data.php":
            if not self.authenticated():
                self.respond("not authenticated", status=401)
                return
            envelope = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if envelope["Name"] != "admin" or envelope["AuthData"] != "wifidata":
                self.respond("unexpected write envelope", status=400)
                return
            ciphertext = bytes.fromhex(envelope["EncryptData"])
            cipher = AES.new(self.server.key, AES.MODE_CCM, nonce=self.server.iv, mac_len=16)
            cipher.update(b"wifidata")
            payload = json.loads(cipher.decrypt_and_verify(ciphertext[:-16], ciphertext[-16:]))
            modes = {"None": 0, "WPA2-Personal": 4, "WPA-WPA2-Personal": 5}
            for name in ("ModeEnabled", "ModeEnabled5G", "ModeEnabledGuest"):
                if payload[name] not in modes:
                    self.respond("invalid security mode", status=400)
                    return
            self.server.writes.append(payload)
            if not payload["SplitSSIDEnable"] and (
                payload["Enable"] != 1 or payload["Enable5G"] != 1
            ):
                self.respond("invalid shared SSID bands", status=422)
                return
            if self.server.fail_write:
                self.respond("write result unavailable", status=503)
                return
            if self.server.partial_write:
                self.server.settings["Enable"] = payload["Enable"]
            else:
                self.server.settings.update(payload)
                if self.server.numeric_security_modes:
                    for name in ("ModeEnabled", "ModeEnabled5G", "ModeEnabledGuest"):
                        self.server.settings[name] = modes[payload[name]]
            if self.server.unexpected_band:
                self.server.settings["Enable5G"] = not bool(payload["Enable5G"])
            self.server.tamper_wifi = self.server.tamper_after_write
            if self.server.write_delay:
                time.sleep(self.server.write_delay)
            if self.server.drop_write_reply:
                self.close_connection = True
                return
            self.respond("{}")
        elif path == "/php/ajaxSet_Session.php":
            self.respond("{}", status=200 if self.authenticated() else 401)
        elif path == "/php/logout.php":
            self.respond("{}")
        else:
            self.respond("unexpected write", status=400)


class RouterProtocolTests(unittest.TestCase):
    def setUp(self):
        self.firmware = FirmwareServer()
        self.thread = threading.Thread(
            target=lambda: self.firmware.serve_forever(poll_interval=0.01),
            daemon=True,
        )
        self.thread.start()
        self.router = TG3442DERouter(address="127.0.0.1", password=self.firmware.password)
        self.router.base_url = f"http://127.0.0.1:{self.firmware.server_port}"
        self.router._CONFIRMATION_DELAY_SECONDS = 0.01

    def tearDown(self):
        self.router.close()
        self.firmware.shutdown()
        self.firmware.server_close()
        self.thread.join(timeout=2)

    def test_slow_write_and_effective_band_reads_confirm_off_and_restore_on(self):
        self.firmware.omit_wifi_master = True
        self.firmware.effective_band_reads = True
        self.firmware.write_delay = 0.12
        self.router.timeout_seconds = 0.02
        self.router._OPERATION_TIMEOUT_SECONDS = 0.8
        self.router.login()
        before = dict(self.firmware.settings)
        for enabled in (False, True):
            self.assertIs(self.router.set_wifi_enabled(enabled=enabled).enabled, enabled)
        self.assertEqual(len(self.firmware.writes), 2)
        self.assertEqual(self.firmware.settings, before)

    def test_slow_restart_confirms_after_a_minute_without_rewriting(self):
        self.firmware.omit_wifi_master = True
        self.firmware.effective_band_reads = True
        for read_duration in (0.0, 10.0):
            with self.subTest(read_duration=read_duration):
                clock = [1000.0]
                recovering_until = [0.0]
                original = self.router._request
                self.router._CONFIRMATION_DELAY_SECONDS = 10.0
                before = dict(self.firmware.settings)
                writes_before = len(self.firmware.writes)

                def sleep(seconds):
                    clock[0] += seconds

                def request(method, path, **kwargs):
                    self.router._remaining_timeout()
                    if path == self.router._WIFI_WRITE and method == "post":
                        recovering_until[0] = clock[0] + 74.0
                        original(method, path, **kwargs)
                        clock[0] += 30.0
                        raise RouterError("lost write reply", reason="unreachable")
                    if path == self.router._WIFI_DATA and clock[0] < recovering_until[0]:
                        clock[0] += read_duration
                        raise RouterError("router is restarting", reason="unreachable")
                    return original(method, path, **kwargs)

                with patch("jittersim.networking.router.time.monotonic", side_effect=lambda: clock[0]), \
                     patch("jittersim.networking.router.time.sleep", side_effect=sleep), \
                     patch.object(self.router, "_request", side_effect=request):
                    for enabled in (False, True):
                        self.router.password = self.firmware.password
                        self.router.login()
                        began = clock[0]
                        self.assertIs(self.router.set_wifi_enabled(enabled=enabled).enabled, enabled)
                        self.assertGreaterEqual(clock[0] - began, 74.0)
                        self.assertLessEqual(clock[0] - began, 120.0)
                        self.router.close()
                self.assertEqual(len(self.firmware.writes), writes_before + 2)
                self.assertEqual(self.firmware.settings, before)

    def test_restart_that_exceeds_the_deadline_stays_uncertain_without_rewriting(self):
        clock = [1000.0]
        restarting = [False]
        original = self.router._request
        self.router._CONFIRMATION_DELAY_SECONDS = 10.0

        def sleep(seconds):
            clock[0] += seconds

        def request(method, path, **kwargs):
            self.router._remaining_timeout()
            if path == self.router._WIFI_WRITE and method == "post":
                original(method, path, **kwargs)
                restarting[0] = True
                clock[0] += 30.0
                raise RouterError("lost write reply", reason="unreachable")
            if path == self.router._WIFI_DATA and restarting[0]:
                clock[0] += min(10.0, self.router._remaining_timeout())
                raise RouterError("router is still restarting", reason="unreachable")
            return original(method, path, **kwargs)

        with patch("jittersim.networking.router.time.monotonic", side_effect=lambda: clock[0]), \
             patch("jittersim.networking.router.time.sleep", side_effect=sleep), \
             patch.object(self.router, "_request", side_effect=request):
            self.router.login()
            with self.assertRaises(RouterError) as caught:
                self.router.set_wifi_enabled(enabled=False)
            self.assertEqual(caught.exception.reason, "uncertain")
            self.assertLessEqual(clock[0], self.router._deadline)
            self.router.close()
        self.assertEqual(len(self.firmware.writes), 1)
        self.assertEqual(self.router.password, "")

    def test_lost_write_reply_is_verified_without_another_write(self):
        self.firmware.omit_wifi_master = True
        self.firmware.effective_band_reads = True
        self.firmware.drop_write_reply = True
        self.router.login()
        for enabled in (False, True):
            self.assertIs(self.router.set_wifi_enabled(enabled=enabled).enabled, enabled)
        self.assertEqual(len(self.firmware.writes), 2)

    def test_matching_state_is_verified_without_writing(self):
        self.router.login()
        self.assertTrue(self.router.set_wifi_enabled(enabled=True).enabled)
        self.assertEqual(self.firmware.writes, [])

    def test_effective_response_rejects_split_or_guest_off_before_write(self):
        self.firmware.omit_wifi_master = True
        self.router.login()
        for field in ("SplitSSIDEnable", "EnableGuest"):
            with self.subTest(field=field):
                self.firmware.settings[field] = True
                with self.assertRaises(RouterError) as caught:
                    self.router.set_wifi_enabled(enabled=False)
                self.assertEqual(caught.exception.reason, "unsupported")
                self.firmware.settings[field] = False
        self.assertEqual(self.firmware.writes, [])

    def test_confirmation_refreshes_authentication_once_without_rewriting(self):
        self.router.login()
        original = self.router._read_verified_wifi_data
        deadline = self.router._deadline
        count = 0

        def read():
            nonlocal count
            count += 1
            if count == 2:
                raise RouterError("expired session", reason="login")
            return original()

        with patch.object(self.router, "_read_verified_wifi_data", side_effect=read):
            self.assertFalse(self.router.set_wifi_enabled(enabled=False).enabled)
        self.assertEqual(self.router._deadline, deadline)
        self.assertEqual(len(self.firmware.writes), 1)
        self.assertEqual(self.firmware.calls.count(("POST", "/php/ajaxSet_Password.php")), 2)

    def test_persistent_confirmation_authentication_loss_stops_after_one_refresh(self):
        self.router.login()
        original = self.router._read_verified_wifi_data
        count = 0

        def read():
            nonlocal count
            count += 1
            if count > 1:
                raise RouterError("expired session", reason="login")
            return original()

        with patch.object(self.router, "_read_verified_wifi_data", side_effect=read):
            with self.assertRaises(RouterError) as caught:
                self.router.set_wifi_enabled(enabled=False)
        self.assertEqual(caught.exception.reason, "uncertain")
        self.assertEqual(len(self.firmware.writes), 1)
        self.assertEqual(self.firmware.calls.count(("POST", "/php/ajaxSet_Password.php")), 2)

    def test_already_off_split_or_guest_settings_do_not_need_a_write(self):
        self.firmware.omit_wifi_master = True
        self.router.login()
        for settings in (
            dict(SplitSSIDEnable=True, Enable=False, Enable5G=False),
            dict(SplitSSIDEnable=False, Enable=True, Enable5G=True, EnableGuest=True),
        ):
            self.firmware.settings.update(settings, EnableWiFiFunction=False)
            self.assertFalse(self.router.set_wifi_enabled(enabled=False).enabled)
        self.assertEqual(self.firmware.writes, [])

    def test_login_refresh_keeps_the_original_deadline(self):
        self.router.login()
        deadline = self.router._deadline
        self.router.session.cookies.clear()
        self.router.session.headers.pop("csrfNonce", None)
        self.router.login(preserve_deadline=True)
        self.assertEqual(self.router._deadline, deadline)

    def test_numeric_security_modes_are_named_in_writes_and_preserved_on_readback(self):
        self.firmware.numeric_security_modes = True
        self.firmware.omit_wifi_master = True
        names = ("ModeEnabled", "ModeEnabled5G", "ModeEnabledGuest")
        modes = {0: "None", 4: "WPA2-Personal", 5: "WPA-WPA2-Personal"}
        self.router.login()
        for values in ((0, 4, 5), (4, 5, 0), (5, 0, 4), ("4", "5", "0")):
            with self.subTest(values=values):
                self.firmware.settings.update(zip(names, values))
                before = dict(self.firmware.settings)
                for enabled in (False, True):
                    self.assertIs(self.router.set_wifi_enabled(enabled=enabled).enabled, enabled)
                    posted = self.firmware.writes[-1]
                    for name, value in zip(names, values):
                        self.assertEqual(posted[name], modes[int(value)])
                        self.assertEqual(self.firmware.settings[name], int(value))
                    for name in set(before) - set(names) - {"EnableWiFiFunction"}:
                        self.assertEqual(self.firmware.settings[name], before[name])
                    self.assertEqual(self.firmware.settings["EnableWiFiFunction"], int(enabled))

    def test_unknown_or_boolean_security_modes_fail_before_write(self):
        self.router.login()
        before = dict(self.firmware.settings)
        for name in ("ModeEnabled", "ModeEnabled5G", "ModeEnabledGuest"):
            for value in (False, True, 10, "10", "WPA3-Personal-Transition", "unknown"):
                with self.subTest(name=name, value=value):
                    self.firmware.settings = dict(before, **{name: value})
                    with self.assertRaises(RouterError) as caught:
                        self.router.set_wifi_enabled(enabled=False)
                    self.assertEqual(caught.exception.reason, "unsupported")
        self.assertEqual(self.firmware.writes, [])

    def test_toggle_reads_missing_master_from_the_authenticated_page(self):
        self.firmware.omit_wifi_master = True
        self.router.login()
        for enabled in (False, True):
            self.assertIs(self.router.set_wifi_enabled(enabled=enabled).enabled, enabled)
        self.assertEqual(len(self.firmware.writes), 2)
        self.assertTrue(self.firmware.settings["Enable"])
        self.assertTrue(self.firmware.settings["Enable5G"])

    def test_missing_master_source_rejects_unknown_and_conflicting_states_before_write(self):
        self.firmware.omit_wifi_master = True
        self.router.login()
        for value, conflicting in (("unknown", False), ("true", True)):
            with self.subTest(value=value, conflicting=conflicting):
                self.firmware.master_page_value = value
                self.firmware.conflicting_master_page = conflicting
                with self.assertRaises(RouterError):
                    self.router.set_wifi_enabled(enabled=False)
        self.assertEqual(self.firmware.writes, [])

    def test_missing_master_page_state_sends_no_write(self):
        self.firmware.omit_wifi_master = True
        self.firmware.omit_master_page = True
        self.router.login()
        with self.assertRaises(RouterError) as caught:
            self.router.set_wifi_enabled(enabled=False)
        self.assertEqual(caught.exception.reason, "unsupported")
        self.assertEqual(self.firmware.writes, [])

    def test_unavailable_master_page_does_not_switch_to_the_legacy_writer(self):
        self.firmware.omit_wifi_master = True
        self.router.login()
        original_request = self.router._request

        def request(method, path, **kwargs):
            if path == "/":
                raise _EndpointUnavailable("not available")
            return original_request(method, path, **kwargs)

        with patch.object(self.router, "_request", side_effect=request), patch.object(
            self.router, "_set_legacy_wifi_enabled",
        ) as legacy:
            with self.assertRaises(RouterError) as caught:
                self.router.set_wifi_enabled(enabled=False)
        self.assertEqual(caught.exception.reason, "unsupported")
        legacy.assert_not_called()
        self.assertEqual(self.firmware.writes, [])

    def test_master_page_failure_after_write_reports_uncertainty_without_retrying(self):
        self.firmware.omit_wifi_master = True
        self.router.login()
        with patch.object(self.router, "_read_master_wifi_state", side_effect=[
            True, *[
                RouterError("page unavailable", reason="unreachable")
                for _ in range(self.router._CONFIRMATION_ATTEMPTS)
            ],
        ]):
            with self.assertRaises(RouterError) as caught:
                self.router.set_wifi_enabled(enabled=False)
        self.assertEqual(caught.exception.reason, "uncertain")
        self.assertEqual(len(self.firmware.writes), 1)

    def test_master_page_object_and_assignment_must_agree(self):
        with patch.object(self.router, "_request", return_value='var _ga={"isWifiEnabled":false};'):
            self.assertFalse(self.router._read_master_wifi_state())
        with patch.object(self.router, "_request", return_value=(
            'var _ga={"isWifiEnabled":false};_ga.isWifiEnabled="true";'
        )):
            with self.assertRaises(RouterError):
                self.router._read_master_wifi_state()

    def test_reads_encrypted_settings_with_verified_nonce_and_session_cookie(self):
        self.router.login()
        settings = self.router.read_wifi_settings()

        self.assertEqual(settings.ssid, "Home-Test")
        self.assertEqual(settings.password, "wifi-test-password")
        self.assertTrue(settings.enabled)
        self.assertEqual(self.router.csrf_nonce, self.firmware.nonce)
        self.assertEqual(
            [path for method, path in self.firmware.calls if method == "POST"],
            ["/php/ajaxSet_Password.php", "/php/ajaxSet_Session.php"],
        )
        self.router.close()
        self.assertEqual(self.router.password, "")
        self.assertIsNone(self.router._session_key)
        self.assertIsNone(self.router._session_iv)

    def test_rejects_failed_login_even_when_response_mentions_admin_match(self):
        self.router.password = "wrong-router-password"
        with self.assertRaises(RouterError) as caught:
            self.router.login()
        self.assertEqual(caught.exception.reason, "login")
        self.assertNotIn(("GET", "/php/wifi_general_data.php"), self.firmware.calls)

    def test_rejects_tampered_nonce_before_using_the_session(self):
        self.firmware.tamper_nonce = True
        with self.assertRaises(RouterError) as caught:
            self.router.login()
        self.assertEqual(caught.exception.reason, "login")
        self.assertIsNone(self.router._session_key)

    def test_rejects_tampered_settings_instead_of_using_unverified_credentials(self):
        self.router.login()
        self.firmware.tamper_wifi = True
        with self.assertRaises(RouterError) as caught:
            self.router.read_wifi_settings()
        self.assertEqual(caught.exception.reason, "invalid")
        self.assertNotIn("wifi-test-password", str(caught.exception))

    def test_copies_the_enabled_five_ghz_band_when_two_point_four_is_disabled(self):
        self.router.login()
        self.firmware.settings.update(
            Enable=False,
            SSID5G="Five-Test",
            Passphrase5G="five-test-password",
        )
        settings = self.router.read_wifi_settings()
        self.assertEqual((settings.ssid, settings.password), ("Five-Test", "five-test-password"))

    def test_does_not_mix_a_wifi_name_and_password_from_incomplete_different_bands(self):
        self.router.login()
        self.firmware.settings.pop("Passphrase")
        self.firmware.settings.pop("SSID5G")
        with self.assertRaises(RouterError) as caught:
            self.router.read_wifi_settings()
        self.assertEqual(caught.exception.reason, "credentials")

    def test_reports_missing_radio_state_as_unknown_while_copying_complete_credentials(self):
        self.router.login()
        for name in ("Enable", "Enable5G", "EnableWiFiFunction"):
            self.firmware.settings.pop(name)
        settings = self.router.read_wifi_settings()
        self.assertIsNone(settings.enabled)
        self.assertEqual(settings.ssid, "Home-Test")

    def test_master_radio_off_overrides_individual_enabled_bands(self):
        self.router.login()
        self.firmware.settings["EnableWiFiFunction"] = False
        self.assertFalse(self.router.read_wifi_settings().enabled)

    def test_unrecognized_master_radio_state_is_not_reported_as_enabled_or_disabled(self):
        self.router.login()
        self.firmware.settings["EnableWiFiFunction"] = "unrecognized"
        self.assertIsNone(self.router.read_wifi_settings().enabled)

    def test_older_firmware_can_still_read_the_legacy_form(self):
        self.router.login()
        self.firmware.api_status = 404
        self.firmware.legacy_html = (
            '<form><input name="wifi_ssid24" value="Legacy-Test">'
            '<input name="wifi_password24" value="legacy-test-password">'
            '<input name="wifi_on_state" value="0"></form>'
        )
        settings = self.router.read_wifi_settings()
        self.assertEqual(
            (settings.ssid, settings.password), ("Legacy-Test", "legacy-test-password")
        )
        self.assertFalse(settings.enabled)

    def test_settings_response_limit_fails_before_parsing_large_data(self):
        self.router.login()
        self.router._MAX_RESPONSE_BYTES = 128
        with self.assertRaises(RouterError) as caught:
            self.router.read_wifi_settings()
        self.assertEqual(caught.exception.reason, "invalid")

    def test_changes_both_radios_off_and_on_with_verified_encrypted_writes(self):
        self.router.login()
        before = dict(self.firmware.settings)
        for enabled in (False, True):
            self.assertIs(self.router.set_wifi_enabled(enabled=enabled).enabled, enabled)
            payload = self.firmware.writes[-1]
            self.assertEqual(payload["EnableWiFiFunction"], int(enabled))
            self.assertEqual(payload["Enable"], 1)
            self.assertEqual(payload["Enable5G"], 1)
            for name in (
                "SSID",
                "SSID5G",
                "BandSteerEnable",
                "SplitSSIDEnable",
                "SSIDAdvertisementEnabled",
                "SSIDAdvertisementEnabled5G",
                "ModeEnabled",
                "ModeEnabled5G",
                "EnableGuest",
                "SSIDGuest",
                "ModeEnabledGuest",
                "IsolationEnabledGuest",
            ):
                self.assertEqual(payload[name], before[name])
            self.assertFalse(
                set(payload)
                & {
                    "Passphrase",
                    "Passphrase5G",
                    "PassphraseGuest",
                    "DefaultKeyPassphrase",
                    "WiFiSchedule",
                }
            )
            for name in ("Passphrase", "Passphrase5G", "PassphraseGuest"):
                self.assertEqual(self.firmware.settings[name], before[name])
        self.assertEqual(len(self.firmware.writes), 2)

    def test_split_ssid_preserves_an_intentionally_disabled_band_on_off_and_on(self):
        self.router.login()
        self.firmware.settings.update(SplitSSIDEnable=True, Enable=False, Enable5G=True)
        for enabled in (False, True):
            self.assertIs(self.router.set_wifi_enabled(enabled=enabled).enabled, enabled)
            posted = self.firmware.writes[-1]
            self.assertEqual(posted["Enable"], 0)
            self.assertEqual(posted["Enable5G"], 1)
            self.assertEqual(posted["EnableWiFiFunction"], int(enabled))

    def test_split_ssid_with_both_bands_disabled_does_not_claim_enablement(self):
        self.router.login()
        self.firmware.settings.update(SplitSSIDEnable=True, Enable=False, Enable5G=False)
        with self.assertRaises(RouterError) as caught:
            self.router.set_wifi_enabled(enabled=True)
        self.assertEqual(caught.exception.reason, "disabled")
        self.assertEqual(self.firmware.writes, [])

    def test_missing_or_unknown_radio_state_fails_before_any_write(self):
        self.router.login()
        before = dict(self.firmware.settings)
        for name in ("Enable", "Enable5G", "EnableWiFiFunction"):
            for missing in (False, True):
                with self.subTest(name=name, missing=missing):
                    self.firmware.settings = dict(before)
                    if missing:
                        self.firmware.settings.pop(name)
                    else:
                        self.firmware.settings[name] = "unknown"
                    with self.assertRaises(RouterError) as caught:
                        self.router.set_wifi_enabled(enabled=False)
                    self.assertEqual(caught.exception.reason, "unsupported")
        self.assertEqual(self.firmware.writes, [])

    def test_partial_radio_change_is_not_success_and_is_never_written_twice(self):
        self.router.login()
        self.firmware.settings.update(Enable=False, Enable5G=False, EnableWiFiFunction=False)
        self.firmware.partial_write = True
        with patch("jittersim.networking.router.time.sleep"):
            with self.assertRaises(RouterError) as caught:
                self.router.set_wifi_enabled(enabled=True)
        self.assertEqual(caught.exception.reason, "uncertain")
        self.assertEqual(len(self.firmware.writes), 1)
        self.assertEqual(
            self.firmware.calls.count(("GET", "/php/wifi_general_data.php")),
            1 + self.router._CONFIRMATION_ATTEMPTS,
        )
        self.assertNotIn(("GET", "/"), self.firmware.calls[4:])

    def test_master_confirmation_does_not_hide_an_unexpected_band_change(self):
        self.router.login()
        self.firmware.settings.update(SplitSSIDEnable=True, Enable=False, Enable5G=True)
        self.firmware.unexpected_band = True
        with patch("jittersim.networking.router.time.sleep"):
            with self.assertRaises(RouterError) as caught:
                self.router.set_wifi_enabled(enabled=False)
        self.assertEqual(caught.exception.reason, "uncertain")
        self.assertFalse(self.firmware.settings["EnableWiFiFunction"])
        self.assertEqual(len(self.firmware.writes), 1)

    def test_tampered_initial_settings_fail_before_write(self):
        self.router.login()
        self.firmware.tamper_wifi = True
        with self.assertRaises(RouterError) as caught:
            self.router.set_wifi_enabled(enabled=False)
        self.assertEqual(caught.exception.reason, "invalid")
        self.assertEqual(self.firmware.writes, [])

    def test_unverified_post_result_is_uncertain_without_retry_or_legacy_fallback(self):
        self.router.login()
        for failed_post in (False, True):
            with self.subTest(failed_post=failed_post):
                self.firmware.tamper_wifi = False
                self.firmware.tamper_after_write = not failed_post
                self.firmware.fail_write = failed_post
                self.firmware.settings["EnableWiFiFunction"] = True
                previous = len(self.firmware.writes)
                with self.assertRaises(RouterError) as caught:
                    self.router.set_wifi_enabled(enabled=False)
                self.assertEqual(caught.exception.reason, "uncertain")
                self.assertEqual(len(self.firmware.writes), previous + 1)
                self.assertNotIn("wifi-test-password", str(caught.exception))

    def test_expired_operation_budget_sends_no_request_and_clears_secrets(self):
        self.router.login()
        before = len(self.firmware.calls)
        self.router._deadline = time.monotonic() - 1
        with self.assertRaises(RouterError):
            self.router.set_wifi_enabled(enabled=False)
        self.router.close()
        self.assertEqual(len(self.firmware.calls), before)
        self.assertEqual(self.router.password, "")
        self.assertIsNone(self.router._session_key)

    def test_shared_deadline_stops_a_response_that_keeps_sending_small_chunks(self):
        self.router.login()
        self.firmware.slow_wifi = True
        started = time.monotonic()
        self.router._deadline = started + 0.03
        with self.assertRaises(RouterError):
            self.router.read_wifi_settings()
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.firmware.writes, [])

    def test_each_request_uses_the_remaining_shared_budget(self):
        self.router.login()
        self.router._deadline = time.monotonic() + 1
        with patch.object(
            self.router.session, "request", wraps=self.router.session.request
        ) as request:
            self.router.read_wifi_settings()
            self.router.close()
        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            self.assertGreater(call.kwargs["timeout"], 0)
            self.assertLessEqual(call.kwargs["timeout"], 1)


class RouterFormTests(unittest.TestCase):
    def test_parser_keeps_selected_wifi_controls(self) -> None:
        parser = _FormParser()
        parser.feed(
            """
            <form action=\"/php/save_wifi.php\" method=\"post\">
              <input type=\"hidden\" name=\"wifi_ssid24\" value=\"Vodafone-Test\">
              <input type=\"password\" name=\"wifi_password24\" value=\"secret\">
              <input type=\"checkbox\" name=\"wifi_on_state\" value=\"1\" checked>
              <input type=\"checkbox\" name=\"main_wifi24_state\" value=\"1\" checked>
              <select name=\"channel\"><option value=\"auto\" selected>Auto</option></select>
            </form>
            """
        )

        self.assertEqual(len(parser.forms), 1)
        form = parser.forms[0]
        self.assertEqual(form.action, "/php/save_wifi.php")
        self.assertEqual(form.controls["wifi_ssid24"], "Vodafone-Test")
        self.assertEqual(form.controls["wifi_password24"], "secret")
        self.assertEqual(form.controls["channel"], "auto")
        self.assertTrue(_wifi_enabled(form.controls))

    def test_set_wifi_enabled_posts_all_present_radio_states_and_verifies(self) -> None:
        initial = _Form(
            action="/php/save_wifi.php",
            method="post",
            controls={
                "wifi_ssid24": "Vodafone-Test",
                "wifi_password24": "secret",
                "wifi_on_state": "1",
                "main_wifi24_state": "1",
                "main_wifi5_state": "1",
            },
        )
        updated = _Form(
            action="/php/save_wifi.php",
            method="post",
            controls={
                "wifi_on_state": "0",
                "main_wifi24_state": "0",
                "main_wifi5_state": "0",
            },
        )
        router = object.__new__(TG3442DERouter)
        router.base_url = "http://192.168.0.1"
        forms = iter(((initial, ""), (updated, "")))
        requests = []
        router._wifi_form = lambda: next(forms)
        router._read_wifi_data = lambda: (_ for _ in ()).throw(
            _EndpointUnavailable("older firmware")
        )
        router._request = lambda method, path, **kwargs: (
            requests.append((method, path, kwargs)) or ""
        )

        settings = router.set_wifi_enabled(enabled=False)

        self.assertFalse(settings.enabled)
        self.assertEqual(requests[0][0], "post")
        self.assertEqual(requests[0][1], "http://192.168.0.1/php/save_wifi.php")
        self.assertEqual(
            {
                name: requests[0][2]["data"][name]
                for name in ("wifi_on_state", "main_wifi24_state", "main_wifi5_state")
            },
            {
                "wifi_on_state": "0",
                "main_wifi24_state": "0",
                "main_wifi5_state": "0",
            },
        )


if __name__ == "__main__":
    unittest.main()
