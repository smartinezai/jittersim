"""Exercise token errors against the served sign-in handler without a browser."""

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from jittersim.api.server import DASHBOARD_HTML


class SignInMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nodes = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.nodes[attrs["id"]] = {"tag": tag, "attrs": attrs}


def token_markup():
    form = re.search(r'<form id="admin-token-form".*?</form>', DASHBOARD_HTML, re.DOTALL)
    parser = SignInMarkup()
    parser.feed(form.group())
    return parser.nodes


HARNESS = r"""
const assert = require('node:assert/strict');
const nodes = new Map();
const document = {activeElement: null, body: {id: 'body'}};
function element(id) {
  if (!nodes.has(id)) {
    const attrs = new Map(Object.entries(markup[id]?.attrs || {}));
    nodes.set(id, {
      id, attributes: attrs, value: attrs.get('value') || '', checked: false,
      disabled: false, hidden: attrs.has('hidden'), textContent: '', listeners: new Map(),
      setAttribute(k, v) {this.attributes.set(k, v);},
      removeAttribute(k) {this.attributes.delete(k);},
      getAttribute(k) {return this.attributes.get(k);},
      addEventListener(k, v) {this.listeners.set(k, v);},
      dispatchEvent(event) {this.listeners.get(event.type)?.(event);},
      focus() {document.activeElement = this;},
      contains(node) {return Boolean(node && this.id === 'admin-token-form' && markup[node.id]);}
    });
  }
  return nodes.get(id);
}
let adminTokenRevision = 0, adminTokenVerified = false, adminTokenChecking = false;
let adminTokenRejected = false, adminTokenRefused = false;
let routerWifiRemoteBusy = false, routerWifiRemoteChanging = false;
let messageSerial = 0, refreshCount = 0;
let access = async () => ({allowed: true});
let tools = async () => ({ok: true, message: 'Token accepted. Diagnostics loaded.'});
function setDashboardUnlocked() {}
function setPrivateDataPanels() {}
function updateCredentialStatus() {}
function stopTrafficSampling() {}
function openCredentials() {element('admin-token').focus();}
function showMessage() {messageSerial++;}
function isConnectionLost(error) {return Boolean(error.connectionLost);}
function lostConnectionError(error) {error.connectionLost = true; return error;}
function recheckTokenWhenPiAnswers() {}
async function loadDashboardAccess() {return access();}
async function loadAdminTools() {return tools();}
async function refresh() {refreshCount++;}
async function loadTrafficSettings() {}
async function startTrafficSampling() {}
function deferred() {
  let resolve, reject;
  const promise = new Promise((done, fail) => {resolve = done; reject = fail;});
  return {promise, resolve, reject};
}
function edit(value) {
  element('admin-token').value = value;
  element('admin-token').listeners.get('input')();
}
function assertKept() {
  assert.equal(element('admin-token').value, 'fixture token');
  assert.equal(element('remember-admin-token').checked, true);
  assert.equal(element('remember-admin-duration').value, '168');
}
function assertCleared() {
  assert.equal(element('admin-token-errors').hidden, true);
  assert.equal(element('admin-token-error-link').textContent, '');
  assert.equal(element('admin-token').getAttribute('aria-invalid'), undefined);
  assert.equal(element('admin-token').getAttribute('aria-describedby'), 'token-check-status');
}
element('admin-token').value = 'fixture token';
element('remember-admin-token').checked = true;
element('remember-admin-duration').value = '168';
element('check-admin-token').focus();
"""


