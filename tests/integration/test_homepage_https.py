import http.client
import json
import socket
import ssl
import subprocess
import threading
import time

import pytest

from jittersim.api.homepage import PairingStore, PolicyServer

ORIGIN = "chrome-extension://" + "a" * 32


class Resolver:
    def resolve(self, _):
        return ("93.184.216.34",)

    def close(self):
        pass


class Helper:
    def homepage_snapshot(self, **_):
        return {
            "verified": True,
            "mac": "02:00:00:00:00:02",
            "epoch": "boot",
            "chance": 100,
            "protected": False,
            "receipt": {},
        }

    def homepage_confirm(self, _):
        return True


@pytest.fixture
def listener(tmp_path):
    key, certificate = tmp_path / "server.key", tmp_path / "server.crt"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
            "-days",
            "1",
            "-subj",
            "/CN=netlab.local",
            "-addext",
            "subjectAltName=DNS:netlab.local,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    store = PairingStore(Helper(), resolver=Resolver())
    server = PolicyServer(("127.0.0.1", 0), store, context)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server, store, ssl.create_default_context(cafile=str(certificate))
    finally:
        store.close()
        server.shutdown()
        server.server_close()
        worker.join(1)


def request(listener, path, payload, origin=ORIGIN, authorization=None, trusted=True):
    server, _, context = listener
    connection = http.client.HTTPSConnection(
        "127.0.0.1",
        server.server_port,
        context=context if trusted else ssl.create_default_context(),
        timeout=2,
    )
    headers = {"Host": "netlab.local:8443", "Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    if authorization:
        headers["Authorization"] = authorization
    try:
        connection.request("POST", path, json.dumps(payload), headers)
        response = connection.getresponse()
        return response.status, dict(response.headers), json.loads(response.read())
    finally:
        connection.close()


def test_verified_tls_pairing_cors_policy_and_revocation(listener):
    _, store, _ = listener
    code = store.create("a" * 32, ["https://example.com/article"])["code"]
    status, headers, session = request(listener, "/homepage/pair", {"code": code})
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == ORIGIN
    status, _, result = request(
        listener,
        "/homepage/policy",
        {"origin": "https://example.com", "request_id": "a" * 32},
        authorization="Homepage " + session["capability"],
    )
    assert status == 200 and result["enabled"] and result["chance"] == 100
    assert set(result) == {
        "enabled",
        "chance",
        "kind",
        "origin",
        "request_id",
        "revision",
        "lease_ms",
    }
    store.revoke()
    assert (
        request(
            listener,
            "/homepage/policy",
            {"origin": "https://example.com", "request_id": "a" * 32},
            authorization="Homepage " + session["capability"],
        )[0]
        == 403
    )


def test_absent_origin_and_admin_token_cannot_pair(listener):
    _, store, _ = listener
    code = store.create("a" * 32, ["https://example.com/article"])["code"]
    assert request(listener, "/homepage/pair", {"code": code}, origin=None)[0] == 403
    assert (
        request(
            listener,
            "/homepage/policy",
            {"origin": "https://example.com", "request_id": "a" * 32},
            authorization="Bearer admin-token",
        )[0]
        == 403
    )


def test_untrusted_certificate_is_refused_normally(listener):
    with pytest.raises(ssl.SSLCertVerificationError):
        request(listener, "/homepage/pair", {"code": "a" * 32}, trusted=False)


def test_idle_tls_connections_have_four_physical_slots_and_expire(listener):
    server, store, _ = listener
    sockets = [socket.create_connection(server.server_address) for _ in range(4)]
    try:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and server.slots.acquire(blocking=False):
            server.slots.release()
            time.sleep(0.01)
        assert not server.slots.acquire(blocking=False)
        extra = socket.create_connection(server.server_address)
        extra.settimeout(1)
        assert extra.recv(1) == b""
        extra.close()
        time.sleep(0.8)
        code = store.create("a" * 32, ["https://example.com/article"])["code"]
        assert request(listener, "/homepage/pair", {"code": code})[0] == 200
    finally:
        for connection in sockets:
            connection.close()
