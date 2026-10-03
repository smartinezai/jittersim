"""jittersim wifi show against a disposable HTTPS dashboard."""

import json

import pytest

from jittersim.cli import main
from tests.remote_support import (
    ROUTER_PASSWORD,
    TEST_TOKEN,
    WIFI_PASSWORD,
    run,
)

pytestmark = pytest.mark.usefixtures("remote_environment")


# wifi show ---------------------------------------------------------------------------------


def test_wifi_show_reads_the_name_with_a_hidden_router_password(dashboard, capsys):
    dashboard.answer("POST", "/api/router/wifi-credentials", {"result": {"ssid": "Browser home"}})
    code, out, err, prompt = run(dashboard, capsys, "wifi", "show",
                                 prompts=(TEST_TOKEN, ROUTER_PASSWORD))
    assert code == 0
    assert prompt.call_count == 2
    assert "Router admin password (hidden)" in prompt.call_args_list[1][0][0]
    assert dashboard.requests[0]["body"] == {"admin_password": ROUTER_PASSWORD,
                                             "include_password": False}
    assert out == "Router Wi-Fi name: Browser home\n"
    assert err == ""


def test_wifi_show_password_warns_first_and_asks_for_it(dashboard, capsys):
    dashboard.answer("POST", "/api/router/wifi-credentials",
                     {"result": {"ssid": "Browser home", "password": WIFI_PASSWORD}})
    code, out, err, _ = run(dashboard, capsys, "wifi", "show", "--password",
                            prompts=(TEST_TOKEN, ROUTER_PASSWORD))
    assert code == 0
    assert dashboard.requests[0]["body"]["include_password"] is True
    assert f"Router Wi-Fi password: {WIFI_PASSWORD}" in out
    assert "stays in this terminal's scrollback" in err


def test_wifi_show_json(dashboard, capsys):
    dashboard.answer("POST", "/api/router/wifi-credentials", {"result": {"ssid": "Home"}})
    code, out, _, _ = run(dashboard, capsys, "wifi", "show", "--json",
                          prompts=(TEST_TOKEN, ROUTER_PASSWORD))
    assert code == 0
    assert json.loads(out) == {"ssid": "Home"}


def test_wifi_show_failure_never_repeats_the_router_password(dashboard, capsys):
    dashboard.answer("POST", "/api/router/wifi-credentials",
                     {"error": f"router said {ROUTER_PASSWORD} is wrong"}, status=502)
    code, out, err, _ = run(dashboard, capsys, "wifi", "show",
                            prompts=(TEST_TOKEN, ROUTER_PASSWORD))
    assert code == 1
    assert "[hidden]" in err


def test_wifi_reset_still_runs_on_the_pi(capsys):
    with pytest.raises(SystemExit) as stop:
        main(["wifi", "--help"])
    assert stop.value.code == 0
    text = capsys.readouterr().out
    assert "reset" in text and "show" in text and "on the Pi" in text and "on your Mac" in text
