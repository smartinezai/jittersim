"""Run the dashboard's saved profile list and profile form functions in node."""

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
HTML = DASHBOARD_HTML.split("<script>")[0]
NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
HIDDEN_FIELDS = {
    "reroll_interval_randomness",
    "outage_duration_randomness",
    "outage_interval_randomness",
    "website_randomness",
}
FEATURES = ("delay", "jitter", "loss", "bandwidth", "reorder", "duplicate", "corrupt")
UNSORTED_DOMAINS = ["Zeta.Example.com", "alpha.example.com", "Mid.example.com"]
SEED_MESSAGE = (
    "This profile has a random seed too large to edit here. Change it through the API or the CLI."
)


def script_between(start, end):
    first = SCRIPT.index(start)
    return SCRIPT[first : SCRIPT.index(end, first)]


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


BASE_PRELUDE = r"""
const store = {};
let newElement;
const element = (id) => (store[id] = store[id] || newElement());
class FakeNode {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.className = "";
    this.textContent = "";
    this.value = "";
    this.disabled = false;
    this.hidden = false;
    this.readOnly = false;
  }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  querySelector() { return null; }
  contains(node) { return node === this || descendants(this).includes(node); }
  focus() { if (!this.disabled) document.activeElement = this; }
}
newElement = () => new FakeNode("div");
const document = { createElement: (tag) => new FakeNode(tag), activeElement: null };
let profiles = [];
let generalProfileState = null;
let networkActionBusy = false;
let helperConfigured = true;
let adminTokenVerified = true;
let savedProfileBusy = null;
const messages = [];
const sent = [];
const revealed = [];
let request = async (path, method, body) => {
  sent.push([path, method, body === undefined ? null : body]);
  return {};
};
function showMessage(text, kind) { messages.push([text, kind]); }
function refreshAfterSuccess(message) { messages.push([message, "success"]); }
function revealElement(node) { revealed.push(node); }
function updateGeneralProfileControls() { renderSavedProfiles(); }
function setActionBusy(busy) { networkActionBusy = busy; updateGeneralProfileControls(); }
async function loadPrivateDataPanel() {}
async function loadGeneralProfile() {}
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
        text: button.textContent,
        disabled: button.disabled,
        aria_disabled: button.attributes["aria-disabled"] === "true",
        busy: button.attributes["aria-busy"] || null
      }))
    };
  });
}
function listButton(profileId, text) {
  return descendants(element("saved-profiles")).find(
    (node) => node.tagName === "button" && node.dataset.profileId === profileId
      && node.textContent === text);
}
"""


def program(body):
    """Assemble the served functions with fake browser state and run body in node."""

    return (
        BASE_PRELUDE
        + script_between("const privateDataPanelIds =", "function setDashboardUnlocked(")
        + script_between("function setPrivateDataPanels(", "async function loadPrivateDataPanel(")
        + script_between("function optionalNumber(", "function timeToMinute(")
        + script_between(
            "async function runGeneralProfileAction(",
            'element("general-profile-select").addEventListener("change"',
        )
        + script_between(
            "function profileSummary(", 'element("device-form").addEventListener("submit"'
        )
        + script_between("async function loadProfiles(", "async function loadGeneralProfile(")
        + "\nconst formDefaults = " + json.dumps(form_defaults()) + ";\n"
        "let formResets = 0;\n"
        'element("profile-form").reset = () => {\n'
        "  formResets++;\n"
        "  Object.entries(formDefaults).forEach(([id, value]) => { element(id).value = value; });\n"
        "};\n"
        "Object.entries(formDefaults).forEach(([id, value]) => { element(id).value = value; });\n"
        "(async () => {\n" + body + "\n})().catch((error) => {\n"
        "  console.error(error);\n"
        "  process.exit(1);\n"
        "});\n"
    )


