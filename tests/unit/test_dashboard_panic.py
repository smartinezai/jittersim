"""Exercise the panic button using the HTML and JavaScript served to the browser."""

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
        block("async function request(", "async function show(")
        + block("function renderMode(", "function renderRuntimeSummary(")
        + block("function setActionBusy(", 'element("scan-observations").addEventListener')
    )


HARNESS = r"""
const assert = require("node:assert/strict");
const nodes = new Map();
const messages = [];
const openedCredentials = [];
const refreshMessages = [];
const runtimeStates = [];
const networkCalls = [];
let helperConfigured = true;
let profiles = [];
let generalProfileState = null;
let lastRuntimeState = null;
let helperKnown = true;
let applyBusy = false;
let networkActionBusy = false;
let networkActionRevision = 0;
let wifiResetPending = false;
let adminTokenVerified = true;
let adminTokenRevision = 0;
let adminTokenChecking = false;
let pendingFormSubmitButton = null;
let network = async () => ({ok: true, json: async () => ({state: {observed_state: "reset"}})});
function element(id) {
  if (!nodes.has(id)) nodes.set(id, {
    id, value: "", textContent: "", className: "", disabled: false,
    attributes: new Map(), listeners: new Map(),
    setAttribute(key, value) {this.attributes.set(key, value)},
    removeAttribute(key) {this.attributes.delete(key)},
    getAttribute(key) {return this.attributes.get(key)},
    addEventListener(kind, callback) {this.listeners.set(kind, callback)}
  });
  return nodes.get(id);
}
function showMessage(text, kind) {messages.push({text, kind})}
function openCredentials(kind) {openedCredentials.push(kind)}
function setButtonBusy(button, busy) {button.disabled = busy}
function renderRuntimeSummary(state) {runtimeStates.push(state); lastRuntimeState = state}
function refreshAfterSuccess(message) {refreshMessages.push(message)}
function renderSavedProfiles() {}
const refreshedPanels = [];
async function loadPrivateDataPanel(panelId) {refreshedPanels.push(panelId)}
async function loadState() {}
async function fetch(path, options) {networkCalls.push({path, options}); return await network()}
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
const ACTION_BUTTONS = ["panic-button", "apply-button", "reset-button"];
"""


