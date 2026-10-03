"""Run the dashboard's Block globally and Add to profile button logic in node."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
WHEN_IT_APPLIES = (
    "It applies on its own while profiles are running. "
    "After Reset or Panic choose Start. After Panic also turn household blocking back on."
)


def served_functions():
    start = SCRIPT.index("function blockButtonState(")
    return SCRIPT[start : SCRIPT.index("function renderObservations(", start)]


def function_body(name):
    """Return the source of one top-level dashboard function."""

    match = re.search(r"\n    (?:async )?function " + re.escape(name) + r"\(", SCRIPT)
    assert match, name
    following = re.search(r"\n    (?:async )?function \w+\(", SCRIPT[match.end() :])
    end = match.end() + following.start() if following else len(SCRIPT)
    return SCRIPT[match.start() : end]


def run_node(program):
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def profile_state(label, disabled, domain="facebook.com"):
    return {"label": label, "disabled": disabled, "ariaLabel": label + " " + domain}


BLOCK_ADD = {
    "label": "Block globally",
    "ariaLabel": "Block globally facebook.com",
    "path": "/api/observations/add-global-block",
    "busy": "Adding facebook.com to the global block list...",
    "done": "Added facebook.com to the global block list. " + WHEN_IT_APPLIES,
}
BLOCK_REMOVE = {
    "label": "Unblock",
    "ariaLabel": "Unblock facebook.com",
    "path": "/api/observations/remove-global-block",
    "busy": "Removing facebook.com from the global block list...",
    "done": "Removed facebook.com from the global block list. " + WHEN_IT_APPLIES,
}

CASES = {
    "an_unblocked_name_offers_block_globally": (
        "blockButtonState({domain:'facebook.com', globally_blocked:false})",
        BLOCK_ADD,
    ),
    "a_blocked_name_offers_unblock": (
        "blockButtonState({domain:'facebook.com', globally_blocked:true})",
        BLOCK_REMOVE,
    ),
    "a_missing_flag_counts_as_not_blocked": (
        "blockButtonState({domain:'facebook.com'})",
        BLOCK_ADD,
    ),
    "only_a_real_true_counts_as_blocked": (
        "blockButtonState({domain:'facebook.com', globally_blocked:'true'})",
        BLOCK_ADD,
    ),
    "a_name_in_the_profile_is_already_in": (
        "profileButtonState('facebook.com', "
        "{name:'Slow', website_domains:['a.test','facebook.com']})",
        profile_state("Already in Slow", True),
    ),
    "a_name_missing_from_the_profile_can_be_added": (
        "profileButtonState('facebook.com', {name:'Slow', website_domains:['a.test']})",
        profile_state("Add to profile", False),
    ),
    "a_parent_entry_does_not_cover_a_subdomain": (
        "profileButtonState('www.facebook.com', {name:'Slow', website_domains:['facebook.com']})",
        profile_state("Add to profile", False, "www.facebook.com"),
    ),
    "a_subdomain_entry_does_not_cover_the_parent": (
        "profileButtonState('facebook.com', {name:'Slow', website_domains:['www.facebook.com']})",
        profile_state("Add to profile", False),
    ),
    "a_profile_without_a_website_list_can_be_added_to": (
        "profileButtonState('facebook.com', {name:'Slow'})",
        profile_state("Add to profile", False),
    ),
    "no_profile_disables_the_button": (
        "profileButtonState('facebook.com', undefined)",
        profile_state("Add to profile", True),
    ),
    "a_null_profile_disables_the_button": (
        "profileButtonState('facebook.com', null)",
        profile_state("Add to profile", True),
    ),
}


@NODE
@pytest.mark.parametrize("name", CASES)
def test_button_states(name):
    expression, expected = CASES[name]
    output = run_node(served_functions() + "\nconsole.log(JSON.stringify(" + expression + "));\n")
    assert json.loads(output) == expected


@NODE
@pytest.mark.parametrize(
    "count, text",
    [(0, "0 queries"), (1, "1 query"), (2, "2 queries"), (1000, "1000 queries")],
)
def test_query_count_text_uses_the_singular_for_one(count, text):
    output = run_node(served_functions() + "\nconsole.log(queryCountText(" + str(count) + "));\n")
    assert output == text


def run_change(domain, blocked, fail):
    """Click the block button once with stubbed requests and report what happened."""

    request = "Promise.reject(new Error('the helper is busy'))" if fail else "Promise.resolve({})"
    program = (
        served_functions()
        + "\nconst calls = [];\n"
        + "function request(path, method, body) { calls.push(['request', path, method, body]);"
        + " return " + request + "; }\n"
        + "function showMessage(text, kind) { calls.push(['message', text, kind]); }\n"
        + "function refreshAfterSuccess(text) { calls.push(['success', text]); }\n"
        + "function setButtonBusy(button, busy) { button.busy = busy; }\n"
        + function_body("changeGlobalBlock")
        + "\n(async () => {\n"
        + "  const observation = {domain: '" + domain + "', globally_blocked: "
        + ("true" if blocked else "false") + "};\n"
        + "  const button = {busy: null};\n"
        + "  let changes = 0;\n"
        + "  await changeGlobalBlock(observation, button, () => { changes += 1; });\n"
        + "  console.log(JSON.stringify({calls, blocked: observation.globally_blocked,"
        + " busy: button.busy, changes}));\n"
        + "})();\n"
    )
    return json.loads(run_node(program))


@NODE
def test_blocking_sends_the_add_request_and_remembers_the_new_state():
    result = run_change("facebook.com", blocked=False, fail=False)
    assert result["calls"] == [
        ["message", "Adding facebook.com to the global block list...", "working"],
        ["request", "/api/observations/add-global-block", "POST", {"domain": "facebook.com"}],
        ["success", "Added facebook.com to the global block list. " + WHEN_IT_APPLIES],
    ]
    assert result["blocked"] is True
    assert result["busy"] is False
    assert result["changes"] == 1


@NODE
def test_unblocking_sends_the_remove_request_and_remembers_the_new_state():
    result = run_change("facebook.com", blocked=True, fail=False)
    assert result["calls"] == [
        ["message", "Removing facebook.com from the global block list...", "working"],
        ["request", "/api/observations/remove-global-block", "POST", {"domain": "facebook.com"}],
        ["success", "Removed facebook.com from the global block list. " + WHEN_IT_APPLIES],
    ]
    assert result["blocked"] is False
    assert result["busy"] is False
    assert result["changes"] == 1


@NODE
@pytest.mark.parametrize("blocked", [False, True])
def test_a_failed_change_keeps_the_state_and_shows_the_error(blocked):
    result = run_change("facebook.com", blocked=blocked, fail=True)
    assert result["calls"][-1] == ["message", "the helper is busy", "error"]
    assert not any(call[0] == "success" for call in result["calls"])
    assert result["blocked"] is blocked
    assert result["busy"] is False
    assert result["changes"] == 1


def test_the_success_text_says_when_the_change_applies():
    # While the scheduler runs it applies a saved change on its next tick. After Reset or
    # Panic it is paused and a saved change waits for Start.
    assert "to enforce it" not in served_functions()


def test_each_row_shows_the_badge_and_both_buttons_from_the_state_helpers():
    body = function_body("renderObservations")
    assert "blockButtonState(observation)" in body
    assert 'badge.className = "observation-badge";' in body
    assert 'badge.textContent = "Blocked";' in body
    assert re.search(r"changeGlobalBlock\(\s*observation,\s*blockButton", body)
    assert "profileButtonState(observation.domain, selectedProfile)" in body
    assert "queryCountText(observation.query_count)" in body
    assert '" queries"' not in body
    assert "innerHTML" not in body


def test_the_profile_buttons_follow_the_profile_select_of_their_card():
    body = function_body("renderObservations")
    assert 'targetProfile.addEventListener("change"' in body
    assert "profiles.find((profile) => profile.id === targetProfile.value)" in body


def test_the_badge_has_a_style_and_the_remove_path_is_a_write_path():
    assert ".observation-badge {" in DASHBOARD_HTML
    assert "/api/observations/remove-global-block" in served_functions()


# ---------------------------------------------------------------------------------------------
# The rendered rows. A small fake DOM lets the real render and click code run in node.
# ---------------------------------------------------------------------------------------------

HARNESS = r"""
class FakeElement {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.listeners = {}; this.dataset = {};
    this.attrs = {}; this.className = ""; this.textContent = ""; this.hidden = false;
    this.disabled = false; this.title = ""; this.href = ""; this.target = ""; this.rel = "";
    this.type = ""; this.selected = false; this._value = "";
  }
  get value() {
    if (this.tagName === "select") {
      const chosen = this.children.find((child) => child.selected) || this.children[0];
      return chosen ? chosen._value : "";
    }
    return this._value;
  }
  set value(next) {
    if (this.tagName === "select") {
      this.children.forEach((child) => { child.selected = child._value === next; });
    } else {
      this._value = next;
    }
  }
  append(...nodes) { nodes.forEach((node) => this.children.push(node)); }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren() { this.children = []; }
  addEventListener(type, handler) {
    (this.listeners[type] = this.listeners[type] || []).push(handler);
  }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
  removeAttribute(name) { delete this.attrs[name]; }
  get childNodes() { return this.children; }
}
const document = { createElement: (tag) => new FakeElement(tag) };
const elements = {
  observations: new FakeElement("div"), "observation-count": new FakeElement("span")
};
function element(id) { return elements[id]; }
let profiles = [];
let observedDomainsByDevice = {};
let observedNames = new Set();
const openObservationGroups = new Set();
const observationProfileChoice = new Map();
const calls = [];
const confirmations = [];
const pending = [];
function confirmWorkflow(message) {
  confirmations.push(message);
  return Promise.resolve(true);
}
function request(path, method, body) {
  calls.push(["request", path, method, body]);
  return new Promise((resolve, reject) => pending.push({ resolve, reject }));
}
function showMessage(text, kind) { calls.push(["message", text, kind]); }
function refreshAfterSuccess(text) { calls.push(["success", text]); }
function deviceDisplayName(device) { return device.display_name || device.ipv4_address; }
function walk(node, visit) { visit(node); node.children.forEach((child) => walk(child, visit)); }
function findAll(node, test) {
  const found = [];
  walk(node, (n) => { if (test(n)) found.push(n); });
  return found;
}
function hasClass(node, name) { return node.className.split(" ").indexOf(name) !== -1; }
function byClass(node, name) { return findAll(node, (n) => hasClass(n, name)); }
async function click(button) {
  // Like a browser a disabled button does not fire its click handlers.
  if (button.disabled) return;
  for (const handler of (button.listeners.click || [])) await handler({});
}
function fire(node, type) { (node.listeners[type] || []).forEach((handler) => handler({})); }
function cards() { return elements.observations.children; }
function selectOf(card) { return findAll(card, (n) => n.tagName === "select")[0]; }
function rowsOf(card) { return byClass(card, "observation-domain"); }
function buttonsOf(row) { return findAll(row, (n) => n.tagName === "button"); }
function describeRow(row) {
  const [profile, block] = buttonsOf(row);
  const badge = byClass(row, "observation-badge")[0];
  return {
    host: byClass(row, "observation-domain-host")[0].children[0].textContent,
    badgeHidden: badge.hidden, badgeText: badge.textContent,
    profile: [profile.textContent, profile.disabled, profile.attrs["aria-label"] || null],
    block: [block.textContent, block.attrs["aria-label"] || null]
  };
}
function describeCard(card) { return rowsOf(card).map(describeRow); }
function requests() { return calls.filter((call) => call[0] === "request"); }
"""

RENDER_FUNCTIONS = (
    "setButtonBusy",
    "domainSearchUrl",
    "domainHint",
    "renderObservations",
    "addDomainToProfile",
    "changeGlobalBlock",
)

# Both profiles already allow only listed sites, so adding to them asks nothing first.
SLOW = {"id": "slow", "name": "Slow", "website_domains": ["a.example"], "website_mode": "allow_only"}
FAST = {"id": "fast", "name": "Fast", "website_domains": [], "website_mode": "allow_only"}


def function_source(name):
    return function_body(name)


def row_item(domain, blocked=False, count=3):
    """Build one domain the way /api/observations sends it."""

    return {
        "domain": domain,
        "query_count": count,
        "first_seen": 1,
        "last_seen": 1,
        "label": {"kind": "app", "name": "App", "category": "Cat", "known": True},
        "seen_with": [],
        "globally_blocked": blocked,
    }


def device(address, domains, assigned="slow"):
    return {
        "ipv4_address": address,
        "assigned_profile_id": assigned,
        "assigned_device_id": None,
        "domains": domains,
    }


def run_dashboard(steps, devices, profiles=None):
    """Render the devices and then run the async JS steps. Print what the steps return."""

    program = (
        HARNESS
        + served_functions()
        + "".join(function_source(name) for name in RENDER_FUNCTIONS)
        + "\nprofiles = "
        + json.dumps([SLOW, FAST] if profiles is None else profiles)
        + ";\nconst devices = "
        + json.dumps(devices)
        + ";\nrenderObservations({available: true, devices: devices, total_domains: 0});\n"
        + "(async () => {\n"
        + steps
        + "\n})().catch((error) => { console.error(error.stack); process.exit(1); });\n"
    )
    return json.loads(run_node(program))


def one_card():
    return [
        device(
            "192.168.50.124",
            [row_item("a.example"), row_item("b.example", blocked=True)],
            assigned="slow",
        )
    ]


@NODE
def test_a_blocked_row_shows_its_badge_and_an_unblock_button_at_render():
    out = run_dashboard("console.log(JSON.stringify(describeCard(cards()[0])));", one_card())

    assert out == [
        {
            "host": "a.example",
            "badgeHidden": True,
            "badgeText": "Blocked",
            "profile": ["Already in Slow", True, "Already in Slow a.example"],
            "block": ["Block globally", "Block globally a.example"],
        },
        {
            "host": "b.example",
            "badgeHidden": False,
            "badgeText": "Blocked",
            "profile": ["Add to profile", False, "Add to profile b.example"],
            "block": ["Unblock", "Unblock b.example"],
        },
    ]


@NODE
def test_the_profile_buttons_start_from_the_profile_the_card_selects():
    devices = [device("192.168.50.124", [row_item("a.example")], assigned="fast")]
    steps = """
      console.log(JSON.stringify({
        selected: selectOf(cards()[0]).value, row: describeCard(cards()[0])[0].profile
      }));
    """

    out = run_dashboard(steps, devices)

    assert out["selected"] == "fast"
    assert out["row"] == ["Add to profile", False, "Add to profile a.example"]


@NODE
def test_without_any_profile_the_profile_button_is_disabled():
    out = run_dashboard(
        "console.log(JSON.stringify(describeCard(cards()[0])[0].profile));",
        one_card(),
        profiles=[],
    )

    assert out == ["Add to profile", True, "Add to profile a.example"]


@NODE
def test_changing_the_select_updates_the_profile_buttons_of_that_card_only():
    devices = [
        device("192.168.50.124", [row_item("a.example")], assigned="slow"),
        device("192.168.50.125", [row_item("a.example")], assigned="fast"),
    ]
    steps = """
      const first = selectOf(cards()[0]);
      const read = () => [describeCard(cards()[0])[0].profile, describeCard(cards()[1])[0].profile];
      const before = read();
      first.value = "fast"; fire(first, "change");
      const afterFast = read();
      first.value = "slow"; fire(first, "change");
      console.log(JSON.stringify({before, afterFast, afterSlow: read()}));
    """

    out = run_dashboard(steps, devices)

    already = ["Already in Slow", True, "Already in Slow a.example"]
    add = ["Add to profile", False, "Add to profile a.example"]
    assert out["before"] == [already, add]
    assert out["afterFast"] == [add, add]
    assert out["afterSlow"] == [already, add]


@NODE
def test_a_select_change_during_an_add_keeps_the_busy_button_disabled():
    steps = """
      const card = cards()[0];
      const select = selectOf(card);
      const button = buttonsOf(rowsOf(card)[1])[0];
      const first = click(button);
      const duringAdd = [button.disabled, button.getAttribute("aria-busy")];
      select.value = "fast"; fire(select, "change");
      const afterChange = [button.disabled, button.getAttribute("aria-busy")];
      const second = click(button);
      const requestsBeforeReply = requests().length;
      pending.shift().resolve({
        profile: { id: "slow", name: "Slow", website_domains: ["a.example"] }
      });
      await first;
      console.log(JSON.stringify({
        duringAdd, afterChange, requestsBeforeReply, requestsAfter: requests().length,
        end: [button.textContent, button.disabled, button.getAttribute("aria-busy")]
      }));
    """

    out = run_dashboard(steps, one_card())

    assert out["duringAdd"] == [True, "true"]
    assert out["afterChange"] == [True, "true"]
    assert out["requestsBeforeReply"] == 1
    assert out["requestsAfter"] == 1
    # The select now shows Fast. So the finished button offers to add to Fast again.
    assert out["end"] == ["Add to profile", False, None]


@NODE
def test_a_finished_add_updates_the_profile_list_and_the_button_at_once():
    steps = """
      const card = cards()[0];
      const button = buttonsOf(rowsOf(card)[1])[0];
      const done = click(button);
      pending.shift().resolve({
        profile: { id: "slow", name: "Slow", website_domains: ["a.example", "b.example"] }
      });
      await done;
      console.log(JSON.stringify({
        calls,
        slow: profiles.find((profile) => profile.id === "slow").website_domains,
        fast: profiles.find((profile) => profile.id === "fast").website_domains,
        button: [button.textContent, button.disabled, button.attrs["aria-label"],
          button.getAttribute("aria-busy")]
      }));
    """

    out = run_dashboard(steps, one_card())

    assert out["calls"] == [
        ["message", "Adding b.example to the profile...", "working"],
        [
            "request",
            "/api/observations/add-to-profile",
            "POST",
            {"domain": "b.example", "profile_id": "slow", "confirm_allow_only": False},
        ],
        ["success", "Added b.example to the profile."],
    ]
    assert out["slow"] == ["a.example", "b.example"]
    assert out["fast"] == []
    assert out["button"] == ["Already in Slow", True, "Already in Slow b.example", None]


@NODE
def test_a_failed_add_shows_the_error_and_offers_the_button_again():
    steps = """
      const card = cards()[0];
      const button = buttonsOf(rowsOf(card)[1])[0];
      const done = click(button);
      pending.shift().reject(new Error("the helper is busy"));
      await done;
      console.log(JSON.stringify({
        last: calls[calls.length - 1],
        slow: profiles.find((profile) => profile.id === "slow").website_domains,
        button: [button.textContent, button.disabled, button.getAttribute("aria-busy")]
      }));
    """

    out = run_dashboard(steps, one_card())

    assert out["last"] == ["message", "the helper is busy", "error"]
    assert out["slow"] == ["a.example"]
    assert out["button"] == ["Add to profile", False, None]


@NODE
def test_blocking_a_row_shows_the_badge_and_the_unblock_button_at_once():
    steps = """
      const card = cards()[0];
      const row = rowsOf(card)[0];
      const block = buttonsOf(row)[1];
      const first = click(block);
      pending.shift().resolve({});
      await first;
      const blocked = describeRow(row);
      const second = click(block);
      pending.shift().resolve({});
      await second;
      console.log(JSON.stringify({blocked, unblocked: describeRow(row), requests: requests()}));
    """

    out = run_dashboard(steps, one_card())

    assert out["blocked"]["badgeHidden"] is False
    assert out["blocked"]["block"] == ["Unblock", "Unblock a.example"]
    assert out["unblocked"]["badgeHidden"] is True
    assert out["unblocked"]["block"] == ["Block globally", "Block globally a.example"]
    assert out["requests"] == [
        ["request", "/api/observations/add-global-block", "POST", {"domain": "a.example"}],
        ["request", "/api/observations/remove-global-block", "POST", {"domain": "a.example"}],
    ]


@NODE
def test_a_failed_block_keeps_the_row_as_it_was():
    steps = """
      const row = rowsOf(cards()[0])[0];
      const block = buttonsOf(row)[1];
      const done = click(block);
      pending.shift().reject(new Error("the helper is busy"));
      await done;
      console.log(JSON.stringify({
        row: describeRow(row), busy: block.getAttribute("aria-busy"), disabled: block.disabled
      }));
    """

    out = run_dashboard(steps, one_card())

    assert out["row"]["badgeHidden"] is True
    assert out["row"]["block"] == ["Block globally", "Block globally a.example"]
    assert out["busy"] is None
    assert out["disabled"] is False
