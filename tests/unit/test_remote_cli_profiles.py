"""jittersim profiles list, apply-all and stop-all against a disposable HTTPS dashboard."""

import json

import pytest

from tests.remote_support import (
    PROFILES,
    run,
    state,
)

pytestmark = pytest.mark.usefixtures("remote_environment")


# profiles ----------------------------------------------------------------------------------


def test_profiles_list_shows_summaries_and_the_general_profile(dashboard, capsys):
    dashboard.answer("GET", "/api/profiles", {"profiles": PROFILES})
    dashboard.answer("GET", "/api/general-profile", {
        "profile": PROFILES[0], "active": True, "state": state()["state"]})
    code, out, _, _ = run(dashboard, capsys, "profiles", "list")
    assert code == 0
    assert out.splitlines() == [
        "Slow connection (slow)  General · active",
        "  ↕ 50 ms ±10 · 2% loss · 5 Mbit/s",
        "Lossy (lossy)",
        "  ↑ 10% loss · ↓ 300 ms · 640 kbit/s · outages 5% · allow-only 1 site",
    ]


def test_profiles_list_json(dashboard, capsys):
    dashboard.answer("GET", "/api/profiles", {"profiles": PROFILES})
    dashboard.answer("GET", "/api/general-profile", {
        "profile": PROFILES[1], "active": False, "state": state("reset", False)["state"]})
    code, out, _, _ = run(dashboard, capsys, "profiles", "list", "--json")
    assert code == 0
    data = json.loads(out)
    assert data["general_profile_id"] == "lossy"
    assert data["general_badge"] == "General · paused"
    assert data["profiles"][0] == {"id": "slow", "name": "Slow connection",
                                   "summary": "↕ 50 ms ±10 · 2% loss · 5 Mbit/s", "general": False}


def test_apply_all_posts_the_profile_and_says_protected_devices_stay(dashboard, capsys):
    dashboard.answer("POST", "/api/general-profile", {
        "applied": {}, "state": state()["state"], "warning": None,
        "general_profile": {"profile": PROFILES[0], "active": True, "state": state()["state"]}})
    code, out, _, _ = run(dashboard, capsys, "profiles", "apply-all", "slow")
    assert code == 0
    assert dashboard.requests[0]["body"] == {"profile_id": "slow"}
    assert "General profile applied. Protected devices remain unaffected." in out
    assert "General profile: Slow connection · active" in out


def test_apply_all_shows_the_dashboard_failure(dashboard, capsys):
    dashboard.answer("POST", "/api/general-profile", {
        "error": "Selection saved but the network update failed. network helper apply failed",
        "hint": "On the Pi, run sudo journalctl -u jittersim-helper.service -n 80 --no-pager."},
        status=502)
    code, out, err, _ = run(dashboard, capsys, "profiles", "apply-all", "slow")
    assert code == 1
    assert out == ""
    assert "Selection saved but the network update failed" in err
    assert "journalctl" in err


def test_stop_all_posts_nothing_and_says_protected_devices_stay(dashboard, capsys):
    dashboard.answer("POST", "/api/general-profile/stop", {
        "applied": {}, "state": state()["state"],
        "general_profile": {"profile": None, "active": False}})
    code, out, _, _ = run(dashboard, capsys, "profiles", "stop-all")
    assert code == 0
    assert dashboard.requests[0]["body"] == {}
    assert out.splitlines() == [
        "General profile stopped. Saved device rules resumed.",
        "Protected devices stay untouched.",
    ]


def test_stop_all_without_a_general_profile_changes_nothing(dashboard, capsys):
    dashboard.answer("POST", "/api/general-profile/stop", {
        "general_profile": {"profile": None, "active": False}})
    code, out, _, _ = run(dashboard, capsys, "profiles", "stop-all", "--json")
    assert code == 0
    assert json.loads(out)["message"] == (
        "No general profile was set. Nothing changed. Protected devices stay untouched."
    )
