"""Run the dashboard's Traffic discovery grouping and group button logic in node."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML, MAX_BATCH_DOMAINS

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
ENFORCE = "Apply the configuration to enforce it."
ADD_BLOCK = "/api/observations/add-global-block"
REMOVE_BLOCK = "/api/observations/remove-global-block"


def served_functions():
    # The group buttons reuse profileButtonState and blockButtonState so the slice starts there.
    start = SCRIPT.index("function blockButtonState(")
    return SCRIPT[start : SCRIPT.index("function renderObservations(", start)]


def run_node(program):
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def evaluate(expression, setup=""):
    program = (
        served_functions()
        + "\n"
        + setup
        + "\nconsole.log(JSON.stringify("
        + expression
        + "));"
    )
    return json.loads(run_node(program))


def item(domain, count, key=None, name=None, blocked=None):
    """Build one API item the way /api/observations sends it."""

    value = {"domain": domain, "query_count": count}
    if key is not None:
        value["group"] = {"key": key, "name": name}
    if blocked is not None:
        value["globally_blocked"] = blocked
    return value


IMMO_NAMES = [
    ("content.header.immobilienscout24.de", 4),
    ("osa.s24-media.immobilienscout24.de", 3),
    ("sso.immobilienscout24.de", 2),
    ("tenant-network.immobilienscout24.de", 2),
    ("tracking.immobilienscout24.de", 6),
    ("web.chatbot.immobilienscout24.de", 1),
    ("api.header.immobilienscout24.de", 4),
]
NETFLIX_NAMES = [("logs.dradis.netflix.com", 3), ("ipv4-c179-fra002-ix.1.oca.nflxvideo.net", 40)]


def immo(blocked=None):
    return [item(d, c, "label:immoscout24", "ImmoScout24", blocked) for d, c in IMMO_NAMES]


def netflix(blocked=None):
    return [item(d, c, "label:netflix", "Netflix", blocked) for d, c in NETFLIX_NAMES]


def group_of(items, name="ImmoScout24"):
    return {"key": "label:" + name.lower(), "name": name, "names": items}


@NODE
def test_the_real_example_makes_one_group_per_app_and_keeps_third_party_names_apart():
    domains = (
        immo()
        + netflix()
        + [
            item("config.extension.grammarly.com", 5, "label:grammarly", "Grammarly"),
            item("tags-eu.tiqcdn.com", 2, "domain:tiqcdn.com", "tiqcdn.com"),
            item("a1051.dscg4.akamai.net", 3, "domain:akamai.net", "akamai.net"),
        ]
    )

    groups = evaluate("groupObservations(" + json.dumps(domains) + ")")

    assert [(g["name"], g["totalQueries"], len(g["names"]), g["single"]) for g in groups] == [
        ("Netflix", 43, 2, False),
        ("ImmoScout24", 22, 7, False),
        ("Grammarly", 5, 1, True),
        ("akamai.net", 3, 1, True),
        ("tiqcdn.com", 2, 1, True),
    ]
    assert [g["key"] for g in groups] == [
        "label:netflix",
        "label:immoscout24",
        "label:grammarly",
        "domain:akamai.net",
        "domain:tiqcdn.com",
    ]


@NODE
def test_a_group_keeps_the_api_items_in_the_order_they_came():
    groups = evaluate("groupObservations(" + json.dumps(immo()) + ")")

    assert [name["domain"] for name in groups[0]["names"]] == [d for d, _ in IMMO_NAMES]
    assert groups[0]["names"][0] == immo()[0]


@NODE
def test_groups_with_the_same_total_sort_by_name_ignoring_case():
    domains = [
        item("z.example.com", 5, "domain:z.example.com", "zeta.example"),
        item("a.example.com", 5, "domain:a.example.com", "Alpha.example"),
        item("b.example.com", 5, "domain:b.example.com", "beta.example"),
        item("big.example.com", 9, "domain:big.example.com", "big.example"),
    ]

    groups = evaluate("groupObservations(" + json.dumps(domains) + ")")

    assert [g["name"] for g in groups] == [
        "big.example",
        "Alpha.example",
        "beta.example",
        "zeta.example",
    ]


@NODE
def test_a_bigger_total_beats_a_smaller_name():
    domains = [item("a.example.com", 1, "domain:a", "a"), item("z.example.com", 2, "domain:z", "z")]

    groups = evaluate("groupObservations(" + json.dumps(domains) + ")")

    assert [g["name"] for g in groups] == ["z", "a"]


@NODE
def test_a_missing_group_falls_back_to_the_name_itself():
    domains = [item("x.example.com", 2), item("y.example.com", 1)]

    groups = evaluate("groupObservations(" + json.dumps(domains) + ")")

    assert [(g["key"], g["name"], g["single"]) for g in groups] == [
        ("domain:x.example.com", "x.example.com", True),
        ("domain:y.example.com", "y.example.com", True),
    ]


@NODE
def test_no_names_make_no_groups():
    assert evaluate("groupObservations([])") == []
    assert evaluate("groupObservations(undefined)") == []


@NODE
def test_a_missing_query_count_counts_as_zero():
    domains = [{"domain": "x.example.com", "group": {"key": "domain:e", "name": "e"}}]

    groups = evaluate("groupObservations(" + json.dumps(domains) + ")")

    assert groups[0]["totalQueries"] == 0


def block_action(items):
    return evaluate("groupBlockAction(" + json.dumps(group_of(items)) + ")")


@NODE
def test_no_blocked_name_offers_block_all_with_every_name():
    action = block_action(immo(blocked=False))

    assert action == {
        "label": "Block all",
        "path": ADD_BLOCK,
        "domains": [d for d, _ in IMMO_NAMES],
    }


@NODE
def test_some_blocked_names_offer_block_remaining_with_only_the_open_ones():
    items = immo(blocked=False)
    items[0]["globally_blocked"] = True
    items[4]["globally_blocked"] = True

    action = block_action(items)

    assert action["label"] == "Block remaining"
    assert action["path"] == ADD_BLOCK
    assert action["domains"] == [d for i, (d, _) in enumerate(IMMO_NAMES) if i not in (0, 4)]


@NODE
def test_all_blocked_names_offer_unblock_all_with_every_name():
    action = block_action(immo(blocked=True))

    assert action == {
        "label": "Unblock all",
        "path": REMOVE_BLOCK,
        "domains": [d for d, _ in IMMO_NAMES],
    }


@NODE
def test_a_missing_or_loose_flag_counts_as_not_blocked_like_the_row_button():
    items = [
        item("a.example.com", 1, "domain:e", "e", "true"),
        item("b.example.com", 1, "domain:e", "e"),
    ]

    action = block_action(items)

    assert action["label"] == "Block all"
    assert action["domains"] == ["a.example.com", "b.example.com"]


SLOW = {"id": "slow", "name": "Slow", "website_domains": ["sso.immobilienscout24.de", "a.test"]}


def profile_action(items, profile):
    return evaluate(
        "groupProfileAction(" + json.dumps(group_of(items)) + ", " + json.dumps(profile) + ")"
    )


@NODE
def test_no_profile_disables_add_all():
    expected = {"label": "Add all to profile", "disabled": True, "domains": []}

    assert profile_action(immo(), None) == expected
    group = json.dumps(group_of(immo()))
    assert evaluate("groupProfileAction(" + group + ", undefined)") == expected


@NODE
def test_every_name_already_in_the_profile_disables_the_button_and_says_so():
    profile = {"id": "slow", "name": "Slow", "website_domains": [d for d, _ in IMMO_NAMES]}

    assert profile_action(immo(), profile) == {
        "label": "All already in Slow",
        "disabled": True,
        "domains": [],
    }


@NODE
def test_missing_names_are_offered_and_the_ones_already_in_are_left_out():
    action = profile_action(immo(), SLOW)

    assert action["label"] == "Add all to profile"
    assert action["disabled"] is False
    assert action["domains"] == [d for d, _ in IMMO_NAMES if d != "sso.immobilienscout24.de"]


@NODE
def test_only_an_exact_match_counts_as_already_in_the_profile():
    profile = {"id": "p", "name": "P", "website_domains": ["immobilienscout24.de"]}

    action = profile_action(immo(), profile)

    assert action["disabled"] is False
    assert len(action["domains"]) == 7


@NODE
def test_a_profile_without_a_website_list_can_take_every_name():
    action = profile_action(immo(), {"id": "p", "name": "P"})

    assert action["disabled"] is False
    assert len(action["domains"]) == 7


@NODE
def test_the_block_messages_name_the_group_and_the_count():
    group = group_of(immo(blocked=False))
    adding = evaluate(
        "groupBlockMessages(" + json.dumps(group) + ", groupBlockAction(" + json.dumps(group) + "))"
    )
    assert adding == {
        "busy": "Adding 7 ImmoScout24 names to the global block list...",
        "done": "Added 7 ImmoScout24 names to the global block list. " + ENFORCE,
    }

    group = group_of(immo(blocked=True))
    removing = evaluate(
        "groupBlockMessages(" + json.dumps(group) + ", groupBlockAction(" + json.dumps(group) + "))"
    )
    assert removing == {
        "busy": "Removing 7 ImmoScout24 names from the global block list...",
        "done": "Removed 7 ImmoScout24 names from the global block list. " + ENFORCE,
    }


@NODE
def test_block_remaining_counts_only_the_names_it_changes_and_uses_the_singular_for_one():
    items = immo(blocked=True)
    items[0]["globally_blocked"] = False
    group = group_of(items)

    messages = evaluate(
        "groupBlockMessages(" + json.dumps(group) + ", groupBlockAction(" + json.dumps(group) + "))"
    )

    assert messages["busy"] == "Adding 1 ImmoScout24 name to the global block list..."


@NODE
def test_the_profile_messages_name_the_group_and_the_count():
    group = group_of(immo())
    action = evaluate(
        "groupProfileAction(" + json.dumps(group) + ", " + json.dumps(SLOW) + ")"
    )

    messages = evaluate(
        "groupProfileMessages(" + json.dumps(group) + ", " + json.dumps(action) + ")"
    )

    assert messages == {
        "busy": "Adding 6 ImmoScout24 names to the profile...",
        "done": "Added 6 ImmoScout24 names to the profile.",
    }


@NODE
@pytest.mark.parametrize(
    "blocked, text",
    [([False, False, False], ""), ([True, False, False], "1 blocked"),
     ([True, True, False], "2 blocked"), ([True, True, True], "All blocked")],
)
def test_the_group_says_how_many_of_its_names_are_blocked(blocked, text):
    items = [
        item("n%d.example.com" % i, 1, "domain:e", "e", flag) for i, flag in enumerate(blocked)
    ]

    assert evaluate("groupBlockedText(" + json.dumps(group_of(items)) + ")") == text


@NODE
def test_the_name_count_text_uses_the_singular_for_one():
    assert evaluate("groupNamesText(1)") == "1 name"
    assert evaluate("groupNamesText(7)") == "7 names"


@NODE
def test_the_open_key_joins_the_device_address_and_the_group_key():
    assert evaluate("observationGroupOpenKey('192.168.50.124', 'label:netflix')") == (
        "192.168.50.124|label:netflix"
    )
    one = evaluate("observationGroupOpenKey('192.168.50.124', 'label:netflix')")
    other = evaluate("observationGroupOpenKey('192.168.50.125', 'label:netflix')")
    assert one != other


def test_the_group_limit_matches_the_server_limit():
    assert "const GROUP_ACTION_MAX_NAMES = %d;" % MAX_BATCH_DOMAINS in served_functions()


@NODE
def test_a_group_that_is_too_big_for_one_call_says_why():
    assert evaluate("groupLimitNote(%d)" % MAX_BATCH_DOMAINS) == ""
    note = evaluate("groupLimitNote(%d)" % (MAX_BATCH_DOMAINS + 1))
    assert "65" in note and str(MAX_BATCH_DOMAINS) in note


# ---------------------------------------------------------------------------------------------
# The rendered groups. A small fake DOM lets the real render and click code run in node.
# ---------------------------------------------------------------------------------------------

HARNESS = r"""
class FakeElement {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.listeners = {}; this.dataset = {};
    this.attrs = {}; this.className = ""; this.textContent = ""; this.hidden = false;
    this.disabled = false; this.title = ""; this.href = ""; this.target = ""; this.rel = "";
    this.type = ""; this.open = false; this.selected = false; this._value = "";
  }
  get value() {
    if (this.tagName === "select") {
      const chosen = this.children.find((child) => child.selected) || this.children[0];
      return chosen ? chosen._value : "";
    }
    return this._value;
  }
  set value(next) {
    if (this.tagName === "select") this.children.forEach((child) => { child.selected = child._value === next; });
    else this._value = next;
  }
  append(...nodes) { nodes.forEach((node) => this.children.push(node)); }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren() { this.children = []; }
  addEventListener(type, handler) { (this.listeners[type] = this.listeners[type] || []).push(handler); }
  setAttribute(name, value) { this.attrs[name] = value; }
  removeAttribute(name) { delete this.attrs[name]; }
  get childNodes() { return this.children; }
}
const document = { createElement: (tag) => new FakeElement(tag) };
const elements = { observations: new FakeElement("div"), "observation-count": new FakeElement("span") };
function element(id) { return elements[id]; }
let profiles = [];
let observedDomainsByDevice = {};
let observedNames = new Set();
const openObservationGroups = new Set();
const observationProfileChoice = new Map();
const calls = [];
let requestResult = {};
let requestFails = false;
function request(path, method, body) {
  calls.push(["request", path, method, body]);
  return requestFails ? Promise.reject(new Error("the helper is busy")) : Promise.resolve(requestResult);
}
function showMessage(text, kind) { calls.push(["message", text, kind]); }
function refreshAfterSuccess(text) { calls.push(["success", text]); }
function setButtonBusy(button, busy) { button.disabled = busy; }
function deviceDisplayName(device) { return device.display_name || device.ipv4_address; }
function walk(node, visit) { visit(node); node.children.forEach((child) => walk(child, visit)); }
function findAll(node, test) { const found = []; walk(node, (n) => { if (test(n)) found.push(n); }); return found; }
function hasClass(node, name) { return node.className.split(" ").indexOf(name) !== -1; }
function byClass(node, name) { return findAll(node, (n) => hasClass(n, name)); }
async function click(button) {
  const event = { stopped: false, stopPropagation() { this.stopped = true; } };
  for (const handler of (button.listeners.click || [])) await handler(event);
  return event;
}
function fire(node, type) { (node.listeners[type] || []).forEach((handler) => handler({})); }
function cards() { return elements.observations.children; }
function topList(card) { return findAll(card, (n) => n.tagName === "ul" && hasClass(n, "observation-domain-list"))[0]; }
function groupParts(card, index) {
  const item = topList(card).children[index];
  const details = findAll(item, (n) => n.tagName === "details")[0];
  return {
    item, details,
    name: byClass(details, "observation-group-name")[0],
    count: byClass(details, "observation-group-count")[0],
    queries: byClass(details, "observation-domain-count")[0],
    badge: byClass(details, "observation-badge")[0],
    profileButton: byClass(details, "observation-group-profile-button")[0],
    blockButton: byClass(details, "observation-group-block-button")[0],
    rows: byClass(details, "observation-domain")
  };
}
function describeTop(card) {
  return topList(card).children.map((item, index) => {
    if (findAll(item, (n) => n.tagName === "details").length === 0) {
      return { kind: "row", host: byClass(item, "observation-domain-host")[0].children[0].textContent };
    }
    const parts = groupParts(card, index);
    return {
      kind: "group", name: parts.name.textContent, names: parts.count.textContent,
      queries: parts.queries.textContent, open: parts.details.open, rows: parts.rows.length
    };
  });
}
"""

RENDER_FUNCTIONS = (
    "domainSearchUrl",
    "domainHint",
    "renderObservations",
    "addDomainToProfile",
    "changeGlobalBlock",
    "changeGroupBlock",
    "addGroupToProfile",
)


def function_body(name):
    """Return the source of one top-level dashboard function."""

    match = re.search(r"\n    (?:async )?function " + re.escape(name) + r"\(", SCRIPT)
    assert match, name
    following = re.search(r"\n    (?:async )?function \w+\(", SCRIPT[match.end() :])
    end = match.end() + following.start() if following else len(SCRIPT)
    return SCRIPT[match.start() : end]


def run_dashboard(steps, devices, profiles=None):
    """Render the devices and then run the async JS steps. Print what the steps return."""

    program = (
        HARNESS
        + served_functions()
        + "".join(function_body(name) for name in RENDER_FUNCTIONS)
        + "\nprofiles = "
        + json.dumps(profiles if profiles is not None else [SLOW_PROFILE, FAST_PROFILE])
        + ";\nconst devices = "
        + json.dumps(devices)
        + ";\nfunction render() { renderObservations({available: true, devices: devices,"
        + " total_domains: 0}); }\n"
        + "render();\n(async () => {\n"
        + steps
        + "\n})().catch((error) => { console.error(error.stack); process.exit(1); });\n"
    )
    return json.loads(run_node(program))


SLOW_PROFILE = {"id": "slow", "name": "Slow", "website_domains": []}
FAST_PROFILE = {"id": "fast", "name": "Fast", "website_domains": []}


def device(address, domains, assigned="slow"):
    return {
        "ipv4_address": address,
        "assigned_profile_id": assigned,
        "assigned_device_id": None,
        "domains": domains,
    }


def with_labels(items):
    """Add the label the server sends so the rows render like the real thing."""

    for value in items:
        value.setdefault("label", {"kind": "app", "name": "App", "category": "Cat", "known": True})
        value.setdefault("seen_with", [])
    return items


def example_device(address="192.168.50.124"):
    third_party = [
        item("config.extension.grammarly.com", 5, "label:grammarly", "Grammarly"),
        item("tags-eu.tiqcdn.com", 2, "domain:tiqcdn.com", "tiqcdn.com"),
        item("a1051.dscg4.akamai.net", 3, "domain:akamai.net", "akamai.net"),
    ]
    return device(address, with_labels(immo() + netflix() + third_party))


@NODE
def test_groups_render_collapsed_with_counts_in_order_and_single_names_stay_plain_rows():
    out = run_dashboard("console.log(JSON.stringify(describeTop(cards()[0])));", [example_device()])

    assert out == [
        {"kind": "group", "name": "Netflix", "names": "2 names", "queries": "43 queries",
         "open": False, "rows": 2},
        {"kind": "group", "name": "ImmoScout24", "names": "7 names", "queries": "22 queries",
         "open": False, "rows": 7},
        {"kind": "row", "host": "config.extension.grammarly.com"},
        {"kind": "row", "host": "a1051.dscg4.akamai.net"},
        {"kind": "row", "host": "tags-eu.tiqcdn.com"},
    ]


@NODE
def test_a_single_name_row_is_the_row_the_code_always_drew():
    steps = """
      const item = topList(cards()[0]).children[0];
      console.log(JSON.stringify({
        tag: item.tagName, cls: item.className, details: findAll(item, (n) => n.tagName === "details").length,
        children: item.children.map((child) => child.tagName + "." + child.className),
        buttons: findAll(item, (n) => n.tagName === "button").map((b) => b.textContent)
      }));
    """
    single = device("192.168.50.124", with_labels([item("config.extension.grammarly.com", 5, "label:grammarly", "Grammarly")]))

    out = run_dashboard(steps, [single])

    assert out["tag"] == "li"
    assert out["cls"] == "observation-domain"
    assert out["details"] == 0
    assert out["children"] == [
        "div.observation-domain-name", "span.observation-domain-count",
        "button.observation-profile-button", "button.",
    ]
    assert out["buttons"] == ["Add to profile", "Block globally"]


@NODE
def test_an_open_group_stays_open_when_the_dashboard_renders_again():
    steps = """
      const first = groupParts(cards()[0], 1);
      first.details.open = true;
      fire(first.details, "toggle");
      render();
      const afterOpen = describeTop(cards()[0]).map((entry) => entry.open);
      groupParts(cards()[0], 1).details.open = false;
      fire(groupParts(cards()[0], 1).details, "toggle");
      render();
      const afterClose = describeTop(cards()[0]).map((entry) => entry.open);
      console.log(JSON.stringify({afterOpen, afterClose, keys: Array.from(openObservationGroups)}));
    """

    out = run_dashboard(steps, [example_device()])

    assert out["afterOpen"] == [False, True, None, None, None]
    assert out["afterClose"] == [False, False, None, None, None]
    assert out["keys"] == []


@NODE
def test_the_open_state_belongs_to_one_group_on_one_device():
    steps = """
      const first = groupParts(cards()[0], 1);
      first.details.open = true;
      fire(first.details, "toggle");
      render();
      console.log(JSON.stringify({
        keys: Array.from(openObservationGroups),
        firstDevice: describeTop(cards()[0])[1].open,
        secondDevice: describeTop(cards()[1])[1].open
      }));
    """

    out = run_dashboard(steps, [example_device("192.168.50.124"), example_device("192.168.50.125")])

    assert out == {
        "keys": ["192.168.50.124|label:immoscout24"],
        "firstDevice": True,
        "secondDevice": False,
    }


@NODE
def test_the_header_block_button_blocks_every_name_in_one_call_and_turns_into_unblock_all():
    steps = """
      const before = groupParts(cards()[0], 1);
      const labelBefore = before.blockButton.textContent;
      const event = await click(before.blockButton);
      const after = groupParts(cards()[0], 1);
      const afterBlock = {
        header: before.blockButton.textContent, badge: before.badge.textContent,
        badgeHidden: before.badge.hidden, rows: before.rows.map((row) => row.children[3].textContent)
      };
      await click(before.blockButton);
      console.log(JSON.stringify({
        labelBefore, stopped: event.stopped, afterBlock, calls,
        last: {header: before.blockButton.textContent, badgeHidden: before.badge.hidden,
               rows: before.rows.map((row) => row.children[3].textContent)}
      }));
    """
    names = [d for d, _ in IMMO_NAMES]

    out = run_dashboard(steps, [example_device()])

    assert out["labelBefore"] == "Block all"
    assert out["stopped"] is True
    assert out["calls"] == [
        ["message", "Adding 7 ImmoScout24 names to the global block list...", "working"],
        ["request", ADD_BLOCK, "POST", {"domains": names}],
        ["success", "Added 7 ImmoScout24 names to the global block list. " + ENFORCE],
        ["message", "Removing 7 ImmoScout24 names from the global block list...", "working"],
        ["request", REMOVE_BLOCK, "POST", {"domains": names}],
        ["success", "Removed 7 ImmoScout24 names from the global block list. " + ENFORCE],
    ]
    assert out["afterBlock"] == {
        "header": "Unblock all", "badge": "All blocked", "badgeHidden": False,
        "rows": ["Unblock"] * 7,
    }
    assert out["last"] == {"header": "Block all", "badgeHidden": True, "rows": ["Block globally"] * 7}


@NODE
def test_unblocking_one_name_in_a_blocked_group_turns_the_header_into_block_remaining():
    steps = """
      const parts = groupParts(cards()[0], 0);
      const start = parts.blockButton.textContent;
      await click(parts.rows[2].children[3]);
      console.log(JSON.stringify({
        start, header: parts.blockButton.textContent, badge: parts.badge.textContent,
        rows: parts.rows.map((row) => row.children[3].textContent), calls
      }));
    """
    blocked = device("192.168.50.124", with_labels(immo(blocked=True)))

    out = run_dashboard(steps, [blocked])

    assert out["start"] == "Unblock all"
    assert out["header"] == "Block remaining"
    assert out["badge"] == "6 blocked"
    assert out["rows"][2] == "Block globally"
    assert out["rows"].count("Unblock") == 6
    assert out["calls"][1] == ["request", REMOVE_BLOCK, "POST", {"domain": IMMO_NAMES[2][0]}]


@NODE
def test_block_remaining_sends_only_the_names_that_are_still_open():
    steps = """
      const parts = groupParts(cards()[0], 0);
      await click(parts.blockButton);
      console.log(JSON.stringify({header: parts.blockButton.textContent, calls}));
    """
    items = with_labels(immo(blocked=False))
    items[0]["globally_blocked"] = True
    items[1]["globally_blocked"] = True

    out = run_dashboard(steps, [device("192.168.50.124", items)])

    assert out["calls"][1] == [
        "request", ADD_BLOCK, "POST", {"domains": [d for d, _ in IMMO_NAMES[2:]]}]
    assert out["calls"][0][1] == "Adding 5 ImmoScout24 names to the global block list..."
    assert out["header"] == "Unblock all"


@NODE
def test_the_header_profile_button_adds_the_missing_names_to_the_selected_profile():
    steps = """
      const parts = groupParts(cards()[0], 1);
      const select = byClass(cards()[0], "observation-actions")[0].children[0];
      const labelBefore = parts.profileButton.textContent;
      requestResult = {profile: {id: "slow", name: "Slow", website_domains: INCLUDED.concat(ALL)}};
      const event = await click(parts.profileButton);
      console.log(JSON.stringify({
        labelBefore, stopped: event.stopped, calls, select: select.value,
        header: parts.profileButton.textContent, disabled: parts.profileButton.disabled,
        rows: parts.rows.map((row) => row.children[2].textContent),
        stored: profiles[0].website_domains.length
      }));
    """
    names = [d for d, _ in IMMO_NAMES]
    steps = "const ALL = %s; const INCLUDED = ['a.test'];\n" % json.dumps(names) + steps
    profile = {"id": "slow", "name": "Slow", "website_domains": ["a.test"]}

    out = run_dashboard(steps, [example_device()], profiles=[profile, FAST_PROFILE])

    assert out["labelBefore"] == "Add all to profile"
    assert out["stopped"] is True
    assert out["select"] == "slow"
    assert out["calls"] == [
        ["message", "Adding 7 ImmoScout24 names to the profile...", "working"],
        ["request", "/api/observations/add-to-profile", "POST",
         {"domains": names, "profile_id": "slow"}],
        ["success", "Added 7 ImmoScout24 names to the profile."],
    ]
    assert out["header"] == "All already in Slow"
    assert out["disabled"] is True
    assert out["rows"] == ["Already in Slow"] * 7
    assert out["stored"] == 8


@NODE
def test_the_header_profile_button_follows_the_cards_profile_select():
    steps = """
      const parts = groupParts(cards()[0], 1);
      const select = byClass(cards()[0], "observation-actions")[0].children[0];
      const before = parts.profileButton.textContent;
      select.value = "fast";
      fire(select, "change");
      console.log(JSON.stringify({
        before, after: parts.profileButton.textContent, disabled: parts.profileButton.disabled
      }));
    """
    slow = {"id": "slow", "name": "Slow", "website_domains": [d for d, _ in IMMO_NAMES]}

    out = run_dashboard(steps, [example_device()], profiles=[slow, FAST_PROFILE])

    assert out == {"before": "All already in Slow", "after": "Add all to profile", "disabled": False}


@NODE
def test_a_failed_group_call_keeps_the_state_and_shows_the_error():
    steps = """
      requestFails = true;
      const parts = groupParts(cards()[0], 1);
      await click(parts.blockButton);
      await click(parts.profileButton);
      console.log(JSON.stringify({
        calls, header: parts.blockButton.textContent, blockDisabled: parts.blockButton.disabled,
        profileDisabled: parts.profileButton.disabled,
        rows: parts.rows.map((row) => row.children[3].textContent)
      }));
    """

    out = run_dashboard(steps, [example_device()])

    assert out["calls"][-1] == ["message", "the helper is busy", "error"]
    assert not any(call[0] == "success" for call in out["calls"])
    assert out["header"] == "Block all"
    assert out["blockDisabled"] is False
    assert out["profileDisabled"] is False
    assert out["rows"] == ["Block globally"] * 7


@NODE
def test_a_group_over_the_call_limit_disables_its_header_buttons_and_says_why():
    many = with_labels([
        item("h%d.example.com" % i, 1, "label:big", "Big") for i in range(MAX_BATCH_DOMAINS + 1)
    ])
    steps = """
      const parts = groupParts(cards()[0], 0);
      await click(parts.blockButton);
      await click(parts.profileButton);
      console.log(JSON.stringify({
        count: parts.count.textContent, blockDisabled: parts.blockButton.disabled,
        blockTitle: parts.blockButton.title, profileDisabled: parts.profileButton.disabled,
        profileTitle: parts.profileButton.title, calls
      }));
    """

    out = run_dashboard(steps, [device("192.168.50.124", many)])

    assert out["count"] == "65 names"
    assert out["blockDisabled"] is True
    assert out["profileDisabled"] is True
    assert "65" in out["blockTitle"] and "65" in out["profileTitle"]
    assert out["calls"] == []


@NODE
def test_a_group_with_no_profile_available_disables_add_all():
    steps = """
      const parts = groupParts(cards()[0], 1);
      console.log(JSON.stringify({label: parts.profileButton.textContent, disabled: parts.profileButton.disabled}));
    """

    out = run_dashboard(steps, [example_device()], profiles=[])

    assert out == {"label": "Add all to profile", "disabled": True}


def test_the_open_state_is_a_module_level_set_updated_on_toggle():
    assert re.search(r"\n    const openObservationGroups = new Set\(\);", SCRIPT)
    body = function_body("renderObservations")
    assert "openObservationGroups.has(" in body
    assert "openObservationGroups.add(" in body
    assert "openObservationGroups.delete(" in body
    assert '"toggle"' in body
    assert "observationGroupOpenKey(device.ipv4_address, group.key)" in body


def test_the_header_buttons_do_not_toggle_the_group_and_nothing_uses_innerhtml():
    body = function_body("renderObservations")
    assert body.count("event.stopPropagation()") >= 2
    assert "innerHTML" not in body
    assert "groupObservations(device.domains" in body
    assert "group.single" in body


def test_the_group_row_has_a_style_that_beats_the_plain_details_rule():
    for selector in (
        ".observation-group {",
        ".observation-group-summary {",
        ".observation-group-body {",
        ".observation-group-summary::before",
    ):
        assert selector in DASHBOARD_HTML, selector
    assert ".observation-group[open] > .observation-group-summary::before" in DASHBOARD_HTML
