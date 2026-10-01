"""Run the dashboard's profile summary and profile form functions in node."""

import json
import re
import shutil
import subprocess
from dataclasses import asdict
from html.parser import HTMLParser

import pytest

from jittersim.api.server import DASHBOARD_HTML
from jittersim.config.models import Impairment, Profile, Randomness

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
HIDDEN_FIELDS = {
    "reroll_interval_randomness",
    "outage_duration_randomness",
    "outage_interval_randomness",
    "website_randomness",
}
FEATURES = ("delay", "jitter", "loss", "bandwidth", "reorder", "duplicate", "corrupt")


def served_helpers():
    start = SCRIPT.index("function optionalNumber(")
    return SCRIPT[start : SCRIPT.index("function timeToMinute(", start)]


def served_profile_functions():
    start = SCRIPT.index("function profileSummary(")
    return SCRIPT[start : SCRIPT.index('element("profile-form").addEventListener("submit"', start)]


class DefaultsParser(HTMLParser):
    """Collect the value every profile form field has after form.reset()."""

    def __init__(self):
        super().__init__()
        self.values = {}
        self.select = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and "id" in attributes:
            self.values[attributes["id"]] = attributes.get("value", "")
        elif tag == "select" and "id" in attributes:
            self.select = attributes["id"]
        elif tag == "option" and self.select and self.select not in self.values:
            self.values[self.select] = attributes.get("value", "")

    def handle_endtag(self, tag):
        if tag == "select":
            self.select = None


def form_defaults():
    form = re.search(r'<form id="profile-form">(.*?)</form>', DASHBOARD_HTML, re.DOTALL).group(1)
    parser = DefaultsParser()
    parser.feed(form)
    return parser.values


def run_node(body, prelude=""):
    program = (
        "const store = {};\n"
        "const element = (id) => (store[id] = store[id] || {value: \"\"});\n"
        + prelude
        + served_helpers()
        + "\n"
        + served_profile_functions()
        + "\nObject.entries("
        + json.dumps(form_defaults())
        + ").forEach(([id, value]) => { element(id).value = value; });\n"
        + body
    )
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def direction(**values):
    return dict(
        {"delay_ms": 0, "jitter_ms": 0, "loss_percent": 0, "bandwidth_kbit": None}, **values
    )


SUMMARIES = {
    "delay_jitter_loss_and_mbit": (
        direction(delay_ms=50, jitter_ms=10, loss_percent=2, bandwidth_kbit=5000),
        direction(delay_ms=50, jitter_ms=10, loss_percent=2, bandwidth_kbit=5000),
        "↕ 50 ms ±10 · 2% loss · 5 Mbit/s",
    ),
    "unequal_directions_use_two_arrows": (
        direction(delay_ms=50, jitter_ms=10, loss_percent=2, bandwidth_kbit=5000),
        direction(delay_ms=20),
        "↑ 50 ms ±10 · 2% loss · 5 Mbit/s · ↓ 20 ms",
    ),
    "one_and_a_half_mbit": (
        direction(bandwidth_kbit=1500), direction(bandwidth_kbit=1500), "↕ 1.5 Mbit/s",
    ),
    "exactly_one_mbit": (direction(bandwidth_kbit=1000), direction(bandwidth_kbit=1000), "↕ 1 Mbit/s"),
    "kbit_below_one_mbit": (
        direction(bandwidth_kbit=800), direction(bandwidth_kbit=800), "↕ 800 kbit/s",
    ),
    "kbit_just_below_one_mbit": (
        direction(bandwidth_kbit=999), direction(bandwidth_kbit=999), "↕ 999 kbit/s",
    ),
    "all_zero_direction": (direction(), direction(), "↕ no impairment"),
    "one_direction_is_clean": (
        direction(loss_percent=3), direction(), "↑ 3% loss · ↓ no impairment",
    ),
    "delay_without_jitter": (direction(delay_ms=20), direction(delay_ms=20), "↕ 20 ms"),
    "jitter_without_delay": (direction(jitter_ms=5), direction(jitter_ms=5), "↕ 0 ms ±5"),
}


