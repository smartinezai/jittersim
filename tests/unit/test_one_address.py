"""Requests for the one neutral address are served the same on both networks."""

import io
import json
import shutil
import socket
import ssl
import tempfile
import threading
import unittest
from email.message import Message
from pathlib import Path

from jittersim import localca, mdnsname
from jittersim.api.server import DASHBOARD_HTML, ControlRequestHandler, ControlServer
from jittersim.api.trust import dashboard_name_report, easy_address
from jittersim.config.storage import ConfigRepository

TOKEN = "one-address-token"
ROUTER_PEER = "192.168.0.40"
WIFI_PEER = "192.168.50.150"


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.status = self.root / "status.json"
        self.server = ControlServer(
            ("127.0.0.1", 0), self.repository, admin_token=TOKEN, local_name="netlab.local",
            name_status_path=self.status)
        self.addCleanup(self.server.server_close)
        self.registered = {ROUTER_PEER: True, WIFI_PEER: True}

        def access(peer, operation="status", **values):
            network = "test" if peer.startswith("192.168.50.") else "management"
            allowed = self.registered.get(peer, False)
            return {"ok": True, "network": network, "allowed": allowed,
                    "can_register": not allowed, "mac_address": None}

        self.server.dashboard_access = access

    def request(self, method, path, *, host, peer, token=TOKEN, origin=None, body=None):
        handler = object.__new__(ControlRequestHandler)
        handler.server = self.server
        handler.path = path
        handler.command = method
        handler.client_address = (peer, 45000)
        handler.headers = Message()
        handler.headers["Host"] = host
        if token:
            handler.headers["Authorization"] = "Bearer " + token
        if origin:
            handler.headers["Origin"] = origin
        raw = json.dumps(body if body is not None else {}).encode()
        handler.rfile = io.BytesIO(raw)
        handler.headers["Content-Length"] = str(len(raw))
        captured = {}
        handler._send_response = lambda **values: captured.update(values)
        {"GET": handler.do_GET, "POST": handler.do_POST}[method]()
        return captured

    @property
    def authority(self) -> str:
        return "netlab.local:%d" % self.server.server_port


class HostHeaderTests(Base):
    def test_the_page_opens_the_same_through_the_name_on_both_networks(self) -> None:
        pages = [
            self.request("GET", "/", host=self.authority, peer=peer, token=None)
            for peer in (ROUTER_PEER, WIFI_PEER, "127.0.0.1")
        ]
        for page in pages:
            self.assertEqual(page["status"], 200)
            self.assertNotIn("Location", page.get("headers") or {})
        self.assertEqual({page["body"] for page in pages}, {pages[0]["body"]})
        expected = DASHBOARD_HTML.replace("@@EASY_ADDRESS@@", easy_address(self.server))
        self.assertEqual(pages[0]["body"].decode(), expected)
        self.assertIn("<strong>https://netlab.local:%d</strong>" % self.server.server_port,
                      pages[0]["body"].decode())

    def test_the_host_header_is_never_trusted_or_echoed(self) -> None:
        reference = self.request("GET", "/", host=self.authority, peer=ROUTER_PEER, token=None)
        for host in ("evil.example", "netlab.local", "192.168.0.5:8080", "192.168.50.1:8080",
                     "pi.home.arpa:8080", "", "a" * 500, "netlab.local:8080\r\nX: y"):
            with self.subTest(host=host[:30]):
                page = self.request("GET", "/", host=host, peer=WIFI_PEER, token=None)
                self.assertEqual(page["status"], 200)
                self.assertEqual(page["body"], reference["body"])
                if host in ("evil.example", "192.168.0.5:8080", "a" * 500):
                    self.assertNotIn(host.encode(), page["body"])

    def test_protected_reads_work_through_the_name_for_a_router_device_and_a_registered_wifi_device(self) -> None:
        for peer in (ROUTER_PEER, WIFI_PEER):
            for path in ("/api/trust", "/api/diagnostics", "/api/devices"):
                with self.subTest(peer=peer, path=path):
                    answer = self.request("GET", path, host=self.authority, peer=peer)
                    self.assertEqual(answer["status"], 200)

    def test_the_registered_device_rule_still_applies_on_the_test_wifi(self) -> None:
        self.registered[WIFI_PEER] = False

        for path in ("/api/trust", "/api/diagnostics", "/api/devices", "/api/trust/ca.crt"):
            with self.subTest(path=path):
                answer = self.request("GET", path, host=self.authority, peer=WIFI_PEER)
                self.assertEqual(answer["status"], 403)
        # The sign-in page and the token check stay reachable so the device can register.
        self.assertEqual(self.request("GET", "/", host=self.authority, peer=WIFI_PEER,
                                      token=None)["status"], 200)
        self.assertEqual(self.request("GET", "/api/dashboard-access/status", host=self.authority,
                                      peer=WIFI_PEER)["status"], 200)
        # The token is still required whatever name was used.
        self.assertEqual(self.request("GET", "/api/devices", host=self.authority, peer=ROUTER_PEER,
                                      token="wrong")["status"], 401)

    def test_a_write_through_the_name_passes_the_same_site_check_on_both_networks(self) -> None:
        for peer in (ROUTER_PEER, WIFI_PEER):
            with self.subTest(peer=peer):
                answer = self.request(
                    "POST", "/api/dashboard-access/register-self", host=self.authority, peer=peer,
                    origin="https://" + self.authority, body={"name": "My Mac"})
                self.assertNotEqual(answer["status"], 403, answer)

    def test_a_write_from_another_site_is_still_refused(self) -> None:
        for origin in ("https://evil.example", "https://netlab.local.evil.example:%d" % self.server.server_port,
                       "http://evil.example"):
            with self.subTest(origin=origin):
                answer = self.request(
                    "POST", "/api/dashboard-access/register-self", host=self.authority,
                    peer=WIFI_PEER, origin=origin, body={"name": "My Mac"})
                self.assertEqual(answer["status"], 403)


