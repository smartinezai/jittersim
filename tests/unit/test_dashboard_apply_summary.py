"""Exercise the Apply button wording using the dashboard served to the browser."""

import json
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

from jittersim.api.server import DASHBOARD_HTML

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is required to run the dashboard")


def between(start, end):
    first = SCRIPT.index(start)
    return SCRIPT[first : SCRIPT.index(end, first)]


def served_functions():
    return between("function applySummary(", "function renderRuntimeSummary(")


def served_with_sidebar_and_runtime_summary():
    return between("function sidebarStatus(", "function connectedRowState(")


def run_node(program):
    result = subprocess.run([NODE, "-"], input=program, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def evaluate(expression):
    return run_node(served_functions() + "\nconsole.log(JSON.stringify(%s));" % expression)


STATES = ("applied", "unknown", "error", "applying", "resetting", "reset", "bogus", None)
NEEDS_HELPER = {"text": "Needs the Pi helper to run.", "tone": "muted"}

# Every row of the status table. A state that is not listed for a running flag gives
# "State unknown." with the muted tone.
STATUS = {
    (True, "applied"): (
        "Running. Your saved settings are applied. New changes apply on their own.",
        "ok",
    ),
    # A change that leaves the plan as it was never writes "applied" again so this row
    # has to be true for as long as the scheduler runs.
    (True, "unknown"): ("Running. Your latest change applies on its own.", "ok"),
    (True, "error"): ("The last apply failed. Check the Pi.", "error"),
    (True, "applying"): ("Applying…", "working"),
    (True, "resetting"): ("Resetting…", "working"),
    (False, "applying"): ("Applying…", "working"),
    (False, "resetting"): ("Resetting…", "working"),
    (False, "reset"): ("Off. No traffic shaping is running.", "muted"),
    (False, "unknown"): ("Nothing is running. Choose Start.", "pending"),
    (False, "error"): ("The last start failed. Check the Pi and try again.", "error"),
    (False, "applied"): ("Applied. Waiting for the scheduler.", "muted"),
}


@lru_cache(maxsize=None)
def status_table():
    combos = [(running, state) for running in (True, False) for state in STATES]
    expression = (
        "[%s].map(([running, state]) => ({running, state,"
        " status: runtimeStatusText(state, running),"
        " label: applyButtonLabel(state, running)}))"
        % ",".join("[%s,%s]" % (json.dumps(running), json.dumps(state)) for running, state in combos)
    )
    return {(row["running"], row["state"]): row for row in evaluate(expression)}


@needs_node
@pytest.mark.parametrize("running", [True, False])
@pytest.mark.parametrize("state", STATES)
def test_the_status_text_follows_the_table(running, state):
    text, tone = STATUS.get((running, state), ("State unknown.", "muted"))
    assert status_table()[(running, state)]["status"] == {"text": text, "tone": tone}


@needs_node
def test_a_missing_running_flag_counts_as_not_running():
    result = evaluate(
        '[runtimeStatusText("applied"), runtimeStatusText("unknown", undefined),'
        ' runtimeStatusText("unknown", "yes"), applyButtonLabel("applied")]'
    )
    assert result == [
        {"text": "Applied. Waiting for the scheduler.", "tone": "muted"},
        {"text": "Nothing is running. Choose Start.", "tone": "pending"},
        {"text": "Nothing is running. Choose Start.", "tone": "pending"},
        "Start",
    ]


@needs_node
def test_a_failed_reset_does_not_say_that_the_start_failed():
    # Reset and Panic pause the scheduler before they ask the Pi to remove its rules. So a
    # failed reset leaves the state "error" with running false and the last action "reset".
    failed_reset = evaluate('runtimeStatusText("error", false, {lastAction: "reset"})')
    assert failed_reset == {
        "text": "The last reset failed. The Pi may still be shaping. Check it.",
        "tone": "error",
    }
    others = evaluate(
        '["apply", "schedule", null, undefined].map('
        '(lastAction) => runtimeStatusText("error", false, {lastAction}))'
    )
    assert others == [
        {"text": "The last start failed. Check the Pi and try again.", "tone": "error"}
    ] * 4
    # The button still offers the start because nothing is running.
    assert evaluate('applyButtonLabel("error", false)') == "Start"


@needs_node
@pytest.mark.parametrize("running", [True, False])
@pytest.mark.parametrize("state", STATES)
def test_without_the_pi_helper_the_status_says_so(running, state):
    result = evaluate(
        "runtimeStatusText(%s, %s, {helperConfigured: false, lastAction: 'reset'})"
        % (json.dumps(state), json.dumps(running))
    )
    assert result == NEEDS_HELPER


@needs_node
@pytest.mark.parametrize("context", ["{helperConfigured: true}", "{}", "undefined"])
def test_a_helper_that_is_not_ruled_out_leaves_the_normal_text(context):
    result = evaluate('runtimeStatusText("unknown", false, %s)' % context)
    assert result == {"text": "Nothing is running. Choose Start.", "tone": "pending"}


@needs_node
@pytest.mark.parametrize("running", [True, False])
@pytest.mark.parametrize("state", STATES)
def test_the_button_says_re_apply_only_while_running(running, state):
    # While it runs the button keeps its name even in the second of an apply or a reset.
    expected = (
        "Start"
        if running and state in ("applied", "unknown", "error", "applying", "resetting")
        else "Start"
    )
    assert status_table()[(running, state)]["label"] == expected


NOTHING = "No device profiles or service lists saved yet"


def summary(**fields):
    base = {"devices": 0, "groups": 0, "serviceLists": 0, "protected": 0,
            "generalProfileName": None}
    return evaluate("applySummary(%s)" % json.dumps({**base, **fields}))


@needs_node
def test_nothing_saved_says_so_without_claiming_more_than_it_knows():
    assert summary() == NOTHING


@needs_node
@pytest.mark.parametrize(
    "fields,text",
    [
        ({"devices": 1}, "1 device with a profile"),
        ({"devices": 3}, "3 devices with a profile"),
        ({"groups": 1}, "1 group"),
        ({"groups": 2}, "2 groups"),
        ({"serviceLists": 1}, "1 service list"),
        ({"serviceLists": 4}, "4 service lists"),
        ({"protected": 1}, "1 protected device stays untouched"),
        ({"protected": 2}, "2 protected devices stay untouched"),
        (
            {"devices": 3, "groups": 2, "serviceLists": 1, "protected": 2},
            "3 devices with a profile · 2 groups · 1 service list"
            " · 2 protected devices stay untouched",
        ),
        (
            {"devices": 1, "groups": 1, "serviceLists": 1, "protected": 1},
            "1 device with a profile · 1 group · 1 service list"
            " · 1 protected device stays untouched",
        ),
        ({"devices": 2, "serviceLists": 1}, "2 devices with a profile · 1 service list"),
    ],
)
def test_the_summary_lists_only_what_is_saved(fields, text):
    assert summary(**fields) == text


@needs_node
def test_a_general_profile_reads_as_a_saved_state_and_keeps_the_service_lists():
    base = 'Profile "Office slow" for every unprotected device'
    # The server runs enabled service lists together with the general profile.
    assert summary(generalProfileName="Office slow", devices=3, groups=1) == base
    assert summary(generalProfileName="Office slow", serviceLists=1) == base + " · 1 service list"
    assert summary(generalProfileName="Office slow", serviceLists=2, protected=1) == (
        base + " · 2 service lists · 1 protected device stays untouched"
    )
    assert summary(generalProfileName="Office slow", protected=3) == (
        base + " · 3 protected devices stay untouched"
    )


@needs_node
def test_the_saved_line_says_saved_and_leaves_the_empty_case_alone():
    result = evaluate(
        "[savedLine({devices: 1}), savedLine({}), savedLine({protected: 1}),"
        ' savedLine({generalProfileName: "G"})]'
    )
    assert result == [
        "Saved: 1 device with a profile",
        NOTHING,
        "Saved: 1 protected device stays untouched",
        'Saved: Profile "G" for every unprotected device',
    ]


@needs_node
def test_nothing_saved_still_says_that_access_rules_are_saved():
    # Start enforces the website and MAC rules so the line must not read as an empty start.
    result = evaluate(
        "[savedLine({accessRules: true}), savedLine({accessRules: false}),"
        " savedLine({devices: 1, accessRules: true})]"
    )
    assert result == [
        NOTHING + ". Access rules are saved too.",
        NOTHING,
        "Saved: 1 device with a profile",
    ]


def started(wasRunning="false", **fields):
    base = {"devices": 0, "groups": 0, "serviceLists": 0, "protected": 0,
            "generalProfileName": None, "accessRules": False}
    return evaluate("startedText(%s, %s)" % (json.dumps({**base, **fields}), wasRunning))


@needs_node
@pytest.mark.parametrize(
    "fields,lead,rest",
    [
        ({"devices": 1}, "Started: ", "1 device with a profile."),
        ({"devices": 2, "groups": 1, "serviceLists": 1, "protected": 1}, "Started: ",
         "2 devices with a profile · 1 group · 1 service list"
         " · 1 protected device stays untouched."),
        ({"generalProfileName": "Office"}, "Started: ",
         'Profile "Office" for every unprotected device.'),
        # access rules alone do not change a message that lists what was started
        ({"devices": 1, "accessRules": True}, "Started: ", "1 device with a profile."),
    ],
)
def test_the_message_after_a_start_lists_what_started(fields, lead, rest):
    assert started(**fields) == lead + rest
    assert started("true", **fields) == "Started: " + rest


@needs_node
def test_a_start_with_nothing_to_shape_does_not_pretend_to_list_something():
    empty = "No device profiles or service lists are saved yet."
    assert started() == "Started. " + empty
    assert started("true") == "Started. " + empty
    assert started(accessRules=True) == "Started. " + empty + " Access rules apply."
    assert started("true", accessRules=True) == "Started. " + empty + " Access rules apply."


@needs_node
def test_a_start_with_only_protected_devices_says_so_and_that_nothing_else_is_saved():
    empty = "No device profiles or service lists are saved yet."
    assert started(protected=1) == "Started. 1 protected device stays untouched. " + empty
    assert started("true", protected=3) == (
        "Started. 3 protected devices stay untouched. " + empty
    )
    assert started(protected=2, accessRules=True) == (
        "Started. 2 protected devices stay untouched. " + empty + " Access rules apply."
    )


HARNESS = r"""
const nodes = new Map();
function makeNode(tag) {
  return {
    tag, children: [], textContent: "", className: "", hidden: false, classes: new Set(),
    classList: {toggle(name, on) {on ? this.owner.classes.add(name) : this.owner.classes.delete(name)}},
    appendChild(child) {this.children.push(child)},
    replaceChildren(...children) {this.children = children}
  };
}
const document = {createElement: makeNode};
function element(id) {
  if (!nodes.has(id)) {
    const node = makeNode("div");
    node.classList.owner = node;
    nodes.set(id, node);
  }
  return nodes.get(id);
}
// Counts how often the page writes the text of a node.
function countWrites(id) {
  const node = element(id);
  const counter = {writes: 0};
  let value = node.textContent;
  Object.defineProperty(node, "textContent", {
    get() { return value; },
    set(next) { counter.writes++; value = next; }
  });
  return counter;
}
let adminTokenVerified = true;
let helperConfigured = true;
let helperKnown = true;
let lastRuntimeState = {observed_state: "applied", running: true};
let generalProfileState = {profile: null, active: false, state: {observed_state: "applied"}};
let profiles = [];
let profilesLoaded = true;
let connectedClients = [];
let lastConnectedData = null;
let connectedClientsAvailable = false;
let serviceListsState = [];
let serviceListsLoaded = true;
let savedDevices = [];
let savedDevicesLoaded = true;
let groups = [];
let groupsLoaded = true;
let unaffectedMacs = [];
let accessRulesSaved = false;
let applyBusy = false;
const unansweredPaths = new Set();
"""


def render(setup):
    program = HARNESS + served_with_sidebar_and_runtime_summary() + "\n" + setup
    return run_node(program)


@needs_node
def test_the_line_says_what_is_saved_before_and_after_the_start():
    setup = """
        savedDevices = [
          {id: "a", mac_address: "m1"}, {id: "b", mac_address: "m2"}, {id: "c", mac_address: null}
        ];
        groups = [{id: "g1", device_ids: ["a", "b"]}, {id: "g2", device_ids: ["c"]}];
        unaffectedMacs = ["m2", "m9"];
        serviceListsState = [{name: "Video", enabled: true}, {name: "Off", enabled: false}];
        const seen = [];
        for (const running of [false, true]) {
          lastRuntimeState = {observed_state: "unknown", running};
          renderApplySummary();
          seen.push(element("apply-summary").textContent);
        }
        console.log(JSON.stringify({seen, hidden: element("apply-summary").hidden}));
    """
    line = (
        "Saved: 2 devices with a profile · 2 groups · 1 service list"
        " · 2 protected devices stay untouched"
    )
    # The page counts what is saved. It never claims to know what the server will skip.
    assert render(setup) == {"seen": [line, line], "hidden": False}


@needs_node
def test_a_device_whose_profile_is_paused_is_not_counted():
    out = render(
        """
        savedDevices = [
          {id: "a", mac_address: "m1", effective_state: "active"},
          {id: "b", mac_address: null, effective_state: "unverified"},
          {id: "c", mac_address: "m3", effective_state: "protected"}
        ];
        groups = [{id: "g1", device_ids: ["b"]}];
        unaffectedMacs = ["m3"];
        renderApplySummary();
        console.log(JSON.stringify(element("apply-summary").textContent));
        """
    )
    assert out == (
        "Saved: 1 device with a profile · 1 saved device profile needs an identity check"
        " · 1 protected device stays untouched"
    )


@needs_node
def test_a_group_whose_devices_are_all_protected_is_not_counted():
    out = render(
        """
        savedDevices = [{id: "a", mac_address: "m1"}, {id: "b", mac_address: "m2"}];
        groups = [{id: "g1", device_ids: ["a"]}, {id: "g2", device_ids: ["b"]}];
        unaffectedMacs = ["m2"];
        renderApplySummary();
        console.log(JSON.stringify(element("apply-summary").textContent));
        """
    )
    assert out == "Saved: 1 device with a profile · 1 group · 1 protected device stays untouched"


@needs_node
def test_the_line_names_the_general_profile_as_plain_text_and_counts_the_lists():
    out = render(
        """
        generalProfileState = {profile: {id: "slow", name: "<b>Slow</b>"}, active: true};
        savedDevices = [{id: "a", mac_address: "m1"}];
        serviceListsState = [{name: "Video", enabled: true}];
        renderApplySummary();
        console.log(JSON.stringify(element("apply-summary").textContent));
        """
    )
    assert out == 'Saved: Profile "<b>Slow</b>" for every unprotected device · 1 service list'


@needs_node
def test_the_empty_line_mentions_saved_access_rules_when_there_are_some():
    out = render(
        """
        accessRulesSaved = true;
        renderApplySummary();
        console.log(JSON.stringify(element("apply-summary").textContent));
        """
    )
    assert out == NOTHING + ". Access rules are saved too."


@needs_node
def test_the_empty_line_does_not_repeat_the_word_saved():
    out = render(
        """
        renderApplySummary();
        console.log(JSON.stringify({text: element("apply-summary").textContent,
          hidden: element("apply-summary").hidden}));
        """
    )
    assert out == {"text": NOTHING, "hidden": False}


@needs_node
@pytest.mark.parametrize(
    "unloaded",
    [
        "savedDevicesLoaded = false;",
        "groupsLoaded = false;",
        "unaffectedMacs = null;",
        "serviceListsLoaded = false;",
        "generalProfileState = null;",
        "accessRulesSaved = null;",
    ],
)
def test_the_line_waits_until_every_input_is_loaded(unloaded):
    # A count made before the data arrived would claim that nothing is saved.
    out = render(
        """
        savedDevices = [{id: "a", mac_address: "m1"}];
        renderApplySummary();
        const before = element("apply-summary").textContent;
        %s
        renderApplySummary();
        console.log(JSON.stringify({before, text: element("apply-summary").textContent,
          hidden: element("apply-summary").hidden}));
        """
        % unloaded
    )
    assert out == {"before": "Saved: 1 device with a profile", "text": "", "hidden": True}


@needs_node
def test_the_line_is_empty_and_hidden_while_the_dashboard_is_locked():
    out = render(
        """
        savedDevices = [{id: "a", mac_address: "m1"}];
        renderApplySummary();
        const before = element("apply-summary").textContent;
        adminTokenVerified = false;
        renderApplySummary();
        console.log(JSON.stringify({before, text: element("apply-summary").textContent,
          hidden: element("apply-summary").hidden}));
        """
    )
    assert out == {"before": "Saved: 1 device with a profile", "text": "", "hidden": True}


@needs_node
def test_locking_forgets_the_counts_so_a_new_check_cannot_show_old_ones():
    out = render(
        """
        savedDevices = [{id: "a", mac_address: "m1"}];
        unaffectedMacs = ["m1"];
        adminTokenVerified = false;
        renderSidebarStatus();
        console.log(JSON.stringify({unaffected: unaffectedMacs, devices: savedDevicesLoaded,
          groups: groupsLoaded, lists: serviceListsLoaded, general: generalProfileState,
          rules: accessRulesSaved}));
        """
    )
    assert out == {"unaffected": None, "devices": False, "groups": False, "lists": False,
                   "general": None, "rules": None}


@needs_node
def test_a_refresh_that_changes_nothing_writes_nothing():
    # The line is a live region so each write is announced again.
    out = render(
        """
        savedDevices = [{id: "a", mac_address: "m1"}];
        const line = countWrites("apply-summary");
        const button = countWrites("apply-button");
        const runtime = countWrites("runtime-summary");
        for (let round = 0; round < 4; round++) {
          renderRuntimeSummary({observed_state: "applied", running: true});
          renderApplySummary();
          renderApplyButton();
        }
        const stable = {line: line.writes, button: button.writes, runtime: runtime.writes};
        savedDevices.push({id: "b", mac_address: "m2"});
        renderApplySummary();
        console.log(JSON.stringify({stable, afterAChange: line.writes,
          text: element("apply-summary").textContent}));
        """
    )
    assert out == {
        "stable": {"line": 1, "button": 1, "runtime": 1},
        "afterAChange": 2,
        "text": "Saved: 2 devices with a profile",
    }


@needs_node
def test_a_saved_state_refreshes_the_line_and_the_button_through_the_sidebar_renderer():
    out = render(
        """
        lastRuntimeState = {observed_state: "reset", running: false};
        savedDevices = [{id: "a", mac_address: "m1"}];
        renderSidebarStatus();
        console.log(JSON.stringify({text: element("apply-summary").textContent,
          button: element("apply-button").textContent}));
        """
    )
    assert out == {"text": "Saved: 1 device with a profile", "button": "Start"}


def runtime_run(states):
    return render(
        """
        const seen = [];
        for (const state of %s) {
          renderRuntimeSummary(state);
          seen.push({
            text: element("runtime-summary").textContent,
            className: element("runtime-summary").className,
            button: element("apply-button").textContent,
            stored: lastRuntimeState === state
          });
        }
        console.log(JSON.stringify(seen));
        """
        % json.dumps(states)
    )


@needs_node
def test_the_runtime_summary_uses_the_table_and_remembers_the_running_flag():
    out = runtime_run(
        [
            {"observed_state": "applied", "running": True},
            {"observed_state": "applied"},
            {"observed_state": "unknown", "running": False},
            {"observed_state": "reset", "running": False},
            {"observed_state": "error", "running": True},
            {"observed_state": "unknown", "running": True},
        ]
    )
    assert out == [
        {
            "text": "Running. Your saved settings are applied. New changes apply on their own.",
            "className": "runtime-summary applied tone-ok",
            "button": "Start",
            "stored": True,
        },
        {
            "text": "Applied. Waiting for the scheduler.",
            "className": "runtime-summary applied tone-muted",
            "button": "Start",
            "stored": True,
        },
        {
            "text": "Nothing is running. Choose Start.",
            "className": "runtime-summary unknown tone-pending",
            "button": "Start",
            "stored": True,
        },
        {
            "text": "Off. No traffic shaping is running.",
            "className": "runtime-summary reset tone-muted",
            "button": "Start",
            "stored": True,
        },
        {
            "text": "The last apply failed. Check the Pi.",
            "className": "runtime-summary error tone-error",
            "button": "Start",
            "stored": True,
        },
        {
            "text": "Running. Your latest change applies on its own.",
            "className": "runtime-summary unknown tone-ok",
            "button": "Start",
            "stored": True,
        },
    ]


@needs_node
def test_the_runtime_summary_reads_the_last_action_of_the_state():
    out = runtime_run(
        [
            {"observed_state": "error", "running": False, "last_action": "reset"},
            {"observed_state": "error", "running": False, "last_action": "apply"},
        ]
    )
    assert [row["text"] for row in out] == [
        "The last reset failed. The Pi may still be shaping. Check it.",
        "The last start failed. Check the Pi and try again.",
    ]
    assert [row["button"] for row in out] == ["Start"] * 2
    assert out[0]["className"] == "runtime-summary error tone-error"


@needs_node
def test_without_the_helper_the_summary_says_so_and_a_persisted_state_does_not_linger():
    out = render(
        """
        helperConfigured = false;
        const seen = [];
        for (const state of [{observed_state: "unknown", running: false},
                             {observed_state: "applied", running: false}]) {
          renderRuntimeSummary(state);
          seen.push(element("runtime-summary").textContent);
        }
        console.log(JSON.stringify(seen));
        """
    )
    assert out == ["Needs the Pi helper to run."] * 2


@needs_node
def test_the_helper_is_not_ruled_out_before_the_health_check_answered():
    out = render(
        """
        helperConfigured = false;
        helperKnown = false;
        renderRuntimeSummary({observed_state: "unknown", running: false});
        console.log(JSON.stringify(element("runtime-summary").textContent));
        """
    )
    assert out == "Nothing is running. Choose Start."


@needs_node
def test_the_button_keeps_its_busy_label_while_an_apply_runs():
    out = render(
        """
        applyBusy = true;
        element("apply-button").textContent = "Applying…";
        renderRuntimeSummary({observed_state: "applied", running: true});
        const during = element("apply-button").textContent;
        applyBusy = false;
        renderRuntimeSummary({observed_state: "applied", running: true});
        console.log(JSON.stringify({during, after: element("apply-button").textContent}));
        """
    )
    assert out == {"during": "Applying…", "after": "Start"}


@needs_node
def test_a_locked_dashboard_goes_back_to_the_start_wording():
    out = render(
        """
        renderRuntimeSummary({observed_state: "applied", running: true});
        const before = element("apply-button").textContent;
        adminTokenVerified = false;
        renderSidebarStatus();
        console.log(JSON.stringify({before, after: element("apply-button").textContent}));
        """
    )
    assert out == {"before": "Start", "after": "Start"}


def test_the_summary_code_never_writes_html():
    assert "innerHTML" not in served_functions()
    assert "insertAdjacentHTML" not in served_functions()


def test_the_loaders_that_feed_the_line_refresh_it_and_say_they_loaded():
    # Each loader hands the line its data. The poll would refresh it later but the line
    # should follow a save at once.
    loaders = [
        ("async function loadUnaffectedDevices(", "function updateGroupMemberOptions(",
         "unaffectedMacs = data.devices"),
        ("async function loadDevicesForSchedule(", "async function loadGroups(",
         "savedDevicesLoaded = true;"),
        ("function renderGroups(", "function renderGroupDataLimits(", "groupsLoaded = true;"),
        ("async function loadPolicies(", "async function loadState(", "accessRulesSaved ="),
    ]
    for start, end, flag in loaders:
        body = between(start, end)
        assert flag in body, start
        assert "renderApplySummary();" in body, start


def sidebar_html():
    return re.search(r'<aside class="sidebar".*?</aside>', DASHBOARD_HTML, re.DOTALL).group(0)


def test_the_page_starts_with_the_start_wording_and_a_line_for_the_summary():
    dock = sidebar_html()
    button = re.search(r'<button[^>]*id="apply-button"[^>]*>([^<]*)</button>', dock)
    assert button.group(1) == "Start"
    assert dock.count('id="apply-summary"') == 1
    assert re.search(r'<p id="apply-summary" class="apply-summary"[^>]*></p>', dock)
    assert dock.index('id="apply-button"') < dock.index('id="apply-summary"') < dock.index(
        'id="reset-button"'
    )


def test_the_summary_line_is_a_polite_live_region_and_starts_hidden():
    tag = re.search(r'<p id="apply-summary"[^>]*>', sidebar_html()).group(0)
    assert 'aria-live="polite"' in tag
    assert " hidden" in tag


def test_the_apply_and_reset_buttons_each_have_a_help_icon():
    dock = sidebar_html()
    helps = re.findall(
        r'<span class="help-icon" tabindex="0" role="img" aria-label="([^"]*)"'
        r' data-help="([^"]*)">\?</span>',
        dock,
    )
    by_label = dict(helps)
    apply_help = by_label["Start help"]
    reset_help = by_label["Reset network help"]
    assert len(apply_help) <= 280
    for fact in (
        "Starts everything you saved",
        "device and group profiles",
        "schedules",
        "limits",
        "service lists",
        "access rules",
        "saved changes apply on their own within a few seconds",
        "Reset or Panic stops the profiles",
        "Protected devices stay untouched",
    ):
        assert fact in apply_help, fact
    assert reset_help == (
        "Stops the profiles and removes their rules from the Pi so devices run normally."
        " Household blocking of devices and sites stays on."
        " Your saved settings are kept."
        " Choose Start to run them again."
    )
    # The icon sits right after its button so keyboard users reach it next.
    assert re.search(r'id="apply-button"[^>]*>[^<]*</button>\s*<span class="help-icon"', dock)
    assert re.search(r'id="reset-button"[^>]*>[^<]*</button>\s*<span class="help-icon"', dock)


def test_the_sidebar_tips_stay_in_the_window_and_the_apply_button_keeps_its_height():
    css = re.search(r"<style>(.*?)</style>", DASHBOARD_HTML, re.DOTALL).group(1)
    # The sidebar scrolls so a tip placed inside it is cut off on a short window. The tip
    # is fixed to the window next to the sidebar instead.
    tip = re.search(
        r"\.sidebar \.sidebar-action-row \.help-icon::after \{([^{}]*)\}", css
    ).group(1)
    assert "position: fixed" in tip
    assert "bottom: 1rem" in tip
    assert "left: 16rem" in tip
    # One line and two lines of the label need the same room so the footer does not jump.
    assert re.search(r"#apply-button \{[^{}]*min-height: calc\(2lh \+ 1\.24rem \+ 2px\)", css)


def test_the_sidebar_is_one_column_that_scrolls_as_a_whole():
    # A pinned footer hid the router Wi-Fi switch and the copy buttons on a laptop window with
    # the taller fonts of the CI machine. They stay in the one column below Panic instead and the
    # column scrolls. So the controls that matter are reachable whatever the fonts are.
    dock = sidebar_html()
    assert "sidebar-scroll" not in DASHBOARD_HTML and "sidebar-secondary" not in DASHBOARD_HTML
    order = [dock.index(marker) for marker in (
        'class="sidebar-brand"', '<nav class="sidebar-nav"', 'id="sidebar-status"',
        'id="apply-button"', 'id="reset-button"', 'id="panic-button"',
        'class="router-wifi-control"', 'class="router-wifi-copy"', 'class="sidebar-credentials"',
    )]
    assert order == sorted(order)
    css = re.search(r"<style>(.*?)</style>", DASHBOARD_HTML, re.DOTALL).group(1)
    sidebar_rule = re.search(r"\n    \.sidebar \{([^{}]*)\}", css).group(1)
    assert "overflow-y: auto" in sidebar_rule
    assert "height: 100dvh" in sidebar_rule


def test_a_short_window_gives_the_footer_room_so_panic_stays_in_view():
    css = re.search(r"<style>(.*?)</style>", DASHBOARD_HTML, re.DOTALL).group(1)
    # Up to 1000px of height (a laptop or a full HD screen) the sidebar and its footer use tighter gaps.
    block = re.search(
        r"@media \(min-width: 48\.01rem\) and \(max-height: 1000px\) \{([^@]*?)\n    \}", css
    ).group(1)
    assert re.search(r"\.sidebar \{[^{}]*gap: 0\.5rem", block)
    assert re.search(r"\.sidebar-nav a \{[^{}]*padding-block: 0\.22rem", block)
    assert re.search(r"\.sidebar-footer, \.sidebar-operate, \.sidebar-actions \{ gap: 0\.35rem", block)


def test_panic_says_how_to_run_again():
    button = re.search(r'<button[^>]*id="panic-button"[^>]*>', sidebar_html()).group(0)
    assert (
        'title="Turn off all profiles and household blocking now. Your saved settings are kept."'
        in button
    )


OLD_PHRASES = re.compile(
    r"Apply current configuration|[Aa]pply the configuration|Apply to enforce"
    r"|No network action has run yet|Configuration applied successfully"
)


def flatten(text):
    """Join wrapped lines and drop the bold marks so a phrase split over lines is found."""

    return re.sub(r"[\s*]+", " ", text)


def test_the_page_never_shows_the_old_wording():
    assert OLD_PHRASES.findall(DASHBOARD_HTML) == []


def test_the_scan_sees_a_phrase_that_a_guide_wraps_over_two_lines():
    wrapped = "Choose **Apply\ncurrent configuration** to start."
    assert OLD_PHRASES.findall(wrapped) == []
    assert OLD_PHRASES.findall(flatten(wrapped)) == ["Apply current configuration"]


@pytest.mark.parametrize(
    "path",
    [
        *sorted((ROOT / "src" / "jittersim").rglob("*.py")),
        ROOT / "README.md",
        ROOT / "deploy" / "raspberry-pi" / "README.md",
    ],
    ids=lambda path: str(path.relative_to(ROOT)),
)
def test_no_source_file_or_guide_tells_people_to_press_the_old_button(path):
    assert OLD_PHRASES.findall(flatten(path.read_text(encoding="utf-8"))) == []
