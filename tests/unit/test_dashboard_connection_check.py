"""The connection check window: its messages, its guards against late answers and its button."""

import inspect
from pathlib import Path

from jittersim.api.dashboard import assemble_dashboard
from jittersim.api.speed_tests import READ_ATTEMPTS, READ_RETRY_SECONDS
from jittersim.networking.helper import HelperClient
from tests.unit.test_dashboard_speed_test import (
    DASHBOARD_SCRIPT,
    NEEDS_NODE,
    SPEED_TEST_SCRIPT,
    SPEED_TEST_STUBS,
    run_node,
)

API = Path(__file__).resolve().parents[2] / "src" / "jittersim" / "api"
CHECK_SCRIPT = (API / "connection-check.js").read_text(encoding="utf-8")
# The window needs the speed test script's two small helpers. Both scripts load like the page does.
LOADED = SPEED_TEST_STUBS + SPEED_TEST_SCRIPT + CHECK_SCRIPT

FAKE_NODES = """
function fakeNode(tag) {
  const node = {tag, children: [], textContent: '', hidden: false, disabled: false, className: '',
    dataset: {}, attributes: {},
    replaceChildren(...items) { this.children = items; },
    focus() {},
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener() {},
    append(...items) { this.children.push(...items); },
    querySelector(selector) {
      const wanted = selector.startsWith('.') ? selector.slice(1) : selector;
      const found = [];
      const walk = (item) => {
        if (!item || typeof item !== 'object') return;
        if (item.tag === wanted || (item.className || '').split(' ').includes(wanted)) found.push(item);
        (item.children || []).forEach(walk);
      };
      this.children.forEach(walk);
      return found[0] || null;
    }};
  return node;
}
const document = {createElement: fakeNode};
"""


def test_the_script_is_assembled_into_the_page_and_follows_the_speed_test_script():
    page = assemble_dashboard()
    assert "function connCheckOpen(" in page
    assert page.index("function speedTestOpen(") < page.index("function connCheckOpen(")
    assert ".conn-check-verdict" in page
    # Strict CSP: the window uses no inline handler and no inline style.
    assert "onclick" not in CHECK_SCRIPT and ".style." not in CHECK_SCRIPT
    assert "innerHTML" not in CHECK_SCRIPT and "window.confirm" not in CHECK_SCRIPT


@NEEDS_NODE
def test_the_start_gets_the_long_browser_timeout_of_the_speed_test():
    start = DASHBOARD_SCRIPT.index("const REQUEST_TIMEOUT_MS")
    end = DASHBOARD_SCRIPT.index("// The requests the Pi has not answered", start)
    source = DASHBOARD_SCRIPT[start:end]
    timeouts = run_node(
        source + "\nconsole.log(JSON.stringify(["
        "requestTimeoutMs('/api/connection-checks/start', 'POST'),"
        "requestTimeoutMs('/api/connection-checks/start', 'GET'),"
        "requestTimeoutMs('/api/connection-checks', 'GET'),"
        "requestTimeoutMs('/api/connection-checks/cancel', 'POST')]));")
    helper_timeout = inspect.signature(HelperClient).parameters["timeout_seconds"].default
    budget_ms = 2 * (READ_ATTEMPTS * helper_timeout + (READ_ATTEMPTS - 1) * READ_RETRY_SECONDS) * 1000
    assert timeouts == [75000, 15000, 15000, 15000]
    assert timeouts[0] - budget_ms >= 20000