class ReportTests(Base):
    def test_diagnostics_carry_the_name_the_address_and_the_announcement(self) -> None:
        self.status.write_text(json.dumps({
            "state": "published", "name": "netlab.local", "updated": 4102444800,
            "message": "ok", "fix": None,
            "links": [{"link": "ethernet", "interface": "eth0", "address": "192.168.0.23",
                       "state": "published"},
                      {"link": "test", "interface": "wlan0", "address": "192.168.50.1",
                       "state": "published"}],
            "extra": []}))

        answer = self.request("GET", "/api/diagnostics", host=self.authority, peer=ROUTER_PEER)

        report = json.loads(answer["body"])["dashboard_name"]
        self.assertEqual(report["name"], "netlab.local")
        self.assertEqual(report["address"], "https://%s" % self.authority)
        self.assertEqual(report["state"], "published")
        self.assertEqual([item["address"] for item in report["networks"]],
                         ["192.168.0.23", "192.168.50.1"])

    def test_a_name_conflict_reaches_diagnostics_with_the_fix(self) -> None:
        import time
        self.status.write_text(json.dumps({
            "state": "conflict", "name": "netlab.local", "updated": int(time.time()),
            "message": "Another device on the Ethernet (router network) already uses netlab.local, "
                       "so it is not announced there.",
            "fix": "Choose another name: sudo jittersim dashboard-name set <other>.local",
            "links": [], "extra": []}))

        report = json.loads(self.request(
            "GET", "/api/diagnostics", host=self.authority, peer=ROUTER_PEER)["body"])["dashboard_name"]

        self.assertEqual(report["state"], "conflict")
        self.assertIn("already uses", report["message"])
        self.assertIn("dashboard-name set", report["fix"])

    def test_without_the_publisher_diagnostics_say_so_and_the_dashboard_still_works(self) -> None:
        answer = self.request("GET", "/api/diagnostics", host=self.authority, peer=ROUTER_PEER)

        self.assertEqual(answer["status"], 200)
        report = json.loads(answer["body"])["dashboard_name"]
        self.assertEqual(report["state"], "stopped")
        self.assertIn("IP address", report["message"])

    def test_a_report_for_an_older_name_is_not_shown_for_the_new_one(self) -> None:
        import time
        self.status.write_text(json.dumps({
            "state": "published", "name": "old-name.local", "updated": int(time.time()),
            "message": "ok", "links": [], "extra": []}))

        self.assertEqual(dashboard_name_report(self.server)["state"], "stopped")

    def test_the_trust_details_stay_neutral_and_carry_no_commands(self) -> None:
        details = json.loads(self.request(
            "GET", "/api/trust", host=self.authority, peer=WIFI_PEER)["body"])

        self.assertEqual(details["name"], "netlab.local")
        self.assertEqual(details["address"], "https://%s" % self.authority)
        self.assertNotIn("fix", details["announce"])
        self.assertNotIn("jittersim", json.dumps(details).lower())


