"""jittersim connect saves where the dashboard is and never a token."""

import json
from unittest.mock import patch

import pytest

from jittersim.cli import main
from jittersim.remote_client import CERTIFICATE_MESSAGE, Connection, resolve_connection
from tests.remote_support import (
    TEST_TOKEN,
    make_certificate,
)

pytestmark = pytest.mark.usefixtures("remote_environment")


# connect -----------------------------------------------------------------------------------


def test_connect_checks_the_certificate_without_a_token_and_saves_no_secret(dashboard, capsys,
                                                                           tmp_path):
    dashboard.answer("GET", "/api/health", {"status": "ok"})
    with patch("jittersim.remote_client.getpass.getpass") as prompt:
        code = main(["connect", "--url", dashboard.url + "/", "--ca-cert",
                     str(dashboard.certificate)])
    output = capsys.readouterr()
    assert code == 0
    prompt.assert_not_called()
    assert dashboard.requests[0]["authorization"] is None
    saved = tmp_path / "home" / ".config" / "jittersim" / "cli.json"
    assert json.loads(saved.read_text()) == {
        "url": dashboard.url, "ca_cert": str(dashboard.certificate.resolve())}
    assert saved.stat().st_mode & 0o777 == 0o600
    assert "never saved" in output.out
    # later commands find the dashboard on their own
    assert resolve_connection(None, None) == Connection(
        dashboard.url, dashboard.certificate.resolve()
    )


def test_connect_refuses_a_certificate_that_does_not_match(dashboard, capsys, tmp_path):
    other, _ = make_certificate(tmp_path, "other")
    code = main(["connect", "--url", dashboard.url, "--ca-cert", str(other)])
    output = capsys.readouterr()
    assert code == 1
    assert output.err.strip() == CERTIFICATE_MESSAGE
    assert not (tmp_path / "home" / ".config" / "jittersim" / "cli.json").exists()


def test_connect_without_check_contacts_nothing(dashboard, capsys, tmp_path):
    code = main(["connect", "--url", dashboard.url, "--ca-cert", str(dashboard.certificate),
                 "--no-check", "--json"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["checked"] is False
    assert dashboard.requests == []


def test_connect_refuses_plain_http_and_a_missing_certificate(dashboard, capsys, tmp_path):
    assert main(["connect", "--url", "http://127.0.0.1:8080", "--ca-cert",
                 str(dashboard.certificate)]) == 2
    assert "Plain HTTP is refused" in capsys.readouterr().err
    assert main(["connect", "--url", dashboard.url, "--ca-cert", str(tmp_path / "x.crt")]) == 2
    assert main(["connect", "--url", dashboard.url]) == 2
    assert dashboard.requests == []
    assert not (tmp_path / "home" / ".config" / "jittersim" / "cli.json").exists()


def test_a_token_given_to_connect_is_refused_and_not_echoed(capsys):
    assert main(["connect", "--token", TEST_TOKEN]) == 2
    assert main(["connect", f"--admin-token={TEST_TOKEN}"]) == 2
    output = capsys.readouterr()
    assert TEST_TOKEN not in output.out + output.err
    with pytest.raises(SystemExit) as stop:
        main(["connect", TEST_TOKEN])
    assert stop.value.code == 2
    assert TEST_TOKEN not in capsys.readouterr().err

