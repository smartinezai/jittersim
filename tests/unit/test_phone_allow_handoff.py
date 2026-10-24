"""Token acceptance exposes Allow only for the person's current sign-in action."""

import pytest

from tests.unit.test_dashboard_auth_state import run_token_case


SETUP = r"""
const phoneLayout = {matches:true};
const focusListeners = new Map();
document.addEventListener = (type, callback) => {
  if (!focusListeners.has(type)) focusListeners.set(type, new Set());
  focusListeners.get(type).add(callback);
};
document.removeEventListener = (type, callback) => focusListeners.get(type)?.delete(callback);
function moveFocus(target, event = "focusin") {
  document.activeElement = target;
  for (const callback of focusListeners.get(event) || []) callback({target});
}
let closed = 0;
function closeCredentials() {
  closed++;
  element("credentials-panel").hidden = true;
  moveFocus(element("open-admin-credentials"));
}
const allow = element("allow-dashboard-device");
const effects = [];
allow.scrollIntoView = options => effects.push(["scroll", options.block]);
allow.focus = () => {effects.push(["focus"]); moveFocus(allow)};
element("credentials-panel").hidden = false;
element("admin-token").value = "fixture-token";
moveFocus(element("admin-token"));
accessHook = async () => ({allowed:false, can_register:true});
function assertClean() {
  for (const callbacks of focusListeners.values()) assert.equal(callbacks.size, 0);
  assert.equal(adminTokenVerified, false);
  assert.equal(refreshCalls, 0);
}
function deferred() {
  let resolve;
  const promise = new Promise(done => {resolve = done});
  return {promise, resolve};
}
const flush = () => new Promise(resolve => setImmediate(resolve));
"""


CASES = {
    "manual_check_exposes_and_focuses_existing_allow": r"""
      await checkAdminToken(true);
      assert.equal(closed, 1);
      assert.equal(document.activeElement, allow);
      assert.deepEqual(effects, [["scroll", "center"], ["focus"]]);
      assertClean();
    """,
    "background_check_keeps_current_credentials_task": r"""
      await checkAdminToken();
      assert.equal(closed, 0);
      assert.equal(element("credentials-panel").hidden, false);
      assert.deepEqual(effects, []);
      assertClean();
    """,
    "desktop_keeps_existing_credentials_flow": r"""
      phoneLayout.matches = false;
      await checkAdminToken(true);
      assert.equal(closed, 0);
      assert.deepEqual(effects, []);
      assertClean();
    """,
    "cannot_register_keeps_credentials_open": r"""
      accessHook = async () => ({allowed:false, can_register:false});
      await checkAdminToken(true);
      assert.equal(closed, 0);
      assert.deepEqual(effects, []);
      assertClean();
    """,
    "closed_panel_is_not_reopened_or_focused": r"""
      element("credentials-panel").hidden = true;
      await checkAdminToken(true);
      assert.equal(closed, 0);
      assert.deepEqual(effects, []);
      assertClean();
    """,
    "manual_request_during_background_check_is_kept_in_queue": r"""
      const pending = deferred(); let reads = 0;
      accessHook = async () => {reads++; return reads === 1 ? pending.promise :
        {allowed:false, can_register:true}};
      const background = checkAdminToken();
      const queuedBackground = checkAdminToken();
      const manual = checkAdminToken(true);
      pending.resolve({allowed:false, can_register:true});
      await Promise.all([background, queuedBackground, manual]);
      assert.equal(reads, 2);
      assert.equal(closed, 1);
      element("credentials-panel").hidden = false;
      moveFocus(element("admin-token"));
      await checkAdminToken();
      assert.equal(closed, 1, "manual intent leaked into a later background check");
      assertClean();
    """,
    "changed_token_cannot_inherit_old_manual_intent": r"""
      const pending = deferred(); let reads = 0;
      accessHook = async () => {reads++; return reads === 1 ? pending.promise :
        {allowed:false, can_register:true}};
      const old = checkAdminToken();
      const queued = checkAdminToken(true);
      adminTokenRevision++;
      const fresh = checkAdminToken();
      pending.resolve({allowed:false, can_register:true});
      await Promise.all([old, queued, fresh]);
      assert.equal(reads, 2);
      assert.equal(closed, 0);
      assertClean();
    """,
}


@pytest.mark.parametrize("case", CASES)
def test_allow_handoff_ownership(case):
    run_token_case(SETUP + "\n(async () => {" + CASES[case] + r"""
      console.log("CASE-COMPLETE");
    })().catch(error => {console.error(error); process.exitCode = 1});
    """)


@pytest.mark.parametrize("event,target", [
    ("focusin", "router-admin-password"),
    ("focusin", "dashboard-access-name"),
    ("pointerdown", "outside"),
])
def test_focus_moved_while_checking_is_not_taken_back(event, target):
    run_token_case(SETUP + r"""
    (async () => {
      const pending = deferred(); accessHook = () => pending.promise;
      const checking = checkAdminToken(true);
      await flush();
      moveFocus(element("TARGET"), "EVENT");
      pending.resolve({allowed:false, can_register:true});
      await checking;
      assert.equal(closed, 0);
      assert.equal(document.activeElement, element("TARGET"));
      assert.deepEqual(effects, []);
      assertClean();
      console.log("CASE-COMPLETE");
    })().catch(error => {console.error(error); process.exitCode = 1});
    """.replace("TARGET", target).replace("EVENT", event))