def run_node(body):
    result = subprocess.run(
        ["node", "-e", program(body)], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def direction(**values):
    return dict(
        {
            "delay_ms": 0, "jitter_ms": 0, "loss_percent": 0, "bandwidth_kbit": None,
            "reorder_percent": 0, "duplicate_percent": 0, "corrupt_percent": 0,
        },
        **values,
    )


def summary_profile(upload, download, **extra):
    return dict(
        {
            "id": "p", "name": "P", "upload": upload, "download": download,
            "outage_probability": 0, "website_mode": "unrestricted", "website_domains": [],
        },
        **extra,
    )


def same(extra=None, **values):
    return summary_profile(direction(**values), direction(**values), **(extra or {}))


SUMMARIES = {
    "delay_jitter_loss_and_mbit": (
        same(delay_ms=50, jitter_ms=10, loss_percent=2, bandwidth_kbit=5000),
        "↕ 50 ms ±10 · 2% loss · 5 Mbit/s",
    ),
    "unequal_directions_use_two_arrows": (
        summary_profile(
            direction(delay_ms=50, jitter_ms=10, loss_percent=2, bandwidth_kbit=5000),
            direction(delay_ms=20),
        ),
        "↑ 50 ms ±10 · 2% loss · 5 Mbit/s · ↓ 20 ms",
    ),
    "one_and_a_half_mbit": (same(bandwidth_kbit=1500), "↕ 1.5 Mbit/s"),
    "two_decimals_are_shown": (same(bandwidth_kbit=1049), "↕ 1.05 Mbit/s"),
    "two_decimals_round": (same(bandwidth_kbit=1234), "↕ 1.23 Mbit/s"),
    "whole_mbit": (same(bandwidth_kbit=5000), "↕ 5 Mbit/s"),
    "exactly_one_mbit": (same(bandwidth_kbit=1000), "↕ 1 Mbit/s"),
    "kbit_below_one_mbit": (same(bandwidth_kbit=800), "↕ 800 kbit/s"),
    "kbit_just_below_one_mbit": (same(bandwidth_kbit=999), "↕ 999 kbit/s"),
    "all_zero_direction": (same(), "↕ no impairment"),
    "one_direction_is_clean": (
        summary_profile(direction(loss_percent=3), direction()),
        "↑ 3% loss · ↓ no impairment",
    ),
    "delay_without_jitter": (same(delay_ms=20), "↕ 20 ms"),
    "jitter_without_delay": (same(jitter_ms=5), "↕ 0 ms ±5"),
    "reorder_only_is_not_no_impairment": (same(reorder_percent=3), "↕ reorder 3%"),
    "duplicate_only_is_not_no_impairment": (same(duplicate_percent=2), "↕ duplicate 2%"),
    "corrupt_only_is_not_no_impairment": (same(corrupt_percent=4), "↕ corrupt 4%"),
    "packet_effects_come_after_delay_loss_and_speed": (
        same(
            delay_ms=50, loss_percent=2, bandwidth_kbit=800,
            reorder_percent=1, duplicate_percent=2, corrupt_percent=3,
        ),
        "↕ 50 ms · 2% loss · 800 kbit/s · reorder 1% · duplicate 2% · corrupt 3%",
    ),
    "packet_effects_in_one_direction": (
        summary_profile(direction(reorder_percent=3), direction(corrupt_percent=4)),
        "↑ reorder 3% · ↓ corrupt 4%",
    ),
    "outages_follow_both_directions": (
        summary_profile(direction(delay_ms=50), direction(delay_ms=20), outage_probability=12),
        "↑ 50 ms · ↓ 20 ms · outages 12%",
    ),
    "outages_on_a_clean_profile": (
        same({"outage_probability": 12}), "↕ no impairment · outages 12%",
    ),
    "allow_only_counts_sites": (
        same({"website_mode": "allow_only", "website_domains": ["a.com", "b.com"]}, delay_ms=5),
        "↕ 5 ms · allow-only 2 sites",
    ),
    "allow_only_with_one_site_is_singular": (
        same({"website_mode": "allow_only", "website_domains": ["a.com"]}, delay_ms=5),
        "↕ 5 ms · allow-only 1 site",
    ),
    "unrestricted_ignores_the_domain_list": (
        same({"website_domains": ["a.com"]}, delay_ms=5), "↕ 5 ms",
    ),
    "outages_then_allow_only": (
        same(
            {
                "outage_probability": 7, "website_mode": "allow_only",
                "website_domains": ["a.com", "b.com", "c.com"],
            },
            delay_ms=5,
        ),
        "↕ 5 ms · outages 7% · allow-only 3 sites",
    ),
}


@NEEDS_NODE
@pytest.mark.parametrize("name", SUMMARIES)
def test_profile_summary(name):
    profile, expected = SUMMARIES[name]
    body = (
        "const profile = " + json.dumps(profile) + ";\n"
        "console.log(JSON.stringify(profileSummary(profile)));\n"
    )
    assert run_node(body) == expected


def every_field_set_profile(profile_id="every-field", name="Every field", other=False):
    """Build a profile whose every field differs from the defaults.

    With other set the hidden randomness details differ from the first profile as well.
    """

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
        id=profile_id,
        name=name,
        upload=impairment(0, 2500),
        download=impairment(30, None),
        random_seed=99,
        reroll_interval_seconds=45,
        reroll_interval_randomness=Randomness(
            variation_percent=9 if other else 7, enabled_probability=55 if other else 80
        ),
        outage_probability=12,
        outage_duration_seconds=33,
        outage_duration_randomness=Randomness(
            variation_percent=31 if other else 11, enabled_probability=45 if other else 60
        ),
        outage_interval_seconds=77,
        outage_interval_randomness=Randomness(
            variation_percent=23 if other else 21, enabled_probability=35 if other else 38
        ),
        website_mode="allow_only",
        website_domains=("a.example.com",),
        website_randomness=Randomness(
            variation_percent=17 if other else 13, enabled_probability=25 if other else 40
        ),
    )