CASES = {
    "resets_every_profile_with_one_click": r"""
      element("admin-token").value = "fixture token";
      press("panic-button");
      await flush();
      assert.equal(networkCalls.length, 1);
      assert.equal(networkCalls[0].path, "/api/reset");
      assert.equal(networkCalls[0].options.method, "POST");
      assert.equal(networkCalls[0].options.headers.Authorization, "Bearer fixture token");
      assert.equal(runtimeStates.length, 1);
      assert.equal(refreshMessages.length, 1);
      assert.equal(
        refreshMessages[0],
        "All profiles are off. Choose Start with saved settings to resume."
      );
      for (const id of ACTION_BUTTONS) assert.equal(element(id).disabled, false, id);
      assert.equal(element("panic-button").getAttribute("aria-busy"), "false");
    """,
    "apply_says_it_started_the_saved_settings": r"""
      element("admin-token").value = "fixture token";
      network = async () => ({ok: true, json: async () => ({
        state: {observed_state: "applied", running: true},
        applied: {devices: 2, groups: 1, service_lists: 0, protected: 1, general_profile: null}
      })});
      press("apply-button");
      await flush();
      assert.equal(networkCalls.length, 1);
      assert.equal(networkCalls[0].path, "/api/apply");
      assert.equal(
        refreshMessages[0],
        "Started: 2 devices with a profile · 1 group · 1 protected device stays untouched."
      );
      assert.equal(element("apply-button").textContent, "Re-apply saved settings");
    """,
    "apply_while_running_says_it_re_applied": r"""
      element("admin-token").value = "fixture token";
      lastRuntimeState = {observed_state: "applied", running: true};
      network = async () => ({ok: true, json: async () => ({
        state: {observed_state: "applied", running: true},
        applied: {devices: 0, groups: 0, service_lists: 0, protected: 0, general_profile: "Office"}
      })});
      press("apply-button");
      await flush();
      assert.equal(
        refreshMessages[0],
        'Re-applied: General profile "Office" for every unprotected device.'
      );
    """,
    "apply_without_a_summary_still_confirms": r"""
      element("admin-token").value = "fixture token";
      network = async () => ({ok: true, json: async () => ({
        state: {observed_state: "applied", running: true}
      })});
      press("apply-button");
      await flush();
      assert.equal(refreshMessages[0], "Started with your saved settings.");
    """,
    "the_apply_button_says_applying_until_the_request_ends": r"""
      element("admin-token").value = "fixture token";
      const pending = deferred();
      network = () => pending.promise;
      press("apply-button");
      await flush();
      assert.equal(element("apply-button").textContent, "Applying…");
      assert.equal(element("apply-button").getAttribute("aria-busy"), "true");
      assert.equal(messages.at(-1).text, "Applying your saved settings…");
      pending.resolve({ok: true, json: async () => ({
        state: {observed_state: "applied", running: true},
        applied: {devices: 1, groups: 0, service_lists: 0, protected: 0, general_profile: null}
      })});
      await flush();
      assert.equal(element("apply-button").textContent, "Re-apply saved settings");
      assert.equal(element("apply-button").getAttribute("aria-busy"), "false");
    """,
    "reset_and_panic_put_the_button_back_to_start": r"""
      element("admin-token").value = "fixture token";
      lastRuntimeState = {observed_state: "applied", running: true};
      network = async () => ({ok: true, json: async () => ({
        state: {observed_state: "reset", running: false}
      })});
      press("reset-button");
      await flush();
      assert.equal(refreshMessages[0], "Network reset successfully.");
      assert.equal(element("apply-button").textContent, "Start with saved settings");
    """,
    "apply_message_counts_the_service_lists_the_server_sent": r"""
      element("admin-token").value = "fixture token";
      network = async () => ({ok: true, json: async () => ({
        state: {observed_state: "applied", running: true},
        applied: {devices: 1, groups: 0, service_lists: 2, protected: 0, general_profile: null}
      })});
      press("apply-button");
      await flush();
      assert.equal(refreshMessages[0], "Started: 1 device with a profile · 2 service lists.");
    """,
    "a_warning_about_skipped_devices_follows_a_finished_sentence": r"""
      element("admin-token").value = "fixture token";
      network = async () => ({ok: true, json: async () => ({
        state: {observed_state: "applied", running: true},
        applied: {devices: 1, groups: 0, service_lists: 0, protected: 1, general_profile: null},
        warning: "Profiles were skipped for devices whose current Wi-Fi identity could not be verified: tv."
      })});
      press("apply-button");
      await flush();
      assert.equal(
        refreshMessages[0],
        "Started: 1 device with a profile · 1 protected device stays untouched. " +
        "Profiles were skipped for devices whose current Wi-Fi identity could not be verified: tv."
      );
    """,
    "a_failed_apply_shows_the_server_error_and_no_summary": r"""
      element("admin-token").value = "fixture token";
      network = async () => ({
        ok: false, status: 502, json: async () => ({
          error: "network helper apply failed", applied: {devices: 9}
        })
      });
      press("apply-button");
      await flush();
      assert.equal(messages.at(-1).kind, "error");
      assert.equal(refreshMessages.length, 0);
    """,
    "without_the_helper_the_status_says_it_is_needed_as_soon_as_the_mode_is_known": r"""
      lastRuntimeState = {observed_state: "unknown", running: false};
      helperKnown = false;
      helperConfigured = false;
      renderMode({helper_configured: false});
      assert.equal(element("runtime-summary").textContent, "Needs the Pi helper to run.");
      assert.equal(element("apply-button").disabled, true);
      renderMode({helper_configured: true});
      assert.equal(
        element("runtime-summary").textContent, "Not started. Choose Start with saved settings."
      );
    """,
    "locks_every_action_while_running": r"""
      element("admin-token").value = "fixture token";
      const pending = deferred();
      network = () => pending.promise;
      press("panic-button");
      await flush();
      for (const id of ACTION_BUTTONS) assert.equal(element(id).disabled, true, id);
      assert.equal(element("panic-button").getAttribute("aria-busy"), "true");
      press("panic-button");
      await flush();
      assert.equal(networkCalls.length, 1);
      pending.resolve({ok: true, json: async () => ({state: {observed_state: "reset"}})});
      await flush();
      for (const id of ACTION_BUTTONS) assert.equal(element(id).disabled, false, id);
      assert.equal(element("panic-button").getAttribute("aria-busy"), "false");
    """,
    "asks_for_the_admin_token": r"""
      press("panic-button");
      await flush();
      assert.equal(networkCalls.length, 0);
      assert.deepEqual(openedCredentials, ["admin"]);
      assert.equal(messages.at(-1).kind, "error");
      assert.match(messages.at(-1).text, /admin token/);
      assert.equal(refreshMessages.length, 0);
      for (const id of ACTION_BUTTONS) assert.equal(element(id).disabled, false, id);
    """,
    "reports_a_failed_reset_and_unlocks": r"""
      element("admin-token").value = "fixture token";
      network = async () => ({
        ok: false, status: 502, json: async () => ({error: "network helper reset failed"})
      });
      press("panic-button");
      await flush();
      assert.equal(networkCalls.length, 1);
      assert.equal(messages.at(-1).kind, "error");
      assert.equal(messages.at(-1).text, "network helper reset failed");
      assert.equal(refreshMessages.length, 0);
      assert.equal(runtimeStates.length, 0);
      assert.deepEqual(refreshedPanels, ["state"]);  // the page reads the state again
      for (const id of ACTION_BUTTONS) assert.equal(element(id).disabled, false, id);
    """,
    "unavailable_without_the_network_helper": r"""
      element("admin-token").value = "fixture token";
      renderMode({helper_configured: false});
      assert.equal(element("panic-button").disabled, true);
      press("panic-button");
      await flush();
      assert.equal(networkCalls.length, 0);
      element("panic-button").disabled = false;  // even a stale enabled button must not act
      press("panic-button");
      await flush();
      assert.equal(networkCalls.length, 0);
      assert.equal(messages.at(-1).kind, "error");
      assert.match(messages.at(-1).text, /configuration-only/);
      renderMode({helper_configured: true});
      assert.equal(element("panic-button").disabled, false);
    """,
}


def test_panic_button_stays_reachable_in_the_sidebar():
    dock = re.search(r'<aside class="sidebar".*?</aside>', DASHBOARD_HTML, re.DOTALL).group(0)
    button = re.search(r'<button[^>]*id="panic-button"[^>]*>', dock).group(0)
    assert 'type="button"' in button
    assert "danger" in button
    assert "Panic" in dock
    panel = re.search(
        r'<section id="credentials-panel".*?</section>', DASHBOARD_HTML, re.DOTALL
    ).group(0)
    assert "panic-button" not in panel
    assert DASHBOARD_HTML.count('id="panic-button"') == 1


@pytest.mark.parametrize("case", CASES)
def test_served_panic_button_behavior(case):
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