@NEEDS_NODE
def test_end_messages_say_when_a_check_was_not_judged():
    start = CHECK_SCRIPT.index("function connCheckEndMessage(")
    end = CHECK_SCRIPT.index("async function connCheckFinished(", start)
    messages = run_node(
        CHECK_SCRIPT[start:end] + """
console.log(JSON.stringify([
  connCheckEndMessage({result: {status: 'complete', summary: {verdict_text: 'Matches the profile'}}}),
  connCheckEndMessage({result: {status: 'complete', summary: {}}}),
  connCheckEndMessage({result: {status: 'changed'}, ended_reason: 'The profile changed.'}),
  connCheckEndMessage({result: {status: 'fault'}}),
  connCheckEndMessage({result: null, ended_reason: 'Cancelled. Nothing was saved.'}),
  connCheckEndMessage({result: null})
]));""")
    assert messages == [
        "Check saved. Matches the profile.",
        "Check saved.",
        "Check saved, but not judged. The profile changed.",
        "Check saved, but not judged.",
        "Cancelled. Nothing was saved.",
        "Check ended.",
    ]


@NEEDS_NODE
def test_a_late_cancel_answer_does_not_end_a_check_started_after_it():
    outcome = run_node(
        LOADED + """
const finished = [];
let polls = 0;
function connCheckFinished(status) { finished.push(status); connCheckState.run = null; }
async function connCheckPoll() { polls += 1; }
(async () => {
  connCheckState.run = {id: 'first'};
  const ending = connCheckCancel();
  // The first check ended by itself and the person started a second one in the same window.
  connCheckState.run = {id: 'second'};
  pending[0].resolve({id: 'first', state: 'ended', result: {id: 7}});
  let error = null;
  try { await ending; } catch (caught) { error = caught.message; }
  // A refused cancel for an older run must not throw over the newer run either.
  connCheckState.run = {id: 'third'};
  const refused = connCheckCancel();
  connCheckState.run = {id: 'fourth'};
  const failure = new Error('This connection check is not running');
  failure.status = 404;
  pending[1].reject(failure);
  let refusedError = null;
  try { await refused; } catch (caught) { refusedError = caught.message; }
  console.log(JSON.stringify({finished, run: connCheckState.run.id, error, refusedError, polls}));
})();
""")
    assert outcome == {"finished": [], "run": "fourth", "error": None, "refusedError": None,
                       "polls": 0}


@NEEDS_NODE
def test_a_cancel_that_finds_the_run_gone_or_ended_asks_again_instead_of_sticking():
    outcome = run_node(
        LOADED + """
const finished = [];
let polls = 0;
function connCheckFinished(status) { finished.push(status.ended_reason); connCheckState.run = null; }
async function connCheckPoll() { polls += 1; }
(async () => {
  connCheckState.run = {id: 'gone'};
  const gone = connCheckCancel();
  const missing = new Error('not running'); missing.status = 404;
  pending[0].reject(missing);
  await gone;
  connCheckState.run = {id: 'ended'};
  const ended = connCheckCancel();
  const conflict = new Error('already ended'); conflict.status = 409;
  pending[1].reject(conflict);
  await ended;
  console.log(JSON.stringify({finished, polls}));
})();
""")
    assert outcome == {"finished": ["This check is no longer running."], "polls": 1}


@NEEDS_NODE
def test_opening_another_device_clears_the_previous_results_first():
    outcome = run_node(
        FAKE_NODES
        + SPEED_TEST_STUBS.replace("function element() { return null; }",
                                   "function element(id) { return nodes[id] ||= fakeNode('x'); }"
                                   ).replace("const privateDataLockHooks",
                                             "const nodes = {};\nconst privateDataLockHooks")
        + SPEED_TEST_SCRIPT + CHECK_SCRIPT + """
function connCheckDialog() { return {open: true, showModal() {}}; }
(async () => {
  connCheckState.results = [{id: 1}];
  nodes['conn-check-history'] = fakeNode('div');
  nodes['conn-check-history'].children = ['row of device A'];
  nodes['conn-check-result'] = fakeNode('div');
  nodes['conn-check-result'].children = ['result of device A'];
  connCheckOpen({mac_address: 'b', display_name: 'B'});
  console.log(JSON.stringify({
    results: connCheckState.results.length,
    history: nodes['conn-check-history'].children.map((child) => child.textContent || child),
    result: nodes['conn-check-result'].children.length,
    resultHidden: nodes['conn-check-result'].hidden,
    title: nodes['conn-check-title'].textContent,
    pending: pending.length,
  }));
})();
""")
    assert outcome == {"results": 0, "history": ["Loading checks…"], "result": 0,
                       "resultHidden": True, "title": "Check connection: B", "pending": 1}


