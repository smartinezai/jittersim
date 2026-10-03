"""Bounded sessions and HTTPS transport for explicit homepage test actions."""

import hashlib
import json
import logging
import re
import secrets
import socket
import ssl
import threading
import time
from concurrent.futures import Future, TimeoutError
from http.server import BaseHTTPRequestHandler, HTTPServer
from ipaddress import ip_address
from queue import Empty, Queue
from socketserver import ThreadingMixIn
from urllib.parse import urlsplit

LOGGER = logging.getLogger(__name__)
EXTENSION_ID = re.compile(r"[a-p]{32}\Z")
TOKEN = re.compile(r"[a-f0-9]{64}\Z")
CODE = re.compile(r"[a-f0-9]{32}\Z")


def controlled_page(value):
    """Enroll a named HTTPS page without accepting private or ambiguous names."""
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError("Use a controlled HTTPS test page")
    url = urlsplit(value)
    host = url.hostname or ""
    if (
        url.scheme != "https"
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.port not in (None, 443)
        or url.path in ("", "/")
        or url.path.startswith("//")
        or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host)
        or host.endswith((".local", ".localhost", ".home.arpa", ".internal", ".test", ".invalid"))
        or not re.fullmatch(r"/[A-Za-z0-9/_-]{1,255}", url.path)
    ):
        raise ValueError("Use a named controlled HTTPS test page with a simple path")
    return {"origin": "https://" + host, "path": url.path}


