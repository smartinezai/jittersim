"""Keep valid device controls independent of optional counter readings."""

import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
WORKSPACE = SCRIPT[
    SCRIPT.index("let workspaceDevice = null;") : SCRIPT.index("function populateWorkflowMenus()")
]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
SETUP = r"""
const assert = require('node:assert/strict');
let adminTokenRevision = 1, adminTokenVerified = true;
const privateDataLockHooks = [], formDrafts = new Map();
const nodes = new Map();
function element(id) {
  if (!nodes.has(id)) nodes.set(id, {textContent:'', dataset:{}, children:[], style:{},
    replaceChildren(...items) { this.children = items; },
    append(...items) { this.children.push(...items); }, focus(){}, scrollIntoView(){}});
  return nodes.get(id);
}
function textNode(tag, text) { return {tag, textContent:text}; }
function actionButton(text, action) { return {textContent:text, action}; }
function replaceIfChanged(node, key, render) { render(); }
function formatTimerRemaining() { return '5 minutes'; }
function currentPage() { return 'devices'; }
function showTab() {}
function populateWorkflowMenus() {}
function renderWorkspaceState() {}
const devices = [];
const deviceConditions = {applied:true};
const data = {timers:[{id:'timer',profile_name:'Slow',remaining_seconds:300}], runs:[],
  progressions:[], data_limits:[{used_bytes:1048576,limit_bytes:2097152,enabled:true}],
  group_data_limits:[], service_quotas:[], time_quotas:[],
  timeline:{changes:[{at:1,profile:'Normal',reason:'Schedule',overridden_schedules:[]}],note:'Preview'}};
let request = async () => data;
let trafficCalls = 0;
let sample = {sampled_at:1, devices:{A:{upload_bytes:100,download_bytes:200}}};
let readTraffic = () => { trafficCalls++; return Promise.resolve(sample); };
const tick = () => new Promise(resolve => setImmediate(resolve));
function controlsVisible() {
 assert.equal(element('workspace-runs').children[1].textContent, 'Stop timer');
 assert.match(element('workspace-usage').children[0].textContent, /1.0 \/ 2.0 MB/);
 assert.equal(element('workspace-timeline').children.length, 1);
}
"""


def run_case(case):
    result = subprocess.run(
        [
            "node",
            "-e",
            SETUP
            + WORKSPACE
            + "\n(async () => {\n"
            + case
            + "\n})().then(() => console.log('completed')).catch(error => {"
            + " console.error(error); process.exitCode = 1; });",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "completed" in result.stdout, "workspace waited for optional traffic"


def test_traffic_failure_keeps_controls_and_recovery_starts_fresh():
    run_case(r"""
workspaceDevice = 'A';
await loadWorkspace(); await tick();
sample = {sampled_at:2, devices:{A:{upload_bytes:1000100,download_bytes:2000200}}};
await loadWorkspace(); await tick();
assert.match(element('workspace-traffic').textContent, /8.00 Mbit/);
readTraffic = () => Promise.reject(new Error('Counters unavailable'));
await loadWorkspace(); await tick();
controlsVisible();
assert.match(element('workspace-traffic').textContent, /unavailable/i);
assert.equal(previousWorkspaceTraffic, null);
readTraffic = () => Promise.resolve(sample);
await loadWorkspace(); await tick();
assert.equal(element('workspace-traffic').textContent, 'Waiting for traffic samples.');
""")


def test_delayed_traffic_does_not_delay_controls_or_accumulate_readers():
    run_case(r"""
workspaceDevice = 'A';
let finish;
readTraffic = () => { trafficCalls++; return new Promise(resolve => { finish = resolve; }); };
let done = false;
loadWorkspace().then(() => { done = true; });
await tick();
controlsVisible();
assert.equal(done, true);
for (let i = 0; i < 10; i++) await loadWorkspace();
assert.equal(trafficCalls, 1);
finish(sample); await tick();
""")


@pytest.mark.parametrize(
    "change",
    [
        "workspaceDevice = 'B';",
        "adminTokenRevision++;",
        "adminTokenVerified = false; privateDataLockHooks.forEach(hook => hook());"
        " adminTokenVerified = true;",
        "await openDevice('B'); await openDevice('A');",
    ],
)
@pytest.mark.parametrize("traffic_fails", [False, True])
def test_stale_workspace_cannot_render_even_when_traffic_rejects(change, traffic_fails):
    run_case(
        r"""
workspaceDevice = 'A';
let finish;
request = () => new Promise(resolve => { finish = resolve; });
readTraffic = () => TRAFFIC_RESULT;
const pending = loadWorkspace();
""".replace(
            "TRAFFIC_RESULT",
            "Promise.reject(new Error('Stale counters'))"
            if traffic_fails
            else "Promise.resolve(sample)",
        )
        + change
        + r"""
finish(data); await pending.catch(() => {}); await tick();
assert.equal(element('workspace-runs').children.length, 0);
assert.equal(previousWorkspaceTraffic, null);
"""
    )


@pytest.mark.parametrize(
    "change",
    [
        "await openDevice('B'); await openDevice('A');",
        "adminTokenRevision++;",
        "privateDataLockHooks.forEach(hook => hook());",
    ],
)
@pytest.mark.parametrize("reject", [False, True])
def test_delayed_traffic_from_previous_context_cannot_replace_new_baseline(reject, change):
    run_case(
        r"""
workspaceDevice = 'A';
let finish, fail;
readTraffic = () => new Promise((resolve, reject) => { finish = resolve; fail = reject; });
loadWorkspace(); await tick();
"""
        + change
        + r"""
previousWorkspaceTraffic = {at:99,counters:{upload_bytes:123,download_bytes:456}};
element('workspace-traffic').textContent = 'New view';
"""
        + ("fail(new Error('Old failure'));" if reject else "finish(sample);")
        + r"""
await tick();
assert.equal(element('workspace-traffic').textContent, 'New view');
assert.equal(previousWorkspaceTraffic.at, 99);
"""
    )
