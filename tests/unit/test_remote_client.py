"""The shared HTTPS client of the remote commands: addresses, saved connection, token, TLS."""

import getpass
import json
import os
import ssl
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim import remote_client
from jittersim.remote_client import (
    CERTIFICATE_MESSAGE,
    Connection,
    DashboardClient,
    RemoteError,
    js_number,
    js_round,
    read_config,
    read_token,
    resolve_connection,
    validate_url,
    write_config,
)
from tests.remote_support import TEST_TOKEN, fake_dashboard, make_certificate


@pytest.mark.parametrize(
    "url",
    [
        "http://pi:8080",
        "HTTP://pi:8080",
        "ftp://pi",
        "https://user:secret@pi:8080",
        "https://user@pi:8080",
        "https://pi:8080/path",
        "https://pi:8080/api/state",
        "https://pi?token=secret",
        "https://pi:8080/?x",
        "https://pi#fragment",
        "https://",
        "https://pi:notaport",
        "https://pi :8080",
        "pi:8080",
        "",
        "https://pi\n:8080",
    ],
)
def test_unsafe_or_unclear_addresses_are_usage_errors(url):
    with pytest.raises(RemoteError) as error:
        validate_url(url)
    assert error.value.exit_code == 2
    assert "secret" not in error.value.message


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://192.168.0.17:8080", "https://192.168.0.17:8080"),
        ("https://192.168.0.17:8080/", "https://192.168.0.17:8080"),
        ("https://jittersim.local:8080", "https://jittersim.local:8080"),
        ("https://[::1]:8080", "https://[::1]:8080"),
    ],
)
def test_plain_dashboard_addresses_are_kept(url, expected):
    assert validate_url(url) == expected


def test_saved_connection_holds_only_the_address_and_certificate_with_private_modes(tmp_path):
    path = tmp_path / "home" / ".config" / "jittersim" / "cli.json"
    write_config(path, Connection("https://pi:8080", tmp_path / "pi.crt"))
    assert json.loads(path.read_text()) == {
        "url": "https://pi:8080", "ca_cert": str(tmp_path / "pi.crt")}
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert read_config(path) == {"url": "https://pi:8080", "ca_cert": str(tmp_path / "pi.crt")}


def test_a_token_in_the_saved_connection_is_never_read(tmp_path):
    path = tmp_path / "cli.json"
    path.write_text(json.dumps({"url": "https://pi:8080", "ca_cert": "/x", "token": "abc"}))
    path.chmod(0o600)
    assert read_config(path) == {"url": "https://pi:8080", "ca_cert": "/x"}


@pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
def test_a_saved_connection_others_can_change_is_refused(tmp_path, mode):
    path = tmp_path / "cli.json"
    path.write_text(json.dumps({"url": "https://pi:8080", "ca_cert": "/x"}))
    path.chmod(mode)
    with pytest.raises(RemoteError) as error:
        read_config(path)
    assert "chmod 600" in error.value.message


def test_a_missing_saved_connection_gives_nothing(tmp_path):
    assert read_config(tmp_path / "missing.json") == {}


def test_options_win_over_the_environment_and_the_environment_over_the_file(tmp_path):
    first = tmp_path / "first.crt"
    second = tmp_path / "second.crt"
    third = tmp_path / "third.crt"
    for path in (first, second, third):
        path.write_text("x")
    saved = tmp_path / "cli.json"
    write_config(saved, Connection("https://saved:8080", third))
    environ = {"JITTERSIM_URL": "https://environment:8080", "JITTERSIM_CA_CERT": str(second)}
    assert resolve_connection("https://option:8080", first, environ=environ, path=saved) == (
        Connection("https://option:8080", first)
    )
    assert resolve_connection(None, None, environ=environ, path=saved) == Connection(
        "https://environment:8080", second
    )
    assert resolve_connection(None, None, environ={}, path=saved) == Connection(
        "https://saved:8080", third
    )


def test_without_any_address_the_command_explains_connect(tmp_path):
    with pytest.raises(RemoteError) as error:
        resolve_connection(None, None, environ={}, path=tmp_path / "missing.json")
    assert error.value.exit_code == 2
    assert "jittersim connect" in error.value.message


def test_a_missing_certificate_file_is_a_usage_error(tmp_path):
    with pytest.raises(RemoteError) as error:
        resolve_connection("https://pi:8080", tmp_path / "missing.crt", environ={})
    assert error.value.exit_code == 2


def test_the_token_comes_from_a_hidden_prompt():
    answer = " " + TEST_TOKEN + "\n"
    with patch("jittersim.remote_client.getpass.getpass", return_value=answer) as prompt:
        assert read_token({}) == TEST_TOKEN
    prompt.assert_called_once()
    assert "hidden" in prompt.call_args[0][0]


def test_the_environment_token_skips_the_prompt():
    with patch("jittersim.remote_client.getpass.getpass") as prompt:
        assert read_token({"JITTERSIM_ADMIN_TOKEN": TEST_TOKEN}) == TEST_TOKEN
    prompt.assert_not_called()


@pytest.mark.parametrize("token", ["", "with space", "tab\there", "naïve", "x\x7f"])
def test_tokens_with_unsafe_characters_are_refused(token):
    with patch("jittersim.remote_client.getpass.getpass", return_value=token):
        with pytest.raises(RemoteError) as error:
            read_token({})
    assert token.strip() == "" or token not in error.value.message


def test_a_terminal_that_would_show_the_token_is_refused():
    def echoing(prompt):
        import warnings

        warnings.warn("echo", getpass.GetPassWarning)
        return TEST_TOKEN

    with patch("jittersim.remote_client.getpass.getpass", side_effect=echoing):
        with pytest.raises(RemoteError) as error:
            read_token({})
    assert "hidden prompt" in error.value.message


