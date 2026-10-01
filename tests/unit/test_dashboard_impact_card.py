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
    ["impact-title", "impact-state", "impact-note", "impact-list", "impact-protected"],
)
def test_each_impact_id_exists_once_inside_the_card(element_id):
    assert HTML.count(f' id="{element_id}"') == 1
    page = diagnostics_page()
    card = page[: page.index("</section>")]
    assert f' id="{element_id}"' in card


def test_the_card_starts_locked_and_announces_updates_politely():
    assert re.search(r'<span id="impact-state" class="count-badge">Check token</span>', HTML)
    assert re.search(
        r'<div id="impact-list" class="impact-list" aria-live="polite">'
        r"Check the admin token to see the impact\.</div>",
        HTML,
    )
    assert re.search(r'<p id="impact-protected" class="impact-protected" hidden></p>', HTML)
    assert "Delay and jitter are shown as configured" in HTML


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


# The card background (rgba(18, 31, 49, 0.9)) over the page background (#0b1220).
CARD_BACKGROUND = "#111e2f"
ROW_BACKGROUND = "#0d1b2d"


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
        background = declarations.get("background") or backgrounds.get(selector, ROW_BACKGROUND)
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
    assert rules[".impact-chip.affected"]["color"] == "#f5ca78"
    assert "#a77b31" in rules[".impact-chip.affected"]["border-color"]
    for muted in (".impact-chip.idle", ".impact-chip.too-few", ".impact-chip.no-effect"):
        assert rules.get(muted, {}).get("color") != "#f5ca78", muted


HARNESS = r"""
const timers = [];
const requests = [];
const responses = [];
let nextError = null;
function makeNode(tag) {
  return {
    tag: tag, children: [], _text: "", hidden: false, className: "", attributes: {},
    set textContent(value) { this._text = String(value); this.children = []; },
    get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); },
    set innerHTML(value) { throw new Error("innerHTML must not be used"); },
    append(...nodes) { nodes.forEach((node) => this.children.push(node)); },
    appendChild(node) { this.children.push(node); return node; },
    replaceChildren(...nodes) { this._text = ""; this.children = nodes; },
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
    interface: "eth0", handle: "11:", kind: "netem",
    impairment: {delay_ms: 0, jitter_ms: 0, loss_percent: 10, bandwidth_kbit: null,
      reorder_percent: 0, duplicate_percent: 0, corrupt_percent: 0},
    packets: 0, bytes: 0, drops: 0, overlimits: 0, requeues: 0, backlog_bytes: 0, qlen: 0
  }, extra || {});
}
function answer(queues, at, names) {
  responses.push({available: true, message: null, sampled_at: at, queues: queues,
    protected: names || []});
}
function dump(value) { console.log(JSON.stringify(value)); }
function rowTexts() {
  return element("impact-list").children.map((row) => row.textContent);
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
    assert result["second"][0].startswith("Lab phoneUploadAffected now")
    assert "Loss: set 10% · actual 10% (9 of 90 packets dropped)" in result["second"][0]
    assert result["firstState"] == result["secondState"] == "Live"
    assert result["note"] is False


@needs_node
def test_each_verdict_has_a_plain_label():
    result = run(
        """
        const labels = [];
        for (const [extra, at] of [
          [{packets: 0}, 10], [{packets: 20}, 10], [{packets: 80}, 10], [{packets: 40, drops: 5}, 10]
        ]) {
          answer([entry()], 0);
          answer([entry(extra)], at);
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
        "Set but no traffic yet",
        "Too few packets to judge",
        "No effect seen",
        "Affected now",
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
    assert result["kept"] == {"state": "Update failed", "rows": 1, "timers": 1}
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