@NEEDS_NODE
@pytest.mark.parametrize("name", SUMMARIES)
def test_profile_summary(name):
    upload, download, expected = SUMMARIES[name]
    body = (
        "const profile = " + json.dumps({"upload": upload, "download": download}) + ";\n"
        "console.log(JSON.stringify(profileSummary(profile)));\n"
    )
    assert run_node(body) == expected


def every_field_set_profile():
    def impairment(offset, bandwidth):
        values = {
            "delay_ms": 20 + offset,
            "jitter_ms": 3 + offset,
            "loss_percent": 4 + offset,
            "bandwidth_kbit": bandwidth,
            "reorder_percent": 5 + offset,
            "duplicate_percent": 6 + offset,
            "corrupt_percent": 7 + offset,
        }
        for index, feature in enumerate(FEATURES):
            values[feature + "_randomness"] = Randomness(
                variation_percent=10 + offset + index, enabled_probability=90 - offset - index
            )
        return Impairment(**values)

    return Profile(
        id="every-field",
        name="Every field",
        upload=impairment(0, 2500),
        download=impairment(30, None),
        random_seed=99,
        reroll_interval_seconds=45,
        reroll_interval_randomness=Randomness(variation_percent=7, enabled_probability=80),
        outage_probability=12,
        outage_duration_seconds=33,
        outage_duration_randomness=Randomness(variation_percent=11, enabled_probability=60),
        outage_interval_seconds=77,
        outage_interval_randomness=Randomness(variation_percent=11, enabled_probability=60),
        website_mode="allow_only",
        website_domains=("a.example.com", "b.example.com"),
        website_randomness=Randomness(variation_percent=13, enabled_probability=40),
    )


def as_json(profile):
    return json.loads(json.dumps(asdict(profile)))


def test_the_round_trip_profile_sets_every_field_to_a_non_default_value():
    profile = as_json(every_field_set_profile())
    default = as_json(Profile(id="every-field", name="Every field"))
    default_impairment = as_json(Profile(id="x", name="x"))["upload"]
    assert set(profile) == set(default)
    for key in profile:
        if key in ("id", "name", "upload", "download"):
            continue
        assert profile[key] != default[key], key
    for side in ("upload", "download"):
        for key in default_impairment:
            if side == "download" and key == "bandwidth_kbit":
                continue
            assert profile[side][key] != default_impairment[key], (side, key)
    assert profile["download"]["bandwidth_kbit"] is None


@NEEDS_NODE
def test_fill_then_read_returns_the_original_profile():
    profile = as_json(every_field_set_profile())
    body = (
        "const profile = " + json.dumps(profile) + ";\n"
        "fillProfileForm(profile);\n"
        "console.log(JSON.stringify(profileFormPayload(profile)));\n"
    )
    assert run_node(body) == profile


@NEEDS_NODE
def test_without_a_base_only_the_four_hidden_fields_change():
    profile = as_json(every_field_set_profile())
    body = (
        "const profile = " + json.dumps(profile) + ";\n"
        "fillProfileForm(profile);\n"
        "console.log(JSON.stringify(profileFormPayload(null)));\n"
    )
    payload = run_node(body)
    assert {key for key in profile if payload[key] != profile[key]} == HIDDEN_FIELDS
    assert set(payload) == set(profile)
    assert payload["reroll_interval_randomness"] == {
        "variation_percent": 7, "enabled_probability": 100,
    }
    assert payload["outage_duration_randomness"] == {
        "variation_percent": 0, "enabled_probability": 100,
    }
    assert payload["outage_interval_randomness"] == {
        "variation_percent": 0, "enabled_probability": 100,
    }
    assert payload["website_randomness"] == {"variation_percent": 0, "enabled_probability": 40}