def as_json(profile):
    data = json.loads(json.dumps(asdict(profile)))
    if data["website_mode"] == "allow_only":
        # The model lowercases domains. The form must keep what the server sends in order.
        data["website_domains"] = list(UNSORTED_DOMAINS)
    return data


def other_json():
    return as_json(every_field_set_profile("other", "Other profile", other=True))


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


def test_the_round_trip_profile_has_distinct_hidden_values_and_unsorted_mixed_case_domains():
    profile = as_json(every_field_set_profile())
    hidden = [profile[key] for key in sorted(HIDDEN_FIELDS)]
    assert len({json.dumps(value, sort_keys=True) for value in hidden}) == len(hidden)
    assert profile["outage_duration_randomness"] != profile["outage_interval_randomness"]
    assert profile["website_domains"] != sorted(profile["website_domains"])
    assert profile["website_domains"] != [domain.lower() for domain in profile["website_domains"]]
    other = other_json()
    for key in HIDDEN_FIELDS:
        assert other[key] != profile[key], key


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


# The list.

SLOW = summary_profile(direction(delay_ms=50), direction(delay_ms=50), id="slow", name="Slow")
FAST = summary_profile(direction(), direction(), id="fast", name="Fast")


def draw(state):
    """Run renderSavedProfiles for one dashboard state and return what it drew."""

    assignments = "".join(f"{name} = {json.dumps(value)};\n" for name, value in state.items())
    return run_node(assignments + "renderSavedProfiles();\nconsole.log(JSON.stringify(drawn()));\n")


def button(text, disabled=False, aria_disabled=False, busy=None):
    return {"text": text, "disabled": disabled, "aria_disabled": aria_disabled, "busy": busy}


def row(id_, name, summary, badge, *buttons):
    return {"id": id_, "name": name, "summary": summary, "badge": badge, "buttons": list(buttons)}


EDIT = button("Edit")
APPLY = button("Apply to all")
STOP = button("Stop")


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
    state = {
        "profiles": [SLOW, FAST],
        "generalProfileState": {
            "profile": SLOW, "active": True, "state": {"observed_state": "applied"},
        },
    }
    assert draw(state) == [
        row("slow", "Slow", "↕ 50 ms", "General · active", EDIT, STOP),
        row("fast", "Fast", "↕ no impairment", None, EDIT, APPLY),
    ]