def test_requests_carry_the_token_to_the_verified_dashboard_only(tmp_path):
    with fake_dashboard(tmp_path) as dashboard:
        dashboard.answer("GET", "/api/state", {"state": {"observed_state": "applied"}})
        client = DashboardClient(Connection(dashboard.url, dashboard.certificate), TEST_TOKEN)
        assert client.get("/api/state") == {"state": {"observed_state": "applied"}}
    assert dashboard.requests[0]["authorization"] == "Bearer " + TEST_TOKEN


def test_a_certificate_that_does_not_match_fails_with_the_fingerprint_sentence(tmp_path):
    other, _ = make_certificate(tmp_path, "other")
    with fake_dashboard(tmp_path) as dashboard:
        client = DashboardClient(Connection(dashboard.url, other), TEST_TOKEN)
        with pytest.raises(RemoteError) as error:
            client.get("/api/state")
    assert error.value.message == CERTIFICATE_MESSAGE
    assert "fingerprint" in CERTIFICATE_MESSAGE and "installer printed" in CERTIFICATE_MESSAGE
    assert dashboard.requests == []


def test_the_certificate_is_checked_with_the_given_file(tmp_path):
    with fake_dashboard(tmp_path) as dashboard:
        with patch(
            "jittersim.remote_client.ssl.create_default_context",
            wraps=ssl.create_default_context,
        ) as create:
            DashboardClient(Connection(dashboard.url, dashboard.certificate), TEST_TOKEN)
    create.assert_called_once_with(cafile=str(dashboard.certificate))


def test_a_redirect_is_refused_and_never_followed(tmp_path):
    with fake_dashboard(tmp_path) as dashboard:
        dashboard.answer("GET", "/api/state", {}, status=302,
                         headers={"Location": dashboard.url + "/elsewhere"})
        client = DashboardClient(Connection(dashboard.url, dashboard.certificate), TEST_TOKEN)
        with pytest.raises(RemoteError) as error:
            client.get("/api/state")
    assert "redirect" in error.value.message.lower()
    assert dashboard.paths() == [("GET", "/api/state")]


def test_an_oversized_answer_is_refused(tmp_path):
    with fake_dashboard(tmp_path) as dashboard:
        dashboard.answer("GET", "/api/state", b"{" + b" " * 2048 + b"}")
        client = DashboardClient(
            Connection(dashboard.url, dashboard.certificate), TEST_TOKEN, max_bytes=1024
        )
        with pytest.raises(RemoteError) as error:
            client.get("/api/state")
    assert error.value.message == "The dashboard answer was too large."


def test_a_refused_token_says_so_without_the_token(tmp_path):
    with fake_dashboard(tmp_path) as dashboard:
        dashboard.answer("GET", "/api/state", {"error": "valid bearer authentication is required"},
                         status=401)
        client = DashboardClient(Connection(dashboard.url, dashboard.certificate), TEST_TOKEN)
        with pytest.raises(RemoteError) as error:
            client.get("/api/state")
    assert error.value.status == 401
    assert "refused the admin token" in error.value.message
    assert TEST_TOKEN not in error.value.message


def test_dashboard_messages_never_repeat_a_secret(tmp_path):
    with fake_dashboard(tmp_path) as dashboard:
        dashboard.answer("POST", "/api/x", {"error": f"bad {TEST_TOKEN} and router-pass"},
                         status=400)
        client = DashboardClient(Connection(dashboard.url, dashboard.certificate), TEST_TOKEN)
        client.add_secret("router-pass")
        with pytest.raises(RemoteError) as error:
            client.post("/api/x", {})
    assert TEST_TOKEN not in error.value.message
    assert "router-pass" not in error.value.message
    assert "[hidden]" in error.value.message


def test_an_unreachable_dashboard_is_one_plain_sentence(tmp_path):
    certificate, _ = make_certificate(tmp_path)
    client = DashboardClient(Connection("https://127.0.0.1:9", certificate), TEST_TOKEN, timeout=2)
    with pytest.raises(RemoteError) as error:
        client.get("/api/state")
    assert error.value.message.startswith("Could not reach the dashboard at https://127.0.0.1:9")


def test_environment_proxies_are_not_used(tmp_path, monkeypatch):
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    with fake_dashboard(tmp_path) as dashboard:
        dashboard.answer("GET", "/api/health", {"status": "ok"})
        client = DashboardClient(Connection(dashboard.url, dashboard.certificate), None)
        assert client.get("/api/health") == {"status": "ok"}
    assert dashboard.requests[0]["authorization"] is None


@pytest.mark.parametrize(
    "value, text",
    [
        (2, "2"), (2.0, "2"), (4.7, "4.7"), (0.1, "0.1"), (21.6, "21.6"), (-3.5, "-3.5"),
        (1e21, "1e+21"), (1e20, "100000000000000000000"), (0.000001, "0.000001"),
        (1e-7, "1e-7"), (123456.789, "123456.789"), (0.0, "0"),
    ],
)
def test_numbers_are_written_like_javascript(value, text):
    assert js_number(value) == text


@pytest.mark.parametrize(
    "value, rounded",
    [(2.5, 3), (2.4999, 2), (-2.5, -2), (-2.6, -3), (0.49999999999999994, 0), (1799.6, 1800)],
)
def test_rounding_is_javascript_math_round(value, rounded):
    assert js_round(value) == rounded


def test_the_client_module_never_imports_the_pi_helper():
    source = Path(remote_client.__file__).read_text()
    assert "networking" not in source
    assert os.path.basename(remote_client.__file__) == "remote_client.py"
