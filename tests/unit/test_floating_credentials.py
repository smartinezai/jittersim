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
        block("const privateDataPanelIds =", "function setDashboardUnlocked(")
        + block("function setPrivateDataPanels(", "async function loadPrivateDataPanel(")
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
        + block("function renderRouterWifi()", 'element("sync-router-ap").addEventListener')
    )


HARNESS = r"""
const assert = require("node:assert/strict");
const nodes = new Map();
const messages = [];
const document = {activeElement: null, documentElement: {style: {setProperty() {}}},
                  addEventListener() {}};
const storage = new Map();
let storageBroken = false;
const blocked = () => {if (storageBroken) throw new Error("storage blocked")};
const windowListeners = new Map();
const window = {innerHeight: 800, setInterval() {}, confirm() {return true},
  addEventListener(type, callback) {windowListeners.set(type, callback)}, localStorage: {
  getItem(key) {blocked(); return storage.has(key) ? storage.get(key) : null},
  setItem(key, value) {blocked(); storage.set(key, String(value))},
  removeItem(key) {blocked(); storage.delete(key)}
}};
let clock = 1700000000000;
Date.now = () => clock;
const DAY = 24 * 60 * 60 * 1000;
const ADMIN_KEY = "jittersim.remembered.admin-token";
const ROUTER_KEY = "jittersim.remembered.router-password";
let pendingFormSubmitButton = null;
let toolResult = {ok: true, message: "accepted fixture token"};
let refreshCalls = 0;
let networkCalls = [];
let network = async () => ({ok: true, json: async () => ({result: {ssid: "Fixture network", security: "WPA2", band: "2.4 GHz", fingerprint: "a".repeat(64), legacy: false}})});
function element(id) {
  if (!nodes.has(id)) nodes.set(id, {
    id, value: "", textContent: "", className: "", disabled: false, checked: false,
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
function showMessage(text, kind) {messages.push({text, kind})}
function stopTrafficSampling() {}
function renderSidebarStatus() {}
async function loadAdminTools() {return await toolResult}
async function loadDashboardAccess() {return {allowed: true}}
function setDashboardUnlocked() {}
async function refresh() {refreshCalls++}
async function loadTrafficSettings() {}
async function startTrafficSampling() {}
async function fetch(path, options) {networkCalls.push({path, options}); return await network()}
function deferred() {
  let resolve;
  const promise = new Promise(done => {resolve = done});
  return {promise, resolve};
}
const ROUTER_BUTTONS = ["check-router-password", "sync-router-ap", "router-wifi-toggle"];
function tick(id, on) {
  element(id).checked = on;
  element(id).dispatchEvent(new Event("change"));
}
function enter(id, value) {
  element(id).value = value;
  element(id).dispatchEvent(new Event("input"));
}
"""


