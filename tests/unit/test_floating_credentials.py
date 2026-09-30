"""Exercise the served credential panel and its overlapping request states."""

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
        + block("let helperConfigured =", "let profiles =")
        + block("async function request(", "const adminTokenHelp =")
        + block("async function checkAdminToken()", "async function refresh()")
        + block(
            'element("admin-token").addEventListener("input",',
            'element("traffic-device").addEventListener("change",',
        )
        + block(
            'element("refresh-diagnostics").addEventListener',
            'element("download-backup").addEventListener',
        )
        + block("async function routerAction(", 'element("sync-router-ap").addEventListener')
    )


HARNESS = r"""
const assert = require("node:assert/strict");
const nodes = new Map();
const document = {activeElement: null, documentElement: {style: {setProperty() {}}},
                  addEventListener() {}};
const window = {innerHeight: 800, addEventListener() {}};
let pendingFormSubmitButton = null;
let toolResult = {ok: true, message: "accepted fixture token"};
let refreshCalls = 0;
let networkCalls = [];
let network = async () => ({ok: true, json: async () => ({ssid: "Fixture network"})});
function element(id) {
  if (!nodes.has(id)) nodes.set(id, {
    id, value: "", textContent: "", className: "", disabled: false,
    hidden: id === "credentials-panel", isConnected: true,
    attributes: new Map(), listeners: new Map(),
    setAttribute(key, value) {this.attributes.set(key, value)},
    removeAttribute(key) {this.attributes.delete(key)},
    getAttribute(key) {return this.attributes.get(key)},
    addEventListener(kind, callback) {this.listeners.set(kind, callback)},
    dispatchEvent(event) {this.listeners.get(event.type)?.(event)},
    focus() {document.activeElement = this}, scrollIntoView() {}
  });
  return nodes.get(id);
}
function setButtonBusy(button, busy) {button.disabled = busy}
function showMessage() {}
function stopTrafficSampling() {}
async function loadAdminTools() {return await toolResult}
async function refresh() {refreshCalls++}
async function loadTrafficSettings() {}
async function startTrafficSampling() {}
async function fetch(path, options) {networkCalls.push({path, options}); return await network()}
function deferred() {
  let resolve;
  const promise = new Promise(done => {resolve = done});
  return {promise, resolve};
}
function enter(id, value) {
  element(id).value = value;
  element(id).dispatchEvent(new Event("input"));
}
"""


