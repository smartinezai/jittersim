"""Check the Impact card in Diagnostics: its markup and its polling and its colours."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

HTML = DASHBOARD_HTML.split("<script>")[0]
SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
STYLE = re.search(r"<style>(.*?)</style>", DASHBOARD_HTML, re.DOTALL).group(1)
SECOND_SCRIPT = DASHBOARD_HTML.split("</script>")[1]

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")


def diagnostics_page():
    return re.search(
        r'<section class="page" id="page-diagnostics"[^>]*>(.*?)<!-- /page:diagnostics -->',
        HTML,
        re.DOTALL,
    ).group(1)


def card_source():
    start = SCRIPT.index("const IMPACT_WINDOW_SECONDS")
    return SCRIPT[start : SCRIPT.index("// end of the impact card", start)]


def test_the_impact_card_is_the_first_card_of_diagnostics():
    page = diagnostics_page()
    first_card = re.search(r'<section class="([^"]*)"([^>]*)>', page)
    assert "impact-card" in first_card.group(1).split()
    assert 'data-collapse="open"' in first_card.group(2)
    assert 'aria-labelledby="impact-title"' in first_card.group(2)
    assert len(re.findall(r"<h2[^>]*>Impact</h2>", HTML)) == 1


@pytest.mark.parametrize(
    "element_id",
    [
        "impact-title", "impact-state", "impact-note", "impact-list", "impact-protected",
        "impact-status",
    ],
)
def test_each_impact_id_exists_once_inside_the_card(element_id):
    assert HTML.count(f' id="{element_id}"') == 1
    page = diagnostics_page()
    card = page[: page.index("</section>")]
    assert f' id="{element_id}"' in card


def test_the_card_starts_locked_with_one_quiet_status_line_and_a_list_that_is_not_live():
    assert re.search(r'<span id="impact-state" class="count-badge">Check token</span>', HTML)
    assert re.search(
        r'<div id="impact-list" class="impact-list">'
        r"Check the admin token to see the impact\.</div>",
        HTML,
    )
    assert re.search(r'<p id="impact-protected" class="impact-protected" hidden></p>', HTML)
    assert re.search(r'<p id="impact-status" class="impact-status" role="status"></p>', HTML)
    card = diagnostics_page()
    card = card[: card.index("</section>")]
    assert card.count("aria-live") == 0
    assert card.count('role="status"') == 1
    assert (
        "Delay, jitter, reorder, duplicate and corrupt are shown as configured because "
        "queue counters cannot measure them."
    ) in HTML


def test_the_status_line_is_hidden_from_view_but_not_from_screen_readers():
    rule = impact_rules()[".impact-status"]
    assert rule["position"] == "absolute"
    assert rule["width"] == "1px" and rule["height"] == "1px"
    assert rule["overflow"] == "hidden"
    assert "clip" in rule
    assert "display" not in rule and "visibility" not in rule
    assert impact_rules()[".impact-card"]["position"] == "relative"


def test_the_card_code_only_writes_text():
    source = card_source()
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert forbidden not in source
    assert "textContent" in source


def test_polling_runs_only_while_diagnostics_and_the_tab_are_visible():
    source = card_source()
    assert "IMPACT_POLL_INTERVAL_MS = 5000" in source
    assert 'request("/api/impact", "GET")' in source
    assert 'document.body.dataset.section === "diagnostics"' in source
    for condition in ("document.hidden", "adminTokenVerified", "helperConfigured"):
        assert condition in source


GUARDED_SYNC = 'if (typeof syncImpact === "function") syncImpact();'


def test_the_page_tells_the_card_when_it_may_poll():
    assert "syncImpact()" in re.search(
        r"function showPage\(.*?\n    }\n", SECOND_SCRIPT, re.DOTALL
    ).group(0)
    assert re.search(r'document.addEventListener\("visibilitychange", syncImpact\)', SCRIPT)
    token_check = re.search(r"async function checkAdminToken\(\).*?\n    }\n", SCRIPT, re.DOTALL)
    assert re.search(
        r"await startTrafficSampling\(\);\n\s+if \(tokenRevision[^\n]*\n\s+"
        + re.escape(GUARDED_SYNC),
        token_check.group(0),
    )
    panels = re.search(r"function setPrivateDataPanels\(.*?\n    }\n", SCRIPT, re.DOTALL).group(0)
    assert GUARDED_SYNC in panels


def hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def luminance(rgb):
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first, second):
    lighter, darker = sorted((luminance(hex_to_rgb(first)), luminance(hex_to_rgb(second))), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# Colours are named design tokens in the first :root block of the stylesheet.
TOKENS = dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-f]{6});", STYLE))


def resolve(value):
    """Return the hex value of a literal colour or of a var(--token) reference."""

    match = re.fullmatch(r"var\((--[a-z0-9-]+)\)", value)
    return TOKENS[match.group(1)] if match else value


CARD_BACKGROUND = TOKENS["--bg-card"]
ROW_BACKGROUND = TOKENS["--bg-inset"]


def impact_rules():
    rules = {}
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", STYLE):
        names = [name.strip() for name in selector.split(",")]
        if any(name.startswith(".impact") for name in names):
            declarations = dict(
                (key.strip(), value.strip())
                for key, value in (item.split(":", 1) for item in body.split(";") if ":" in item)
            )
            for name in names:
                if name.startswith(".impact"):
                    rules.setdefault(name, {}).update(declarations)
    return rules


def test_every_impact_text_colour_has_enough_contrast_on_its_background():
    rules = impact_rules()
    assert ".impact-row" in rules and ".impact-chip.affected" in rules
    backgrounds = {
        ".impact-note": CARD_BACKGROUND,
        ".impact-protected": CARD_BACKGROUND,
        ".impact-list": CARD_BACKGROUND,
    }
    checked = []
    for selector, declarations in rules.items():
        color = declarations.get("color")
        if not color:
            continue
        color = resolve(color)
        background = resolve(declarations.get("background") or backgrounds.get(selector, ROW_BACKGROUND))
        assert re.fullmatch(r"#[0-9a-f]{6}", color), selector
        assert re.fullmatch(r"#[0-9a-f]{6}", background), selector
        assert contrast(color, background) >= 4.5, (selector, contrast(color, background))
        checked.append(selector)
    for expected in (
        ".impact-note",
        ".impact-protected",
        ".impact-name",
        ".impact-direction",
        ".impact-lines",
        ".impact-chip",
        ".impact-chip.affected",
    ):
        assert expected in checked, expected


def test_the_affected_chip_uses_the_amber_pair_and_the_others_stay_muted():
    rules = impact_rules()
    assert resolve(rules[".impact-chip.affected"]["color"]) == TOKENS["--warn-text"]
    assert resolve(rules[".impact-chip.affected"]["border-color"]) == TOKENS["--warn-border"]
    for muted in (".impact-chip.idle", ".impact-chip.too-few", ".impact-chip.clean", ".impact-chip.unmeasured"):
        assert resolve(rules.get(muted, {}).get("color", "")) != TOKENS["--warn-text"], muted


HARNESS = r"""
const timers = [];
const requests = [];
const responses = [];
let nextError = null;
let fakeNow = 1000000;
Date.now = () => fakeNow;
function makeNode(tag) {
  return {
    tag: tag, children: [], parent: null, _text: "", hidden: false, _className: "",
    attributes: {}, writes: 0, classWrites: 0,
    set textContent(value) {
      this._text = String(value);
      this.children.forEach((child) => { child.parent = null; });
      this.children = [];
      this.writes += 1;
    },
    get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); },
    set className(value) { this._className = value; this.classWrites += 1; },
    get className() { return this._className; },
    set innerHTML(value) { throw new Error("innerHTML must not be used"); },
    append(...nodes) { nodes.forEach((node) => this.appendChild(node)); },
    appendChild(node) {
      if (node.parent) node.remove();
      node.parent = this;
      this.children.push(node);
      return node;
    },
    insertBefore(node, reference) {
      if (node.parent) node.remove();
      node.parent = this;
      const index = reference ? this.children.indexOf(reference) : -1;
      if (index === -1) this.children.push(node); else this.children.splice(index, 0, node);
      return node;
    },
    remove() {
      if (this.parent) {
        this.parent.children.splice(this.parent.children.indexOf(this), 1);
        this.parent = null;
      }
    },
    replaceChildren(...nodes) {
      this.children.forEach((child) => { child.parent = null; });
      this._text = "";
      this.children = [];
      nodes.forEach((node) => this.appendChild(node));
    },
    setAttribute(name, value) { this.attributes[name] = value; }
  };
}
const nodes = {};
const element = (id) => nodes[id] || (nodes[id] = makeNode("div"));
const document = {
  hidden: false,
  body: {dataset: {section: "diagnostics"}},
  createElement: (tag) => makeNode(tag)
};
const window = {
  setInterval: (callback, milliseconds) => { timers.push({callback, milliseconds, active: true}); return timers.length; },
  clearInterval: (id) => { if (timers[id - 1]) timers[id - 1].active = false; }
};
let adminTokenVerified = true;
let helperConfigured = true;
async function request(path, method) {
  requests.push(path);
  if (nextError) { const error = nextError; nextError = null; throw error; }
  return responses.shift();
}
function activeTimers() { return timers.filter((timer) => timer.active); }
function entry(extra) {
  return Object.assign({
    device_id: "lab", name: "Lab phone", kind_label: "Device", direction: "upload",
    interface: "eth0", handle: "11:", kind: "netem", generation: "a1b2c3d4-1",
    impairment: {delay_ms: 0, jitter_ms: 0, loss_percent: 10, bandwidth_kbit: null,
      reorder_percent: 0, duplicate_percent: 0, corrupt_percent: 0},
    packets: 0, bytes: 0, drops: 0, overlimits: 0, requeues: 0, backlog_bytes: 0, qlen: 0
  }, extra || {});
}
function answer(queues, at, names) {
  responses.push({available: true, paused: false, message: null, sampled_at: at, queues: queues,
    protected: names || []});
}
function paused() {
  responses.push({available: false, paused: true, message: "Impact could not be read right now.",
    sampled_at: 0, queues: [], protected: []});
}
function dump(value) { console.log(JSON.stringify(value)); }
function rowTexts() {
  return element("impact-list").children.map((row) => row.textContent);
}
function listNodes() {
  const list = element("impact-list");
  const all = [];
  list.children.forEach((row) => {
    all.push(row);
    row.children.forEach((part) => { all.push(part); part.children.forEach((leaf) => all.push(leaf)); });
  });
  return all;
}
function sameNodes(before, after) {
  return before.length === after.length && before.every((node, index) => node === after[index]);
}
"""


def run(scenario):
    program = HARNESS + card_source() + "\n(async () => {\n" + scenario + "\n})();\n"
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_it_collects_first_and_then_shows_the_loss_and_the_verdict():
    result = run(
        """
        answer([entry()], 0);
        answer([entry({packets: 81, drops: 9})], 10);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        const first = rowTexts();
        const firstState = element("impact-state").textContent;
        await loadImpact();
        const second = rowTexts();
        dump({first, second, firstState, secondState: element("impact-state").textContent,
          note: element("impact-note").hidden});
        """
    )
    assert result["first"] == ["Lab phoneUploadCollecting…"]
    assert result["second"][0].startswith("Lab phoneUploadAffected in the last minute")
    assert "Loss: set 10% · actual 10% (9 of 90 packets dropped)" in result["second"][0]
    assert result["firstState"] == result["secondState"] == "Live"
    assert result["note"] is False


@needs_node
def test_each_verdict_has_a_plain_label():
    result = run(
        """
        const delayOnly = {delay_ms: 50, jitter_ms: 0, loss_percent: 0, bandwidth_kbit: null,
          reorder_percent: 0, duplicate_percent: 0, corrupt_percent: 0};
        const labels = [];
        for (const [first, second] of [
          [{}, {packets: 0}], [{}, {packets: 20}], [{}, {packets: 80}],
          [{}, {packets: 40, drops: 5}],
          [{impairment: delayOnly}, {impairment: delayOnly, packets: 40}]
        ]) {
          answer([entry(first)], 0);
          answer([entry(second)], 10);
          syncImpact();
          await new Promise((resolve) => setTimeout(resolve, 5));
          await loadImpact();
          labels.push(element("impact-list").children[0].children[0].children[2].textContent);
          stopImpact("x", "y");
        }
        dump(labels);
        """
    )
    assert result == [
        "No traffic in the last minute",
        "Too few packets to judge",
        "No drops or cap hit seen",
        "Affected in the last minute",
        "Effect not measured here",
    ]


@needs_node
def test_names_are_written_as_text_even_when_they_look_like_markup():
    result = run(
        """
        const hostile = '<img src=x onerror="alert(1)">';
        answer([entry({name: hostile})], 0, ['<b>Dad</b>', "Mum & co"]);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        dump({row: rowTexts()[0], protectedText: element("impact-protected").textContent});
        """
    )
    assert result["row"].startswith('<img src=x onerror="alert(1)">Upload')
    assert result["protectedText"] == "Protected (not affected): <b>Dad</b>, Mum & co"


@needs_node
def test_the_protected_line_shows_only_names_and_hides_when_empty():
    result = run(
        """
        answer([entry()], 0, ["Dad's laptop", "Phone"]);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        const shown = [element("impact-protected").hidden, element("impact-protected").textContent];
        answer([entry()], 5, []);
        await loadImpact();
        dump({shown, hidden: element("impact-protected").hidden});
        """
    )
    assert result["shown"] == [False, "Protected (not affected): Dad's laptop, Phone"]
    assert result["hidden"] is True


@needs_node
def test_an_unavailable_answer_clears_the_history_and_shows_only_its_message():
    result = run(
        """
        answer([entry()], 0, ["Phone"]);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        responses.push({available: false, sampled_at: 5, queues: [], protected: [],
          message: "The networking helper is out of date. Update it to see the impact check."});
        await loadImpact();
        dump({list: element("impact-list").textContent, state: element("impact-state").textContent,
          note: element("impact-note").hidden, protectedHidden: element("impact-protected").hidden,
          history: impactHistory, children: element("impact-list").children.length});
        """
    )
    assert result == {
        "list": "The networking helper is out of date. Update it to see the impact check.",
        "state": "Unavailable",
        "note": True,
        "protectedHidden": True,
        "history": {},
        "children": 0,
    }


@needs_node
def test_an_available_answer_without_impaired_queues_says_so():
    result = run(
        """
        answer([entry({impairment: {delay_ms: 0, jitter_ms: 0, loss_percent: 0,
          bandwidth_kbit: null, reorder_percent: 0, duplicate_percent: 0, corrupt_percent: 0}})], 0);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        dump(element("impact-list").textContent);
        """
    )
    assert result == "Nothing is slowed down right now. Apply a profile to a device to see its impact here."


@needs_node
def test_polling_waits_for_the_token_the_helper_the_tab_and_the_page():
    result = run(
        """
        const outcomes = {};
        function snapshot(name) {
          outcomes[name] = {requests: requests.length, timers: activeTimers().length,
            list: element("impact-list").textContent, state: element("impact-state").textContent};
        }
        adminTokenVerified = false;
        syncImpact();
        snapshot("no token");
        adminTokenVerified = true; helperConfigured = false;
        syncImpact();
        snapshot("no helper");
        helperConfigured = true; document.hidden = true;
        syncImpact();
        snapshot("tab hidden");
        document.hidden = false; document.body.dataset.section = "devices";
        syncImpact();
        snapshot("other page");
        document.body.dataset.section = "diagnostics";
        answer([entry()], 0);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        snapshot("ready");
        outcomes.interval = timers[timers.length - 1].milliseconds;
        syncImpact();
        snapshot("ready again");
        document.hidden = true;
        syncImpact();
        snapshot("hidden again");
        dump(outcomes);
        """
    )
    assert result["no token"]["requests"] == 0 and result["no token"]["timers"] == 0
    assert result["no token"]["list"] == "Check the admin token to see the impact."
    assert result["no token"]["state"] == "Check token"
    assert result["no helper"]["requests"] == 0 and result["no helper"]["timers"] == 0
    assert result["no helper"]["list"] == "Impact needs the Pi helper."
    assert result["no helper"]["state"] == "Unavailable"
    assert result["tab hidden"]["requests"] == 0 and result["tab hidden"]["timers"] == 0
    assert result["other page"]["requests"] == 0 and result["other page"]["timers"] == 0
    assert result["ready"]["requests"] == 1 and result["ready"]["timers"] == 1
    assert result["interval"] == 5000
    assert result["ready again"]["requests"] == 1 and result["ready again"]["timers"] == 1
    assert result["hidden again"]["timers"] == 0


@needs_node
def test_losing_the_token_stops_polling_and_forgets_the_samples():
    result = run(
        """
        answer([entry()], 0);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        const before = [activeTimers().length, Object.keys(impactHistory).length];
        adminTokenVerified = false;
        syncImpact();
        dump({before, timers: activeTimers().length, history: impactHistory,
          list: element("impact-list").textContent, state: element("impact-state").textContent});
        """
    )
    assert result["before"] == [1, 1]
    assert result["timers"] == 0
    assert result["history"] == {}
    assert result["list"] == "Check the admin token to see the impact."
    assert result["state"] == "Check token"


@needs_node
def test_an_answer_that_arrives_after_the_token_changed_is_ignored():
    result = run(
        """
        answer([entry()], 0, ["Phone"]);
        syncImpact();
        adminTokenVerified = false;
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        dump({history: impactHistory, list: element("impact-list").textContent,
          protectedHidden: element("impact-protected").hidden});
        """
    )
    assert result["history"] == {}
    assert result["list"] == "Check the admin token to see the impact."
    assert result["protectedHidden"] is True


@needs_node
def test_a_rejected_token_stops_and_another_failure_keeps_the_rows():
    result = run(
        """
        answer([entry()], 0);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        nextError = Object.assign(new Error("boom"), {status: 502});
        await loadImpact();
        const kept = {state: element("impact-state").textContent, rows: rowTexts().length,
          timers: activeTimers().length};
        nextError = Object.assign(new Error("no"), {status: 401});
        await loadImpact();
        dump({kept, timers: activeTimers().length, history: impactHistory,
          list: element("impact-list").textContent});
        """
    )
    assert result["kept"] == {"state": "Update paused", "rows": 1, "timers": 1}
    assert result["timers"] == 0
    assert result["history"] == {}
    assert result["list"] == "Check the admin token to see the impact."


@needs_node
def test_two_overlapping_polls_send_one_request():
    result = run(
        """
        answer([entry()], 0);
        const first = loadImpact();
        const second = loadImpact();
        await Promise.all([first, second]);
        dump(requests.length);
        """
    )
    assert result == 1


@needs_node
def test_identical_answers_keep_every_row_node_and_write_nothing():
    result = run(
        """
        answer([entry()], 0);
        answer([entry({packets: 90, drops: 10})], 5);
        answer([entry({packets: 180, drops: 20})], 10);
        answer([entry({packets: 180, drops: 20})], 15);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        await loadImpact();
        const afterSecond = listNodes();
        const lossLine = element("impact-list").children[0].children[1].children[0];
        const textSecond = lossLine.textContent;
        await loadImpact();
        const afterThird = listNodes();
        const textThird = lossLine.textContent;
        const writes = (nodes) => nodes.reduce((sum, node) => sum + node.writes, 0);
        const writesThird = writes(afterThird);
        const listWrites = element("impact-list").writes;
        await loadImpact();
        dump({sameThird: sameNodes(afterSecond, afterThird),
          sameFourth: sameNodes(afterThird, listNodes()),
          textSecond, textThird,
          nothingWritten: writes(listNodes()) === writesThird,
          listUntouched: element("impact-list").writes === listWrites,
          count: afterThird.length});
        """
    )
    assert result["sameThird"] is True and result["sameFourth"] is True
    assert result["count"] == 7  # row and head and three head parts and the list and one line
    assert "(10 of 100 packets dropped)" in result["textSecond"]
    assert "(20 of 200 packets dropped)" in result["textThird"]
    assert result["nothingWritten"] is True
    assert result["listUntouched"] is True


@needs_node
def test_a_changed_number_updates_the_text_in_place_and_nothing_else():
    result = run(
        """
        answer([entry()], 0);
        answer([entry({packets: 90, drops: 10})], 5);
        answer([entry({packets: 180, drops: 20})], 10);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        await loadImpact();
        const row = element("impact-list").children[0];
        const parts = row.children.slice();
        const lossLine = row.children[1].children[0];
        const chip = row.children[0].children[2];
        const chipWrites = [chip.writes, chip.classWrites];
        await loadImpact();
        dump({sameRow: element("impact-list").children[0] === row,
          sameParts: row.children.every((part, index) => part === parts[index]),
          sameLine: row.children[1].children[0] === lossLine,
          text: lossLine.textContent,
          chipUntouched: chip.writes === chipWrites[0] && chip.classWrites === chipWrites[1]});
        """
    )
    assert result["sameRow"] and result["sameParts"] and result["sameLine"]
    assert result["chipUntouched"] is True
    assert "(20 of 200 packets dropped)" in result["text"]


@needs_node
def test_rows_that_come_and_go_change_only_themselves():
    result = run(
        """
        const second = (extra) => entry(Object.assign({name: "Kitchen tablet", handle: "12:",
          device_id: "kitchen"}, extra || {}));
        answer([entry()], 0);
        answer([entry({packets: 90, drops: 10})], 5);
        answer([entry({packets: 180, drops: 20}), second()], 10);
        answer([second({packets: 90, drops: 10})], 15);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        await loadImpact();
        const lab = element("impact-list").children[0];
        await loadImpact();
        const nameOf = (row) => row.children[0].children[0].textContent;
        const afterAdd = element("impact-list").children.map(nameOf);
        const kitchen = element("impact-list").children.find((row) => nameOf(row) === "Kitchen tablet");
        const labKept = element("impact-list").children.indexOf(lab) !== -1;
        await loadImpact();
        dump({afterAdd, labKept,
          kitchenKept: element("impact-list").children[0] === kitchen,
          afterRemove: element("impact-list").children.length,
          labDetached: lab.parent === null});
        """
    )
    assert result["afterAdd"] == ["Kitchen tablet", "Lab phone"]
    assert result["labKept"] is True
    assert result["kitchenKept"] is True
    assert result["afterRemove"] == 1
    assert result["labDetached"] is True


@needs_node
def test_a_verdict_change_updates_the_status_line_once_and_nothing_else_does():
    result = run(
        """
        const status = element("impact-status");
        answer([entry()], 0);
        answer([entry({packets: 90, drops: 10})], 5);
        answer([entry({packets: 180, drops: 20})], 10);
        answer([entry({packets: 270, drops: 30})], 15);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        const afterFirst = [status.textContent, status.writes];
        await loadImpact();
        const afterVerdict = [status.textContent, status.writes];
        await loadImpact();
        await loadImpact();
        dump({afterFirst, afterVerdict, last: [status.textContent, status.writes]});
        """
    )
    assert result["afterFirst"][0] == ""
    assert result["afterVerdict"][0] == "Lab phone upload: Affected in the last minute"
    assert result["last"] == result["afterVerdict"]


@needs_node
def test_a_restart_goes_quiet_and_the_next_verdict_is_announced_again():
    result = run(
        """
        const status = element("impact-status");
        answer([entry()], 0);
        answer([entry({packets: 90, drops: 10})], 5);
        answer([entry({packets: 10, drops: 1, generation: "a1b2c3d4-2"})], 10);
        answer([entry({packets: 100, drops: 11, generation: "a1b2c3d4-2"})], 15);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        await loadImpact();
        const afterVerdict = [status.textContent, status.writes];
        await loadImpact();
        const afterRestart = [status.textContent, status.writes,
          element("impact-list").children[0].children[0].children[2].textContent];
        const lines = element("impact-list").children[0].children[1];
        const restartedLines = [lines.children.length, lines.hidden];
        await loadImpact();
        dump({afterVerdict, afterRestart, restartedLines,
          final: [status.textContent, status.writes]});
        """
    )
    assert result["afterRestart"][2] == "Collecting…"
    assert result["afterRestart"][:2] == result["afterVerdict"]
    assert result["restartedLines"] == [0, True]
    assert result["final"][0] == "Lab phone upload: Affected in the last minute"
    assert result["final"][1] == result["afterVerdict"][1] + 1


@needs_node
def test_two_rows_that_change_together_share_one_announcement():
    result = run(
        """
        const down = (extra) => entry(Object.assign({direction: "download", interface: "wlan0"}, extra || {}));
        const status = element("impact-status");
        answer([entry(), down()], 0);
        answer([entry({packets: 90, drops: 10}), down({packets: 90, drops: 10})], 5);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        const before = status.writes;
        await loadImpact();
        dump({text: status.textContent, writes: status.writes - before});
        """
    )
    assert result["writes"] == 1
    assert result["text"] == (
        "Lab phone upload: Affected in the last minute. "
        "Lab phone download: Affected in the last minute"
    )


@needs_node
def test_a_new_state_message_is_announced_once():
    result = run(
        """
        const status = element("impact-status");
        const unavailable = (message) => responses.push({available: false, paused: false,
          message: message, sampled_at: 0, queues: [], protected: []});
        unavailable("Network is reset. Choose Start to start impact checks.");
        unavailable("Network is reset. Choose Start to start impact checks.");
        unavailable("Impact needs the Pi helper.");
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        const first = [status.textContent, status.writes];
        await loadImpact();
        const second = [status.textContent, status.writes];
        await loadImpact();
        dump({first, second, third: [status.textContent, status.writes]});
        """
    )
    assert result["first"][0] == "Network is reset. Choose Start to start impact checks."
    assert result["second"] == result["first"]
    assert result["third"][0] == "Impact needs the Pi helper."
    assert result["third"][1] == result["first"][1] + 1


@needs_node
def test_a_paused_answer_keeps_the_rows_and_the_history_and_marks_the_badge():
    result = run(
        """
        answer([entry()], 0);
        answer([entry({packets: 90, drops: 10})], 5);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        await loadImpact();
        const before = listNodes();
        const textBefore = rowTexts();
        const samples = impactHistory["eth0/11:"].samples.length;
        paused();
        fakeNow += 10000;
        await loadImpact();
        const during = {badge: element("impact-state").textContent, same: sameNodes(before, listNodes()),
          texts: rowTexts().join() === textBefore.join(),
          samples: impactHistory["eth0/11:"].samples.length, noteHidden: element("impact-note").hidden};
        answer([entry({packets: 180, drops: 20})], 20);
        await loadImpact();
        dump({samples, during, after: {badge: element("impact-state").textContent,
          same: sameNodes(before, listNodes()),
          samples: impactHistory["eth0/11:"].samples.length}});
        """
    )
    assert result["samples"] == 2
    assert result["during"] == {
        "badge": "Update paused", "same": True, "texts": True, "samples": 2, "noteHidden": False,
    }
    assert result["after"] == {"badge": "Live", "same": True, "samples": 3}


@needs_node
def test_a_pause_longer_than_the_window_clears_the_stale_rows():
    result = run(
        """
        answer([entry()], 0);
        answer([entry({packets: 90, drops: 10})], 5);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        await loadImpact();
        paused();
        fakeNow += 61000;
        await loadImpact();
        dump({badge: element("impact-state").textContent, list: element("impact-list").textContent,
          history: impactHistory, timers: activeTimers().length});
        """
    )
    assert result == {
        "badge": "Update paused",
        "list": "Impact could not be read right now.",
        "history": {},
        "timers": 1,
    }


@needs_node
def test_a_pause_before_any_row_shows_the_message_and_keeps_polling():
    result = run(
        """
        paused();
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        dump({badge: element("impact-state").textContent, list: element("impact-list").textContent,
          timers: activeTimers().length});
        """
    )
    assert result == {
        "badge": "Update paused",
        "list": "Impact could not be read right now.",
        "timers": 1,
    }


@needs_node
def test_a_paused_answer_never_clears_the_protected_line():
    result = run(
        """
        answer([entry()], 0, ["Dad's laptop"]);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        paused();
        await loadImpact();
        dump([element("impact-protected").hidden, element("impact-protected").textContent]);
        """
    )
    assert result == [False, "Protected (not affected): Dad's laptop"]


@needs_node
def test_going_from_rows_to_a_message_and_back_leaves_no_old_rows():
    result = run(
        """
        answer([entry()], 0);
        responses.push({available: false, paused: false, message: "Network is reset. Choose Start to start impact checks.",
          sampled_at: 5, queues: [], protected: []});
        answer([entry()], 10);
        syncImpact();
        await new Promise((resolve) => setTimeout(resolve, 5));
        await loadImpact();
        const message = [element("impact-list").textContent, element("impact-list").children.length];
        await loadImpact();
        dump({message, rows: rowTexts(), history: Object.keys(impactHistory)});
        """
    )
    assert result["message"][1] == 0
    assert result["rows"] == ["Lab phoneUploadCollecting…"]


@needs_node
def test_a_row_that_first_shows_with_a_verdict_is_not_announced():
    result = run(
        """
        impactHistory = recordImpactSample(
          recordImpactSample({}, {queues: [entry()]}, 0),
          {queues: [entry({packets: 90, drops: 10})]}, 5);
        renderImpact([]);
        dump([element("impact-status").textContent,
          element("impact-list").children[0].children[0].children[2].textContent]);
        """
    )
    assert result == ["", "Affected in the last minute"]