CASES = {
    "router_switch_tracks_confirmed_state_and_expires": r"""
      helperConfigured = true;
      enter("admin-token", "fixture token");
      adminTokenVerified = true;
      enter("router-admin-password", "fixture password");
      network = async () => ({ok:true, json:async () => ({result: {
        enabled:false, checked_at:clock/1000, busy:false, changing:false, retry_after:15}})});
      await pollRouterWifi();
      assert.equal(element("router-wifi-toggle").disabled, false);
      assert.equal(element("router-wifi-toggle").getAttribute("aria-checked"), "false");
      assert.match(element("router-wifi-status").textContent, /^Off/);
      clock += 46000;
      renderRouterWifi();
      assert.equal(element("router-wifi-toggle").disabled, true);
      assert.equal(element("router-wifi-toggle").getAttribute("data-unknown"), "true");
    """,
    "router_status_rejection_stops_automatic_checks": r"""
      helperConfigured = true;
      enter("admin-token", "fixture token");
      adminTokenVerified = true;
      enter("router-admin-password", "fixture password");
      network = async () => ({ok:true, json:async () => ({result: {
        enabled:null, busy:false, error:"Router login failed. Check the router admin password and try again."}})});
      await pollRouterWifi();
      assert.equal(routerPasswordRejected, true);
      await pollRouterWifi();
      assert.equal(networkCalls.length, 1);
      assert.equal(element("router-wifi-toggle").disabled, true);
    """,
    "router_status_ignores_replaced_credentials": r"""
      helperConfigured = true;
      enter("admin-token", "fixture token");
      adminTokenVerified = true;
      enter("router-admin-password", "fixture password");
      const pending = deferred();
      network = () => pending.promise;
      const polling = pollRouterWifi();
      enter("router-admin-password", "replacement password");
      pending.resolve({ok:true, json:async () => ({result: {enabled:true, checked_at:clock/1000, busy:false}})});
      await polling;
      assert.equal(routerWifiEnabled, null);
      assert.equal(routerPasswordVerified, false);
      assert.equal(element("router-wifi-toggle").disabled, true);
    """,

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
      for (const id of ["sync-router-ap", "router-wifi-toggle"])
        assert.equal(element(id).disabled, true);
      await routerAction("/api/router/wifi", {enabled:true}, "Enabling", element("router-wifi-toggle"));
      assert.equal(networkCalls.length, 1);
      enter("router-admin-password", "");
      enter("router-admin-password", "fixture password");
      pending.resolve({ok:true, json:async () => ({ssid:"Fixture network"})});
      await running;
      assert.equal(element("router-admin-password").value, "fixture password");
      assert.equal(element("router-credential-state").textContent, "Entered");
      network = async () => ({ok:false, status:502, json:async () => ({error:"fixture failure"})});
      await routerAction("/api/router/sync-ap", {}, "Importing", element("sync-router-ap"));
      assert.equal(element("router-admin-password").value, "fixture password");
      assert.equal(element("credential-router-status").textContent, "fixture failure");
      assert.equal(element("sync-router-ap").disabled, false);
      assert.equal(element("router-wifi-toggle").disabled, true);
      assert.equal(routerActionPending, false);
      enter("router-admin-password", "");
      await routerAction("/api/router/sync-ap", {}, "Importing", element("sync-router-ap"));
      assert.equal(document.activeElement.id, "router-admin-password");
      assert.equal(networkCalls.length, 2);
    """,
    "router_password_check_accepts_and_keeps_the_password": r"""
      assert.equal(element("check-router-password").listeners.get("click"), checkRouterPassword);
      enter("admin-token", "fixture token");
      enter("router-admin-password", "fixture password");
      const checked = {result: {ok: true, action: "router-check"}};
      network = async () => ({ok:true, json:async () => checked});
      await checkRouterPassword();
      assert.equal(networkCalls.length, 1);
      assert.equal(networkCalls[0].path, "/api/router/check");
      assert.equal(networkCalls[0].options.method, "POST");
      assert.equal(networkCalls[0].options.headers.Authorization, "Bearer fixture token");
      assert.deepEqual(
        JSON.parse(networkCalls[0].options.body), {admin_password:"fixture password", background:true});
      assert.equal(element("router-credential-state").textContent, "Accepted");
      assert.equal(element("router-credential-state").className, "credential-state accepted");
      assert.match(element("credential-router-status").textContent, /accepted.*nothing.*changed/i);
      assert.equal(messages.at(-1).kind, "success");
      assert.equal(element("router-admin-password").value, "fixture password");
      assert.equal(element("check-router-password").disabled, false);
      assert.equal(element("check-router-password").textContent, "Check password");
      enter("router-admin-password", "edited password");
      assert.equal(element("router-credential-state").textContent, "Entered");
      assert.equal(element("router-credential-state").className, "credential-state");
    """,
    "router_password_check_rejection_and_other_failures": r"""
      enter("admin-token", "fixture token");
      enter("router-admin-password", "fixture password");
      const loginFailed = "Router login failed. Check the router admin password and try again.";
      network = async () => ({ok:false, status:502, json:async () => ({error:loginFailed})});
      await checkRouterPassword();
      assert.equal(element("router-credential-state").textContent, "Rejected");
      assert.equal(element("credential-router-status").textContent, loginFailed);
      assert.equal(messages.at(-1).kind, "error");
      assert.equal(element("router-admin-password").value, "fixture password");
      enter("router-admin-password", "another password");
      assert.equal(element("router-credential-state").textContent, "Entered");
      for (const [status, error] of [[502, "Could not contact the router. Try again."],
                                     [503, "network helper is not configured"]]) {
        network = async () => ({ok:false, status, json:async () => ({error})});
        await checkRouterPassword();
        assert.equal(element("router-credential-state").textContent, "Entered", error);
        assert.equal(element("credential-router-status").textContent, error);
        assert.equal(element("check-router-password").disabled, false);
      }
    """,
    "router_password_check_needs_both_credentials": r"""
      enter("router-admin-password", "fixture password");
      await checkRouterPassword();
      assert.equal(networkCalls.length, 0);
      assert.equal(document.activeElement.id, "admin-token");
      assert.match(messages.at(-1).text, /admin token/);
      closeCredentials();
      enter("admin-token", "fixture token");
      enter("router-admin-password", "");
      await checkRouterPassword();
      assert.equal(networkCalls.length, 0);
      assert.equal(document.activeElement.id, "router-admin-password");
      assert.equal(
        element("credential-router-status").textContent, "Enter the router admin password first.");
      assert.equal(element("check-router-password").disabled, false);
    """,
    "router_check_runs_alone_and_ignores_stale_results": r"""
      enter("admin-token", "fixture token");
      enter("router-admin-password", "fixture password");
      const pending = deferred();
      network = () => pending.promise;
      const checking = checkRouterPassword();
      assert.equal(element("router-credential-state").textContent, "Checking...");
      for (const id of ROUTER_BUTTONS)
        assert.equal(element(id).disabled, true, id);
      const blockedAction = routerAction(
        "/api/router/wifi", {enabled:true}, "Enabling", element("router-wifi-toggle"));
      const blockedCheck = checkRouterPassword();
      await new Promise(resolve => setTimeout(resolve, 0));
      assert.equal(networkCalls.length, 1);
      enter("router-admin-password", "replacement password");
      pending.resolve({ok:true, json:async () => ({result:{ok:true}})});
      await checking;
      assert.equal(element("router-credential-state").textContent, "Entered");
      assert.ok(!/accepted/i.test(element("credential-router-status").textContent));
      for (const id of ROUTER_BUTTONS)
        assert.equal(element(id).disabled, id === "router-wifi-toggle", id);
    """,
    "router_operations_report_the_password_state_and_keep_it": r"""
      enter("admin-token", "fixture token");
      enter("router-admin-password", "fixture password");
      await routerAction("/api/router/sync-ap", {}, "Importing", element("sync-router-ap"));
      assert.equal(element("router-admin-password").value, "fixture password");
      assert.equal(element("router-credential-state").textContent, "Accepted");
      const loginFailed = "Router login failed. Check the router admin password and try again.";
      network = async () => ({ok:false, status:502, json:async () => ({error:loginFailed})});
      await routerAction("/api/router/sync-ap", {}, "Importing", element("sync-router-ap"));
      assert.equal(element("router-admin-password").value, "fixture password");
      assert.equal(element("router-credential-state").textContent, "Rejected");
      const unreachable = {error: "Could not contact the router."};
      network = async () => ({ok:false, status:502, json:async () => unreachable});
      enter("router-admin-password", "fixture password");
      await routerAction("/api/router/sync-ap", {}, "Importing", element("sync-router-ap"));
      assert.equal(element("router-credential-state").textContent, "Entered");
    """,
    "remembers_an_accepted_token_only_when_ticked": r"""
      enter("admin-token", "fixture token");
      await checkAdminToken();
      assert.equal(storage.size, 0);
      tick("remember-admin-token", true);
      assert.deepEqual(
        JSON.parse(storage.get(ADMIN_KEY)), {value: "fixture token", expires: clock + DAY});
      assert.match(
        element("remember-admin-token-note").textContent, /^Remembered until .*\d{2}:\d{2}/);
      tick("remember-admin-token", false);
      assert.equal(storage.size, 0);
      assert.equal(element("remember-admin-token-note").textContent, "");
    """,
    "waits_for_acceptance_and_replaces_the_saved_token": r"""
      tick("remember-admin-token", true);
      enter("admin-token", "first token");
      assert.equal(storage.size, 0);
      assert.match(element("remember-admin-token-note").textContent, /once accepted/);
      await checkAdminToken();
      assert.equal(JSON.parse(storage.get(ADMIN_KEY)).value, "first token");
      clock += 1000;
      enter("admin-token", "second token");
      assert.equal(JSON.parse(storage.get(ADMIN_KEY)).value, "first token");
      assert.match(element("remember-admin-token-note").textContent, /once accepted/);
      await checkAdminToken();
      assert.deepEqual(
        JSON.parse(storage.get(ADMIN_KEY)), {value: "second token", expires: clock + DAY});
    """,
    "a_rejected_token_is_not_kept": r"""
      tick("remember-admin-token", true);
      enter("admin-token", "good token");
      await checkAdminToken();
      assert.ok(storage.has(ADMIN_KEY));
      toolResult = {ok: false, message: "rejected fixture token", status: 401};
      await checkAdminToken();
      assert.equal(storage.has(ADMIN_KEY), false);
      assert.match(element("remember-admin-token-note").textContent, /rejected/);
      assert.equal(element("remember-admin-token").checked, true);
    """,
    "clearing_a_field_forgets_it": r"""
      tick("remember-admin-token", true);
      enter("admin-token", "fixture token");
      await checkAdminToken();
      assert.ok(storage.has(ADMIN_KEY));
      element("clear-admin-token").listeners.get("click")();
      assert.equal(element("admin-token").value, "");
      assert.equal(storage.has(ADMIN_KEY), false);
    """,
    "load_restores_both_but_checks_only_the_admin_token": r"""
      storage.set(ADMIN_KEY, JSON.stringify({value: "saved token", expires: clock + DAY}));
      storage.set(ROUTER_KEY, JSON.stringify({value: "saved password", expires: clock + DAY}));
      await restoreRememberedCredentials();
      assert.equal(element("admin-token").value, "saved token");
      assert.equal(element("remember-admin-token").checked, true);
      assert.equal(adminTokenVerified, true);
      assert.equal(refreshCalls, 1);
      assert.equal(element("router-admin-password").value, "saved password");
      assert.equal(element("remember-router-password").checked, true);
      assert.equal(element("router-credential-state").textContent, "Entered");
      assert.equal(networkCalls.length, 0);
      assert.equal(storage.size, 2);
    """,
    "startup_leaves_saved_credentials_alone_until_they_are_restored": r"""
      rememberedCredentialsReady = false;  // the page is still starting
      storage.set(ADMIN_KEY, JSON.stringify({value: "saved token", expires: clock + DAY}));
      updateCredentialStatus();
      syncRememberedCredentials();
      assert.equal(storage.size, 1);
      await restoreRememberedCredentials();
      assert.equal(element("admin-token").value, "saved token");
      assert.equal(storage.size, 1);
    """,
    "a_saved_token_that_is_now_rejected_is_forgotten": r"""
      storage.set(ADMIN_KEY, JSON.stringify({value: "stale token", expires: clock + DAY}));
      toolResult = {ok: false, message: "rejected fixture token", status: 401};
      await restoreRememberedCredentials();
      assert.equal(element("admin-credential-state").textContent, "Rejected");
      assert.equal(storage.has(ADMIN_KEY), false);
    """,
    "expired_or_damaged_entries_are_dropped_on_load": r"""
      storage.set(ADMIN_KEY, JSON.stringify({value: "old token", expires: clock - 1}));
      storage.set(ROUTER_KEY, "not json {");
      await restoreRememberedCredentials();
      assert.equal(element("admin-token").value, "");
      assert.equal(element("router-admin-password").value, "");
      assert.equal(element("remember-admin-token").checked, false);
      assert.equal(element("remember-router-password").checked, false);
      assert.equal(storage.size, 0);
      assert.equal(refreshCalls, 0);
      assert.match(element("remember-admin-token-note").textContent, /after 24 hours/);
      assert.equal(element("remember-router-password-note").textContent, "");
    """,
    "an_open_page_forgets_a_credential_after_a_day": r"""
      tick("remember-admin-token", true);
      enter("admin-token", "fixture token");
      await checkAdminToken();
      clock += DAY - 1;
      syncRememberedCredentials();
      assert.ok(storage.has(ADMIN_KEY));
      clock += 2;
      syncRememberedCredentials();
      assert.equal(storage.has(ADMIN_KEY), false);
      assert.equal(element("remember-admin-token").checked, false);
      assert.match(element("remember-admin-token-note").textContent, /after 24 hours/);
      updateCredentialStatus();
      assert.equal(storage.size, 0);
      assert.equal(element("admin-token").value, "fixture token");
    """,
    "router_password_is_kept_once_checked_and_dropped_when_refused": r"""
      enter("admin-token", "fixture token");
      enter("router-admin-password", "fixture password");
      tick("remember-router-password", true);
      assert.equal(storage.size, 0);
      network = async () => ({ok: true, json: async () => ({result: {ok: true}})});
      await checkRouterPassword();
      assert.deepEqual(
        JSON.parse(storage.get(ROUTER_KEY)), {value: "fixture password", expires: clock + DAY});
      const refused = "Router login failed. Check the router admin password and try again.";
      network = async () => ({ok: false, status: 502, json: async () => ({error: refused})});
      await routerAction("/api/router/sync-ap", {}, "Importing", element("sync-router-ap"));
      assert.equal(storage.has(ROUTER_KEY), false);
    """,
    "a_successful_router_operation_also_keeps_the_password": r"""
      enter("admin-token", "fixture token");
      enter("router-admin-password", "fixture password");
      tick("remember-router-password", true);
      await routerAction("/api/router/sync-ap", {}, "Importing", element("sync-router-ap"));
      assert.equal(JSON.parse(storage.get(ROUTER_KEY)).value, "fixture password");
    """,
    "blocked_browser_storage_is_reported_and_harmless": r"""
      storageBroken = true;
      enter("admin-token", "fixture token");
      await checkAdminToken();
      tick("remember-admin-token", true);
      assert.equal(adminTokenVerified, true);
      assert.match(element("remember-admin-token-note").textContent, /blocked/);
      await restoreRememberedCredentials();
      assert.equal(element("admin-token").value, "fixture token");
      assert.equal(storage.size, 0);
    """,
    "a_failed_check_that_is_not_a_refusal_keeps_the_saved_token": r"""
      tick("remember-admin-token", true);
      enter("admin-token", "good token");
      await checkAdminToken();
      assert.ok(storage.has(ADMIN_KEY));
      for (const failure of [{ok: false, message: "Failed to fetch"},
                             {ok: false, message: "service unavailable", status: 503}]) {
        toolResult = failure;
        await checkAdminToken();
        assert.equal(JSON.parse(storage.get(ADMIN_KEY)).value, "good token", failure.message);
        assert.match(element("remember-admin-token-note").textContent, /^Remembered until/);
      }
      toolResult = {ok: false, message: "unauthorized", status: 401};
      await checkAdminToken();
      assert.equal(storage.has(ADMIN_KEY), false);
    """,
    "a_refusal_removes_only_the_entry_it_refers_to": r"""
      storage.set(ADMIN_KEY, JSON.stringify({value: "other tab token", expires: clock + DAY}));
      tick("remember-admin-token", true);
      enter("admin-token", "wrong token");
      toolResult = {ok: false, message: "unauthorized", status: 401};
      await checkAdminToken();
      assert.equal(JSON.parse(storage.get(ADMIN_KEY)).value, "other tab token");
      storage.set(ADMIN_KEY, JSON.stringify({value: "wrong token", expires: clock + DAY}));
      await checkAdminToken();
      assert.equal(storage.has(ADMIN_KEY), false);
    """,
    "unticking_after_a_restore_removes_the_entry": r"""
      storage.set(ADMIN_KEY, JSON.stringify({value: "saved token", expires: clock + DAY}));
      storage.set(ROUTER_KEY, JSON.stringify({value: "saved password", expires: clock + DAY}));
      await restoreRememberedCredentials();
      tick("remember-admin-token", false);
      assert.equal(storage.has(ADMIN_KEY), false);
      assert.ok(storage.has(ROUTER_KEY));
      element("clear-router-password").listeners.get("click")();
      assert.equal(storage.size, 0);
    """,
    "a_tab_that_saved_nothing_never_deletes_another_tabs_entry": r"""
      storage.set(ADMIN_KEY, JSON.stringify({value: "other tab token", expires: clock + DAY}));
      enter("admin-token", "typed here");
      enter("admin-token", "");
      syncRememberedCredentials();
      updateCredentialStatus();
      assert.equal(JSON.parse(storage.get(ADMIN_KEY)).value, "other tab token");
    """,
    "unticking_removes_only_the_entry_this_tab_saved": r"""
      tick("remember-admin-token", true);
      enter("admin-token", "my token");
      await checkAdminToken();
      storage.set(ADMIN_KEY, JSON.stringify({value: "other tab token", expires: clock + DAY}));
      tick("remember-admin-token", false);
      assert.equal(JSON.parse(storage.get(ADMIN_KEY)).value, "other tab token");
    """,
    "forgetting_in_another_tab_unticks_this_one_and_stays_forgotten": r"""
      tick("remember-admin-token", true);
      enter("admin-token", "fixture token");
      await checkAdminToken();
      assert.ok(storage.has(ADMIN_KEY));
      storage.delete(ADMIN_KEY);
      windowListeners.get("storage")({key: ADMIN_KEY, newValue: null});
      assert.equal(element("remember-admin-token").checked, false);
      syncRememberedCredentials();
      updateCredentialStatus();
      assert.equal(storage.size, 0);
      assert.equal(element("admin-token").value, "fixture token");
      enter("router-admin-password", "fixture password");
      tick("remember-router-password", true);
      await routerAction("/api/router/sync-ap", {}, "Importing", element("sync-router-ap"));
      assert.ok(storage.has(ROUTER_KEY));
      storage.clear();
      windowListeners.get("storage")({key: null, newValue: null});
      assert.equal(element("remember-router-password").checked, false);
      syncRememberedCredentials();
      assert.equal(storage.size, 0);
    """,
    "unrelated_storage_changes_leave_the_boxes_alone": r"""
      tick("remember-admin-token", true);
      enter("admin-token", "fixture token");
      await checkAdminToken();
      windowListeners.get("storage")({key: "something-else", newValue: null});
      windowListeners.get("storage")({key: ADMIN_KEY, newValue: "{}"});
      assert.equal(element("remember-admin-token").checked, true);
      assert.ok(storage.has(ADMIN_KEY));
    """,
    "a_rejected_router_password_is_not_sent_again_until_it_changes": r"""
      enter("admin-token", "fixture token");
      enter("router-admin-password", "typo password");
      const refused = "Router login failed. Check the router admin password and try again.";
      network = async () => ({ok: false, status: 502, json: async () => ({error: refused})});
      await checkRouterPassword();
      assert.equal(networkCalls.length, 1);
      assert.equal(element("router-credential-state").textContent, "Rejected");
      await checkRouterPassword();
      await routerAction("/api/router/sync-ap", {}, "Importing", element("sync-router-ap"));
      await routerAction(
        "/api/router/wifi", {enabled: true}, "Enabling", element("router-wifi-toggle"));
      assert.equal(networkCalls.length, 1);
      assert.match(messages.at(-1).text, /rejected.*change/i);
      assert.equal(messages.at(-1).kind, "error");
      assert.equal(document.activeElement.id, "router-admin-password");
      assert.equal(element("check-router-password").disabled, false);
      enter("router-admin-password", "corrected password");
      network = async () => ({ok: true, json: async () => ({result: {ok: true}})});
      await checkRouterPassword();
      assert.equal(networkCalls.length, 2);
      assert.equal(element("router-credential-state").textContent, "Accepted");
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
        + "\nrememberedCredentialsReady = true;  // the page has finished starting"
        + "\n(async () => {"
        + CASES[case]
        + "\nconsole.log('CASE-COMPLETE');"
        + "\n})().catch(error => {console.error(error); process.exitCode = 1});"
    )
    result = subprocess.run([node, "-"], input=program, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    # Node exits quietly with code 0 if a case hangs on a promise that never settles.
    assert "CASE-COMPLETE" in result.stdout, "the case never finished"


def test_router_password_check_button_sits_with_the_password_field():
    panel = re.search(
        r'<section id="credentials-panel".*?</section>', DASHBOARD_HTML, re.DOTALL
    ).group(0)
    router = panel[panel.index("<h3>Router password</h3>") :]
    button = re.search(
        r'<button[^>]*id="check-router-password"[^>]*>[^<]*</button>', router
    ).group(0)
    assert 'type="button"' in button
    assert "Check password" in button
    assert router.index('id="check-router-password"') < router.index('id="clear-router-password"')
    assert "Nothing on the router is changed" in router
    assert "Used for one router operation" not in router
    assert DASHBOARD_HTML.count('id="check-router-password"') == 1


def test_remember_choices_are_offered_for_both_credentials_and_start_off():
    panel = re.search(
        r'<section id="credentials-panel".*?</section>', DASHBOARD_HTML, re.DOTALL
    ).group(0)
    for name in ("admin-token", "router-password"):
        box = re.search(rf'<input[^>]*id="remember-{name}"[^>]*>', panel).group(0)
        assert 'type="checkbox"' in box
        assert "checked" not in box
        assert re.search(
            rf'<label[^>]*>\s*<input[^>]*id="remember-{name}"[^>]*>\s*Remember for 24 hours',
            panel,
        )
        note = re.search(rf'<p[^>]*id="remember-{name}-note"[^>]*>', panel).group(0)
        assert 'role="status"' in note
    assert "Kept in this page only unless you choose Remember" in panel


def test_remembered_credentials_are_restored_once_at_the_end_of_startup():
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
    assert script.count("restoreRememberedCredentials();") == 1
    assert "restoreRememberedCredentials();" in script[script.rindex("loadHealth()") :]
    assert "window.setInterval(syncRememberedCredentials" in script


def test_diagnostics_failures_report_their_http_status():
    """Only a 401 may count as a refused token; other failures must be told apart."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise the served dashboard")
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
    real = script[
        script.index("async function loadAdminTools()") : script.index(
            "async function checkAdminToken()"
        )
    ]
    program = (
        r"""
const assert = require("node:assert/strict");
const nodes = new Map();
let randomWebsiteAnalyticsRequestVersion = 0;
let failure = null;
function element(id) {
  if (!nodes.has(id)) nodes.set(id, {value: "fixture token", textContent: "", className: "",
                                     replaceChildren() {}});
  return nodes.get(id);
}
async function request() {throw failure}
async function loadRandomWebsiteAnalytics() {}
"""
        + real
        + r"""
(async () => {
  failure = Object.assign(new Error("unauthorized"), {status: 401});
  assert.deepEqual(await loadAdminTools(), {ok: false, message: "unauthorized", status: 401});
  failure = new Error("Failed to fetch");
  const network = await loadAdminTools();
  assert.equal(network.ok, false);
  assert.equal(network.status, undefined);
  console.log("CASE-COMPLETE");
})().catch(error => {console.error(error); process.exitCode = 1});
"""
    )
    result = subprocess.run([node, "-"], input=program, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "CASE-COMPLETE" in result.stdout


def test_login_failure_wording_matches_what_the_dashboard_recognises():
    from jittersim.networking.router import (
        ROUTER_CHECK_MESSAGES,
        ROUTER_IMPORT_MESSAGES,
        ROUTER_WIFI_MESSAGES,
    )

    for table in (ROUTER_CHECK_MESSAGES, ROUTER_IMPORT_MESSAGES, ROUTER_WIFI_MESSAGES):
        assert table["login"].startswith("Router login failed")
    assert "/^Router login failed/" in DASHBOARD_HTML
