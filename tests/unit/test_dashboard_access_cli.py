"""The Mac command verifies TLS and never forwards its token on redirects."""

import json
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from jittersim.cli import main
from jittersim.dashboard_access_cli import register_self
from jittersim.networking.dashboard_access import AccessError


@pytest.fixture
def https_dashboard(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1",
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    observed = []
    redirect = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            observed.append((self.path, self.headers.get("Authorization"),
                             json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            if redirect:
                self.send_response(302)
                self.send_header("Location", redirect[0])
                self.end_headers()
            else:
                body = b'{"allowed":true}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, *_):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            yield f"https://127.0.0.1:{server.server_port}", cert, observed, redirect
        finally:
            server.shutdown()
            worker.join(timeout=5)


def test_mac_command_uses_hidden_token_and_connection_identity(https_dashboard, capsys):
    url, cert, observed, _ = https_dashboard
    with patch("jittersim.dashboard_access_cli.getpass.getpass", return_value="secret-test-token"):
        assert main(["dashboard-access", "allow-self", "--url", url, "--ca-cert", str(cert),
                     "--name", "My Mac"]) == 0
    assert observed == [("/api/dashboard-access/register-self", "Bearer secret-test-token", {"name": "My Mac"})]
    output = capsys.readouterr()
    assert "secret-test-token" not in output.out + output.err


def test_redirect_does_not_resend_credentials(https_dashboard):
    url, cert, observed, redirect = https_dashboard
    redirect.append(url + "/other")
    with patch("jittersim.dashboard_access_cli.getpass.getpass", return_value="secret-test-token"):
        with pytest.raises(HTTPError) as error:
            register_self(url, cert, "Mac")
    assert error.value.code == 302
    assert len(observed) == 1


def test_untrusted_certificate_is_rejected(https_dashboard):
    url, _, observed, _ = https_dashboard
    # The system roots do not trust this disposable dashboard certificate.
    with patch("jittersim.dashboard_access_cli.ssl.create_default_context",
               return_value=ssl.create_default_context()), patch(
                   "jittersim.dashboard_access_cli.getpass.getpass", return_value="secret-test-token"):
        with pytest.raises(OSError):
            register_self(url, "unused", "Mac")
    assert observed == []


@pytest.mark.parametrize("url", ["http://pi:8080", "https://user:secret@pi:8080", "https://pi/path",
                                 "https://pi?token=secret", "https://pi#fragment"])
def test_unsafe_urls_are_rejected_before_prompt(url, tmp_path):
    with patch("jittersim.dashboard_access_cli.getpass.getpass") as prompt:
        with pytest.raises(AccessError):
            register_self(url, tmp_path / "missing.crt", "Mac")
        prompt.assert_not_called()


def test_local_recovery_guides_mac_users_without_a_sudo_prompt(capsys):
    with patch("jittersim.dashboard_access_cli.os.geteuid", return_value=501):
        assert main(["dashboard-access", "list"]) == 1
    assert "On your Mac use dashboard-access allow-self" in capsys.readouterr().err
