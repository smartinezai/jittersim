"""jittersim traffic, scan, block and unblock against a disposable HTTPS dashboard."""

import json

import pytest

from tests.remote_support import (
    run,
)

pytestmark = pytest.mark.usefixtures("remote_environment")


# traffic -----------------------------------------------------------------------------------


def observation(domain, count, *, name, kind, category, group=None, seen_with=(), blocked=False):
    return {
        "domain": domain, "query_count": count, "first_seen": 1, "last_seen": 2,
        "label": {"name": name, "kind": kind, "category": category, "known": kind != "unknown"},
        "group": group or {"key": "domain:" + domain, "name": domain, "kind": kind},
        "seen_with": list(seen_with), "globally_blocked": blocked,
    }


NETFLIX = {"key": "label:netflix", "name": "Netflix", "kind": "app"}
OBSERVATIONS = {
    "available": True,
    "total_domains": 4,
    "message": None,
    "devices": [
        {
            "ipv4_address": "192.168.50.124", "display_name": "Kitchen iPad", "hostname": None,
            "assigned_device_id": None, "assigned_device_name": None, "assigned_profile_id": None,
            "domains": [
                observation("logs.dradis.netflix.com", 3, name="Netflix", kind="app",
                            category="Streaming", group=NETFLIX),
                observation("ipv4-c1.oca.nflxvideo.net", 40, name="Netflix", kind="app",
                            category="Streaming", group=NETFLIX, blocked=True),
                observation("a1051.dscg4.akamai.net", 3, name="Akamai", kind="cdn",
                            category="Shared CDN", seen_with=["Netflix"]),
            ],
        },
        {
            "ipv4_address": "192.168.50.184", "display_name": "", "hostname": None,
            "assigned_device_id": None, "assigned_device_name": None, "assigned_profile_id": None,
            "domains": [
                observation("10000.mt.eu.iot.dreame.tech", 7, name="Dreame", kind="iot",
                            category="Robot vacuum"),
            ],
        },
    ],
}


def test_traffic_shows_groups_labels_guesses_and_blocked_names(dashboard, capsys):
    dashboard.answer("GET", "/api/observations", OBSERVATIONS)
    code, out, _, _ = run(dashboard, capsys, "traffic")
    assert code == 0
    lines = out.splitlines()
    assert "Labels come from a built-in table. 'Seen with' is a guess." in lines[0]
    assert "Kitchen iPad  (192.168.50.124)" in lines
    assert "  Netflix  [app]  2 names  43 queries  1 blocked" in lines
    assert ("  a1051.dscg4.akamai.net  [CDN] Akamai · Shared CDN  3 queries  "
            "Seen with Netflix (guess)") in lines
    assert "192.168.50.184  (192.168.50.184)" in lines
    assert "  10000.mt.eu.iot.dreame.tech  [smart-home] Dreame · Robot vacuum  7 queries" in lines
    assert not any("logs.dradis" in line for line in lines)
    assert "Add --expand to list every name of a group." in lines


def test_traffic_expand_lists_every_name_and_marks_blocked(dashboard, capsys):
    dashboard.answer("GET", "/api/observations", OBSERVATIONS)
    code, out, _, _ = run(dashboard, capsys, "traffic", "--expand", "--device", "kitchen ipad")
    assert code == 0
    lines = out.splitlines()
    assert "      ipv4-c1.oca.nflxvideo.net  40 queries  Blocked" in lines
    assert "      logs.dradis.netflix.com  3 queries" in lines
    assert not any("dreame" in line for line in lines)


def test_traffic_blocked_only(dashboard, capsys):
    dashboard.answer("GET", "/api/observations", OBSERVATIONS)
    code, out, _, _ = run(dashboard, capsys, "traffic", "--blocked")
    assert code == 0
    assert "  ipv4-c1.oca.nflxvideo.net  [app] Netflix · Streaming  40 queries  Blocked" in out
    assert "akamai" not in out and "dreame" not in out


def test_traffic_unknown_device_is_a_failure(dashboard, capsys):
    dashboard.answer("GET", "/api/observations", OBSERVATIONS)
    code, _, err, _ = run(dashboard, capsys, "traffic", "--device", "192.168.50.9")
    assert code == 1
    assert "No device named or at 192.168.50.9" in err


