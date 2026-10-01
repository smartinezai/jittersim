"""Run the dashboard's Traffic discovery label helpers in node."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML
from jittersim.config.domain_labels import label_domain

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)


def served_functions():
    start = SCRIPT.index("function domainSearchUrl(")
    return SCRIPT[start : SCRIPT.index("function renderObservations(", start)]


def function_body(name):
    """Return the source of one top-level dashboard function."""

    match = re.search(r"\n    (?:async )?function " + re.escape(name) + r"\(", SCRIPT)
    assert match, name
    following = re.search(r"\n    (?:async )?function \w+\(", SCRIPT[match.end() :])
    end = match.end() + following.start() if following else len(SCRIPT)
    return SCRIPT[match.start() : end]


CASES = {
    "search_url_is_encoded": (
        "domainSearchUrl('a b.example.com')",
        "https://duckduckgo.com/?q=a%20b.example.com",
    ),
    "hint_for_a_shared_name": (
        "JSON.stringify(domainHint({label:{kind:'cdn'}, seen_with:['WhatsApp']}))",
        '{"text":"Seen with WhatsApp (guess)","searchable":true}',
    ),
    "two_apps": (
        "JSON.stringify(domainHint({label:{kind:'tracker'}, seen_with:['A','B']}))",
        '{"text":"Seen with A and B (guess)","searchable":false}',
    ),
    "unknown_without_hint": (
        "JSON.stringify(domainHint({label:{kind:'unknown'}, seen_with:[]}))",
        '{"text":"","searchable":true}',
    ),
    "known_app": (
        "JSON.stringify(domainHint({label:{kind:'app'}, seen_with:[]}))",
        '{"text":"","searchable":false}',
    ),
    "missing_label": (
        "JSON.stringify(domainHint({}))",
        '{"text":"","searchable":false}',
    ),
}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
@pytest.mark.parametrize("name", CASES)
def test_label_helpers(name):
    expression, expected = CASES[name]
    program = served_functions() + "\nconsole.log(" + expression + ");\n"
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_each_row_is_built_from_the_row_parts():
    body = function_body("renderObservations")
    assert "domainRowParts(observation)" in body
    assert "What is this?" in body
    assert 'link.target = "_blank";' in body
    assert 'link.rel = "noopener noreferrer";' in body
    assert 'link.setAttribute("aria-label", parts.searchLabel);' in body


def test_the_row_text_is_set_without_parsing_html():
    body = function_body("renderObservations")
    assert "innerHTML" not in body
    assert "insertAdjacentHTML" not in body


def test_the_row_keeps_the_query_count_and_both_buttons():
    body = function_body("renderObservations")
    assert "queryCountText(observation.query_count)" in body
    assert "profileButtonState(observation.domain, selectedProfile)" in body
    assert "blockButtonState(observation).label" in body


def test_the_dashboard_never_calls_the_search_site_by_itself():
    # The address appears only as the link target that a click opens.
    assert SCRIPT.count("duckduckgo.com") == 1
    assert "fetch(domainSearchUrl" not in SCRIPT
    assert "request(domainSearchUrl" not in SCRIPT


def run_node(program):
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def row_parts(observation):
    program = (
        served_functions()
        + "\nconsole.log(JSON.stringify(domainRowParts(" + json.dumps(observation) + ")));\n"
    )
    return json.loads(run_node(program))


def observed(domain, seen_with=()):
    return {
        "domain": domain,
        "query_count": 3,
        "label": label_domain(domain).as_dict(),
        "seen_with": list(seen_with),
    }


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")


@needs_node
@pytest.mark.parametrize(
    ("domain", "shows_link"),
    [
        ("a1051.dscg4.akamai.net", True),
        ("cdn.somesmallsite.io", True),
        ("mmx-ds.cdn.whatsapp.net", False),
        ("tags-eu.tiqcdn.com", False),
        ("10000.mt.eu.iot.dreame.tech", False),
        ("www.apple.com", False),
    ],
)
def test_the_search_link_shows_only_for_unknown_and_cdn_names(domain, shows_link):
    parts = row_parts(observed(domain))
    if shows_link:
        assert parts["searchUrl"] == "https://duckduckgo.com/?q=" + domain
    else:
        assert parts["searchUrl"] == ""


@needs_node
@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("a1051.dscg4.akamai.net", "Search the web for a1051.dscg4.akamai.net (opens a new tab)"),
        ("cdn.somesmallsite.io", "Search the web for cdn.somesmallsite.io (opens a new tab)"),
        ("mmx-ds.cdn.whatsapp.net", ""),
        ("tags-eu.tiqcdn.com", ""),
    ],
)
def test_the_search_link_has_a_spoken_label_only_when_it_shows(domain, expected):
    assert row_parts(observed(domain))["searchLabel"] == expected


@needs_node
def test_the_hint_text_appears_in_the_parts():
    parts = row_parts(observed("a1051.dscg4.akamai.net", ["WhatsApp", "Netflix"]))
    assert parts["hintText"] == "Seen with WhatsApp and Netflix (guess)"
    assert row_parts(observed("a1051.dscg4.akamai.net"))["hintText"] == ""


@needs_node
def test_the_label_slot_shows_the_label_name_and_not_the_raw_domain():
    parts = row_parts(observed("mmx-ds.cdn.whatsapp.net"))
    assert parts["labelName"] == "WhatsApp"
    assert parts["category"] == "Messaging"
    assert parts["kind"] == "app"
    assert parts["domain"] == "mmx-ds.cdn.whatsapp.net"


@needs_node
def test_an_unknown_name_shows_its_registrable_domain_in_the_label_slot():
    parts = row_parts(observed("cdn.somesmallsite.io"))
    assert (parts["labelName"], parts["category"], parts["kind"]) == (
        "somesmallsite.io", "Unknown site", "unknown")
    assert parts["domain"] == "cdn.somesmallsite.io"
    parts = row_parts(observed("a.b.example.co.uk"))
    assert parts["labelName"] == "example.co.uk"


@needs_node
def test_a_row_without_a_label_has_empty_label_fields_and_no_link():
    parts = row_parts({"domain": "old.example.com", "query_count": 1})
    assert parts == {
        "labelName": "", "category": "", "kind": "", "domain": "old.example.com",
        "hintText": "", "searchUrl": "", "searchLabel": "",
    }


@needs_node
def test_every_part_is_plain_text_and_markup_is_not_interpreted():
    hostile = "<img src=x onerror=alert(1)>"
    parts = row_parts({
        "domain": "a.example.com", "label": {"kind": "cdn", "name": hostile, "category": hostile},
        "seen_with": [hostile],
    })
    assert all(isinstance(value, str) for value in parts.values())
    assert parts["labelName"] == hostile
    assert parts["hintText"] == "Seen with " + hostile + " (guess)"
    assert parts["searchUrl"] == "https://duckduckgo.com/?q=a.example.com"
    assert parts["searchLabel"] == "Search the web for a.example.com (opens a new tab)"


# A small stand-in for the browser's document so the real renderObservations can run in node.
FAKE_DOM = """
class FakeNode {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.attributes = {}; this.dataset = {};
    this.className = ""; this.ownText = "";
  }
  set textContent(value) { this.ownText = String(value); this.children = []; }
  get textContent() { return this.ownText; }
  set innerHTML(value) { throw new Error("innerHTML must not be used"); }
  set outerHTML(value) { throw new Error("outerHTML must not be used"); }
  append(...nodes) { nodes.forEach((node) => this.children.push(node)); }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener() {}
  get childNodes() { return this.children; }
}
const document = { createElement: (tag) => new FakeNode(tag) };
const nodesById = {
  "observations": new FakeNode("div"), "observation-count": new FakeNode("span")
};
const element = (id) => nodesById[id];
let observedDomainsByDevice = {};
let observedNames = new Set();
const openObservationGroups = new Set();
const observationProfileChoice = new Map();
const profiles = [];
const deviceDisplayName = () => "Test device";
function addDomainToProfile() {}
function addGlobalBlock() {}
function changeGlobalBlock() {}
function dump(node) {
  return {
    tag: node.tagName, cls: node.className, text: node.ownText, attributes: node.attributes,
    href: node.href, target: node.target, rel: node.rel, kind: node.dataset.kind,
    children: node.children.map(dump),
  };
}
"""


def rendered_rows(domains):
    """Run the served renderObservations and return its domain rows as dumped trees."""

    start = SCRIPT.index("function domainSearchUrl(")
    functions = SCRIPT[start : SCRIPT.index("async function addDomainToProfile(", start)]
    data = {
        "available": True,
        "total_domains": len(domains),
        "devices": [
            {"ipv4_address": "192.168.50.9", "assigned_device_id": None, "domains": domains},
        ],
    }
    program = (
        FAKE_DOM + functions + "\nrenderObservations(" + json.dumps(data) + ");\n"
        + 'console.log(JSON.stringify(dump(element("observations"))));\n'
    )
    tree = json.loads(run_node(program))
    return find_all(tree, "observation-domain")


def find_all(node, class_name):
    found = [node] if class_name in node["cls"].split() else []
    for child in node["children"]:
        found.extend(find_all(child, class_name))
    return found


def host_of(row):
    return one(row, "observation-domain-host")["children"][0]["text"]


def one(node, class_name):
    matches = find_all(node, class_name)
    assert len(matches) == 1, (class_name, len(matches))
    return matches[0]


@needs_node
def test_a_rendered_row_shows_the_label_line_and_the_raw_domain_below_it():
    row, = rendered_rows([observed("mmx-ds.cdn.whatsapp.net")])
    label_line = one(row, "observation-label")
    assert [child["text"] for child in label_line["children"]] == ["WhatsApp", "Messaging"]
    assert one(label_line, "observation-category")["kind"] == "app"
    assert one(row, "observation-domain-host")["children"][0]["text"] == "mmx-ds.cdn.whatsapp.net"
    assert one(row, "observation-domain-count")["text"] == "3 queries"
    assert sorted(child["text"] for child in row["children"] if child["tag"] == "button") == [
        "Add to profile", "Block globally"]


@needs_node
def test_an_unknown_row_shows_the_registrable_domain_as_its_label():
    row, = rendered_rows([observed("cdn.somesmallsite.io")])
    label_line = one(row, "observation-label")
    assert label_line["children"][0]["text"] == "somesmallsite.io"
    assert label_line["children"][1]["text"] == "Unknown site"
    assert one(row, "observation-domain-host")["children"][0]["text"] == "cdn.somesmallsite.io"


@needs_node
def test_the_search_link_is_rendered_only_for_unknown_and_cdn_rows():
    rows = rendered_rows([
        observed("mmx-ds.cdn.whatsapp.net"),
        observed("a1051.dscg4.akamai.net", ["WhatsApp"]),
        observed("tags-eu.tiqcdn.com", ["WhatsApp"]),
        observed("cdn.somesmallsite.io"),
        observed("10000.mt.eu.iot.dreame.tech"),
    ])
    # Traffic groups sort by total queries and then by name so look the rows up by their name.
    links = {host_of(row): find_all(row, "observation-search-link") for row in rows}
    assert {domain: len(found) for domain, found in links.items()} == {
        "mmx-ds.cdn.whatsapp.net": 0,
        "a1051.dscg4.akamai.net": 1,
        "tags-eu.tiqcdn.com": 0,
        "cdn.somesmallsite.io": 1,
        "10000.mt.eu.iot.dreame.tech": 0,
    }
    for domain in ("a1051.dscg4.akamai.net", "cdn.somesmallsite.io"):
        found = links[domain]
        link, = found
        assert link["tag"] == "a"
        assert link["text"] == "What is this?"
        assert link["href"] == "https://duckduckgo.com/?q=" + domain
        assert link["target"] == "_blank"
        assert link["rel"] == "noopener noreferrer"
        assert link["attributes"] == {
            "aria-label": "Search the web for " + domain + " (opens a new tab)"
        }


@needs_node
def test_the_hint_is_rendered_in_its_own_line():
    rows = rendered_rows([
        observed("a1051.dscg4.akamai.net", ["WhatsApp"]),
        observed("mmx-ds.cdn.whatsapp.net"),
    ])
    by_host = {host_of(row): row for row in rows}
    shared = by_host["a1051.dscg4.akamai.net"]
    own = by_host["mmx-ds.cdn.whatsapp.net"]
    assert one(shared, "observation-hint")["text"] == "Seen with WhatsApp (guess)"
    assert find_all(own, "observation-hint") == []
    assert find_all(own, "observation-hint-line") == []


@needs_node
def test_a_row_without_a_label_has_no_label_line_but_still_shows_the_domain():
    row, = rendered_rows([{"domain": "old.example.com", "query_count": 1}])
    assert find_all(row, "observation-label") == []
    assert one(row, "observation-domain-host")["children"][0]["text"] == "old.example.com"


@needs_node
def test_hostile_text_is_only_ever_assigned_as_text():
    hostile = "<img src=x onerror=alert(1)>"
    row, = rendered_rows([{
        "domain": "a.example.com", "query_count": 1,
        "label": {"kind": "cdn", "name": hostile, "category": hostile, "known": False},
        "seen_with": [hostile],
    }])
    label_line = one(row, "observation-label")
    assert [child["text"] for child in label_line["children"]] == [hostile, hostile]
    assert one(row, "observation-hint")["text"] == "Seen with " + hostile + " (guess)"
