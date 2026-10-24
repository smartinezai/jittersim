"""Run the dashboard's Traffic discovery grouping and group button logic in node."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML, MAX_BATCH_DOMAINS

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
ENFORCE = (
    "It applies on its own while profiles are running. "
    "After Reset or Panic choose Start. After Panic also turn household blocking back on."
)
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


def item(domain, count, key=None, name=None, blocked=None, kind=None):
    """Build one API item the way /api/observations sends it."""

    value = {"domain": domain, "query_count": count}
    if key is not None:
        value["group"] = {"key": key, "name": name}
        if kind is not None:
            value["group"]["kind"] = kind
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


def group_of(items, name="ImmoScout24", kind=None):
    value = {"key": "label:" + name.lower(), "name": name, "names": items}
    if kind is not None:
        value["kind"] = kind
    return value


def kinded(items, kind):
    """Give every API item the group kind the server would send."""

    for value in items:
        value["group"]["kind"] = kind
    return items


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
def test_a_lowercase_name_sorts_before_an_uppercase_one_that_comes_later_in_the_alphabet():
    # A sort that looks at the character codes would put Beta before alpha.
    domains = [
        item("b.example.com", 5, "domain:b", "Beta"),
        item("a.example.com", 5, "domain:a", "alpha"),
        item("c.example.com", 5, "domain:c", "Charlie"),
        item("d.example.com", 5, "domain:d", "bravo"),
    ]

    groups = evaluate("groupObservations(" + json.dumps(domains) + ")")

    assert [g["name"] for g in groups] == ["alpha", "Beta", "bravo", "Charlie"]


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


def block_action(items, kind=None):
    return evaluate("groupBlockAction(" + json.dumps(group_of(items, kind=kind)) + ")")


def only(action, *keys):
    return {key: action[key] for key in keys}


@NODE
def test_no_blocked_name_offers_block_all_with_every_name():
    action = block_action(immo(blocked=False))

    assert action == {
        "label": "Block all",
        "path": ADD_BLOCK,
        "domains": [d for d, _ in IMMO_NAMES],
        "disabled": False,
        "ariaLabel": "Block all 7 ImmoScout24 names",
        "title": "Block all 7 ImmoScout24 names",
        "note": "",
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
    assert action["ariaLabel"] == "Block remaining 5 ImmoScout24 names"
    assert action["disabled"] is False


@NODE
def test_all_blocked_names_offer_unblock_all_with_every_name():
    action = block_action(immo(blocked=True))

    assert action == {
        "label": "Unblock all",
        "path": REMOVE_BLOCK,
        "domains": [d for d, _ in IMMO_NAMES],
        "disabled": False,
        "ariaLabel": "Unblock all 7 ImmoScout24 names",
        "title": "Unblock all 7 ImmoScout24 names",
        "note": "",
    }


@NODE
def test_the_block_button_name_uses_the_singular_for_one_name():
    items = immo(blocked=True)
    items[0]["globally_blocked"] = False

    assert block_action(items)["ariaLabel"] == "Block remaining 1 ImmoScout24 name"


@NODE
def test_a_missing_or_loose_flag_counts_as_not_blocked_like_the_row_button():
    items = [
        item("a.example.com", 1, "domain:e", "e", "true"),
        item("b.example.com", 1, "domain:e", "e"),
    ]

    action = block_action(items)

    assert action["label"] == "Block all"
    assert action["domains"] == ["a.example.com", "b.example.com"]


@NODE
def test_a_group_over_the_call_limit_has_a_disabled_block_action_with_the_note():
    many = [item("h%d.example.com" % i, 1) for i in range(MAX_BATCH_DOMAINS + 1)]

    action = block_action(many)

    assert action["disabled"] is True
    assert "65" in action["title"] and str(MAX_BATCH_DOMAINS) in action["title"]
    assert action["ariaLabel"] == "Block all 65 ImmoScout24 names"


@NODE
def test_a_group_of_exactly_the_call_limit_can_still_be_blocked():
    many = [item("h%d.example.com" % i, 1) for i in range(MAX_BATCH_DOMAINS)]

    assert block_action(many)["disabled"] is False


RISK_NOTE = (
    "Blocking a whole group of system or content delivery names could cut the device off the "
    "internet. Block single names instead."
)


@NODE
@pytest.mark.parametrize("kind", ["system", "cdn"])
def test_a_system_or_cdn_group_has_no_block_all(kind):
    action = block_action(immo(blocked=False), kind=kind)

    assert action["label"] == "Block all"
    assert action["disabled"] is True
    assert action["title"] == RISK_NOTE
    assert action["note"] == RISK_NOTE
    assert action["ariaLabel"] == "Block all 7 ImmoScout24 names"


@NODE
@pytest.mark.parametrize("kind", ["system", "cdn"])
def test_a_system_or_cdn_group_has_no_block_remaining_either(kind):
    items = immo(blocked=False)
    items[0]["globally_blocked"] = True

    action = block_action(items, kind=kind)

    assert action["label"] == "Block remaining"
    assert action["disabled"] is True
    assert action["title"] == RISK_NOTE


@NODE
@pytest.mark.parametrize("kind", ["system", "cdn"])
def test_unblocking_a_whole_system_or_cdn_group_stays_possible(kind):
    action = block_action(immo(blocked=True), kind=kind)

    assert action["label"] == "Unblock all"
    assert action["disabled"] is False
    assert action["title"] == "Unblock all 7 ImmoScout24 names"
    assert action["note"] == RISK_NOTE


@NODE
@pytest.mark.parametrize("kind", ["app", "iot", "tracker", "unknown", None, "something-new"])
def test_other_kinds_keep_the_block_all_button(kind):
    action = block_action(immo(blocked=False), kind=kind)

    assert action["disabled"] is False
    assert action["title"] == "Block all 7 ImmoScout24 names"
    assert action["note"] == ""


@NODE
def test_the_risk_note_is_true_for_both_kinds_and_names_neither_hosting_nor_a_cdn():
    assert "shared hosting" not in RISK_NOTE
    assert "system or content delivery" in RISK_NOTE
    assert evaluate("RISKY_BLOCK_NOTE") == RISK_NOTE


def unblock_action(items, kind=None):
    return evaluate("groupUnblockAction(" + json.dumps(group_of(items, kind=kind)) + ")")


@NODE
@pytest.mark.parametrize("kind", ["system", "cdn"])
def test_a_partly_blocked_system_or_cdn_group_offers_to_unblock_the_blocked_names(kind):
    items = immo(blocked=False)
    items[1]["globally_blocked"] = True
    items[5]["globally_blocked"] = True

    action = unblock_action(items, kind=kind)

    assert action == {
        "label": "Unblock blocked names",
        "path": REMOVE_BLOCK,
        "domains": [IMMO_NAMES[1][0], IMMO_NAMES[5][0]],
        "disabled": False,
        "ariaLabel": "Unblock blocked names: 2 ImmoScout24 names",
        "title": "Unblock blocked names: 2 ImmoScout24 names",
    }


@NODE
def test_the_undo_uses_the_singular_for_one_blocked_name():
    items = immo(blocked=False)
    items[2]["globally_blocked"] = True

    assert unblock_action(items, kind="system")["ariaLabel"] == (
        "Unblock blocked names: 1 ImmoScout24 name"
    )


@NODE
@pytest.mark.parametrize("kind", ["app", "iot", "tracker", "unknown", None])
def test_other_kinds_get_no_undo_because_block_remaining_works_for_them(kind):
    items = immo(blocked=False)
    items[1]["globally_blocked"] = True

    assert unblock_action(items, kind=kind) is None


@NODE
def test_a_risky_group_with_nothing_blocked_or_everything_blocked_gets_no_undo():
    assert unblock_action(immo(blocked=False), kind="system") is None
    assert unblock_action(immo(blocked=True), kind="system") is None


@NODE
def test_the_unblock_messages_name_the_group_and_the_count():
    items = immo(blocked=False)
    items[1]["globally_blocked"] = True
    items[2]["globally_blocked"] = True
    group = json.dumps(group_of(items, kind="cdn"))

    messages = evaluate(
        "groupBlockMessages(%s, groupUnblockAction(%s))" % (group, group)
    )

    assert messages == {
        "busy": "Removing 2 ImmoScout24 names from the global block list...",
        "done": "Removed 2 ImmoScout24 names from the global block list. " + ENFORCE,
    }


@NODE
def test_the_profile_button_stays_for_system_and_cdn_groups():
    for kind in ("system", "cdn"):
        group = json.dumps(group_of(immo(), kind=kind))
        action = evaluate("groupProfileAction(%s, %s)" % (group, json.dumps(SLOW_PROFILE)))
        assert action["disabled"] is False
        assert len(action["domains"]) == 7


@NODE
def test_a_group_takes_the_kind_of_its_names():
    domains = kinded(immo(), "app") + kinded(netflix(), "system")

    groups = evaluate("groupObservations(" + json.dumps(domains) + ")")

    assert {g["name"]: g["kind"] for g in groups} == {"ImmoScout24": "app", "Netflix": "system"}


@NODE
def test_a_missing_group_kind_is_an_empty_kind():
    groups = evaluate("groupObservations(" + json.dumps(immo()) + ")")

    assert groups[0]["kind"] == ""


@NODE
def test_a_risky_name_makes_the_whole_group_risky_whatever_came_first():
    items = kinded(immo(), "tracker")
    items[3]["group"]["kind"] = "cdn"

    groups = evaluate("groupObservations(" + json.dumps(items) + ")")
    action = evaluate("groupBlockAction(" + json.dumps(groups[0]) + ")")

    assert groups[0]["kind"] == "cdn"
    assert action["disabled"] is True


SLOW = {"id": "slow", "name": "Slow", "website_domains": ["sso.immobilienscout24.de", "a.test"]}


def profile_action(items, profile):
    return evaluate(
        "groupProfileAction(" + json.dumps(group_of(items)) + ", " + json.dumps(profile) + ")"
    )


@NODE
def test_no_profile_disables_add_all():
    expected = {
        "label": "Add all to profile",
        "disabled": True,
        "domains": [],
        "ariaLabel": "Add all to profile: 7 ImmoScout24 names (no profile available)",
        "title": "Add all to profile: 7 ImmoScout24 names (no profile available)",
    }

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
        "ariaLabel": "All already in Slow: 7 ImmoScout24 names",
        "title": "All already in Slow: 7 ImmoScout24 names",
    }


@NODE
def test_missing_names_are_offered_and_the_ones_already_in_are_left_out():
    action = profile_action(immo(), SLOW)

    assert action["label"] == "Add all to profile"
    assert action["disabled"] is False
    assert action["domains"] == [d for d, _ in IMMO_NAMES if d != "sso.immobilienscout24.de"]
    assert action["ariaLabel"] == "Add all to profile: 6 ImmoScout24 names to Slow"


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
def test_a_group_over_the_call_limit_has_a_disabled_profile_action_with_the_note():
    many = [item("h%d.example.com" % i, 1) for i in range(MAX_BATCH_DOMAINS + 1)]

    action = profile_action(many, {"id": "p", "name": "P", "website_domains": []})

    assert action["disabled"] is True
    assert len(action["domains"]) == 65
    assert "65" in action["title"] and str(MAX_BATCH_DOMAINS) in action["title"]


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


@NODE
def test_the_group_dom_id_is_unique_per_device_and_group_and_holds_no_spaces():
    keys = [
        ("192.168.50.124", "label:x (twitter)"),
        ("192.168.50.124", "label:x_28_twitter_29_"),
        ("192.168.50.124", "label:netflix"),
        ("192.168.50.125", "label:netflix"),
        ("192.168.50.1", "24|label:netflix"),
        ("192.168.50.12", "4|label:netflix"),
    ]
    expression = "[%s]" % ", ".join(
        "observationGroupDomId(%s, %s)" % (json.dumps(a), json.dumps(k)) for a, k in keys
    )

    ids = evaluate(expression)

    assert len(set(ids)) == len(keys)
    assert all(re.fullmatch(r"observation-group-[A-Za-z0-9_-]+", value) for value in ids)


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
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; }
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
const collapsedObservationDevices = new Set();
const observationProfileChoice = new Map();
const calls = [];
const confirmations = [];
let confirmAnswer = true;
let confirmFailure = null;
let requestResult = {};
let requestFails = false;
let requestFailure = "the helper is busy";
function confirmWorkflow(message) {
  confirmations.push(message);
  return confirmFailure ? Promise.reject(new Error(confirmFailure)) : Promise.resolve(confirmAnswer);
}
function request(path, method, body) {
  calls.push(["request", path, method, body]);
  return requestFails ? Promise.reject(new Error(requestFailure)) : Promise.resolve(requestResult);
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
  const toggle = byClass(item, "observation-group-toggle")[0];
  const body = byClass(item, "observation-group-body")[0];
  return {
    item, toggle, body,
    header: byClass(item, "observation-group-header")[0],
    name: byClass(item, "observation-group-name")[0],
    count: byClass(item, "observation-group-count")[0],
    queries: byClass(toggle, "observation-domain-count")[0],
    badge: byClass(toggle, "observation-badge")[0],
    profileButton: byClass(item, "observation-group-profile-button")[0],
    blockButton: byClass(item, "observation-group-block-button")[0],
    unblockButton: byClass(item, "observation-group-unblock-button")[0],
    note: byClass(item, "observation-group-note")[0],
    rows: byClass(body, "observation-domain"),
    isOpen: () => toggle.attrs["aria-expanded"] === "true" && body.hidden === false
  };
}
function describeTop(card) {
  return topList(card).children.map((item, index) => {
    if (byClass(item, "observation-group-toggle").length === 0) {
      return { kind: "row", host: byClass(item, "observation-domain-host")[0].children[0].textContent };
    }
    const parts = groupParts(card, index);
    return {
      kind: "group", name: parts.name.textContent, names: parts.count.textContent,
      queries: parts.queries.textContent, open: parts.isOpen(), rows: parts.rows.length
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


# These profiles already allow only listed sites, so adding to them asks nothing first.
SLOW_PROFILE = {"id": "slow", "name": "Slow", "website_domains": [], "website_mode": "allow_only"}
FAST_PROFILE = {"id": "fast", "name": "Fast", "website_domains": [], "website_mode": "allow_only"}
# An open profile. Adding the first name would turn it into an allow list.
OPEN_PROFILE = {
    "id": "open", "name": "Open", "website_domains": [], "website_mode": "unrestricted"}


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
        tag: item.tagName, cls: item.className, toggles: byClass(item, "observation-group-toggle").length,
        children: item.children.map((child) => child.tagName + "." + child.className),
        buttons: findAll(item, (n) => n.tagName === "button").map((b) => b.textContent)
      }));
    """
    grammarly = item("config.extension.grammarly.com", 5, "label:grammarly", "Grammarly")
    single = device("192.168.50.124", with_labels([grammarly]))

    out = run_dashboard(steps, [single])

    assert out["tag"] == "li"
    assert out["cls"] == "observation-domain"
    assert out["toggles"] == 0
    assert out["children"] == [
        "div.observation-domain-name", "span.observation-domain-count",
        "button.observation-profile-button", "button.",
    ]
    assert out["buttons"] == ["Add to profile", "Block globally"]