def test_traffic_json_has_every_name_and_marks_the_guess(dashboard, capsys):
    dashboard.answer("GET", "/api/observations", OBSERVATIONS)
    code, out, _, _ = run(dashboard, capsys, "traffic", "--json")
    assert code == 0
    data = json.loads(out)
    assert data["scan"] is None
    netflix = data["devices"][0]["groups"][0]
    assert (netflix["name"], netflix["kind"], netflix["names_count"], netflix["total_queries"],
            netflix["blocked_text"]) == ("Netflix", "app", 2, 43, "1 blocked")
    assert [row["domain"] for row in netflix["names"]] == [
        "logs.dradis.netflix.com", "ipv4-c1.oca.nflxvideo.net"]
    akamai = data["devices"][0]["groups"][1]["names"][0]
    assert akamai["seen_with"] == ["Netflix"]
    assert akamai["seen_with_text"] == "Seen with Netflix (guess)"
    assert akamai["kind"] == "CDN"
    assert "guess" in data["note"]


def test_traffic_scan_first_then_lists(dashboard, capsys):
    dashboard.answer("POST", "/api/observations/scan", {"captured_observations": 4,
                                                         "observations": []})
    dashboard.answer("GET", "/api/observations", OBSERVATIONS)
    code, out, err, _ = run(dashboard, capsys, "traffic", "--scan")
    assert code == 0
    assert dashboard.paths() == [("POST", "/api/observations/scan"), ("GET", "/api/observations")]
    assert dashboard.requests[0]["body"] == {}
    assert "Traffic scan complete: 4 domain observations captured." in out
    assert "Sampling DNS queries" in err


def test_a_refused_scan_shows_the_dashboard_message(dashboard, capsys):
    message = ("Traffic can only be scanned while at most 16 devices are protected. "
               "Remove some from the Unaffected list.")
    dashboard.answer("POST", "/api/observations/scan", {"error": message}, status=409)
    code, out, err, _ = run(dashboard, capsys, "traffic", "--scan")
    assert code == 1
    assert message in err
    assert dashboard.paths() == [("POST", "/api/observations/scan")]


def test_traffic_unavailable_says_why(dashboard, capsys):
    dashboard.answer("GET", "/api/observations", {
        "available": False, "devices": [],
        "message": "traffic discovery is unavailable in configuration-only mode"})
    code, _, err, _ = run(dashboard, capsys, "traffic")
    assert code == 1
    assert "configuration-only mode" in err


@pytest.mark.parametrize(
    "word, path, listed",
    [("block", "/api/observations/add-global-block", ["tracker.example"]),
     ("unblock", "/api/observations/remove-global-block", [])],
)
def test_block_and_unblock_say_the_change_is_saved_and_applies_on_its_own(
        dashboard, capsys, word, path, listed):
    before = [] if word == "block" else ["tracker.example"]
    dashboard.answer("GET", "/api/policies", {"websites": {"blocked_domains": before}})
    dashboard.answer("POST", path, {"policies": {"websites": {"blocked_domains": listed}}})
    code, out, _, _ = run(dashboard, capsys, "traffic", word, "Tracker.Example.")
    assert code == 0
    assert dashboard.paths() == [("GET", "/api/policies"), ("POST", path)]
    assert dashboard.requests[1]["body"] == {"domain": "tracker.example"}
    assert out.startswith("Saved: ")
    assert "It applies on its own while JitterSim is running." in out


def test_block_that_the_dashboard_did_not_confirm_fails(dashboard, capsys):
    dashboard.answer("GET", "/api/policies", {"websites": {"blocked_domains": []}})
    dashboard.answer("POST", "/api/observations/add-global-block",
                     {"policies": {"websites": {"blocked_domains": []}}})
    code, _, err, _ = run(dashboard, capsys, "traffic", "block", "tracker.example")
    assert code == 1
    assert "did not confirm" in err


def test_block_refuses_an_invalid_name_before_the_prompt(dashboard, capsys):
    code, _, err, prompt = run(dashboard, capsys, "traffic", "block", "bad name!", prompts=())
    assert code == 2
    prompt.assert_not_called()
    assert dashboard.requests == []


def test_the_saved_note_is_the_page_note_word_for_word():
    import re

    from jittersim.api.server import DASHBOARD_HTML
    from jittersim.remote_cli import ENFORCE_NOTE

    declaration = re.search(r"const ENFORCE_NOTE = (.*?);", DASHBOARD_HTML, re.S).group(1)
    page_note = "".join(json.loads(part) for part in re.findall(r'"(?:[^"\\]|\\.)*"', declaration))
    assert ENFORCE_NOTE == page_note


def test_block_help_says_names_under_a_blocked_name_are_blocked_too(capsys):
    from jittersim.cli import main

    with pytest.raises(SystemExit):
        main(["traffic", "block", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "example.com also blocks www.example.com" in text
