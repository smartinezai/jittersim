"""The Updates page: check, see the release, install, ride out the restart and say what changed.

The page logic of workflows.js runs in node against a fake page and a fake API that behaves like
the Pi: a check job that runs and finishes, a staged release, an install that restarts the
dashboard for a few requests and then reports the new version.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[2] / "src/jittersim/api/workflows.js"
NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")

HARNESS = r"""
const assert = require("node:assert/strict");
let fakeNow = 1000000;
Date.now = () => fakeNow;
const calls = [];
let adminTokenRevision=0,adminTokenVerified=true;
const privateDataLockHooks=[];
const confirmations = [];
let tick = 0;
let onTick = () => {};
setTimeout = (fn, ms) => { fakeNow += ms; tick += 1; onTick(tick); setImmediate(fn); return 0; };

class Fake {
  constructor(id) {
    this.id = id; this.hidden = false; this.disabled = false; this.checked = false;
    this.textContent = ""; this.attributes = {}; this.listeners = {}; this.children = [];
    this.className = ""; this.type = "";
    const owner = this;
    this.classList = {values: new Set(),
      toggle(name, on) { (on === undefined ? !this.values.has(name) : on) ? this.values.add(name) : this.values.delete(name); },
      add(name) { this.values.add(name); }, remove(name) { this.values.delete(name); },
      contains(name) { return this.values.has(name); }};
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); }
  append(...nodes) { this.children.push(...nodes); nodes.forEach(node => { if (node.id) elements[node.id] = node; }); }
  click() {
    if (this.disabled) return Promise.resolve();
    return Promise.all((this.listeners.click || []).map(fn => fn({target: this})));
  }
  toggleTo(value) {
    this.checked = value;
    return Promise.all((this.listeners.change || []).map(fn => fn({target: this})));
  }
}
const elements = {};
const HIDDEN_AT_START = ["sidebar-update-link", "update-available-badge", "update-progress", "github-access-hint"];
function element(id) {
  if (!elements[id]) { elements[id] = new Fake(id); elements[id].hidden = HIDDEN_AT_START.includes(id); }
  return elements[id];
}
const document = {hidden: false, createElement: tag => new Fake(tag)};
let page = "updates";
function currentPage() { return page; }
const messages = [];
function showMessage(text, kind) { messages.push([kind, text]); }
function setButtonBusy(button, busy) {
  button.disabled = busy;
  if (busy) button.setAttribute("aria-busy", "true"); else button.removeAttribute("aria-busy");
}
let confirmAnswer = true;
async function confirmWorkflow(message) { confirmations.push(message); return confirmAnswer; }
let activeTestRecord = null;

async function waitFor(done, step) {
  for (let i = 0; i < 400; i++) {
    if (step) step();
    if (done()) return;
    await new Promise(r => setImmediate(r));
  }
  throw new Error("condition never became true");
}

