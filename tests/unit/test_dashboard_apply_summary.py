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


def served_functions():
    start = SCRIPT.index("function applySummary(")
    return SCRIPT[start : SCRIPT.index("function renderRuntimeSummary(", start)]


def served_with_sidebar_and_runtime_summary():
    start = SCRIPT.index("function sidebarStatus(")
    return SCRIPT[start : SCRIPT.index("function connectedRowState(", start)]


def run_node(program):
    result = subprocess.run([NODE, "-"], input=program, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def evaluate(expression):
    return run_node(served_functions() + "\nconsole.log(JSON.stringify(%s));" % expression)


STATES = ("applied", "unknown", "error", "applying", "resetting", "reset", "bogus", None)

# Every row of the status table. A state that is not listed for a running flag gives
# "State unknown." with the muted tone.
STATUS = {
    (True, "applied"): (
        "Running. Your saved settings are applied. New changes apply on their own.",
        "ok",
    ),
    (True, "unknown"): ("Applying your latest change…", "working"),
    (True, "error"): ("The last apply failed. Check the Pi.", "error"),
    (True, "applying"): ("Applying…", "working"),
    (True, "resetting"): ("Resetting…", "working"),
    (False, "applying"): ("Applying…", "working"),
    (False, "resetting"): ("Resetting…", "working"),
    (False, "reset"): ("Off. No traffic shaping is running.", "muted"),
    (False, "unknown"): ("Not started. Choose Start with saved settings.", "pending"),
    (False, "error"): ("The last start failed. Check the Pi and try again.", "error"),
    (False, "applied"): ("Applied. Waiting for the scheduler.", "muted"),
}


@lru_cache(maxsize=None)
def status_table():
    combos = [(running, state) for running in (True, False) for state in STATES]
    expression = "[%s].map(([running, state]) => ({running, state, status: runtimeStatusText(" \
        "state, running), label: applyButtonLabel(state, running)}))" % ",".join(
            "[%s,%s]" % (json.dumps(running), json.dumps(state)) for running, state in combos
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
        {"text": "Not started. Choose Start with saved settings.", "tone": "pending"},
        {"text": "Not started. Choose Start with saved settings.", "tone": "pending"},
        "Start with saved settings",
    ]


@needs_node
@pytest.mark.parametrize("running", [True, False])
@pytest.mark.parametrize("state", STATES)
def test_the_button_says_re_apply_only_while_running(running, state):
    expected = (
        "Re-apply saved settings"
        if running and state in ("applied", "unknown", "error")
        else "Start with saved settings"
    )
    assert status_table()[(running, state)]["label"] == expected


def summary(**fields):
    base = {"devices": 0, "groups": 0, "serviceLists": 0, "protected": 0,
            "generalProfileName": None}
    return evaluate("applySummary(%s)" % json.dumps({**base, **fields}))


@needs_node
def test_nothing_saved_says_so():
    assert summary() == "Nothing saved to apply yet"


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
def test_a_general_profile_is_named_instead_of_counting_devices():
    assert summary(generalProfileName="Office slow", devices=3, groups=1, serviceLists=2) == (
        'Applies the general profile "Office slow" to every device that is not protected'
    )
    assert summary(generalProfileName="Office slow", protected=1) == (
        'Applies the general profile "Office slow" to every device that is not protected'
        " · 1 protected device stays untouched"
    )
    assert summary(generalProfileName="Office slow", protected=3).endswith(
        " · 3 protected devices stay untouched"
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
let adminTokenVerified = true;
let lastRuntimeState = {observed_state: "applied", running: true};
let generalProfileState = null;
let profiles = [];
let connectedClients = [];
let serviceListsState = [];
let savedDevices = [];
let groups = [];
let unaffectedMacs = [];
let applyBusy = false;
"""


def render(setup):
    program = HARNESS + served_with_sidebar_and_runtime_summary() + "\n" + setup
    return run_node(program)


@needs_node
def test_the_line_under_the_button_says_what_will_run_before_the_start():
    out = render(
        """
        lastRuntimeState = {observed_state: "unknown", running: false};
        savedDevices = [
          {id: "a", mac_address: "m1"}, {id: "b", mac_address: "m2"}, {id: "c", mac_address: null}
        ];
        groups = [{id: "g1", device_ids: ["a", "b"]}, {id: "g2", device_ids: ["c"]}];
        unaffectedMacs = ["m2", "m9"];
        serviceListsState = [{name: "Video", enabled: true}, {name: "Off", enabled: false}];
        renderApplySummary();
        console.log(JSON.stringify({text: element("apply-summary").textContent,
          hidden: element("apply-summary").hidden}));
        """
    )
    assert out == {
        "text": "Will run: 2 devices with a profile · 2 groups · 1 service list"
        " · 2 protected devices stay untouched",
        "hidden": False,
    }


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
    assert out == "Running: 1 device with a profile · 1 group · 1 protected device stays untouched"


@needs_node
def test_the_line_says_running_while_the_scheduler_runs():
    out = render(
        """
        savedDevices = [{id: "a", mac_address: "m1"}];
        renderApplySummary();
        console.log(JSON.stringify(element("apply-summary").textContent));
        """
    )
    assert out == "Running: 1 device with a profile"


@needs_node
def test_the_line_names_the_general_profile_as_plain_text():
    out = render(
        """
        generalProfileState = {profile: {id: "slow", name: "<b>Slow</b>"}, active: true};
        savedDevices = [{id: "a", mac_address: "m1"}];
        renderApplySummary();
        console.log(JSON.stringify(element("apply-summary").textContent));
        """
    )
    assert out == (
        'Running: Applies the general profile "<b>Slow</b>" to every device that is not protected'
    )


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
    assert out == {"before": "Running: 1 device with a profile", "text": "", "hidden": True}


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
    assert out == {"text": "Will run: 1 device with a profile", "button": "Start with saved settings"}


@needs_node
def test_the_runtime_summary_uses_the_table_and_remembers_the_running_flag():
    out = render(
        """
        const seen = [];
        for (const state of [
          {observed_state: "applied", running: true},
          {observed_state: "applied"},
          {observed_state: "unknown", running: false},
          {observed_state: "reset", running: false},
          {observed_state: "error", running: true},
        ]) {
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
    )
    assert out == [
        {
            "text": "Running. Your saved settings are applied. New changes apply on their own.",
            "className": "runtime-summary applied tone-ok",
            "button": "Re-apply saved settings",
            "stored": True,
        },
        {
            "text": "Applied. Waiting for the scheduler.",
            "className": "runtime-summary applied tone-muted",
            "button": "Start with saved settings",
            "stored": True,
        },
        {
            "text": "Not started. Choose Start with saved settings.",
            "className": "runtime-summary unknown tone-pending",
            "button": "Start with saved settings",
            "stored": True,
        },
        {
            "text": "Off. No traffic shaping is running.",
            "className": "runtime-summary reset tone-muted",
            "button": "Start with saved settings",
            "stored": True,
        },
        {
            "text": "The last apply failed. Check the Pi.",
            "className": "runtime-summary error tone-error",
            "button": "Re-apply saved settings",
            "stored": True,
        },
    ]


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
    assert out == {"during": "Applying…", "after": "Re-apply saved settings"}


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
    assert out == {"before": "Re-apply saved settings", "after": "Start with saved settings"}


def test_the_summary_code_never_writes_html():
    assert "innerHTML" not in served_functions()
    assert "insertAdjacentHTML" not in served_functions()


def sidebar_html():
    return re.search(r'<aside class="sidebar".*?</aside>', DASHBOARD_HTML, re.DOTALL).group(0)


def test_the_page_starts_with_the_start_wording_and_a_line_for_the_summary():
    dock = sidebar_html()
    button = re.search(r'<button[^>]*id="apply-button"[^>]*>([^<]*)</button>', dock)
    assert button.group(1) == "Start with saved settings"
    assert dock.count('id="apply-summary"') == 1
    assert re.search(r'<p id="apply-summary" class="apply-summary"[^>]*></p>', dock)
    assert dock.index('id="apply-button"') < dock.index('id="apply-summary"') < dock.index(
        'id="reset-button"'
    )


def test_the_apply_and_reset_buttons_each_have_a_help_icon():
    dock = sidebar_html()
    helps = re.findall(
        r'<span class="help-icon" tabindex="0" role="img" aria-label="([^"]*)"'
        r' data-help="([^"]*)">\?</span>',
        dock,
    )
    by_label = dict(helps)
    apply_help = by_label["Start with saved settings help"]
    reset_help = by_label["Reset network help"]
    assert len(apply_help) <= 260
    for fact in (
        "Starts JitterSim with everything you saved",
        "device and group profiles",
        "schedules",
        "limits",
        "service lists",
        "access rules",
        "saved changes apply on their own within a few seconds",
        "Reset or Panic stops it",
        "Protected devices stay untouched",
    ):
        assert fact in apply_help, fact
    assert reset_help == (
        "Stops JitterSim and removes every rule from the Pi so all devices run normally."
        " Your saved settings are kept."
        " Choose Start with saved settings to run them again."
    )
    # The icon sits right after its button so keyboard users reach it next.
    assert re.search(r'id="apply-button"[^>]*>[^<]*</button>\s*<span class="help-icon"', dock)
    assert re.search(r'id="reset-button"[^>]*>[^<]*</button>\s*<span class="help-icon"', dock)


def test_panic_says_how_to_run_again():
    button = re.search(r'<button[^>]*id="panic-button"[^>]*>', sidebar_html()).group(0)
    assert (
        'title="Remove all traffic shaping now. Choose Start with saved settings to run again."'
        in button
    )


OLD_PHRASES = re.compile(
    r"Apply current configuration|[Aa]pply the configuration|Apply to enforce"
    r"|No network action has run yet|Configuration applied successfully"
)


def test_the_page_never_shows_the_old_wording():
    assert OLD_PHRASES.findall(DASHBOARD_HTML) == []


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
    assert OLD_PHRASES.findall(path.read_text(encoding="utf-8")) == []
