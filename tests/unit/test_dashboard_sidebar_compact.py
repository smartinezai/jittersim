"""Full-width navigation keeps real page links before the global commands."""

import re

from jittersim.api.server import DASHBOARD_HTML


def test_page_navigation_is_a_single_labelled_set_of_real_links():
    html = DASHBOARD_HTML.split("<script>")[0]
    nav = re.search(r'<nav class="sidebar-nav"[^>]*>(.*?)</nav>', html, re.DOTALL)
    assert nav and 'aria-label="Sections"' in nav.group(0)
    links = re.findall(r'<a href="#([a-z]+)" data-page-link="([a-z]+)"', nav.group(1))
    assert len(links) == 10 and len(set(links)) == 10
    assert all(destination == page for destination, page in links)
    assert 'role="tab"' not in nav.group(0)
    assert html.index(nav.group(0)) < html.index('id="sidebar-controls"')


def test_secondary_utilities_have_a_named_group_without_duplicating_emergency_actions():
    html = DASHBOARD_HTML.split("<script>")[0]
    utilities = html[html.index('<div class="control-utilities"'):html.index('</aside>')]
    assert 'role="group" aria-label="Network utilities"' in utilities
    for element_id in ("refresh-router-wifi", "sync-router-ap", "quick-reset-wifi-name",
                       "open-admin-credentials", "open-router-credentials"):
        assert utilities.count(f'id="{element_id}"') == 1
    assert 'id="panic-button"' not in utilities
    assert 'id="apply-button"' not in utilities
