"""The Active profile control above the Start button, run in node with the served functions."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
HTML = DASHBOARD_HTML.split("<script>")[0]
NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
SLOW = {"id": "slow", "name": "Slow office"}
LOSSY = {"id": "lossy", "name": "Lossy mobile"}
APPLIED = "General profile applied. Protected devices remain unaffected."
STOPPED = "General profile stopped. Saved device rules resumed."


def between(start, end):
    first = SCRIPT.index(start)
    return SCRIPT[first : SCRIPT.index(end, first)]


def function_source(name):
    match = re.search(r"\n    (?:async )?function " + re.escape(name) + r"\(", SCRIPT)
    following = re.search(r"\n    (?:async )?function \w+\(", SCRIPT[match.end() :])
    return SCRIPT[match.start() : match.end() + following.start()]


def test_the_control_sits_above_the_start_button_with_a_label_and_a_note():
    sidebar = re.search(r'<aside class="sidebar".*?</aside>', HTML, re.DOTALL).group(0)
    order = [sidebar.index(marker) for marker in (
        'class="sidebar-operate"', 'id="active-profile"', 'id="active-profile-select"',
        'id="active-profile-button"', 'id="active-profile-note"', 'id="apply-button"',
        'id="apply-summary"',
    )]
    assert order == sorted(order)
    assert '<label for="active-profile-select">Active profile</label>' in sidebar
    select = re.search(r"<select id=\"active-profile-select\"[^>]*>\s*<option[^>]*>[^<]*", sidebar)
    assert 'aria-describedby="active-profile-note"' in select.group(0)
    assert select.group(0).endswith('<option value="">None selected')
    assert ">Apply to all devices</button>" in sidebar


def test_the_control_is_drawn_whenever_the_general_profile_or_the_sidebar_change():
    guarded = 'if (typeof renderActiveProfileControl === "function") renderActiveProfileControl();'
    assert guarded in function_source("updateGeneralProfileControls")
    assert guarded in function_source("renderSidebarStatus")
    # The code only writes text.
    block = between("    // Active profile control in the sidebar", "    // end of the active profile control")
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML"):
        assert forbidden not in block


PRELUDE = r"""
const assert = require("node:assert/strict");
class FakeNode {
  constructor(tag) {
    Object.assign(this, {tagName: tag, children: [], dataset: {}, attributes: {}, listeners: {},
      className: "", value: "", disabled: false, hidden: false, href: ""});
  }
  get textContent() {
    return this._text !== undefined ? this._text
      : this.children.map((child) => typeof child === "string" ? child : child.textContent).join("");
  }
  set textContent(value) { this.children = []; this._text = String(value); }
  append(...nodes) {
    if (this._text !== undefined) { this.children = [this._text]; this._text = undefined; }
    this.children.push(...nodes);
  }
  replaceChildren(...nodes) { this._text = undefined; this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
}
const store = {};
const element = (id) => (store[id] = store[id] || new FakeNode("div"));
const document = { createElement: (tag) => new FakeNode(tag) };
let profiles = [];
let generalProfileState = null;
let networkActionBusy = false;
let helperConfigured = true;
let adminTokenVerified = true;
let savedProfileBusy = null;
const sent = [];
const messages = [];
const tabs = [];
const busyWhileSending = [];
let failNext = false;
let server = {profile: null, active: false, state: {observed_state: "applied"}};
async function request(path, method, body) {
  sent.push([path, method, body === undefined ? null : body]);
  busyWhileSending.push(element("active-profile-button").getAttribute("aria-busy"));
  if (failNext) { failNext = false; throw new Error("Fixture network update failed"); }
  if (path === "/api/general-profile") {
    server = {profile: profiles.find((profile) => profile.id === body.profile_id), active: true,
              state: {observed_state: "applied"}};
  }
  if (path === "/api/general-profile/stop") {
    server = {profile: null, active: false, state: {observed_state: "applied"}};
  }
  return {};
}
function showMessage(text, kind) { messages.push([text, kind]); }
function refreshAfterSuccess(text) { messages.push([text, "success"]); }
function setActionBusy(busy) { networkActionBusy = busy; renderActiveProfileControl(); }
async function loadPrivateDataPanel(id, load) { await load(); }
async function loadGeneralProfile() {
  generalProfileState = JSON.parse(JSON.stringify(server));
  renderActiveProfileControl();
}
function followTabLink(event, name) { tabs.push(name); return true; }
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
function control() {
  const select = element("active-profile-select");
  const button = element("active-profile-button");
  const note = element("active-profile-note");
  const links = note.children.filter((child) => typeof child !== "string");
  return {
    options: select.children.map((option) => [option.value, option.textContent]),
    value: select.value, select_disabled: select.disabled, button: button.textContent,
    primary: button.className === "primary", disabled: button.disabled,
    aria_disabled: button.getAttribute("aria-disabled"), busy: button.getAttribute("aria-busy"),
    note: note.textContent, links: links.map((link) => [link.textContent, link.href])
  };
}
function choose(value) {
  const select = element("active-profile-select");
  select.value = value;
  select.listeners.change({target: select});
}
async function press() {
  element("active-profile-button").listeners.click();
  for (let round = 0; round < 5; round++) await flush();
}
"""


def run_node(setup, body):
    program = (
        PRELUDE
        + between(
            "async function runGeneralProfileAction(",
            'element("general-profile-select").addEventListener("change"',
        )
        + between("    // Active profile control in the sidebar", "    // end of the active profile control")
        + "\n(async () => {\n" + setup + "\n" + body + "\n})().catch((error) => {\n"
        + "  console.error(error); process.exit(1);\n});\n"
    )
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def state(profile=None, active=False, observed="applied"):
    return json.dumps({"profile": profile, "active": active, "state": {"observed_state": observed}})


def with_profiles(*profiles):
    return f"profiles = {json.dumps(list(profiles))};"


OPTIONS = [["", "None selected"], ["slow", "Slow office"], ["lossy", "Lossy mobile"]]


@NEEDS_NODE
def test_nothing_active_lists_the_saved_profiles_and_waits_for_a_choice():
    result = run_node(
        with_profiles(SLOW, LOSSY) + f"generalProfileState = {state()};",
        "renderActiveProfileControl(); console.log(JSON.stringify(control()));",
    )
    assert result["options"] == OPTIONS
    assert result["value"] == "" and result["select_disabled"] is False
    assert result["button"] == "Apply to all devices" and result["disabled"] is True
    assert result["note"] == "Protected devices stay untouched."


@NEEDS_NODE
def test_the_active_general_profile_is_shown_and_the_button_stops_it():
    result = run_node(
        with_profiles(SLOW, LOSSY) + f"generalProfileState = {state(SLOW, True)};",
        "renderActiveProfileControl(); console.log(JSON.stringify(control()));",
    )
    assert result["value"] == "slow"
    assert result["button"] == "Stop" and result["primary"] is False and result["disabled"] is False


@NEEDS_NODE
def test_a_paused_general_profile_is_offered_again():
    result = run_node(
        with_profiles(SLOW, LOSSY) + f"generalProfileState = {state(SLOW, False, 'reset')};",
        "renderActiveProfileControl(); console.log(JSON.stringify(control()));",
    )
    assert result["value"] == "slow"
    assert result["button"] == "Apply to all devices" and result["disabled"] is False


@NEEDS_NODE
def test_choosing_a_profile_does_nothing_until_the_button_is_pressed():
    result = run_node(
        with_profiles(SLOW, LOSSY) + f"generalProfileState = {state()};",
        "renderActiveProfileControl(); choose('lossy');\n"
        "renderActiveProfileControl();  // a poll in between keeps the choice\n"
        "console.log(JSON.stringify({sent, control: control()}));",
    )
    assert result["sent"] == []
    assert result["control"]["value"] == "lossy"
    assert result["control"]["button"] == "Apply to all devices"
    assert result["control"]["disabled"] is False


@NEEDS_NODE
def test_apply_to_all_devices_uses_the_saved_list_request_and_messages():
    result = run_node(
        with_profiles(SLOW, LOSSY) + f"generalProfileState = {state()};",
        "renderActiveProfileControl(); choose('lossy'); await press();\n"
        "console.log(JSON.stringify({sent, messages, busyWhileSending, control: control(),\n"
        "  networkActionBusy, savedProfileBusy}));",
    )
    assert result["sent"] == [["/api/general-profile", "POST", {"profile_id": "lossy"}]]
    assert result["messages"] == [
        ["Applying the profile to all unprotected devices...", "working"], [APPLIED, "success"],
    ]
    assert result["busyWhileSending"] == ["true"]
    assert result["control"]["value"] == "lossy" and result["control"]["button"] == "Stop"
    assert result["control"]["busy"] == "false" and result["control"]["aria_disabled"] is None
    assert result["networkActionBusy"] is False and result["savedProfileBusy"] is None


@NEEDS_NODE
def test_stop_uses_the_saved_list_request_and_messages():
    result = run_node(
        with_profiles(SLOW, LOSSY) + f"generalProfileState = {state(SLOW, True)};"
        + f"server = {state(SLOW, True)};",
        "renderActiveProfileControl(); await press();\n"
        "console.log(JSON.stringify({sent, messages, control: control()}));",
    )
    assert result["sent"] == [["/api/general-profile/stop", "POST", {}]]
    assert result["messages"] == [["Stopping the general profile...", "working"], [STOPPED, "success"]]
    assert result["control"]["value"] == ""
    assert result["control"]["button"] == "Apply to all devices" and result["control"]["disabled"] is True


@NEEDS_NODE
def test_another_profile_replaces_the_active_one():
    result = run_node(
        with_profiles(SLOW, LOSSY) + f"generalProfileState = {state(SLOW, True)};",
        "renderActiveProfileControl(); choose('lossy');\n"
        "const before = control().button; await press();\n"
        "console.log(JSON.stringify({before, sent, after: control()}));",
    )
    assert result["before"] == "Apply to all devices"
    assert result["sent"] == [["/api/general-profile", "POST", {"profile_id": "lossy"}]]
    assert result["after"]["value"] == "lossy" and result["after"]["button"] == "Stop"


@NEEDS_NODE
def test_a_locked_dashboard_disables_it_and_keeps_no_profile_names():
    result = run_node(
        with_profiles(SLOW, LOSSY) + f"generalProfileState = {state(SLOW, True)};"
        + "adminTokenVerified = false;",
        "renderActiveProfileControl(); await press();\n"
        "console.log(JSON.stringify({sent, control: control()}));",
    )
    assert result["sent"] == []
    assert result["control"]["options"] == [["", "None selected"]]
    assert result["control"]["value"] == ""
    assert result["control"]["button"] == "Apply to all devices"
    assert result["control"]["select_disabled"] is True and result["control"]["disabled"] is True
    assert result["control"]["note"] == "Check the admin token first."


@NEEDS_NODE
def test_without_the_pi_helper_it_says_why_it_is_disabled():
    result = run_node(
        with_profiles(SLOW, LOSSY) + f"generalProfileState = {state()};" + "helperConfigured = false;",
        "renderActiveProfileControl(); choose('slow'); await press();\n"
        "console.log(JSON.stringify({sent, control: control()}));",
    )
    assert result["sent"] == []
    assert result["control"]["select_disabled"] is True and result["control"]["disabled"] is True
    assert result["control"]["note"] == "Needs the Pi helper to run."


@NEEDS_NODE
def test_no_saved_profiles_points_to_the_profiles_page():
    result = run_node(
        f"generalProfileState = {state()};",
        "renderActiveProfileControl(); renderActiveProfileControl();\n"
        "const note = element('active-profile-note');\n"
        "const link = note.children.find((child) => typeof child !== 'string');\n"
        "link.listeners.click({button: 0});\n"
        "console.log(JSON.stringify({control: control(), tabs}));",
    )
    assert result["control"]["options"] == [["", "None selected"]]
    assert result["control"]["select_disabled"] is True and result["control"]["disabled"] is True
    assert result["control"]["note"] == "No saved profiles yet. Create one on the Profiles page"
    assert result["control"]["links"] == [["Create one on the Profiles page", "#profiles"]]
    assert result["tabs"] == ["profiles"]


@NEEDS_NODE
def test_it_follows_changes_made_elsewhere():
    result = run_node(
        with_profiles(SLOW, LOSSY) + f"generalProfileState = {state(SLOW, True)};",
        "renderActiveProfileControl(); choose('lossy');\n"
        "const seen = [[control().value, control().button]];\n"
        f"generalProfileState = {state(LOSSY, True)}; renderActiveProfileControl();\n"
        "seen.push([control().value, control().button]);\n"
        f"generalProfileState = {state()}; renderActiveProfileControl();\n"
        "seen.push([control().value, control().button]);\n"
        "profiles = [profiles[0]]; renderActiveProfileControl();\n"
        "seen.push(control().options);\n"
        "console.log(JSON.stringify(seen));",
    )
    assert result == [
        ["lossy", "Apply to all devices"],
        ["lossy", "Stop"],
        ["", "Apply to all devices"],
        [["", "None selected"], ["slow", "Slow office"]],
    ]


@NEEDS_NODE
def test_a_running_action_keeps_the_button_focusable_and_ignores_presses():
    result = run_node(
        with_profiles(SLOW, LOSSY) + f"generalProfileState = {state(SLOW, True)};"
        + "networkActionBusy = true;",
        "renderActiveProfileControl(); await press();\n"
        "console.log(JSON.stringify({sent, control: control()}));",
    )
    assert result["sent"] == []
    assert result["control"]["select_disabled"] is True
    assert result["control"]["disabled"] is False and result["control"]["aria_disabled"] == "true"


@NEEDS_NODE
def test_a_failed_request_keeps_the_choice_and_shows_the_error():
    result = run_node(
        with_profiles(SLOW, LOSSY) + f"generalProfileState = {state()};",
        "renderActiveProfileControl(); choose('lossy'); failNext = true; await press();\n"
        "console.log(JSON.stringify({messages, control: control()}));",
    )
    assert result["messages"][-1] == ["Fixture network update failed", "error"]
    assert result["control"]["value"] == "lossy"
    assert result["control"]["button"] == "Apply to all devices" and result["control"]["disabled"] is False
