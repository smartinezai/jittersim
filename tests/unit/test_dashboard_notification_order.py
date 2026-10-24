"""A delayed refresh must not replace a newer action's notification."""

import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
START = SCRIPT.index("function refreshAfterSuccess(message) {")
FUNCTION = SCRIPT[START:SCRIPT.index("function setButtonBusy(", START)]
HARNESS = r"""
const assert = require('node:assert/strict');
let messageSerial = 0, adminTokenRevision = 0;
let adminTokenVerified = true, helperConfigured = true, trafficSamplingAllowed = true;
let samplingStarts = 0, hidden = false;
const messages = [], reads = [], recentWrites = new Set(['/api/profiles']);
const readsAfterWrites = paths => new Set(paths);
function showMessage(text, kind) { messageSerial++; hidden = false; messages.push({text, kind}); }
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
function refresh() { const read = deferred(); reads.push(read); return read.promise; }
function startTrafficSampling() { samplingStarts++; return Promise.resolve(); }
const flush = () => new Promise(resolve => setImmediate(resolve));
"""

CASES = {
    "current_failure_is_reported_in_full": """
      refreshAfterSuccess('Profile saved.');
      reads[0].reject(new Error('workspace unavailable')); await flush();
      assert.deepEqual(messages.at(-1), {
        text: 'Profile saved. Dashboard refresh failed: workspace unavailable', kind: 'error'
      });
    """,
    "newer_success_survives_old_failure": """
      refreshAfterSuccess('Older profile saved.');
      showMessage('Newer profile saved.', 'success');
      reads[0].reject(new Error('old read failed')); await flush();
      assert.equal(messages.length, 2);
      assert.deepEqual(messages.at(-1), {text: 'Newer profile saved.', kind: 'success'});
    """,
    "newer_error_survives_old_failure": """
      refreshAfterSuccess('Older profile saved.');
      showMessage('Newer action failed: check its settings.', 'error');
      reads[0].reject(new Error('old read failed')); await flush();
      assert.equal(messages.length, 2);
      assert.deepEqual(messages.at(-1), {
        text: 'Newer action failed: check its settings.', kind: 'error'
      });
    """,
    "latest_shared_refresh_failure_is_reported": """
      const shared = deferred(); refresh = () => shared.promise;
      refreshAfterSuccess('Older action saved.'); refreshAfterSuccess('Newer action saved.');
      shared.reject(new Error('shared read failed')); await flush();
      assert.equal(messages.length, 3);
      assert.deepEqual(messages.at(-1), {
        text: 'Newer action saved. Dashboard refresh failed: shared read failed', kind: 'error'
      });
    """,
    "changed_token_discards_old_failure": """
      refreshAfterSuccess('Private action saved.');
      adminTokenRevision++; adminTokenVerified = false;
      reads[0].reject(new Error('old token refused')); await flush();
      assert.equal(messages.length, 1);
    """,
    "changed_token_cannot_restart_sampling": """
      trafficSamplingAllowed = false; refreshAfterSuccess('Private action saved.');
      adminTokenRevision++; // a different token has already been accepted
      reads[0].resolve(); await flush(); assert.equal(samplingStarts, 0);
    """,
    "normal_refresh_restarts_sampling": """
      trafficSamplingAllowed = false; refreshAfterSuccess('Saved.');
      reads[0].resolve(); await flush(); assert.equal(samplingStarts, 1);
    """,
    "newer_notice_does_not_block_same_token_sampling": """
      trafficSamplingAllowed = false; refreshAfterSuccess('Saved.');
      showMessage('A newer action failed.', 'error');
      reads[0].resolve(); await flush(); assert.equal(samplingStarts, 1);
      assert.equal(messages.at(-1).text, 'A newer action failed.');
    """,
    "dismissal_keeps_current_failure_actionable": """
      refreshAfterSuccess('Saved.'); hidden = true; // dismissal does not change messageSerial
      reads[0].reject(new Error('current read failed')); await flush();
      assert.equal(hidden, false);
      assert.equal(messages.at(-1).text, 'Saved. Dashboard refresh failed: current read failed');
    """,
    "current_authentication_failure_is_not_suppressed": """
      refreshAfterSuccess('Saved.'); adminTokenVerified = false;
      reads[0].reject(new Error('token refused')); await flush();
      assert.equal(messages.at(-1).text, 'Saved. Dashboard refresh failed: token refused');
    """,
}


@pytest.mark.parametrize("case", CASES)
def test_refresh_notification_ownership(case):
    program = (HARNESS + FUNCTION + "\n(async () => {\n" + CASES[case]
               + "\n})().catch(error => { console.error(error); process.exitCode = 1; });\n")
    result = subprocess.run([NODE, "-"], input=program, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