// The fake Pi.
const pi = {
  installed: "1.0.0", staged: null, tokenSet: true, phase: "idle", rollback: false,
  settings: {daily_check: false, install_automatically: false},
  checkJob: null, checkPolls: 0, restartFailures: 0, installing: false, endPhase: null, checkedAt: null,
};
function updatesView() {
  const staged = pi.staged;
  return {installed: pi.installed, available: staged ? staged.version : null, tag: staged ? staged.tag : null,
    phase: pi.phase, message: "ok", rollback_available: pi.rollback, installable: Boolean(staged),
    signed_by: staged ? "desk-mac" : null, reason: null, token_set: pi.tokenSet,
    source: staged ? "github" : null, notes: staged ? staged.notes ?? null : null,
    recovery_problem: pi.recoveryProblem ?? null};
}
function jobView(state) {
  return {job: "0123456789abcdef", state, requested_at: 1, checked_at: state === "done" ? pi.checkedAt : null,
    result: state === "done" ? "staged" : null, message: state === "done" ? "Release v1.1.0 is staged." : "Checking GitHub for a release.",
    available: state === "done" && pi.staged ? pi.staged.version : null, tag: pi.staged ? pi.staged.tag : null,
    install_queued: false, cached: false, settings: {...pi.settings}, token_set: pi.tokenSet};
}
let blockNextGet = null;
async function request(path, method, body) {
  calls.push([method, path, body === undefined ? undefined : JSON.parse(JSON.stringify(body))]);
  if (path === "/api/updates" && method === "GET") {
    if (pi.restartFailures > 0) { pi.restartFailures -= 1; throw new Error("Failed to fetch"); }
    if (pi.installing) {
      pi.installing = false; pi.phase = pi.endPhase || "complete";
      if (pi.phase === "complete") { pi.installed = pi.staged.version; pi.staged = null; }
    }
    return updatesView();
  }
  if (path === "/api/updates/check-online" && method === "POST") {
    pi.checkJob = "running"; pi.checkPolls = 0; return jobView("running");
  }
  if (path.startsWith("/api/updates/check-online") && method === "GET") {
    if (pi.checkJob === "running") {
      pi.checkPolls += 1;
      if (pi.checkPolls >= 2) { pi.checkJob = "done"; pi.checkedAt = fakeNow / 1000; pi.staged = pi.staged || {version: "1.1.0", tag: "v1.1.0"}; }
    }
    return jobView(pi.checkJob || "none");
  }
  if (path === "/api/updates/online-settings") {
    if (method === "POST") Object.assign(pi.settings, Object.fromEntries(Object.entries(body).filter(([k]) => k !== "confirm")));
    return {settings: {...pi.settings}, token_set: pi.tokenSet};
  }
  if (path === "/api/updates/install" && method === "POST") {
    pi.phase = "queued"; pi.restartFailures = 2; pi.installing = true; return {phase: "queued"};
  }
  if (path === "/api/updates/rollback" && method === "POST") { pi.phase = "rolling-back"; return {phase: "queued"}; }
  throw new Error("unexpected " + method + " " + path);
}
%(source)s
"""

PRELUDE = r"""
wireReleaseControls();
"""


def sources():
    text = WORKFLOWS.read_text(encoding="utf-8")
    parts = []
    for name in ("textNode", "actionButton"):
        start = text.index(f"function {name}(")
        parts.append(text[start: text.index("\n}\n", start) + 3])
    start = text.index("let releaseState = null;")
    parts.append(text[start: text.index("function installReleaseControls(")])
    return "\n".join(parts)


def run(body):
    script = HARNESS % {"source": sources()} + "\n(async () => {\n" + PRELUDE + body + "\n})().then(() => console.log('DONE')).catch(error => { console.error(error, JSON.stringify(messages), JSON.stringify(calls.slice(-8))); process.exit(1); });"
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip().endswith("DONE"), result.stdout


@NODE
def test_check_find_confirm_install_restart_and_reconnect():
    run(r"""
  await new Promise(r => setImmediate(r));
  assert.equal(element("sidebar-update-link").hidden, true);
  element("release-check").click();
  element("release-check").click();   // a second click while the first runs does nothing
  await Promise.resolve();
  assert.equal(element("release-check").disabled, true, "busy while the request runs");
  assert.equal(element("release-install").disabled, true);
  await waitFor(() => !element("release-check").disabled);
  const posts = calls.filter(c => c[0] === "POST" && c[1] === "/api/updates/check-online");
  assert.equal(posts.length, 1, "one online check, not two");
  assert.deepEqual(posts[0][2], {force: true});
  assert.match(element("update-available").textContent, /1\.1\.0/);
  assert.match(element("update-signer").textContent, /desk-mac/);
  assert.match(element("update-last-checked").textContent, /\d/);
  assert.equal(element("sidebar-update-link").hidden, false);
  assert.match(element("sidebar-update-link").textContent, /Update available/);
  assert.match(element("sidebar-version").textContent, /1\.0\.0/);
  assert.equal(element("update-available-badge").hidden, false);
  assert.equal(element("release-install").disabled, false);

  const install = element("release-install").click();
  await install;
  assert.equal(confirmations.length, 1);
  assert.match(confirmations[0], /1\.1\.0/);
  assert.match(confirmations[0], /restart/);
  const installs = calls.filter(c => c[1] === "/api/updates/install");
  assert.equal(installs.length, 1);
  assert.deepEqual(installs[0][2], {version: "1.1.0", confirm: true});
  assert.equal(element("release-install").disabled, true, "no second install while one runs");
  assert.equal(element("update-progress").hidden, false);

  let sawRestart = false;
  await waitFor(() => {
    if (/restart/i.test(element("update-progress").textContent)) sawRestart = true;
    return /Updated to 1\.1\.0/.test(element("update-progress").textContent);
  });
  assert.ok(sawRestart, "says the dashboard is restarting while requests fail");
  assert.equal(element("sidebar-update-link").hidden, true);
  assert.equal(element("update-available-badge").hidden, true);
  assert.match(element("sidebar-version").textContent, /1\.1\.0/);
  assert.equal(element("release-check").disabled, false);
