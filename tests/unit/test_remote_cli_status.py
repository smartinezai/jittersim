"""jittersim status against a disposable HTTPS dashboard."""

import json
from unittest.mock import patch

import pytest

from jittersim.cli import main
from jittersim.remote_cli import remote_status_wanted
from tests.remote_support import (
    PROFILES,
    TEST_TOKEN,
    client,
    run,
    serve_status,
)

pytestmark = pytest.mark.usefixtures("remote_environment")


# status ------------------------------------------------------------------------------------


def test_status_lists_every_affected_device_with_the_general_profile(dashboard, capsys):
    clients = [client(f"Phone {index}", f"02:00:00:00:00:0{index}") for index in range(1, 8)]
    clients.append(client("Protected laptop", "02:00:00:00:00:99", unaffected=True))
    serve_status(dashboard, general=PROFILES[0], active=True, clients=clients,
                 lists=[{"name": "Games", "enabled": True}, {"name": "Off", "enabled": False}])
    code, out, _, prompt = run(dashboard, capsys, "status")
    assert code == 0
    prompt.assert_called_once()
    assert "Running. Your saved settings are applied. New changes apply on their own." in out
    assert "General profile: Slow connection · active" in out
    assert "7 devices affected:" in out
    assert "  Phone 7: Slow connection" in out
    assert "Protected laptop" not in out
    assert "1 protected device stays untouched" in out
    assert "Service lists: Games (every unprotected device)" in out
    assert out.splitlines()[1] == "Release on the Pi: abc1234"
    assert all(item["authorization"] == "Bearer " + TEST_TOKEN for item in dashboard.requests)


def test_status_uses_each_device_profile_and_the_group_profile_first(dashboard, capsys):
    clients = [
        client("Tablet", "02:00:00:00:00:01", assigned_device_id="tab", assigned_profile_id="slow",
               group_profile_id="lossy"),
        client("Phone", "02:00:00:00:00:02", assigned_device_id="ph", assigned_profile_id="slow"),
        client("Guest", "02:00:00:00:00:03"),
    ]
    serve_status(dashboard, general=None, clients=clients)
    code, out, _, _ = run(dashboard, capsys, "status")
    assert code == 0
    assert "General profile: none" in out
    assert "2 devices affected:" in out
    assert "  Tablet: Lossy" in out and "  Phone: Slow connection" in out
    assert "Guest" not in out


def test_status_when_the_network_is_reset(dashboard, capsys):
    serve_status(dashboard, general=PROFILES[0], observed="reset", running=False,
                 clients=[client("Phone", "02:00:00:00:00:01")])
    code, out, _, _ = run(dashboard, capsys, "status")
    assert code == 0
    assert "Off. No traffic shaping is running." in out
    assert "General profile: Slow connection · paused" in out
    assert "Profiles off" in out
    assert "Phone" not in out


def test_status_json_shape(dashboard, capsys):
    serve_status(dashboard, general=PROFILES[0], active=True,
                 clients=[client("Phone", "02:00:00:00:00:01")])
    code, out, _, _ = run(dashboard, capsys, "status", "--json")
    assert code == 0
    data = json.loads(out)
    assert data == {
        "dashboard": dashboard.url,
        "running": True,
        "state": "applied",
        "summary": "Running. Your saved settings are applied. New changes apply on their own.",
        "last_action": "apply",
        "last_result": "success",
        "general_profile": {"id": "slow", "name": "Slow connection", "status": "active"},
        "devices_summary": "1 device affected",
        "connected_devices_available": True,
        "affected_devices": [{"name": "Phone", "profile": "Slow connection"}],
        "service_lists": [],
        "protected_connected_devices": 0,
        "release": {"installed": "abc1234", "staged": None},
    }


def test_status_with_a_refused_token_exits_one_without_the_token(dashboard, capsys):
    dashboard.answer("GET", "/api/health", {"status": "ok"})
    dashboard.answer("GET", "/api/state", {"error": "valid bearer authentication is required"},
                     status=401)
    code, out, err, _ = run(dashboard, capsys, "status")
    assert code == 1
    assert out == ""
    assert "refused the admin token" in err
    assert "Traceback" not in err


def test_status_routing_between_the_dashboard_and_the_pi(tmp_path):
    config = tmp_path / "home" / ".config" / "jittersim" / "cli.json"
    assert remote_status_wanted([], {"HOME": str(tmp_path / "home")}) is False
    assert remote_status_wanted(["--json"], {"HOME": str(tmp_path / "home")}) is True
    assert remote_status_wanted([], {"HOME": "/nowhere", "JITTERSIM_URL": "https://pi:8080"})
    config.parent.mkdir(parents=True)
    config.write_text("{}")
    assert remote_status_wanted([], {"HOME": str(tmp_path / "home")}) is True
    assert remote_status_wanted(["--database", "/x"], {"HOME": str(tmp_path / "home")}) is False
    assert remote_status_wanted(["--helper-socket=/x"], {"JITTERSIM_URL": "https://pi"}) is False


def test_local_status_stays_the_pi_command_without_a_saved_connection(tmp_path, capsys):
    with patch("jittersim.cli.HelperClient") as helper:
        helper.return_value.ping.return_value = {"ok": True}
        helper.return_value.gateway_status.return_value = {"gateway_state": "available"}
        code = main(["status", "--database", str(tmp_path / "missing.db")])
    output = capsys.readouterr()
    assert code == 0
    assert "Database: not readable" in output.out
    assert "jittersim connect" in output.err


@pytest.mark.parametrize(
    "release, line",
    [
        ({"installed": "abc1234", "available": "def5678", "phase": "ready"},
         "Release on the Pi: abc1234. Staged and ready to install: def5678"),
        ({"installed": "abc1234", "available": "abc1234", "phase": "ready"},
         "Release on the Pi: abc1234"),
        ({"phase": "unavailable", "message": "Release controls require the Pi network helper."},
         "Release on the Pi: unknown (Release controls require the Pi network helper.)"),
        (None, "Release on the Pi: unknown (The dashboard does not report its release.)"),
    ],
)
def test_status_names_the_release_the_pi_runs(dashboard, capsys, release, line):
    serve_status(dashboard, release=release)
    code, out, _, _ = run(dashboard, capsys, "status")
    assert code == 0
    assert out.splitlines()[1] == line
    assert ("GET", "/api/updates") in dashboard.paths()


def test_status_json_names_a_staged_release(dashboard, capsys):
    serve_status(dashboard, release={"installed": "abc1234", "available": "def5678"})
    code, out, _, _ = run(dashboard, capsys, "status", "--json")
    assert code == 0
    assert json.loads(out)["release"] == {"installed": "abc1234", "staged": "def5678"}