@NEEDS_NODE
@pytest.mark.parametrize(
    "observed,badge",
    [
        ("reset", "General · paused"),
        ("error", "General · saved"),
        ("unknown", "General · saved"),
        ("applying", "General · saved"),
        ("resetting", "General · saved"),
    ],
)
def test_an_inactive_general_profile_is_paused_only_after_a_reset(observed, badge):
    state = {
        "profiles": [SLOW, FAST],
        "generalProfileState": {
            "profile": SLOW, "active": False, "state": {"observed_state": observed},
        },
    }
    assert draw(state) == [
        row("slow", "Slow", "↕ 50 ms", badge, EDIT, APPLY),
        row("fast", "Fast", "↕ no impairment", None, EDIT, APPLY),
    ]


@NEEDS_NODE
def test_an_inactive_general_profile_without_a_runtime_state_is_saved_not_paused():
    state = {"profiles": [SLOW], "generalProfileState": {"profile": SLOW, "active": False}}
    assert draw(state) == [row("slow", "Slow", "↕ 50 ms", "General · saved", EDIT, APPLY)]


@NEEDS_NODE
def test_apply_to_all_stays_focusable_but_unavailable_while_a_network_action_runs():
    drawn = draw({"profiles": [SLOW], "networkActionBusy": True})
    assert drawn == [
        row("slow", "Slow", "↕ 50 ms", None, EDIT, button("Apply to all", aria_disabled=True))
    ]


@NEEDS_NODE
def test_apply_to_all_is_disabled_without_the_network_helper():
    drawn = draw({"profiles": [SLOW], "helperConfigured": False})
    assert drawn == [
        row("slow", "Slow", "↕ 50 ms", None, EDIT, button("Apply to all", disabled=True))
    ]


