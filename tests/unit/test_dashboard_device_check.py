"""Exercise the connected-device check button using the dashboard served to the browser."""

import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML


def served_functions():
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)

    def block(start, end):
        return script[script.index(start) : script.index(end, script.index(start))]

    return (
        block("const privateDataPanelIds =", "async function loadPrivateDataPanel(")
        + block("function requireAdminToken(", "function closeCredentials(")
        + block("async function loadConnectedDevices(", "async function loadUplink(")
    )


HARNESS = r"""
const assert = require("node:assert/strict");
const nodes = new Map();
const messages = [];
const requests = [];
const rendered = [];
const openedCredentials = [];
let adminTokenVerified = true;
let adminTokenChecking = false;
let connectedDevicesRequestInFlight = false;
let tokenChecks = 0;
let responses = {};
const document = {activeElement: null};
function element(id) {
  if (!nodes.has(id)) nodes.set(id, {
    id, value: "", textContent: "", className: "", disabled: false,
    attributes: new Map(), listeners: new Map(),
    setAttribute(key, value) {this.attributes.set(key, value)},
    removeAttribute(key) {this.attributes.delete(key)},
    getAttribute(key) {return this.attributes.get(key)},
    addEventListener(kind, callback) {this.listeners.set(kind, callback)},
    contains() {return false}
  });
  return nodes.get(id);
}
function showMessage(text, kind) {messages.push({text, kind})}
function openCredentials(kind) {openedCredentials.push(kind)}
function setButtonBusy(button, busy) {
  button.disabled = busy;
  if (busy) button.setAttribute("aria-busy", "true");
  else button.removeAttribute("aria-busy");
}
async function checkAdminToken() {tokenChecks++}
function renderConnectedDevices(data) {rendered.push(data)}
function renderSidebarStatus() {}
async function request(path) {
  requests.push(path);
  return await responses[path]();
}
function deferred() {
  let resolve;
  const promise = new Promise(done => {resolve = done});
  return {promise, resolve};
}
const flush = () => new Promise(resolve => setTimeout(resolve, 0));
function press(id) {
  const click = element(id).listeners.get("click");
  assert.ok(click, id + " has no click handler");
  if (element(id).disabled) return;  // a browser never clicks a disabled button
  click({currentTarget: element(id)});
}
function twoDevices() {
  responses = {
    "/api/connected-devices": async () => ({available: true, devices: [{}, {}]}),
    "/api/connected-device-labels": async () => ({labels: []})
  };
}
const CLOCK = /\d{2}:\d{2}:\d{2}/;
element("check-devices").textContent = "Check for devices";  // the label the page starts with
"""


