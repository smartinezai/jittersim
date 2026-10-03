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
    return script[start : script.index("function renderSidebarStatus(", start)]


ALL_LOADED = {"runtime": True, "general": True, "clients": True, "profiles": True, "lists": True}

BASE = {
    "adminTokenVerified": True,
    "loaded": ALL_LOADED,
    "runtimeState": "applied",
    "running": True,
    "generalState": None,
    "profiles": [],
    "clients": [],
    "clientsAvailable": True,
    "serviceLists": [],
}


def run_function(data):
    program = served_function() + "\nconsole.log(JSON.stringify(sidebarStatus(%s)));" % json.dumps(
        data
    )
    result = subprocess.run([NODE, "-"], input=program, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def status(**overrides):
    return run_function({**BASE, **overrides})


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
        "loading": False,
        "general": {"text": "Everyone: none", "tone": "muted"},
        "devices": {"summary": "Not running", "items": [], "more": 0},
        "lists": {"text": ""},
    }


@pytest.mark.parametrize("missing", sorted(ALL_LOADED))
def test_a_single_loading_line_shows_while_any_input_is_not_loaded(missing):
    result = status(
        loaded={**ALL_LOADED, missing: False},
        generalState=general(active=True),
        clients=[client("Laptop", "02:00:00:00:00:01")],
        serviceLists=[{"name": "Video", "enabled": True}],
    )
    assert result == {
        "visible": True,
        "loading": True,
        "general": {"text": "Loading…", "tone": "muted"},
        "devices": {"summary": "", "items": [], "more": 0},
        "lists": {"text": ""},
    }


def test_nothing_is_loaded_before_the_page_says_so():
    assert status(loaded={})["loading"] is True
    assert status(loaded=None)["loading"] is True


def test_a_locked_dashboard_stays_hidden_while_loading():
    result = status(adminTokenVerified=False, loaded={})
    assert result["visible"] is False


@pytest.mark.parametrize(
    "state", [None, {"profile": None, "active": False, "state": {"observed_state": "applied"}}]
)
def test_no_general_profile(state):
    assert status(generalState=state)["general"] == {
        "text": "Everyone: none",
        "tone": "muted",
    }


# running, runtime state, what the server says about active, expected wording and tone
GENERAL_LINE = [
    (True, "applied", True, "active", "on"),
    (True, "applied", False, "saved, not applied", "muted"),
    # A saved change leaves the state unknown for about a second while the scheduler runs.
    (True, "unknown", False, "active", "on"),
    (True, "unknown", True, "active", "on"),
    # The server data can be older than the runtime state so the state wins.
    (True, "reset", True, "paused", "muted"),
    (True, "error", True, "saved, not applied", "muted"),
    (True, "applying", True, "saved, not applied", "muted"),
    (True, "resetting", True, "saved, not applied", "muted"),
    (False, "applied", True, "saved, not applied", "muted"),
    (False, "unknown", True, "saved, not applied", "muted"),
    (False, "unknown", False, "saved, not applied", "muted"),
    (False, "reset", False, "paused", "muted"),
    (False, "reset", True, "paused", "muted"),
    (False, "error", False, "saved, not applied", "muted"),
]


@pytest.mark.parametrize("running,state,active,wording,tone", GENERAL_LINE)
def test_general_profile_wording_follows_the_runtime_state(running, state, active, wording, tone):
    result = status(
        running=running, runtimeState=state, generalState=general(active=active, observed=state)
    )
    assert result["general"] == {
        "text": "Everyone: Shared slow · " + wording,
        "tone": tone,
    }


def test_a_saved_change_does_not_make_the_general_profile_look_unapplied():
    result = status(
        generalState=general(active=False, observed="unknown"),
        runtimeState="unknown",
        running=True,
        clients=[client("Laptop", "02:00:00:00:00:01")],
    )
    assert result["general"] == {"text": "Everyone: Shared slow · active", "tone": "on"}
    assert result["devices"]["items"] == [{"name": "Laptop", "profile": "Shared slow"}]