class PublicResolver:
    """Keep two physical DNS lookups admitted even after a caller times out."""

    def __init__(self):
        self.slots = threading.BoundedSemaphore(2)
        self.jobs = Queue(maxsize=2)
        self.lock = threading.Lock()
        self.closed = False
        try:
            for index in range(2):
                threading.Thread(
                    target=self._work, daemon=True, name=f"homepage-dns-{index}"
                ).start()
        except RuntimeError:
            self.close()
            raise

    def _work(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            future, host = job
            try:
                future.set_result(self.lookup(host))
            except BaseException as error:
                future.set_exception(error)
            finally:
                self.slots.release()

    @staticmethod
    def lookup(host):
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not answers or len(answers) > 16:
            raise ValueError("destination unavailable")
        values = tuple(sorted({answer[4][0] for answer in answers}))
        if any(not ip_address(value).is_global for value in values):
            raise ValueError("destination must resolve only to public addresses")
        return values

    def resolve(self, host):
        with self.lock:
            if self.closed or not self.slots.acquire(blocking=False):
                raise ValueError("destination verification busy")
            future = Future()
            self.jobs.put_nowait((future, host))
        try:
            return future.result(timeout=0.12)
        except (OSError, TimeoutError) as error:
            raise ValueError("destination verification unavailable") from error

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            while True:
                try:
                    future, _ = self.jobs.get_nowait()
                    future.set_exception(ValueError("resolver closed"))
                    self.slots.release()
                except Empty:
                    break
            self.jobs.put_nowait(None)
            self.jobs.put_nowait(None)


def digest(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


class PairingStore:
    """Keep codes, sessions and rate limits in finite process-local memory."""

    def __init__(self, helper, *, resolver=None, clock=time.monotonic):
        self.helper = helper
        self.resolver = resolver or PublicResolver()
        self.clock = clock
        self.lock = threading.Lock()
        self.codes = {}
        self.sessions = {}
        self.revision = 0
        self.stopped = False

    def expire(self):
        now = self.clock()
        self.codes = {key: value for key, value in self.codes.items() if value["expires"] > now}
        self.sessions = {
            key: value for key, value in self.sessions.items() if value["expires"] > now
        }

    def create(self, extension_id, pages):
        if not isinstance(extension_id, str) or not EXTENSION_ID.fullmatch(extension_id):
            raise ValueError("Use the extension ID shown by Chrome")
        if not isinstance(pages, list) or not 1 <= len(pages) <= 8:
            raise ValueError("Enroll one to eight controlled test pages")
        parsed = [controlled_page(value) for value in pages]
        if len({(page["origin"], page["path"]) for page in parsed}) != len(parsed):
            raise ValueError("Use distinct test pages")
        for page in parsed:
            self.resolver.resolve(urlsplit(page["origin"]).hostname)
        with self.lock:
            self.expire()
            if self.stopped or len(self.codes) >= 8:
                raise ValueError("Pairing is unavailable or full")
            code = secrets.token_hex(16)
            self.codes[digest(code)] = {
                "extension": extension_id,
                "pages": parsed,
                "expires": self.clock() + 120,
            }
            return {"code": code, "expires_in": 120}

    def revoke(self):
        with self.lock:
            self.codes.clear()
            self.sessions.clear()
            self.revision += 1

    def close(self):
        with self.lock:
            self.stopped = True
            self.codes.clear()
            self.sessions.clear()
        self.resolver.close()

    @staticmethod
    def extension(origin):
        if not isinstance(origin, str) or not origin.startswith("chrome-extension://"):
            raise PermissionError("Pairing required")
        identity = origin[len("chrome-extension://") :]
        if not EXTENSION_ID.fullmatch(identity):
            raise PermissionError("Pairing required")
        return identity

    def pair(self, code, origin, peer):
        identity = self.extension(origin)
        if not isinstance(code, str) or not CODE.fullmatch(code):
            raise PermissionError("Pairing required")
        with self.lock:
            self.expire()
            entry = self.codes.get(digest(code))
            if self.stopped or entry is None or entry["extension"] != identity:
                raise PermissionError("Pairing required")
        snapshot = self.helper.homepage_snapshot(peer=peer)
        with self.lock:
            self.expire()
            if (
                self.stopped
                or self.codes.get(digest(code)) is not entry
                or snapshot.get("verified") is not True
                or len(self.sessions)
                - sum(
                    (value["mac"], value["extension"]) == (snapshot.get("mac"), identity)
                    for value in self.sessions.values()
                )
                >= 32
            ):
                raise PermissionError("Pairing unavailable")
            del self.codes[digest(code)]
            # Re-pairing this verified device replaces this extension's old sessions.
            self.sessions = {
                key: value
                for key, value in self.sessions.items()
                if (value["mac"], value["extension"]) != (snapshot["mac"], identity)
            }
            token = secrets.token_hex(32)
            self.revision += 1
            self.sessions[digest(token)] = {
                "extension": identity,
                "mac": snapshot["mac"],
                "epoch": snapshot["epoch"],
                "pages": entry["pages"],
                "expires": self.clock() + 3600,
                "tokens": 4.0,
                "last": self.clock(),
                "revision": self.revision,
            }
            return {
                "kind": "homepage-pair",
                "capability": token,
                "revision": self.revision,
                "pages": entry["pages"],
            }

    def policy(self, token, origin, peer, target, request_id):
        identity = self.extension(origin)
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise PermissionError("Pairing required")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise ValueError("Invalid request")
        with self.lock:
            self.expire()
            session = self.sessions.get(digest(token))
            if self.stopped or session is None or session["extension"] != identity:
                raise PermissionError("Pairing required")
            if target not in {page["origin"] for page in session["pages"]}:
                raise PermissionError("Test page not enrolled")
            now = self.clock()
            session["tokens"] = min(4, session["tokens"] + (now - session["last"]) * 2)
            session["last"] = now
            allowed = session["tokens"] >= 1
            if allowed:
                session["tokens"] -= 1
        response = {
            "kind": "homepage-policy",
            "enabled": False,
            "chance": 0,
            "revision": session["revision"],
            "lease_ms": 1000,
            "origin": target,
            "request_id": request_id,
        }
        if not allowed:
            return response
        try:
            addresses = self.resolver.resolve(urlsplit(target).hostname)
            snapshot = self.helper.homepage_snapshot(
                peer=peer,
                host=urlsplit(target).hostname,
                addresses=addresses,
                expected=session["epoch"],
            )
        except (ValueError, RuntimeError):
            return response
        with self.lock:
            self.expire()
            if self.stopped or self.sessions.get(digest(token)) is not session:
                raise PermissionError("Pairing required")
            if (
                snapshot.get("verified") is not True
                or snapshot.get("epoch") != session["epoch"]
                or snapshot.get("mac") != session["mac"]
            ):
                del self.sessions[digest(token)]
                raise PermissionError("Pair again on the test Wi-Fi")
            chance = snapshot.get("chance")
            if type(chance) is int and 0 <= chance <= 100 and snapshot.get("protected") is False:
                try:
                    if self.helper.homepage_confirm(snapshot["receipt"]):
                        response.update(enabled=True, chance=chance)
                except (KeyError, RuntimeError):
                    pass
            return response


class PolicyHandler(BaseHTTPRequestHandler):
    """Accept only bounded one-shot JSON requests from an enrolled extension."""

    server_version = "server"
    sys_version = ""
    rbufsize = 0

    def log_message(self, *_):
        # Credentials and destinations never enter access logs.
        pass

    def do_OPTIONS(self):
        origin = self.headers.get("Origin")
        try:
            self.server.store.extension(origin)
            if len(self.headers.get_all("Origin", [])) != 1:
                raise PermissionError("Pairing required")
            if (
                self.path not in ("/homepage/pair", "/homepage/policy")
                or self.headers.get("Access-Control-Request-Method") != "POST"
                or set(
                    value.strip().lower()
                    for value in self.headers.get("Access-Control-Request-Headers", "").split(",")
                )
                - {"authorization", "content-type"}
            ):
                raise ValueError("request unavailable")
        except (ValueError, PermissionError):
            self.respond(403, {"error": "Request unavailable"})
            return
        self.respond(200, {}, origin)

    def do_POST(self):
        origin = self.headers.get("Origin")
        try:
            self.server.store.extension(origin)
            if len(self.headers.get_all("Origin", [])) != 1:
                raise PermissionError("Pairing required")
            if (
                self.headers.get("Host") != self.server.authority
                or len(self.headers.get_all("Host", [])) != 1
                or self.headers.get_all("Content-Length") is None
                or len(self.headers.get_all("Content-Length")) != 1
                or self.headers.get("Transfer-Encoding") is not None
                or self.headers.get("Content-Type") != "application/json"
            ):
                raise ValueError("invalid request")
            size = int(self.headers["Content-Length"])
            if not 0 < size <= 8192:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("invalid request")
            if self.path == "/homepage/pair" and set(payload) == {"code"}:
                result = self.server.store.pair(payload["code"], origin, self.client_address[0])
            elif self.path == "/homepage/policy" and set(payload) == {"origin", "request_id"}:
                authorization = self.headers.get("Authorization", "")
                if not authorization.startswith("Homepage "):
                    raise PermissionError("Pairing required")
                result = self.server.store.policy(
                    authorization[9:],
                    origin,
                    self.client_address[0],
                    payload["origin"],
                    payload["request_id"],
                )
            else:
                raise ValueError("request unavailable")
            self.respond(200, result, origin)
        except PermissionError:
            self.respond(403, {"error": "Pairing required"}, origin)
        except (ValueError, OSError, RuntimeError):
            self.respond(400, {"error": "Policy unavailable"}, origin)

    def respond(self, status, payload, origin=None):
        if time.monotonic() >= getattr(self, "deadline", float("inf")):
            payload = {"error": "Policy unavailable"}
            status = 503
        self.connection.settimeout(0.1)
        body = json.dumps(payload).encode()
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "POST")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()
        self.wfile.write(body)


class PolicyServer(ThreadingMixIn, HTTPServer):
    """Four physical workers include TLS handshakes with no application queue."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 4

    def __init__(self, address, store, context, authority="netlab.local:8443"):
        if not isinstance(context, ssl.SSLContext):
            raise ValueError("TLS required for homepage pairing")
        self.store = store
        self.context = context
        self.authority = authority
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(address, PolicyHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        from jittersim.networking.worker_slots import start_admitted_worker

        start_admitted_worker(
            self.slots, lambda: self.process_request_thread(request, client_address)
        )

    def process_request_thread(self, request, client_address):
        wrapped = request
        deadline = time.monotonic() + 0.75
        try:
            request.settimeout(0.75)
            wrapped = self.context.wrap_socket(
                request, server_side=True, do_handshake_on_connect=False
            )
            wrapped.do_handshake()
            # Reads use an absolute deadline including handshake and slow input.
            import io

            from .server import _DeadlineReader

            class Handler(PolicyHandler):
                def setup(inner):
                    super().setup()
                    inner.deadline = deadline

                    class LimitedReader(_DeadlineReader):
                        remaining = 16384

                        def readinto(reader, buffer):
                            if reader.remaining <= 0:
                                raise OSError("request is too large")
                            count = super().readinto(memoryview(buffer)[: reader.remaining])
                            reader.remaining -= count or 0
                            return count

                    inner.rfile = io.BufferedReader(
                        LimitedReader(inner.rfile, inner.connection, deadline)
                    )

            self.finish_request_with_handler(wrapped, client_address, Handler)
        except (OSError, ValueError):
            pass
        finally:
            self.shutdown_request(wrapped)

    def finish_request_with_handler(self, request, address, handler):
        handler(request, address, self)
