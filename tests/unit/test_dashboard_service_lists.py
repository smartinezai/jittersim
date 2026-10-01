"""Exercise the service list panel using the JavaScript served to the browser."""

import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML


def served_functions():
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)

    def block(start, end):
        begin = script.index(start)
        return script[begin : script.index(end, begin)]

    return block("let serviceCatalogue = [];", "function renderServiceAssignments(") + block(
        'element("service-list-add-observed")', 'element("service-assignment-form")'
    )


HARNESS = r"""
const assert = require("node:assert/strict");
const nodes = new Map();
const messages = [];
const refreshMessages = [];
const requests = [];
let observedNames = new Set();
let request = async (path, method, body) => {
  requests.push({path, method, body});
  return {live_applied: true};
};
function makeNode(tag) {
  return {
    tag, id: "", value: "", textContent: "", className: "", checked: false, title: "",
    type: "", disabled: false, children: [], listeners: new Map(), attributes: new Map(),
    append(...items) {this.children.push(...items)},
    appendChild(item) {this.children.push(item); return item},
    replaceChildren(...items) {this.children = items},
    addEventListener(kind, callback) {this.listeners.set(kind, callback)},
    scrollIntoView() {},
    querySelectorAll(selector) {
      const wantChecked = selector.endsWith(":checked");
      const found = [];
      const walk = (node) => {
        for (const child of node.children) {
          if (child && child.tag === "input" && (!wantChecked || child.checked)) found.push(child);
          if (child && child.children) walk(child);
        }
      };
      walk(this);
      return found;
    },
    get options() {return this.children.filter((child) => child.tag === "option")},
  };
}
const document = {
  createElement: makeNode,
  createTextNode: (text) => ({tag: "#text", textContent: text, children: []}),
};
function makeSelect() {
  // A select shows its first option unless its value names an option it holds.
  const node = makeNode("select");
  node.selected = "";
  Object.defineProperty(node, "value", {
    get() {
      const options = this.options;
      return options.some((option) => option.value === this.selected)
        ? this.selected : (options.length ? options[0].value : "");
    },
    set(value) {this.selected = value},
  });
  const replace = node.replaceChildren;
  node.replaceChildren = function (...items) {replace.apply(this, items); this.selected = ""};
  return node;
}
function element(id) {
  if (!nodes.has(id)) {
    const node = id === "service-list-observed" ? makeSelect() : makeNode("div");
    node.id = id;
    nodes.set(id, node);
  }
  return nodes.get(id);
}
function showMessage(text, kind) {messages.push({text, kind})}
function refreshAfterSuccess(message) {refreshMessages.push(message)}
function setButtonBusy(button, busy) {button.disabled = busy}
const revealed = [];
function revealElement(target) {revealed.push(target.id)}
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const CATALOGUE = [
  {key: "netflix", name: "Netflix", domains: ["netflix.com", "nflxvideo.net"]},
  {key: "claude", name: "Claude", domains: ["claude.ai"]},
];
"""

