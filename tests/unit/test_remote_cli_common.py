"""Refusals every remote command shares: plain HTTP, wrong certificates, secrets, redirects."""

from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim.cli import main
from jittersim.remote_client import CERTIFICATE_MESSAGE
from tests.remote_support import (
    ROUTER_PASSWORD,
    TEST_TOKEN,
    fake_dashboard,
    make_certificate,
    run,
)

pytestmark = pytest.mark.usefixtures("remote_environment")


# common refusals ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [["status"], ["profiles", "list"], ["traffic"], ["impact"], ["wifi", "show"],
     ["traffic", "block", "example.com"], ["profiles", "apply-all", "slow"],
     ["profiles", "stop-all"], ["backup", "pull"]],
)
def test_plain_http_is_refused_before_any_prompt_or_request(tmp_path, capsys, command):
    with fake_dashboard(tmp_path, tls=False) as plain:
        certificate, _ = make_certificate(tmp_path, "pi")
        with patch("jittersim.remote_client.getpass.getpass") as prompt:
            code = main([*command, "--url", plain.url, "--ca-cert", str(certificate)])
    assert code == 2
    prompt.assert_not_called()
    assert plain.requests == []
    assert "Plain HTTP is refused" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command",
    [["status"], ["profiles", "list"], ["traffic"], ["impact"], ["wifi", "show"],
     ["backup", "pull"]],
)
def test_a_wrong_certificate_never_sends_the_token(dashboard, capsys, tmp_path, command):
    other, _ = make_certificate(tmp_path, "other")
    with patch("jittersim.remote_client.getpass.getpass", return_value=TEST_TOKEN):
        code = main([*command, "--url", dashboard.url, "--ca-cert", str(other)])
    output = capsys.readouterr()
    assert code == 1
    assert output.err.strip() == CERTIFICATE_MESSAGE
    assert dashboard.requests == []
    assert TEST_TOKEN not in output.out + output.err


@pytest.mark.parametrize(
    "arguments",
    [["status", "--token", TEST_TOKEN], ["traffic", f"--token={TEST_TOKEN}"],
     ["wifi", "show", f"--password={ROUTER_PASSWORD}"], ["impact", "--admin-token", TEST_TOKEN]],
)
def test_secrets_given_as_arguments_are_refused_and_never_echoed(capsys, arguments):
    with patch("jittersim.remote_client.getpass.getpass") as prompt:
        code = main(arguments)
    output = capsys.readouterr()
    assert code == 2
    prompt.assert_not_called()
    assert TEST_TOKEN not in output.out + output.err
    assert ROUTER_PASSWORD not in output.out + output.err
    assert "never accepted as arguments" in output.err


def test_unrecognized_arguments_are_not_echoed(capsys):
    with pytest.raises(SystemExit) as stop:
        main(["traffic", TEST_TOKEN])
    assert stop.value.code == 2
    output = capsys.readouterr()
    assert TEST_TOKEN not in output.err
    with pytest.raises(SystemExit) as stop:
        main(["profiles", TEST_TOKEN])
    assert stop.value.code == 2
    assert TEST_TOKEN not in capsys.readouterr().err


def test_a_redirect_is_refused_by_every_command(dashboard, capsys):
    dashboard.answer("GET", "/api/profiles", {}, status=307,
                     headers={"Location": "https://evil.example/api/profiles"})
    code, _, err, _ = run(dashboard, capsys, "profiles", "list")
    assert code == 1
    assert "redirect" in err
    assert dashboard.paths() == [("GET", "/api/profiles")]


def test_an_answer_that_is_not_understood_is_one_line_and_no_traceback(dashboard, capsys):
    dashboard.answer("GET", "/api/profiles", {"profiles": "not a list"})
    code, _, err, _ = run(dashboard, capsys, "profiles", "list")
    assert code == 1
    assert err.strip() == "The dashboard sent an answer this command does not understand."


def test_the_environment_token_is_used_for_scripts(dashboard, capsys, monkeypatch):
    monkeypatch.setenv("JITTERSIM_ADMIN_TOKEN", TEST_TOKEN)
    dashboard.answer("GET", "/api/profiles", {"profiles": []})
    dashboard.answer("GET", "/api/general-profile", {"profile": None, "active": False})
    code, out, _, prompt = run(dashboard, capsys, "profiles", "list", prompts=())
    assert code == 0
    prompt.assert_not_called()
    assert dashboard.requests[0]["authorization"] == "Bearer " + TEST_TOKEN


def test_help_names_where_the_commands_run_and_the_token_risk(capsys):
    for command in (["status", "--json", "--help"], ["impact", "--help"],
                    ["wifi", "show", "--help"], ["profiles", "list", "--help"],
                    ["traffic", "block", "--help"], ["connect", "--help"],
                    ["backup", "pull", "--help"]):
        with pytest.raises(SystemExit) as stop:
            main(command)
        assert stop.value.code == 0
        text = capsys.readouterr().out
        assert "Runs on your Mac" in text
        assert "JITTERSIM_ADMIN_TOKEN" in text
        assert "never accepted as an argument" in text


def test_the_top_level_help_names_the_mac_commands_and_the_pi_commands(capsys):
    assert main(["--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "Headless commands on the Pi:" in text
    assert ("Commands for your Mac through the Pi's HTTPS dashboard: connect, status, "
            "profiles list/apply-all/stop-all, traffic, wifi show, impact and backup pull.") in text


def test_the_remote_modules_load_without_the_pi_networking_code():
    for name in ("remote_cli.py", "remote_client.py", "dashboard_rules.py", "impact_rules.py"):
        source = (Path(__file__).parents[2] / "src" / "jittersim" / name).read_text()
        assert "jittersim.networking" not in source, name
