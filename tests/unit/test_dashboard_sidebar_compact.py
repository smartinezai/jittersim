"""The sidebar lists nine pages and must stay short enough for a laptop screen."""

import re

from jittersim.api.server import DASHBOARD_HTML


def test_the_nine_page_links_are_compact_up_to_1000px_of_height():
    assert len(re.findall(r'data-page-link="[a-z]+"', DASHBOARD_HTML)) == 9
    match = re.search(
        r"@media \(min-width: 48\.01rem\) and \(max-height: 1000px\) \{(.*?)\n    \}",
        DASHBOARD_HTML,
        re.DOTALL,
    )
    assert match, "no compact navigation rule for laptop heights"
    rules = match.group(1)
    assert ".sidebar-nav ul { gap: 0; }" in rules
    assert "padding-block: 0.22rem" in rules
    # A target of 24px is the smallest comfortable size for a pointer.
    assert "min-height: 1.5rem" in rules
