"""Every write route of the merged dashboard needs the admin token.

Main's workflow and access routes joined the Pi line's token check, so a
request without a bearer token is refused before the route is looked at.
"""

import tempfile
import unittest
from email.message import Message
from io import BytesIO
from pathlib import Path

from jittersim.api.access import ACCESS_WRITES
from jittersim.api.server import (
    PUBLIC_READ_PATHS,
    WRITE_PATHS,
    ControlRequestHandler,
    ControlServer,
)
from jittersim.api.workflows import WORKFLOW_WRITES
from jittersim.config.storage import ConfigRepository

READ_PATHS = (
    "/api/profiles", "/api/devices", "/api/plan", "/api/state", "/api/everyone-else",
    "/api/scenarios", "/api/activity", "/api/conditions", "/api/gateway-persistence",
    "/api/access", "/api/history", "/api/test-sessions", "/api/updates",
    "/api/update-preferences", "/api/export", "/api/backup", "/api/general-profile",
    "/api/service-lists", "/api/unaffected-devices", "/api/traffic", "/api/observations",
    "/api/system-health",
)


class MergedWritePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repository = ConfigRepository(Path(self.directory.name) / "settings.db")
        self.addCleanup(self.repository.close)
        self.server = ControlServer(("127.0.0.1", 0), self.repository, admin_token="admin-token")
        self.addCleanup(self.server.server_close)

    def send(self, method, path, authorization=None):
        handler = object.__new__(ControlRequestHandler)
        handler.server = self.server
        handler.path = path
        handler.client_address = ("127.0.0.1", 45000)
        handler.headers = Message()
        handler.headers["Host"] = "127.0.0.1:8080"
        if authorization:
            handler.headers["Authorization"] = authorization
        if method != "GET":
            handler.headers["Content-Type"] = "application/json"
            handler.headers["Content-Length"] = "2"
            handler.rfile = BytesIO(b"{}")
        response = {}
        handler._send_response = lambda **values: response.update(values)
        if method == "GET":
            handler.do_GET()
        else:
            handler._handle_write()
        return response

    def test_main_routes_are_part_of_the_write_paths(self) -> None:
        self.assertTrue(WORKFLOW_WRITES <= WRITE_PATHS)
        self.assertTrue(ACCESS_WRITES <= WRITE_PATHS)
        for path in ("/api/everyone-else", "/api/scenarios", "/api/activity/clear",
                     "/api/normal-access", "/api/updates/install", "/api/gateway-persistence"):
            self.assertIn(path, WRITE_PATHS, path)

    def test_every_write_path_needs_the_token(self) -> None:
        for path in sorted(WRITE_PATHS):
            with self.subTest(path=path):
                self.assertEqual(self.send("POST", path)["status"], 401)
                self.assertEqual(self.send("POST", path, "Bearer wrong")["status"], 401)

    def test_private_reads_need_the_token_and_health_does_not(self) -> None:
        for path in READ_PATHS:
            with self.subTest(path=path):
                self.assertNotIn(path, PUBLIC_READ_PATHS)
                self.assertEqual(self.send("GET", path)["status"], 401)
        self.assertEqual(self.send("GET", "/api/health")["status"], 200)
        # The page itself and the mode check are the only public reads.
        self.assertEqual(PUBLIC_READ_PATHS, frozenset({"/", "/api/health"}))

    def test_every_response_is_not_cached(self) -> None:
        for method, path in (("GET", "/api/health"), ("POST", "/api/router/wifi-credentials")):
            captured = []
            handler = object.__new__(ControlRequestHandler)
            handler.server = self.server
            handler.path = path
            handler.client_address = ("127.0.0.1", 45000)
            handler.headers = Message()
            handler.headers["Host"] = "127.0.0.1:8080"
            handler.requestline = f"{method} {path} HTTP/1.1"
            handler.request_version = "HTTP/1.1"
            handler.command = method
            handler.wfile = BytesIO()
            handler.send_response = lambda status, message=None: captured.append(("status", status))
            handler.send_header = lambda name, value: captured.append((name, value))
            handler.end_headers = lambda: None
            if method == "GET":
                handler.do_GET()
            else:
                handler.rfile = BytesIO(b"{}")
                handler.headers["Content-Type"] = "application/json"
                handler.headers["Content-Length"] = "2"
                handler._handle_write()
            self.assertIn(("Cache-Control", "no-store"), captured, path)
            self.assertIn(("X-Frame-Options", "DENY"), captured, path)


if __name__ == "__main__":
    unittest.main()