CASES = {
    "panel_and_token_edits": r"""
      const opener = element("open-admin-credentials");
      enter("admin-token", "fixture token");
      openCredentials("admin", opener);
      assert.equal(element("credentials-panel").hidden, false);
      assert.equal(document.activeElement.id, "admin-token");
      assert.equal(opener.getAttribute("aria-expanded"), "true");
      closeCredentials();
      assert.equal(element("credentials-panel").hidden, true);
      assert.equal(document.activeElement, opener);
      assert.equal(element("admin-token").value, "fixture token");
      await checkAdminToken();
      assert.equal(adminTokenVerified, true);
      assert.equal(element("admin-credential-state").textContent, "Accepted");
      const pending = deferred();
      toolResult = pending.promise;
      const checking = checkAdminToken();
      enter("admin-token", "replacement token");
      pending.resolve({ok:true, message:"old accepted result"});
      await checking;
      assert.equal(adminTokenVerified, false);
      assert.equal(refreshCalls, 1);
      assert.ok(!element("token-check-status").textContent.includes("old accepted"));
      assert.equal(element("admin-credential-state").textContent, "Check needed");
      assert.equal(element("check-admin-token").disabled, false);
      enter("admin-token", "");
      await checkAdminToken();
      assert.equal(element("admin-credential-state").textContent, "Needed");
      assert.equal(element("credentials-panel").hidden, false);
      const outsideOpener = element("open-router-credentials");
      openCredentials("router", outsideOpener);
      element("check-admin-token").focus();
      await checkAdminToken();
      closeCredentials();
      assert.equal(document.activeElement, outsideOpener);
    """,
    "stale_diagnostic_status": r"""
      enter("admin-token", "fixture token");
      adminTokenVerified = true;
      const pending = deferred();
      toolResult = pending.promise;
      const running = element("refresh-diagnostics").listeners.get("click")({currentTarget:element("refresh-diagnostics")});
      enter("admin-token", "replacement token");
      pending.resolve({ok:true, message:"old diagnostics accepted"});
      await running;
      assert.ok(!element("token-check-status").textContent.includes("old diagnostics accepted"));
      assert.equal(element("refresh-diagnostics").disabled, false);
      assert.equal(adminTokenVerified, false);
      toolResult = {ok:false, message:"rejected fixture token"};
      await checkAdminToken();
      assert.equal(element("admin-credential-state").textContent, "Rejected");
      enter("admin-token", "another token");
      assert.equal(element("admin-credential-state").textContent, "Check needed");
    """,
    "stale_private_reads": r"""
      enter("admin-token", "fixture token");
      adminTokenVerified = true;
      const pending = deferred();
      network = () => pending.promise;
      const loading = show("/api/profiles", "profiles", "profiles");
      enter("admin-token", "replacement token");
      pending.resolve({ok:true, json:async () => ({profiles:["stale private profile"]})});
      await assert.rejects(loading, /Token changed/);
      assert.ok(!element("profiles").textContent.includes("stale private profile"));
      const count = networkCalls.length;
      await assert.rejects(request("/api/groups", "GET"), /Check token/);
      assert.equal(networkCalls.length, count);
      enter("admin-token", "");
      await assert.rejects(request("/api/profiles", "POST", {}), /admin token/);
      assert.equal(networkCalls.length, count);
      assert.equal(element("credentials-panel").hidden, false);
    """,
    "router_replacements_and_failure": r"""
      enter("admin-token", "fixture token");
      enter("router-admin-password", "fixture password");
      const pending = deferred();
      network = () => pending.promise;
      const running = routerAction("/api/router/sync-ap", {}, "Importing", element("sync-router-ap"));
      assert.equal(routerActionPending, true);
      for (const id of ["sync-router-ap", "disable-router-wifi", "enable-router-wifi"])
        assert.equal(element(id).disabled, true);
      await routerAction("/api/router/wifi", {enabled:true}, "Enabling", element("enable-router-wifi"));
      assert.equal(networkCalls.length, 1);
      enter("router-admin-password", "");
      enter("router-admin-password", "fixture password");
      pending.resolve({ok:true, json:async () => ({ssid:"Fixture network"})});
      await running;
      assert.equal(element("router-admin-password").value, "fixture password");
      assert.equal(element("router-credential-state").textContent, "Entered");
      network = async () => ({ok:false, status:502, json:async () => ({error:"fixture failure"})});
      await routerAction("/api/router/sync-ap", {}, "Importing", element("sync-router-ap"));
      assert.equal(element("router-admin-password").value, "");
      assert.equal(element("credential-router-status").textContent, "fixture failure");
      for (const id of ["sync-router-ap", "disable-router-wifi", "enable-router-wifi"])
        assert.equal(element(id).disabled, false);
      assert.equal(routerActionPending, false);
      await routerAction("/api/router/sync-ap", {}, "Importing", element("sync-router-ap"));
      assert.equal(document.activeElement.id, "router-admin-password");
      assert.equal(networkCalls.length, 2);
    """,
}


@pytest.mark.parametrize("case", CASES)
def test_served_credential_state_transitions(case):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise the served dashboard")
    program = (
        HARNESS
        + served_functions()
        + "\n(async () => {"
        + CASES[case]
        + "\n})().catch(error => {console.error(error); process.exitCode = 1});"
    )
    result = subprocess.run([node, "-"], input=program, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
