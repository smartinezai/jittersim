"""A refresh started with an older token cannot reject a new accepted check."""

import re

import pytest

from jittersim.api.server import DASHBOARD_HTML
from tests.unit.test_dashboard_auth_state import run_token_case


SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)


def block(start, end):
    first = SCRIPT.index(start)
    return SCRIPT[first:SCRIPT.index(end, first)]


# Use the served refresh, request and refreshDashboard bodies. Only the leaf readers
# unrelated to the controlled health response are replaced; no browser is required.
LOADERS = (
    "loadProfiles loadObservations loadDevicesForSchedule loadState loadGeneralProfile "
    "loadGroups loadSchedules loadTimers loadScenarios loadActivity loadProgressions "
    "loadPolicies loadConnectedDevices loadUnaffectedDevices loadUplink loadGatewayPersistence "
    "loadQuotas loadServiceAssignments loadServiceLists loadConditions loadEveryoneElse "
    "loadDashboardAccessList loadTestSessions loadSettingsHistory loadWorkspace"
).split()

HARNESS = r"""
let dashboardRefresh = null;
let dashboardRefreshRevision = -1;
let dashboardPoll = null;
let refreshAgain = false;
let dashboardLoaded = false;
let pendingFormSubmitButton = null;
let adminToolsStale = false;
const ADMIN_TOOLS_PAGES = [];
const recentWrites = new Set();
const uncertainWritePaths = new Set();
const unansweredPaths = new Set();
const replies = [];
const responses = [];
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}
const flush = () => new Promise(resolve => setImmediate(resolve));
function reply(status = 200, data = {}) {
  return {ok: status < 400, status, json: async () => data};
}
async function fetch(path) {
  const response = replies.length ? await replies.shift() : reply();
  responses.push({path, status: response.status});
  return response;
}
function currentPage() { return "devices"; }
function pagePollJobs() { return []; }
function renderGettingStarted() {}
function populateWorkflowMenus() {}
function populateAccessTargets() {}
function restoreDrafts() {}
function invalidateAdminTools() {}
function renderMode() { refreshCalls++; }
function renderSystemHealth() {}
function notePiAnswered() {}
function requestTimeoutMs() { return 1000; }
function setDashboardUnlocked(allowed) { element("private-dashboard").hidden = !allowed; }
async function loadDashboardAccess() { setDashboardUnlocked(true); return {allowed: true}; }
async function loadPrivateDataPanel(id, load) { return load(); }
async function show() {}
async function readAdminToolsOnce() { return {ok: true}; }
"""


def run_refresh_case(checks):
    functions = block("async function refresh(", "// Panic can start")
    functions += block("async function request(path,", "async function show(")
    functions += block("async function refreshDashboard(", "function readOldAdminTools(")
    functions += block("async function loadHealth()", "let profilesRequestRevision")
    stubs = "\n".join(f"async function {name}() {{}}" for name in LOADERS)
    run_token_case(HARNESS + stubs + functions + checks)


@pytest.mark.parametrize("old_status", [200, 401, 403])
def test_a_new_accepted_check_does_not_inherit_an_old_refresh_error(old_status):
    run_refresh_case(r"""
      (async () => {
        result = {ok: true, message: "Token accepted. Diagnostics loaded."};
        element("admin-token").value = "first fixture token";
        const oldReply = deferred();
        replies.push(oldReply.promise);
        const older = checkAdminToken();
        await flush();
        assert.equal(adminTokenVerified, true);
        assert.ok(dashboardRefresh, "the old check did not enter its refresh");
        // The normal input handler invalidates the old request's token revision.
        element("admin-token").value = "second fixture token";
        listeners.get("input")();
        const newer = checkAdminToken();
        await flush();
        assert.equal(adminTokenVerified, true, "the new diagnostics check was not accepted");
        oldReply.resolve(reply(OLD_STATUS, {registration_required: true, error: "fixture denial"}));
        await Promise.all([older, newer]);
        assert.equal(adminTokenVerified, true, "the old refresh rejected the new token");
        assert.equal(adminTokenRejected, false);
        assert.equal(adminTokenRefused, false);
        assert.equal(adminTokenChecking, false);
        assert.equal(element("private-dashboard").hidden, false);
        assert.equal(element("token-check-status").textContent, result.message);
        assert.deepEqual(responses.map(response => response.status), [OLD_STATUS, 200, 200]);
        assert.deepEqual(responses.map(response => response.path),
          ["/api/health", "/api/health", "/api/system-health"]);
        console.log("CASE-COMPLETE");
      })().catch(error => {console.error(error); process.exitCode = 1});
    """.replace("OLD_STATUS", str(old_status)))


