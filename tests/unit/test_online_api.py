"""The dashboard side of the online check: the API, the helper actions and the page script.

The dashboard asks the helper and the helper asks the root fetch unit. The token and the download
never reach the dashboard process, so these tests give it a helper that only reports state.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim import updates
from jittersim.api.server import ControlServer
from jittersim.config.storage import ConfigRepository
from jittersim.networking import helper as helper_module
from jittersim.networking.helper import HelperClient, HelperClientError, HelperService
from tests.github_support import TOKEN
from tests.release_support import have_ssh_keygen
from tests.unit.test_online_release import Online

ROOT = Path(__file__).resolve().parents[2]


class DelegatingHelper:
    """What the real helper does for the online actions, against a stand-in Pi."""

    def __init__(self, online):
        self.online = online
        self.clock = [2_000_000.0]
        self.ids = iter("%016x" % number for number in range(1, 99))
        self.calls = []

    def now(self):
        return self.clock[0]

    def release_status(self):
        return updates.update_status(root=self.online.pi.root, code=self.online.pi.code,
                                     config=self.online.pi.config)

    def online_check(self, *, force=False):
        self.calls.append(("check", force))
        return updates.request_online_check(
            force=force, root=self.online.pi.root, config=self.online.pi.config,
            run=self.online.run, now=self.now, job_id=lambda: next(self.ids))

    def online_status(self, job=None):
        return updates.online_overview(job, root=self.online.pi.root,
                                       config=self.online.pi.config, now=self.now)

    def online_settings(self, changes=None):
        self.calls.append(("settings", changes))
        if changes is not None:
            updates.set_online_settings(changes, root=self.online.pi.root)
        return self.online_status()

    def run_unit(self):
        return updates.run_fetch_job(
            root=self.online.pi.root, code=self.online.pi.code, config=self.online.pi.config,
            source=self.online.source(), run=self.online.run, now=self.now)


@pytest.fixture
def stack(tmp_path):
    if not have_ssh_keygen():
        pytest.skip("ssh-keygen signs the releases")
    online = Online(tmp_path)
    repository = ConfigRepository(":memory:")
    server = ControlServer(("127.0.0.1", 0), repository)
    helper = DelegatingHelper(online)
    server.helper_client = helper
    yield server, helper, online
    server.server_close()
    repository.close()
    online.github.stop()


def post(server, path, payload=None):
    status, _, body = server.write_response_for(path, payload or {})
    return status, json.loads(body)


def get(server, path):
    status, _, body = server.response_for(path)
    return status, json.loads(body)


def test_check_now_returns_a_job_at_once_and_polling_shows_the_result(stack):
    server, helper, online = stack
    status, answer = post(server, "/api/updates/check-online", {"force": True})
    assert status == 202 and answer["state"] == "running" and answer["job"]
    assert online.github.requests == []
    status, running = get(server, "/api/updates/check-online/" + answer["job"])
    assert status == 200 and running["state"] == "running"
    helper.run_unit()
    status, done = get(server, "/api/updates/check-online/" + answer["job"])
    assert done["state"] == "done" and done["result"] == "staged" and done["available"] == "v7"
    assert done["checked_at"] and done["message"]
    assert done["settings"] == {"daily_check": False, "install_automatically": False}
    assert get(server, "/api/updates/check-online")[1]["job"] == answer["job"]
    # The release shows in the ordinary update status, ready to install.
    status, release = get(server, "/api/updates")
    assert release["available"] == "v7" and release["installable"] is True
    assert release["token_set"] is True and release["source"] == "github"
    assert TOKEN not in json.dumps([answer, running, done, release])


def test_the_dashboard_asks_for_a_forced_check_only_when_check_now_says_so(stack):
    server, helper, _ = stack
    post(server, "/api/updates/check-online")
    post(server, "/api/updates/check-online", {"force": True})
    assert helper.calls == [("check", False), ("check", True)]


def test_a_repeated_automatic_request_is_answered_from_the_last_check(stack):
    server, helper, online = stack
    post(server, "/api/updates/check-online")
    helper.run_unit()
    count = len(online.github.requests)
    helper.clock[0] += 120
    status, answer = post(server, "/api/updates/check-online")
    assert status == 202 and answer["cached"] is True and answer["state"] == "done"
    assert len(online.github.requests) == count


@pytest.mark.parametrize("payload", [{"force": "yes"}, {"token": TOKEN}, {"force": True, "x": 1}])
def test_the_check_endpoint_takes_no_token_and_only_a_true_or_false_force(stack, payload):
    server, helper, _ = stack
    status, answer = post(server, "/api/updates/check-online", payload)
    assert status == 400 and helper.calls == []
    assert TOKEN not in json.dumps(answer)


def test_a_job_name_that_is_not_a_job_is_not_passed_on(stack):
    server, _, _ = stack
    for job in ("../etc", "x", "A" * 16, "1" * 17):
        status, _ = get(server, "/api/updates/check-online/" + job)
        assert status == 404


def test_the_settings_read_and_change_with_a_confirmation_for_automatic_installs(stack):
    server, helper, online = stack
    status, settings = get(server, "/api/updates/online-settings")
    assert settings["settings"] == {"daily_check": False, "install_automatically": False}
    assert post(server, "/api/updates/online-settings", {"daily_check": True})[0] == 200
    assert updates.online_settings(online.pi.root)["daily_check"] is True
    status, refused = post(server, "/api/updates/online-settings",
                           {"install_automatically": True})
    assert status == 400 and "confirm" in refused["error"]
    assert updates.online_settings(online.pi.root)["install_automatically"] is False
    status, _ = post(server, "/api/updates/online-settings",
                     {"install_automatically": True, "confirm": True})
    assert status == 200 and updates.online_settings(online.pi.root)["install_automatically"]
    # Turning it off needs no confirmation.
    assert post(server, "/api/updates/online-settings", {"install_automatically": False})[0] == 200
    for payload in ({}, {"daily_check": "on"}, {"other": True}, {"confirm": True}):
        assert post(server, "/api/updates/online-settings", payload)[0] == 400


def test_without_the_helper_the_endpoints_say_so(stack):
    server, _, _ = stack
    server.helper_client = None
    assert post(server, "/api/updates/check-online")[0] == 503
    assert get(server, "/api/updates/check-online")[0] == 503
    assert get(server, "/api/updates/online-settings")[0] == 503


def test_a_helper_that_fails_gives_a_plain_503(stack):
    server, helper, _ = stack

    def broken(**options):
        raise HelperClientError("The networking helper is busy.")

    helper.online_check = broken
    status, answer = post(server, "/api/updates/check-online")
    assert status == 503 and answer["message"] == "The networking helper is busy."


def test_the_endpoints_need_the_admin_token(stack):
    server, _, _ = stack
    from jittersim.api import server as server_module

    for path in ("/api/updates/check-online", "/api/updates/online-settings"):
        assert path in server_module.WRITE_PATHS or path.startswith("/api/")
    assert "/api/updates/check-online" in server_module.WRITE_PATHS
    assert "/api/updates/online-settings" in server_module.WRITE_PATHS


# The helper


def test_the_helper_actions_report_state_and_never_the_token(stack, monkeypatch):
    _, _, online = stack
    pi = online.pi
    monkeypatch.setattr(updates, "UPDATE_ROOT", pi.root)
    monkeypatch.setattr(updates, "CONFIG_ROOT", pi.config)
    monkeypatch.setattr(updates, "CODE_ROOT", pi.code)
    for name in ("request_online_check", "online_overview", "set_online_settings"):
        original = getattr(updates, name)
        monkeypatch.setattr(helper_module, name, _bound(original, pi, online))
    answer = HelperService._handle_payload(None, {"action": "update-online-check", "force": True})
    assert answer["ok"] is True and answer["state"] == "running"
    answer = HelperService._handle_payload(
        None, {"action": "update-online-settings", "changes": {"daily_check": True}})
    assert answer["ok"] and answer["settings"]["daily_check"] is True
    answer = HelperService._handle_payload(
        None, {"action": "update-online-settings", "changes": {"daily_check": "x"}})
    assert answer["ok"] is False and "daily_check" in answer["error"]
    answer = HelperService._handle_payload(None, {"action": "update-online-status", "job": 7})
    assert answer["ok"] and TOKEN not in json.dumps(answer)


def _bound(function, pi, online):
    def call(*args, **options):
        options.setdefault("root", pi.root)
        if function.__name__ != "set_online_settings":
            options.setdefault("config", pi.config)
        if function.__name__ == "request_online_check":
            options.setdefault("run", online.run)
        return function(*args, **options)

    return call


def test_the_client_methods_send_the_right_actions():
    client = HelperClient.__new__(HelperClient)
    sent = []
    client._request = lambda payload, **options: sent.append(payload) or {"ok": True}
    client.online_check(force=True)
    client.online_check()
    client.online_status("a" * 16)
    client.online_settings({"daily_check": True})
    client.online_settings()
    assert sent == [
        {"action": "update-online-check", "force": True},
        {"action": "update-online-check", "force": False},
        {"action": "update-online-status", "job": "a" * 16},
        {"action": "update-online-settings", "changes": {"daily_check": True}},
        {"action": "update-online-settings", "changes": None},
    ]


# The units and the installer


def test_the_fetch_unit_is_a_root_one_shot_that_needs_a_request():
    text = (ROOT / "deploy/raspberry-pi/jittersim-update-fetch.service").read_text()
    assert "Type=oneshot" in text and "User=root" in text
    assert "ConditionPathExists=/var/lib/jittersim-updates/online-request.json" in text
    assert "ExecStart=/usr/bin/python3 -I /usr/local/lib/jittersim-update.py fetch" in text
    assert "NoNewPrivileges=true" in text
    assert "[Install]" not in text


def test_the_installer_and_the_recovery_list_know_the_fetch_unit():
    installer = (ROOT / "deploy/raspberry-pi/install.sh").read_text()
    assert "jittersim-update-fetch.service" in installer
    assert updates.FETCH_UNIT in updates.UNITS
    assert updates.FETCH_UNIT not in updates.SERVICES


def test_the_sudoers_rule_is_unchanged_by_the_online_check():
    assert "fetch" not in updates.SUDOERS_TEXT and "token" not in updates.SUDOERS_TEXT
    assert updates.SUDOERS_TEXT.count("NOPASSWD") == 1


# The command line


def test_update_check_online_runs_the_root_manager_check(monkeypatch):
    from jittersim import cli

    with patch("jittersim.updates.main", return_value=0) as manager:
        assert cli.main(["update", "check", "--online"]) == 0
    manager.assert_called_once_with(["check-online"], prog="jittersim update")
    with patch("jittersim.updates.main") as manager:
        assert cli.main(["update", "check", "--online", "--extra"]) == 2
    manager.assert_not_called()


def test_set_and_clear_token_are_local_manager_commands():
    from jittersim import cli

    assert {"set-token", "clear-token"} <= set(cli.LOCAL_UPDATE_COMMANDS)
    with patch("jittersim.updates.main", return_value=0) as manager:
        assert cli.main(["update", "set-token"]) == 0
    manager.assert_called_once_with(["set-token"], prog="jittersim update")


def test_update_check_shows_only_whether_a_token_is_set(capsys):
    from jittersim import cli

    status = {"installed": "old", "available": None, "phase": "idle", "trusted_keys": 1,
              "token_set": True}
    with patch("jittersim.cli.HelperClient") as helper, \
            patch("jittersim.cli._diagnostics", return_value=0):
        helper.return_value.release_status.return_value = status
        assert cli.main(["update", "check"]) == 0
        assert "Online check token: set" in capsys.readouterr().out
        status["token_set"] = False
        cli.main(["update", "check"])
        assert "Online check token: not set" in capsys.readouterr().out


# The page script


def run_node(source):
    return subprocess.run(["node", "-e", source], capture_output=True, text=True, timeout=60)


@pytest.mark.skipif(subprocess.run(["which", "node"], capture_output=True).returncode != 0,
                    reason="node runs the page script")
def test_the_page_script_has_the_online_controls_and_never_a_token_field():
    script = (ROOT / "src/jittersim/api/workflows.js").read_text()
    for needle in ("check-online", "online-settings", "release-check",
                   "online-daily-check", "online-install-automatically"):
        assert needle in script
    lowered = script.lower()
    assert 'type="password"' not in lowered and "update-token" not in lowered
    assert "set-token" in script
    result = run_node("new Function(require('fs').readFileSync(%r,'utf8'))" % str(
        ROOT / "src/jittersim/api/workflows.js"))
    assert result.returncode == 0, result.stderr
