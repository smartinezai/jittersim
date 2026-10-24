"""Hints name the page where the missing thing is made and motion follows the person's setting."""

import re

from jittersim.api.server import DASHBOARD_HTML


def test_devices_page_hints_point_at_the_profiles_page():
    # Groups and saved profiles are made on the Profiles page, not further down the Devices page.
    assert '"Create a group on the Profiles page."' in DASHBOARD_HTML
    assert 'groups.length ? "Choose group..." : "No groups"' in DASHBOARD_HTML
    assert '"Create a saved profile on the Profiles page to use this control."' in DASHBOARD_HTML
    assert "Create a group below" not in DASHBOARD_HTML
    assert "Create a saved profile below" not in DASHBOARD_HTML


def test_reveal_scrolls_without_animation_when_reduced_motion_is_asked_for():
    body = re.search(r"function revealElement\(target\) \{.*?\n    \}", DASHBOARD_HTML, re.DOTALL)
    assert body
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in body.group(0)
    assert 'behavior: reduceMotion ? "auto" : "smooth"' in body.group(0)