@NEEDS_NODE
def test_a_new_profile_keeps_the_hard_coded_hidden_defaults():
    body = (
        'element("profile-id").value = "fresh";\n'
        'element("profile-name").value = "Fresh";\n'
        "console.log(JSON.stringify(profileFormPayload(null)));\n"
    )
    payload = run_node(body)
    assert payload == as_json(Profile(id="fresh", name="Fresh"))
    assert payload["reroll_interval_randomness"] == {
        "variation_percent": 0, "enabled_probability": 100,
    }
    assert payload["website_randomness"] == {"variation_percent": 0, "enabled_probability": 100}


@NEEDS_NODE
def test_filling_a_profile_with_no_seed_or_bandwidth_clears_those_fields():
    profile = as_json(every_field_set_profile())
    profile["random_seed"] = None
    profile["upload"]["bandwidth_kbit"] = None
    body = (
        "const profile = " + json.dumps(profile) + ";\n"
        'element("profile-seed").value = "5";\n'
        'element("upload-bandwidth").value = "9";\n'
        "fillProfileForm(profile);\n"
        "console.log(JSON.stringify([element(\"profile-seed\").value,"
        ' element("upload-bandwidth").value, profileFormPayload(profile)]));\n'
    )
    seed, bandwidth, payload = run_node(body)
    assert (seed, bandwidth) == ("", "")
    assert payload == profile


def test_the_submit_handler_builds_its_body_with_the_payload_function():
    submit = SCRIPT[SCRIPT.index('element("profile-form").addEventListener("submit"') :]
    submit = submit[: submit.index('element("device-form").addEventListener("submit"')]
    assert "profileFormPayload(editingProfile)" in submit


HTML = DASHBOARD_HTML.split("<script>")[0]

FAKE_DOM = """
class FakeNode {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.className = "";
    this.textContent = "";
    this.disabled = false;
    this.hidden = false;
  }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
}
const document = { createElement: (tag) => new FakeNode(tag) };
store["saved-profiles"] = new FakeNode("div");
let profiles = [];
let generalProfileState = null;
let networkActionBusy = false;
let helperConfigured = true;
let adminTokenVerified = true;
let savedProfileBusy = null;
function setButtonBusy(button, busy) {
  button.disabled = busy;
  if (busy) button.setAttribute("aria-busy", "true");
  else button.removeAttribute("aria-busy");
}
function descendants(node) {
  return node.children.reduce((all, child) => all.concat([child], descendants(child)), []);
}
function drawn() {
  return element("saved-profiles").children.map((row) => {
    if (row.tagName !== "div") return {message: row.textContent};
    const nodes = descendants(row);
    const badge = nodes.find((node) => node.className.includes("general-badge"));
    return {
      id: row.dataset.profileId,
      name: nodes.find((node) => node.tagName === "strong").textContent,
      summary: nodes.find((node) => node.className === "saved-profile-summary").textContent,
      badge: badge ? badge.textContent : null,
      buttons: nodes.filter((node) => node.tagName === "button").map((button) => ({
        text: button.textContent, disabled: button.disabled, busy: button.attributes["aria-busy"] || null
      }))
    };
  });
}
"""

SLOW = {"id": "slow", "name": "Slow", "upload": direction(delay_ms=50), "download": direction(
    delay_ms=50)}
FAST = {"id": "fast", "name": "Fast", "upload": direction(), "download": direction()}


def draw(state):
    """Run renderSavedProfiles for one dashboard state and return what it drew."""

    assignments = "".join(f"{name} = {json.dumps(value)};\n" for name, value in state.items())
    return run_node(assignments + "renderSavedProfiles();\nconsole.log(JSON.stringify(drawn()));\n",
                    FAKE_DOM)


def row(id_, name, summary, badge, *buttons):
    return {
        "id": id_, "name": name, "summary": summary, "badge": badge,
        "buttons": [
            {"text": text, "disabled": disabled, "busy": busy} for text, disabled, busy in buttons
        ],
    }


EDIT = ("Edit", False, None)
APPLY = ("Apply to all", False, None)


@NEEDS_NODE
def test_an_empty_list_says_so():
    assert draw({"profiles": []}) == [{"message": "No saved profiles yet. Create one below."}]


