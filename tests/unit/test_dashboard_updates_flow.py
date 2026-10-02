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
    source: staged ? "github" : null};
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
  assert.match(element("update-available").textContent, /v1\.1\.0/);
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
  assert.match(confirmations[0], /v1\.1\.0/);
  assert.match(confirmations[0], /few seconds/);
  const installs = calls.filter(c => c[1] === "/api/updates/install");
  assert.equal(installs.length, 1);
  assert.deepEqual(installs[0][2], {version: "1.1.0", confirm: true});
  assert.equal(element("release-install").disabled, true, "no second install while one runs");
  assert.equal(element("update-progress").hidden, false);

  let sawRestart = false;
  await waitFor(() => {
    if (/restart/i.test(element("update-progress").textContent)) sawRestart = true;
    return /Updated to v1\.1\.0/.test(element("update-progress").textContent);
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
  assert.match(element("github-access-hint").textContent, /sudo jittersim update set-token/);
""")


@NODE
def test_hidden_pages_and_hidden_tabs_make_no_requests():
    run(r"""
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
  const gets = calls.filter(c => c[0] === "GET" && c[1].startsWith("/api/updates/check-online/"));
  assert.ok(gets.length >= 2, "polling continues once the page is visible again");
  const firstGet = calls.findIndex(c => c[0] === "GET" && c[1].startsWith("/api/updates/check-online/"));
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
  await waitFor(() => /did not finish|failed/i.test(element("update-progress").textContent));
  assert.doesNotMatch(element("update-progress").textContent, /Updated to/);
  assert.equal(element("release-check").disabled, false);
""")