CASES = {
    "reloads_the_list_and_reports_what_it_found": r"""
      element("admin-token").value = "fixture token";
      twoDevices();
      press("check-devices");
      await flush();
      assert.deepEqual(requests.sort(), ["/api/connected-device-labels", "/api/connected-devices"]);
      assert.equal(rendered.length, 1);
      assert.equal(rendered[0].devices.length, 2);
      assert.deepEqual(messages.at(-1), {text: "2 devices found.", kind: "success"});
      assert.match(element("connected-refresh-status").textContent, CLOCK);
      assert.match(element("connected-refresh-status").textContent, /every 5 seconds/);
      assert.equal(element("check-devices").disabled, false);
      assert.equal(element("check-devices").textContent, "Check for devices");
      assert.equal(element("check-devices").attributes.has("aria-busy"), false);
    """,
    "singular_and_empty_results": r"""
      element("admin-token").value = "fixture token";
      responses = {
        "/api/connected-devices": async () => ({available: true, devices: [{}]}),
        "/api/connected-device-labels": async () => ({labels: []})
      };
      press("check-devices");
      await flush();
      assert.equal(messages.at(-1).text, "1 device found.");
      responses["/api/connected-devices"] = async () => ({available: true, devices: []});
      press("check-devices");
      await flush();
      assert.deepEqual(messages.at(-1), {text: "No devices found.", kind: "success"});
    """,
    "shows_progress_and_ignores_a_second_click": r"""
      element("admin-token").value = "fixture token";
      const pending = deferred();
      responses = {
        "/api/connected-devices": () => pending.promise,
        "/api/connected-device-labels": async () => ({labels: []})
      };
      press("check-devices");
      await flush();
      assert.equal(element("check-devices").disabled, true);
      assert.equal(element("check-devices").textContent, "Checking...");
      assert.equal(element("check-devices").getAttribute("aria-busy"), "true");
      press("check-devices");
      await flush();
      assert.equal(requests.filter(path => path === "/api/connected-devices").length, 1);
      pending.resolve({available: true, devices: [{}]});
      await flush();
      assert.equal(element("check-devices").disabled, false);
      assert.equal(element("check-devices").textContent, "Check for devices");
      assert.equal(messages.at(-1).text, "1 device found.");
    """,
    "asks_for_the_admin_token": r"""
      press("check-devices");
      await flush();
      assert.equal(requests.length, 0);
      assert.equal(tokenChecks, 0);
      assert.deepEqual(openedCredentials, ["admin"]);
      assert.equal(messages.at(-1).kind, "error");
      assert.match(messages.at(-1).text, /admin token/);
      assert.equal(element("check-devices").disabled, false);
    """,
    "checks_an_unverified_token_instead_of_loading": r"""
      element("admin-token").value = "fixture token";
      adminTokenVerified = false;
      press("check-devices");
      await flush();
      assert.equal(tokenChecks, 1);
      assert.equal(requests.length, 0);
      assert.equal(element("check-devices").disabled, false);
      assert.equal(element("check-devices").textContent, "Check for devices");
    """,
    "waits_while_the_token_is_still_being_checked": r"""
      element("admin-token").value = "fixture token";
      adminTokenVerified = false;
      adminTokenChecking = true;
      press("check-devices");
      await flush();
      assert.equal(tokenChecks, 0);
      assert.equal(requests.length, 0);
      assert.match(messages.at(-1).text, /still being checked/);
      assert.equal(element("check-devices").disabled, false);
    """,
    "locking_the_private_panels_resets_the_refresh_status": r"""
      element("admin-token").value = "fixture token";
      twoDevices();
      press("check-devices");
      await flush();
      assert.match(element("connected-refresh-status").textContent, /^Updated /);
      setPrivateDataPanels("Token changed. Choose Check token to load.");
      assert.equal(
        element("connected-refresh-status").textContent,
        "Refreshes every 5 seconds once the admin token is accepted."
      );
    """,
    "reports_why_devices_could_not_be_read": r"""
      element("admin-token").value = "fixture token";
      responses = {
        "/api/connected-devices": async () => {throw new Error("network helper unavailable")},
        "/api/connected-device-labels": async () => ({labels: []})
      };
      press("check-devices");
      await flush();
      assert.equal(rendered.length, 1);
      assert.equal(rendered[0].available, false);
      assert.deepEqual(messages.at(-1), {text: "network helper unavailable", kind: "error"});
      assert.equal(
        element("connected-refresh-status").textContent,
        "Could not update: network helper unavailable"
      );
      assert.equal(element("check-devices").disabled, false);
      assert.equal(element("check-devices").textContent, "Check for devices");
    """,
    "does_not_start_a_second_check_while_one_is_running": r"""
      element("admin-token").value = "fixture token";
      connectedDevicesRequestInFlight = true;
      twoDevices();
      press("check-devices");
      await flush();
      assert.equal(requests.length, 0);
      assert.equal(rendered.length, 0);
      assert.equal(messages.at(-1).text, "A device check is already running.");
      assert.equal(element("check-devices").disabled, false);
    """,
    "background_refresh_updates_the_status_line_quietly": r"""
      twoDevices();
      await loadConnectedDevices();
      assert.equal(rendered.length, 1);
      assert.equal(messages.length, 0);
      assert.match(element("connected-refresh-status").textContent, CLOCK);
      connectedDevicesRequestInFlight = false;
    """,
}


def test_check_for_devices_button_sits_in_the_connected_clients_card():
    card = re.search(
        r'<section class="card connected-card">.*?</section>', DASHBOARD_HTML, re.DOTALL
    ).group(0)
    assert 'id="connected-devices"' in card
    button = re.search(r'<button[^>]*id="check-devices"[^>]*>[^<]*</button>', card).group(0)
    assert 'type="button"' in button
    assert "Check for devices" in button
    status = re.search(r'<p[^>]*id="connected-refresh-status"[^>]*>', card).group(0)
    # The timestamp changes every few seconds, so it must not be announced by screen readers.
    assert "aria-live" not in status
    assert 'role="status"' not in status
    assert DASHBOARD_HTML.count('id="check-devices"') == 1


@pytest.mark.parametrize("case", CASES)
def test_served_device_check_behavior(case):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise the served dashboard")
    program = (
        HARNESS
        + served_functions()
        + "\n(async () => {"
        + CASES[case]
        + "\nconsole.log('CASE-COMPLETE');"
        + "\n})().catch(error => {console.error(error); process.exitCode = 1});"
    )
    result = subprocess.run([node, "-"], input=program, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    # Node exits quietly with code 0 if a case hangs on a promise that never settles.
    assert "CASE-COMPLETE" in result.stdout, "the case never finished"


def test_refresh_status_wording_matches_between_the_page_and_the_script():
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
    initial = re.search(
        r'<p id="connected-refresh-status"[^>]*>\s*(.*?)\s*</p>', DASHBOARD_HTML, re.DOTALL
    ).group(1)
    locked = script[script.index("function setPrivateDataPanels(") :]
    locked = locked[: locked.index("async function loadPrivateDataPanel(")]
    assert initial in locked
