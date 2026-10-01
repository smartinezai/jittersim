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


RENDER_HARNESS = r"""
const nodes = new Map();
function makeNode(tag) {
  return {
    tag, children: [], textContent: "", className: "", hidden: false, classes: new Set(),
    classList: {toggle(name, on) {on ? this.owner.classes.add(name) : this.owner.classes.delete(name)}},
    appendChild(child) {this.children.push(child)},
    replaceChildren(...children) {this.children = children}
  };
}
const document = {createElement: makeNode};
function element(id) {
  if (!nodes.has(id)) {
    const node = makeNode("div");
    node.classList.owner = node;
    nodes.set(id, node);
  }
  return nodes.get(id);
}
let adminTokenVerified = true;
let lastRuntimeState = {observed_state: "applied", running: true};
let generalProfileState = {profile: {id: "slow", name: "Shared slow"}, active: true,
  state: {observed_state: "applied"}};
let profiles = [];
let connectedClients = [];
let serviceListsState = [];
let savedDevices = [];
let groups = [];
let unaffectedMacs = [];
let applyBusy = false;
const listed = () => element("status-devices-list").children.map(
  (row) => row.children.map((part) => part.textContent));
"""


def render(setup):
    program = RENDER_HARNESS + served_function() + "\n" + setup
    result = subprocess.run([NODE, "-"], input=program, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_block_fills_in_every_line_with_plain_text():
    out = render(
        """
        connectedClients = [
          {display_name: "<b>Laptop</b>", mac_address: "m1", unaffected: false},
          {display_name: "Protected phone", mac_address: "m2", unaffected: true},
        ];
        serviceListsState = [{name: "Video", enabled: true}];
        renderSidebarStatus();
        console.log(JSON.stringify({
          hidden: element("sidebar-status").hidden,
          general: element("status-general").textContent,
          generalClass: element("status-general").className,
          summary: element("status-devices-summary").textContent,
          items: listed(),
          moreHidden: element("status-devices-more").hidden,
          emptyClass: element("status-devices").classes.has("is-empty"),
          lists: element("status-lists").textContent,
          listsHidden: element("status-lists").hidden,
        }));
        """
    )
    assert out == {
        "hidden": False,
        "general": "General profile: Shared slow · active",
        "generalClass": "status-line status-on",
        "summary": "1 device affected",
        "items": [["<b>Laptop</b>", "Shared slow"]],
        "moreHidden": True,
        "emptyClass": False,
        "lists": "Service lists: Video (every device)",
        "listsHidden": False,
    }


def test_the_more_line_and_the_quiet_states():
    out = render(
        """
        connectedClients = Array.from({length: 7}, (_, i) => ({display_name: "D" + i,
          mac_address: "m" + i}));
        renderSidebarStatus();
        const crowded = {items: listed().length, more: element("status-devices-more").textContent,
          moreHidden: element("status-devices-more").hidden};
        lastRuntimeState = {observed_state: "reset", running: false};
        generalProfileState = {profile: {id: "slow", name: "Shared slow"}, active: false,
          state: {observed_state: "reset"}};
        renderSidebarStatus();
        console.log(JSON.stringify({
          crowded,
          summary: element("status-devices-summary").textContent,
          items: listed().length,
          more: element("status-devices-more").hidden,
          emptyClass: element("status-devices").classes.has("is-empty"),
          general: element("status-general").textContent,
          generalClass: element("status-general").className,
        }));
        """
    )
    assert out["crowded"] == {"items": 5, "more": "+2 more", "moreHidden": False}
    assert out["summary"] == "Profiles off"
    assert out["items"] == 0
    assert out["more"] is True
    assert out["emptyClass"] is True
    assert out["general"] == "General profile: Shared slow · paused"
    assert out["generalClass"] == "status-line"


def test_a_locked_dashboard_hides_the_block_and_removes_names():
    out = render(
        """
        connectedClients = [{display_name: "Laptop", mac_address: "m1"}];
        renderSidebarStatus();
        const before = listed().length;
        adminTokenVerified = false;
        renderSidebarStatus();
        console.log(JSON.stringify({
          before,
          hidden: element("sidebar-status").hidden,
          general: element("status-general").textContent,
          summary: element("status-devices-summary").textContent,
          items: listed().length,
          lists: element("status-lists").textContent,
        }));
        """
    )
    assert out == {"before": 1, "hidden": True, "general": "", "summary": "", "items": 0,
                   "lists": ""}


def test_the_status_code_never_writes_html():
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
    assert "innerHTML" not in served_function()
    assert "insertAdjacentHTML" not in served_function()
    assert "function renderSidebarStatus(" in script


def test_the_block_sits_between_the_navigation_and_the_footer():
    nav_end = DASHBOARD_HTML.index("</nav>")
    block = DASHBOARD_HTML.index('<section id="sidebar-status"')
    footer = DASHBOARD_HTML.index('<div class="sidebar-footer">')
    assert nav_end < block < footer
    assert re.search(r'<section id="sidebar-status"[^>]*\shidden>', DASHBOARD_HTML)
    assert DASHBOARD_HTML.count('id="sidebar-status"') == 1


def test_every_input_of_the_block_calls_the_renderer():
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)

    def body(start, end):
        begin = script.index(start)
        return script[begin : script.index(end, begin)]

    assert "lastRuntimeState = state;" in body(
        "function renderRuntimeSummary(", "function connectedRowState("
    )
    calls = [
        ("function renderRuntimeSummary(", "function connectedRowState("),
        ("function renderConnectedDevices(", "function renderSavedConnectedDeviceLabels("),
        ("async function loadProfiles(", "async function loadGeneralProfile("),
        ("async function loadGeneralProfile(", "async function loadDevicesForSchedule("),
        ("function renderServiceLists(", "async function loadServiceLists("),
        ("function setPrivateDataPanels(", "async function loadPrivateDataPanel("),
    ]
    for start, end in calls:
        assert "renderSidebarStatus();" in body(start, end), start
    assert "serviceListsState = lists;" in body(*calls[4])