@pytest.mark.parametrize("status,code,rejected,refused", [
    (401, "", True, True),
    (403, "", True, False),
    (409, "helper-busy", False, False),
    (503, "", True, False),
])
def test_a_current_refresh_failure_keeps_its_denial(status, code, rejected, refused):
    run_refresh_case(r"""
      (async () => {
        result = {ok: true, message: "Token accepted. Diagnostics loaded."};
        element("admin-token").value = "current fixture token";
        replies.push(reply(STATUS, {error: "fixture denial", code: "CODE"}));
        await checkAdminToken();
        assert.equal(adminTokenVerified, false);
        assert.equal(adminTokenRejected, REJECTED);
        assert.equal(adminTokenRefused, REFUSED);
        assert.equal(adminTokenChecking, false);
        assert.equal(element("private-dashboard").hidden, true);
        assert.equal(element("admin-token").value, "current fixture token");
        assert.equal(responses.length, 1, "a current failure was retried");
        console.log("CASE-COMPLETE");
      })().catch(error => {console.error(error); process.exitCode = 1});
    """.replace("STATUS", str(status)).replace("CODE", code)
       .replace("REJECTED", str(rejected).lower()).replace("REFUSED", str(refused).lower()))


@pytest.mark.parametrize("old_status", [200, 401, 403])
def test_a_new_check_waits_for_an_old_poll_without_inheriting_its_read_error(old_status):
    polling = block("async function pollDashboard()", "function startDashboard()")
    run_refresh_case(polling + r"""
      (async () => {
        result = {ok: true, message: "Token accepted. Diagnostics loaded."};
        dashboardLoaded = true;
        adminTokenVerified = true;
        document.hidden = false;
        element("admin-token").value = "first fixture token";
        const oldReply = deferred();
        replies.push(oldReply.promise);
        const polling = pollDashboard();
        await flush();
        assert.ok(dashboardPoll);
        element("admin-token").value = "second fixture token";
        listeners.get("input")();
        const checking = checkAdminToken();
        await flush();
        assert.equal(adminTokenVerified, true);
        assert.equal(responses.length, 0, "refresh did not wait for the old poll");
        oldReply.resolve(reply(OLD_STATUS, {registration_required: true}));
        await Promise.all([polling, checking]);
        assert.equal(adminTokenVerified, true);
        assert.equal(adminTokenRejected, false);
        assert.equal(adminTokenRefused, false);
        assert.equal(element("private-dashboard").hidden, false);
        assert.equal(dashboardPoll, null);
        assert.equal(dashboardRefresh, null);
        assert.deepEqual(responses.map(response => response.status), [OLD_STATUS, 200, 200]);
        console.log("CASE-COMPLETE");
      })().catch(error => {console.error(error); process.exitCode = 1});
    """.replace("OLD_STATUS", str(old_status)))