@NEEDS_NODE
def test_the_button_joins_the_test_speed_line_and_explains_what_it_cannot_do():
    outcome = run_node(
        FAKE_NODES + SPEED_TEST_STUBS + SPEED_TEST_SCRIPT + CHECK_SCRIPT + """
function describe(device) {
  const details = fakeNode('div');
  connectedRowHooks.forEach((hook) => hook(device, details));
  const lines = details.children.filter((child) => child.className === 'speed-test-line');
  const buttons = [];
  const notes = [];
  const walk = (node) => {
    if (node.tag === 'button') buttons.push([node.textContent, node.disabled]);
    if (node.tag === 'small') notes.push(node.textContent);
    (node.children || []).forEach(walk);
  };
  details.children.forEach(walk);
  return {lines: lines.length, buttons, notes};
}
const base = {mac_address: 'aa', display_name: 'Phone', ipv4_address: '192.168.50.10'};
console.log(JSON.stringify({
  connected: describe({...base, association_verified: true}),
  protectedDevice: describe({...base, association_verified: true, unaffected: true}),
  unknown: describe({...base, association_verified: false}),
  noMac: describe({display_name: 'No MAC', association_verified: true}),
}));
""")
    assert outcome["connected"] == {
        "lines": 1, "buttons": [["Test speed", False], ["Check connection", False]], "notes": []}
    # The check works for a protected device: it shows the plain Wi-Fi path.
    assert outcome["protectedDevice"]["buttons"] == [["Test speed", True],
                                                     ["Check connection", False]]
    assert any("plain Wi-Fi path" in note for note in outcome["protectedDevice"]["notes"])
    # An unknown connection status disables both and says so once.
    assert outcome["unknown"]["buttons"] == [["Test speed", True], ["Check connection", True]]
    assert len(outcome["unknown"]["notes"]) == 1
    assert outcome["noMac"] == {"lines": 0, "buttons": [], "notes": []}


@NEEDS_NODE
def test_a_result_is_shown_as_text_with_a_status_word_for_every_number():
    outcome = run_node(
        FAKE_NODES + SPEED_TEST_STUBS.replace(
            "function element() { return null; }",
            "function element(id) { return nodes[id] ||= fakeNode('x'); }"
        ).replace("const privateDataLockHooks", "const nodes = {};\nconst privateDataLockHooks")
        + SPEED_TEST_SCRIPT + CHECK_SCRIPT + """
const result = {id: 4, started_at: 1700000000, label: 'Before', profile: {profile_name: 'Lossy'},
  summary: {verdict: 'less', verdict_text: 'Less than set',
    probes: {sent: 100, received: 100, lost: 0},
    metrics: [{id: 'loss', label: 'Packet loss', set: '10%', measured: '0% (0 of 100 lost)',
               status: 'less', status_label: 'Less than set', sentence: 'Less loss than set.'}],
    notes: ['Only the download side can be checked.']}};
connCheckShowResult(result);
const texts = [];
const walk = (node) => {
  if (typeof node === 'string') { texts.push(node); return; }
  if (node.textContent) texts.push(node.textContent);
  (node.children || []).forEach(walk);
};
walk(nodes['conn-check-result']);
console.log(JSON.stringify({texts, hidden: nodes['conn-check-result'].hidden,
  shown: connCheckState.shown}));
""")
    joined = " | ".join(outcome["texts"])
    for expected in ("Less than set", "Packet loss", "10%", "0% (0 of 100 lost)",
                     "Less loss than set.", "100 probes sent, 100 answered, 0 lost",
                     "Profile: Lossy", "Only the download side can be checked."):
        assert expected in joined, expected
    assert outcome["hidden"] is False and outcome["shown"] == 4
