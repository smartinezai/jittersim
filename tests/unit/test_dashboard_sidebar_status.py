"""Exercise the sidebar status function using the dashboard served to the browser."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required to run the dashboard")


def served_function():
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
    start = script.index("function sidebarStatus(")
    return script[start : script.index("function renderRuntimeSummary(", start)]


BASE = {
    "adminTokenVerified": True,
    "runtimeState": "applied",
    "running": True,
    "generalState": None,
    "profiles": [],
    "clients": [],
    "serviceLists": [],
}


def status(**overrides):
    data = {**BASE, **overrides}
    program = served_function() + "\nconsole.log(JSON.stringify(sidebarStatus(%s)));" % json.dumps(
        data
    )
    result = subprocess.run([NODE, "-"], input=program, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def general(name="Shared slow", active=False, observed="applied"):
    return {
        "profile": {"id": "slow", "name": name},
        "active": active,
        "state": {"observed_state": observed},
    }


def client(name, mac, **fields):
    return {
        "display_name": name,
        "mac_address": mac,
        "unaffected": False,
        "association_verified": True,
        "assigned_device_id": None,
        "assigned_profile_id": None,
        "group_profile_id": None,
        **fields,
    }


def test_the_block_is_hidden_until_the_admin_token_is_verified():
    assert status(adminTokenVerified=False)["visible"] is False
    assert status(adminTokenVerified=True)["visible"] is True


def test_empty_input_gives_quiet_lines():
    result = status(runtimeState=None, running=False)
    assert result == {
        "visible": True,
        "general": {"text": "General profile: none", "tone": "muted"},
        "devices": {"summary": "Not started yet", "items": [], "more": 0},
        "lists": {"text": ""},
    }


@pytest.mark.parametrize(
    "state", [None, {"profile": None, "active": False, "state": {"observed_state": "applied"}}]
)
def test_no_general_profile(state):
    assert status(generalState=state)["general"] == {
        "text": "General profile: none",
        "tone": "muted",
    }


def test_active_general_profile_is_marked_on():
    result = status(generalState=general(active=True))
    assert result["general"] == {"text": "General profile: Shared slow · active", "tone": "on"}


def test_general_profile_paused_after_a_reset():
    result = status(
        generalState=general(observed="reset"), runtimeState="reset", running=False
    )
    assert result["general"] == {"text": "General profile: Shared slow · paused", "tone": "muted"}


@pytest.mark.parametrize("observed", ["unknown", "applied", "error", "applying"])
def test_general_profile_saved_but_not_applied(observed):
    result = status(generalState=general(observed=observed), runtimeState=observed, running=False)
    assert result["general"] == {
        "text": "General profile: Shared slow · saved, not applied",
        "tone": "muted",
    }


def test_a_saved_change_does_not_make_the_general_profile_look_unapplied():
    # The server reports the general profile as inactive while the observed state is unknown.
    # That lasts about a second after every saved change while the scheduler keeps running.
    result = status(
        generalState=general(active=False, observed="unknown"),
        runtimeState="unknown",
        running=True,
        clients=[client("Laptop", "02:00:00:00:00:01")],
    )
    assert result["general"] == {"text": "General profile: Shared slow · active", "tone": "on"}
    assert result["devices"]["items"] == [{"name": "Laptop", "profile": "Shared slow"}]


@pytest.mark.parametrize(
    "state,summary",
    [
        ("reset", "Profiles off"),
        ("unknown", "Not started yet"),
        (None, "Not started yet"),
        ("applied", "Not started yet"),
        ("applying", "Changing…"),
        ("resetting", "Changing…"),
        ("error", "Last network action failed"),
    ],
)
def test_summary_when_the_scheduler_is_not_running(state, summary):
    result = status(
        runtimeState=state,
        running=False,
        generalState=general(active=True),
        clients=[client("Laptop", "02:00:00:00:00:01", assigned_device_id="laptop")],
    )
    assert result["devices"] == {"summary": summary, "items": [], "more": 0}
    assert result["lists"] == {"text": ""}


@pytest.mark.parametrize(
    "state,summary",
    [
        ("applying", "Changing…"),
        ("resetting", "Changing…"),
        ("error", "Last network action failed"),
        ("reset", "Profiles off"),
    ],
)
def test_summary_while_running_but_not_in_a_steady_state(state, summary):
    result = status(
        runtimeState=state,
        running=True,
        generalState=general(active=True),
        clients=[client("Laptop", "02:00:00:00:00:01")],
        serviceLists=[{"name": "Video", "enabled": True}],
    )
    assert result["devices"] == {"summary": summary, "items": [], "more": 0}
    assert result["lists"] == {"text": ""}


@pytest.mark.parametrize("state", ["applied", "unknown"])
def test_general_profile_lists_connected_unprotected_verified_clients(state):
    result = status(
        runtimeState=state,
        generalState=general(active=True),
        clients=[
            client("Laptop", "02:00:00:00:00:01"),
            client("Protected phone", "02:00:00:00:00:02", unaffected=True),
            client("Unverified tv", "02:00:00:00:00:03", association_verified=False),
            client("", "02:00:00:00:00:04"),
            client("", ""),
        ],
    )
    assert result["devices"]["summary"] == "3 devices affected"
    assert result["devices"]["items"] == [
        {"name": "Laptop", "profile": "Shared slow"},
        {"name": "02:00:00:00:00:04", "profile": "Shared slow"},
        {"name": "Unknown device", "profile": "Shared slow"},
    ]
    assert result["devices"]["more"] == 0
    assert "Protected phone" not in json.dumps(result)
    assert "Unverified tv" not in json.dumps(result)


def test_without_a_general_profile_lists_assigned_clients_with_their_profile():
    result = status(
        generalState=None,
        profiles=[
            {"id": "slow", "name": "Slow network"},
            {"id": "lossy", "name": "Lossy network"},
        ],
        clients=[
            client("Laptop", "02:00:00:00:00:01", assigned_device_id="laptop",
                   assigned_profile_id="slow"),
            client("Grouped phone", "02:00:00:00:00:02", assigned_device_id="phone",
                   assigned_profile_id="slow", group_profile_id="lossy"),
            client("Deleted profile", "02:00:00:00:00:03", assigned_device_id="old",
                   assigned_profile_id="gone"),
            client("Protected tablet", "02:00:00:00:00:04", assigned_device_id="tablet",
                   assigned_profile_id="slow", unaffected=True),
            client("Not assigned", "02:00:00:00:00:05"),
        ],
    )
    assert result["devices"]["summary"] == "3 devices affected"
    assert result["devices"]["items"] == [
        {"name": "Laptop", "profile": "Slow network"},
        {"name": "Grouped phone", "profile": "Lossy network"},
        {"name": "Deleted profile", "profile": "gone"},
    ]
    assert "Protected tablet" not in json.dumps(result)
    assert "Not assigned" not in json.dumps(result)


def test_a_saved_general_profile_that_is_not_applied_falls_back_to_assigned_clients():
    # Observed applied but the scheduler does not carry the general queue.
    result = status(
        generalState=general(active=False, observed="applied"),
        profiles=[{"id": "slow", "name": "Slow network"}],
        clients=[
            client("Laptop", "02:00:00:00:00:01", assigned_device_id="laptop",
                   assigned_profile_id="slow"),
            client("Guest", "02:00:00:00:00:02"),
        ],
    )
    assert result["devices"]["items"] == [{"name": "Laptop", "profile": "Slow network"}]


def test_more_than_five_clients_are_counted_but_only_five_are_listed():
    clients = [client("Device %d" % number, "02:00:00:00:00:%02d" % number) for number in range(8)]
    result = status(generalState=general(active=True), clients=clients)
    assert result["devices"]["summary"] == "8 devices affected"
    assert [item["name"] for item in result["devices"]["items"]] == [
        "Device 0", "Device 1", "Device 2", "Device 3", "Device 4",
    ]
    assert result["devices"]["more"] == 3


def test_exactly_five_clients_have_no_more_line():
    clients = [client("Device %d" % number, "02:00:00:00:00:%02d" % number) for number in range(5)]
    result = status(generalState=general(active=True), clients=clients)
    assert len(result["devices"]["items"]) == 5
    assert result["devices"]["more"] == 0


def test_singular_and_empty_summaries():
    one = status(
        generalState=general(active=True), clients=[client("Laptop", "02:00:00:00:00:01")]
    )
    none = status(generalState=general(active=True), clients=[])
    only_protected = status(
        generalState=general(active=True),
        clients=[client("Protected", "02:00:00:00:00:01", unaffected=True)],
    )
    assert one["devices"]["summary"] == "1 device affected"
    assert none["devices"] == {
        "summary": "No connected device is affected",
        "items": [],
        "more": 0,
    }
    assert only_protected["devices"]["summary"] == "No connected device is affected"
    assert only_protected["devices"]["items"] == []


def test_service_lists_line_only_while_running_and_enabled():
    lists = [
        {"name": "Video", "enabled": True},
        {"name": "Games", "enabled": False},
        {"name": "Chat", "enabled": True},
    ]
    assert status(serviceLists=lists)["lists"] == {
        "text": "Service lists: Video · Chat (every device)"
    }
    assert status(serviceLists=lists, runtimeState="unknown")["lists"]["text"].startswith(
        "Service lists: Video"
    )
    assert status(serviceLists=lists, running=False)["lists"] == {"text": ""}
    for state in ("reset", "applying", "resetting", "error"):
        assert status(serviceLists=lists, runtimeState=state)["lists"] == {"text": ""}
    assert status(serviceLists=[{"name": "Games", "enabled": False}])["lists"] == {"text": ""}
    assert status(serviceLists=[])["lists"] == {"text": ""}


def test_names_are_returned_as_plain_text_for_the_page_to_insert_safely():
    result = status(
        generalState=general(name="<b>Bad</b>", active=True),
        clients=[client("<img src=x onerror=alert(1)>", "02:00:00:00:00:01")],
    )
    assert result["general"]["text"] == "General profile: <b>Bad</b> · active"
    assert result["devices"]["items"][0]["name"] == "<img src=x onerror=alert(1)>"
