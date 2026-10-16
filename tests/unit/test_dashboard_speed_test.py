"""Check request timing and end messages for the device speed-test dashboard."""

import inspect
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jittersim.api.server import DASHBOARD_HTML, run_server
from jittersim.api.speed_tests import READ_ATTEMPTS, READ_RETRY_SECONDS
from jittersim.networking.helper import HelperClient

SCRIPTS = re.findall(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL)
DASHBOARD_SCRIPT = SCRIPTS[0]
NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")


def run_node(source):
    result = subprocess.run(["node", "-e", source], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@NEEDS_NODE
def test_start_timeout_covers_both_sequential_helper_reads():
    start = DASHBOARD_SCRIPT.index("const REQUEST_TIMEOUT_MS")
    end = DASHBOARD_SCRIPT.index("// The requests the Pi has not answered", start)
    timeout_source = DASHBOARD_SCRIPT[start:end]
    timeouts = run_node(
        timeout_source
        + "\nconsole.log(JSON.stringify(["
        + "requestTimeoutMs('/api/speed-tests/start', 'POST'),"
        + "requestTimeoutMs('/api/speed-tests/start', 'GET'),"
        + "requestTimeoutMs('/api/impact', 'GET'),"
        + "requestTimeoutMs('/api/apply', 'POST')]));"
    )

    helper_timeout = inspect.signature(HelperClient).parameters["timeout_seconds"].default
    bundled_timeout = inspect.signature(run_server).parameters["helper_timeout_seconds"].default
    helper_budget_ms = 2 * (
        READ_ATTEMPTS * helper_timeout + (READ_ATTEMPTS - 1) * READ_RETRY_SECONDS
    ) * 1000

    assert helper_timeout == bundled_timeout == 8.0
    assert timeouts == [75000, 15000, 15000, 45000]
    assert timeouts[0] - helper_budget_ms >= 20000


@NEEDS_NODE
def test_end_message_marks_saved_changed_and_fault_results_incomplete():
    start = DASHBOARD_SCRIPT.index("function speedTestEndMessage(")
    end = DASHBOARD_SCRIPT.index("async function speedTestFinished(", start)
    message_source = DASHBOARD_SCRIPT[start:end]
    messages = run_node(
        message_source
        + "\nconsole.log(JSON.stringify(["
        + "speedTestEndMessage({result: {status: 'complete'}}),"
        + "speedTestEndMessage({result: {status: 'finished-early'}}),"
        + "speedTestEndMessage({result: {status: 'changed'}, ended_reason: 'Queues changed.'}),"
        + "speedTestEndMessage({result: {status: 'fault', summary: {ended_reason: 'Read failed.'}}}),"
        + "speedTestEndMessage({result: null, ended_reason: 'Cancelled. Nothing was saved.'})"
        + "]));"
    )

    assert messages == [
        "Speed test saved.",
        "Speed test saved.",
        "Speed test saved, but may be incomplete. Queues changed.",
        "Speed test saved, but may be incomplete. Read failed.",
        "Cancelled. Nothing was saved.",
    ]


SPEED_TEST_SCRIPT = (
    Path(__file__).resolve().parents[2] / "src" / "jittersim" / "api" / "speed-test.js"
).read_text(encoding="utf-8")
# Just enough of the dashboard for speed-test.js to load. The two overrides below replace the
# window code so the test sees only what speedTestEnd decides.
SPEED_TEST_STUBS = """
const privateDataLockHooks = [];
const connectedRowHooks = [];
const window = {setTimeout: () => 1, clearTimeout: () => {}};
let pending = [];
function request(path, method, body) {
  return new Promise((resolve, reject) => pending.push({path, resolve, reject}));
}
function element() { return null; }
function showMessage() {}
function setButtonBusy() {}
"""
SPEED_TEST_PROBES = """
const finished = [];
let polls = 0;
function speedTestFinished(status) { finished.push(status); speedTestState.run = null; }
async function speedTestPoll() { polls += 1; }
"""


@NEEDS_NODE
def test_a_late_finish_answer_does_not_end_a_run_started_after_it():
    outcome = run_node(
        SPEED_TEST_STUBS + SPEED_TEST_SCRIPT + SPEED_TEST_PROBES + """
(async () => {
  const first = {id: 'first'};
  speedTestState.run = first;
  const ending = speedTestEnd('finish');
  // The poll saw the first run end, then the person started a second one in the same window.
  speedTestState.run = {id: 'second'};
  pending[0].resolve({id: 'first', state: 'ended', result: {id: 7, status: 'finished-early'}});
  let error = null;
  try { await ending; } catch (caught) { error = caught.message; }
  // A refused Finish for the first run must not throw over the second run either.
  const third = {id: 'third'};
  speedTestState.run = third;
  const refused = speedTestEnd('finish');
  speedTestState.run = {id: 'fourth'};
  const failure = new Error('This speed test is not running');
  failure.status = 404;
  pending[1].reject(failure);
  let refusedError = null;
  try { await refused; } catch (caught) { refusedError = caught.message; }
  console.log(JSON.stringify({finished, run: speedTestState.run.id, error, refusedError}));
})();
""")

    assert outcome == {"finished": [], "run": "fourth", "error": None, "refusedError": None}


@NEEDS_NODE
def test_opening_another_device_clears_the_previous_results_first():
    outcome = run_node(
        """
function fakeNode(tag) {
  return {tag, children: [], textContent: '', hidden: false, disabled: false, className: '',
    replaceChildren(...items) { this.children = items; }, focus() {}, append(...items) {
      this.children.push(...items); }};
}
const nodes = {};
const document = {createElement: fakeNode};
"""
        + SPEED_TEST_STUBS.replace("function element() { return null; }",
                                   "function element(id) { return nodes[id] ||= fakeNode('x'); }")
        + SPEED_TEST_SCRIPT
        + """
function speedTestDialog() { return {open: true, showModal() {}}; }
(async () => {
  // Device A's rows are on screen.
  speedTestState.results = [{id: 1}];
  nodes['speed-test-history'] = fakeNode('div');
  nodes['speed-test-history'].children = ['row of device A'];
  nodes['speed-test-baseline'] = fakeNode('select');
  nodes['speed-test-baseline'].children = ['option of device A'];
  speedTestOpen({mac_address: 'b', display_name: 'B'});
  // Device B's results have not arrived yet.
  console.log(JSON.stringify({
    results: speedTestState.results.length,
    history: nodes['speed-test-history'].children.map((child) => child.textContent || child),
    baseline: nodes['speed-test-baseline'].children.length,
    compareDisabled: nodes['speed-test-compare-button'].disabled,
    pending: pending.length,
  }));
})();
""")

    assert outcome == {"results": 0, "history": ["Loading results…"], "baseline": 0,
                       "compareDisabled": True, "pending": 1}