@NODE
def test_the_toggle_opens_and_closes_a_group_and_an_open_group_survives_a_render():
    steps = """
      await click(groupParts(cards()[0], 1).toggle);
      const justOpened = describeTop(cards()[0]).map((entry) => entry.open);
      const keysOpen = Array.from(openObservationGroups);
      render();
      const afterOpen = describeTop(cards()[0]).map((entry) => entry.open);
      await click(groupParts(cards()[0], 1).toggle);
      const justClosed = describeTop(cards()[0]).map((entry) => entry.open);
      render();
      const afterClose = describeTop(cards()[0]).map((entry) => entry.open);
      console.log(JSON.stringify({
        justOpened, keysOpen, afterOpen, justClosed, afterClose,
        keys: Array.from(openObservationGroups)
      }));
    """

    out = run_dashboard(steps, [example_device()])

    assert out["justOpened"] == [False, True, None, None, None]
    assert out["keysOpen"] == ["192.168.50.124|label:immoscout24"]
    assert out["afterOpen"] == [False, True, None, None, None]
    assert out["justClosed"] == [False, False, None, None, None]
    assert out["afterClose"] == [False, False, None, None, None]
    assert out["keys"] == []


@NODE
def test_the_open_state_belongs_to_one_group_on_one_device():
    steps = """
      await click(groupParts(cards()[0], 1).toggle);
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
      await click(before.blockButton);
      const after = groupParts(cards()[0], 1);
      const afterBlock = {
        header: before.blockButton.textContent, badge: before.badge.textContent,
        badgeHidden: before.badge.hidden, rows: before.rows.map((row) => row.children[3].textContent)
      };
      await click(before.blockButton);
      console.log(JSON.stringify({
        labelBefore, afterBlock, calls,
        last: {header: before.blockButton.textContent, badgeHidden: before.badge.hidden,
               rows: before.rows.map((row) => row.children[3].textContent)}
      }));
    """
    names = [d for d, _ in IMMO_NAMES]

    out = run_dashboard(steps, [example_device()])

    assert out["labelBefore"] == "Block all"
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
    assert out["last"] == {
        "header": "Block all",
        "badgeHidden": True,
        "rows": ["Block globally"] * 7,
    }


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
      await click(parts.profileButton);
      console.log(JSON.stringify({
        labelBefore, calls, select: select.value,
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
    assert out["select"] == "slow"
    assert out["calls"] == [
        ["message", "Adding 7 ImmoScout24 names to the profile...", "working"],
        ["request", "/api/observations/add-to-profile", "POST",
         {"domains": names, "profile_id": "slow", "confirm_allow_only": True}],
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

    assert out == {
        "before": "All already in Slow",
        "after": "Add all to profile",
        "disabled": False,
    }


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


@NODE
def test_a_group_is_a_disclosure_button_and_its_actions_are_siblings_of_it():
    steps = """
      const first = groupParts(cards()[0], 0);
      const second = groupParts(cards()[0], 1);
      const other = groupParts(cards()[1], 0);
      const buttonsInside = (node) => findAll(node, (n) => n.tagName === "button" && n !== node);
      const parentOf = (target) => findAll(elements.observations, (n) => n.children.indexOf(target) !== -1)[0];
      const describe = (parts) => ({
        toggleTag: parts.toggle.tagName, toggleType: parts.toggle.type,
        expanded: parts.toggle.attrs["aria-expanded"], controls: parts.toggle.attrs["aria-controls"],
        bodyId: parts.body.id, bodyHidden: parts.body.hidden, bodyTag: parts.body.tagName,
        actionTags: [parts.profileButton.tagName, parts.blockButton.tagName],
        actionTypes: [parts.profileButton.type, parts.blockButton.type],
        buttonsInsideToggle: buttonsInside(parts.toggle).length,
        sameRow: parentOf(parts.toggle) === parts.header && parentOf(parts.profileButton) === parts.header
          && parentOf(parts.blockButton) === parts.header,
        bodyOutsideHeader: findAll(parts.header, (n) => n === parts.body).length === 0,
        chevronHidden: byClass(parts.toggle, "observation-group-chevron")[0].attrs["aria-hidden"],
        nameInsideToggle: byClass(parts.toggle, "observation-group-name").length,
        countInsideToggle: byClass(parts.toggle, "observation-group-count").length,
        domainsInsideBody: parts.rows.length
      });
      console.log(JSON.stringify({
        first: describe(first), second: describe(second),
        ids: [first.body.id, second.body.id, other.body.id]
      }));
    """

    out = run_dashboard(steps, [example_device("192.168.50.124"), example_device("192.168.50.125")])

    for key, rows in (("first", 2), ("second", 7)):
        part = out[key]
        assert part["toggleTag"] == "button"
        assert part["toggleType"] == "button"
        assert part["expanded"] == "false"
        assert part["controls"] == part["bodyId"] != ""
        assert part["bodyHidden"] is True
        assert part["bodyTag"] == "div"
        assert part["actionTags"] == ["button", "button"]
        assert part["actionTypes"] == ["button", "button"]
        assert part["buttonsInsideToggle"] == 0
        assert part["sameRow"] is True
        assert part["bodyOutsideHeader"] is True
        assert part["chevronHidden"] == "true"
        assert part["nameInsideToggle"] == 1
        assert part["countInsideToggle"] == 1
        assert part["domainsInsideBody"] == rows
    assert len(set(out["ids"])) == 3
    assert all(" " not in value for value in out["ids"])


@NODE
def test_clicking_an_action_button_does_not_open_or_close_the_group():
    steps = """
      const parts = groupParts(cards()[0], 1);
      const closed = parts.isOpen();
      await click(parts.blockButton);
      await click(parts.profileButton);
      const stillClosed = parts.isOpen();
      await click(parts.toggle);
      const opened = parts.isOpen();
      await click(parts.blockButton);
      const stillOpen = parts.isOpen();
      console.log(JSON.stringify({
        closed, stillClosed, opened, stillOpen,
        requests: calls.filter((call) => call[0] === "request").length
      }));
    """

    out = run_dashboard(steps, [example_device()])

    assert out == {"closed": False, "stillClosed": False, "opened": True,
                   "stillOpen": True, "requests": 3}


@NODE
def test_the_toggle_sends_no_request_and_needs_no_key_handlers():
    steps = """
      const parts = groupParts(cards()[0], 1);
      await click(parts.toggle);
      await click(parts.toggle);
      console.log(JSON.stringify({
        calls, toggleHandlers: (parts.toggle.listeners.click || []).length,
        keyboardHandlers: Object.keys(parts.toggle.listeners).filter((name) => name.startsWith("key"))
      }));
    """

    out = run_dashboard(steps, [example_device()])

    assert out["calls"] == []
    assert out["toggleHandlers"] == 1
    # Enter and Space on a native button fire a click so nothing extra is needed.
    assert out["keyboardHandlers"] == []


@NODE
def test_the_action_buttons_are_named_by_the_group_and_the_count():
    steps = """
      const parts = groupParts(cards()[0], 1);
      const names = () => [parts.profileButton.attrs["aria-label"], parts.blockButton.attrs["aria-label"]];
      const start = names();
      requestResult = {profile: {id: "slow", name: "Slow", website_domains: ALL}};
      await click(parts.profileButton);
      await click(parts.blockButton);
      console.log(JSON.stringify({start, after: names()}));
    """
    names = [d for d, _ in IMMO_NAMES]
    steps = "const ALL = %s;\n" % json.dumps(names) + steps

    out = run_dashboard(steps, [example_device()])

    assert out["start"] == [
        "Add all to profile: 7 ImmoScout24 names to Slow",
        "Block all 7 ImmoScout24 names",
    ]
    assert out["after"] == [
        "All already in Slow: 7 ImmoScout24 names",
        "Unblock all 7 ImmoScout24 names",
    ]


def test_the_open_state_is_a_module_level_set_updated_when_the_toggle_is_clicked():
    assert re.search(r"\n    const openObservationGroups = new Set\(\);", SCRIPT)
    body = function_body("renderObservations")
    assert "openObservationGroups.has(" in body
    assert "openObservationGroups.add(" in body
    assert "openObservationGroups.delete(" in body
    assert "observationGroupOpenKey(device.ipv4_address, group.key)" in body


def test_a_group_is_built_without_details_and_summary_and_without_innerhtml():
    body = function_body("renderObservations")
    assert 'createElement("details")' not in body
    assert 'createElement("summary")' not in body
    assert "innerHTML" not in body
    assert "stopPropagation" not in body
    assert "groupObservations(device.domains" in body
    assert "group.single" in body
    assert '"aria-expanded"' in body and '"aria-controls"' in body


def test_the_group_row_has_styles_for_the_header_the_toggle_and_the_body():
    for selector in (
        ".observation-group {",
        ".observation-group-header {",
        ".observation-group-toggle {",
        ".observation-group-body {",
        ".observation-group-chevron {",
        ".observation-group-note {",
        '.observation-group-toggle[aria-expanded="true"] .observation-group-chevron',
    ):
        assert selector in DASHBOARD_HTML, selector
    assert ".observation-group-body[hidden]" in DASHBOARD_HTML


@NODE
def test_a_system_group_has_a_disabled_block_all_but_keeps_its_other_buttons():
    steps = """
      const parts = groupParts(cards()[0], 0);
      await click(parts.blockButton);
      console.log(JSON.stringify({
        block: [parts.blockButton.textContent, parts.blockButton.disabled, parts.blockButton.title],
        profile: [parts.profileButton.textContent, parts.profileButton.disabled],
        rowBlock: parts.rows.map((row) => [row.children[3].textContent, row.children[3].disabled]),
        rowProfile: parts.rows.map((row) => row.children[2].disabled),
        calls
      }));
    """
    google = with_labels(
        [item("n%d.google.com" % i, 1, "label:google", "Google", None, "system") for i in range(3)]
    )

    out = run_dashboard(steps, [device("192.168.50.124", google)])

    assert out["block"] == ["Block all", True, RISK_NOTE]
    assert out["profile"] == ["Add all to profile", False]
    assert out["rowBlock"] == [["Block globally", False]] * 3
    assert out["rowProfile"] == [False] * 3
    assert out["calls"] == []


@NODE
def test_a_group_of_apps_still_blocks_all_in_one_call():
    steps = """
      const parts = groupParts(cards()[0], 0);
      await click(parts.blockButton);
      console.log(JSON.stringify({header: parts.blockButton.textContent, calls}));
    """
    items = with_labels(kinded(immo(), "app"))

    out = run_dashboard(steps, [device("192.168.50.124", items)])

    assert out["calls"][1][:3] == ["request", ADD_BLOCK, "POST"]
    assert out["header"] == "Unblock all"


@NODE
def test_the_server_text_of_a_full_list_reaches_the_message_unchanged():
    steps = """
      requestFails = true;
      requestFailure = "The global block list can hold 100 names. Remove some first.";
      const parts = groupParts(cards()[0], 1);
      await click(parts.blockButton);
      requestFailure = "A profile can list 100 websites. Remove some first.";
      await click(parts.profileButton);
      console.log(JSON.stringify({calls}));
    """

    out = run_dashboard(steps, [example_device()])

    messages = [call[1:] for call in out["calls"] if call[0] == "message" and call[2] == "error"]
    assert messages == [
        ["The global block list can hold 100 names. Remove some first.", "error"],
        ["A profile can list 100 websites. Remove some first.", "error"],
    ]


def labelled(domain, count, key, group_name, label_name, kind="app", category="Cat"):
    value = item(domain, count, key, group_name, None, kind)
    value["label"] = {"kind": kind, "name": label_name, "category": category, "known": True}
    value["seen_with"] = []
    return value


LABEL_STEPS = """
  const parts = groupParts(cards()[0], 0);
  console.log(JSON.stringify({
    labelLines: parts.rows.map((row) => byClass(row, "observation-label").length),
    hosts: parts.rows.map((row) => byClass(row, "observation-domain-host")[0].children[0].textContent),
    firstLabel: parts.rows.map((row) => byClass(row, "observation-label").map((l) => l.children[0].textContent))
  }));
"""


@NODE
def test_rows_whose_label_is_the_group_name_skip_the_label_line_inside_the_group():
    items = [
        labelled("a.immobilienscout24.de", 3, "label:immoscout24", "ImmoScout24", "ImmoScout24"),
        labelled("b.immobilienscout24.de", 2, "label:immoscout24", "ImmoScout24", "ImmoScout24"),
    ]

    out = run_dashboard(LABEL_STEPS, [device("192.168.50.124", items)])

    assert out["labelLines"] == [0, 0]
    assert out["hosts"] == ["a.immobilienscout24.de", "b.immobilienscout24.de"]


@NODE
def test_rows_with_a_different_label_keep_it_inside_a_registrable_domain_group():
    items = [
        labelled("a.tiqcdn.com", 3, "domain:tiqcdn.com", "tiqcdn.com", "Tealium", "tracker"),
        labelled("b.tiqcdn.com", 2, "domain:tiqcdn.com", "tiqcdn.com", "tiqcdn.com", "unknown"),
        labelled("c.tiqcdn.com", 1, "domain:tiqcdn.com", "tiqcdn.com", "Tealium IQ", "tracker"),
    ]

    out = run_dashboard(LABEL_STEPS, [device("192.168.50.124", items)])

    assert out["labelLines"] == [1, 0, 1]
    assert out["firstLabel"] == [["Tealium"], [], ["Tealium IQ"]]


@NODE
def test_a_group_name_that_only_differs_in_case_keeps_the_label_line():
    items = [
        labelled("a.example.com", 3, "label:x", "X (Twitter)", "x (twitter)"),
        labelled("b.example.com", 2, "label:x", "X (Twitter)", "X (Twitter)"),
    ]

    out = run_dashboard(LABEL_STEPS, [device("192.168.50.124", items)])

    assert out["labelLines"] == [1, 0]


CATEGORY_STEPS = """
  const parts = groupParts(cards()[0], 0);
  console.log(JSON.stringify(parts.rows.map((row) =>
    byClass(row, "observation-label").map((line) => line.children.map((node) =>
      node.tagName + "." + node.className + ":" + node.textContent)))));
"""


@NODE
def test_a_row_that_repeats_the_group_name_keeps_a_category_that_differs_from_the_others():
    items = [
        labelled("a.msftconnecttest.com", 3, "label:windows", "Windows", "Windows", "system",
                 "Connectivity check"),
        labelled("b.windows.com", 2, "label:windows", "Windows", "Windows", "system",
                 "Windows services"),
    ]

    out = run_dashboard(CATEGORY_STEPS, [device("192.168.50.124", items)])

    assert out == [
        [["span.observation-category:Connectivity check"]],
        [["span.observation-category:Windows services"]],
    ]


@NODE
def test_a_group_with_one_category_for_every_row_has_no_label_line_at_all():
    items = [
        labelled("a.immobilienscout24.de", 3, "label:immoscout24", "ImmoScout24", "ImmoScout24",
                 "app", "Real estate"),
        labelled("b.immobilienscout24.de", 2, "label:immoscout24", "ImmoScout24", "ImmoScout24",
                 "app", "Real estate"),
    ]

    out = run_dashboard(CATEGORY_STEPS, [device("192.168.50.124", items)])

    assert out == [[], []]


@NODE
def test_a_row_with_another_name_keeps_both_the_name_and_the_category():
    items = [
        labelled("a.tiqcdn.com", 3, "domain:tiqcdn.com", "tiqcdn.com", "Tealium", "tracker",
                 "Tag manager"),
        labelled("b.tiqcdn.com", 2, "domain:tiqcdn.com", "tiqcdn.com", "tiqcdn.com", "unknown",
                 "Tag manager"),
    ]

    out = run_dashboard(CATEGORY_STEPS, [device("192.168.50.124", items)])

    assert out == [
        [["strong.:Tealium", "span.observation-category:Tag manager"]],
        [],
    ]


@NODE
def test_the_category_line_of_a_repeated_name_has_no_name_in_it_when_categories_differ():
    items = [
        labelled("a.apple.com", 3, "label:apple", "Apple", "Apple", "system", "Apple services"),
        labelled("b.mzstatic.com", 2, "label:apple", "Apple", "Apple", "system", "App Store media"),
        labelled("c.aaplimg.com", 1, "label:apple", "Apple", "Apple", "system", "Apple downloads"),
    ]

    out = run_dashboard(CATEGORY_STEPS, [device("192.168.50.124", items)])

    assert [len(lines) for lines in out] == [1, 1, 1]
    assert all(len(lines[0]) == 1 and lines[0][0].startswith("span.") for lines in out)


@NODE
def test_every_action_name_starts_with_the_text_the_button_shows():
    # WCAG 2.5.3 label in name. A voice user says the visible text so the name must hold it.
    profiles = [None, SLOW, {"id": "p", "name": "P", "website_domains": []},
                {"id": "q", "name": "Q", "website_domains": [d for d, _ in IMMO_NAMES]}]
    for kind in (None, "app", "system", "cdn"):
        for flags in ((False,) * 7, (True,) * 7, (True, False) + (False,) * 5):
            items = immo()
            for value, flag in zip(items, flags):
                value["globally_blocked"] = flag
            group = json.dumps(group_of(items, kind=kind))
            for expression in ["groupBlockAction(%s)" % group, "groupUnblockAction(%s)" % group]:
                action = evaluate(expression)
                if action is not None:
                    assert action["ariaLabel"].startswith(action["label"]), action
            for profile in profiles:
                action = evaluate("groupProfileAction(%s, %s)" % (group, json.dumps(profile)))
                assert action["ariaLabel"].startswith(action["label"]), action


@NODE
def test_every_rendered_group_button_has_a_name_that_starts_with_its_visible_text():
    steps = """
      const parts = groupParts(cards()[0], 0);
      const names = () => [parts.profileButton, parts.blockButton, parts.unblockButton]
        .filter((button) => !button.hidden)
        .map((button) => [button.textContent, button.attrs["aria-label"]]);
      const result = [names()];
      await click(parts.blockButton);
      result.push(names());
      await click(parts.profileButton);
      result.push(names());
      console.log(JSON.stringify(result));
    """
    app_items = with_labels(kinded(immo(), "app"))

    out = run_dashboard(steps, [device("192.168.50.124", app_items)])

    assert len(out) == 3
    for states in out:
        for visible, name in states:
            assert name.startswith(visible), (visible, name)


def label_parts(parts, group_name, uniform):
    return evaluate("rowLabelParts(%s, %s, %s)" % (
        json.dumps(parts), json.dumps(group_name), json.dumps(uniform)))


@NODE
def test_the_label_parts_of_a_row_follow_the_name_and_category_rules():
    parts = {"labelName": "Windows", "category": "Windows services"}

    # A single row has no group so it keeps everything.
    assert label_parts(parts, None, True) == {"showName": True, "showCategory": True}
    # Same name and one category for the group: nothing to show.
    assert label_parts(parts, "Windows", True) == {"showName": False, "showCategory": False}
    # Same name and categories differ: only the category.
    assert label_parts(parts, "Windows", False) == {"showName": False, "showCategory": True}
    # Another name keeps both whatever the categories are.
    assert label_parts(parts, "Google", True) == {"showName": True, "showCategory": True}
    assert label_parts(parts, "Google", False) == {"showName": True, "showCategory": True}
    # No label at all shows nothing.
    assert label_parts({"labelName": "", "category": ""}, "Windows", False) == {
        "showName": False, "showCategory": False}


@NODE
def test_a_group_has_one_category_when_every_row_has_the_same_one():
    def uniform(categories):
        items = [
            {"domain": "n%d.example.com" % i, "label": {"name": "X", "category": category}}
            for i, category in enumerate(categories)
        ]
        return evaluate("groupCategoriesUniform(" + json.dumps(group_of(items)) + ")")

    assert uniform(["A", "A", "A"]) is True
    assert uniform(["A", "B", "A"]) is False
    assert uniform(["A"]) is True
    assert uniform(["A", None]) is False


@NODE
def test_a_single_name_row_always_keeps_its_label_line():
    steps = """
      const item = topList(cards()[0]).children[0];
      console.log(JSON.stringify({labels: byClass(item, "observation-label").length}));
    """
    only_name = labelled("a.immobilienscout24.de", 3, "label:immoscout24", "ImmoScout24", "ImmoScout24")

    out = run_dashboard(steps, [device("192.168.50.124", [only_name])])

    assert out == {"labels": 1}


def system_device(blocked_flags=(False, False, False)):
    names = [
        item("n%d.google.com" % i, 1, "label:google", "Google", flag, "system")
        for i, flag in enumerate(blocked_flags)
    ]
    return device("192.168.50.124", with_labels(names))


@NODE
def test_a_risky_group_shows_the_reason_in_a_visible_note_that_the_disabled_button_points_at():
    steps = """
      const parts = groupParts(cards()[0], 0);
      console.log(JSON.stringify({
        text: parts.note.textContent, tag: parts.note.tagName, hidden: parts.note.hidden,
        id: parts.note.id, describedBy: parts.blockButton.attrs["aria-describedby"] || null,
        disabled: parts.blockButton.disabled,
        profileDescribedBy: parts.profileButton.attrs["aria-describedby"] || null,
        bodyId: parts.body.id,
        order: Array.from(parts.item.children[0].children).map((node) => node.className)
      }));
    """

    out = run_dashboard(steps, [system_device()])

    assert out["text"] == RISK_NOTE
    assert out["tag"] == "p"
    assert out["hidden"] is False
    assert out["id"] != "" and out["describedBy"] == out["id"]
    assert out["disabled"] is True
    assert out["profileDescribedBy"] is None
    assert out["id"] != out["bodyId"]


@NODE
def test_a_group_that_is_not_risky_has_a_hidden_note_and_no_describedby():
    steps = """
      const parts = groupParts(cards()[0], 0);
      console.log(JSON.stringify({
        hidden: parts.note.hidden, text: parts.note.textContent,
        describedBy: parts.blockButton.attrs["aria-describedby"] || null
      }));
    """
    items = with_labels(kinded(immo(), "app"))

    out = run_dashboard(steps, [device("192.168.50.124", items)])

    assert out == {"hidden": True, "text": "", "describedBy": None}


@NODE
def test_the_note_is_part_of_the_group_under_the_header_and_not_inside_a_button():
    steps = """
      const parts = groupParts(cards()[0], 0);
      const wrapper = parts.item.children[0];
      console.log(JSON.stringify({
        order: wrapper.children.map((node) => node.className),
        insideButton: findAll(parts.toggle, (n) => n === parts.note).length
      }));
    """

    out = run_dashboard(steps, [system_device()])

    assert out["order"] == [
        "observation-group-header", "observation-group-note", "observation-group-body"
    ]
    assert out["insideButton"] == 0


@NODE
def test_the_header_buttons_come_in_tab_order_toggle_then_profile_then_block_then_undo():
    steps = """
      const parts = groupParts(cards()[0], 0);
      console.log(JSON.stringify(parts.header.children.map((node) => node.className)));
    """

    out = run_dashboard(steps, [system_device()])

    assert out == [
        "observation-group-toggle",
        "observation-group-profile-button",
        "observation-group-block-button",
        "observation-group-unblock-button",
    ]


@NODE
def test_a_partly_blocked_system_group_shows_an_undo_next_to_the_disabled_block_remaining():
    steps = """
      const parts = groupParts(cards()[0], 0);
      const state = () => ({
        block: [parts.blockButton.textContent, parts.blockButton.disabled],
        undo: [parts.unblockButton.textContent, parts.unblockButton.hidden, parts.unblockButton.disabled,
               parts.unblockButton.attrs["aria-label"] || null],
        note: parts.note.hidden, badge: parts.badge.textContent
      });
      const before = state();
      await click(parts.unblockButton);
      console.log(JSON.stringify({
        before, after: state(), calls,
        rows: parts.rows.map((row) => row.children[3].textContent)
      }));
    """
    blocked = system_device((True, False, True))

    out = run_dashboard(steps, [blocked])

    assert out["before"] == {
        "block": ["Block remaining", True],
        "undo": ["Unblock blocked names", False, False, "Unblock blocked names: 2 Google names"],
        "note": False,
        "badge": "2 blocked",
    }
    assert out["calls"] == [
        ["message", "Removing 2 Google names from the global block list...", "working"],
        ["request", REMOVE_BLOCK, "POST", {"domains": ["n0.google.com", "n2.google.com"]}],
        ["success", "Removed 2 Google names from the global block list. " + ENFORCE],
    ]
    assert out["after"]["block"] == ["Block all", True]
    assert out["after"]["undo"][1] is True
    assert out["after"]["badge"] == ""
    assert out["rows"] == ["Block globally"] * 3


@NODE
def test_the_undo_is_hidden_when_nothing_is_blocked_and_when_everything_is():
    steps = """
      const parts = groupParts(cards()[0], 0);
      console.log(JSON.stringify([parts.unblockButton.hidden, parts.blockButton.textContent]));
    """

    assert run_dashboard(steps, [system_device((False, False, False))]) == [True, "Block all"]
    assert run_dashboard(steps, [system_device((True, True, True))]) == [True, "Unblock all"]


@NODE
def test_a_failed_undo_keeps_the_state_and_shows_the_error():
    steps = """
      requestFails = true;
      const parts = groupParts(cards()[0], 0);
      await click(parts.unblockButton);
      console.log(JSON.stringify({
        last: calls[calls.length - 1], undoHidden: parts.unblockButton.hidden,
        undoDisabled: parts.unblockButton.disabled, badge: parts.badge.textContent
      }));
    """

    out = run_dashboard(steps, [system_device((True, False, False))])

    assert out == {
        "last": ["message", "the helper is busy", "error"],
        "undoHidden": False,
        "undoDisabled": False,
        "badge": "1 blocked",
    }


# ---------------------------------------------------------------------------------------------
# Each card keeps the profile that was picked in its select across dashboard refreshes.
# ---------------------------------------------------------------------------------------------

CHOICE_PROFILES = [{"id": "slow"}, {"id": "fast"}, {"id": "mid"}]


def chosen(remembered, assigned, profiles=None):
    """Call chosenObservationProfile with a map holding the remembered id for device 'd'."""

    entries = [] if remembered is None else [["d", remembered]]
    expression = "chosenObservationProfile(new Map(%s), 'd', %s, %s)" % (
        json.dumps(entries),
        json.dumps(CHOICE_PROFILES if profiles is None else profiles),
        json.dumps(assigned),
    )
    return evaluate(expression)


@NODE
def test_a_remembered_profile_that_still_exists_wins_over_the_assigned_one():
    assert chosen("mid", "slow") == "mid"


@NODE
def test_a_remembered_profile_that_was_deleted_falls_back_to_the_assigned_one():
    assert chosen("gone", "fast") == "fast"


@NODE
def test_a_deleted_remembered_profile_and_a_missing_assigned_one_fall_back_to_the_first():
    assert chosen("gone", "also-gone") == "slow"
    assert chosen("gone", None) == "slow"


@NODE
def test_without_a_memory_the_assigned_profile_is_used():
    assert chosen(None, "fast") == "fast"


@NODE
def test_an_assigned_profile_that_no_longer_exists_uses_the_first_profile():
    assert chosen(None, "gone") == "slow"
    assert chosen(None, None) == "slow"
    assert chosen(None, "") == "slow"


@NODE
def test_no_profiles_give_an_empty_choice_whatever_was_remembered():
    assert chosen(None, "slow", profiles=[]) == ""
    assert chosen("slow", "slow", profiles=[]) == ""


@NODE
def test_the_choice_is_per_device():
    expression = (
        "[chosenObservationProfile(new Map([['a', 'mid']]), 'a', %s, 'slow'),"
        " chosenObservationProfile(new Map([['a', 'mid']]), 'b', %s, 'slow')]"
    ) % (json.dumps(CHOICE_PROFILES), json.dumps(CHOICE_PROFILES))

    assert evaluate(expression) == ["mid", "slow"]


@NODE
def test_a_picked_profile_stays_picked_when_the_dashboard_renders_again():
    steps = """
      const select = (index) => byClass(cards()[index], "observation-actions")[0].children[0];
      const start = [select(0).value, select(1).value];
      select(0).value = "mid";
      fire(select(0), "change");
      render();
      const afterRender = [select(0).value, select(1).value];
      profiles = profiles.filter((profile) => profile.id !== "mid");
      render();
      const afterDelete = [select(0).value, select(1).value];
      console.log(JSON.stringify({start, afterRender, afterDelete}));
    """
    profiles = [SLOW_PROFILE, FAST_PROFILE, {"id": "mid", "name": "Mid", "website_domains": []}]
    devices = [
        example_device("192.168.50.124"),
        example_device("192.168.50.125"),
    ]
    devices[0]["assigned_profile_id"] = "fast"
    devices[1]["assigned_profile_id"] = "slow"

    out = run_dashboard(steps, devices, profiles=profiles)

    assert out == {
        "start": ["fast", "slow"],
        "afterRender": ["mid", "slow"],
        "afterDelete": ["fast", "slow"],
    }


@NODE
def test_the_header_buttons_follow_the_remembered_profile_after_a_render():
    steps = """
      const select = () => byClass(cards()[0], "observation-actions")[0].children[0];
      select().value = "fast";
      fire(select(), "change");
      render();
      const parts = groupParts(cards()[0], 1);
      console.log(JSON.stringify({
        select: select().value, header: parts.profileButton.textContent,
        row: parts.rows[0].children[2].textContent
      }));
    """
    slow = {"id": "slow", "name": "Slow", "website_domains": [d for d, _ in IMMO_NAMES]}

    out = run_dashboard(steps, [example_device()], profiles=[slow, FAST_PROFILE])

    assert out == {"select": "fast", "header": "Add all to profile", "row": "Add to profile"}


CONFIRM_TEXT = (
    "turns the profile Open into an allow list. "
    "Every other site will stop working for devices on that profile. Continue?"
)


def open_device(items):
    return device("192.168.50.124", with_labels(items), assigned="open")


@NODE
def test_adding_one_name_to_an_open_profile_asks_first_and_declining_sends_nothing():
    steps = """
      confirmAnswer = false;
      const row = topList(cards()[0]).children[0];
      const button = row.children[2];
      await click(button);
      console.log(JSON.stringify({
        calls, confirmations, label: button.textContent, disabled: button.disabled,
        busy: button.getAttribute("aria-busy"), mode: profiles[0].website_mode,
        domains: profiles[0].website_domains
      }));
    """

    out = run_dashboard(
        steps, [open_device([item("a.test", 1)])], profiles=[OPEN_PROFILE, SLOW_PROFILE]
    )

    assert out["confirmations"] == ["Adding a.test " + CONFIRM_TEXT]
    assert out["calls"] == []
    assert (out["label"], out["disabled"], out["busy"]) == ("Add to profile", False, None)
    assert (out["mode"], out["domains"]) == ("unrestricted", [])


@NODE
def test_confirming_adds_the_name_with_the_same_request_and_later_adds_ask_nothing():
    steps = """
      const rows = topList(cards()[0]).children;
      requestResult = {profile: {
        id: "open", name: "Open", website_mode: "allow_only", website_domains: ["a.test"]
      }};
      await click(rows[0].children[2]);
      const afterFirst = {calls: calls.slice(), confirmations: confirmations.slice()};
      requestResult = {profile: {
        id: "open", name: "Open", website_mode: "allow_only", website_domains: ["a.test", "b.test"]
      }};
      await click(rows[1].children[2]);
      console.log(JSON.stringify({
        afterFirst, calls, confirmations, mode: profiles[0].website_mode,
        first: rows[0].children[2].textContent
      }));
    """

    out = run_dashboard(
        steps,
        [open_device([item("a.test", 2), item("b.test", 1)])],
        profiles=[OPEN_PROFILE, SLOW_PROFILE],
    )

    assert out["afterFirst"]["confirmations"] == ["Adding a.test " + CONFIRM_TEXT]
    assert out["afterFirst"]["calls"] == [
        ["message", "Adding a.test to the profile...", "working"],
        ["request", "/api/observations/add-to-profile", "POST",
         {"domain": "a.test", "profile_id": "open", "confirm_allow_only": True}],
        ["success", "Added a.test to the profile."],
    ]
    # The profile is an allow list now, so the second name is added without a question.
    assert out["confirmations"] == out["afterFirst"]["confirmations"]
    assert out["calls"][3:] == [
        ["message", "Adding b.test to the profile...", "working"],
        ["request", "/api/observations/add-to-profile", "POST",
         {"domain": "b.test", "profile_id": "open", "confirm_allow_only": False}],
        ["success", "Added b.test to the profile."],
    ]
    assert out["mode"] == "allow_only"
    assert out["first"] == "Already in Open"


@NODE
def test_an_allow_list_profile_is_never_asked_about():
    steps = """
      await click(topList(cards()[0]).children[0].children[2]);
      console.log(JSON.stringify({calls, confirmations}));
    """

    out = run_dashboard(
        steps, [device("192.168.50.124", with_labels([item("a.test", 1)]))],
        profiles=[SLOW_PROFILE, OPEN_PROFILE],
    )

    assert out["confirmations"] == []
    assert out["calls"][1] == [
        "request", "/api/observations/add-to-profile", "POST",
        {"domain": "a.test", "profile_id": "slow", "confirm_allow_only": False}]


@NODE
def test_the_card_select_decides_which_profile_the_question_names():
    steps = """
      const select = byClass(cards()[0], "observation-actions")[0].children[0];
      select.value = "open";
      fire(select, "change");
      await click(topList(cards()[0]).children[0].children[2]);
      console.log(JSON.stringify({calls, confirmations}));
    """

    out = run_dashboard(
        steps, [device("192.168.50.124", with_labels([item("a.test", 1)]))],
        profiles=[SLOW_PROFILE, OPEN_PROFILE],
    )

    assert out["confirmations"] == ["Adding a.test " + CONFIRM_TEXT]
    assert out["calls"][1][3] == {"domain": "a.test", "profile_id": "open", "confirm_allow_only": True}


@NODE
def test_add_all_to_an_open_profile_asks_first_and_declining_sends_nothing():
    steps = """
      confirmAnswer = false;
      const parts = groupParts(cards()[0], 1);
      await click(parts.profileButton);
      console.log(JSON.stringify({
        calls, confirmations, label: parts.profileButton.textContent,
        disabled: parts.profileButton.disabled, busy: parts.profileButton.getAttribute("aria-busy")
      }));
    """

    out = run_dashboard(
        steps,
        [open_device(immo() + netflix())],
        profiles=[OPEN_PROFILE, SLOW_PROFILE],
    )

    assert out["confirmations"] == ["Adding 7 ImmoScout24 names " + CONFIRM_TEXT]
    assert out["calls"] == []
    assert (out["label"], out["disabled"], out["busy"]) == ("Add all to profile", False, None)


@NODE
def test_confirming_add_all_sends_the_same_request_and_then_stops_asking():
    steps = """
      requestResult = {profile: {
        id: "open", name: "Open", website_mode: "allow_only", website_domains: ALL
      }};
      const ALL_PARTS = groupParts(cards()[0], 1);
      await click(ALL_PARTS.profileButton);
      const first = {calls: calls.slice(), confirmations: confirmations.slice()};
      await click(groupParts(cards()[0], 0).profileButton);
      console.log(JSON.stringify({first, confirmations, mode: profiles[0].website_mode}));
    """
    names = [d for d, _ in IMMO_NAMES]
    steps = "const ALL = %s;\n" % json.dumps(names) + steps

    out = run_dashboard(
        steps, [open_device(immo() + netflix())], profiles=[OPEN_PROFILE, SLOW_PROFILE]
    )

    assert out["first"]["confirmations"] == ["Adding 7 ImmoScout24 names " + CONFIRM_TEXT]
    assert out["first"]["calls"] == [
        ["message", "Adding 7 ImmoScout24 names to the profile...", "working"],
        ["request", "/api/observations/add-to-profile", "POST",
         {"domains": names, "profile_id": "open", "confirm_allow_only": True}],
        ["success", "Added 7 ImmoScout24 names to the profile."],
    ]
    # The profile came back as an allow list, so the Netflix group asks nothing.
    assert out["confirmations"] == out["first"]["confirmations"]
    assert out["mode"] == "allow_only"


@NODE
def test_a_dialog_that_cannot_open_sends_nothing_and_shows_why():
    steps = """
      confirmFailure = "This browser cannot show the question.";
      // The big group sorts first and the single name second.
      const row = topList(cards()[0]).children[1];
      await click(row.children[2]);
      await click(groupParts(cards()[0], 0).profileButton);
      console.log(JSON.stringify({calls, disabled: row.children[2].disabled}));
    """

    out = run_dashboard(
        steps,
        [open_device([item("a.test", 1)] + immo())],
        profiles=[OPEN_PROFILE],
    )

    assert out["calls"] == [
        ["message", "This browser cannot show the question.", "error"],
        ["message", "This browser cannot show the question.", "error"],
    ]
    assert out["disabled"] is False


def test_the_question_is_the_in_page_dialog_and_never_the_browser_one():
    for name in ("addDomainToProfile", "addGroupToProfile", "askAllowOnly"):
        body = function_body(name)
        assert "window.confirm" not in body and not re.search(r"(?<![\w.])confirm\(", body), name
    assert "await confirmWorkflow(allowOnlyConfirmText(" in function_body("askAllowOnly")


def test_the_choice_map_is_module_level_and_stored_when_the_select_changes():
    assert re.search(r"\n    const observationProfileChoice = new Map\(\);", SCRIPT)
    body = function_body("renderObservations")
    assert "chosenObservationProfile(" in body
    assert "observationProfileChoice.set(" in body


@NODE
@pytest.mark.parametrize("grouped", [False, True], ids=["single", "group"])
def test_stale_profile_conflict_waits_for_a_new_click_and_explicit_confirmation(grouped):
    steps = """
      const button = GROUPED ? groupParts(cards()[0], 0).profileButton
        : topList(cards()[0]).children[0].children[2];
      request = async (path, method, body) => {
        calls.push(["request", path, method, body]);
        const error = new Error("Nothing was added. Choose Add again to confirm restricting it.");
        error.status = 409;
        error.code = "allow_only_confirmation_required";
        error.profile = {id: "slow", name: "Changed", website_mode: "unrestricted", website_domains: []};
        throw error;
      };
      await click(button);
      const afterConflict = {calls: calls.slice(), confirmations: confirmations.slice(),
        mode: profiles[0].website_mode, disabled: button.disabled};
      confirmAnswer = false;
      await click(button);
      const afterDecline = {calls: calls.slice(), confirmations: confirmations.slice()};
      confirmAnswer = true;
      request = async (path, method, body) => {
        calls.push(["request", path, method, body]);
        return {profile: {id: "slow", name: "Changed", website_mode: "allow_only", website_domains: []}};
      };
      await click(button);
      console.log(JSON.stringify({afterConflict, afterDecline, calls, confirmations}));
    """
    steps = "const GROUPED = " + json.dumps(grouped) + ";\n" + steps
    items = immo() if grouped else [item("a.test", 1)]
    out = run_dashboard(steps, [device("192.168.50.124", with_labels(items))])

    first = out["afterConflict"]
    posts = [call for call in first["calls"] if call[0] == "request"]
    assert len(posts) == 1
    assert posts[0][2] == "POST"
    assert posts[0][3]["confirm_allow_only"] is False
    assert first["confirmations"] == []
    assert first["mode"] == "unrestricted"
    assert first["disabled"] is False
    assert out["afterDecline"]["calls"] == first["calls"]
    assert len(out["afterDecline"]["confirmations"]) == 1
    assert "turns the profile Changed into an allow list" in out["confirmations"][0]
    posts = [call for call in out["calls"] if call[0] == "request"]
    assert len(posts) == 2
    assert posts[1][3]["confirm_allow_only"] is True
    assert len(out["confirmations"]) == 2


@NODE
@pytest.mark.parametrize("grouped", [False, True], ids=["single", "group"])
@pytest.mark.parametrize("conflict", [False, True], ids=["network-error", "other-profile"])
def test_failed_profile_add_does_not_retry_or_replace_a_different_profile(grouped, conflict):
    steps = """
      request = async (path, method, body) => {
        calls.push(["request", path, method, body]);
        const error = new Error("The request failed.");
        if (CONFLICT) {
          error.status = 409;
          error.code = "allow_only_confirmation_required";
          error.profile = {id: "fast", name: "Other", website_mode: "unrestricted", website_domains: []};
        }
        throw error;
      };
      const button = GROUPED ? groupParts(cards()[0], 0).profileButton
        : topList(cards()[0]).children[0].children[2];
      await click(button);
      console.log(JSON.stringify({calls, confirmations, profiles, disabled: button.disabled}));
    """
    steps = "const GROUPED = %s; const CONFLICT = %s;\n" % (
        json.dumps(grouped), json.dumps(conflict)
    ) + steps
    items = immo() if grouped else [item("a.test", 1)]
    out = run_dashboard(steps, [device("192.168.50.124", with_labels(items))])

    assert len([call for call in out["calls"] if call[0] == "request"]) == 1
    assert out["confirmations"] == []
    assert out["profiles"] == [SLOW_PROFILE, FAST_PROFILE]
    assert out["disabled"] is False


@NODE
def test_device_names_can_be_collapsed_independently_and_stay_collapsed_after_refresh():
    steps = """
      function parts(index) {
        const card = cards()[index];
        return {toggle: byClass(card, "observation-device-toggle")[0],
                body: byClass(card, "observation-device-body")[0]};
      }
      const initial = [parts(0).body.hidden, parts(1).body.hidden];
      await click(groupParts(cards()[0], 1).toggle);
      await click(parts(0).toggle);
      const collapsed = [parts(0).body.hidden, parts(1).body.hidden];
      render();
      const refreshed = [parts(0).body.hidden, parts(1).body.hidden];
      const a = parts(0), b = parts(1);
      const semantics = {tag: a.toggle.tagName, type: a.toggle.type,
        expanded: a.toggle.attrs["aria-expanded"], controls: a.toggle.attrs["aria-controls"],
        bodyId: a.body.id, otherId: b.body.id, label: a.toggle.attrs["aria-label"],
        text: a.toggle.textContent};
      await click(a.toggle);
      console.log(JSON.stringify({initial, collapsed, refreshed, semantics,
        reopened: !a.body.hidden, groupStillOpen: groupParts(cards()[0], 1).isOpen(), calls}));
    """
    out = run_dashboard(steps, [example_device(), example_device("192.168.50.125")])
    assert out["initial"] == [False, False]
    assert out["collapsed"] == out["refreshed"] == [True, False]
    assert out["reopened"] and out["groupStillOpen"]
    assert out["calls"] == []
    semantics = out["semantics"]
    assert semantics["tag"] == "button" and semantics["type"] == "button"
    assert semantics["expanded"] == "false"
    assert semantics["controls"] == semantics["bodyId"] != semantics["otherId"]
    assert semantics["label"].startswith(semantics["text"])
    assert "192.168.50.124" in semantics["label"]


@NODE
def test_collapsed_device_keeps_profile_choice_and_updates_the_visible_name_count():
    steps = """
      const card = cards()[0];
      const select = findAll(card, n => n.tagName === "select")[0];
      select.value = "fast";
      fire(select, "change");
      await click(byClass(card, "observation-device-toggle")[0]);
      devices[0].domains.push({domain: "new.example", query_count: 1});
      render();
      const updated = cards()[0];
      console.log(JSON.stringify({hidden: byClass(updated, "observation-device-body")[0].hidden,
        text: byClass(updated, "observation-device-toggle")[0].textContent,
        profile: findAll(updated, n => n.tagName === "select")[0].value,
        names: Array.from(observedNames)}));
    """
    out = run_dashboard(steps, [example_device()])
    assert out["hidden"] is True
    assert out["text"] == "Show names (13)"
    assert out["profile"] == "fast"
    assert "new.example" in out["names"]
