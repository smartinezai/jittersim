"""Characterisation test of the dashboard's HTTP routes.

It sends a fixed corpus of requests (every read path, every write path, unknown
paths, other methods, with no token, a wrong token and the admin token) to a real
ControlServer and compares the answers with tests/unit/route_characterisation.json:
the status, the content type, the security relevant headers, the top-level JSON
keys and every error message. It was recorded before the read router became a
route table, so a refactor of the routing must keep it green.

A new route does not need a new entry. To record the corpus again after a wanted
change, run:

    JITTERSIM_RECORD_ROUTES=1 python -m pytest tests/unit/test_route_characterisation.py
"""

import http.client
import json
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from jittersim.api import server as server_module
from jittersim.api.server import DASHBOARD_CONTENT_SECURITY_POLICY, WRITE_PATHS, ControlServer
from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.storage import ConfigRepository
from tests.unit.test_api import FakeHelper

SNAPSHOT = Path(__file__).with_name("route_characterisation.json")
TOKEN = "route-characterisation-token"
AUTH_HEADERS = {
    "none": {},
    "wrong": {"Authorization": "Bearer not-the-token"},
    "admin": {"Authorization": "Bearer " + TOKEN},
}
KEPT_HEADERS = ("Allow", "WWW-Authenticate", "Retry-After", "Content-Disposition",
                "Cache-Control", "Connection")
# Sent with every answer. The snapshot does not record them, so a test below pins them.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

READ_PATHS = (
    "/", "/favicon.ico", "/api/brand", "/api/health", "/api/system-health",
    "/api/wifi-operation/request-id", "/api/wifi-operation", "/api/wifi-operation?id=unknown",
    "/api/profiles", "/api/profiles?ignored=1", "/api/pattern-history", "/api/general-profile",
    "/api/devices", "/api/ad-blocking", "/api/unaffected-devices", "/api/groups", "/api/schedules",
    "/api/activity", "/api/activity/latest", "/api/scenarios", "/api/timers", "/api/progressions",
    "/api/data-limits", "/api/group-data-limits", "/api/service-quotas",
    "/api/service-assignments", "/api/service-lists", "/api/time-quotas", "/api/policies",
    "/api/everyone-else", "/api/router", "/api/uplink", "/api/connected-device-labels",
    "/api/connected-devices", "/api/observations", "/api/plan", "/api/conditions", "/api/state",
    "/api/action-history",
    "/api/random-website-analytics", "/api/random-website-analytics?days=7",
    "/api/random-website-analytics?days=0", "/api/random-website-analytics?days=31",
    "/api/random-website-analytics?days=abc", "/api/random-website-analytics?days=1&days=2",
    "/api/random-website-analytics?device_id=" + "x" * 65,
    "/api/random-website-analytics?device_id=alice",
    "/api/random-website-analytics?a=1&b=2&c=3&d=4&e=5",
    "/api/backup", "/api/export", "/api/traffic-settings", "/api/gateway-persistence",
    "/api/traffic", "/api/impact", "/api/diagnostics",
    # Routes answered before the main read router.
    "/api/access", "/api/trust", "/api/trust/ca.crt", "/api/trust/profile.mobileconfig",
    "/api/update-preferences", "/api/device-workspace/alice", "/api/device-workspace/nobody",
    "/api/updates", "/api/updates/check-online", "/api/updates/check-online/not-a-job!",
    "/api/updates/online-settings", "/api/history", "/api/test-sessions",
    "/api/test-sessions/unknown",
    "/api/dashboard-access", "/api/dashboard-access/status", "/api/dashboard-access/moves",
    # Added on main after the first recording. Recorded from main's if-chain code.
    "/api/speed-tests", "/api/speed-tests?mac=bad", "/api/speed-tests/status",
    "/api/speed-tests/status?run=unknown", "/api/speed-tests/compare",
    "/api/speed-tests/compare?baseline=1&result=2", "/api/speed-tests/export",
    "/api/speed-tests/export?format=csv", "/api/speed-tests/export?format=xml",
    "/api/speed-tests?a=1&b=2&c=3&d=4&e=5&f=6&g=7&h=8&i=9",
    # Paths that do not exist.
    "/api", "/api/", "/api/nope", "/api/profiles/", "/API/health", "/nope", "/index.html",
    "/api/apply", "/api/reset",
)
OTHER_METHODS = ("HEAD", "DELETE", "OPTIONS", "PATCH")
OTHER_METHOD_PATHS = ("/", "/api/health", "/api/profiles", "/api/apply", "/api/nope")


