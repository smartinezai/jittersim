"""Sidebar shell: sections, preserved ids, no floating panels, routing helpers."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jittersim.api.server import DASHBOARD_HTML

PAGES = ("devices", "profiles", "schedules", "router", "traffic", "access", "diagnostics")
OLD_IDS = Path(__file__).with_name("dashboard_ids.txt").read_text().split()
HTML = DASHBOARD_HTML.split("<script>")[0]

CARD_PAGES = {
    "General profile": "devices", "Connected clients": "devices",
    "Unaffected by profiles": "devices",
    "Save profile": "profiles", "Assign a device": "profiles", "Group devices": "profiles",
    "Schedule a profile": "schedules", "Run a profile timer": "schedules",
    "Progression mode": "schedules", "Device data limit": "schedules",
    "Shared group data limit": "schedules", "Browser service profile": "schedules",
    "Service data limit": "schedules", "Service time limit": "schedules",
    "Upstream router connection": "router", "Manual Pi access-point fallback": "router",
    "Live traffic": "traffic", "Traffic discovery": "traffic",
    "Random website choices": "traffic",
    "Network access rules": "access",
    "Profiles": "diagnostics", "Devices": "diagnostics", "Compiled plan": "diagnostics",
    "Runtime state": "diagnostics", "Access rules": "diagnostics", "Diagnostics": "diagnostics",
    "Backup and action history": "diagnostics",
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


def test_sidebar_links_follow_the_seven_pages_in_order():
    assert re.findall(r'href="#(\w+)"', sidebar_html()) == list(PAGES)
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
                       "runtime-summary", "mode-badge", "message"):
        assert f'id="{element_id}"' in sidebar, element_id
    assert 'id="reset-wifi-name"' in page_html("router")


def test_floating_dock_and_sticky_bar_are_gone():
    assert "credential-dock" not in DASHBOARD_HTML
    assert "action-bar" not in DASHBOARD_HTML
    css = re.search(r"<style>(.*?)</style>", DASHBOARD_HTML, re.DOTALL).group(1)
    pinned = {
        selector.strip()
        for selector, _ in re.findall(
            r"([^{}]+)\{([^{}]*position:\s*(?:fixed|sticky)[^{}]*)\}", css
        )
    }
    assert pinned <= {".sidebar", ".toast", ".credentials-panel", ".help-icon::after"}, pinned


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