def test_an_old_refresh_finally_cannot_clear_the_new_refresh_owner():
    run_refresh_case(r"""
      (async () => {
        result = {ok: true, message: "Token accepted. Diagnostics loaded."};
        element("admin-token").value = "first fixture token";
        const oldReply = deferred();
        const freshReply = deferred();
        replies.push(oldReply.promise, freshReply.promise);
        const older = checkAdminToken();
        await flush();
        const oldOwner = dashboardRefresh;
        element("admin-token").value = "second fixture token";
        listeners.get("input")();
        const newer = checkAdminToken();
        await flush();
        oldReply.resolve(reply());
        await older;
        await flush();
        assert.ok(dashboardRefresh, "the old finally cleared the new refresh");
        assert.notEqual(dashboardRefresh, oldOwner);
        assert.equal(dashboardRefreshRevision, adminTokenRevision);
        assert.equal(adminTokenChecking, true);
        assert.equal(adminTokenRejected, false);
        freshReply.resolve(reply());
        await newer;
        assert.equal(adminTokenVerified, true);
        assert.equal(adminTokenChecking, false);
        assert.equal(dashboardRefresh, null);
        console.log("CASE-COMPLETE");
      })().catch(error => {console.error(error); process.exitCode = 1});
    """)


def test_an_edit_while_waiting_for_an_old_refresh_keeps_only_the_newest_check():
    run_refresh_case(r"""
      (async () => {
        result = {ok: true, message: "Token accepted. Diagnostics loaded."};
        element("admin-token").value = "first fixture token";
        const oldReply = deferred();
        replies.push(oldReply.promise);
        const older = checkAdminToken();
        await flush();
        element("admin-token").value = "second fixture token";
        listeners.get("input")();
        const middle = checkAdminToken();
        await flush();
        element("admin-token").value = "third fixture token";
        listeners.get("input")();
        const newest = checkAdminToken();
        await flush();
        oldReply.resolve(reply());
        await Promise.all([older, middle, newest]);
        assert.equal(adminTokenVerified, true);
        assert.equal(adminTokenRejected, false);
        assert.equal(adminTokenChecking, false);
        assert.equal(element("private-dashboard").hidden, false);
        assert.equal(dashboardRefreshRevision, adminTokenRevision);
        assert.equal(responses.length, 3, "an obsolete waiting check started another refresh");
        console.log("CASE-COMPLETE");
      })().catch(error => {console.error(error); process.exitCode = 1});
    """)


def test_initial_wrong_token_and_failed_enrollment_do_not_poison_a_later_check():
    access = block("async function loadDashboardAccess()", "let dashboardAccessListSerial")
    run_refresh_case(r"""
      let thisDeviceMac = null, addressTokenRevision = null;
      function accessReason() { return "Fixture access is allowed."; }
      async function loadAddressMoves() {}
    """ + access + r"""
      (async () => {
        result = {ok: true, message: "Token accepted. Diagnostics loaded."};
        element("admin-token").value = "wrong fixture token";
        replies.push(reply(401, {error: "fixture authentication denial"}));
        await checkAdminToken();
        assert.equal(adminTokenRefused, true);
        element("admin-token").value = "valid fixture token";
        listeners.get("input")();
        replies.push(reply(200, {allowed: false, can_register: true}));
        await checkAdminToken();
        assert.equal(adminTokenRefused, false);
        assert.equal(adminTokenRejected, false);
        assert.match(element("token-check-status").textContent, /Allow this device/);
        assert.equal(element("private-dashboard").hidden, true);
        replies.push(reply(503, {error: "fixture enrollment save failed"}));
        await assert.rejects(request("/api/dashboard-access/register-self", "POST", {}),
          error => error.status === 503);
        assert.equal(adminTokenRejected, false);
        replies.push(reply(), reply(200, {allowed: true, can_register: false}));
        await request("/api/dashboard-access/register-self", "POST", {});
        await checkAdminToken();
        assert.equal(adminTokenVerified, true);
        assert.equal(adminTokenRejected, false);
        assert.equal(adminTokenRefused, false);
        assert.equal(element("private-dashboard").hidden, false);
        assert.equal(element("token-check-status").textContent, result.message);
        assert.deepEqual(responses.map(response => response.status), [401, 200, 503, 200, 200, 200, 200]);
        console.log("CASE-COMPLETE");
      })().catch(error => {console.error(error); process.exitCode = 1});
    """)
