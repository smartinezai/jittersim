"""Disposable loopback fixture with explicitly simulated Wi-Fi identity."""

import hmac
import json
import secrets
import socket
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Fixture:
    """Keep test-only pairing and policy state in bounded memory."""

    def __init__(self):
        self.lock = threading.Lock()
        self.harness_key = secrets.token_hex(32)
        self.code = secrets.token_hex(16)
        self.code_deadline = time.monotonic() + 120
        self.capability = None
        self.session_deadline = 0
        self.revision = 1
        self.config = self.defaults()
        self.counts = Counter()
        self.server = BoundedServer(("127.0.0.1", 0), Handler)
        self.server.fixture = self
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @staticmethod
    def defaults():
        return {"identity": "verified", "protected": False, "service": None,
                "general": 100, "device": 0, "mode": "normal", "delay": 0}

    def start(self):
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise RuntimeError("Reference fixture did not stop")

    def control(self, payload):
        with self.lock:
            if payload.get("read_counts") is True:
                return {"counts": dict(self.counts)}
            allowed = set(self.defaults())
            if set(payload) - allowed:
                raise ValueError("Unknown fixture setting")
            updated = self.defaults() | payload
            for name in ("service", "general", "device"):
                chance = updated[name]
                if chance is not None and (type(chance) is not int or not 0 <= chance <= 100):
                    raise ValueError("Invalid chance")
            if updated["identity"] not in ("verified", "unknown"):
                raise ValueError("Invalid simulated identity")
            if type(updated["protected"]) is not bool:
                raise ValueError("Invalid protection")
            if updated["mode"] not in (
                "normal", "unavailable", "stale", "malformed", "wrong-origin", "oversized"
            ) or not isinstance(updated["delay"], (int, float)) or not 0 <= updated["delay"] <= 2:
                raise ValueError("Invalid fixture failure")
            self.config = updated
            self.revision += 1
            return {"ok": True}

    def pair(self, payload):
        with self.lock:
            if (set(payload) != {"code"} or not isinstance(payload["code"], str)
                    or not self.code or time.monotonic() >= self.code_deadline
                    or not hmac.compare_digest(payload["code"], self.code)):
                raise ValueError("Pairing refused")
            self.code = None
            self.capability = secrets.token_hex(32)
            self.session_deadline = time.monotonic() + 3600
            return {"kind": "reference-pair", "reference_only": True,
                    "capability": self.capability, "revision": self.revision}

    def policy(self, payload, authorization):
        with self.lock:
            if (not self.capability or time.monotonic() >= self.session_deadline
                    or not hmac.compare_digest(authorization, "Reference " + self.capability)
                    or set(payload) != {"origin", "request_id"}
                    or payload["origin"] != self.origin
                    or not isinstance(payload["request_id"], str)
                    or len(payload["request_id"]) > 40):
                raise ValueError("Policy refused")
            config = self.config.copy()
            revision = self.revision
            self.counts["policy"] += 1
        time.sleep(config["delay"])
        identity = "protected" if config["protected"] else config["identity"]
        chance = next((config[key] for key in ("service", "general", "device")
                       if config[key] is not None), 0)
        result = {"kind": "homepage-reference", "reference_only": True,
                  "simulated_identity": identity, "enabled": identity == "verified",
                  "chance": chance, "revision": revision, "lease_ms": 1000,
                  "origin": self.origin, "request_id": payload["request_id"]}
        if config["mode"] == "stale":
            result["lease_ms"] = 0
        elif config["mode"] == "malformed":
            result["chance"] = 101
        elif config["mode"] == "wrong-origin":
            result["origin"] = "http://192.168.50.1:8080"
        elif config["mode"] == "oversized":
            result["extra"] = "x" * 9000
        return (503 if config["mode"] == "unavailable" else 200), result


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, *args):
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(*args)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(2)

    def log_message(self, *_args):
        pass

    def respond(self, status, payload, content_type="application/json"):
        body = (json.dumps(payload).encode()
                if content_type == "application/json" else payload.encode())
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass

    def do_GET(self):
        titles = {"/": "Reference home", "/pages/article": "Reference article",
                  "/pages/notes": "Reference notes"}
        if self.path not in titles:
            self.respond(404, {"error": "No reference page"})
            return
        with self.server.fixture.lock:
            self.server.fixture.counts[self.path] += 1
        self.respond(200, "<!doctype html><html lang='en'><meta charset='utf-8'>"
                     f"<title>{titles[self.path]}</title><h1>{titles[self.path]}</h1>"
                     "<p>Harmless local HTML fixture. No login or download.</p></html>",
                     "text/html; charset=utf-8")

    def do_POST(self):
        fixture = self.server.fixture
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 < length <= 8192:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Invalid request")
            if self.path == "/__reference/control":
                expected = "Harness " + fixture.harness_key
                if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                    raise ValueError("Harness authorization required")
                self.respond(200, fixture.control(payload))
            elif self.path == "/reference/pair":
                self.respond(200, fixture.pair(payload))
            elif self.path == "/reference/policy":
                status, result = fixture.policy(payload, self.headers.get("Authorization", ""))
                self.respond(status, result)
            else:
                self.respond(404, {"error": "No reference route"})
        except (ValueError, UnicodeError, socket.timeout):
            self.respond(400, {"error": "Reference request refused"})