@NEEDS_NODE
def test_the_row_whose_action_is_running_shows_a_busy_button_that_can_keep_focus():
    state = {
        "profiles": [SLOW, FAST], "networkActionBusy": True,
        "savedProfileBusy": {"id": "fast", "stop": False},
    }
    assert draw(state) == [
        row("slow", "Slow", "↕ 50 ms", None, EDIT, button("Apply to all", aria_disabled=True)),
        row(
            "fast", "Fast", "↕ no impairment", None, EDIT,
            button("Apply to all", aria_disabled=True, busy="true"),
        ),
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
    assert run_node(body) == [True, True]


def focus_after_rebuild(focus, change):
    """Focus one list button then change the state and report where focus ends."""

    body = (
        "profiles = [" + json.dumps(SLOW) + "," + json.dumps(FAST) + "];\n"
        "renderSavedProfiles();\n"
        f"const old = listButton({focus[0]!r}, {focus[1]!r});\n"
        "old.focus();\n"
        + change
        + "const now = document.activeElement;\n"
        "console.log(JSON.stringify({\n"
        "  isNew: now !== old,\n"
        "  inList: element('saved-profiles').contains(now),\n"
        "  text: now.textContent, profile: now.dataset.profileId\n"
        "}));\n"
    )
    return run_node(body)


@NEEDS_NODE
def test_focus_returns_to_the_same_row_button_after_the_list_is_rebuilt():
    change = (
        "networkActionBusy = true;\n"
        "savedProfileBusy = {id: 'fast', stop: false};\n"
        "renderSavedProfiles();\n"
    )
    assert focus_after_rebuild(("fast", "Apply to all"), change) == {
        "isNew": True, "inList": True, "text": "Apply to all", "profile": "fast",
    }


@NEEDS_NODE
def test_focus_follows_apply_to_all_when_it_turns_into_stop():
    change = (
        "generalProfileState = {profile: profiles[1], active: true,"
        " state: {observed_state: 'applied'}};\n"
        "renderSavedProfiles();\n"
    )
    assert focus_after_rebuild(("fast", "Apply to all"), change) == {
        "isNew": True, "inList": True, "text": "Stop", "profile": "fast",
    }


@NEEDS_NODE
def test_focus_returns_to_edit_when_it_was_on_edit():
    change = (
        "const third = Object.assign({}, profiles[0], {id: 'third', name: 'Third'});\n"
        "profiles = profiles.concat([third]);\n"
        "renderSavedProfiles();\n"
    )
    assert focus_after_rebuild(("slow", "Edit"), change) == {
        "isNew": True, "inList": True, "text": "Edit", "profile": "slow",
    }


@NEEDS_NODE
def test_focus_outside_the_list_is_left_alone_by_a_rebuild():
    body = (
        "profiles = [" + json.dumps(SLOW) + "];\n"
        "renderSavedProfiles();\n"
        "const outside = element('profile-name');\n"
        "outside.focus();\n"
        "networkActionBusy = true;\n"
        "renderSavedProfiles();\n"
        "console.log(JSON.stringify(document.activeElement === outside));\n"
    )
    assert run_node(body) is True


@NEEDS_NODE
def test_focus_is_not_invented_when_the_row_is_gone():
    body = (
        "profiles = [" + json.dumps(SLOW) + "];\n"
        "renderSavedProfiles();\n"
        "const old = listButton('slow', 'Edit');\n"
        "old.focus();\n"
        "profiles = [];\n"
        "renderSavedProfiles();\n"
        "console.log(JSON.stringify(document.activeElement === old));\n"
    )
    assert run_node(body) is True


# Edit.

EDIT_HELPERS = (
    "async function pressEdit(profileId) {\n"
    "  listButton(profileId, 'Edit').listeners.click();\n"
    "  await new Promise((resolve) => setTimeout(resolve, 0));\n"
    "}\n"
    "function serveProfiles(...loads) {\n"
    "  let call = 0;\n"
    "  request = async (path, method, body) => {\n"
    "    sent.push([path, method, body === undefined ? null : body]);\n"
    "    return {profiles: loads[Math.min(call++, loads.length - 1)]};\n"
    "  };\n"
    "}\n"
)


def edit_result(first_load, second_load, extra=""):
    body = (
        EDIT_HELPERS
        + "serveProfiles(" + json.dumps(first_load) + ", " + json.dumps(second_load) + ");\n"
        "await loadProfiles();\n"
        + extra
        + "await pressEdit('every-field');\n"
        "console.log(JSON.stringify({\n"
        "  messages: messages, name: element('profile-name').value,\n"
        "  editing: editingProfile ? editingProfile.name : null, sent: sent,\n"
        "  list: drawn().map((item) => item.name || item.message)\n"
        "}));\n"
    )
    return run_node(body)


@NEEDS_NODE
def test_edit_reloads_the_profiles_so_a_change_made_elsewhere_is_not_saved_back_stale():
    old = as_json(every_field_set_profile())
    changed = dict(old, name="Changed elsewhere", outage_probability=44)
    result = edit_result([old], [changed])
    assert result["sent"] == [["/api/profiles", "GET", None], ["/api/profiles", "GET", None]]
    assert result["name"] == "Changed elsewhere"
    assert result["editing"] == "Changed elsewhere"
    assert result["list"] == ["Changed elsewhere"]
    assert result["messages"] == []


@NEEDS_NODE
def test_edit_of_a_profile_that_no_longer_exists_says_so_and_redraws_the_list():
    old = as_json(every_field_set_profile())
    result = edit_result([old], [])
    assert result["messages"] == [
        ["That profile no longer exists. The list was refreshed.", "error"]
    ]
    assert result["list"] == ["No saved profiles yet. Create one below."]
    assert result["editing"] is None
    assert result["name"] == ""


@NEEDS_NODE
def test_edit_reports_a_failed_reload_and_leaves_the_form_alone():
    old = as_json(every_field_set_profile())
    extra = (
        "request = async () => {\n"
        "  throw new Error('Token changed. Choose Check token to load.');\n"
        "};\n"
    )
    result = edit_result([old], [old], extra)
    assert result["messages"] == [["Token changed. Choose Check token to load.", "error"]]
    assert result["editing"] is None
    assert result["name"] == ""


@NEEDS_NODE
@pytest.mark.parametrize("seed", [2**53, 2**53 + 1, 2**63 - 1])
def test_edit_refuses_a_seed_the_browser_cannot_hold_exactly(seed):
    # The browser reads JSON numbers as doubles so the value is already rounded when it arrives.
    profile = dict(as_json(every_field_set_profile()), random_seed=seed)
    result = edit_result([profile], [profile])
    assert result["messages"] == [[SEED_MESSAGE, "error"]]
    assert result["editing"] is None
    assert result["name"] == ""


@NEEDS_NODE
@pytest.mark.parametrize("seed", [None, 0, 99, 2**53 - 1])
def test_edit_loads_a_profile_with_a_seed_the_browser_holds_exactly(seed):
    profile = dict(as_json(every_field_set_profile()), random_seed=seed)
    result = edit_result([profile], [profile])
    assert result["messages"] == []
    assert result["editing"] == "Every field"


@NEEDS_NODE
def test_the_last_edit_click_wins_when_two_reloads_overlap():
    first, second = as_json(every_field_set_profile("first", "First")), other_json()
    body = (
        "const gates = [];\n"
        "request = (path) => new Promise((resolve) => gates.push(resolve));\n"
        "profiles = [" + json.dumps(first) + "," + json.dumps(second) + "];\n"
        "renderSavedProfiles();\n"
        "listButton('first', 'Edit').listeners.click();\n"
        "listButton('other', 'Edit').listeners.click();\n"
        "const both = {profiles: profiles};\n"
        "gates[1](both);\n"
        "await new Promise((resolve) => setTimeout(resolve, 0));\n"
        "gates[0](both);\n"
        "await new Promise((resolve) => setTimeout(resolve, 0));\n"
        "console.log(JSON.stringify([element('profile-id').value, editingProfile.id]));\n"
    )
    assert run_node(body) == ["other", "other"]


def start_editing(profile):
    return (
        "const profile = " + json.dumps(profile) + ";\n"
        "startEditingProfile(profile);\n"
    )


@NEEDS_NODE
def test_start_editing_fills_the_form_locks_the_id_and_switches_the_title():
    profile = as_json(every_field_set_profile())
    body = start_editing(profile) + (
        "console.log(JSON.stringify({\n"
        "  id: element('profile-id').value, name: element('profile-name').value,\n"
        "  domains: element('profile-website-domains').value,\n"
        "  locked: element('profile-id').readOnly,\n"
        "  title: element('profile-form-title').textContent,\n"
        "  newHidden: element('new-profile').hidden,\n"
        "  revealed: revealed.length === 1 && revealed[0] === element('profile-form-title'),\n"
        "  focused: document.activeElement === element('profile-name'),\n"
        "  base: editingProfile === profile\n"
        "}));\n"
    )
    assert run_node(body) == {
        "id": "every-field", "name": "Every field", "domains": ", ".join(UNSORTED_DOMAINS),
        "locked": True, "title": "Edit profile", "newHidden": False, "revealed": True,
        "focused": True, "base": True,
    }


@NEEDS_NODE
def test_reset_profile_editing_unlocks_the_id_and_restores_the_title():
    body = start_editing(as_json(every_field_set_profile())) + (
        "resetProfileEditing();\n"
        "console.log(JSON.stringify({\n"
        "  locked: element('profile-id').readOnly,\n"
        "  title: element('profile-form-title').textContent,\n"
        "  newHidden: element('new-profile').hidden,\n"
        "  base: editingProfile\n"
        "}));\n"
    )
    assert run_node(body) == {
        "locked": False, "title": "Save profile", "newHidden": True, "base": None,
    }


@NEEDS_NODE
def test_the_new_profile_button_clears_the_form_and_leaves_editing():
    body = start_editing(as_json(every_field_set_profile())) + (
        "element('new-profile').listeners.click();\n"
        "console.log(JSON.stringify({\n"
        "  resets: formResets, id: element('profile-id').value,\n"
        "  name: element('profile-name').value, locked: element('profile-id').readOnly,\n"
        "  title: element('profile-form-title').textContent,\n"
        "  newHidden: element('new-profile').hidden, base: editingProfile,\n"
        "  focused: document.activeElement === element('profile-id')\n"
        "}));\n"
    )
    assert run_node(body) == {
        "resets": 1, "id": "", "name": "", "locked": False, "title": "Save profile",
        "newHidden": True, "base": None, "focused": True,
    }


@NEEDS_NODE
def test_locking_the_private_panels_clears_the_form_and_leaves_editing():
    body = start_editing(as_json(every_field_set_profile())) + (
        "setPrivateDataPanels('Token changed. Choose Check token to load.');\n"
        "console.log(JSON.stringify({\n"
        "  resets: formResets, id: element('profile-id').value,\n"
        "  name: element('profile-name').value, locked: element('profile-id').readOnly,\n"
        "  title: element('profile-form-title').textContent,\n"
        "  newHidden: element('new-profile').hidden, base: editingProfile,\n"
        "  list: element('saved-profiles').textContent\n"
        "}));\n"
    )
    assert run_node(body) == {
        "resets": 1, "id": "", "name": "", "locked": False, "title": "Save profile",
        "newHidden": True, "base": None,
        "list": "Token changed. Choose Check token to load.",
    }


# Save.

SUBMIT = (
    "const submit = () => element('profile-form').listeners.submit({preventDefault() {}});\n"
)


@NEEDS_NODE
def test_saving_an_edit_keeps_editing_the_same_profile():
    profile = as_json(every_field_set_profile())
    body = SUBMIT + start_editing(profile) + (
        "await submit();\n"
        "console.log(JSON.stringify({\n"
        "  sent: sent, messages: messages,\n"
        "  base: editingProfile === profile, locked: element('profile-id').readOnly,\n"
        "  title: element('profile-form-title').textContent,\n"
        "  newHidden: element('new-profile').hidden\n"
        "}));\n"
    )
    result = run_node(body)
    assert result["sent"] == [["/api/profiles", "POST", profile]]
    assert result["messages"] == [["Profile saved.", "success"]]
    assert result["base"] is True
    assert (result["locked"], result["title"], result["newHidden"]) == (
        True, "Edit profile", False,
    )


@NEEDS_NODE
def test_editing_another_profile_while_a_save_is_in_flight_saves_the_other_profile_intact():
    first, second = as_json(every_field_set_profile("first", "First")), other_json()
    body = SUBMIT + (
        "const gates = [];\n"
        "request = (path, method, body) => new Promise((resolve) => {\n"
        "  sent.push([path, method, body]);\n"
        "  gates.push(resolve);\n"
        "});\n"
        "const first = " + json.dumps(first) + ";\n"
        "const second = " + json.dumps(second) + ";\n"
        "startEditingProfile(first);\n"
        "element('upload-delay').value = '70';\n"
        "const savingFirst = submit();\n"
        "startEditingProfile(second);\n"
        "gates[0]({});\n"
        "await savingFirst;\n"
        "element('upload-delay').value = '71';\n"
        "const savingSecond = submit();\n"
        "gates[1]({});\n"
        "await savingSecond;\n"
        "console.log(JSON.stringify(sent.map((call) => call[2])));\n"
    )
    first_body, second_body = run_node(body)
    assert first_body["id"] == "first"
    for key in HIDDEN_FIELDS:
        assert first_body[key] == first[key], key
    assert second_body["id"] == "other"
    assert second_body["upload"]["delay_ms"] == 71
    for key in HIDDEN_FIELDS:
        assert second_body[key] == second[key], key
        assert second_body[key] != first[key], key


@NEEDS_NODE
def test_a_failed_save_reports_the_error_and_stays_in_edit_mode():
    profile = as_json(every_field_set_profile())
    body = SUBMIT + (
        "request = async () => { throw new Error('profile is invalid'); };\n"
        + start_editing(profile)
        + "await submit();\n"
        "console.log(JSON.stringify([messages, editingProfile === profile]));\n"
    )
    assert run_node(body) == [[["profile is invalid", "error"]], True]


# The served page.


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


def test_long_names_and_ids_wrap_inside_the_row():
    css = re.search(r"<style>(.*?)</style>", DASHBOARD_HTML, re.DOTALL).group(1)
    rule = re.search(r"\.saved-profile-name strong,\s*\.saved-profile-id\s*\{([^}]*)\}", css)
    assert rule, "the name and id need a shared wrapping rule"
    assert "min-width: 0" in rule.group(1)
    assert "overflow-wrap: anywhere" in rule.group(1)