""")


@NODE
def test_without_a_token_check_reads_staged_releases_and_explains_set_token():
    run(r"""
  pi.tokenSet = false;
  await loadOnlineStatus();
  await element("release-check").click();
  assert.equal(calls.filter(c => c[1] === "/api/updates/check-online" && c[0] === "POST").length, 0);
  assert.ok(calls.some(c => c[0] === "GET" && c[1] === "/api/updates"));
  assert.equal(element("github-access-hint").hidden, false);
  assert.match(element("github-access-hint").textContent, /setup command in The GitHub token/);
""")


@NODE
def test_hidden_pages_and_hidden_tabs_make_no_requests():
    run(r"""
  await loadOnlineStatus();
  calls.length = 0;
  page = "diagnostics";
  const before = calls.length;
  await pollReleaseProgress();
  assert.equal(calls.length, before, "an idle hidden page makes no request");
  page = "updates";
  const check = element("release-check").click();
  page = "devices";
  let ticks = 0;
  onTick = n => { if (n === 6) page = "updates"; };
  await check;
  const gets = calls.filter(c => c[0] === "GET" && c[1] === "/api/updates/check-online");
  assert.ok(gets.length >= 2, "polling continues once the page is visible again");
  const firstGet = calls.findIndex(c => c[0] === "GET" && c[1] === "/api/updates/check-online");
  assert.ok(tick >= 6, "it waited at least six ticks while hidden before the first poll: " + tick);
  assert.ok(firstGet > 0);
""")


@NODE
def test_install_automatically_asks_first_and_both_settings_start_off():
    run(r"""
  await loadOnlineStatus();
  assert.equal(element("online-daily-check").checked, false);
  assert.equal(element("online-install-automatically").checked, false);
  confirmAnswer = false;
  await element("online-install-automatically").toggleTo(true);
  assert.equal(element("online-install-automatically").checked, false);
  assert.equal(calls.filter(c => c[1] === "/api/updates/online-settings" && c[0] === "POST").length, 0);
  confirmAnswer = true;
  await element("online-install-automatically").toggleTo(true);
  const post = calls.filter(c => c[1] === "/api/updates/online-settings" && c[0] === "POST");
  assert.deepEqual(post[0][2], {install_automatically: true, confirm: true});
  await element("online-daily-check").toggleTo(true);
  const daily = calls.filter(c => c[1] === "/api/updates/online-settings" && c[0] === "POST")[1];
  assert.deepEqual(daily[2], {daily_check: true});
""")


@NODE
def test_a_failed_install_is_reported_not_called_updated():
    run(r"""
  pi.staged = {version: "1.1.0", tag: "v1.1.0"}; pi.endPhase = "rolled-back";
  await loadReleaseStatus({force: true});
  await element("release-install").click();
  await waitFor(() => /not confirmed|failed/i.test(element("update-progress").textContent));
  assert.doesNotMatch(element("update-progress").textContent, /Updated to/);
  assert.equal(element("release-check").disabled, false);
""")


@NODE
def test_install_button_names_the_release_and_notes_and_recovery_follow_the_status():
    run(r"""
  await loadReleaseStatus({force: true});
  assert.equal(element("release-install").textContent, "Install update", "nothing staged");
  assert.equal(element("update-notes-box").hidden, true);
  assert.equal(element("update-recovery").textContent, "Made automatically before the next install.");

  pi.staged = {version: "1.1.0", tag: "v1.1.0", notes: "  ## v1.1.0\n\n* Faster checks\n"};
  await loadReleaseStatus({force: true});
  assert.equal(element("release-install").textContent, "Install 1.1.0");
  assert.equal(element("update-notes-box").hidden, false);
  assert.equal(element("update-notes").textContent, "## v1.1.0\n\n* Faster checks");

  pi.staged.notes = "x".repeat(20000);
  await loadReleaseStatus({force: true});
  assert.equal(element("update-notes").textContent.length, 16384, "the page shows at most 16 KB of notes");

  pi.staged.notes = 42;
  await loadReleaseStatus({force: true});
  assert.equal(element("update-notes-box").hidden, true, "notes that are not text stay hidden");

  pi.staged = {version: "1.0.0", tag: "v1.0.0", notes: "Already installed"};
  await loadReleaseStatus({force: true});
  assert.equal(element("release-install").textContent, "Install update", "the installed release is not offered");
  assert.equal(element("update-notes-box").hidden, true, "notes belong to a release that can be installed");

  pi.staged = null;
  pi.rollback = true;
  await loadReleaseStatus({force: true});
  assert.match(element("update-recovery").textContent, /^Kept\. Restore previous installation is available/);
  pi.rollback = false;
  pi.recoveryProblem = "cannot read the recovery folder";
  await loadReleaseStatus({force: true});
  assert.match(element("update-recovery").textContent, /^Could not be checked/);
  assert.doesNotMatch(element("update-recovery").textContent, /Made automatically/);

  releaseState = {phase: "unavailable", message: "Release controls require the Pi network helper."};
  renderUpdates();
  assert.equal(element("update-recovery").textContent, "Unknown", "an answer without recovery facts says Unknown");
  releaseState = null;
  renderUpdates();
  assert.equal(element("update-recovery").textContent, "Unknown");