class PageTests(unittest.TestCase):
    def test_the_address_is_shown_prominently_after_sign_in_in_plain_words(self) -> None:
        start = DASHBOARD_HTML.index('<div id="one-address"')
        block = DASHBOARD_HTML[start:DASHBOARD_HTML.index("</div>", start)]
        self.assertIn("hidden", block.split(">")[0])
        self.assertIn("Bookmark <strong id=\"one-address-url\">@@EASY_ADDRESS@@</strong>, "
                      "it works on both networks.", block)
        self.assertNotIn("jittersim", block.lower())

    def test_the_sign_in_help_names_the_one_address_without_the_product_name(self) -> None:
        start = DASHBOARD_HTML.index('id="sign-in-address"')
        text = DASHBOARD_HTML[start:DASHBOARD_HTML.index("</p>", start)]
        self.assertIn("@@EASY_ADDRESS@@", text)
        self.assertIn("router Wi-Fi", text)
        self.assertIn("test Wi-Fi", text)
        self.assertNotIn("jittersim", text.lower())

    def test_the_placeholder_is_never_inside_the_inline_script_or_style(self) -> None:
        # The policy hashes the inline blocks, so only plain page text is filled in.
        import re
        for tag in ("script", "style"):
            for content in re.findall(r"<%s>(.*?)</%s>" % (tag, tag), DASHBOARD_HTML, re.S):
                self.assertNotIn("@@EASY_ADDRESS@@", content)

    def test_the_nudge_opens_the_one_address_and_says_to_sign_in_once(self) -> None:
        start = DASHBOARD_HTML.index('id="one-address-nudge"')
        text = " ".join(DASHBOARD_HTML[start:DASHBOARD_HTML.index("</p>", start)].split())
        self.assertIn("hidden", DASHBOARD_HTML[start - 40:start + 60])
        self.assertIn("enter the admin token once there", text)
        self.assertIn('href="@@EASY_ADDRESS@@/"', text)
        script = (Path(__file__).resolve().parents[2] / "src/jittersim/api/trust.js").read_text()
        # Shown only when the page was opened at some other address, and never with the token in a link.
        self.assertIn("window.location.hostname", script)
        self.assertIn("nudge.hidden = current === target.hostname.toLowerCase()", script)
        # The executable link tests verify that credentials and query tokens
        # are excluded. Adjacent credential-fence functions belong here too.
        self.assertNotIn("jittersim", script.lower())


@unittest.skipUnless(shutil.which("openssl"), "openssl is required")
class TlsThroughTheNameTests(unittest.TestCase):
    """A real HTTPS request to the one name works on both networks' certificates."""

    def serve(self, wifi_network: str):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = Path(directory.name)
        localca.prepare(config, host_name="pi-lab.local", neutral_name="netlab.local",
                        ethernet_ip="192.168.1.20")
        repository = ConfigRepository(":memory:")
        self.addCleanup(repository.close)
        server = ControlServer(
            ("127.0.0.1", 0), repository, admin_token=TOKEN, local_name="netlab.local",
            tls_certfile=config / "dashboard.crt", tls_keyfile=config / "dashboard.key",
            tls_wifi_certfile=config / "dashboard-wifi.crt", tls_wifi_network=wifi_network,
            name_status_path=config / "none.json")
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            thread.join(timeout=5)

        self.addCleanup(stop)
        return server, config

    def fetch(self, server, config):
        context = ssl.create_default_context(cafile=str(config / "ca" / "ca.crt"))
        with socket.create_connection(("127.0.0.1", server.server_port), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname="netlab.local") as stream:
                stream.sendall(("GET / HTTP/1.1\r\nHost: netlab.local:%d\r\nConnection: close\r\n\r\n"
                                % server.server_port).encode())
                data = b""
                while True:
                    chunk = stream.recv(65536)
                    if not chunk:
                        break
                    data += chunk
        return data

    def test_the_router_network_and_the_test_wifi_both_verify_the_one_name(self) -> None:
        for label, network in (("test Wi-Fi", "127.0.0.0/8"), ("router network", "192.168.50.0/24")):
            with self.subTest(network=label):
                server, config = self.serve(network)
                data = self.fetch(server, config)
                self.assertTrue(data.startswith(b"HTTP/1.0 200"), data[:60])
                self.assertIn(b"https://netlab.local:%d" % server.server_port, data)


if __name__ == "__main__":
    unittest.main()