def run_case(checks):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required")
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)

    def block(start, end):
        first = script.index(start)
        return script[first:script.index(end, first)]

    functions = block("async function checkAdminToken(", "// Which parts the next refresh reads:")
    functions += block('element("admin-token").addEventListener("input",',
                       'element("traffic-device").addEventListener("change",')
    functions += block('element("admin-token-error-link").addEventListener("click",',
                       '// A pasted token')
    functions += block('element("clear-admin-token").addEventListener("click",',
                       'element("clear-router-password").addEventListener("click",')
    program = "const markup = " + json.dumps(token_markup()) + ";\n"
    program += HARNESS + functions + "\n(async () => {\n" + checks
    program += "\nconsole.log('CASE-COMPLETE');\n})().catch(e => {console.error(e); process.exitCode = 1;});"
    result = subprocess.run([node, "-"], input=program, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("CASE-COMPLETE"), result.stdout


def test_failed_then_successful_sign_in_keeps_values_and_clears_error_on_retry():
    run_case(r"""
      tools = async () => ({ok: false, status: 401, message: 'Invalid token'});
      await checkAdminToken();
      assertKept();
      assert.equal(element('admin-token').getAttribute('aria-invalid'), 'true');
      const summary = element('admin-token-errors');
      assert.equal(summary.hidden, false);
      assert.equal(document.activeElement, summary);
      assert.equal(element('admin-token-error-link').getAttribute('href'), '#admin-token');
      assert.match(element('admin-token-error-link').textContent, /Invalid token/);
      assert.equal(element('admin-token-error-link').textContent,
        element('token-check-status').textContent);
      let prevented = false;
      element('admin-token-error-link').listeners.get('click')({preventDefault() {prevented = true;}});
      assert.equal(prevented, true);
      assert.equal(document.activeElement, element('admin-token'));
      const held = deferred();
      tools = () => held.promise;
      const retry = checkAdminToken();
      assertCleared();
      held.resolve({ok: true, message: 'Token accepted. Diagnostics loaded.'});
      await retry;
      assertCleared();
      assertKept();
      assert.equal(adminTokenVerified, true);
      assert.equal(refreshCount, 1);
      assert.equal(element('check-admin-token').disabled, false);
    """)


def test_blank_token_focuses_summary_and_edit_or_clear_removes_error():
    run_case(r"""
      element('admin-token').value = '  ';
      await checkAdminToken();
      assert.equal(element('admin-token').value, '  ');
      assert.equal(element('admin-token').getAttribute('aria-invalid'), 'true');
      assert.equal(document.activeElement, element('admin-token-errors'));
      assert.match(element('admin-token-error-link').textContent, /Enter the admin token/);
      edit('fixture token');
      assertCleared();
      tools = async () => ({ok: false, status: 401, message: 'Invalid token'});
      await checkAdminToken();
      element('clear-admin-token').listeners.get('click')();
      assertCleared();
      assert.equal(element('admin-token').value, '');
      assert.equal(document.activeElement, element('admin-token'));
    """)


@pytest.mark.parametrize("failure,invalid", [
    ("throw Object.assign(new Error('Invalid token'), {status: 401})", True),
    ("throw Object.assign(new Error('Device not allowed'), {status: 403})", False),
    ("throw Object.assign(new Error('Service unavailable'), {status: 503})", False),
    ("throw Object.assign(new Error('Pi unreachable'), {connectionLost: true})", False),
    ("throw Object.assign(new Error('Device verification busy'), {code: 'helper-busy'})", False),
    ("return {ok: false, interrupted: true, message: 'Try the check again'}", False),
])
def test_failed_checks_describe_recovery_without_blaming_valid_tokens(failure, invalid):
    run_case("tools = async () => {" + failure + "};" + r"""
      await checkAdminToken();
      assertKept();
      assert.equal(element('admin-token-errors').hidden, false);
      assert.equal(document.activeElement, element('admin-token-errors'));
      assert.equal(element('admin-token').getAttribute('aria-invalid'), INVALID);
      assert.equal(element('admin-token-error-link').textContent,
        element('token-check-status').textContent);
    """.replace("INVALID", "'true'" if invalid else "undefined"))


@pytest.mark.parametrize("move_during_check", [False, True])
def test_background_or_abandoned_check_does_not_take_focus(move_during_check):
    run_case(r"""
      const elsewhere = element('other-task');
      if (!MOVE) elsewhere.focus();
      const held = deferred();
      tools = () => held.promise;
      const checking = checkAdminToken();
      elsewhere.focus();
      held.resolve({ok: false, status: 401, message: 'Invalid token'});
      await checking;
      assert.equal(document.activeElement, elsewhere);
      assert.equal(element('admin-token-errors').hidden, false);
    """.replace("MOVE", str(move_during_check).lower()))


def test_old_rejection_cannot_repaint_a_new_accepted_token_or_take_focus():
    run_case(r"""
      const held = deferred();
      access = () => held.promise;
      const older = checkAdminToken();
      edit('new fixture token');
      access = async () => ({allowed: true});
      await checkAdminToken();
      assertCleared();
      const elsewhere = element('other-task');
      elsewhere.focus();
      held.reject(Object.assign(new Error('Old invalid token'), {status: 401}));
      await older;
      assertCleared();
      assert.equal(adminTokenVerified, true);
      assert.equal(element('admin-token').value, 'new fixture token');
      assert.equal(document.activeElement, elsewhere);
    """)


def test_summary_uses_existing_error_design_and_single_announcement_path():
    nodes = token_markup()
    summary = nodes["admin-token-errors"]["attrs"]
    assert "hidden" in summary
    assert summary["tabindex"] == "-1"
    assert summary["class"] == "form-error"
    assert summary["aria-labelledby"] in nodes
    for id in ("admin-token-errors", "admin-token-errors-title", "admin-token-error-link"):
        assert "role" not in nodes[id]["attrs"]
        assert "aria-live" not in nodes[id]["attrs"]
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important", DASHBOARD_HTML)
    assert nodes["admin-token"]["attrs"]["autocomplete"] == "current-password"
