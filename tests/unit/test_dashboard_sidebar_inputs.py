"""Drive the real dashboard loaders in node and check what the sidebar block shows."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required to run the dashboard")
SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)


def between(start, end):
    first = SCRIPT.index(start)
    return SCRIPT[first : SCRIPT.index(end, first)]


PRELUDE = r"""
class FakeNode {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.style = {};
    this.className = "";
    this.textContent = "";
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.classes = new Set();
    this.classList = {
      add: (...names) => names.forEach((name) => this.classes.add(name)),
      remove: (...names) => names.forEach((name) => this.classes.delete(name)),
      contains: (name) => this.classes.has(name),
      toggle: (name, force) => {
        const on = force === undefined ? !this.classes.has(name) : force;
        if (on) this.classes.add(name); else this.classes.delete(name);
        return on;
      },
    };
  }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node); return node; }
  prepend(...nodes) { this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  contains(node) { return node === this; }
  matches() { return false; }
  focus() {}
  remove() {}
  scrollIntoView() {}
}
const store = {};
const element = (id) => (store[id] = store[id] || new FakeNode("div"));
const document = {
  createElement: (tag) => new FakeNode(tag), activeElement: null, hidden: false,
};
let helperConfigured = true;
let adminTokenVerified = true;
let adminTokenChecking = false;
let networkActionBusy = false;
let connectedDevicesRequestInFlight = false;
let profiles = [];
let generalProfileState = null;
let savedDevices = [];
let groups = [];
let connectedClients = [];
let lastConnectedData = null;
let connectedClientsAvailable = false;
let lastRuntimeState = null;
let serviceListsState = [];
let serviceListsLoaded = false;
let profilesLoaded = false;
let unaffectedMacs = null;
let accessRulesSaved = null;
let savedDevicesLoaded = false;
let groupsLoaded = false;
let helperKnown = true;
let applyBusy = false;
let serviceCatalogue = [];
let networkActionRevision = 0;
const connectedDrafts = new Map();
const clearConnectedDrafts = new Set();
const messages = [];
const requests = [];
let responses = {};
let request = async (path, method, body) => {
  requests.push(method ? method + " " + path : "GET " + path);
  const answer = responses[path];
  if (answer instanceof Error) throw answer;
  return typeof answer === "function" ? answer(body) : answer;
};
function showMessage(text, kind) { messages.push([text, kind]); }
function refreshAfterSuccess(message) { messages.push([message, "success"]); }
function revealElement() {}
function setButtonBusy() {}
function updateGeneralProfileControls() {}
function updateTrafficDeviceOptions() {}
function updateGroupMemberOptions() {}
function applyProfileToDevice() {}
function connectedControlAction() {}
function saveConnectedDeviceLabel() {}
function clearConnectedDeviceLabel() {}
function editServiceList() {}
function renderSavedConnectedDeviceLabels() {}
function renderSavedProfiles() {}
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return {promise, resolve};
};
const text = (node) => [node.textContent].concat(node.children.map(text)).join("|");
const block = () => ({
  hidden: element("sidebar-status").hidden,
  general: element("status-general").textContent,
  generalClass: element("status-general").className,
  plain: element("status-devices-plain").textContent,
  plainHidden: element("status-devices-plain").hidden,
  detailsHidden: element("status-devices").hidden,
  summary: element("status-devices-summary").textContent,
  items: element("status-devices-list").children.map(
    (row) => row.children.map((part) => part.textContent)),
  more: element("status-devices-more").hidden ? "" : element("status-devices-more").textContent,
  lists: element("status-lists").hidden ? "" : element("status-lists").textContent,
  everything: text(element("sidebar-status")) + ["status-general", "status-devices-plain",
    "status-devices-summary", "status-devices-list", "status-lists"].map(
    (id) => text(element(id))).join("#"),
});
"""

SIDEBAR = lambda: between("function sidebarStatus(", "function connectedRowState(")  # noqa: E731
CONNECTED = lambda: between(  # noqa: E731
    "function connectedRowState(", "function renderSavedConnectedDeviceLabels("
)
LOAD_CONNECTED = lambda: between(  # noqa: E731
    "async function loadConnectedDevices(", "async function checkForDevices("
)
SERVICE_LISTS = lambda: between("function renderServiceLists(", "function editServiceList(")  # noqa: E731
# loadGeneralProfile follows loadProfiles in the page so one slice holds both.
LOAD_PROFILES = lambda: between(  # noqa: E731
    "async function loadProfiles(", "async function loadDevicesForSchedule("
)
LOAD_STATE = lambda: between("async function loadState(", "function formatUtcDay(")  # noqa: E731
PRIVATE_PANELS = lambda: (  # noqa: E731
    between("const privateDataPanelIds =", "function setDashboardUnlocked(")
    + between("function setPrivateDataPanels(", "let helperConfigured = false;")
)
ACTIONS = lambda: between(  # noqa: E731
    "function setActionBusy(", 'element("apply-button").addEventListener'
)
POLL = lambda: between("function pollLiveStatus(", "window.setInterval(pollLiveStatus")  # noqa: E731


def run(body):
    program = (
        PRELUDE
        + PRIVATE_PANELS()
        + SIDEBAR()
        + CONNECTED()
        + LOAD_CONNECTED()
        + SERVICE_LISTS()
        + LOAD_PROFILES()
        + LOAD_STATE()
        + ACTIONS()
        + POLL()
        + "\n(async () => {\n"
        + body
        + "\n})().catch((error) => { console.error(error); process.exit(1); });\n"
    )
    result = subprocess.run(
        [NODE, "-"], input=program, text=True, capture_output=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def client(name, mac, **fields):
    return {
        "display_name": name, "mac_address": mac, "ipv4_address": "192.168.50.10",
        "unaffected": False, "association_verified": True, "assigned_device_id": None,
        "assigned_profile_id": None, "group_profile_id": None, "group_name": None,
        "device_type": "laptop", **fields,
    }


LAPTOP = client("Laptop", "02:00:00:00:00:01", assigned_device_id="laptop",
                assigned_profile_id="slow")
GOOD_CLIENTS = {"available": True, "association_status_available": True, "devices": [LAPTOP]}
STATE = {"observed_state": "applied", "running": True}
NO_GENERAL = {"profile": None, "active": False, "state": {"observed_state": "applied"}}
ACTIVE_GENERAL = {"profile": {"id": "g", "name": "Shared slow"}, "active": True,
                  "state": {"observed_state": "applied"}}
PROFILES = {"profiles": [{"id": "slow", "name": "Slow network"}]}
LISTS = {"service_lists": [
    {"id": "v", "name": "Video", "enabled": True, "services": [], "hostnames": ["v.example"],
     "profile_name": "Slow network"},
]}

# One call for each real loader. Each stores its input and shows the block.
LOADERS = {
    "runtime": "renderRuntimeSummary(%s);" % json.dumps(STATE),
    "profiles": "responses['/api/profiles'] = %s; await loadProfiles();" % json.dumps(PROFILES),
    "general": "generalProfileState = null; responses['/api/general-profile'] = %s;"
               "await loadGeneralProfile();" % json.dumps(NO_GENERAL),
    "lists": "renderServiceLists(%s);" % json.dumps(LISTS),
    "clients": "renderConnectedDevices(%s);" % json.dumps(GOOD_CLIENTS),
}

def load_everything_but(last):
    first = [name for name in LOADERS if name != last]
    return "\n".join(LOADERS[name] for name in first) + "\n" + LOADERS[last]


# Every input of the block comes from a real loader.

@pytest.mark.parametrize("last", sorted(LOADERS))
def test_the_block_shows_complete_data_as_soon_as_the_last_input_arrives(last):
    body = (
        "adminTokenVerified = true;\n"
        + load_everything_but(last)
        + "\nconsole.log(JSON.stringify(block()));"
    )
    result = run(body)
    assert result["hidden"] is False
    assert result["general"] == "Everyone: none"
    assert result["summary"] == "1 device affected"
    assert result["items"] == [["Laptop", "Slow network"]]
    assert result["lists"] == "Service lists: Video (every unprotected device)"
    assert result["plainHidden"] is True
    assert result["detailsHidden"] is False


def test_the_active_general_profile_shows_as_soon_as_it_arrives():
    body = (
        "\n".join(LOADERS[name] for name in ("runtime", "profiles", "lists", "clients"))
        + "\ngeneralProfileState = null; responses['/api/general-profile'] = %s;"
        % json.dumps(ACTIVE_GENERAL)
        + "\nawait loadGeneralProfile();\nconsole.log(JSON.stringify(block()));"
    )
    result = run(body)
    assert result["general"] == "Everyone: Shared slow · active"
    assert result["generalClass"] == "status-line status-on"
    assert result["items"] == [["Laptop", "Shared slow"]]


@pytest.mark.parametrize("missing", sorted(LOADERS))
def test_one_loading_line_shows_until_every_input_has_arrived(missing):
    others = [name for name in LOADERS if name != missing]
    body = (
        "\n".join(LOADERS[name] for name in others)
        + "\nconsole.log(JSON.stringify(block()));"
    )
    result = run(body)
    assert result["hidden"] is False
    assert result["general"] == "Loading…"
    assert result["generalClass"] == "status-line"
    assert result["plainHidden"] is True
    assert result["detailsHidden"] is True
    assert result["lists"] == ""


def test_nothing_shows_before_the_first_check():
    result = run("adminTokenVerified = false; renderSidebarStatus();"
                              "console.log(JSON.stringify(block()));")
    assert result["hidden"] is True
    assert result["general"] == ""


# The device list can be unavailable for two reasons.

@pytest.mark.parametrize(
    "payload",
    [
        {"available": False, "devices": [], "message": "scan failed"},
        {"available": True, "association_status_available": False,
         "devices": [{**LAPTOP, "association_verified": False}]},
    ],
)
def test_an_unavailable_device_list_says_so(payload):
    body = (
        "\n".join(LOADERS[name] for name in ("runtime", "profiles", "general", "lists"))
        + "\nrenderConnectedDevices(%s);\n" % json.dumps(payload)
        + "console.log(JSON.stringify(block()));"
    )
    result = run(body)
    assert result["plain"] == "Connected devices unavailable"
    assert result["detailsHidden"] is True
    assert result["items"] == []
    assert "Laptop" not in result["everything"]
    assert result["lists"] == "Service lists: Video (every unprotected device)"


def test_a_failed_device_request_is_unavailable_and_recovery_lists_again():
    body = (
        "\n".join(LOADERS[name] for name in ("runtime", "profiles", "general", "lists", "clients"))
        + "\nresponses['/api/connected-devices'] = new Error('refused');"
        "\nresponses['/api/connected-device-labels'] = {labels: []};"
        "\nawait loadConnectedDevices();"
        "\nconst failed = block();"
        "\nresponses['/api/connected-devices'] = %s;" % json.dumps(GOOD_CLIENTS)
        + "\nawait loadConnectedDevices();"
        "\nconsole.log(JSON.stringify({failed, recovered: block()}));"
    )
    result = run(body)
    assert result["failed"]["plain"] == "Connected devices unavailable"
    assert result["failed"]["items"] == []
    assert result["recovered"]["summary"] == "1 device affected"
    assert result["recovered"]["items"] == [["Laptop", "Slow network"]]


# The runtime state

def test_a_state_without_the_running_flag_does_not_count_as_running():
    body = (
        "\n".join(LOADERS[name] for name in ("profiles", "general", "lists", "clients"))
        + "\nrenderRuntimeSummary({observed_state: 'applied'});"
        "\nconsole.log(JSON.stringify(block()));"
    )
    result = run(body)
    assert result["plain"] == "Not running"
    assert result["lists"] == ""
    assert result["items"] == []


# Locking the dashboard

def everything_loaded():
    return "\n".join(LOADERS.values())


def test_locking_hides_the_block_and_forgets_every_input():
    body = (
        everything_loaded()
        + "\nconst before = block();"
        "\nadminTokenVerified = false;"
        "\nrenderSidebarStatus();"
        "\nconst locked = block();"
        "\nconsole.log(JSON.stringify({before, locked, inputs: {"
        "  runtime: lastRuntimeState, general: generalProfileState, data: lastConnectedData,"
        "  clients: connectedClients, lists: serviceListsState, listsLoaded: serviceListsLoaded,"
        "  profilesLoaded, available: connectedClientsAvailable}}));"
    )
    result = run(body)
    assert result["before"]["items"] == [["Laptop", "Slow network"]]
    assert result["locked"]["hidden"] is True
    assert result["locked"]["general"] == ""
    assert result["locked"]["items"] == []
    assert result["locked"]["summary"] == ""
    assert result["locked"]["lists"] == ""
    assert "Laptop" not in result["locked"]["everything"]
    assert result["inputs"] == {
        "runtime": None, "general": None, "data": None, "clients": [], "lists": [],
        "listsLoaded": False, "profilesLoaded": False, "available": False,
    }


def test_editing_the_token_forgets_the_data_so_a_new_check_cannot_show_it():
    body = (
        everything_loaded()
        + "\nadminTokenVerified = false;"
        "\nsetPrivateDataPanels('Token changed. Choose Check token to load.');"
        "\nconst locked = block();"
        # the next check verifies the token and the first answer to arrive is the profiles
        "\nadminTokenVerified = true;"
        "\nresponses['/api/profiles'] = %s;" % json.dumps(PROFILES)
        + "\nawait loadProfiles();"
        "\nconsole.log(JSON.stringify({locked, reverified: block()}));"
    )
    result = run(body)
    assert result["locked"]["hidden"] is True
    assert result["reverified"]["hidden"] is False
    assert result["reverified"]["general"] == "Loading…"
    assert "Laptop" not in result["reverified"]["everything"]
    assert "Video" not in result["reverified"]["everything"]


# Nothing to toggle

def test_no_affected_devices_is_plain_text_and_affected_devices_are_a_list():
    body = (
        everything_loaded()
        + "\nconst listed = block();"
        "\nrenderConnectedDevices({available: true, association_status_available: true,"
        " devices: [%s]});" % json.dumps(client("Guest", "02:00:00:00:00:09"))
        + "\nconst empty = block();"
        "\nconsole.log(JSON.stringify({listed, empty}));"
    )
    result = run(body)
    assert result["listed"]["detailsHidden"] is False
    assert result["listed"]["plainHidden"] is True
    assert result["listed"]["summary"] == "1 device affected"
    assert result["empty"]["detailsHidden"] is True
    assert result["empty"]["plainHidden"] is False
    assert result["empty"]["plain"] == "No device has its own profile"
    assert result["empty"]["items"] == []
    assert result["empty"]["more"] == ""


def test_more_than_five_devices_show_the_rest_as_a_count():
    devices = [
        client("Device %d" % number, "02:00:00:00:00:%02d" % number,
               assigned_device_id="d%d" % number, assigned_profile_id="slow")
        for number in range(8)
    ]
    body = (
        everything_loaded()
        + "\nrenderConnectedDevices({available: true, association_status_available: true,"
        " devices: %s});" % json.dumps(devices)
        + "\nconsole.log(JSON.stringify(block()));"
    )
    result = run(body)
    assert result["summary"] == "8 devices affected"
    assert len(result["items"]) == 5
    assert result["more"] == "+3 more"


# The live state poll

def test_an_external_reset_is_picked_up_by_the_next_poll():
    body = (
        everything_loaded()
        + "\nrequests.length = 0;\nconst running = block();"
        "\nresponses['/api/state'] = {state: {observed_state: 'reset', running: false}};"
        "\nresponses['/api/connected-devices'] = %s;" % json.dumps(GOOD_CLIENTS)
        + "\nresponses['/api/connected-device-labels'] = {labels: []};"
        "\nresponses['/api/general-profile'] = %s;" % json.dumps(NO_GENERAL)
        + "\npollLiveStatus();"
        "\nawait flush();"
        "\nawait flush();"
        "\nconsole.log(JSON.stringify({running, polled: block(), requests,"
        " runtime: element('runtime-summary').textContent}));"
    )
    result = run(body)
    assert result["running"]["summary"] == "1 device affected"
    assert result["polled"]["plain"] == "Profiles off"
    assert result["polled"]["items"] == []
    assert result["polled"]["lists"] == ""
    assert "GET /api/state" in result["requests"]
    assert result["runtime"] == "Off. No traffic shaping is running."


@pytest.mark.parametrize(
    "setup",
    [
        "networkActionBusy = true;",
        "adminTokenVerified = false;",
    ],
)
def test_the_poll_does_nothing_while_busy_or_locked(setup):
    body = (
        everything_loaded()
        + "\nrequests.length = 0;\n%s\npollLiveStatus();\nawait flush();" % setup
        + "\nconsole.log(JSON.stringify({requests}));"
    )
    assert run(body)["requests"] == []


def test_the_poll_skips_the_state_while_the_tab_is_hidden():
    body = (
        everything_loaded()
        + "\nrequests.length = 0;\ndocument.hidden = true;"
        "\nresponses['/api/state'] = {state: {observed_state: 'reset', running: false}};"
        "\nresponses['/api/connected-devices'] = %s;" % json.dumps(GOOD_CLIENTS)
        + "\nresponses['/api/connected-device-labels'] = {labels: []};"
        "\nresponses['/api/general-profile'] = %s;" % json.dumps(NO_GENERAL)
        + "\npollLiveStatus();\nawait flush();\nawait flush();"
        "\nconsole.log(JSON.stringify({requests, shown: block().summary}));"
    )
    result = run(body)
    assert "GET /api/state" not in result["requests"]
    assert result["shown"] == "1 device affected"


def test_a_poll_answer_that_arrives_after_an_action_started_is_dropped():
    body = (
        everything_loaded()
        + "\nconst slow = deferred();"
        "\nresponses['/api/state'] = () => slow.promise;"
        "\nconst polling = loadState();"
        "\nawait flush();"
        # the user presses Reset before the poll answer arrives
        "\nsetActionBusy(true, '/api/reset');"
        "\nrenderRuntimeSummary({observed_state: 'reset', running: false});"
        "\nsetActionBusy(false, '/api/reset');"
        "\nslow.resolve({state: {observed_state: 'applied', running: true}});"
        "\nawait polling;"
        "\nconsole.log(JSON.stringify({shown: block().plain,"
        " runtime: element('runtime-summary').textContent}));"
    )
    result = run(body)
    assert result["shown"] == "Profiles off"
    assert result["runtime"] == "Off. No traffic shaping is running."


# A failed Apply or Reset

@pytest.mark.parametrize(
    "path,last_action,runtime,shown",
    [
        ("/api/apply", "apply", "The last start failed. Check the Pi and try again.",
         "Last network action failed"),
        # The scheduler is paused before the Pi is asked to remove its rules. A failed reset
        # therefore reads as an error with running false and the last action "reset".
        ("/api/reset", "reset", "The last reset failed. The Pi may still be shaping. Check it.",
         "Last reset failed. The Pi may still be shaping."),
    ],
)
def test_a_failed_action_refreshes_the_state_once(path, last_action, runtime, shown):
    body = (
        everything_loaded()
        + "\nrequests.length = 0;"
        "\nresponses[%s] = new Error('network helper failed');" % json.dumps(path)
        + "\nresponses['/api/state'] = {state: {observed_state: 'error', running: false,"
        " last_action: %s}};" % json.dumps(last_action)
        + "\nawait runAction(%s, 'done', 'working');" % json.dumps(path)
        + "\nawait flush();"
        "\nconsole.log(JSON.stringify({requests, messages, shown: block().plain,"
        " runtime: element('runtime-summary').textContent,"
        " button: element('apply-button').textContent}));"
    )
    result = run(body)
    assert result["requests"].count("GET /api/state") == 1
    assert result["shown"] == shown
    assert result["runtime"] == runtime
    assert result["button"] == "Start"
    assert result["messages"][-1] == ["network helper failed", "error"]


def test_a_successful_action_does_not_fetch_the_state_twice():
    body = (
        everything_loaded()
        + "\nrequests.length = 0;"
        "\nresponses['/api/reset'] = {state: {observed_state: 'reset', running: false}};"
        "\nawait runAction('/api/reset', 'done', 'working');"
        "\nawait flush();"
        "\nconsole.log(JSON.stringify({requests, shown: block().plain}));"
    )
    result = run(body)
    assert "GET /api/state" not in result["requests"]
    assert result["shown"] == "Profiles off"