@NEEDS_NODE
def test_each_row_has_name_summary_edit_and_apply_to_all():
    assert draw({"profiles": [SLOW, FAST]}) == [
        row("slow", "Slow", "↕ 50 ms", None, EDIT, APPLY),
        row("fast", "Fast", "↕ no impairment", None, EDIT, APPLY),
    ]


@NEEDS_NODE
def test_the_active_general_profile_has_a_badge_and_a_stop_button():
    state = {"profiles": [SLOW, FAST], "generalProfileState": {"profile": SLOW, "active": True}}
    assert draw(state) == [
        row("slow", "Slow", "↕ 50 ms", "General · active", EDIT, ("Stop", False, None)),
        row("fast", "Fast", "↕ no impairment", None, EDIT, APPLY),
    ]


@NEEDS_NODE
def test_a_paused_general_profile_is_badged_but_offers_apply_to_all():
    state = {"profiles": [SLOW, FAST], "generalProfileState": {"profile": SLOW, "active": False}}
    assert draw(state) == [
        row("slow", "Slow", "↕ 50 ms", "General · paused", EDIT, APPLY),
        row("fast", "Fast", "↕ no impairment", None, EDIT, APPLY),
    ]


@NEEDS_NODE
@pytest.mark.parametrize("state", [{"networkActionBusy": True}, {"helperConfigured": False}])
def test_apply_to_all_is_disabled_while_busy_or_without_the_helper(state):
    drawn = draw(dict({"profiles": [SLOW]}, **state))
    assert drawn == [row("slow", "Slow", "↕ 50 ms", None, EDIT, ("Apply to all", True, None))]


@NEEDS_NODE
def test_the_row_whose_action_is_running_shows_a_busy_button():
    state = {
        "profiles": [SLOW, FAST], "networkActionBusy": True,
        "savedProfileBusy": {"id": "fast", "stop": False},
    }
    assert draw(state) == [
        row("slow", "Slow", "↕ 50 ms", None, EDIT, ("Apply to all", True, None)),
        row("fast", "Fast", "↕ no impairment", None, EDIT, ("Apply to all", True, "true")),
    ]


@NEEDS_NODE
def test_nothing_is_drawn_before_the_token_is_accepted():
    assert draw({"profiles": [SLOW], "adminTokenVerified": False}) == []


@NEEDS_NODE
def test_an_unchanged_list_is_not_rebuilt_but_a_wiped_one_is():
    body = (
        "profiles = [" + json.dumps(SLOW) + "];\n"
        "renderSavedProfiles();\n"
        "const first = element(\"saved-profiles\").children[0];\n"
        "renderSavedProfiles();\n"
        "const kept = element(\"saved-profiles\").children[0] === first;\n"
        "element(\"saved-profiles\").children = [];\n"
        "renderSavedProfiles();\n"
        "const rebuilt = element(\"saved-profiles\").children[0] !== first;\n"
        "console.log(JSON.stringify([kept, rebuilt]));\n"
    )
    assert run_node(body, FAKE_DOM) == [True, True]


@NEEDS_NODE
def test_edit_loads_the_current_saved_profile_not_the_one_drawn_earlier():
    stale = dict(SLOW, name="Old name")
    body = (
        "const started = [];\n"
        "startEditingProfile = (profile) => started.push(profile);\n"
        "profiles = [" + json.dumps(stale) + "];\n"
        "renderSavedProfiles();\n"
        "const edit = descendants(element(\"saved-profiles\"))"
        ".find((node) => node.textContent === \"Edit\");\n"
        "profiles = [" + json.dumps(dict(SLOW, name="New name")) + "];\n"
        "edit.listeners.click();\n"
        "console.log(JSON.stringify(started.map((profile) => profile.name)));\n"
    )
    assert run_node(body, FAKE_DOM) == ["New name"]