CASES = {
    "observed_names_are_sorted_and_the_choice_survives_a_refresh": r"""
      observedNames = new Set(["b.example", "a.example"]);
      renderServiceListObserved();
      assert.deepEqual(element("service-list-observed").options.map((o) => o.value),
        ["", "a.example", "b.example"]);
      element("service-list-observed").value = "b.example";

      observedNames = new Set(["c.example", "b.example", "a.example"]);
      renderServiceListObserved();

      assert.equal(element("service-list-observed").value, "b.example");
      observedNames = new Set(["c.example"]);
      renderServiceListObserved();
      assert.equal(element("service-list-observed").value, "");
    """,
    "no_names_seen_says_so": r"""
      renderServiceListObserved();
      assert.deepEqual(element("service-list-observed").options.map((o) => o.textContent),
        ["No names seen yet"]);
    """,
    "catalogue_keeps_ticks_across_a_reload": r"""
      serviceCatalogue = CATALOGUE;
      renderServiceCatalogue();
      const boxes = element("service-list-catalogue").querySelectorAll("input");
      assert.deepEqual(boxes.map((box) => box.value), ["netflix", "claude"]);
      boxes[1].checked = true;

      renderServiceCatalogue();

      const again = element("service-list-catalogue").querySelectorAll("input");
      assert.deepEqual(again.filter((box) => box.checked).map((box) => box.value), ["claude"]);
      const label = element("service-list-catalogue").children.find((c) => c.tag === "label");
      assert.equal(label.title, "netflix.com, nflxvideo.net");
    """,
    "editing_fills_the_form": r"""
      serviceCatalogue = CATALOGUE;
      renderServiceCatalogue();
      editServiceList({
        id: "streaming", name: "Streaming", profile_id: "choppy", services: ["netflix"],
        hostnames: ["a.example", "b.example"], enabled: false,
      });
      assert.equal(element("service-list-id").value, "streaming");
      assert.equal(element("service-list-name").value, "Streaming");
      assert.equal(element("service-list-profile").value, "choppy");
      assert.equal(element("service-list-hostnames").value, "a.example\nb.example");
      assert.equal(element("service-list-enabled").checked, false);
      assert.deepEqual(
        element("service-list-catalogue").querySelectorAll("input:checked").map((b) => b.value),
        ["netflix"]);
      assert.deepEqual(revealed, ["service-list-form"]);
    """,
    "the_list_renders_names_and_actions": r"""
      serviceCatalogue = CATALOGUE;
      renderServiceLists({service_lists: [{
        id: "streaming", name: "Streaming", profile_name: "Choppy", services: ["netflix"],
        hostnames: ["extra.example"], enabled: true,
      }]});
      assert.equal(element("service-list-count").textContent, "1 service list");
      const row = element("service-list-list").children[0];
      assert.match(row.children[0].children[1].textContent, /All affected devices → Choppy/);
      assert.equal(row.children[1].textContent, "Netflix, extra.example");
      assert.equal(row.children[2].children.length, 2);

      renderServiceLists({service_lists: [], error: "boom"});
      assert.equal(element("service-list-list").textContent, "boom");
      assert.equal(element("service-list-count").textContent, "0 service lists");
    """,
    "adding_an_observed_name_never_duplicates": r"""
      observedNames = new Set(["a.example"]);
      renderServiceListObserved();
      element("service-list-observed").value = "a.example";
      element("service-list-hostnames").value = "x.example, y.example";
      element("service-list-add-observed").listeners.get("click")();
      element("service-list-add-observed").listeners.get("click")();
      assert.equal(element("service-list-hostnames").value, "x.example\ny.example\na.example");
      element("service-list-observed").value = "";
      element("service-list-add-observed").listeners.get("click")();
      assert.equal(element("service-list-hostnames").value, "x.example\ny.example\na.example");
    """,
    "saving_posts_the_choices": r"""
      serviceCatalogue = CATALOGUE;
      renderServiceCatalogue();
      element("service-list-catalogue").querySelectorAll("input")[0].checked = true;
      element("service-list-id").value = "streaming";
      element("service-list-name").value = "Streaming";
      element("service-list-profile").value = "choppy";
      element("service-list-hostnames").value = "a.example\n  b.example,c.example";
      element("service-list-enabled").checked = true;

      await element("service-list-form").listeners.get("submit")({preventDefault() {}});
      await flush();

      assert.deepEqual(requests, [{
        path: "/api/service-lists", method: "POST",
        body: {id: "streaming", name: "Streaming", profile_id: "choppy", services: ["netflix"],
               hostnames: ["a.example", "b.example", "c.example"], enabled: true},
      }]);
      assert.deepEqual(refreshMessages, ["Service list saved and applied."]);
    """,
    "saving_needs_something_to_match": r"""
      serviceCatalogue = CATALOGUE;
      renderServiceCatalogue();
      element("service-list-hostnames").value = "  ";

      await element("service-list-form").listeners.get("submit")({preventDefault() {}});
      await flush();

      assert.deepEqual(requests, []);
      assert.equal(messages[0].kind, "error");
      assert.match(messages[0].text, /at least one service or enter a hostname/);
    """,
}


@pytest.mark.parametrize("case", CASES)
def test_served_service_list_panel_behavior(case):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise the served dashboard")
    program = (
        HARNESS
        + served_functions()
        + "\n(async () => {"
        + CASES[case]
        + "\nconsole.log('CASE-COMPLETE');"
        + "\n})().catch(error => {console.error(error); process.exitCode = 1});"
    )
    result = subprocess.run([node, "-"], input=program, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    # Node exits quietly with code 0 if a case hangs on a promise that never settles.
    assert "CASE-COMPLETE" in result.stdout, "the case never finished"
