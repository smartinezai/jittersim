"""Dashboard shell: preserved navigation, controls and safe responsive layout."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jittersim.api.server import DASHBOARD_HTML

PAGES = (
    "devices", "profiles", "schedules", "router", "traffic", "measurements", "activity", "access",
    "diagnostics", "updates",
)
OLD_IDS = Path(__file__).with_name("dashboard_ids.txt").read_text().split()
HTML = DASHBOARD_HTML.split("<script>")[0]

CARD_PAGES = {
    "Getting started": "devices", "Everyone": "devices", "Connected clients": "devices",
    "Saved devices": "devices", "Everyone else": "devices", "Protected devices": "devices",
    "Saved profiles": "profiles", "Save profile": "profiles",
    "Assign a device": "profiles", "Group devices": "profiles",
    "Schedule a profile": "schedules", "Run a profile timer": "schedules", "Scenarios": "schedules",
    "Progression mode": "schedules", "Device data limit": "schedules",
    "Shared group data limit": "schedules", "Service lists": "schedules",
    "Browser service profile": "schedules",
    "Service data limit": "schedules", "Service time limit": "schedules",
    "Upstream router connection": "router", "Router outage behavior": "router",
    "Manual Pi access-point fallback": "router",
    "Live traffic": "traffic", "Traffic discovery": "traffic",
    "Random website choices": "traffic",
    "Measurements": "measurements", "Activity log": "activity",
    "Network access rules": "access",
    "Profiles": "diagnostics", "Devices": "diagnostics", "Compiled plan": "diagnostics",
    "Runtime state": "diagnostics", "Access rules": "diagnostics", "Diagnostics": "diagnostics",
    "Backup and action history": "diagnostics",
    "Updates": "router",
}


def page_html(page):
    match = re.search(
        rf'<section class="page" id="page-{page}"[^>]*>(.*?)<!-- /page:{page} -->', HTML, re.DOTALL
    )
    assert match, f"page-{page} is missing"
    return match.group(1)


def sidebar_html():
    return re.search(r'<aside class="sidebar".*?</aside>', HTML, re.DOTALL).group(0)


def test_every_previous_id_survives_exactly_once():
    for element_id in OLD_IDS:
        assert HTML.count(f' id="{element_id}"') == 1, element_id


def test_sidebar_links_follow_the_ten_pages_in_order():
    assert re.findall(r'href="#(\w+)" data-page-link', sidebar_html()) == list(PAGES)
    for page in PAGES:
        page_html(page)


@pytest.mark.parametrize("title,page", CARD_PAGES.items())
def test_each_card_sits_on_its_page(title, page):
    heading = re.compile(rf"<h2[^>]*>{re.escape(title)}</h2>")
    assert len(heading.findall(HTML)) == 1, title
    assert heading.search(page_html(page)), f"{title} should be on {page}"


def test_sidebar_holds_the_always_available_controls():
    sidebar = sidebar_html()
    for element_id in ("apply-button", "reset-button", "panic-button", "router-wifi-toggle",
                       "open-admin-credentials", "open-router-credentials",
                       "runtime-summary", "mode-badge", "message", "quick-reset-wifi-name"):
        assert f'id="{element_id}"' in sidebar, element_id
    assert 'id="reset-wifi-name"' in page_html("router")


PHONE_PINNED = {".safety-bar", ".controls-toggle", ".sidebar.controls-open .sidebar-footer"}


def test_floating_dock_and_sticky_bar_are_gone():
    assert "credential-dock" not in DASHBOARD_HTML
    assert "action-bar" not in DASHBOARD_HTML
    css = re.search(r"<style>(.*?)</style>", DASHBOARD_HTML, re.DOTALL).group(1)
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    pinned = {
        selector.strip()
        for selector, _ in re.findall(
            r"([^{}]+)\{([^{}]*position:\s*(?:fixed|sticky)[^{}]*)\}", css
        )
    }
    # The sidebar scrolls so the tips of its buttons are fixed to the window instead.
    # .field-tooltip is the help bubble that follows its button. On a phone the bar with Panic
    # and the router switch and the Controls sheet are pinned (see the next test).
    allowed = {".sidebar", ".toast", ".credentials-panel", ".help-icon::after",
               ".sidebar-action-help", ".field-tooltip",
               ".sidebar.controls-open .control-utilities"} | PHONE_PINNED
    assert pinned <= allowed, pinned


def test_the_phone_sheet_keeps_panic_and_its_toggle_pinned_above_it():
    css = re.search(r"<style>(.*?)</style>", DASHBOARD_HTML, re.DOTALL).group(1)
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    phone = re.search(
        r"@media \(max-width: 48rem\), \(max-height: 36rem\) \{(.*?)\n\}", css, re.DOTALL
    ).group(1)
    for selector in PHONE_PINNED:
        rule = re.search(r"(?:^|\n)\s*" + re.escape(selector) + r" \{([^{}]*)\}", phone)
        assert rule and "position: fixed" in rule.group(1), selector


SECOND_SCRIPT = (
    DASHBOARD_HTML.split("</script>")[1] if DASHBOARD_HTML.count("<script>") > 1 else ""
)


def run_node(expression):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required")
    functions = "\n".join(
        re.findall(
            r"    function (?:pageFromHash|cardStartsOpen)\b.*?\n    }\n", SECOND_SCRIPT, re.DOTALL
        )
    )
    program = (
        f"const PAGES = {json.dumps(list(PAGES))};\n{functions}\n"
        f"console.log(JSON.stringify({expression}));"
    )
    result = subprocess.run([node, "-e", program], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("hash_,expected", [
    ("#router", "router"), ("", "devices"), ("#nope", "devices"),
    ("#__proto__", "devices"), (None, "devices"), ("#traffic", "traffic"),
])
def test_page_from_hash(hash_, expected):
    assert run_node(f"pageFromHash({json.dumps(hash_)})") == expected


@pytest.mark.parametrize("saved,default,expected", [
    ("open", "closed", True), ("closed", "open", False),
    (None, "open", True), (None, "closed", False), ("junk", "open", True),
])
def test_card_start_state(saved, default, expected):
    assert run_node(f"cardStartsOpen({json.dumps(saved)}, {json.dumps(default)})") is expected


def test_sections_are_labelled_with_the_visible_link_text():
    for page in PAGES:
        link = re.search(rf'<a href="#{page}"[^>]*>([^<]+?)\s*(?:<span|</a>)', HTML).group(1).strip()
        assert f'id="page-{page}" data-page="{page}" aria-label="{link}"' in HTML, page


def test_updates_have_their_own_page_and_the_sidebar_shows_version_and_badge():
    sidebar = sidebar_html()
    assert re.search(r'<a href="#updates" data-page-link="updates"[^>]*>Updates ', sidebar)
    assert 'id="update-available-badge"' in sidebar
    assert 'id="sidebar-version"' in sidebar
    assert sidebar.index('id="sidebar-version"') < sidebar.index('class="sidebar-nav"')
    link = re.search(r'<a id="sidebar-update-link"[^>]*>', sidebar).group(0)
    assert 'href="#updates"' in link and "hidden" in link
    assert "page-updates" in HTML
    # Router and Wi-Fi keeps a link to the new page.
    assert 'href="#updates"' in page_html("router")
    assert 'updates: "updates"' in DASHBOARD_HTML


def test_the_update_card_is_mounted_on_updates_and_only_that_page_polls_it():
    script = (Path(__file__).resolve().parents[2] / "src/jittersim/api/workflows.js").read_text()
    assert "workflowCard('release-controls', 'Updates and recovery', 'updates'" in script
    assert 'if (page === "updates") jobs.push(pollReleaseProgress);' in DASHBOARD_HTML
    assert "pollReleaseProgress" not in re.search(r'if \(page === "router"\)[^\n]*', DASHBOARD_HTML).group(0)


def test_a_skip_link_is_the_first_stop_and_leads_to_focusable_main_content():
    assert re.search(r'<body[^>]*>\s*<a class="skip-link" href="#content">Skip to main content</a>', HTML)
    assert re.search(r'<main[^>]*id="content"[^>]*tabindex="-1"', HTML)


def test_main_has_the_only_level_one_heading_with_neutral_initial_text():
    assert re.findall(r'<h1\b[^>]*>.*?</h1>', HTML) == [
        '<h1 id="page-heading" class="page-heading">Sign in</h1>'
    ]
    assert re.search(r'<main\b[^>]*>\s*<h1 id="page-heading"', HTML)
    assert '<p id="brand-title">Network lab</p>' in sidebar_html()


def test_named_regions_do_not_share_a_name():
    names = re.findall(r'<(?:section|pre)[^>]*aria-label="([^"]+)"', HTML)
    assert len(names) == len(set(names)), sorted({n for n in names if names.count(n) > 1})
