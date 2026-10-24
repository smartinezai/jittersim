"""Exercise authentication states using the JavaScript served to the browser."""

import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML


def run_token_case(checks):
    """Run `checks` in Node against the token check functions that the dashboard serves."""

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise the served dashboard")
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)

    def block(start, end):
        return script[script.index(start) : script.index(end, script.index(start))]

    functions = block("const privateDataPanelIds =", "function setDashboardUnlocked(")
    functions += block("function setPrivateDataPanels(", "async function loadPrivateDataPanel(")
    functions += block("function clearAdminToolsUi()", "async function loadRandomWebsiteAnalytics(")
    functions += block("async function checkAdminToken(", "async function refresh(")
    listener = block(
        'element("admin-token").addEventListener("input",',
        'element("traffic-device").addEventListener("change",',
    )
    harness = r"""
      const assert = require("node:assert/strict");
      const nodes = new Map();
      const listeners = new Map();
      const document = {createElement: tag => ({tagName: tag.toUpperCase()})};
      let randomWebsiteAnalyticsRequestVersion = 0;
      let adminTokenVerified = false;
      let adminTokenRevision = 0;
      let preparedLoaded = false, preparedBusy = false;
      let messageSerial = 0;  // showMessage is stubbed here so nothing else ever speaks
      let adminTokenChecking = false;
      let adminTokenRejected = false;
      let refreshCalls = 0;
      let samplingStops = 0;
      let result = {ok: false, message: "rejected fixture token"};
      let failure = null;
      let accessFailure = null;
      let accessHook = null;  // a case sets it to hold the read of the dashboard access
      function element(id) {
        if (!nodes.has(id)) nodes.set(id, {
          value: "", textContent: "pending", className: "", disabled: false,
          attributes: new Map(), children: [{textContent: "private result"}],
          replaceChildren() {this.children = []; this.textContent = ""},
          appendChild(child) {this.children.push(child)},
          focus() {},
          setAttribute(key, value) {this.attributes.set(key, value)},
          removeAttribute(key) {this.attributes.delete(key)},
          addEventListener(event, callback) {listeners.set(event, callback)}
        });
        return nodes.get(id);
      }
      function showMessage() {}
      function updateCredentialStatus() {}
      function renderSidebarStatus() {}
      function openCredentials() {}
      function stopTrafficSampling() {samplingStops++}
      async function loadAdminTools() {if (failure) throw failure; return result}
      async function loadDashboardAccess() {
        if (accessFailure) throw accessFailure;
        return (accessHook && await accessHook()) || {allowed: true};
      }
      function setDashboardUnlocked() {}
      async function refresh() {
        refreshCalls++;
        for (const id of privateDataPanelIds) element(id).textContent = "fixture data";
      }
      async function loadTrafficSettings() {}
      async function startTrafficSampling() {}
      async function loadPreparedChoices() {preparedLoaded = true;}
    """
    result = subprocess.run(
        [node, "-"],
        input=harness + functions + listener + checks,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    # Node exits quietly with code 0 if the case hangs on a promise that never settles.
    assert "CASE-COMPLETE" in result.stdout, "the case never finished"


def test_token_checks_and_edits_release_all_private_loading_states():
    checks = r"""
      function assertLocked() {
        assert.equal(adminTokenVerified, false);
        const message = element("profiles-json").textContent;
        assert.ok(message.length > 0);
        for (const id of ["devices-json", "plan", "state", "policies",
                          "connected-devices", "unaffected-devices", "uplink-details",
                          "diagnostics-json", "action-history", "random-website-status"])
          assert.equal(element(id).textContent, message, id);
        assert.equal(element("random-website-rows").children.length, 0);
        assert.deepEqual(element("random-website-device").children.map(child => child.textContent),
                         ["All devices"]);
        assert.equal(element("runtime-summary").textContent, message);
        assert.ok(element("runtime-summary").className.includes("unknown"));
        for (const id of ["connected-count", "uplink-state"])
          assert.ok(!element(id).textContent.includes("..."), id);
        assert.equal(element("check-admin-token").disabled, false);
        assert.equal(element("check-admin-token").attributes.has("aria-busy"), false);
      }
      (async () => {
        adminTokenVerified = true;
        await checkAdminToken();
        assertLocked();
        assert.equal(refreshCalls, 0);
        element("admin-token").value = "wrong fixture token";
        await checkAdminToken();
        assertLocked();
        assert.equal(refreshCalls, 0);
        result = {ok: true, message: "accepted fixture token"};
        element("admin-token").value = "valid fixture token";
        await checkAdminToken();
        assert.equal(adminTokenVerified, true);
        assert.equal(refreshCalls, 1);
        for (const id of privateDataPanelIds)
          assert.equal(element(id).textContent, "fixture data");
        listeners.get("input")();
        assertLocked();
        assert.equal(samplingStops, 1);
        adminTokenVerified = true;
        failure = new Error("fixture connection failure");
        await checkAdminToken();
        assertLocked();
        assert.equal(refreshCalls, 1);
        failure = null;
        accessFailure = Object.assign(new Error("Device verification is busy. Try again."),
          {status: 409, code: "helper-busy"});
        element("admin-token").value = "valid fixture token";
        await checkAdminToken();
        assert.equal(adminTokenVerified, false);
        assert.equal(adminTokenRejected, false);
        assert.equal(adminTokenRefused, false);
        assert.equal(element("admin-token").value, "valid fixture token");
        assert.match(element("token-check-status").textContent, /Your token is kept/);
        assert.equal(element("check-admin-token").disabled, false);
        console.log("CASE-COMPLETE");
      })().catch(error => {console.error(error); process.exitCode = 1});
    """
    run_token_case(checks)


def test_a_token_edit_starts_a_new_check_while_an_older_one_hangs():
    """A check that hangs (the Pi does not answer) must not hold up the check of an edited token.
    It used to queue the new check behind the hung one, with no word on the page until the old
    one gave up. The old check is dead once the token changed: what it finds is dropped."""

    checks = r"""
      const flush = () => new Promise(resolve => setTimeout(resolve, 0));
      const answers = [];  // the first two reads of the dashboard access, answered by the case
      let reads = 0;
      accessHook = () => ++reads <= 2 ? new Promise(resolve => answers.push(resolve)) : null;
      (async () => {
        result = {ok: true, message: "accepted fixture token"};
        element("admin-token").value = "first fixture token";
        const stale = checkAdminToken();
        await flush();
        assert.equal(reads, 1);
        // A second call for the same token waits for the first one, as before.
        const queued = checkAdminToken();
        await flush();
        assert.equal(reads, 1, "a call for the same token must wait");
        // The token is edited while the first check still waits for the Pi.
        element("admin-token").value = "second fixture token";
        listeners.get("input")();
        const fresh = checkAdminToken();
        await flush();
        assert.equal(reads, 2, "the check of the new token waited for the one that hangs");
        assert.equal(adminTokenChecking, true);
        assert.equal(element("token-check-status").textContent,
                     "Checking the token and loading diagnostics...");
        // The old check answers late, with an answer that would lock the dashboard. It changes
        // nothing: the new check is still running and keeps its flags and its words.
        answers[0]({allowed: false, can_register: true});
        await stale;
        assert.equal(adminTokenChecking, true, "the old check ended the new one's checking state");
        assert.equal(element("check-admin-token").disabled, true);
        assert.equal(element("check-admin-token").attributes.get("aria-busy"), "true");
        assert.equal(element("token-check-status").textContent,
                     "Checking the token and loading diagnostics...");
        assert.equal(refreshCalls, 0);
        answers[1]();
        await fresh;
        assert.equal(adminTokenVerified, true);
        assert.equal(adminTokenChecking, false);
        assert.equal(refreshCalls, 1);
        assert.equal(element("token-check-status").textContent, "accepted fixture token");
        // The call that waited behind the old check is covered by the new one: no third check.
        await queued;
        assert.equal(reads, 2, "the call that waited started a third check");
        assert.equal(refreshCalls, 1);
        console.log("CASE-COMPLETE");
      })().catch(error => {console.error(error); process.exitCode = 1});
    """
    run_token_case(checks)