def _corpus():
    """Every request of the corpus as (mode, method, path, auth, extra headers, body)."""
    requests = []
    for mode in ("configuration-only", "helper"):
        for path in READ_PATHS:
            for auth in AUTH_HEADERS:
                requests.append((mode, "GET", path, auth, {}, None))
        for method in OTHER_METHODS:
            for path in OTHER_METHOD_PATHS:
                requests.append((mode, method, path, "admin", {}, None))
        for path in sorted(WRITE_PATHS) + ["/api/nope", "/api/profiles/"]:
            requests.append((mode, "POST", path, "none", {}, None))
            # A body that is not JSON stops every write before its handler runs, so
            # this pins the checks in front of the routes and changes nothing.
            requests.append((mode, "POST", path, "admin", {}, b"not json"))
            requests.append((mode, "POST", path, "admin",
                             {"Origin": "https://other.example"}, b"not json"))
            if mode == "helper":
                requests.append((mode, "PUT", path, "admin", {}, b"not json"))
        for path in ("/api/apply", "/api/nope"):
            requests.append((mode, "POST", path, "wrong", {}, None))
    for mode in ("no-token",):
        for method, path in (("GET", "/"), ("GET", "/api/health"), ("GET", "/api/profiles"),
                             ("GET", "/api/nope"), ("POST", "/api/apply"), ("POST", "/api/nope")):
            requests.append((mode, method, path, "admin", {}, b"{}" if method == "POST" else None))
    return requests


def _key(request):
    mode, method, path, auth, extra, body = request
    origin = " cross-site" if extra else ""
    return f"{mode} {method} {path} auth={auth}{origin}" + (" body" if body is not None else "")


def _summary(response, body: bytes):
    result = {"status": response.status, "content_type": response.getheader("Content-Type")}
    headers = {name: response.getheader(name) for name in KEPT_HEADERS if response.getheader(name)}
    if headers:
        result["headers"] = headers
    if body and (result["content_type"] or "").startswith("application/json"):
        data = json.loads(body)
        if isinstance(data, dict):
            result["keys"] = sorted(data)
            if "error" in data:
                result["error"] = data["error"]
        else:
            result["json"] = type(data).__name__
    return result


