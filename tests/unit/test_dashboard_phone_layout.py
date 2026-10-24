"""Phone layout: a short header, a bar with Panic and the router status and a Controls sheet."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

PAGES = (
    "devices", "profiles", "schedules", "router", "traffic", "measurements", "activity", "access",
    "diagnostics",
)
HTML = DASHBOARD_HTML.split("<script>")[0]
CSS = re.sub(
    r"/\*.*?\*/", "", re.search(r"<style>(.*?)</style>", DASHBOARD_HTML, re.DOTALL).group(1),
    flags=re.DOTALL,
)
PHONE = re.search(
    r"@media \(max-width: 48rem\), \(max-height: 36rem\) \{(.*?)\n\}", CSS, re.DOTALL
).group(1)
SCRIPTS = re.findall(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL)
SECOND_SCRIPT = SCRIPTS[1]
NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
TARGET = "2.75rem"  # 44px with the default font size


def declarations(block):
    """Every selector of a block of rules with its declarations. A later rule wins."""

    found = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", block):
        values = dict(
            (name.strip(), value.strip())
            for name, value in (item.split(":", 1) for item in body.split(";") if ":" in item)
        )
        for selector in selectors.split(","):
            found.setdefault(" ".join(selector.split()), {}).update(values)
    return found


PHONE_RULES = declarations(PHONE)


def phone(selector):
    assert selector in PHONE_RULES, selector
    return PHONE_RULES[selector]


def tag(element_id):
    return re.search(r"<[a-z]+[^>]*\bid=\"" + element_id + r"\"[^>]*>", HTML).group(0)


def block_of(class_name):
    start = HTML.index(f'<div class="{class_name}"')
    depth, index = 0, start
    while True:
        opening = HTML.find("<div", index)
        closing = HTML.find("</div>", index)
        if opening != -1 and opening < closing:
            depth, index = depth + 1, opening + 4
        else:
            depth, index = depth - 1, closing + 6
            if depth == 0:
                return HTML[start:index]


# Structure and style


def test_the_phone_rules_come_after_the_earlier_narrow_screen_rules():
    style = re.search(r"<style>(.*?)</style>", DASHBOARD_HTML, re.DOTALL).group(1)
    assert style.index(".field-tooltip {") < style.index(
        "@media (max-width: 48rem), (max-height: 36rem) {"
    )
    # Compact screens retain one scrolling row even when desktop links wrap.
    assert "wrap" not in phone(".sidebar-nav ul").get("flex-wrap", "")


def test_the_bar_is_pinned_to_the_bottom_with_panic_and_the_router_status():
    bar = phone(".safety-bar")
    assert bar["position"] == "fixed"
    assert (bar["left"], bar["right"], bar["bottom"]) == ("0", "0", "0")
    assert bar["height"] == "var(--phone-bar-height)"
    assert int(bar["z-index"]) > int(phone(".sidebar.controls-open .sidebar-footer")["z-index"])
    markup = block_of("safety-bar")
    for element_id in ("panic-button", "router-wifi-toggle", "router-wifi-status"):
        assert f'id="{element_id}"' in markup, element_id
    for element_id in ("sync-router-ap", "apply-button", "open-admin-credentials"):
        assert f'id="{element_id}"' not in markup, element_id
    # Both layouts retain one emergency control and the same read-only router value.
    assert HTML.count('id="panic-button"') == 1
    assert HTML.count('id="router-wifi-toggle"') == 1


def test_the_controls_toggle_sits_at_the_end_of_the_bar():
    toggle = phone(".controls-toggle")
    assert toggle["position"] == "fixed"
    assert toggle["width"] == "var(--controls-toggle-width)"
    assert toggle["height"] == TARGET
    assert toggle["bottom"] == "calc(0.5rem + env(safe-area-inset-bottom, 0px))"
    assert "var(--controls-toggle-width)" in phone(".safety-bar")["padding"]
    assert int(toggle["z-index"]) > int(phone(".safety-bar")["z-index"])
    assert phone(".dashboard-locked .controls-toggle")["display"] == "none"


def test_the_page_and_its_messages_keep_clear_of_the_bar():
    assert "var(--phone-bar-height)" in phone(".content")["padding"]
    assert "var(--phone-bar-height)" in phone("html")["scroll-padding-bottom"]
    assert phone(".toast")["top"] == "auto"
    assert "--phone-bar-height" in phone(".toast")["bottom"]
    assert phone(".toast")["width"] == "min(22rem, calc(100vw - 1.5rem))"
    rules = declarations(CSS.replace(PHONE, ""))
    assert rules[".sidebar .sidebar-action-row .help-icon::after"]["display"] == "none"
    tip = rules[".sidebar-action-help"]
    assert tip["position"] == "fixed"
    assert tip["max-height"] == "calc(100dvh - 2rem)"
    assert tip["overflow-y"] == "auto"
    assert int(tip["z-index"]) > int(phone(".safety-bar")["z-index"])


def test_every_page_link_stays_in_one_row_that_scrolls_inside_itself():
    row = phone(".sidebar-nav ul")
    assert row["display"] == "flex" and row["overflow-x"] == "auto"
    assert "wrap" not in row.get("flex-wrap", "")
    link = phone(".sidebar-nav a")
    assert link["white-space"] == "nowrap" and link["min-height"] == TARGET


@pytest.mark.parametrize("selector", [
    ".sidebar-nav a", ".status-strip", ".sidebar button", ".sidebar-credentials button",
    ".sidebar .router-wifi-copy button", ".credentials-panel button", ".toast button",
    "dialog button", ".status-devices summary", ".sidebar .active-profile select",
    ".sidebar .active-profile button", ".remember-choice",
])
def test_touch_targets_are_at_least_44px(selector):
    assert phone(selector)["min-height"] == TARGET


def test_the_bar_controls_are_44px_tall():
    assert phone(".safety-bar .panic-button")["height"] == TARGET
    assert phone(".safety-bar .router-wifi-control")["height"] == TARGET
    # The cards and forms of the pages were 44px already.
    access = declarations(CSS)
    assert access[".content button"]["min-height"] == TARGET
    assert access[".content select"]["min-height"] == TARGET


def test_nothing_in_the_phone_rules_is_wider_than_a_320px_window():
    for selector, values in PHONE_RULES.items():
        for name in ("width", "min-width"):
            value = values.get(name, "")
            for number, unit in re.findall(r"(\d+(?:\.\d+)?)(px|rem)", value):
                size = float(number) * (16 if unit == "rem" else 1)
                assert size <= 320 or "100vw -" in value, (selector, name, value)
            assert "100vw" not in value or "100vw -" in value, (selector, value)


def test_the_sheet_opens_above_the_bar_and_scrolls_inside_the_window():
    sheet = phone(".sidebar.controls-open .sidebar-footer")
    assert sheet["position"] == "fixed"
    assert sheet["bottom"] == "var(--phone-bar-height)"
    assert "100dvh - var(--phone-bar-height)" in sheet["max-height"]
    assert sheet["overflow-y"] == "auto" and sheet["overscroll-behavior"] == "contain"
    assert phone(".sidebar-footer")["display"] == "contents"
    assert phone(".sidebar-operate")["display"] == "contents"
    for selector in (".active-profile", ".sidebar-actions", "#runtime-summary", "#message",
                     ".router-wifi-copy", ".sidebar-credentials"):
        assert phone(selector)["display"] == "none", selector
        assert phone(".sidebar.controls-open " + selector)["display"] in ("grid", "block"), selector
    # Locked, the admin token is the only thing to do and its button stays in the header.
    assert phone(".dashboard-locked .sidebar-credentials")["display"] == "grid"


def test_the_status_strip_opens_the_full_status_block():
    strip = tag("status-strip")
    assert 'aria-expanded="false"' in strip and 'aria-controls="sidebar-status"' in strip
    assert " hidden" in strip
    assert phone(".sidebar:not(.status-open) #sidebar-status")["display"] == "none"
    sidebar = re.search(r'<aside class="sidebar".*?</aside>', HTML, re.DOTALL).group(0)
    assert sidebar.index('id="status-strip"') < sidebar.index('id="sidebar-status"')


def test_the_controls_toggle_names_the_sheet_and_comes_before_it():
    toggle = tag("controls-toggle")
    assert 'aria-expanded="false"' in toggle and 'aria-controls="sidebar-controls"' in toggle
    assert 'class="sidebar-footer" id="sidebar-controls"' in HTML
    assert HTML.index('id="controls-toggle"') < HTML.index('id="sidebar-controls"')


def test_no_element_id_is_also_a_page_name():
    # A page link is a hash link. An element with the same id made the browser jump to it.
    for page in PAGES:
        assert f'id="{page}"' not in DASHBOARD_HTML, page
        for script in SCRIPTS:
            assert f'element("{page}")' not in script, page
            assert f'getElementById("{page}")' not in script, page
    for element_id in ("profiles-json", "devices-json", "diagnostics-json"):
        assert DASHBOARD_HTML.count(f'id="{element_id}"') == 1, element_id


def test_the_script_follows_page_links_and_the_back_button():
    assert re.search(
        r'querySelectorAll\("\.sidebar-nav \[data-page-link\]"\)\.forEach\(\(link\) => \{\n\s+'
        r'link\.addEventListener\("click", \(event\) => followTabLink\(event, link\.dataset\.pageLink\)\);',
        SECOND_SCRIPT,
    )
    assert 'window.addEventListener("popstate", followLocation);' in SECOND_SCRIPT
    assert 'window.addEventListener("hashchange", followLocation);' in SECOND_SCRIPT


def test_dark_only():
    assert "prefers-color-scheme" not in DASHBOARD_HTML
    assert "color-scheme: dark" in CSS


def test_scrolling_honours_reduced_motion():
    page = "".join(SCRIPTS)
    assert 'window.matchMedia("(prefers-reduced-motion: reduce)")' in SECOND_SCRIPT
    reveal = re.search(r"function revealElement\(.*?\n    }\n", SECOND_SCRIPT, re.DOTALL).group(0)
    assert 'behavior: reduceMotion ? "auto" : "smooth"' in reveal
    # Every other smooth scroll asks the same setting through scrollBehavior().
    smooth = re.findall(r'behavior: ([^,}]*"smooth"[^,}]*)', page)
    assert len(smooth) >= 3, smooth
    assert all("scrollBehavior()" in found or found.startswith("reduceMotion ?") for found in smooth), smooth


def test_navigation_no_longer_reserves_a_scrolling_desktop_rail():
    # The navigation is a page-level landmark. Its controls keep the same ids across layouts.
    assert 'class="sidebar-nav" aria-label="Sections"' in HTML
    assert 'min-height: 7rem; overflow-y: auto' not in CSS
    for element_id in ("controls-toggle", "sidebar-controls", "panic-button"):
        assert HTML.count(f'id="{element_id}"') == 1


# Behaviour in node


def function_source(name):
    match = re.search(r"\n    function " + name + r"\(.*?\n    }\n", SECOND_SCRIPT, re.DOTALL)
    assert match, name
    return match.group(0)


FUNCTIONS = (
    "pageFromHash", "scrollBehavior", "pageStartTop", "moreToShow", "showPageStart",
    "syncNavHint", "revealNavLink", "controlsOpen", "setControlsOpen", "keepsControlsOpen",
    "setStatusOpen", "statusStripText", "syncStatusStrip", "showPage", "followLocation",
)

PRELUDE = r"""
const PAGES = %s;
class Node {
  constructor(id, extra) {
    Object.assign(this, {id, hidden: false, disabled: false, textContent: "", dataset: {},
      attributes: {}, classes: new Set(), children: [], parent: null, rects: 1,
      box: {top: 0, bottom: 40, left: 0, right: 100, width: 100, height: 40},
      scrollLeft: 0, scrollTop: 0, scrollWidth: 0, clientWidth: 0, scrollHeight: 0, clientHeight: 0},
      extra || {});
    this.classList = {
      contains: (name) => this.classes.has(name),
      toggle: (name, on) => { if (on) this.classes.add(name); else this.classes.delete(name); }
    };
  }
  add(child) { child.parent = this; this.children.push(child); return child; }
  all() { return this.children.reduce((list, child) => list.concat([child], child.all()), []); }
  contains(node) { return node === this || this.all().includes(node); }
  closest(selector) {
    for (let node = this; node; node = node.parent) if (node.matches && node.matches(selector)) return node;
    return null;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
  removeAttribute(name) { delete this.attributes[name]; }
  toggleAttribute(name, on) { if (on) this.attributes[name] = ""; else delete this.attributes[name]; }
  getClientRects() { return this.rects ? [this.box] : []; }
  getBoundingClientRect() { return this.box; }
  focus() { focused.push(this.id); document.activeElement = this; }
  querySelectorAll(selector) { return this.all().filter((node) => node.matches && node.matches(selector)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
let focused = [];
const scrolls = [];
const window = {
  scrollY: 300,
  scrollTo(where, top) { scrolls.push(typeof where === "object" ? where : {top}); },
  location: {hash: ""}
};
const phoneLayout = {matches: true};
const reducedMotion = {matches: false};
const body = new Node("body", {dataset: {section: "devices"}});
const nav = body.add(new Node("nav", {matches: (s) => s === ".sidebar-nav"}));
const row = nav.add(new Node("row", {matches: (s) => s === "ul"}));
const links = PAGES.map((page) => row.add(new Node("link-" + page, {
  dataset: {pageLink: page},
  matches: (s) => s === "[data-page-link]" || s === '[data-page-link="' + page + '"]'
})));
const sections = PAGES.map((page) => body.add(new Node("page-" + page, {
  dataset: {page}, box: {top: 660, bottom: 1400, left: 0, right: 390, width: 390, height: 740},
  matches: (s) => s === "[data-page]"
})));
const sidebar = body.add(new Node("sidebar", {matches: (s) => s === ".sidebar"}));
const controlsToggle = sidebar.add(new Node("controls-toggle", {matches: (s) => s === "button"}));
const controlsSheet = sidebar.add(new Node("sidebar-controls", {matches: (s) => s === "#sidebar-controls"}));
const bar = controlsSheet.add(new Node("bar", {matches: (s) => s === ".safety-bar"}));
const panic = bar.add(new Node("panic-button", {matches: (s) => s === "button" || s === "select, button"}));
const select = controlsSheet.add(new Node("active-profile-select", {disabled: true,
  matches: (s) => s === "select, button"}));
const apply = controlsSheet.add(new Node("apply-button", {matches: (s) => s === "select, button"}));
const utilities = controlsSheet.add(new Node("utilities",
  {matches: (s) => s === ".control-utilities"}));
const utility = utilities.add(new Node("refresh-router-wifi",
  {matches: (s) => s === "select, button"}));
const panel = body.add(new Node("credentials-panel",
  {matches: (s) => s === "#credentials-panel, #toast, dialog, .field-tooltip"}));
const content = body.add(new Node("content", {matches: () => false}));
const statusStrip = sidebar.add(new Node("status-strip"));
const statusBlock = sidebar.add(new Node("sidebar-status", {hidden: true}));
const general = statusBlock.add(new Node("status-general"));
const plain = statusBlock.add(new Node("status-devices-plain"));
const summary = statusBlock.add(new Node("status-devices-summary"));
const stripText = statusStrip.add(new Node("status-strip-text"));
const byId = new Map(body.all().map((node) => [node.id, node]));
const document = {
  body, activeElement: null,
  getElementById: (id) => byId.get(id) || null,
  querySelector: (selector) => body.querySelector(selector),
  querySelectorAll: (selector) => body.querySelectorAll(selector)
};
""" % json.dumps(list(PAGES))


def run_node(body):
    program = PRELUDE + "".join(function_source(name) for name in FUNCTIONS) + body
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@NEEDS_NODE
@pytest.mark.parametrize("top,scrolled,margin,expected", [
    (660, 0, 8, 652), (-200, 900, 8, 692), (3, 0, 8, 0), (100.6, 10, 8, 103),
])
def test_the_page_start_is_scrolled_to_the_top(top, scrolled, margin, expected):
    assert run_node(
        f"console.log(JSON.stringify(pageStartTop({top}, {scrolled}, {margin})));"
    ) == expected


@NEEDS_NODE
@pytest.mark.parametrize("offset,visible,total,expected", [
    (0, 300, 900, {"before": False, "after": True}),
    (600, 300, 900, {"before": True, "after": False}),
    (200, 300, 900, {"before": True, "after": True}),
    (0, 300, 300, {"before": False, "after": False}),
    (0.5, 300, 300.5, {"before": False, "after": False}),
])
def test_a_scrolling_row_knows_what_it_hides(offset, visible, total, expected):
    assert run_node(
        f"console.log(JSON.stringify(moreToShow({offset}, {visible}, {total})));"
    ) == expected


@NEEDS_NODE
def test_smooth_scrolling_only_without_reduced_motion():
    assert run_node(
        "const answers = [scrollBehavior(true), scrollBehavior(false), scrollBehavior()];\n"
        "reducedMotion.matches = true; answers.push(scrollBehavior());\n"
        "console.log(JSON.stringify(answers));"
    ) == ["auto", "smooth", "smooth", "auto"]


@NEEDS_NODE
@pytest.mark.parametrize("general,devices,expected", [
    ("Everyone: Slow · active", "1 device affected", "Everyone: Slow · active · 1 device affected"),
    ("Everyone: none", "Not running", "Everyone: none · Not running"),
    ("Everyone: none", "", "Everyone: none"),
    ("", "Profiles off", "Profiles off"),
])
def test_the_status_strip_is_one_line_of_the_status(general, devices, expected):
    assert run_node(
        f"console.log(JSON.stringify(statusStripText({json.dumps(general)}, {json.dumps(devices)})));"
    ) == expected


@NEEDS_NODE
def test_a_page_link_on_a_phone_shows_and_focuses_the_page_start():
    result = run_node(
        "sidebar.classes.add('controls-open');\n"
        "showPage('router', true);\n"
        "console.log(JSON.stringify({\n"
        "  shown: sections.filter((s) => !s.hidden).map((s) => s.id),\n"
        "  current: links.filter((l) => l.attributes['aria-current'] === 'page').map((l) => l.id),\n"
        "  scrolls, focused, open: sidebar.classes.has('controls-open'),\n"
        "  expanded: controlsToggle.attributes['aria-expanded']}));"
    )
    assert result["shown"] == ["page-router"]
    assert result["current"] == ["link-router"]
    # The section is 660px down with 300px scrolled: the window goes to 952 (8px margin).
    assert result["scrolls"] == [{"top": 952, "behavior": "auto"}]
    assert result["focused"] == ["page-router"]
    assert result["open"] is False and result["expanded"] == "false"


@NEEDS_NODE
def test_a_page_link_on_a_wide_window_focuses_the_selected_page():
    result = run_node(
        "phoneLayout.matches = false;\n"
        "showPage('traffic', true); showPage('profiles', false);\n"
        "console.log(JSON.stringify({scrolls, focused}));"
    )
    assert result == {"scrolls": [{"top": 952, "behavior": "auto"}], "focused": ["page-traffic"]}


@NEEDS_NODE
def test_a_hidden_page_scrolls_the_window_to_the_top():
    result = run_node(
        "sections.forEach((s) => { s.rects = 0; });\n"
        "showPage('access', true);\n"
        "console.log(JSON.stringify({scrolls, focused}));"
    )
    assert result == {"scrolls": [{"top": 0}], "focused": []}


@NEEDS_NODE
def test_back_and_forward_switch_the_page_once():
    result = run_node(
        "const calls = [];\n"
        "const original = showPage;\n"
        "showPage = (id, user) => { calls.push([id, user]); original(id, user); };\n"
        "window.location.hash = '#profiles'; followLocation(); followLocation();\n"
        "window.location.hash = '#nonsense'; followLocation();\n"
        "console.log(JSON.stringify(calls));"
    )
    assert result == [["profiles", True], ["devices", True]]


@NEEDS_NODE
def test_the_current_link_is_scrolled_into_the_middle_of_the_row():
    result = run_node(
        "row.scrollWidth = 1200; row.clientWidth = 390;\n"
        "row.box = {top: 50, bottom: 95, left: 0, right: 390, width: 390, height: 45};\n"
        "links[8].box = {top: 50, bottom: 95, left: 900, right: 1010, width: 110, height: 45};\n"
        "revealNavLink('diagnostics');\n"
        "console.log(JSON.stringify({left: row.scrollLeft, more: Object.keys(nav.attributes).sort()}));"
    )
    # 900 - (390 - 110) / 2 = 760 and the row still hides links on both sides.
    assert result["left"] == 760
    assert result["more"] == ["data-more-after", "data-more-before"]


@NEEDS_NODE
def test_opening_the_sheet_moves_focus_to_its_first_control_and_closing_returns_it():
    result = run_node(
        "setControlsOpen(true, true);\n"
        "const opened = [sidebar.classes.has('controls-open'), controlsToggle.attributes['aria-expanded']];\n"
        "setControlsOpen(false, true);\n"
        "console.log(JSON.stringify({opened, focused, open: sidebar.classes.has('controls-open'),\n"
        "  expanded: controlsToggle.attributes['aria-expanded']}));"
    )
    # The disabled select and Panic in the bar are skipped.
    assert result["opened"] == [True, "true"]
    assert result["focused"] == ["apply-button", "controls-toggle"]
    assert result["open"] is False and result["expanded"] == "false"


@NEEDS_NODE
def test_desktop_utilities_focus_the_first_usable_utility_and_return_to_controls():
    result = run_node(
        "phoneLayout.matches = false;\n"
        "setControlsOpen(true, true); setControlsOpen(false, true);\n"
        "console.log(JSON.stringify({focused, "
        "expanded: controlsToggle.attributes['aria-expanded']}));"
    )
    assert result == {"focused": ["refresh-router-wifi", "controls-toggle"], "expanded": "false"}


@NEEDS_NODE
def test_horizontal_navigation_hint_works_on_desktop_too():
    result = run_node(
        "phoneLayout.matches = false; row.scrollLeft = 100;\n"
        "row.clientWidth = 900; row.scrollWidth = 1400;\n"
        "syncNavHint(); console.log(JSON.stringify(Object.keys(nav.attributes).sort()));"
    )
    assert result == ["data-more-after", "data-more-before"]


@NEEDS_NODE
def test_only_a_tap_outside_closes_the_sheet():
    result = run_node(
        "console.log(JSON.stringify([apply, panic, controlsToggle, panel, content, null]\n"
        "  .map((node) => keepsControlsOpen(node))));"
    )
    assert result == [True, True, True, True, False, True]


@NEEDS_NODE
def test_the_strip_follows_the_status_block():
    result = run_node(
        "const seen = [];\n"
        "syncStatusStrip(); seen.push([statusStrip.hidden, stripText.textContent]);\n"
        "statusBlock.hidden = false; general.textContent = 'Everyone: Slow · active';\n"
        "general.classes.add('status-on'); plain.hidden = true; summary.textContent = '2 devices affected';\n"
        "syncStatusStrip();\n"
        "seen.push([statusStrip.hidden, stripText.textContent, statusStrip.classes.has('status-on')]);\n"
        "setStatusOpen(true); seen.push(statusStrip.attributes['aria-expanded']);\n"
        "statusBlock.hidden = true; syncStatusStrip();\n"
        "seen.push([statusStrip.hidden, statusStrip.attributes['aria-expanded'],\n"
        "  sidebar.classes.has('status-open')]);\n"
        "console.log(JSON.stringify(seen));"
    )
    assert result == [
        [True, ""],
        [False, "Everyone: Slow · active · 2 devices affected", True],
        "true",
        [True, "false", False],
    ]


def test_router_status_is_visible_text_without_a_control_role():
    value = tag("router-wifi-toggle")
    assert value.startswith("<span ")
    assert 'id="router-wifi-label" class="router-wifi-label">Router Wi-Fi</span>' in HTML
    for attribute in ("role=", "tabindex=", "aria-checked=", "aria-label=", "disabled"):
        assert attribute not in value
    assert 'class="router-wifi-value"' in value
    assert 'class="switch-track"' not in HTML
    assert phone(".safety-bar .router-wifi-label")["display"] == "block"
    assert "text-overflow" not in phone(".safety-bar .router-wifi-label")