@pytest.mark.parametrize(
    "state,summary",
    [
        ("reset", "Profiles off"),
        ("unknown", "Not running"),
        (None, "Not running"),
        ("applied", "Not running"),
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


@pytest.mark.parametrize("running", [False, True])
def test_a_failed_reset_says_the_pi_may_still_be_shaping(running):
    # The block must not suggest that nothing is affected after a reset that failed.
    result = status(
        runtimeState="error",
        lastAction="reset",
        running=running,
        generalState=general(active=True),
        clients=[client("Laptop", "02:00:00:00:00:01", assigned_device_id="laptop")],
    )
    assert result["devices"] == {
        "summary": "Last reset failed. The Pi may still be shaping.", "items": [], "more": 0
    }


@pytest.mark.parametrize("last_action", ["apply", "schedule", None])
def test_another_failed_action_keeps_the_general_wording(last_action):
    result = status(runtimeState="error", lastAction=last_action, running=False)
    assert result["devices"]["summary"] == "Last network action failed"


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


# Without a general profile and with clients that the page knows about

def test_no_device_has_its_own_profile_when_nobody_connected_is_assigned():
    result = status(
        generalState=None,
        clients=[client("Laptop", "02:00:00:00:00:01"), client("Phone", "02:00:00:00:00:02")],
        serviceLists=[{"name": "Video", "enabled": True}],
    )
    assert result["devices"] == {"summary": "No device has its own profile", "items": [], "more": 0}
    assert result["lists"]["text"] == "Service lists: Video (every unprotected device)"


def test_assigned_clients_that_are_all_protected_are_not_affected():
    mixed = status(
        generalState=None,
        clients=[
            client("Protected", "02:00:00:00:00:01", assigned_device_id="a",
                   assigned_profile_id="p", unaffected=True),
            client("Guest", "02:00:00:00:00:02"),
        ],
    )
    only = status(
        generalState=None,
        clients=[
            client("Protected", "02:00:00:00:00:01", assigned_device_id="a",
                   assigned_profile_id="p", unaffected=True),
        ],
    )
    assert mixed["devices"]["summary"] == "No connected device is affected"
    assert only["devices"]["summary"] == "No connected device is affected"


def test_nobody_connected_is_not_affected_rather_than_unassigned():
    assert status(generalState=None, clients=[])["devices"]["summary"] == (
        "No connected device is affected"
    )


def test_a_general_profile_never_says_no_device_has_its_own_profile():
    result = status(
        generalState=general(active=True),
        clients=[client("Protected", "02:00:00:00:00:01", unaffected=True)],
    )
    assert result["devices"]["summary"] == "No connected device is affected"


# The device list can be unavailable for two different reasons. The page works out which.

@pytest.mark.parametrize("general_state", [None, general(active=True)])
def test_an_unavailable_device_list_is_not_reported_as_no_affected_devices(general_state):
    result = status(
        generalState=general_state,
        clientsAvailable=False,
        profiles=[{"id": "slow", "name": "Slow network"}],
        clients=[
            client("Laptop", "02:00:00:00:00:01", assigned_device_id="laptop",
                   assigned_profile_id="slow", association_verified=False),
        ],
        serviceLists=[{"name": "Video", "enabled": True}],
    )
    assert result["devices"] == {
        "summary": "Connected devices unavailable",
        "items": [],
        "more": 0,
    }
    assert "Laptop" not in json.dumps(result)
    # The lines that do not depend on the device list keep working.
    assert result["general"]["text"].startswith(
        "Everyone: " + ("Shared slow" if general_state else "none")
    )
    assert result["lists"]["text"] == "Service lists: Video (every unprotected device)"


@pytest.mark.parametrize(
    "state,summary",
    [("reset", "Profiles off"), ("applying", "Changing…"), ("error", "Last network action failed")],
)
def test_the_other_summaries_do_not_depend_on_the_device_list(state, summary):
    assert status(runtimeState=state, clientsAvailable=False)["devices"]["summary"] == summary
    assert status(running=False, clientsAvailable=False)["devices"]["summary"] == (
        "Not running"
    )


def test_the_page_must_say_the_device_list_is_available():
    data = {key: value for key, value in BASE.items() if key != "clientsAvailable"}
    assert run_function(data)["devices"]["summary"] == "Connected devices unavailable"


def test_service_lists_line_only_while_running_and_enabled():
    lists = [
        {"name": "Video", "enabled": True},
        {"name": "Games", "enabled": False},
        {"name": "Chat", "enabled": True},
    ]
    assert status(serviceLists=lists)["lists"] == {
        "text": "Service lists: Video · Chat (every unprotected device)"
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
    assert result["general"]["text"] == "Everyone: <b>Bad</b> · active"
    assert result["devices"]["items"][0]["name"] == "<img src=x onerror=alert(1)>"


def test_the_status_code_never_writes_html():
    assert "innerHTML" not in served_function()
    assert "insertAdjacentHTML" not in served_function()


def test_a_client_whose_saved_profile_is_paused_is_never_listed_as_affected():
    result = status(
        generalState=None,
        profiles=[{"id": "slow", "name": "Slow network"}],
        clients=[
            client("Laptop", "02:00:00:00:00:01", assigned_device_id="laptop",
                   assigned_profile_id="slow", assigned_effective_state="active"),
            client("Unchecked phone", "02:00:00:00:00:02", assigned_device_id="phone",
                   assigned_profile_id="slow", assigned_effective_state="unverified"),
            client("Protected tablet", "02:00:00:00:00:03", assigned_device_id="tablet",
                   assigned_profile_id="slow", assigned_effective_state="protected"),
        ],
    )
    assert result["devices"]["summary"] == "1 device affected"
    assert result["devices"]["items"] == [{"name": "Laptop", "profile": "Slow network"}]
    assert "Unchecked phone" not in json.dumps(result)
    assert "Protected tablet" not in json.dumps(result)