class RouteCharacterisationTests(unittest.TestCase):
    maxDiff = None

    def _serve(self, mode):
        repository = ConfigRepository(":memory:")
        repository.save_profile(Profile(id="slow", name="Slow", upload=Impairment(delay_ms=3_000)))
        repository.save_device(Device(id="alice", name="Alice's laptop",
                                      ipv4_address="192.168.50.10", profile_id="slow"))
        server = ControlServer(("127.0.0.1", 0), repository)
        server.admin_token = None if mode == "no-token" else TOKEN
        if mode == "helper":
            server.helper_client = FakeHelper()
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()

        def close():
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
            repository.close()

        self.addCleanup(close)
        return server

    def _observe(self):
        servers = {}
        observed = {}
        for request in _corpus():
            mode, method, path, auth, extra, body = request
            if mode not in servers:
                servers[mode] = self._serve(mode)
            connection = http.client.HTTPConnection(*servers[mode].server_address, timeout=10)
            headers = {"Host": "127.0.0.1", **AUTH_HEADERS[auth], **extra}
            if body is not None:
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            observed[_key(request)] = _summary(response, response.read())
            connection.close()
        return observed

    def test_every_route_answers_as_recorded(self):
        observed = self._observe()
        if os.environ.get("JITTERSIM_RECORD_ROUTES") == "1":
            # One request per line keeps the file short and its diffs readable.
            SNAPSHOT.write_text("{\n" + ",\n".join(
                json.dumps(key) + ": " + json.dumps(observed[key], sort_keys=True)
                for key in sorted(observed)) + "\n}\n")
            self.skipTest("route answers recorded")
        recorded = json.loads(SNAPSHOT.read_text())
        # Routes added later answer with their own tests: only the recorded corpus is compared.
        compared = {key: observed.get(key) for key in recorded}
        self.assertEqual(compared, recorded)

    def _answer(self, server, method, path, auth="none"):
        connection = http.client.HTTPConnection(*server.server_address, timeout=10)
        connection.request(method, path, headers={"Host": "127.0.0.1", **AUTH_HEADERS[auth]})
        response = connection.getresponse()
        response.read()
        connection.close()
        return response

    def test_every_answer_has_the_security_headers_and_only_the_page_has_the_csp(self):
        server = self._serve("helper")
        for method, path, auth, content_type in (
            ("GET", "/", "none", "text/html"),
            ("GET", "/api/health", "none", "application/json"),
            ("GET", "/api/profiles", "admin", "application/json"),
            ("GET", "/api/profiles", "none", "application/json"),
            ("GET", "/api/nope", "admin", "application/json"),
            ("POST", "/api/apply", "none", "application/json"),
        ):
            with self.subTest(method=method, path=path, auth=auth):
                response = self._answer(server, method, path, auth)
                self.assertTrue(response.getheader("Content-Type").startswith(content_type))
                for name, value in SECURITY_HEADERS.items():
                    self.assertEqual(response.getheader(name), value, name)
                policy = response.getheader("Content-Security-Policy")
                if content_type != "text/html":
                    self.assertIsNone(policy)
                    continue
                self.assertEqual(policy, DASHBOARD_CONTENT_SECURITY_POLICY)
                directives = dict(part.split(" ", 1) for part in policy.split("; "))
                others = {name: value for name, value in directives.items()
                          if name not in ("script-src", "style-src")}
                self.assertEqual(
                    others,
                    {"default-src": "'none'", "connect-src": "'self'", "img-src": "'self' data:",
                     "base-uri": "'none'", "form-action": "'self'", "frame-ancestors": "'none'"})
                # Inline code runs only by its hash: no 'unsafe-inline', no nonce, no host.
                for name in ("script-src", "style-src"):
                    sources = directives[name].split(" ")
                    self.assertTrue(sources and all(
                        source.startswith("'sha256-") for source in sources), name)

    def test_health_pings_the_helper_fresh_with_the_token_and_reuses_its_answer_without(self):
        server = self._serve("helper")
        pings = []

        class CountingHelper(FakeHelper):
            def ping(self):
                pings.append(1)
                return super().ping()

        server.helper_client = CountingHelper()
        with patch.object(server_module, "PUBLIC_HEALTH_REUSE_SECONDS", 3600.0):
            for auth in ("none", "wrong", "none"):
                self.assertEqual(self._answer(server, "GET", "/api/health", auth).status, 200)
            # Without the token one ping serves every caller for the reuse window.
            self.assertEqual(len(pings), 1)
            for _ in range(2):
                self.assertEqual(self._answer(server, "GET", "/api/health", "admin").status, 200)
            # With the token every request asks the helper itself.
            self.assertEqual(len(pings), 3)

    def test_no_recorded_write_route_disappeared(self):
        recorded = json.loads(SNAPSHOT.read_text())
        paths = {key.split()[2] for key in recorded
                 if " POST " in key and key.startswith("helper ")}
        self.assertEqual(paths - set(WRITE_PATHS) - {"/api/nope", "/api/profiles/"}, set())


if __name__ == "__main__":
    unittest.main()
