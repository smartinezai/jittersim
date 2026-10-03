"""Exercise delayed trust replies and certificate downloads through the real page script."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[2] / "src/jittersim/api/trust.js").read_text()
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")


@pytest.mark.parametrize("scenario", ["details", "duplicate", "headers", "body", "oversized", "refused", "timeout", "success"])
def test_trust_requests_keep_current_access_and_bounded_downloads(scenario):
    program = r"""
const assert = require('node:assert/strict');
const nodes = new Map();
const clicks = [];
const timers = new Map();
let nextTimer = 0;
function element(id) {
  if (!nodes.has(id)) nodes.set(id, {
    textContent:'', hidden:true, disabled:false, value:'accepted-token', href:'',
    replaceChildren(...children) { this.children = children; },
    removeAttribute(name) { this[name] = ''; }, addEventListener() {},
  });
  return nodes.get(id);
}
const window = {location:{host:'netlab.local:8080', hostname:'netlab.local'},
  setTimeout(fn) { const id = ++nextTimer; timers.set(id, fn); return id; },
  clearTimeout(id) { timers.delete(id); }};
const document = {createElement() { return {click() { clicks.push(this.download); }}; }};
let urls = 0;
URL.createObjectURL = () => { urls++; return 'blob:certificate'; };
URL.revokeObjectURL = () => {};
let adminTokenRevision = 1;
let adminTokenVerified = true;
let adminTokenRejected = false;
let adminTokenRefused = false;
const privateDataLockHooks = [];
function lockDashboardAccess() { adminTokenVerified = false; privateDataLockHooks.forEach(hook => hook()); }
function deferred() { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise, resolve, reject}; }
let request;
let fetch;
function revoke() { adminTokenRevision++; element('admin-token').value = 'changed-token';
  adminTokenVerified = false; privateDataLockHooks.forEach(hook => hook()); }
const data = {address:'https://netlab.local:8080', steps:{mac:['private installation step'],iphone:['private installation step']},
  available:true, fingerprint:'private fingerprint', announce:{state:'published'}};
""" + SCRIPT + r"""
async function main(scenario) {
  const pending = deferred();
  let requests = 0;
  request = () => { requests++; return pending.promise; };
  if (scenario === 'details' || scenario === 'duplicate') {
    const first = loadTrustDetails();
    await loadTrustDetails();
    assert.equal(requests, 1);
    if (scenario === 'details') revoke();
    pending.resolve(data);
    await first;
    if (scenario === 'details') {
      assert.equal(element('trust-address').textContent, '');
      assert.equal(element('trust-fingerprint').textContent, '');
      assert.equal(element('one-address-url').textContent, '');
      assert.equal(element('one-address-link').href, '');
      assert.equal(element('trust-download-crt').disabled, true);
    } else {
      assert.equal(element('trust-fingerprint').textContent, data.fingerprint);
      await loadTrustDetails();
      assert.equal(requests, 1);
    }
    return;
  }
  request = async () => data;
  await loadTrustDetails();
  const responsePending = deferred();
  const bodyPending = deferred();
  let reads = 0;
  let cancellations = 0;
  const reader = {
    async read() { reads++; if (scenario === 'body') return bodyPending.promise;
      if (reads > 1) return {done:true};
      return {done:false, value:new Uint8Array(scenario === 'oversized' ? 65537 : 5)}; },
    async cancel() { cancellations++; }
  };
  fetch = (path, options) => {
    assert.equal(options.headers.Authorization, 'Bearer accepted-token');
    assert.equal(options.cache, 'no-store');
    options.signal.addEventListener('abort', () => responsePending.reject(new Error('aborted')));
    return responsePending.promise;
  };
  const button = element('trust-download-crt');
  const operation = downloadTrustFile('/api/trust/ca.crt', 'local-dashboard.crt', button);
  assert.equal(button.disabled, true);
  if (scenario === 'headers') revoke();
  if (scenario === 'timeout') {
    [...timers.values()][0]();
  } else {
    responsePending.resolve({ok:scenario !== 'refused', status:scenario === 'refused' ? 403 : 200,
      body:{getReader:() => reader}});
  }
  if (scenario === 'body') {
    await Promise.resolve(); await Promise.resolve();
    revoke(); bodyPending.resolve({done:false, value:new Uint8Array(5)});
  }
  await operation;
  if (scenario === 'success') {
    assert.deepEqual(clicks, ['local-dashboard.crt']); assert.equal(urls, 1);
    assert.equal(cancellations, 1);
  } else {
    assert.deepEqual(clicks, []); assert.equal(urls, 0);
  }
  if (scenario === 'oversized') assert.match(element('trust-status').textContent, /too large/);
  if (scenario === 'timeout') assert.match(element('trust-status').textContent, /timed out/);
  if (['headers','body','refused'].includes(scenario)) {
    assert.equal(button.disabled, true);
    assert.equal(element('trust-fingerprint').textContent, '');
  }
}
main(SCENARIO).catch(error => { console.error(error); process.exitCode = 1; });
"""
    program = program.replace("SCENARIO", json.dumps(scenario))
    result = subprocess.run([shutil.which("node"), "-"], input=program, text=True,
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