""")


@NODE
def test_signing_out_clears_the_release_name_notes_and_recovery_facts():
    run(r"""
  pi.staged = {version: "1.1.0", tag: "v1.1.0", notes: "* Faster checks"};
  pi.rollback = true;
  await loadReleaseStatus({force: true});
  assert.equal(element("release-install").textContent, "Install 1.1.0");
  assert.equal(element("update-notes-box").hidden, false);
  clearUpdatePrivateState();
  assert.equal(element("release-install").textContent, "Install update");
  assert.equal(element("release-install").disabled, true);
  assert.equal(element("update-notes-box").hidden, true);
  assert.equal(element("update-notes").textContent, "");
  assert.equal(element("update-recovery").textContent, "Unknown");
""")


@NODE
def test_short_release_labels_preserve_exact_details_and_raw_requests():
    run(r"""
  const commit = "03fe31d8a1695dd42c9e213540f3127b555e8640";
  assert.equal(shortReleaseLabel("0.2.0"), "0.2.0");
  assert.equal(shortReleaseLabel("v0.2.0"), "0.2.0");
  assert.equal(shortReleaseLabel("0.2.0.dev+03fe31d"), "Preview 03fe31d");
  assert.equal(shortReleaseLabel("0.2.0.dev+" + commit), "Preview 03fe31d");
  assert.equal(shortReleaseLabel("v1"), "v1", "legacy labels stay literal");
  assert.equal(shortReleaseLabel(null), "Unknown");
  assert.equal(shortReleaseLabel("0.2.0.dev+not-a-commit"), "0.2.0.dev+not-a-commit");
  const version = "0.2.0.dev+03fe31d";
  releaseState = {installed: version, available: "0.2.0", tag:"v0.2.0", installable:true,
    installed_metadata:{commit, sequence:17, signer:null, signer_label:null},
    available_metadata:{commit:"a".repeat(40), sequence:18, signer:"SHA256:fixture", signer_label:"<img src=x>"}};
  const original = JSON.stringify(releaseState);
  renderUpdates();
  assert.equal(element("sidebar-version").textContent, "Version Preview 03fe31d");
  assert.equal(element("update-installed").textContent, "Preview 03fe31d");
  assert.equal(element("update-available").textContent, "0.2.0");
  assert.equal(element("update-installed-version").textContent, version);
  assert.equal(element("update-installed-commit").textContent, commit);
  assert.equal(element("update-installed-signer").textContent, "No signer recorded");
  assert.equal(element("update-available-commit").textContent, "a".repeat(40));
  assert.equal(element("update-signer").textContent, "<img src=x> (SHA256:fixture)");
  assert.equal(JSON.stringify(releaseState), original, "presentation leaves raw identity untouched");
  assert.equal(stagedReleaseReady({installed:"0.2.0.dev+03fe31d000", available:"0.2.0.dev+03fe31d111", installable:true}), true,
    "identical short previews never determine readiness");
  assert.equal(stagedReleaseReady({installed:"0.2.0", available:"0.2.0", installable:true}), false);

  releaseState.installed_metadata = null;
  renderUpdates();
  assert.equal(element("update-installed-commit").textContent, "Unknown", "never infer commit from version text");
  assert.equal(element("update-installed-signer").textContent, "Unknown");
  clearUpdatePrivateState();
  assert.equal(element("update-available-commit").textContent, "Unknown");
  assert.equal(element("update-signer").textContent, "Sign in to read update status.");
  assert.equal(element("update-release-details").open, false);
  adminTokenVerified = true;
  pi.installed = "0.1.0";
  pi.staged = {version, tag:"unrelated-tag"};
  await loadReleaseStatus({force:true});
  await installUpdate();
  const install = calls.find(c => c[1] === "/api/updates/install");
  assert.deepEqual(install[2], {version, confirm:true});
