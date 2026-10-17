"""The Trust this Pi downloads need the admin token and a registered device."""

import json
import plistlib
import shutil
import socket
import ssl
import tempfile
import threading
import unittest
from email.message import Message
from pathlib import Path

from jittersim import localca
from jittersim.api.server import (
    DASHBOARD_HTML,
    PUBLIC_READ_PATHS,
    ControlRequestHandler,
    ControlServer,
)
from jittersim.config.storage import ConfigRepository
from tests.tls_support import needs_openssl

TOKEN = "trust-test-token"


@unittest.skipUnless(shutil.which("openssl"), "openssl is required")
class TrustDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Path(self.directory.name)
        localca.prepare(self.config, host_name="pi-lab.local", neutral_name="netlab.local",
                        ethernet_ip="192.168.1.20")
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.server = ControlServer(
            ("127.0.0.1", 0), self.repository, admin_token=TOKEN,
            trust_ca_path=self.config / "ca" / "ca.crt", local_name="netlab.local")
        self.addCleanup(self.server.server_close)

    def get(self, path, token=TOKEN, peer="127.0.0.1"):
        handler = object.__new__(ControlRequestHandler)
        handler.server = self.server
        handler.path = path
        handler.client_address = (peer, 45000)
        handler.headers = Message()
        if token:
            handler.headers["Authorization"] = "Bearer " + token
        captured = {}
        handler._send_response = lambda **values: captured.update(values)
        handler.do_GET()
        return captured

    def test_downloads_need_the_admin_token(self) -> None:
        for path in ("/api/trust", "/api/trust/ca.crt", "/api/trust/profile.mobileconfig"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path, token=None)["status"], 401)
                self.assertEqual(self.get(path, token="wrong")["status"], 401)
                self.assertEqual(self.get(path)["status"], 200)

    def test_an_unregistered_test_wifi_device_cannot_download(self) -> None:
        self.server.dashboard_access = lambda peer, operation="status", **values: {
            "ok": True, "network": "test", "allowed": False, "can_register": True,
            "mac_address": None}

        for path in ("/api/trust", "/api/trust/ca.crt", "/api/trust/profile.mobileconfig"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path, peer="192.168.50.150")["status"], 403)

    def test_certificate_download_is_the_public_authority_only(self) -> None:
        response = self.get("/api/trust/ca.crt")

        self.assertEqual(response["content_type"], "application/x-x509-ca-cert")
        self.assertIn('filename="local-dashboard.crt"', response["headers"]["Content-Disposition"])
        self.assertEqual(response["body"], (self.config / "ca" / "ca.crt").read_bytes())
        self.assertNotIn(b"PRIVATE", response["body"])
        self.assertEqual(response["body"].count(b"BEGIN CERTIFICATE"), 1)

    def test_profile_download_installs_the_authority_with_neutral_names(self) -> None:
        response = self.get("/api/trust/profile.mobileconfig")

        self.assertEqual(response["content_type"], "application/x-apple-aspen-config")
        self.assertIn('filename="local-dashboard.mobileconfig"', response["headers"]["Content-Disposition"])
        profile = plistlib.loads(response["body"])
        self.assertEqual(profile["PayloadContent"][0]["PayloadType"], "com.apple.security.root")
        self.assertNotIn(b"jittersim", response["body"].lower())

    def test_trust_details_carry_the_address_fingerprint_and_short_steps(self) -> None:
        details = json.loads(self.get("/api/trust")["body"])

        self.assertTrue(details["available"])
        self.assertEqual(details["address"], "https://netlab.local:%d" % self.server.server_port)
        self.assertRegex(details["fingerprint"], r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")
        self.assertEqual(len(details["steps"]["mac"]), 3)
        self.assertEqual(len(details["steps"]["iphone"]), 3)
        self.assertNotIn("jittersim", json.dumps(details).lower())

    def test_missing_authority_is_reported_not_served(self) -> None:
        (self.config / "ca" / "ca.crt").unlink()

        self.assertFalse(json.loads(self.get("/api/trust")["body"])["available"])
        self.assertEqual(self.get("/api/trust/ca.crt")["status"], 404)
        self.assertEqual(self.get("/api/trust/profile.mobileconfig")["status"], 404)

    def test_the_easy_address_is_in_the_sign_in_page_and_nothing_new_is_public(self) -> None:
        page = self.get("/", token=None)

        self.assertEqual(page["status"], 200)
        self.assertIn("<strong>https://netlab.local:%d</strong>" % self.server.server_port,
                      page["body"].decode())
        self.assertNotIn("@@EASY_ADDRESS@@", page["body"].decode())
        self.assertEqual(PUBLIC_READ_PATHS, frozenset({"/", "/api/health"}))
        self.assertEqual(self.get("/api/trust", token=None)["status"], 401)
        health = json.loads(self.get("/api/health", token=None)["body"])
        self.assertNotIn("easy_address", health)

    def test_the_page_offers_the_downloads_without_naming_the_product(self) -> None:
        self.assertIn('id="trust-pi"', DASHBOARD_HTML)
        self.assertIn('id="easy-address"', DASHBOARD_HTML)
        self.assertIn("/api/trust/profile.mobileconfig", DASHBOARD_HTML)
        start = DASHBOARD_HTML.index('<div id="trust-pi"')
        card = DASHBOARD_HTML[start:DASHBOARD_HTML.index("</section>", start)]
        self.assertNotIn("jittersim", card.lower())
        script = (Path(__file__).resolve().parents[2] / "src/jittersim/api/trust.js").read_text()
        self.assertNotIn("jittersim", script.lower())


@unittest.skipUnless(shutil.which("openssl"), "openssl is required")
class NeutralCertificateTests(unittest.TestCase):
    @needs_openssl
    def test_test_wifi_clients_get_the_certificate_without_other_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            localca.prepare(config, host_name="pi-lab.local", neutral_name="netlab.local",
                            ethernet_ip="192.168.1.20")
            repository = ConfigRepository(":memory:")
            self.addCleanup(repository.close)
            for network, expected in (("127.0.0.0/8", "wifi"), ("192.168.50.0/24", "full")):
                with self.subTest(network=network):
                    server = ControlServer(
                        ("127.0.0.1", 0), repository, admin_token=TOKEN,
                        tls_certfile=config / "dashboard.crt", tls_keyfile=config / "dashboard.key",
                        tls_wifi_certfile=config / "dashboard-wifi.crt", tls_wifi_network=network)
                    self.addCleanup(server.server_close)
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        context = ssl.create_default_context(cafile=str(config / "ca" / "ca.crt"))
                        # Without a name, as when a browser opens the gateway address.
                        context.check_hostname = False
                        with socket.create_connection(("127.0.0.1", server.server_port), timeout=5) as raw:
                            with context.wrap_socket(raw) as stream:
                                der = stream.getpeercert(binary_form=True)
                        pem = ssl.DER_cert_to_PEM_cert(der)
                    finally:
                        server.shutdown()
                        thread.join(timeout=5)
                    wifi = (config / "dashboard-wifi.crt").read_text().strip()
                    full = (config / "dashboard.crt").read_text().strip()
                    self.assertEqual(pem.strip(), wifi if expected == "wifi" else full)

    def test_a_test_wifi_client_gets_the_neutral_certificate_whichever_address_it_reaches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            localca.prepare(config, host_name="pi-lab.local", neutral_name="netlab.local",
                            ethernet_ip="192.168.1.20")
            repository = ConfigRepository(":memory:")
            self.addCleanup(repository.close)
            server = ControlServer(
                ("127.0.0.1", 0), repository, admin_token=TOKEN,
                tls_certfile=config / "dashboard.crt", tls_keyfile=config / "dashboard.key",
                tls_wifi_certfile=config / "dashboard-wifi.crt")
            self.addCleanup(server.server_close)
            full, neutral = server._tls_context, server._tls_wifi_context
            self.assertIsNotNone(neutral)
            cases = (
                # (address the client reached, client address, expected)
                ("192.168.50.1", "192.168.50.7", neutral),
                # The firewall lets the test Wi-Fi reach the dashboard port on the Ethernet address too.
                ("192.168.1.20", "192.168.50.7", neutral),
                ("192.168.1.20", "192.168.1.5", full),
                ("127.0.0.1", "127.0.0.1", full),
            )
            for local, peer, expected in cases:
                with self.subTest(local=local, peer=peer):
                    self.assertIs(server.tls_context_for(local, peer), expected)
            self.assertIs(server.tls_context_for("not-an-address", "192.168.1.5"), full)
            self.assertIs(server.tls_context_for("192.168.1.20", "not-an-address"), full)


if __name__ == "__main__":
    unittest.main()