def run_general_profile_action(clicks):
    """Press the row buttons with stubbed requests and return what was sent."""

    start = SCRIPT.index("async function runGeneralProfileAction(")
    action = SCRIPT[start : SCRIPT.index('element("general-profile-select").addEventListener', start)]
    body = (
        "const sent = [];\n"
        "const messages = [];\n"
        "request = async (path, method, body) => { sent.push([path, method, body]); return {}; };\n"
        "(async () => {\n"
        "  profiles = [" + json.dumps(SLOW) + "];\n"
        "  generalProfileState = {profile: null, active: false};\n"
        "  renderSavedProfiles();\n"
        + clicks
        + "  console.log(JSON.stringify([sent, messages]));\n"
        "})();\n"
    )
    stubs = (
        "let request;\n"
        "function setActionBusy(busy) { networkActionBusy = busy; renderSavedProfiles(); }\n"
        "function showMessage(message, kind) { if (kind !== 'working') messages.push(message); }\n"
        "function refreshAfterSuccess(message) { messages.push(message); }\n"
        "async function loadPrivateDataPanel() {}\n"
        "function loadGeneralProfile() {}\n"
    )
    program_prelude = FAKE_DOM + stubs + action
    return run_node(body, program_prelude)


def press(label):
    return (
        "  descendants(element(\"saved-profiles\"))"
        f".find((node) => node.textContent === \"{label}\").listeners.click();\n"
        "  await new Promise((resolve) => setTimeout(resolve, 0));\n"
    )


@NEEDS_NODE
def test_apply_to_all_posts_only_the_profile_id():
    sent, messages = run_general_profile_action(press("Apply to all"))
    assert sent == [["/api/general-profile", "POST", {"profile_id": "slow"}]]
    assert messages == ["General profile applied. Protected devices remain unaffected."]


@NEEDS_NODE
def test_stop_posts_an_empty_body_to_the_stop_path():
    clicks = (
        "  generalProfileState = {profile: profiles[0], active: true};\n"
        "  renderSavedProfiles();\n" + press("Stop")
    )
    sent, messages = run_general_profile_action(clicks)
    assert sent == [["/api/general-profile/stop", "POST", {}]]
    assert messages == ["General profile stopped. Saved device rules resumed."]


def page_html(page):
    return re.search(
        rf'<section class="page" id="page-{page}"[^>]*>(.*?)<!-- /page:{page} -->', HTML, re.DOTALL
    ).group(1)


def test_the_saved_profiles_card_sits_above_the_form_on_the_profiles_page():
    page = page_html("profiles")
    assert len(re.findall(r"<h2[^>]*>Saved profiles</h2>", HTML)) == 1
    assert page.index("Saved profiles</h2>") < page.index('<h2 id="profile-form-title">')
    assert page.index('id="saved-profiles"') < page.index('id="profile-form"')


def test_the_form_title_can_change_and_the_new_profile_button_starts_hidden():
    assert '<h2 id="profile-form-title">Save profile</h2>' in HTML
    assert '<button id="new-profile" type="button" hidden>New profile</button>' in page_html(
        "profiles"
    )


def function_source(name):
    match = re.search(r"\n    (?:async )?function " + re.escape(name) + r"\(", SCRIPT)
    assert match, name
    following = re.search(r"\n    (?:async )?function \w+\(", SCRIPT[match.end() :])
    return SCRIPT[match.start() : match.end() + following.start()]


def test_the_list_is_redrawn_whenever_profiles_or_the_general_profile_change():
    assert "renderSavedProfiles();" in function_source("updateGeneralProfileControls")
    assert "updateGeneralProfileControls();" in function_source("loadProfiles")
    assert "updateGeneralProfileControls();" in function_source("loadGeneralProfile")


def test_the_list_is_cleared_with_the_other_private_panels():
    assert '"saved-profiles"' in SCRIPT[SCRIPT.index("const privateDataPanelIds") :][:300]


def test_a_new_profile_button_clears_the_form_and_leaves_editing():
    handler = SCRIPT[SCRIPT.index('element("new-profile").addEventListener("click"') :][:300]
    assert 'element("profile-form").reset();' in handler
    assert "resetProfileEditing();" in handler