""")


def test_release_identity_uses_native_details_and_text_values():
    source = WORKFLOWS.read_text()
    assert '<details id="update-release-details"><summary>Details</summary>' in source
    assert '<dd id="update-signer">' in source
    assert "element(id + '-commit').textContent" in source


@NODE
def test_ready_message_shortens_only_the_exact_current_verified_template():
    run(r"""
  const version = "0.2.0.dev+03fe31d";
  pi.staged = {version};
  const ready = {installed:"0.1.0", available:version, phase:"ready", installable:true, signed_by:"desk-mac",
    message:"Release " + version + " signed by desk-mac verified and ready to install."};
  request = async () => ready;
  await loadReleaseStatus({force:true});
  assert.match(element("release-status").textContent, /Preview 03fe31d verified and ready to install\./);
  assert.ok(!element("release-status").textContent.includes(version));
  assert.ok(!element("release-status").textContent.includes("desk-mac"));
  assert.equal(ready.message, "Release " + version + " signed by desk-mac verified and ready to install.");
  for (const diagnostic of ["Release " + version + " signed by another-key verified and ready to install.",
    "Signature failed for " + version + ": desk-mac is no longer trusted.",
    "Release " + version + " signed by desk-mac verified and ready to install. Unexpected suffix."]) {
    ready.message = diagnostic;
    await loadReleaseStatus({force:true});
    assert.ok(element("release-status").textContent.endsWith(diagnostic));
  }
  ready.message = "Release " + version + " signed by desk-mac verified and ready to install.";
  ready.installable = false;
  await loadReleaseStatus({force:true});
  assert.ok(element("release-status").textContent.endsWith(ready.message), "never rewrite a non-ready diagnostic");
""")


@NODE
def test_exact_installed_version_never_uses_cached_health_or_previous_identity():
    run(r"""
  knownHealthVersion = "0.2.0.dev+03fe31d";
  lastConfirmedInstalledVersion = "0.1.0";
  for (const installed of ["Unversioned installation", "Unversioned", undefined, null, ""]) {
    releaseState = {installed, available:null};
    renderUpdates();
    assert.equal(element("update-installed-version").textContent, "Unknown");
    assert.equal(element("update-installed-commit").textContent, "Unknown");
    assert.equal(element("update-installed-signer").textContent, "Unknown");
    assert.equal(element("sidebar-version").textContent, "Version 0.1.0", "sidebar keeps existing fallback");
  }
  releaseState = {installed:"0.2.0.dev+03fe31d"};
  renderUpdates();
  assert.equal(element("update-installed-version").textContent, releaseState.installed);
  element("update-release-details").open = true;
  clearUpdatePrivateState();
  assert.equal(element("update-installed-version").textContent, "Unknown", "sign-out never substitutes retained health version");
  assert.equal(element("update-installed-commit").textContent, "Unknown");
  assert.equal(element("update-installed-signer").textContent, "Unknown");
  assert.equal(element("update-release-details").open, false);
""")


@NODE
def test_install_confirmations_keep_distinct_full_versions_and_tag_precedence():
    run(r"""
  confirmAnswer = false;
  const versions = ["0.2.0.dev+03fe31d000", "0.2.0.dev+03fe31d111"];
  for (const version of versions) {
    releaseState = {installed:"0.1.0", available:version, installable:true, signed_by:"desk-mac"};
    renderUpdates();
    assert.equal(element("release-install").textContent, "Install Preview 03fe31d");
    await installUpdate();
    assert.ok(confirmations.at(-1).startsWith("Install " + version + "?"));
    assert.ok(confirmations.at(-1).includes("It is signed by desk-mac."));
  }
  assert.notEqual(confirmations[0], confirmations[1]);
  releaseState.tag = "v0.2.0-preview-exact-tag";
  await installUpdate();
  assert.ok(confirmations.at(-1).startsWith("Install v0.2.0-preview-exact-tag?"));
  releaseState.tag = "";
  await installUpdate();
  assert.ok(confirmations.at(-1).startsWith("Install " + versions[1] + "?"));
  assert.equal(calls.filter(c => c[1] === "/api/updates/install").length, 0, "declining still sends no install");
""")
