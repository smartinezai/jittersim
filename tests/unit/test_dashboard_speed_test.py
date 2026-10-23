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
from jittersim.speed_test import VERDICT_LABELS

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
function request(path, method, body, options) {
  return new Promise((resolve, reject) => pending.push({path, method, body, options, resolve, reject}));
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


# Stubs whose element() hands out one fake node per id, so a test can read what the window shows.
# showMessage records the page's toast calls and any unhandled rejection is collected.
FAKE_NODES = """
function fakeNode(tag) {
  return {tag, children: [], textContent: '', hidden: false, disabled: false, className: '', value: '',
    replaceChildren(...items) { this.children = items; }, focus() { this.focused = true; },
    append(...items) { this.children.push(...items); }, setAttribute() {}, addEventListener() {}};
}
const nodes = {};
const shown = [];
const unhandled = [];
process.on('unhandledRejection', (reason) => unhandled.push(reason.message));
const tick = () => new Promise((resolve) => setImmediate(resolve));
const text = (id) => (nodes[id] || {}).textContent;
const document = {createElement: fakeNode};
"""


def stubs_with_nodes():
    return FAKE_NODES + SPEED_TEST_STUBS.replace(
        "function element() { return null; }",
        "function element(id) { return nodes[id] ||= fakeNode('x'); }",
    ).replace("function showMessage() {}", "function showMessage(message, kind) { shown.push([message, kind]); }")


@NEEDS_NODE
def test_an_error_shows_in_the_open_window_and_only_toasts_when_no_window_is_open():
    outcome = run_node(
        stubs_with_nodes() + SPEED_TEST_SCRIPT + """
speedTestState.dialog = {open: true};
speedTestSay('Measuring.');
const afterStatus = [text('speed-test-announce'), text('speed-test-alert')];
speedTestSay('The Pi did not answer.', 'error');
const afterError = [text('speed-test-announce'), text('speed-test-alert')];
speedTestSay('Result deleted.');
const afterNext = [text('speed-test-announce'), text('speed-test-alert')];
const toastsWhileOpen = shown.length;
speedTestState.dialog = {open: false};
speedTestSay('The window was closed.', 'error');
console.log(JSON.stringify({afterStatus, afterError, afterNext, toastsWhileOpen, shown}));
""")

    assert outcome == {
        "afterStatus": ["Measuring.", ""],
        "afterError": ["", "The Pi did not answer."],
        "afterNext": ["Result deleted.", ""],
        "toastsWhileOpen": 0,
        "shown": [["The window was closed.", "error"]],
    }


ENDED_RUN = """{state: 'ended', seconds_left: 0, source: 'device', source_label: 'Own queues',
  source_note: '', live: {download: {kbit: 1, peak_kbit: 1}, upload: {kbit: 1, peak_kbit: 1}},
  result: null, ended_reason: 'Cancelled. Nothing was saved.'}"""


@NEEDS_NODE
def test_a_failed_reload_after_the_poll_sees_the_end_is_shown_and_never_unhandled():
    outcome = run_node(
        stubs_with_nodes() + SPEED_TEST_SCRIPT + """
(async () => {
  speedTestState.dialog = {open: true};
  speedTestState.device = {mac_address: 'aa'};
  const seen = [];
  // The poll runs from a timer and nobody awaits it. The end is seen in the status answer
  // first and as a 404 second; each time reading the results again then fails.
  for (const failure of [null, 404]) {
    speedTestState.run = {id: 'r' + seen.length};
    speedTestPoll();
    await tick();
    if (failure) {
      const gone = new Error('This speed test is not known');
      gone.status = failure;
      pending.shift().reject(gone);
    } else {
      pending.shift().resolve(%s);
    }
    await tick();
    pending.shift().reject(new Error('The results could not be read.'));
    await tick();
    await tick();
    seen.push([text('speed-test-alert'), speedTestState.run]);
    speedTestSay('');
  }
  console.log(JSON.stringify({seen, unhandled, shown}));
})();
""" % ENDED_RUN)

    assert outcome == {
        "seen": [["The results could not be read.", None]] * 2,
        "unhandled": [],
        "shown": [],
    }


@NEEDS_NODE
def test_a_length_that_is_not_a_whole_number_is_refused_not_replaced_by_the_default():
    outcome = run_node(
        stubs_with_nodes() + SPEED_TEST_SCRIPT + """
(async () => {
  speedTestState.device = {mac_address: 'aa'};
  speedTestState.meta = {limits: {min_seconds: 15, max_seconds: 90, default_seconds: 45}};
  nodes['speed-test-provider'] = {value: 'cloudflare'};
  nodes['speed-test-label'] = {value: ''};
  const field = nodes['speed-test-duration'] = fakeNode('input');
  const refused = {};
  for (const typed of ['45.5', '', '1e2', '-5', 'abc', '4 5']) {
    field.value = typed;
    field.focused = false;
    try { await speedTestStart(); refused[typed] = 'started'; }
    catch (error) { refused[typed] = [error.message, field.focused]; }
  }
  const requested = pending.length;
  field.value = ' 30 ';
  speedTestStart().catch(() => {});
  await tick();
  console.log(JSON.stringify({refused, requested, body: pending[0].body, after: pending.length}));
})();
""")

    message = "Length must be a whole number of seconds from 15 to 90."
    assert outcome["refused"] == {
        typed: [message, True] for typed in ["45.5", "", "1e2", "-5", "abc", "4 5"]
    }
    assert outcome["requested"] == 0
    assert outcome["after"] == 1
    assert outcome["body"]["duration_seconds"] == 30


@NEEDS_NODE
def test_the_export_goes_through_request_so_its_errors_and_sign_in_lock_are_the_pages():
    outcome = run_node(
        stubs_with_nodes() + SPEED_TEST_SCRIPT + """
(async () => {
  globalThis.fetch = () => { throw new Error('the export must not call fetch itself'); };
  const links = [];
  document.createElement = (tag) => { const node = fakeNode(tag); node.click = () => { node.clicked = true; };
    node.remove = () => {}; links.push(node); return node; };
  document.body = {append() {}};
  URL.createObjectURL = () => 'blob:test';
  URL.revokeObjectURL = () => {};
  speedTestState.device = {mac_address: 'aa:bb'};
  speedTestState.dialog = {open: true};
  const failing = speedTestExport('csv');
  await tick();
  const first = {path: pending[0].path, method: pending[0].method, options: pending[0].options};
  pending.shift().reject(new Error('Sign in again'));
  let error = null;
  try { await failing; } catch (caught) { error = caught.message; }
  const working = speedTestExport('json');
  await tick();
  pending.shift().resolve(new Blob(['{}']));
  await working;
  console.log(JSON.stringify({first, error, link: [links[0].download, links[0].clicked],
    status: text('speed-test-announce')}));
})();
""")

    assert outcome == {
        "first": {"path": "/api/speed-tests/export?format=csv&mac=aa%3Abb", "method": "GET",
                  "options": {"download": True}},
        "error": "Sign in again",
        "link": ["device-speed-tests.json", True],
        "status": "Export downloaded.",
    }


@NEEDS_NODE
def test_a_dashboard_run_does_not_blame_the_internet_for_a_slow_speed():
    outcome = run_node(
        stubs_with_nodes() + SPEED_TEST_SCRIPT + """
(async () => {
  speedTestState.meta = {verdict_labels: %s};
  const slow = {download: {verdict: 'below-cap', busy_average_kbit: 1000}};
  const row = (provider) => speedTestDirection({provider, summary: slow}, 'download')[1];
  speedTestCompare();
  pending.shift().resolve({
    changes: [], warnings: [], baseline: {provider: 'fast-com'}, result: {provider: 'dashboard'},
    verdicts: {download: {baseline: 'below-cap', result: 'below-cap'},
               upload: {baseline: null, result: null}}});
  await tick();
  const box = nodes['speed-test-comparison'];
  console.log(JSON.stringify({dashboard: row('dashboard'), site: row('fast-com'),
    comparison: box.children[1].children[0].textContent}));
})();
""" % json.dumps(VERDICT_LABELS))

    assert outcome == {
        "dashboard": "Slower than the cap: the Pi's Wi-Fi or the device was the limit",
        "site": VERDICT_LABELS["below-cap"],
        "comparison": "Download against the cap: before " + VERDICT_LABELS["below-cap"]
        + "; after " + VERDICT_LABELS["below-cap-dashboard"] + ".",
    }
