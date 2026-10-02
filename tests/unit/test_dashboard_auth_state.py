"""Exercise authentication states using the JavaScript served to the browser."""

import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML


def test_token_checks_and_edits_release_all_private_loading_states():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise the served dashboard")
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)

    def block(start, end):
        return script[script.index(start) : script.index(end, script.index(start))]

    functions = block("const privateDataPanelIds =", "function setDashboardUnlocked(")
    functions += block("function setPrivateDataPanels(", "async function loadPrivateDataPanel(")
    functions += block("async function checkAdminToken()", "async function refresh()")
    listener = block(
        'element("admin-token").addEventListener("input",',
        'element("traffic-device").addEventListener("change",',
    )
    harness = r"""
      const assert = require("node:assert/strict");
      const nodes = new Map();
      const listeners = new Map();
      let adminTokenVerified = false;
      let adminTokenRevision = 0;
      let adminTokenChecking = false;
      let adminTokenRejected = false;
      let refreshCalls = 0;
      let samplingStops = 0;
      let result = {ok: false, message: "rejected fixture token"};
      let failure = null;
      function element(id) {
        if (!nodes.has(id)) nodes.set(id, {
          value: "", textContent: "pending", className: "", disabled: false,
          attributes: new Map(),
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
      async function loadDashboardAccess() {return {allowed: true}}
      function setDashboardUnlocked() {}
      async function refresh() {
        refreshCalls++;
        for (const id of privateDataPanelIds) element(id).textContent = "fixture data";
      }
      async function loadTrafficSettings() {}
      async function startTrafficSampling() {}
    """
    checks = r"""
      function assertLocked() {
        assert.equal(adminTokenVerified, false);
        const message = element("profiles-json").textContent;
        assert.ok(message.length > 0);
        for (const id of ["devices-json", "plan", "state", "policies",
                          "connected-devices", "unaffected-devices", "uplink-details"])
          assert.equal(element(id).textContent, message, id);
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
      })().catch(error => {console.error(error); process.exitCode = 1});
    """
    result = subprocess.run(
        [node, "-"],
        input=harness + functions + listener + checks,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
